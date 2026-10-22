"""Freeze vocabulary coverage before scores; evaluate bounded album tag rank fusion.

This is an offline experiment. Unknown object judgments never become negatives;
machine scores never modify production tags, FTS, filters, or library records.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np

from tools.evaluation.album_light_tagger import COMMIT, FILES, PREPROCESS, parse_mapping
from tools.evaluation.album_linear_probe import space_spec
from tools.evaluation.prepare_broad_album_data import Inputs, digest

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"baseline": 0.0, "tags_only": None, "rrf_tag025": .25, "rrf_tag050": .5, "rrf_equal": 1.0}
ALIASES = {
    "sofa": ("sofa", "couch"), "couch": ("sofa", "couch"),
    "airplane": ("airplane", "aeroplane"), "motorcycle": ("motorcycle", "motorbike"),
    "cell phone": ("cell phone", "mobile phone", "cellphone"),
    "mobile phone": ("cell phone", "mobile phone", "cellphone"),
    "tv": ("tv", "television"), "teddy bear": ("teddy bear", "teddy"),
    "dining table": ("dining table", "dinner table"),
    "human face": ("human face", "face"), "human head": ("human head", "head"),
    "human hair": ("human hair", "hair"), "human arm": ("human arm", "arm"),
    "human hand": ("human hand", "hand"), "human leg": ("human leg", "leg"),
    "human eye": ("human eye", "eye"), "human ear": ("human ear", "ear"),
    "human nose": ("human nose", "nose"), "human mouth": ("human mouth", "mouth"),
    "clothing": ("clothing", "clothes"), "footwear": ("footwear", "shoe"),
    "vehicle registration plate": ("vehicle registration plate", "license plate"),
    "remote": ("remote", "remote control"), "hair drier": ("hair drier", "hair dryer"),
    "handbag": ("handbag", "purse"),
    "water bottle": ("water bottle", "bottled water"),
    "tomato": ("tomato", "tomatoes"), "noodle": ("noodle", "noodles"),
}
# The official ImageNet-to-product rules contain scene heuristics (neck brace ->
# portrait, car wheel -> car). Only these checked biological groups are admitted.
SAFE_LIGHT_GROUPS = {"cat", "dog", "bird", "fish", "bear", "flower", "insect"}
WILD_DOGS = {"coyote", "dingo", "dhole", "african hunting dog", "hyena"}
# Fixed before any new scores. Identity/time words and generic people nouns are
# deliberately absent: a person's name must not become an arbitrary person tag.
PHOTO_CONCEPTS = (
    "water bottle", "warehouse", "storefront", "noodle", "counter", "tent", "aquarium",
    "kitchen", "bowl", "mop", "floor", "sparkler", "dam", "sunset", "garden", "map",
    "dropper", "train", "store", "corridor", "wedding", "statue", "zoo", "performance",
    "bottle cap", "chicken", "menu", "costume", "key", "rocket", "buffalo", "shop",
    "machinery", "restaurant", "food", "car", "dinosaur", "passport", "airport",
    "tomato", "cable car", "drawing", "spoon", "egg", "bird", "bridge", "wristband",
    "greenhouse", "actor", "movie", "schedule", "mink", "sand sculpture",
)
CN_FIXED = {"person": "人", "car": "汽车", "bicycle": "自行车", "teddy bear": "玩具熊",
            "human face": "人脸", "human hair": "头发", "clothing": "衣服", "footwear": "鞋子"}
FAMILY_PRIORITY = ("Human body", "Person", "Clothing", "Animal", "Plant", "Food", "Vehicle", "Furniture", "Building")
EVALUATION_POLICY = {
    "schema": "album-tag-fusion-evaluation-policy-v1", "family_priority": FAMILY_PRIORITY,
    "family_assignment": "first matching self/ancestor name; otherwise Other",
    "calibration_admission": "at least one positive and one explicit negative within calibration for AP",
    "metric_admission": "AP requires both classes; recall requires positives; unknown never becomes false",
    "selection": "maximize family-balanced calibration AP over baseline and three fixed RRF variants; baseline wins ties",
    "tag_only": "diagnostic only; not eligible to replace baseline after calibration",
    "precision": "known positives / known judgments among actual top-k, with unknown fraction separately",
    "small_subject": "COCO max non-crowd bounding-box area <= 5% of image; presence recall only",
    "confidence_intervals": "2000 seed-20261021 paired bootstrap over concept groups; bilingual pairs averaged before sampling",
}


def frozen_policy(plan_path: Path) -> Path:
    """Freeze statistical details without rewriting the existing vocabulary plan."""
    path = plan_path.parent / "evaluation-policy.json"
    raw = json.dumps(EVALUATION_POLICY, ensure_ascii=False, indent=2).encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("frozen evaluation policy differs")
    else:
        with path.open("xb") as stream:
            stream.write(raw)
    return path


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", value.lower())).strip()


def names_for(term: str) -> set[str]:
    return {norm(value) for value in ALIASES.get(norm(term), (term,))}


def indices_for(term: str, model: str, vocabulary: list[str], light_rules: list[dict]) -> list[int]:
    names = names_for(term)
    result = []
    duplicate = {value for value, count in Counter(vocabulary).items() if count > 1}
    for i, name in enumerate(vocabulary):
        if name in duplicate:  # Ambiguous ImageNet names cannot select both senses.
            continue
        matched = norm(name) in names
        if model == "light" and norm(term) in SAFE_LIGHT_GROUPS:
            matched |= light_rules[i]["label"] == norm(term) and norm(name) not in WILD_DOGS
        if matched:
            result.append(i)
    return result


def photo_concepts(text: str) -> list[str]:
    text = " " + norm(text) + " "
    found: list[tuple[str, str]] = []
    for concept in sorted(PHOTO_CONCEPTS, key=len, reverse=True):
        for alias in sorted(names_for(concept), key=len, reverse=True):
            # Only regular plural suffixes are automatic; aliases own irregulars.
            match = re.search(r"(?<!\w)" + re.escape(alias) + r"(?:s|es)?(?!\w)", text)
            if match:
                found.append((concept, match.group()))
                text = text[:match.start()] + " " * len(match.group()) + text[match.end():]
                break
    return [concept for concept, _ in found]


def freeze(data_dir: Path, out_dir: Path, root: Path = ROOT) -> dict:
    reader = Inputs(root)
    out_dir = reader.path(out_dir)
    if out_dir.exists():
        raise ValueError("mapping output exists; never revise a frozen experiment")
    ram_dir = ".tmp/search-quality-product/broad-tags/official/ram/data/"
    ram = reader.raw(ram_dir + "ram_tag_list.txt").decode("utf-8").strip().splitlines()
    chinese = reader.raw(ram_dir + "ram_tag_list_chinese.txt").decode("utf-8").strip().splitlines()
    light_dir = ".tmp/search-quality-product/broad-tags/light-model/"
    light, rules = parse_mapping(reader.raw(light_dir + "labels.txt").decode("utf-8"),
                                reader.raw(light_dir + "rules.go").decode("utf-8"))
    if len(ram) != 4585 or len(chinese) != len(ram):
        raise ValueError("official RAM vocabulary size mismatch")
    vocab = {"ram": ram, "light": light}
    datasets, baseline_texts = {}, {}
    for name in ("openimages400", "coco480", "photobench1047"):
        path = data_dir / (name + ".json")
        data = reader.json(path)
        queries = []
        originals = {q["query_id"]: q for q in data["queries"]}
        for query in data["queries"]:
            if name != "photobench1047" and not query["positive_asset_ids"]:
                continue
            if name == "photobench1047":
                pair = query["query_id"].rsplit("-", 1)[0]
                concepts = photo_concepts(originals[pair + "-en"]["text"])
                variants = [(query["query_id"].rsplit("-", 1)[1], query["text"])]
            else:
                concepts = [query["text"]]
                variants = [("en", query["text"])]
                # Additional Chinese paired diagnostic only for pre-specified
                # supported OI categories; no translation selection using scores.
                if name == "openimages400" and query["diagnostic_supported"]:
                    matches = indices_for(query["text"], "ram", ram, rules)
                    translation = CN_FIXED.get(query["text"])
                    if translation is None and len(matches) == 1:
                        translation = chinese[matches[0]]
                    if translation:
                        variants.append(("cn", translation.strip()))
            tags = {model: [{"concept": concept, "indices": indices_for(concept, model, vocabulary, rules)}
                            for concept in concepts] for model, vocabulary in vocab.items()}
            for language, text in variants:
                query_id = query["query_id"] if name == "photobench1047" else query["query_id"] + ":" + language
                baseline_id = query_id if name == "photobench1047" else "broad-" + digest(text.encode("utf-8"))[:16]
                if name != "photobench1047":
                    baseline_texts[baseline_id] = text
                queries.append({"query_id": query_id, "source_query_id": query["query_id"],
                    "group_id": query["query_id"].rsplit("-", 1)[0] if name == "photobench1047" else query["query_id"],
                    "language": language, "text": text, "baseline_query_id": baseline_id, "tags": tags})
        datasets[name] = {"manifest": str(reader.path(path)), "manifest_sha256": reader.hashes[reader.key(path)], "queries": queries}
    protocol = {
        "schema": "album-tag-fusion-plan-v1", "status": "FROZEN_BEFORE_NEW_MODEL_SCORES",
        "methods": METHODS, "rrf_k": 60,
        "tag_concept_pooling": {"ram": "maximum across synonymous/subtype sigmoid scores",
                                "light": "sum mutually exclusive class softmax within each view, then maximum over real views",
                                "multiple_concepts": "minimum across mapped concepts; retain complete original vector query"},
        "unmapped": "exact baseline fallback; never remove an image or query",
        "baseline": "formal Qwen EXIF-v2 unit-normalized MRL512; existing matching image caches and new formal text vectors",
        "selection": "select among fixed fusion variants using OI calibration family-balanced paired AP; test cannot select parameters",
        "unknown": "masked judged-only AP/precision plus unknown fraction at real top20/50; no unknown-as-negative",
        "bootstrap": "2000 deterministic paired resamples over concept groups; bilingual variants averaged first; correlated hierarchy remains limitation",
        "thresholds": "no confidence thresholds fitted; official tagger thresholds diagnostic only",
        "limitations": ["Open Images enriched cat/dog and confirmed-negative sample, not user-library prevalence",
                        "Previously used public datasets; pretraining overlaps unproven; COCO has positive-only object labels",
                        "Object English queries and supported OI Chinese pairs; PhotoBench original bilingual queries retained",
                        "PhotoPrism mapping heuristics are not truth: only exact nouns and audited biological grouping used",
                        "Unknown-heavy precision is judged-only, not complete-gallery precision"],
        "datasets": datasets, "vocabularies": vocab, "input_sha256": reader.hashes,
        "mapping_policy": {"aliases": ALIASES, "safe_light_groups": sorted(SAFE_LIGHT_GROUPS),
                           "excluded_wild_dogs": sorted(WILD_DOGS), "photobench_concepts": PHOTO_CONCEPTS,
                           "chinese_fixed": CN_FIXED, "ambiguous_duplicate_names": "unmapped"},
    }
    out_dir.mkdir(parents=True)
    (out_dir / "plan.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline = [{"query_id": key, "text": text} for key, text in sorted(baseline_texts.items())]
    (out_dir / "queries.json").write_text(json.dumps({"schema": "broad-album-queries-v1", "queries": baseline}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"queries_to_encode": len(baseline), "datasets": {name: {"queries": len(data["queries"]),
        "mapped": {model: sum(any(c["indices"] for c in q["tags"][model]) for q in data["queries"]) for model in vocab}}
        for name, data in datasets.items()}, "plan_sha256": digest((out_dir / "plan.json").read_bytes()),
        "queries_sha256": digest((out_dir / "queries.json").read_bytes())}


def array(reader: Inputs, path: Path, expected_sha: str) -> np.ndarray:
    raw = reader.raw(path)
    if digest(raw) != expected_sha:
        raise ValueError("array checksum mismatch")
    result = np.load(io.BytesIO(raw), allow_pickle=False)
    if result.dtype.kind != "f" or not np.isfinite(result).all():
        raise ValueError("array values invalid")
    return result


def normalize(matrix: np.ndarray, dimension: int) -> np.ndarray:
    if matrix.ndim != 2 or matrix.shape[1] < dimension or not np.isfinite(matrix).all():
        raise ValueError("invalid vector matrix")
    matrix = np.asarray(matrix[:, :dimension], dtype=np.float64)
    lengths = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(lengths < 1e-12):
        raise ValueError("zero embedding")
    return matrix / lengths


def verify_compatibility(proof: dict, image_record: dict, query_record: dict | None = None) -> None:
    if (proof.get("schema") != "category-head-compatibility-v1" or proof.get("status") != "PASS"
            or proof.get("dimension") != 512 or proof.get("decoded_rgb_equal") is not True
            or proof.get("image_comparison", {}).get("passed") is not True
            or not 0 <= proof.get("text_max_abs_error", float("inf")) <= 2e-6
            or not 0 <= proof.get("image_comparison", {}).get("max_abs_error", float("inf")) <= 2e-6
            or proof.get("source", {}).get("space_spec") != space_spec(image_record, 512)):
        raise ValueError("formal-image compatibility proof does not bind cached image space")
    if query_record is not None and any(query_record.get(key) != proof.get(key) for key in ("processor_id", "space_id")):
        raise ValueError("formal query space differs from compatibility proof")


def verify_tagger_identity(record: dict, model: str, expected_ram: dict | None = None) -> None:
    lock = record.get("model_lock", {})
    if model == "ram":
        if (expected_ram is None or lock != expected_ram or record.get("score_type") != "sigmoid_scores"
                or record.get("settings", {}).get("image_size") != 384
                or record.get("settings", {}).get("limit") is not None):
            raise ValueError("RAM model/source/preprocessing lock differs")
    else:
        files = {name: {"url": url, "sha256": sha, "bytes": size} for name, (url, sha, size) in FILES.items()}
        if (lock.get("photoprism_commit") != COMMIT or lock.get("files") != files
                or lock.get("source_checkpoint_revision") != "1c56a76355000c79568559d34ba3fa24416b5107"
                or record.get("preprocess") != PREPROCESS or record.get("smoke_limit") != 0
                or record.get("score_type") != "probabilities"):
            raise ValueError("light model/source/preprocessing lock differs")


def tag_inputs(reader: Inputs, path: Path, model: str, plan: dict, data: dict) -> tuple[dict, np.ndarray, list[int] | None]:
    record = reader.json(path / "result.json")
    if (model == "ram" and (record.get("schema") != "album-ram-plus-run-v1" or record.get("status") != "PASS")) or (model == "light" and record.get("schema") != "album-tagging-run-v1"):
        raise ValueError("tag run is incomplete or unsupported")
    verify_tagger_identity(record, model, reader.json(ROOT / "tools/evaluation/album_tagging_lock.json") if model == "ram" else None)
    expected = plan["datasets"][data["dataset"]]["manifest_sha256"]
    if record.get("dataset_manifest_sha256", record.get("manifest_sha256")) != expected:
        raise ValueError("tag run dataset manifest identity differs")
    if [(a["asset_id"], a["sha256"]) for a in record["assets"]] != [(a["asset_id"], a["sha256"]) for a in data["assets"]]:
        raise ValueError("tag run rows differ")
    classes = [row["english"] for row in record["classes"]] if model == "ram" else record["classes"]
    if classes != plan["vocabularies"][model]:
        raise ValueError("tag run vocabulary order differs")
    class_ids = [row["index"] for row in record["classes"]] if model == "ram" else record["class_ids"]
    if class_ids != list(range(len(classes))):
        raise ValueError("tag run class indices differ")
    scores = array(reader, path / "scores.npy", record["scores_sha256"])
    if scores.shape != (len(data["assets"]), len(classes)) or np.any((scores < 0) | (scores > 1)):
        raise ValueError("tag scores shape or range invalid")
    counts = None
    if model == "light":
        logits = array(reader, path / "view_logits.npy", record["view_logits_sha256"])
        if [row["asset_id"] for row in record["per_image_ms"]] != [row["asset_id"] for row in data["assets"]]:
            raise ValueError("light per-image view-count rows differ")
        counts = [row["view_count"] for row in record["per_image_ms"]]
        if logits.shape != (len(data["assets"]), 2, len(classes)) or any(c not in (1, 2) for c in counts) or len(counts) != len(scores):
            raise ValueError("light view logits or counts invalid")
        scores = np.exp(logits.astype(np.float64) - logits.max(axis=2, keepdims=True))
        scores /= scores.sum(axis=2, keepdims=True)
        # Padded slots must not contribute to max across actual views.
        for i, count in enumerate(counts):
            scores[i, count:] = 0
    return record, scores, counts


def concept_scores(scores: np.ndarray, concepts: list[dict], model: str) -> np.ndarray | None:
    mapped = [concept["indices"] for concept in concepts if concept["indices"]]
    if not mapped:
        return None
    parts = []
    for indices in mapped:
        if len(set(indices)) != len(indices) or min(indices) < 0 or max(indices) >= scores.shape[-1]:
            raise ValueError("invalid frozen class index")
        parts.append(scores[:, :, indices].sum(axis=2).max(axis=1) if model == "light" else scores[:, indices].max(axis=1))
    return np.min(parts, axis=0)


def ranking(scores: np.ndarray) -> np.ndarray:
    if scores.ndim != 1 or not np.isfinite(scores).all():
        raise ValueError("ranking requires finite scalar scores")
    # Dataset order is frozen, so mergesort supplies a reproducible tie-break.
    return np.argsort(-scores, kind="stable")


def fused_order(baseline: np.ndarray, tags: np.ndarray | None, method: str) -> np.ndarray:
    base = ranking(baseline)
    if method == "baseline" or tags is None or np.ptp(tags) == 0:
        return base
    if len(tags) != len(baseline):
        raise ValueError("ranking score population differs")
    tagged = ranking(tags)
    if method == "tags_only":
        return tagged
    if method not in METHODS:
        raise ValueError("unplanned fusion method")
    # Midranks prevent equal/saturated class scores from arbitrarily promoting
    # a frozen-row prefix. Stable baseline order wins final fused-score ties.
    def ranks(values: np.ndarray) -> np.ndarray:
        order = ranking(values)
        result = np.empty(len(values), dtype=np.float64)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and values[order[end]] == values[order[start]]:
                end += 1
            result[order[start:end]] = (start + 1 + end) / 2
            start = end
        return result
    mixed = 1 / (60 + ranks(baseline)) + METHODS[method] / (60 + ranks(tags))
    return base[np.argsort(-mixed[base], kind="stable")]


def measured(order: np.ndarray, truth: np.ndarray, small: np.ndarray | None = None) -> dict[str, float | int | None]:
    if len(order) != len(truth) or set(order) != set(range(len(truth))) or not np.isin(truth, [-1, 0, 1]).all():
        raise ValueError("ranking/judgment population invalid")
    ordered = truth[order]
    positive, negative = int((truth == 1).sum()), int((truth == 0).sum())
    known = ordered[ordered >= 0]
    ap = None
    if positive and negative:
        precision = np.cumsum(known == 1) / np.arange(1, len(known) + 1)
        ap = float(precision[known == 1].sum() / positive)
    result = {"positive": positive, "negative": negative, "unknown": int((truth < 0).sum()), "judged_ap": ap}
    for k in (20, 50):
        top = ordered[:k]
        pos, neg = int((top == 1).sum()), int((top == 0).sum())
        result.update({f"recall_at_{k}": pos / positive if positive else None,
            f"known_precision_at_{k}": pos / (pos + neg) if pos + neg and negative else None,
            f"unknown_fraction_at_{k}": float((top < 0).mean()) if len(top) else None,
            f"negative_retrieval_fraction_at_{k}": neg / negative if negative else None})
        if small is not None:
            if small.shape != truth.shape or np.any(small & (truth != 1)):
                raise ValueError("small subject must have positive ground truth")
            result[f"small_recall_at_{k}"] = float(small[order[:k]].sum() / small.sum()) if small.any() else None
    return result


def group_values(rows: list[dict], method: str, metric: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        value = row["metrics"][method].get(metric)
        if value is not None:
            grouped.setdefault(row["group_id"], []).append(value)
    return {key: float(np.mean(values)) for key, values in grouped.items()}


def family_mean(rows: list[dict], method: str, metric: str) -> float | None:
    values = group_values(rows, method, metric)
    families: dict[str, list[float]] = {}
    lookup = {row["group_id"]: row["family"] for row in rows}
    for group, value in values.items():
        families.setdefault(lookup[group], []).append(value)
    return float(np.mean([np.mean(values) for values in families.values()])) if families else None


def select_method(calibration: list[dict]) -> dict:
    eligible = [name for name in METHODS if name != "tags_only"]
    values = {name: family_mean(calibration, name, "judged_ap") for name in eligible}
    best = "baseline"
    for name in eligible[1:]:
        if values[name] is not None and (values[best] is None or values[name] > values[best] + 1e-12):
            best = name
    return {"chosen_method": best, "calibration_family_balanced_ap": values,
            "rule": EVALUATION_POLICY["selection"], "calibration_groups": len(group_values(calibration, "baseline", "judged_ap"))}


def paired_summary(rows: list[dict], method: str, metric: str) -> dict:
    base, tested = group_values(rows, "baseline", metric), group_values(rows, method, metric)
    groups = sorted(base.keys() & tested.keys())
    if not groups:
        return {"groups": 0, "baseline": None, "method": None, "delta": None, "ci95": None}
    delta = np.array([tested[key] - base[key] for key in groups])
    lower_is_better = metric.startswith(("unknown_fraction_", "negative_retrieval_fraction_"))
    benefit = -delta if lower_is_better else delta
    rng = np.random.default_rng(20261021)
    sample = delta[rng.integers(len(delta), size=(2000, len(delta)))].mean(axis=1)
    return {"groups": len(groups), "baseline": float(np.mean([base[key] for key in groups])),
            "method": float(np.mean([tested[key] for key in groups])), "delta": float(delta.mean()),
            "ci95": np.quantile(sample, [.025, .975]).tolist(),
            "preferred_direction": "lower" if lower_is_better else "higher",
            "improved_groups": int((benefit > 1e-12).sum()), "regressed_groups": int((benefit < -1e-12).sum()),
            "family_balanced_baseline": family_mean(rows, "baseline", metric),
            "family_balanced_method": family_mean(rows, method, metric)}


def truths(data: dict, query: dict) -> tuple[np.ndarray, np.ndarray | None, str, bool]:
    ids = [row["asset_id"] for row in data["assets"]]
    if data["dataset"] == "photobench1047":
        label = next(row for row in data["evaluation"]["queries"] if row["query_id"] == query["source_query_id"])
        positive = set(label["ground_truth_asset_ids"])
        if not positive <= set(ids):
            raise ValueError("PhotoBench ground truth outside gallery")
        dimensions = sorted(key for key, value in label["dimensions"].items() if value)
        return np.array([int(value in positive) for value in ids]), None, "+".join(dimensions) or "Other", len(dimensions) >= 2
    source = next(row for row in data["queries"] if row["query_id"] == query["source_query_id"])
    pos, neg = set(source["positive_asset_ids"]), set(source["negative_asset_ids"])
    if pos & neg or not (pos | neg) <= set(ids):
        raise ValueError("contradictory or out-of-gallery object judgments")
    truth = np.array([1 if value in pos else 0 if value in neg else -1 for value in ids])
    small = None
    family = "Other"
    if data["dataset"] == "coco480":
        small_ids = set()
        for row in data["judgments"]:
            if row["class_id"] != query["source_query_id"]:
                continue
            boxes = [box["box_area_fraction"] for box in row["instances"] if not box["iscrowd"]]
            if boxes and max(boxes) <= .05:
                small_ids.add(row["asset_id"])
        small = np.array([value in small_ids for value in ids])
    else:
        names = data["category_names"]
        ancestors = set(data["ancestors"].get(query["source_query_id"], [])) | {query["source_query_id"]}
        candidates = {names[value] for value in ancestors}
        family = next((name for name in FAMILY_PRIORITY if name in candidates), "Other")
    return truth, small, family, False


def evaluate(plan_path: Path, dataset: str, model: str, tag_run: Path, query_run: Path,
             output: Path, selection_path: Path | None, compatibility_path: Path, root: Path = ROOT) -> dict:
    reader = Inputs(root)
    source_before = {str(path.relative_to(ROOT)): digest(reader.raw(path)) for path in (
        Path(__file__).resolve(), ROOT / "tools/evaluation/prepare_broad_album_data.py",
        ROOT / "tools/evaluation/album_linear_probe.py", ROOT / "tools/evaluation/album_light_tagger.py")}
    output = reader.path(output)
    if output.exists():
        raise ValueError("evaluation output exists; preserve original results")
    plan = reader.json(plan_path)
    policy = reader.json(plan_path.parent / "evaluation-policy.json")
    if json.dumps(policy, sort_keys=True) != json.dumps(EVALUATION_POLICY, sort_keys=True):
        raise ValueError("evaluation differs from frozen policy")
    entry = plan["datasets"][dataset]
    data = reader.json(entry["manifest"])
    if reader.hashes[reader.key(entry["manifest"])] != entry["manifest_sha256"]:
        raise ValueError("frozen dataset manifest changed")
    compatibility = reader.json(compatibility_path)
    cache = data["embedding_caches"]["qwen"]
    image_record = reader.json(Path(cache["directory"]) / "result.json")
    verify_compatibility(compatibility, image_record)
    images = normalize(array(reader, Path(cache["image_vectors"]), cache["image_vectors_sha256"]), 512)
    tag_record, scores, _ = tag_inputs(reader, tag_run, model, plan, data)
    if dataset == "photobench1047":
        query_record = reader.json(Path(cache["directory"]) / "result.json")
        query_matrix = normalize(array(reader, Path(cache["directory"]) / "queries.npy", query_record["vectors_sha256"]["queries.npy"]), 512)
    else:
        query_record = reader.json(query_run / "result.json")
        if query_record.get("schema") != "album-query-baseline-v1" or query_record.get("status") != "PASS" or query_record.get("dimension") != 512:
            raise ValueError("formal query baseline identity incomplete")
        verify_compatibility(compatibility, image_record, query_record)
        expected_queries = reader.raw(plan_path.parent / "queries.json")
        if query_record["queries"] != json.loads(expected_queries)["queries"]:
            raise ValueError("formal text vectors differ from frozen query ordering")
        vector_sha = query_record["vectors_sha256"]
        vector_sha = vector_sha["queries.npy"] if isinstance(vector_sha, dict) else vector_sha
        query_matrix = normalize(array(reader, query_run / "queries.npy", vector_sha), 512)
    queries = query_record["queries"]
    if len(query_matrix) != len(queries) or len({row["query_id"] for row in queries}) != len(queries):
        raise ValueError("query matrix rows differ")
    positions = {row["query_id"]: i for i, row in enumerate(queries)}
    gallery_scores = images @ query_matrix.T
    rows = []
    started = time.perf_counter()
    for query in entry["queries"]:
        if query["baseline_query_id"] not in positions:
            raise ValueError("missing baseline query")
        index = positions[query["baseline_query_id"]]
        if queries[index]["text"] != query["text"]:
            raise ValueError("baseline query text differs")
        truth, small, family, complex_query = truths(data, query)
        tags = concept_scores(scores, query["tags"][model], model)
        for split in (("calibration", "test") if dataset == "openimages400" else ("all",)):
            selected = np.array([i for i, asset in enumerate(data["assets"]) if split == "all" or data["split_by_asset"][str(asset["asset_id"]) ] == split])
            row_split = split
            if dataset == "photobench1047":
                label = next(r for r in data["evaluation"]["queries"] if r["query_id"] == query["source_query_id"])
                row_split = label["split"]
            metrics = {method: measured(fused_order(gallery_scores[selected, index], None if tags is None else tags[selected], method),
                                       truth[selected], None if small is None else small[selected]) for method in METHODS}
            rows.append({**{key: query[key] for key in ("query_id", "source_query_id", "group_id", "language", "text")},
                "split": row_split, "family": family, "complex_query": complex_query,
                "diagnostic_supported": (next(r for r in data["queries"] if r["query_id"] == query["source_query_id"]).get("diagnostic_supported", False)
                                         if dataset != "photobench1047" else False),
                "mapped_concepts": [row["concept"] for row in query["tags"][model] if row["indices"]], "metrics": metrics})
    if dataset == "openimages400":
        selection = select_method([row for row in rows if row["split"] == "calibration"])
        selection.update(model=model, source="Open Images calibration images only")
    else:
        if selection_path is None:
            raise ValueError("held-out evaluation requires original OI calibration selection")
        selected_run = reader.json(selection_path)
        if selected_run.get("dataset") != "openimages400" or selected_run.get("model") != model or selected_run.get("plan_sha256") != reader.hashes[reader.key(plan_path)]:
            raise ValueError("selection source identity differs")
        selection = selected_run["selection"]
    summaries = {}
    metric_names = ["judged_ap", "recall_at_20", "recall_at_50", "known_precision_at_20", "unknown_fraction_at_20", "negative_retrieval_fraction_at_20"]
    if dataset == "coco480":
        metric_names += ["small_recall_at_20", "small_recall_at_50"]
    for split in sorted({row["split"] for row in rows}):
        selected_rows = [row for row in rows if row["split"] == split]
        summaries[split] = {method: {metric: paired_summary(selected_rows, method, metric) for metric in metric_names} for method in METHODS}
    slices = {}
    for name, predicate in {"mapped": lambda row: bool(row["mapped_concepts"]),
                            "unmapped": lambda row: not row["mapped_concepts"],
                            "complex": lambda row: row["complex_query"],
                            "supported_categories": lambda row: row["diagnostic_supported"],
                            "chinese": lambda row: row["language"] == "cn",
                            "english": lambda row: row["language"] == "en"}.items():
        selected_rows = [row for row in rows if predicate(row) and (row["split"] != "calibration")]
        slices[name] = {metric: paired_summary(selected_rows, selection["chosen_method"], metric) for metric in metric_names}
    report = {"schema": "album-tag-fusion-result-v1", "dataset": dataset, "model": model,
        "plan_sha256": reader.hashes[reader.key(plan_path)], "selection": selection,
        "summaries": summaries, "selected_method_slices": slices, "queries": rows,
        "cost": {"tag_resources": tag_record.get("resources"), "gpu": tag_record.get("gpu"),
                 "tag_elapsed_seconds": tag_record.get("elapsed_seconds"), "tag_load_seconds": tag_record.get("load_seconds"),
                 "full_score_bytes_per_image": len(plan["vocabularies"][model]) * 4,
                 "evaluation_seconds": time.perf_counter() - started,
                 "note": "evaluation includes metrics/bootstrap; not application query latency or GTX1660 certification"},
        "input_sha256": reader.unchanged(), "source_sha256": source_before,
        "limitations": plan["limitations"]}
    output.mkdir(parents=True)
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dataset": dataset, "model": model, "selection": selection, "summaries": summaries,
            "report_path": str(output / "result.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("freeze")
    command.add_argument("--data-dir", type=Path, required=True)
    command.add_argument("--out-dir", type=Path, required=True)
    command = sub.add_parser("evaluate")
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--dataset", choices=("openimages400", "coco480", "photobench1047"), required=True)
    command.add_argument("--model", choices=("ram", "light"), required=True)
    command.add_argument("--tag-run", type=Path, required=True)
    command.add_argument("--query-run", type=Path, required=True)
    command.add_argument("--selection", type=Path)
    command.add_argument("--compatibility", type=Path, required=True)
    command.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    result = (freeze(args.data_dir, args.out_dir) if args.command == "freeze" else
              evaluate(args.plan, args.dataset, args.model, args.tag_run, args.query_run,
                       args.out_dir, args.selection, args.compatibility))
    if args.command == "evaluate":
        result = {key: value for key, value in result.items() if key != "summaries"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
