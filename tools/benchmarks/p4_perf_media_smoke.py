"""Offline synthetic-media functional smoke; never a 300k-photo throughput claim.

Run phases separately so model inference can be scheduled without benchmark contention.
Only the explicit project-local work root is written. Existing weights and packaged NASA
fixture are read locally; no downloading, authentication, real-library scan or ACL edits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORK = ROOT / ".tmp/perf-300k-all-20261015/media"
OUTPUT = ROOT / "docs/reports/artifacts"
MODEL_DATA = ROOT / "PhotoSearchData"
CLIP_MANIFEST = ROOT / "tools/prepare_models/manifests/chinese-clip-vit-b-16.json"
COLORS = {"red": (225, 35, 35), "green": (25, 170, 65),
          "blue": (35, 75, 220), "yellow": (240, 210, 20)}
COLOR_CN = {"red": "红色", "green": "绿色", "blue": "蓝色", "yellow": "黄色"}
SHAPE_CN = {"circle": "圆形", "square": "正方形", "triangle": "三角形"}


def timed(call: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    value = call()
    return value, (time.perf_counter() - start) * 1000


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def check(report: dict, name: str, passed: bool, detail: Any = None) -> None:
    report.setdefault("checks", []).append(
        {"name": name, "passed": bool(passed), "detail": detail}
    )


def prepare(work: Path, report: dict) -> None:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

    manifest_path = work / "fixture.json"
    if manifest_path.exists():
        report["fixture"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in report["fixture"]["files"]:
            if not (work / item["path"]).resolve().is_relative_to(work):
                raise ValueError("fixture paths must stay inside the diagnostic work root")
        check(report, "existing_fixture_hashes", all(
            digest(work / item["path"]) == item["sha256"]
            for item in report["fixture"]["files"]
        ))
        return
    library = work / "library"
    library.mkdir(parents=True, exist_ok=True)
    invalid = work / "invalid-library"
    invalid.mkdir(exist_ok=True)
    planned = []

    def record(path: Path, **facts: Any) -> None:
        planned.append({"path": path.relative_to(work).as_posix(), "name": path.name,
                        "sha256": digest(path), "bytes": path.stat().st_size, **facts})

    for color, rgb in COLORS.items():
        for shape in SHAPE_CN:
            image = Image.new("RGB", (800, 600), "white")
            drawing = ImageDraw.Draw(image)
            if shape == "circle":
                drawing.ellipse((180, 80, 620, 520), fill=rgb)
            elif shape == "square":
                drawing.rectangle((180, 80, 620, 520), fill=rgb)
            else:
                drawing.polygon([(400, 70), (660, 530), (140, 530)], fill=rgb)
            path = library / f"shape_{color}_{shape}.png"
            image.save(path)
            record(path, kind="shape", color=color, shape=shape, expected_face_count=0)
    for color in ("red", "green", "blue"):
        path = library / f"solid_{color}.png"
        Image.new("RGB", (800, 600), COLORS[color]).save(path)
        record(path, kind="solid", color=color, expected_face_count=0)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 56)
    documents = {
        "text_cn.png": ["深圳旅行记录", "日期 2026-10-15", "金额 1280 元", "订单 AB12345"],
        "text_en.png": ["INVOICE", "SHENZHEN TRAVEL", "DATE 2026-10-15", "TOTAL 1280",
                        "ORDER AB12345"],
        "text_dense.png": [f"旅行记录 {row:02d} 日期 2026-10-15 金额 1280" for row in range(12)],
    }
    for name, lines in documents.items():
        size = (1800, 1500) if "dense" in name else (1400, 900)
        image = Image.new("RGB", size, "white")
        drawing = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            drawing.text((80, 60 + index * 105), line, fill="black", font=font)
        path = library / name
        image.save(path)
        record(path, kind="text", expected_lines=lines, expected_face_count=0)
    blank = library / "blank.png"
    Image.new("RGB", (800, 600), "white").save(blank)
    record(blank, kind="blank", expected_face_count=0)
    upright = Image.new("RGB", (400, 240), "white")
    drawing = ImageDraw.Draw(upright)
    drawing.rectangle((0, 0, 199, 239), fill=COLORS["red"])
    drawing.polygon([(210, 210), (380, 210), (295, 20)], fill=COLORS["blue"])
    path = library / "orientation_6.jpg"
    exif = Image.Exif()
    exif[274] = 6
    upright.transpose(Image.Transpose.ROTATE_90).save(path, quality=95, exif=exif)
    record(path, kind="orientation", expected_upright_size=[400, 240], expected_face_count=0)
    with Image.open(path) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        canonical = library / "orientation_upright.png"
        normalized.save(canonical)
    record(canonical, kind="canonical_orientation", expected_face_count=0)
    astronaut = Path(sys.prefix) / "Lib/site-packages/skimage/data/astronaut.png"
    if astronaut.is_file():
        path = library / "public_astronaut.png"
        shutil.copy2(astronaut, path)
        record(path, kind="public_face", expected_face_count=1,
               provenance="Locally packaged skimage NASA astronaut public-domain fixture",
               source_sha256=digest(astronaut))
        with Image.open(astronaut) as image:
            path = library / "public_astronaut_bright.png"
            ImageEnhance.Brightness(image).enhance(1.15).save(path)
        record(path, kind="public_face_variant", expected_face_count=1,
               provenance="Brightness 1.15 derivative of the packaged NASA fixture")
    else:
        report["positive_face_fixture_missing"] = True
    duplicate = library / "exact_duplicate.png"
    shutil.copy2(library / "solid_red.png", duplicate)
    record(duplicate, kind="exact_duplicate", duplicate_of="solid_red.png")
    broken = invalid / "broken.jpg"
    broken.write_bytes(b"synthetic invalid JPEG fixture; deliberately not decodable")
    record(broken, kind="corrupt", expected_decode_error="DECODE_FAILED")
    fixture = {"kind": "synthetic plus explicitly identified packaged public NASA fixture",
               "files": planned, "font": "Microsoft YaHei (installed Windows font)",
               "real_user_library_accessed": False}
    manifest_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    report["fixture"] = fixture
    check(report, "fixture_created", len(planned) >= 20, len(planned))


def service_for(work: Path):
    from photo_search.ingestion.service import CatalogService
    return CatalogService.open(data_root=work / "data", stability_window_ms=0)


def assets(service: Any) -> dict[str, int]:
    return {Path(str(row["relative_path"])).name: int(row["asset_id"])
            for row in service.catalog.read_all(
                "SELECT relative_path, asset_id FROM file_locations WHERE asset_id IS NOT NULL")}


def scan_media(work: Path, report: dict) -> None:
    from PIL import Image

    from photo_search.media.contract import spec_for
    from photo_search.media.decode import load_normalized
    from photo_search.media.pipeline import MediaGenerator, select_targets

    with service_for(work) as service:
        scans = []
        for name, folder in (("media-valid", "library"), ("media-invalid", "invalid-library")):
            existing = next((s for s in service.list_sources() if s["display_name"] == name), None)
            source = existing or service.register_source(display_name=name, root_path=work / folder)
            outcome, elapsed = timed(lambda: service.run_scan(source["source_id"]))
            scans.append({"source": name, "elapsed_ms": elapsed, "outcome": outcome})
        report["scan"] = scans
        check(report, "both_scans_complete", all(s["outcome"]["state"] == "SUCCEEDED"
                                                 for s in scans), scans)
        ids = assets(service)
        report["assets"] = ids
        report["counts"] = service.counts()
        check(report, "exact_duplicate_shares_asset",
              ids["exact_duplicate.png"] == ids["solid_red.png"])
        check(report, "corrupt_input_is_catalogued", "broken.jpg" in ids)
        generator = MediaGenerator(service.catalog, cache=service.media_cache(),
                                   data_root=service.data_root)
        rows = []
        names = {asset_id: name for name, asset_id in ids.items()}
        for kind in ("thumbnail", "preview"):
            for target in select_targets(service.catalog):
                result, elapsed = timed(lambda: generator.build(spec_for(kind), target))
                row = {**result.as_dict(), "wall_ms": elapsed, "name": names[target.asset_id]}
                if result.artifact_path:
                    with Image.open(result.artifact_path) as image:
                        row["artifact_size"] = list(image.size)
                        check(report, f"{kind}_{target.asset_id}_bounded",
                              max(image.size) <= spec_for(kind).long_edge, row["artifact_size"])
                if target.asset_id == ids["broken.jpg"]:
                    check(report, f"{kind}_corrupt_refused", result.code == "DECODE_FAILED", row)
                else:
                    check(report, f"{kind}_{target.asset_id}_published", result.ok, row["state"])
                rows.append(row)
        report["media"] = rows
        decoded = load_normalized(work / "library/orientation_6.jpg", long_edge=1600)
        check(report, "exif_upright_dimensions", [decoded.width, decoded.height] == [400, 240])
        check(report, "exif_upright_color", decoded.image.getpixel((20, 120))[0] > 180)
        decoded.image.close()


def clip(work: Path, report: dict, *, manifest_path: Path = CLIP_MANIFEST) -> None:
    from photo_search.indexing import semantic
    from photo_search.models.embedding.cli import _verify_local_source
    from photo_search.search.embedding import encode_query
    from photo_search.search.service import search_assets
    from photo_search.search.upload import encode_image_query, stage_upload
    from photo_search.workers.query_encoder import build_worker_query_encoder

    model_dir = MODEL_DATA / "models/chinese_clip/vit-b-16"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report["weights"], report["weight_validation_ms"] = timed(
        lambda: _verify_local_source(model_dir, manifest))
    encoder, report["worker_start_and_model_load_ms"] = timed(lambda: build_worker_query_encoder(
        model_dir=model_dir, manifest_path=manifest_path, device="cuda", device_policy="gpu_required"))
    try:
        report["health"] = encoder.health()
        check(report, "clip_cuda_loaded", report["health"]["device"]["provider"] ==
              "CUDAExecutionProvider", report["health"]["device"])
        with service_for(work) as service:
            ids = assets(service)
            source = next(s for s in service.list_sources() if s["display_name"] == "media-valid")
            build, report["index_build_inference_and_publish_ms"] = timed(lambda: semantic.build_index(
                service.catalog, encoder=encoder, manifest_path=manifest_path,
                source_id=source["source_id"], limit=100, device="cuda",
                device_policy="gpu_required", worker_id="media-smoke"))
            report["build"] = build
            check(report, "clip_generation_ready", build["generation"]["state"] == "READY", build)
            binding = {"space_id": build["model"]["space_id"],
                       "generation_id": build["generation"]["generation_id"], "dimension": 512}
            queries = []
            for color in COLORS:
                for shape in SHAPE_CN:
                    queries.append((COLOR_CN[color] + "的" + SHAPE_CN[shape],
                                    [f"shape_{color}_{shape}.png"],
                                    (color, shape) in {("blue", "triangle"), ("yellow", "square")}))
            for color in ("red", "green", "blue"):
                queries.append((COLOR_CN[color] + "的纯色图片", [f"solid_{color}.png"], False))
            queries += [("白色的空白图片", ["blank.png"], True),
                        ("宇航员的照片", ["public_astronaut.png"], False),
                        ("一个穿宇航服的人", ["public_astronaut.png"], True),
                        ("中文旅行记录", ["text_cn.png", "text_dense.png"], False),
                        ("英文发票", ["text_en.png"], False),
                        ("金额1280元", ["text_cn.png", "text_en.png", "text_dense.png"], False),
                        ("订单AB12345", ["text_cn.png", "text_en.png"], False),
                        ("2026年10月15日", ["text_cn.png", "text_en.png", "text_dense.png"], False),
                        ("沙滩上的大象", [], True), ("雪山上的汽车", [], True)]
            results = []
            reverse = {value: key for key, value in ids.items() if key != "exact_duplicate.png"}
            for query, targets, held_out in queries:
                vector, encoding_ms = timed(lambda: encode_query(
                    encoder, query, space_id=binding["space_id"], dimension=512))
                body, retrieval_ms = timed(lambda: search_assets(
                    service.catalog, text=query, query_vector=vector, top_k=50,
                    source_id=source["source_id"], **binding))
                hits = [int(item["asset_id"]) for item in body["hits"]]
                wanted = [ids[name] for name in targets if name in ids]
                ranks = [hits.index(value) + 1 for value in wanted if value in hits]
                results.append({"query": query, "known_targets": targets, "held_out": held_out,
                                "encode_ms": encoding_ms, "retrieval_ms": retrieval_ms,
                                "target_rank": min(ranks) if ranks else None,
                                "top_5": [reverse.get(value, str(value)) for value in hits[:5]],
                                "response": body})
                check(report, f"text_vector_finite_{len(results)}",
                      len(vector) == 512 and abs(sum(v * v for v in vector) - 1) < 0.001)
            report["queries"] = results
            report["quality_scope"] = "25 fixed synthetic queries; held-out labels fixed before run; no tuning. No population recall/precision claim."
            before = service.catalog.read_one("SELECT COUNT(*) FROM assets")[0]
            image_results = []
            for name in ("solid_red.png", "shape_blue_triangle.png", "text_cn.png"):
                with stage_upload((work / "library" / name).read_bytes()) as staged:
                    vector, encode_ms = timed(lambda: encode_image_query(
                        encoder, staged.path, space_id=binding["space_id"], dimension=512))
                    body, retrieval_ms = timed(lambda: search_assets(
                        service.catalog, query_vector=vector, top_k=50, **binding))
                    staged_dir = staged.directory
                hits = [int(item["asset_id"]) for item in body["hits"]]
                check(report, "image_self_top1_" + name, bool(hits) and hits[0] == ids[name], hits[:3])
                check(report, "upload_cleanup_" + name, not staged_dir.exists())
                image_results.append({"name": name, "encode_ms": encode_ms,
                                      "retrieval_ms": retrieval_ms, "response": body})
            report["image_queries"] = image_results
            check(report, "upload_does_not_add_assets",
                  service.catalog.read_one("SELECT COUNT(*) FROM assets")[0] == before)
            first = encoder.encode_image(work / "library/orientation_6.jpg")
            second = encoder.encode_image(work / "library/orientation_upright.png")
            cosine = sum(a * b for a, b in zip(first, second, strict=True))
            report["exif_equivalent_pixels_cosine"] = cosine
            if manifest["preprocessing"].get("orientation") == "exif-transpose-v1":
                check(report, "clip_exif_equivalent_pixels", cosine >= 0.9999, cosine)
            else:
                report["orientation_policy"] = "legacy stored pixels; canonical cosine is observation"
        report["actual_provider_usage"] = encoder._client.provider_usage()
    finally:
        encoder.close()


def install_small_weights(work: Path, module: Any) -> dict:
    source = module.verify(module.resolve(MODEL_DATA))
    target = module.resolve(work / "data")
    for item in target.files:
        origin = next(value.absolute_path for value in source.files if value.role == item.role)
        item.absolute_path.parent.mkdir(parents=True, exist_ok=True)
        if not item.absolute_path.exists():
            shutil.copy2(origin, item.absolute_path)
    module.verify(target)
    return {"files": len(target.files), "bytes": sum(item.expected_bytes for item in target.files),
            "source": "existing local pinned weights; copied, not downloaded"}


def exif(
    work: Path, report: dict, *, manifest_path: Path = CLIP_MANIFEST,
    clip_report: Path = OUTPUT / "P4-CORE-RELIABILITY-media-clip.json",
) -> None:
    from PIL import Image, ImageOps

    from photo_search.search.service import search_assets
    from photo_search.search.upload import encode_image_query, stage_upload
    from photo_search.workers.query_encoder import build_worker_query_encoder

    original = work / "library/orientation_6.jpg"
    canonical = work / "library/orientation_upright.png"
    with Image.open(original) as source, Image.open(canonical) as expected:
        actual = ImageOps.exif_transpose(source).convert("RGB")
        report["pixel_identity"] = {"original_exif": int(source.getexif().get(274, 1)),
                                    "normalized_size": list(actual.size),
                                    "sha256": hashlib.sha256(actual.tobytes()).hexdigest(),
                                    "canonical_sha256": hashlib.sha256(expected.tobytes()).hexdigest()}
        check(report, "normalized_pixels_identical", actual.tobytes() == expected.tobytes())
        actual.close()
    previous = json.loads(clip_report.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    binding = {"space_id": previous["build"]["model"]["space_id"],
               "generation_id": previous["build"]["generation"]["generation_id"], "dimension": 512}
    encoder, report["worker_start_and_model_load_ms"] = timed(lambda: build_worker_query_encoder(
        model_dir=MODEL_DATA / "models/chinese_clip/vit-b-16", manifest_path=manifest_path,
        device="cuda", device_policy="gpu_required"))
    try:
        original_vector = encoder.encode_image(original)
        canonical_vector = encoder.encode_image(canonical)
        with stage_upload(original.read_bytes()) as staged:
            upload_vector = encode_image_query(encoder, staged.path,
                                               space_id=binding["space_id"], dimension=512)
        cosine = lambda first, second: sum(a * b for a, b in zip(first, second, strict=True))
        report["cosines"] = {
            "catalog_original_vs_normalized_pixels": cosine(original_vector, canonical_vector),
            "uploaded_original_vs_normalized_pixels": cosine(upload_vector, canonical_vector),
            "uploaded_original_vs_catalog_original": cosine(upload_vector, original_vector),
        }
        if manifest["preprocessing"].get("orientation") == "exif-transpose-v1":
            check(report, "upload_is_normalized", report["cosines"][
                "uploaded_original_vs_normalized_pixels"] >= 0.9999, report["cosines"])
        else:
            report["orientation_policy"] = "legacy stored pixels; canonical cosine is observation"
        check(report, "catalog_upload_encoding_consistent", report["cosines"][
            "uploaded_original_vs_catalog_original"] >= 0.9999, report["cosines"])
        with service_for(work) as service:
            ids = assets(service)
            response = search_assets(service.catalog, query_vector=upload_vector, top_k=50, **binding)
            hits = [int(item["asset_id"]) for item in response["hits"]]
            report["retrieval"] = {"original_asset_id": ids[original.name],
                                   "canonical_asset_id": ids[canonical.name],
                                   "original_rank": hits.index(ids[original.name]) + 1,
                                   "canonical_rank": hits.index(ids[canonical.name]) + 1,
                                   "response": response}
        report["actual_provider_usage"] = encoder._client.provider_usage()
    finally:
        encoder.close()


def ocr(work: Path, report: dict) -> None:
    from unittest.mock import patch

    from rapidocr_onnxruntime import RapidOCR
    from rapidocr_onnxruntime.utils import infer_engine

    from photo_search.media.decode import load_normalized
    from photo_search.models.embedding.chinese_clip_onnx import _summarise_profile
    from photo_search.ocr import service as ocr_service
    from photo_search.ocr import weights
    from photo_search.ocr.engine import OcrEngineCache, _to_bgr, plan_slices, recognize_image
    from photo_search.ocr.processor import ocr_processor_spec
    from photo_search.search.service import search_assets

    report["weights"], report["weight_copy_verify_ms"] = timed(
        lambda: install_small_weights(work, weights))
    sessions = []
    constructions = []
    real_session = infer_engine.InferenceSession

    def profiled_session(path, *, sess_options, providers):
        sess_options.enable_profiling = True
        sess_options.profile_file_prefix = str(work / "temp" / f"ocr-profile-{len(sessions)}")
        session = real_session(path, sess_options=sess_options, providers=providers)
        sessions.append((Path(path).name, session))
        return session

    def profiled_engine(**kwargs):
        started = time.perf_counter()
        with patch.object(infer_engine, "InferenceSession", profiled_session):
            result = RapidOCR(**kwargs)
        constructions.append({"cuda_requested": kwargs["det_use_cuda"],
                              "elapsed_ms": (time.perf_counter() - started) * 1000})
        return result

    cache = OcrEngineCache(engine_factory=profiled_engine)
    report["loads"] = []
    engines = {}
    for device in ("gpu", "cpu"):
        engine, elapsed = timed(lambda: cache.engine(
            work / "data", resolved_device=device, orientation_retry=True))
        profile = ocr_processor_spec(device)
        engines[device] = engine
        report["loads"].append({"device": device, "elapsed_ms": elapsed,
                                "session_providers": engine.session_providers,
                                "processor_id": profile.processor_id,
                                "spec_revision": profile.spec_revision,
                                "config_hash": profile.config_hash,
                                "orientation_retry": engine.orientation_retry})
    observed = []
    for name in ("text_cn.png", "text_en.png", "text_dense.png", "blank.png"):
        decoded = load_normalized(work / "library" / name, long_edge=4096)
        document, elapsed = timed(lambda: recognize_image(engines["gpu"], decoded.image))
        decoded.image.close()
        observed.append({"name": name, "inference_ms": elapsed, "text": document.original_text,
                         "regions": [region.as_dict() for region in document.regions],
                         "slice_count": document.slice_count,
                         "working_size": document.working_size})
    report["direct_inference"] = observed
    check(report, "ocr_blank_has_no_text", not observed[-1]["regions"])
    check(report, "ocr_dense_is_sliced", observed[2]["slice_count"] > 1)
    for expected in ("深圳", "2026-10-15", "1280", "AB12345"):
        check(report, "ocr_cn_" + expected, expected in observed[0]["text"], observed[0]["text"])
    for expected in ("INVOICE", "1280", "AB12345"):
        check(report, "ocr_en_" + expected, expected in observed[1]["text"], observed[1]["text"])
    # Preserve model output before product overlap deduplication to locate omitted text.
    dense = load_normalized(work / "library/text_dense.png", long_edge=4096)
    dense_tiles = []
    for box in plan_slices(*dense.image.size):
        crop = dense.image.crop(tuple(int(value) for value in box))
        raw = engines["gpu"].read(_to_bgr(crop))
        dense_tiles.append({"box": box, "raw": [
            {"polygon": [[float(v) for v in point] for point in row[0]],
             "text": str(row[1]), "confidence": float(row[2])} for row in raw or []]})
        crop.close()
    raw = engines["gpu"].read(_to_bgr(dense.image))
    report["dense_diagnostic"] = {
        "scope": "Same model/input; raw tile and unsliced outputs are diagnostics, not an alternative product path",
        "tiles": dense_tiles, "unsliced_text": [str(row[1]) for row in raw or []],
        "product_text": observed[2]["text"],
    }
    dense.image.close()
    check(report, "ocr_dense_retains_line_prefix", "旅行记录" in observed[2]["text"],
          report["dense_diagnostic"])
    with service_for(work) as service:
        ids = assets(service)
        selected = [ids[row["name"]] for row in observed]
        targets = ocr_service.ocr_targets(service.catalog, asset_ids=selected)
        report["enqueue"] = ocr_service.enqueue_targets(
            service.catalog, targets, now_ms=service.catalog.now_ms(), requested_device="gpu", force=True)
        before_constructions = len(constructions)
        report["pipeline"], report["pipeline_total_ms"] = timed(
            lambda: ocr_service.run_queue(service.catalog, data_root=work / "data", engine_cache=cache))
        report["pipeline_model_initialization_ms"] = sum(
            item["elapsed_ms"] for item in constructions[before_constructions:])
        report["pipeline_excluding_model_initialization_ms"] = (
            report["pipeline_total_ms"] - report["pipeline_model_initialization_ms"])
        report["projection"] = ocr_service.catch_up_projection(service.catalog)
        report["jobs"] = [dict(row) for row in service.catalog.read_all(
            "SELECT subject_id,state,resolved_device,runtime_provider,last_error_code FROM jobs "
            "WHERE task_type='image_ocr' ORDER BY job_id")]
        check(report, "ocr_four_jobs_succeeded", report["pipeline"]["jobs"] == {"SUCCEEDED": 4},
              report["pipeline"])
        hits = []
        for text, target in (("深圳", "text_cn.png"), ("1280", "text_cn.png"),
                             ("AB12345", "text_cn.png"), ("INVOICE", "text_en.png")):
            body = search_assets(service.catalog, text=text, top_k=50)
            found = [int(item["asset_id"]) for item in body["hits"]]
            check(report, "ocr_search_" + text, ids[target] in found, found)
            hits.append({"query": text, "target": target, "response": body})
        report["text_search"] = hits
    report["engine_constructions"] = constructions
    report["actual_provider_usage"] = [
        {"model": name, **_summarise_profile(session.end_profiling())}
        for name, session in sessions
    ]


def face(work: Path, report: dict) -> None:
    import onnxruntime as ort

    from photo_search.face import service as face_service
    from photo_search.face import weights
    from photo_search.face.engine import FaceEngineCache, analyze_image
    from photo_search.media.decode import load_normalized
    from photo_search.models.embedding.chinese_clip_onnx import _summarise_profile

    report["weights"], report["weight_copy_verify_ms"] = timed(
        lambda: install_small_weights(work, weights))
    sessions = []

    def profiled_session(path, *, sess_options, providers):
        sess_options.enable_profiling = True
        sess_options.profile_file_prefix = str(work / "temp" / "face-profile")
        session = ort.InferenceSession(path, sess_options=sess_options, providers=providers)
        sessions.append((Path(path).name, session))
        return session

    cache = FaceEngineCache(session_factory=profiled_session)
    engines = {}
    report["loads"] = []
    for device in ("gpu", "cpu"):
        engines[device], elapsed = timed(lambda: cache.engine(work / "data", device=device))
        engine = engines[device]
        report["loads"].append({"device": device, "elapsed_ms": elapsed,
                                "provider": engine.provider,
                                "detector_providers": engine.detector_providers,
                                "recognizer_providers": engine.recognizer_providers,
                                "mixed_cuda_detection": engine.mixed_cuda_detection,
                                "recognition_cpu_fallback_disabled":
                                engine.recognition_cpu_fallback_disabled})
    names = ["blank.png", "shape_red_circle.png", "text_cn.png"]
    names += [name for name in ("public_astronaut.png", "public_astronaut_bright.png")
              if (work / "library" / name).exists()]
    documents = {}
    results = []
    for name in names:
        decoded = load_normalized(work / "library" / name, long_edge=1600)
        document, elapsed = timed(lambda: analyze_image(engines["gpu"], decoded.image))
        decoded.image.close()
        documents[name] = document
        faces = [{key: value for key, value in face.as_dict().items() if key != "embedding"}
                 for face in document.faces]
        results.append({"name": name, "inference_ms": elapsed, "faces": faces,
                        "embedding_norms": [sum(v * v for v in face.embedding) ** 0.5
                                            for face in document.faces]})
        expected = 1 if name.startswith("public_astronaut") else 0
        check(report, "face_count_" + name, len(document.faces) == expected, len(document.faces))
    report["direct_inference"] = results
    if all(documents.get(name) and documents[name].faces for name in names[-2:]):
        first, second = (documents[name].faces[0].embedding for name in names[-2:])
        report["same_fixture_brightness_cosine"] = sum(a * b for a, b in zip(first, second))
    with service_for(work) as service:
        ids = assets(service)
        report["pipeline"], report["pipeline_warm_inference_publish_ms"] = timed(
            lambda: face_service.detect(service.catalog, data_root=work / "data", device="gpu",
                                        asset_ids=[ids[name] for name in names], engine_cache=cache))
        report["published_faces"] = [dict(row) for row in service.catalog.read_all(
            "SELECT asset_id,face_id,quality_state FROM faces ORDER BY asset_id,face_id")]
        report["jobs"] = [dict(row) for row in service.catalog.read_all(
            "SELECT subject_id,state,resolved_device,runtime_provider,last_error_code FROM jobs "
            "WHERE task_type='image_face' ORDER BY job_id")]
        check(report, "face_jobs_succeeded", report["pipeline"]["jobs"] ==
              {"SUCCEEDED": len(names)}, report["pipeline"])
    report["actual_provider_usage"] = [
        {"model": name, **_summarise_profile(session.end_profiling())}
        for name, session in sessions
    ]
    report["quality_scope"] = "Only synthetic negatives plus one packaged public face and brightness derivative; no person identity/association accuracy claim."


def ocr_stability(work: Path, report: dict) -> None:
    from photo_search.ocr import contract
    from photo_search.ocr.engine import OcrEngineCache
    from photo_search.ocr.processor import artifact_ref, extract_for_asset, ocr_processor_spec

    # No profiler or mock: re-run the exact product extraction and compare its immutable bytes.
    cache = OcrEngineCache()
    engine, report["model_load_ms"] = timed(lambda: cache.engine(
        work / "data", resolved_device="gpu", runtime_provider="CUDAExecutionProvider",
        orientation_retry=True))
    profile = ocr_processor_spec("gpu")
    report["ocr_identity"] = {
        "processor_id": profile.processor_id, "spec_revision": profile.spec_revision,
        "config_hash": profile.config_hash, "orientation_retry": engine.orientation_retry,
    }
    report["profiling_enabled"] = False
    with service_for(work) as service:
        selected = assets(service)["text_cn.png"]
        original = service.catalog.read_one(
            "SELECT a.sha256, ar.checksum_sha256,ar.storage_uri FROM assets a JOIN artifacts ar "
            "ON ar.input_revision=a.sha256 WHERE a.asset_id=? AND ar.processor_id=?",
            (selected, contract.OCR_GPU_PROCESSOR_ID))
        prior = json.loads((work / "data" / original["storage_uri"]).read_text(encoding="utf-8"))
        attempts = []
        for number in range(2):
            payload, elapsed = timed(lambda: extract_for_asset(
                service.catalog, asset_id=selected, input_revision=original["sha256"],
                data_root=work / "data", engine_cache=cache, resolved_device="gpu",
                runtime_provider="CUDAExecutionProvider", processor_id=contract.OCR_GPU_PROCESSOR_ID))
            ref = artifact_ref(payload)
            attempts.append({"attempt": number + 1, "inference_ms": elapsed,
                             "checksum_sha256": ref["checksum_sha256"], "payload": payload})
            check(report, f"immutable_ocr_reproduction_{number + 1}",
                  ref["checksum_sha256"] == original["checksum_sha256"],
                  {"prior": original["checksum_sha256"], "current": ref["checksum_sha256"]})
        report["prior"] = {"checksum_sha256": original["checksum_sha256"], "payload": prior}
        report["attempts"] = attempts
        report["same_session_attempts_identical"] = attempts[0]["checksum_sha256"] == attempts[1]["checksum_sha256"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "scan", "clip", "exif", "ocr", "face", "ocr_stability"))
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--clip-provider", default="chinese-clip-vit-b-16",
        choices=("chinese-clip-vit-b-16", "chinese-clip-vit-b-16-exif-v2"),
    )
    parser.add_argument(
        "--report-prefix", default="P4-CORE-RELIABILITY-media",
        help="artifact basename prefix; use distinct values to preserve previous runs",
    )
    args = parser.parse_args()
    if not args.report_prefix or Path(args.report_prefix).name != args.report_prefix:
        raise ValueError("report prefix must be a plain filename prefix")
    manifest_path = CLIP_MANIFEST.with_name(args.clip_provider + ".json")
    work = args.work.resolve()
    if not work.is_relative_to((ROOT / ".tmp").resolve()):
        raise ValueError("The diagnostic root must stay under this repository's .tmp")
    work.mkdir(parents=True, exist_ok=True)
    temp = work / "temp"
    temp.mkdir(exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temp)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HOME=str(work / "hf-cache"), TORCH_HOME=str(work / "torch-cache"),
                      UV_CACHE_DIR=str(ROOT / ".uv-cache"), PYTHONDONTWRITEBYTECODE="1")
    import tempfile
    tempfile.tempdir = str(temp)
    report = {"phase": args.phase, "work_root": str(work), "python": sys.version,
              "clip_provider": args.clip_provider, "clip_manifest": str(manifest_path),
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "timing_scope": "Diagnostic same-host timings with concurrent CPU/disk work; not exclusive-load SLA. CLIP/EXIF/OCR/face use ORT profiling; OCR stability intentionally does not.",
              "scope": "Small synthetic/public-fixture functional observations, not 300k real-photo throughput or formal model quality acceptance", "checks": []}
    start = time.perf_counter()
    try:
        if args.phase == "clip":
            clip(work, report, manifest_path=manifest_path)
        elif args.phase == "exif":
            exif(work, report, manifest_path=manifest_path,
                 clip_report=OUTPUT / f"{args.report_prefix}-clip.json")
        else:
            {"prepare": prepare, "scan": scan_media, "ocr": ocr,
             "face": face, "ocr_stability": ocr_stability}[args.phase](work, report)
    except Exception as error:
        report["exception"] = {"type": type(error).__name__, "code": getattr(error, "code", None),
                               "message": str(error), "traceback": traceback.format_exc()}
        check(report, "phase_completed", False, report["exception"])
    report["elapsed_ms"] = (time.perf_counter() - start) * 1000
    report["tool_sha256"] = digest(Path(__file__))
    failed = [item for item in report["checks"] if not item["passed"]]
    report["status"] = "FAIL" if failed else "SCOPED PASS"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT / f"{args.report_prefix}-{args.phase}.json"
    xml_path = OUTPUT / f"{args.report_prefix}-{args.phase}.xml"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                    default=str), encoding="utf-8")
    suite = ET.Element("testsuite", name=f"media_smoke_{args.phase}",
                       tests=str(len(report["checks"])), failures=str(len(failed)),
                       errors="0", skipped="0", time=str(report["elapsed_ms"] / 1000))
    for item in report["checks"]:
        case = ET.SubElement(suite, "testcase", classname=f"media.{args.phase}", name=item["name"])
        if not item["passed"]:
            ET.SubElement(case, "failure", message=item["name"]).text = json.dumps(
                item["detail"], ensure_ascii=False, default=str)
    ET.ElementTree(suite).write(xml_path, encoding="utf-8", xml_declaration=True)
    print(json.dumps({"phase": args.phase, "status": report["status"],
                      "checks": len(report["checks"]), "failures": [item["name"] for item in failed],
                      "elapsed_ms": report["elapsed_ms"], "report": str(json_path)},
                     ensure_ascii=False), flush=True)
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
