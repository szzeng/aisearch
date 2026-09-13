"""Explicit local tool for the offline name datasets (ADR-0005 §8).

Nothing in the running application downloads or prepares a dataset, and this tool never
touches the network, a photo or a source root. It builds a package from a local JSON
specification, imports it into the application data root, verifies it, activates or rolls
it back, prints the current state and removes an unreferenced package.

Examples::

    uv run --no-sync python tools/places/manage_dataset.py build \\
        --spec tools/places/examples/mini-cities.spec.json --out %TEMP%\\mini-cities
    uv run --no-sync python tools/places/manage_dataset.py import \\
        --source %TEMP%\\mini-cities --activate
    uv run --no-sync python tools/places/manage_dataset.py status
    uv run --no-sync python tools/places/manage_dataset.py rollback
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.geo import dataset as dataset_module
from photo_search.places import datasets as datasets_module


def _catalog(args: argparse.Namespace) -> Catalog:
    return Catalog.open(data_root=args.data_root)


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _build(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    manifest = dataset_module.build_package(spec, args.out)
    # Verify what was just written with the same code the import path uses.
    dataset_module.load_content(
        args.out, supported_schema_version=int(_supported())
    )
    _print({"status": "ok", "package_dir": str(Path(args.out).resolve()), "manifest": manifest["package_id"]})
    return 0


def _supported() -> int:
    from photo_search.catalog import schema

    return int(schema.SUPPORTED_SCHEMA_VERSION)


def _import(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        staged = datasets_module.stage_package(catalog, args.source)
        validated = datasets_module.validate_dataset(catalog, str(staged["dataset_id"]))
        if args.activate:
            activated = datasets_module.activate_dataset(catalog, str(staged["dataset_id"]))
            _print({"status": "ok", "imported": validated, "activated": activated})
        else:
            _print({"status": "ok", "imported": validated})
    return 0


def _validate(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        _print({"status": "ok", "dataset": datasets_module.validate_dataset(catalog, args.dataset_id)})
    return 0


def _activate(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        _print({"status": "ok", "activated": datasets_module.activate_dataset(catalog, args.dataset_id)})
    return 0


def _rollback(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        _print({"status": "ok", "rolled_back": datasets_module.rollback_dataset(catalog)})
    return 0


def _status(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        _print(
            {"status": "ok", "datasets": datasets_module.dataset_status(catalog.connection, data_root=catalog.data_root)}
        )
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    with _catalog(args) as catalog:
        _print({"status": "ok", "removed": datasets_module.cleanup_dataset(catalog, args.dataset_id)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline place name-dataset tool")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="application data root (default: the normal per-user data root)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a package from a local spec")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.set_defaults(handler=_build)

    import_parser = subparsers.add_parser("import", help="stage, verify and import a package")
    import_parser.add_argument("--source", type=Path, required=True)
    import_parser.add_argument("--activate", action="store_true")
    import_parser.set_defaults(handler=_import)

    validate = subparsers.add_parser("validate", help="re-verify an imported package")
    validate.add_argument("--dataset-id", required=True)
    validate.set_defaults(handler=_validate)

    activate = subparsers.add_parser("activate", help="make a validated package active")
    activate.add_argument("--dataset-id", required=True)
    activate.set_defaults(handler=_activate)

    rollback = subparsers.add_parser("rollback", help="activate the previous package again")
    rollback.set_defaults(handler=_rollback)

    status = subparsers.add_parser("status", help="print the dataset projection")
    status.set_defaults(handler=_status)

    cleanup = subparsers.add_parser("cleanup", help="remove an unreferenced package")
    cleanup.add_argument("--dataset-id", required=True)
    cleanup.set_defaults(handler=_cleanup)

    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except CatalogError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
