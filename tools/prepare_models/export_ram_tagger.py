"""Export pinned RAM++ once; run the resulting graph using the production runtime.

The export command needs the isolated research environment. The verify command uses
the ordinary application environment and never imports the upstream Python package.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from photo_search.tagging.model import (
    OUTPUT,
    PACKAGE_NAME,
    PREPROCESS,
    RUNTIME,
    SCHEMA,
    content_id,
    file_sha256,
    read_model_directory,
)
from photo_search.tagging.runtime import RamTagger, preprocess_image
from tools.evaluation.album_tagging_probe import (
    LOCK,
    load_model,
    load_sample,
    local_path,
    verify_assets,
    verify_runtime,
    write_json,
)
from tools.evaluation.wemm_pilot_runtime import (
    configure_offline,
    decode_image,
    decoder_identity,
    peak_ram_bytes,
)

ROOT = Path(__file__).resolve().parents[2]


def source_identity() -> dict[str, str]:
    names = ("tools/prepare_models/export_ram_tagger.py",
             "src/photo_search/tagging/model.py", "src/photo_search/tagging/runtime.py",
             "tools/evaluation/album_tagging_probe.py",
             "tools/evaluation/wemm_pilot_runtime.py", "tools/evaluation/album_tagging_lock.json")
    return {name: file_sha256(ROOT / name) for name in names}


def top_indices(scores: np.ndarray, limit: int = 32) -> np.ndarray:
    """Stable class-ID tie break shared by the export evidence and persisted top tags."""
    return np.lexsort((np.arange(scores.size), -scores))[:limit]


def compare_scores(reference: np.ndarray, actual: np.ndarray,
                   thresholds: np.ndarray) -> dict:
    if (reference.shape != actual.shape or reference.ndim != 2
            or reference.shape[1] != 4585 or thresholds.shape != (4585,)
            or not np.isfinite(reference).all() or not np.isfinite(actual).all()
            or np.any((reference < 0) | (reference > 1))
            or np.any((actual < 0) | (actual > 1))):
        raise ValueError("invalid score comparison inputs")
    rows = []
    for expected, obtained in zip(reference, actual, strict=True):
        a, b = top_indices(expected), top_indices(obtained)
        entire = top_indices(obtained, obtained.size)
        rows.append({
            "max_abs": float(np.abs(expected - obtained).max()),
            "top32_same_order": bool(np.array_equal(a, b)),
            "top32_same_set": bool(set(a) == set(b)),
            "top32_overlap": len(set(a) & set(b)),
            "top32_boundary_tie": bool(obtained[entire[31]] == obtained[entire[32]]),
            "selected_same": bool(np.array_equal(expected > thresholds, obtained > thresholds)),
            "selected_flips": int(np.count_nonzero((expected > thresholds) != (obtained > thresholds))),
        })
    return {"images": len(rows), "max_abs": max(row["max_abs"] for row in rows),
            "top32_same_order_images": sum(row["top32_same_order"] for row in rows),
            "top32_same_set_images": sum(row["top32_same_set"] for row in rows),
            "top32_min_overlap": min(row["top32_overlap"] for row in rows),
            "selected_same_images": sum(row["selected_same"] for row in rows),
            "selected_flips": sum(row["selected_flips"] for row in rows), "per_image": rows}


def make_wrapper(model, torch):
    class ScoresOnly(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = model

        def forward(self, image):
            m = self.model
            embeds = m.image_proj(m.visual_encoder(image))
            atts = torch.ones(embeds.size()[:-1], dtype=torch.long, device=image.device)
            cls = embeds[:, 0, :]
            cls = cls / cls.norm(dim=-1, keepdim=True)
            logits = (m.reweight_scale.exp() * cls @ m.label_embed.t()).view(1, 4585, 51)
            weight = torch.nn.functional.softmax(logits, dim=2)
            labels = (weight[0].unsqueeze(-1) * m.label_embed.view(4585, 51, 512))
            labels = labels.sum(dim=1).unsqueeze(0)
            labels = torch.nn.functional.relu(m.wordvec_proj(labels))
            tagged = m.tagging_head(encoder_embeds=labels, encoder_hidden_states=embeds,
                                   encoder_attention_mask=atts, return_dict=False, mode="tagging")
            return torch.sigmoid(m.fc(tagged[0]).squeeze(-1))

    return ScoresOnly().eval()


def _fresh_output(path: Path) -> Path:
    path = local_path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


@contextmanager
def staged_package(data_root: Path):
    """Publish only a fully qualified package; keep failed exports undiscoverable."""
    destination = local_path(data_root / "models" / "taggers" / PACKAGE_NAME)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"tagger package already exists: {destination}")
    # A sibling directory keeps publication on one filesystem. Discovery reads
    # only PACKAGE_NAME, never temporary directories or half-written manifests.
    with TemporaryDirectory(prefix=f".{PACKAGE_NAME}-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        yield staging
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"tagger package appeared during export: {destination}")
        staging.rename(destination)


def _inputs(sample: Path, limit: int | None) -> tuple[str, list[dict]]:
    before = file_sha256(local_path(sample))
    _, rows = load_sample(sample, limit)
    if file_sha256(sample) != before:
        raise ValueError("sample manifest changed while reading")
    return before, rows


def export(args: argparse.Namespace) -> dict:
    code_identity, decode_identity = source_identity(), decoder_identity()
    lock = json.loads(LOCK.read_bytes())
    verify_runtime(lock)
    source, weights, tokenizer = map(local_path, (args.source_dir, args.model, args.tokenizer_dir))
    verify_assets(source, weights, tokenizer, lock)
    sample_sha, rows = _inputs(args.sample, args.limit)
    out = _fresh_output(args.out_dir)
    with staged_package(args.data_root) as package:
        configure_offline(out / "cache")
        import torch

        model, transform, checkpoint = load_model(source, weights, tokenizer, "fp16", lock)
        wrapper = make_wrapper(model, torch)
        image, _ = decode_image(Path(rows[0]["path"]), rows[0]["sha256"])
        try:
            tensor = transform(image).unsqueeze(0).to(device="cuda:0", dtype=torch.float16)
        finally:
            image.close()
        with torch.inference_mode():
            traced = torch.jit.trace(wrapper, tensor, check_trace=False, strict=True)
            # Do not freeze: the unmodified parameter graph is smaller and avoids an
            # additional numerical transformation. CUDA/shape/precision stay explicit.
            traced.save(str(package / "model.ts"))
        classes = [{"index": i, "english": en, "chinese": cn, "threshold": float(threshold)}
                   for i, (en, cn, threshold) in enumerate(zip(
                       model.tag_list.tolist(), model.tag_list_chinese.tolist(),
                       model.class_threshold.tolist(), strict=True))]
        manifest = {"schema": SCHEMA,
                    "model": {"file": "model.ts", "bytes": (package / "model.ts").stat().st_size,
                              "sha256": file_sha256(package / "model.ts")},
                    "classes": classes, "preprocess": PREPROCESS, "output": OUTPUT,
                    "runtime": RUNTIME, "source": {"repository": lock["source"]["repository"],
                        "revision": lock["source"]["revision"],
                        "checkpoint_sha256": lock["model"]["sha256"],
                        "export_lock_sha256": file_sha256(LOCK)}}
        manifest["model_id"] = content_id(manifest)
        write_json(package / "manifest.json", manifest)
        info = read_model_directory(package)
        deployed = RamTagger.load(info)
        references, actuals, pre_save = [], [], []
        preprocessing_equal, wrapper_equal = True, True
        for row in rows:
            image, _ = decode_image(Path(row["path"]), row["sha256"])
            try:
                original_tensor = transform(image).unsqueeze(0)
                preprocessing_equal &= torch.equal(original_tensor, preprocess_image(image, torch))
                tensor = original_tensor.to(device="cuda:0", dtype=torch.float16)
                captured = []
                hook = model.fc.register_forward_hook(
                    lambda _, inputs, output: captured.append(torch.sigmoid(output.squeeze(-1))))
                try:
                    with torch.inference_mode():
                        english, chinese = model.generate_tag(tensor)
                        native = wrapper(tensor)
                    reference = captured[0].detach().float().cpu().numpy()[0]
                    wrapper_equal &= torch.equal(captured[0], native)
                finally:
                    hook.remove()
                selected = np.flatnonzero(reference > np.array([x.threshold for x in info.classes]))
                if ([" | ".join(info.classes[i].english for i in selected)] != english
                        or [" | ".join(info.classes[i].chinese for i in selected)] != chinese):
                    raise ValueError("reference scores disagree with official tag output")
                with torch.inference_mode(), torch.jit.optimized_execution(False):
                    before_save = traced(tensor).detach().float().cpu().numpy()[0]
                prediction = deployed.predict(image)
                references.append(reference)
                pre_save.append(before_save)
                actuals.append(prediction.scores)
            finally:
                image.close()
        deployed.close()
        references, actuals, pre_save = map(lambda values: np.asarray(values, dtype=np.float32),
                                          (references, actuals, pre_save))
        np.save(out / "reference-scores.npy", references)
        np.save(out / "exported-scores.npy", actuals)
        np.save(out / "before-save-scores.npy", pre_save)
        result = {"schema": "ram-tagger-export-evidence-v1", "model_id": info.model_id,
                  "manifest_sha256": info.manifest_sha256, "sample_sha256": sample_sha,
                  "assets": rows, "checkpoint": checkpoint, "decoder": decode_identity,
                  "source_identity": code_identity,
                  "official_preprocessing_exact": bool(preprocessing_equal),
                  "official_wrapper_exact": bool(wrapper_equal),
                  "serialization_exact": bool(np.array_equal(pre_save, actuals)),
                  "serialization_comparison": compare_scores(pre_save, actuals, np.array(
                      [item.threshold for item in info.classes], dtype=np.float32)),
                  "official_comparison": compare_scores(references, actuals, np.array(
                      [item.threshold for item in info.classes], dtype=np.float32)),
                  "reference_scores_sha256": file_sha256(out / "reference-scores.npy"),
                  "exported_scores_sha256": file_sha256(out / "exported-scores.npy")}
        verify_assets(source, weights, tokenizer, lock)
        if (file_sha256(args.sample) != sample_sha or source_identity() != code_identity
                or decoder_identity() != decode_identity):
            raise ValueError("sample/source/decoder changed during export")
        # FP16 graph execution may select different kernels after serialization. Record
        # the actual difference; the saved graph has its own immutable model identity and
        # requires downstream quality acceptance, not a claim of bitwise compatibility.
        if not all(result[key] for key in ("official_preprocessing_exact", "official_wrapper_exact")):
            write_json(out / "export.json", result)
            raise ValueError("export preprocessing/wrapper changed outputs")
        write_json(out / "export.json", result)
        return result


def verify(args: argparse.Namespace) -> dict:
    code_identity, decode_identity = source_identity(), decoder_identity()
    sample_sha, rows = _inputs(args.sample, args.limit)
    info = read_model_directory(local_path(args.package_dir))
    out = _fresh_output(args.out_dir)
    configure_offline(out / "cache")
    import torch

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = RamTagger.load(info)
    load_seconds = time.perf_counter() - started
    scores, inference_ms, decode_ms = [], [], []
    for row in rows:
        phase = time.perf_counter()
        image, _ = decode_image(Path(row["path"]), row["sha256"])
        decode_ms.append((time.perf_counter() - phase) * 1000)
        try:
            phase = time.perf_counter()
            prediction = model.predict(image)
            inference_ms.append((time.perf_counter() - phase) * 1000)
            scores.append(prediction.scores)
        finally:
            image.close()
    scores = np.asarray(scores, dtype=np.float32)
    np.save(out / "scores.npy", scores)
    result = {"schema": "ram-tagger-production-run-v1", "model_id": info.model_id,
              "manifest_sha256": info.manifest_sha256, "sample_sha256": sample_sha,
              "dataset_manifest_sha256": sample_sha, "assets": rows,
              "classes": [vars(item) for item in info.classes], "score_type": "sigmoid_scores",
              "scores_sha256": file_sha256(out / "scores.npy"),
              "runtime": {name: importlib.metadata.version(name)
                          for name in ("torch", "numpy", "Pillow", "transformers")},
              "decoder": decode_identity, "source_identity": code_identity,
              "load_seconds": load_seconds,
              "elapsed_seconds": time.perf_counter() - started, "inference_ms": inference_ms,
              "decode_ms": decode_ms, "gpu": torch.cuda.get_device_name(),
              "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
              "gpu_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
              "peak_ram_bytes": peak_ram_bytes()}
    if args.reference_scores is not None:
        if args.reference_result is None:
            raise ValueError("reference scores require their identity-bearing result JSON")
        reference_result_path = local_path(args.reference_result)
        if reference_result_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("reference result exceeds byte bound")
        reference_result = json.loads(reference_result_path.read_bytes())
        expected_assets = [(str(row["asset_id"]), row["sha256"]) for row in rows]
        reference_assets = [(str(row["asset_id"]), row["sha256"])
                            for row in reference_result["assets"]]
        reference_sha = file_sha256(local_path(args.reference_scores))
        if (reference_assets != expected_assets
                or reference_result["scores_sha256"] != reference_sha
                or reference_result["classes"] != result["classes"]):
            raise ValueError("reference score identity, assets or vocabulary do not match")
        reference = np.load(local_path(args.reference_scores), allow_pickle=False)
        result["comparison"] = compare_scores(reference, scores, np.array(
            [item.threshold for item in info.classes], dtype=np.float32))
        result["reference_scores_sha256"] = file_sha256(args.reference_scores)
        result["reference_result_sha256"] = file_sha256(args.reference_result)
    info.verify()
    if (file_sha256(args.sample) != sample_sha or source_identity() != code_identity
            or decoder_identity() != decode_identity):
        raise ValueError("sample/source/decoder changed during production verification")
    model.close()
    write_json(out / "result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("export")
    for flag in ("source-dir", "model", "tokenizer-dir", "data-root"):
        build.add_argument("--" + flag, type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--package-dir", type=Path, required=True)
    check.add_argument("--reference-scores", type=Path)
    check.add_argument("--reference-result", type=Path)
    for command in (build, check):
        command.add_argument("--sample", type=Path, required=True)
        command.add_argument("--limit", type=int)
        command.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = export(args) if args.command == "export" else verify(args)
    print(json.dumps({"model_id": result["model_id"], "output": str(args.out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
