"""``python -m photo_search.face.cli`` - the controlled face entry point.

The ``ingestion`` package is frozen for this task, so the face entry lives in the face
package and follows the same shape as ``photo_search.ocr.cli``: one JSON document with
``--json``, exit 0 when the requested work completed, 1 for a structured ``CatalogError``
and 2 for a usage error.

Commands

``weights``    verify the pinned P0-04 ``buffalo_l`` files (size + SHA-256).
``status``     read-only: analysed/pending assets, faces, groups, suggestions, index state.
``enqueue``    queue (or ``--force`` rewind) one face job per selected asset.
``run``        drain the runnable face jobs through the P2-02 scheduler.
``index``      publish and confirm the independent candidate index.
``group``      run the conservative association pass.
``rematch``    reconsider unassigned stored faces; no model inference or library scan.
``detect``     ``enqueue`` + ``run`` + ``index`` + ``group`` + ``index`` in one call.

``--device`` is the ADR-0003 requested device. ``auto`` (the default) resolves to GPU when
the runtime really offers a healthy, qualified CUDA provider and to CPU otherwise;
``--device cpu`` is served and reported as a degradation; ``--device gpu`` blocks instead of
falling back. Nothing here starts a background thread.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CATALOG_UNREADABLE, CatalogError
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.face import weights
from photo_search.face.service import (
    catch_up_association,
    catch_up_index,
    enqueue_targets,
    face_targets,
    job_counts,
    rematch_unassigned,
    run_queue,
    weights_report,
)
from photo_search.ingestion.service import CatalogService
from photo_search.scheduler.projection import face_summary

COMMANDS = ("weights", "status", "enqueue", "run", "index", "group", "rematch", "detect")
DEVICES = ("auto", "cpu", "gpu")


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
    parser = argparse.ArgumentParser(prog="photo-search-face", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    common = _common_options()
    commands.add_parser("weights", parents=[common], help="verify the pinned buffalo_l files")
    commands.add_parser("status", parents=[common], help="read-only face readiness summary")
    for name, help_text in (
        ("enqueue", "queue face jobs for the selected assets"),
        ("detect", "queue, detect, publish and group the selected assets"),
    ):
        subparser = commands.add_parser(name, parents=[common], help=help_text)
        _selection_options(subparser)
        subparser.add_argument(
            "--force",
            action="store_true",
            help="rewind an already finished job instead of only creating a missing one",
        )
        _device_options(subparser)
        subparser.add_argument("--worker-id", default="face-cli")
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
    run_parser = commands.add_parser("run", parents=[common], help="drain the runnable face jobs")
    _device_options(run_parser)
    run_parser.add_argument("--worker-id", default="face-cli")
    run_parser.add_argument("--max-jobs", type=int, default=None)
    run_parser.add_argument("--deadline-ms", type=int, default=None)
    commands.add_parser(
        "index", parents=[common], help="publish and confirm the face candidate index"
    )
    group_parser = commands.add_parser(
        "group", parents=[common], help="run the conservative association pass"
    )
    group_parser.add_argument("--limit", type=int, default=None)
    rematch_parser = commands.add_parser(
        "rematch", parents=[common], help="match unassigned stored faces without detection"
    )
    rematch_parser.add_argument("--limit", type=int, default=None, help="maximum assets per pass")
    return parser


def _device_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device",
        choices=DEVICES,
        default="auto",
        help=(
            "requested device: auto (default), cpu (served, reported as a degradation) or "
            "gpu (blocked instead of silently falling back)"
        ),
    )


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
    for key, value in error.details.items():
        print(f"  {key}: {value}", file=sys.stderr)


def _status_payload(data_root: Path) -> dict[str, Any]:
    from photo_search.config import catalog_db_path

    summary = face_summary(
        catalog_db_path(data_root), weights_present=weights.weights_present(data_root)
    )
    return {"status": "ok", "faces": summary}


def _select(service: CatalogService, args: argparse.Namespace) -> list[dict[str, Any]]:
    targets = face_targets(
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
                "a requested asset has no readable original to analyse",
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
                args,
                CatalogError("USAGE", f"--{option.replace('_', '-')} must not be negative"),
            )
            return 2
    if getattr(args, "deadline_ms", None) is not None and int(args.deadline_ms) < 1:
        _emit_error(
            args, CatalogError("USAGE", "the deadline must be a positive number of milliseconds")
        )
        return 2
    data_root = resolve_data_root(args.data_root)
    try:
        if command == "weights":
            _emit(args, weights_report(data_root))
            return 0
        if command == "status":
            _emit(args, _status_payload(data_root))
            return 0
        if command == "rematch":
            if not catalog_db_path(data_root).is_file():
                raise CatalogError(
                    CATALOG_UNREADABLE,
                    "rematch requires an existing catalog; check --data-root",
                )
            with CatalogService.open(data_root=data_root) as service:
                limit = args.limit if args.limit is not None else 100_000
                _emit(
                    args,
                    {"status": "ok", "rematch": rematch_unassigned(service.catalog, limit=limit)},
                )
            return 0
        # Every work command proves the pinned weights before anything is queued, so a broken
        # model root is one structured failure instead of a queue full of blocked jobs.
        resolved = weights.resolve_and_verify(data_root)
        with CatalogService.open(data_root=data_root) as service:
            payload: dict[str, Any] = {
                "status": "ok",
                "model_fingerprint": resolved.fingerprint,
            }
            device = str(getattr(args, "device", "auto"))
            if command in ("enqueue", "detect"):
                targets = _select(service, args)
                payload["selection"] = len(targets)
                payload["enqueue"] = enqueue_targets(
                    service.catalog,
                    targets,
                    now_ms=service.catalog.now_ms(),
                    force=bool(getattr(args, "force", False)),
                    device=device,
                )
            if command in ("run", "detect"):
                payload["run"] = run_queue(
                    service.catalog,
                    data_root=data_root,
                    device=device,
                    worker_id=str(getattr(args, "worker_id", "face-cli")),
                    limit=getattr(args, "max_jobs", None),
                    deadline_ms=getattr(args, "deadline_ms", None),
                )
            if command in ("index", "detect"):
                payload["index"] = catch_up_index(service.catalog)
            if command in ("group", "detect"):
                payload["association"] = catch_up_association(
                    service.catalog, limit=getattr(args, "limit", None) or 100_000
                )
            if command == "detect":
                payload["index_after_association"] = catch_up_index(service.catalog)
            payload["jobs"] = job_counts(service.catalog)
            payload["faces"] = face_summary(service.catalog.db_path, weights_present=True)
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


__all__ = ["COMMANDS", "DEVICES", "build_parser", "main", "run_command"]
