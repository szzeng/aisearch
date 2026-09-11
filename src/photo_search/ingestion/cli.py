"""``catalog`` CLI: the observable internal entry for P1-01.

Every subcommand can emit one JSON document with ``--json`` and returns a non-zero exit
code on failure, including structured catalog errors. No HTTP business API is added in
P1-01; ``GET /api/v1/health`` keeps its P0-01 behaviour.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.catalog.migrate import catalog_info, migrate_database
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.ingestion.service import CatalogService

COMMANDS = ("init", "info", "add-source", "list-sources", "scan", "status")


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


def add_catalog_parser(subparsers: Any) -> None:
    common = _common_options()
    catalog = subparsers.add_parser(
        "catalog", help="catalog maintenance, source registration and minimal scans"
    )
    commands = catalog.add_subparsers(dest="catalog_command")
    for name, help_text in (
        ("init", "create or migrate the catalog database"),
        ("info", "read-only schema and journal-mode facts"),
        ("list-sources", "list registered sources"),
        ("status", "source and asset counts"),
    ):
        subparser = commands.add_parser(name, parents=[common], help=help_text)
        if name == "status":
            subparser.add_argument("--source-id", type=int, default=None)
    add_source = commands.add_parser(
        "add-source", parents=[common], help="register a photo source root"
    )
    add_source.add_argument("--name", required=True)
    add_source.add_argument("--path", required=True, type=Path)
    add_source.add_argument("--volume-hint", default=None)
    scan = commands.add_parser("scan", parents=[common], help="run the minimal catalog scan")
    scan.add_argument("--source-id", type=int, required=True)
    scan.add_argument("--worker-id", default=None)
    scan.add_argument("--lease-seconds", type=int, default=None)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        rendered = value if _is_scalar(value) else json.dumps(value, ensure_ascii=False)
        print(f"{key}: {rendered}")


def _emit_error(args: argparse.Namespace, error: CatalogError) -> None:
    payload = {"status": "error", "error": error.as_dict()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"error: {error.code}: {error.message}", file=sys.stderr)
        for key, value in error.details.items():
            print(f"  {key}: {value}", file=sys.stderr)


def run_catalog_command(args: argparse.Namespace) -> int:
    """Execute one ``catalog`` subcommand and return its process exit code."""
    command = getattr(args, "catalog_command", None)
    if command is None:
        message = f"choose one of {list(COMMANDS)}"
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"status": "error", "error": {"code": "USAGE", "message": message}},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"error: USAGE: {message}", file=sys.stderr)
        return 2
    data_root = resolve_data_root(args.data_root)
    try:
        if command == "info":
            _emit(args, catalog_info(None, data_root=data_root))
            return 0
        if command == "init":
            result = migrate_database(catalog_db_path(data_root), data_root=data_root)
            payload = {"status": "ok", **result.as_dict()}
            _emit(args, payload)
            return 0
        with CatalogService.open(data_root=data_root) as service:
            if command == "add-source":
                payload = {
                    "status": "ok",
                    "source": service.register_source(
                        display_name=args.name,
                        root_path=args.path,
                        volume_hint=args.volume_hint,
                    ),
                }
            elif command == "list-sources":
                payload = {"status": "ok", "sources": service.list_sources()}
            elif command == "scan":
                payload = {
                    "status": "ok",
                    "scan": service.run_scan(
                        args.source_id,
                        worker_id=args.worker_id,
                        lease_seconds=args.lease_seconds,
                    ),
                }
            elif command == "status":
                payload = {"status": "ok"}
                if args.source_id is None:
                    payload["counts"] = service.counts()
                    payload["sources"] = service.list_sources()
                else:
                    payload["source"] = service.get_source(args.source_id)
            else:  # pragma: no cover - argparse restricts the choices
                raise CatalogError("USAGE", f"unsupported catalog command: {command}")
            _emit(args, payload)
            return 0
    except CatalogError as error:
        _emit_error(args, error)
        return 1
