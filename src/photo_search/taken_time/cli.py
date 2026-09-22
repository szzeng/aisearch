"""``python -m photo_search.taken_time.cli`` - the controlled taken-time entry point.

Follows the shape of the OCR entry point (``photo_search.ocr.cli``): one JSON document with
``--json``, exit 0 when the requested work completed, 1 for a structured ``CatalogError``
and 2 for a usage error. Nothing here starts a background thread: ``run``/``extract`` return
when the queue is drained or the requested bound is reached.

Commands

``status``   read-only: schema version, rule version, the honesty block and job states.
``enqueue``  queue (or ``--force`` rewind) one extraction job per selected asset.
``run``      drain the runnable extraction jobs through the P2-02 scheduler.
``extract``  ``enqueue`` + ``run`` in one call, for one asset, one source or the library.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog import schema
from photo_search.catalog.errors import SCHEMA_INCONSISTENT, CatalogError
from photo_search.catalog.sqlite_io import open_readonly
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.ingestion.service import CatalogService
from photo_search.taken_time import read as read_module
from photo_search.taken_time.service import (
    coverage,
    enqueue_targets,
    job_counts,
    run_queue,
    taken_time_targets,
)

COMMANDS = ("status", "enqueue", "run", "extract")


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
        help="select every catalogued asset (the default when no id is given)",
    )
    parser.add_argument("--limit", type=int, default=None, help="process at most this many")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photo-search-taken-time", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    common = _common_options()
    commands.add_parser("status", parents=[common], help="read-only taken-time readiness")
    for name, help_text in (
        ("enqueue", "queue extraction jobs for the selected assets"),
        ("extract", "enqueue and run the extraction for the selected assets"),
        ("run", "drain the runnable extraction jobs"),
    ):
        subparser = commands.add_parser(name, parents=[common], help=help_text)
        if name != "run":
            _selection_options(subparser)
        if name in ("enqueue", "extract"):
            subparser.add_argument(
                "--force",
                action="store_true",
                help="rewind an already finished job instead of only creating a missing one",
            )
        if name in ("run", "extract"):
            subparser.add_argument("--worker-id", default="taken-time-cli")
            subparser.add_argument(
                "--max-jobs",
                type=int,
                default=None,
                help="stop after draining at most this many jobs",
            )
    return parser


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        rendered = (
            value
            if isinstance(value, (str, int, float, bool)) or value is None
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
        print(f"{key}: {rendered}")


def _emit_error(args: argparse.Namespace, error: CatalogError) -> None:
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"status": "error", "error": error.as_dict()}, ensure_ascii=False, sort_keys=True
            )
        )
        return
    print(f"error: {error.code}: {error.message}", file=sys.stderr)


def _status_payload(data_root: Path) -> dict[str, Any]:
    path = catalog_db_path(data_root)
    if not path.exists():
        return {
            "status": "ok",
            "taken_time": {
                "state": "UNINITIALISED",
                "schema_version": None,
                "supported_schema_version": int(schema.SUPPORTED_SCHEMA_VERSION),
            },
        }
    connection = open_readonly(path)
    try:
        version = schema.read_schema_version(connection)
        if version is None:
            raise CatalogError(
                SCHEMA_INCONSISTENT,
                "the catalog has tables but no usable metadata.schema_version",
            )
        if version < 15:
            return {
                "status": "ok",
                "taken_time": {
                    "state": "NOT_MIGRATED",
                    "schema_version": int(version),
                    "supported_schema_version": int(schema.SUPPORTED_SCHEMA_VERSION),
                },
            }
        payload = coverage_from_connection(connection)
    finally:
        connection.close()
    return {"status": "ok", "taken_time": payload}


def coverage_from_connection(connection: Any) -> dict[str, Any]:
    """The coverage summary from an already-open read-only connection (no writes)."""
    assets_total, extracted, captured = read_module.counts(connection)
    jobs_block: dict[str, int] = {}
    try:
        jobs_block = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM jobs WHERE task_type = ? "
                "GROUP BY state ORDER BY state",
                ("taken_time_extract",),
            ).fetchall()
        }
    except Exception:  # noqa: BLE001 - a catalog without the 0002 job table has no jobs
        jobs_block = {}
    return {
        "rules_version": read_module.rules_version(connection),
        "rules_version_expected": 1,
        "rules_version_matches": read_module.rules_version_matches(connection),
        "assets_total": assets_total,
        "extracted_assets": extracted,
        "capture_time_assets": captured,
        "block": read_module.state_block(connection),
        "jobs": jobs_block,
    }


def _select(service: CatalogService, args: argparse.Namespace) -> list[dict[str, Any]]:
    targets = taken_time_targets(
        service.catalog,
        asset_ids=args.asset_id,
        source_id=args.source_id,
        limit=args.limit,
    )
    if args.asset_id:
        missing = {int(value) for value in args.asset_id} - {
            int(item["asset_id"]) for item in targets
        }
        if missing:
            raise CatalogError(
                "ASSET_NOT_FOUND",
                "a requested asset is not in this catalog",
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
            _emit_error(
                args, CatalogError("USAGE", f"--{option.replace('_', '-')} must not be negative")
            )
            return 2
    data_root = resolve_data_root(args.data_root)
    try:
        if command == "status":
            _emit(args, _status_payload(data_root))
            return 0
        with CatalogService.open(data_root=data_root) as service:
            payload: dict[str, Any] = {"status": "ok"}
            if command in ("enqueue", "extract"):
                targets = _select(service, args)
                payload["selection"] = len(targets)
                payload["enqueue"] = enqueue_targets(
                    service.catalog,
                    targets,
                    now_ms=service.catalog.now_ms(),
                    force=bool(getattr(args, "force", False)),
                )
            if command in ("run", "extract"):
                payload["run"] = run_queue(
                    service.catalog,
                    worker_id=str(getattr(args, "worker_id", "taken-time-cli")),
                    limit=getattr(args, "max_jobs", None),
                )
            payload["jobs"] = job_counts(service.catalog)
            payload["taken_time"] = coverage(service.catalog)
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


__all__ = ["COMMANDS", "build_parser", "coverage_from_connection", "main", "run_command"]
