"""P4-02R read-only diagnosis: why 379 assets have an embedding but no ready thumbnail.

Purely diagnostic and read-only: it opens the catalog and the media cache, samples the assets
whose thumbnail state is not ``ready`` while their embedding is ``CONFIRMED``, and prints the
decision chain of every layer that looked at the same file:

* what the file is (extension, size, header magic),
* what ``Image.open`` reports (format, or the exception type),
* what the *media contract* decides (``plugin_format_decision`` / ``sniff_format``) and what
  it does with the pixels (``load_normalized``),
* what the *semantic build* probe decided (``indexing.semantic.image_eligibility``) and what
  the adapter's own decode would do (``Image.open(...).convert("RGB")``),

No image is displayed, no pixel content is inspected by a human, nothing is written outside
``--output``, and no absolute path is printed: the report carries only labels, sizes, codes and
asset ids.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.catalog.db import Catalog
from photo_search.indexing import semantic
from photo_search.media import decode as media_decode
from photo_search.media.cache import MediaCache
from photo_search.media.status import asset_media_states

REPO_ROOT = Path(__file__).resolve().parents[2]


def _samples(catalog: Catalog, cache: MediaCache, limit: int) -> list[dict[str, Any]]:
    rows = catalog.read_all(
        "SELECT a.asset_id, a.sha256, a.byte_size FROM assets AS a "
        "JOIN index_entries AS e ON e.asset_id = a.asset_id AND e.state = 'CONFIRMED' "
        "ORDER BY a.asset_id"
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        asset_id = int(row["asset_id"])
        sha = str(row["sha256"])
        states = asset_media_states(catalog, cache, asset_id=asset_id, sha256=sha)
        thumbnail = states.get("thumbnail") or {}
        if str(thumbnail.get("state")) == "ready":
            continue
        location = catalog.read_one(
            "SELECT relative_path, root_path FROM file_locations AS l "
            "JOIN source_roots AS s ON s.source_id = l.source_id "
            "WHERE l.asset_id = ? AND l.availability = 'PRESENT' "
            "ORDER BY l.location_id LIMIT 1",
            (asset_id,),
        )
        if location is None:
            continue
        suffix = Path(str(location["relative_path"])).suffix.lower()
        bucket = groups.setdefault(suffix, [])
        if len(bucket) >= max(1, int(limit)):
            continue
        bucket.append(
            {
                "asset_id": asset_id,
                "sha256": sha,
                "byte_size": int(row["byte_size"] or 0),
                "relative_path": str(location["relative_path"]),
                "root_path": str(location["root_path"]),
                "thumbnail_state": str(thumbnail.get("state")),
                "thumbnail_code": thumbnail.get("code"),
                "thumbnail_record": {
                    key: thumbnail["record"].get(key)
                    for key in ("code", "format", "message", "processed_at")
                    if isinstance(thumbnail.get("record"), dict) and key in thumbnail["record"]
                },
            }
        )
    return [item for bucket in groups.values() for item in bucket]


def _probe(entry: dict[str, Any]) -> dict[str, Any]:
    path = Path(entry["root_path"]) / entry["relative_path"]
    result: dict[str, Any] = {
        "asset_id": entry["asset_id"],
        "suffix": Path(entry["relative_path"]).suffix.lower(),
        "byte_size": entry["byte_size"],
        "thumbnail_state": entry["thumbnail_state"],
        "thumbnail_code": entry["thumbnail_code"],
        "thumbnail_record": entry["thumbnail_record"],
    }
    try:
        header = media_decode.read_header(path)
        result["header_hex"] = header[:12].hex()
        result["sniffed"] = media_decode.sniff_format(header)
    except Exception as error:  # noqa: BLE001 - a header read failure is a finding, not a crash
        result["header_hex"] = None
        result["sniffed"] = None
        result["header_error"] = type(error).__name__
    try:
        with Image.open(path) as source:
            pil_format = str(source.format or "")
            result["pil_format"] = pil_format
            result["pil_size"] = list(source.size)
            label, decodable = media_decode.plugin_format_decision(source.format)
            result["media_decision"] = {"label": label, "decodable": bool(decodable)}
    except Exception as error:  # noqa: BLE001 - the exception type is the finding
        result["pil_format"] = None
        result["media_decision"] = None
        result["pil_error"] = f"{type(error).__name__}: {str(error)[:120]}"
    # What the semantic build's selection decided.
    eligible, label = semantic.image_eligibility(path)
    result["semantic_eligibility"] = {"eligible": bool(eligible), "label": label}
    # What the media contract does with the pixels (this is the thumbnail path).
    try:
        decoded = media_decode.load_normalized(path, long_edge=320)
        result["load_normalized"] = {
            "ok": True,
            "source_format": str(decoded.source_format),
            "size": [int(decoded.width), int(decoded.height)],
        }
        decoded.image.close()
    except Exception as error:  # noqa: BLE001 - the structured code is the finding
        result["load_normalized"] = {
            "ok": False,
            "code": str(getattr(error, "code", type(error).__name__)),
            "details": {
                key: value
                for key, value in (getattr(error, "details", None) or {}).items()
                if key in ("format", "reason", "supported_formats")
            },
        }
    # What the adapter's own preprocessing would do (it opens with Pillow and converts).
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            result["adapter_decode"] = {"ok": True, "size": list(image.size)}
            image.close()
    except Exception as error:  # noqa: BLE001
        result["adapter_decode"] = {"ok": False, "error": type(error).__name__}
    return result


def run(args: argparse.Namespace) -> int:
    root = str(args.data_root)
    catalog = Catalog.open(data_root=root)
    cache = MediaCache.open(data_root=root)
    try:
        entries = _samples(catalog, cache, int(args.per_suffix))
        probes = [_probe(entry) for entry in entries]
    finally:
        catalog.close()
    summary = {
        "sampled": len(probes),
        "by_suffix": dict(Counter(item["suffix"] for item in probes).most_common()),
        "pil_format": dict(Counter(str(item.get("pil_format")) for item in probes).most_common()),
        "sniffed": dict(Counter(str(item.get("sniffed")) for item in probes).most_common()),
        "media_decision": dict(
            Counter(
                None
                if item.get("media_decision") is None
                else f"{item['media_decision']['label']}/decodable={item['media_decision']['decodable']}"
                for item in probes
            ).most_common()
        ),
        "semantic_eligible": dict(
            Counter(
                f"{item['semantic_eligibility']['label']}/eligible={item['semantic_eligibility']['eligible']}"
                for item in probes
            ).most_common()
        ),
        "load_normalized": dict(
            Counter(
                "ok" if item["load_normalized"]["ok"] else str(item["load_normalized"]["code"])
                for item in probes
            ).most_common()
        ),
        "adapter_decode": dict(
            Counter(
                "ok" if item["adapter_decode"]["ok"] else str(item["adapter_decode"]["error"])
                for item in probes
            ).most_common()
        ),
        "thumbnail_codes": dict(Counter(str(item["thumbnail_code"]) for item in probes).most_common()),
    }
    report = {"data_root": os.path.basename(root), "summary": summary, "probes": probes}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--per-suffix", type=int, default=6)
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
