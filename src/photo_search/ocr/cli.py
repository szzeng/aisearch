"""``python -m photo_search.ocr.cli`` - the controlled OCR entry point.

The ``catalog`` command of :mod:`photo_search.ingestion.cli` is not this task's to change
(``ingestion/**`` is frozen for P3-01), so the OCR entry lives in the OCR package and
follows the same shape: one JSON document with ``--json``, exit 0 when the requested work
completed, 1 for a structured ``CatalogError`` and 2 for a usage error.

Commands

``weights``    verify the pinned P0-04 files (size + SHA-256) and print their identity.
``status``     read-only: covered/stale documents, region counts, job states.
``enqueue``    queue (or ``--force`` rewind) one OCR job per selected asset.
``run``        drain the runnable OCR jobs through the P2-02 scheduler, then catch the
               projection up.
``recognize``  ``enqueue`` + ``run`` in one call, for one asset, one source or the library.

Nothing here starts a background thread: ``run`` and ``recognize`` return when the queue is
drained or the requested bound is reached.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.config import resolve_data_root
from photo_search.ingestion.service import CatalogService
from photo_search.ocr import contract, weights
from photo_search.ocr.service import (
    catch_up_projection,
    enqueue_targets,
    job_counts,
    ocr_targets,
    run_queue,
)
from photo_search.scheduler.projection import ocr_summary

COMMANDS = ("weights", "status", "enqueue", "run", "recognize")


def _common_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="override the application data root (diagnostics and tests)",
    )
    common.add_argument("--json", action="store_true", help="emit one JSON document")
    return common


def _selection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--asset-id",
        type=int,
        action="append",
        default=None,
        help="restrict the work to one asset (repeatable)",
    )
    parser.add_argument("--source-id", type=int, default=None)
    parser.add_argument(
        "--all",
        action="store_true",
        help="select every asset with a readable original (the default when no id is given)",
    )
    parser.add_argument("--limit", type=int, default=None, help="process at most this many")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photo-search-ocr", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    common = _common_options()
    commands.add_parser("weights", parents=[common], help="verify the pinned PP-OCRv4 files")
    commands.add_parser("status", parents=[common], help="read-only OCR readiness summary")
    for name, help_text in (
        ("enqueue", "queue OCR jobs for the selected assets"),
        ("recognize", "enqueue and run OCR for the selected assets"),
        ("run", "drain the runnable OCR jobs"),
    ):
        subparser = commands.add_parser(name, parents=[common], help=help_text)
        if name != "run":
            _selection_options(subparser)
        if name in ("enqueue", "recognize"):
            subparser.add_argument(
                "--force",
                action="store_true",
                help="rewind an already finished job instead of only creating a missing one",
            )
        if name in ("run", "recognize"):
            subparser.add_argument("--worker-id", default="ocr-cli")
            subparser.add_argument(
                "--max-jobs",
                type=int,
                default=None,
                help="stop after draining at most this many jobs",
            )
            subparser.add_argument(
                "--deadline-ms",
                type=int,
                default=None,
                help="wall-clock budget of one image (default: the frozen limit)",
            )
            subparser.add_argument(
                "--skip-projection",
                action="store_true",
                help="do not drain the derived-text queue afterwards",
            )
    return parser


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        rendered = value if isinstance(value, (str, int, float, bool)) or value is None else json.dumps(
            value, ensure_ascii=False, sort_keys=True
        )
        print(f"{key}: {rendered}")


def _emit_error(args: argparse.Namespace, error: CatalogError) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"status": "error", "error": error.as_dict()}, ensure_ascii=False, sort_keys=True))
        return
    print(f"error: {error.code}: {error.message}", file=sys.stderr)
    for key, value in error.details.items():
        print(f"  {key}: {value}", file=sys.stderr)


def _weights_payload(data_root: Path) -> dict[str, Any]:
    resolved = weights.resolve_and_verify(data_root)
    return {
        "status": "ok",
        "model_root": str(resolved.root),
        "model_fingerprint": resolved.fingerprint,
        "config_hash": resolved.config_hash,
        "files": weights.inspect(resolved),
    }


def _status_payload(data_root: Path) -> dict[str, Any]:
    from photo_search.config import catalog_db_path

    summary = ocr_summary(
        catalog_db_path(data_root), weights_present=weights.weights_present(data_root)
    )
    return {"status": "ok", "ocr": summary}


def _select(service: CatalogService, args: argparse.Namespace) -> list[dict[str, Any]]:
    targets = ocr_targets(
        service.catalog,
        asset_ids=args.asset_id,
        source_id=args.source_id,
        limit=args.limit,
    )
    if args.asset_id:
        missing = {int(value) for value in args.asset_id} - {int(item["asset_id"]) for item in targets}
        if missing:
            raise CatalogError(
                "ASSET_NOT_FOUND",
                "a requested asset has no readable original to recognise",
                details={"asset_ids": sorted(missing)},
            )
    return targets


def run_command(args: argparse.Namespace) -> int:
    command = getattr(args, "command", None)
    if command is None:
        _emit_error(args, CatalogError("USAGE", f"choose one of {list(COMMANDS)}"))
        return 2
    for option in ("limit", "max_jobs"):
        value = getattr(args, option, None)
        if value is not None and int(value) < 0:
            _emit_error(args, CatalogError("USAGE", f"--{option.replace('_', '-')} must not be negative"))
            return 2
    if getattr(args, "deadline_ms", None) is not None and int(args.deadline_ms) < 1:
        _emit_error(args, CatalogError("USAGE", "the deadline must be a positive number of milliseconds"))
        return 2
    data_root = resolve_data_root(args.data_root)
    try:
        if command == "weights":
            _emit(args, _weights_payload(data_root))
            return 0
        if command == "status":
            _emit(args, _status_payload(data_root))
            return 0
        # Every work command proves the pinned weights before anything is queued, so a
        # broken model root is one structured failure instead of a queue full of blocked jobs.
        resolved = weights.resolve_and_verify(data_root)
        with CatalogService.open(data_root=data_root) as service:
            payload: dict[str, Any] = {
                "status": "ok",
                "model_fingerprint": resolved.fingerprint,
            }
            if command in ("enqueue", "recognize"):
                targets = _select(service, args)
                payload["selection"] = len(targets)
                payload["enqueue"] = enqueue_targets(
                    service.catalog,
                    targets,
                    now_ms=service.catalog.now_ms(),
                    force=bool(getattr(args, "force", False)),
                )
            if command in ("run", "recognize"):
                payload["run"] = run_queue(
                    service.catalog,
                    data_root=data_root,
                    worker_id=str(getattr(args, "worker_id", "ocr-cli")),
                    limit=getattr(args, "max_jobs", None),
                    deadline_ms=getattr(args, "deadline_ms", None),
                )
                if not bool(getattr(args, "skip_projection", False)):
                    payload["projection"] = catch_up_projection(service.catalog)
            payload["jobs"] = job_counts(service.catalog)
            payload["ocr"] = ocr_summary(
                service.catalog.db_path, weights_present=True
            )
            _emit(args, payload)
            return 0
    except CatalogError as error:
        _emit_error(args, error)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_command(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__ = ["COMMANDS", "build_parser", "main", "run_command", "contract"]
