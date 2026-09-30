"""P3-01: the repeatable ``%TEMP%`` OCR end-to-end fixture and evidence producer.

It builds its own work directory, renders real text images with the system CJK font (a
Chinese/digit invoice line set, a high-density page and a blank page), registers that
directory as a source, scans it, runs the product's OCR processor through the P2-02
scheduler and drains the derived-text projection. Every claim it prints is asserted
against the catalog, the structured artifact and the FTS index before it exits 0.

It never touches a real photo library, never starts a server and never writes outside its
own work directory.

Usage::

    uv run python tools/diagnostics/ocr_smoke_fixture.py
    uv run python tools/diagnostics/ocr_smoke_fixture.py --work-dir "%TEMP%\\P3-01-OCR" --json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.errors import CatalogError
from photo_search.ingestion.service import CatalogService
from photo_search.ocr import contract
from photo_search.ocr.service import (
    catch_up_projection,
    enqueue_targets,
    ocr_targets,
    run_queue,
)
from photo_search.ocr.weights import resolve, verify
from photo_search.scheduler.projection import ocr_summary
from photo_search.search.tokens import build_match

DEFAULT_WORK_DIRNAME = "P3-01 OCR 验收"
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")

INVOICE_LINES = [
    "发票 金额 1280 元",
    "车牌 苏A12345",
    "护照 MRZ P<CHN",
    "编号 20260914",
    "合计 3760.50",
]
QUERIES = ("发票", "1280", "20260914")


def render_text_image(
    path: Path, lines: list[str], *, size: tuple[int, int], font_size: int, columns: int = 1
) -> Path:
    font = ImageFont.truetype(str(FONT_PATH), int(font_size))
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    rows = max(1, (len(lines) + columns - 1) // columns)
    width = size[0] // max(1, columns)
    for index, line in enumerate(lines):
        draw.text(
            (30 + (index // rows) * width, 30 + (index % rows) * (font_size + 24)),
            line,
            font=font,
            fill=(10, 10, 10),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=95)
    return path


def build_library(library: Path) -> dict[str, Path]:
    library.mkdir(parents=True, exist_ok=True)
    invoice = render_text_image(
        library / "invoice.jpg", INVOICE_LINES, size=(1400, 900), font_size=48
    )
    dense_lines = [
        f"{column + 1}-{row + 1} 编号 {20260900 + row} 金额 {(row + 1) * 128}"
        for row in range(4)
        for column in range(3)
    ]
    dense = render_text_image(
        library / "dense.jpg", dense_lines, size=(1800, 1200), font_size=40, columns=3
    )
    blank = library / "blank.jpg"
    Image.new("RGB", (800, 500), (255, 255, 255)).save(blank, "JPEG", quality=95)
    return {"invoice": invoice, "dense": dense, "blank": blank}


def install_pinned_weights(data_root: Path) -> Path:
    """Copy the machine's pinned files into this run's own model root.

    The product only ever reads ``<data_root>/models``, so a self-contained smoke run
    carries its own verified copy instead of pointing the processor at another data root.
    """
    source = verify(resolve())
    target = resolve(data_root)
    for entry in target.files:
        origin = next(item.absolute_path for item in source.files if item.role == entry.role)
        entry.absolute_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, entry.absolute_path)
    return verify(target).root


def run(work_dir: Path, *, requested_device: str = "auto") -> dict[str, Any]:
    data_root = work_dir / "PhotoSearchData"
    library = work_dir / "照片库 中文 空格"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    files = build_library(library)
    install_pinned_weights(data_root)
    resolved = verify(resolve(data_root))
    started = time.monotonic()
    with CatalogService.open(data_root=data_root) as service:
        source = service.register_source(display_name="ocr-smoke", root_path=library)
        scan = service.run_scan(int(source["source_id"]))
        targets = ocr_targets(service.catalog)
        enqueue = enqueue_targets(
            service.catalog,
            targets,
            now_ms=service.catalog.now_ms(),
            requested_device=requested_device,
        )
        run_report = run_queue(service.catalog, data_root=data_root)
        projection = catch_up_projection(service.catalog)
        summary = ocr_summary(service.catalog.db_path, weights_present=True)

        artifact_store = ArtifactStore.open(data_root)
        images: dict[str, Any] = {}
        for name, path in files.items():
            row = service.catalog.read_one(
                "SELECT fl.asset_id AS asset_id, a.sha256 AS sha256 FROM file_locations AS fl "
                "JOIN assets AS a ON a.asset_id = fl.asset_id WHERE fl.relative_path = ?",
                (path.name,),
            )
            asset_id = int(row["asset_id"])
            text_row = service.catalog.read_one(
                "SELECT * FROM asset_ocr_text WHERE asset_id = ?", (asset_id,)
            )
            artifact = service.catalog.read_one(
                "SELECT * FROM artifacts WHERE input_revision = ?", (str(row["sha256"]),)
            )
            payload = (
                None
                if artifact is None
                else json.loads(artifact_store.read(artifact).decode("utf-8"))
            )
            images[name] = {
                "asset_id": asset_id,
                "regions": 0 if text_row is None else int(text_row["region_count"]),
                "search_text": "" if text_row is None else str(text_row["search_text"]),
                "original_text": None if payload is None else str(payload["original_text"]),
                "slice_count": None if payload is None else int(payload["slice_count"]),
                "working_size": None if payload is None else payload["working_size"],
                "min_confidence": None if text_row is None else text_row["min_confidence"],
                "max_confidence": None if text_row is None else text_row["max_confidence"],
            }
        hits = {
            query: [
                int(item["asset_id"])
                for item in service.catalog.read_all(
                    "SELECT rowid AS asset_id FROM search_documents_fts "
                    "WHERE search_documents_fts MATCH ? ORDER BY rowid",
                    (build_match(query).expression,),
                )
            ]
            for query in QUERIES
        }
        invoice_id = images["invoice"]["asset_id"]
        assert scan["state"] == "SUCCEEDED"
        assert enqueue["queued"] == 3, enqueue
        assert run_report["jobs"] == {"SUCCEEDED": 3}, run_report
        assert images["invoice"]["regions"] >= 4
        assert "1280" in str(images["invoice"]["original_text"])
        assert images["dense"]["slice_count"] >= 2
        assert images["blank"]["regions"] == 0
        for query in QUERIES:
            assert invoice_id in hits[query], (query, hits[query])
        assert summary is not None and summary["state"] == "SYNCED"
        return {
            "status": "ok",
            "task": "P3-01",
            "work_dir": str(work_dir),
            "data_root": str(data_root),
            "model_root": str(resolved.root),
            "model_fingerprint": resolved.fingerprint,
            "engine": f"{contract.OCR_ENGINE}/{contract.ENGINE_VERSION}",
            "max_long_edge": contract.MAX_LONG_EDGE,
            "slice_size": contract.SLICE_SIZE,
            "slice_overlap": contract.SLICE_OVERLAP,
            "text_score": contract.MIN_TEXT_SCORE,
            "seconds": round(time.monotonic() - started, 2),
            "enqueue": enqueue,
            "run": run_report,
            "requested_device": requested_device,
            "ocr_jobs": [
                dict(row)
                for row in service.catalog.read_all(
                    "SELECT state, requested_device, resolved_device, runtime_provider, precision "
                    "FROM jobs WHERE task_type = ? ORDER BY job_id",
                    (contract.OCR_TASK_TYPE,),
                )
            ],
            "projection": projection,
            "images": images,
            "fts_hits": {query: sorted(hits[query]) for query in QUERIES},
            "ocr_summary": summary,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / DEFAULT_WORK_DIRNAME,
        help="the (recreated) work directory; never a real photo library",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--device", choices=("auto", "cpu", "gpu"), default="auto")
    parser.add_argument("--output", type=Path, default=None, help="also write the UTF-8 JSON report here")
    parser.add_argument("--keep", action="store_true", help="keep the work directory on failure")
    args = parser.parse_args(argv)
    try:
        report = run(args.work_dir.resolve(), requested_device=args.device)
    except (CatalogError, AssertionError, FileNotFoundError) as error:
        detail = error.as_dict() if isinstance(error, CatalogError) else str(error)
        print(
            json.dumps(
                {"status": "fail", "exception_type": type(error).__name__, "detail": detail},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        if not args.keep:
            shutil.rmtree(args.work_dir.resolve(), ignore_errors=True)
        return 1
    if args.json:
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    else:
        print(json.dumps(
            {
                "status": report["status"],
                "seconds": report["seconds"],
                "regions": {name: item["regions"] for name, item in report["images"].items()},
                "slices": {name: item["slice_count"] for name, item in report["images"].items()},
                "fts_hits": report["fts_hits"],
                "ocr_state": report["ocr_summary"]["state"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ))
    return 0


if __name__ == "__main__":  # pragma: no cover - diagnostic entry point
    raise SystemExit(main())
