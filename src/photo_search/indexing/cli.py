"""``python -m photo_search.indexing.cli`` - the controlled semantic index entry point.

This is the operator-facing shell of :mod:`photo_search.indexing.maintenance`, and it
follows the shape of the other controlled entries in this build (``photo_search.face.cli``,
``photo_search.ocr.cli``): one JSON document with ``--json``, exit code 0 when the requested
work completed, 1 for a structured ``CatalogError`` and 2 for a usage error. It starts no
thread, no server and no scheduler.

Commands

``status``  read-only semantic index facts (schema version, outbox/entry/generation counts).
``drain``   the production maintenance entry: apply pending semantic outbox events, confirm
            the events whose derived row is already written, and report what the run cost -
            the events this call confirmed (including the batches that landed before a bounded
            retry), write transactions, lock-wait and hold-time distributions, and any
            structured ``busy_timeout`` refusal with its retry outcome.
``reexecute`` the production recovery entry (P4-02C): re-run the pinned embedding model on the
            assets behind artifacts that are awaiting re-execution and republish those artifacts
            through ``ArtifactStore.republish_reclaimed``. The model is never chosen silently:
            ``--model-dir``, ``--manifest`` and ``--device`` are required unless ``--dry-run``
            is used, which only lists the targets.

``drain`` is safe to run twice, and safe to run while another maintainer or a foreground
writer is active: nothing here takes an exclusive lock, so an event another maintainer
already confirmed is skipped instead of being written twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.indexing.maintenance import (
    DEFAULT_LIMIT,
    DEFAULT_REEXECUTION_LIMIT,
    DEFAULT_RETRY_BACKOFF_MS,
    DEFAULT_RETRY_LIMIT,
    drain_semantic_outbox,
    reexecute_reclaimed_artifacts,
    semantic_index_status,
)
from photo_search.runtime.device import DevicePolicy

COMMANDS = ("status", "drain", "reexecute")


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photo-search-index", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    common = _common_options()
    commands.add_parser(
        "status", parents=[common], help="read-only semantic index readiness summary"
    )
    drain = commands.add_parser(
        "drain", parents=[common], help="apply and confirm the pending semantic outbox"
    )
    drain.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"apply at most this many events (default {DEFAULT_LIMIT})",
    )
    drain.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="events per engine write; a maintenance access-path parameter only",
    )
    drain.add_argument(
        "--repair",
        action="store_true",
        help=(
            "run the P2-03 repair audit first (one engine point query per embedding "
            "artifact; not paid for by default)"
        ),
    )
    drain.add_argument(
        "--retry-limit",
        type=int,
        default=DEFAULT_RETRY_LIMIT,
        help="re-attempts of a refusal caused by an expired SQLite busy_timeout",
    )
    drain.add_argument(
        "--retry-backoff-ms",
        type=int,
        default=DEFAULT_RETRY_BACKOFF_MS,
        help="delay between those re-attempts",
    )
    reexecute = commands.add_parser(
        "reexecute",
        parents=[common],
        help="republish artifacts that are awaiting re-execution (P4-02C recovery)",
    )
    reexecute.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"re-execute at most this many artifacts (default {DEFAULT_REEXECUTION_LIMIT})",
    )
    reexecute.add_argument(
        "--dry-run",
        action="store_true",
        help="list the targets only: no model is loaded, nothing is encoded or written",
    )
    reexecute.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="directory of the pinned ONNX model (required unless --dry-run)",
    )
    reexecute.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="the pinned model manifest (required unless --dry-run)",
    )
    reexecute.add_argument(
        "--device",
        default=None,
        help="explicit device request; there is no silent default (required unless --dry-run)",
    )
    reexecute.add_argument(
        "--device-policy",
        choices=[policy.value for policy in DevicePolicy],
        default=DevicePolicy.CPU_ALLOWED.value,
        help="how the adapter resolves that request (default: cpu_allowed)",
    )
    return parser


def _image_encoder(
    *, model_dir: Path, manifest: Path, device: str, device_policy: str
) -> Any:
    """Build the pinned Chinese-CLIP adapter behind the ADR-0003 image-encoder boundary.

    The heavy runtime stays behind this function: the module itself imports no ONNX, and a
    model that cannot be loaded is a structured refusal (with the operator's own paths and the
    failure class) rather than a traceback or a silent CPU run.
    """
    try:
        from photo_search.models.embedding.chinese_clip_onnx import (
            ChineseClipOnnxAdapter,
            RuntimeConfig,
        )

        adapter = ChineseClipOnnxAdapter(
            Path(model_dir),
            Path(manifest),
            RuntimeConfig(
                requested_device=str(device),
                device_policy=DevicePolicy(str(device_policy)),
            ),
        )
        adapter.load()
    except Exception as error:
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "the re-execution model could not be loaded",
            details={
                "model_dir": str(model_dir),
                "manifest": str(manifest),
                "requested_device": str(device),
                "device_policy": str(device_policy),
                "reason": type(error).__name__,
            },
        ) from error
    return adapter


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


def run_command(args: argparse.Namespace) -> int:
    """Execute one ``index`` subcommand and return its process exit code."""
    command = getattr(args, "command", None)
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
    for option in ("limit", "retry_limit", "retry_backoff_ms"):
        value = getattr(args, option, None)
        if value is not None and int(value) < 0:
            _emit_error(
                args,
                CatalogError("USAGE", f"--{option.replace('_', '-')} must not be negative"),
            )
            return 2
    batch_size = getattr(args, "batch_size", None)
    if batch_size is not None and int(batch_size) < 1:
        _emit_error(args, CatalogError("USAGE", "--batch-size must be a positive number"))
        return 2
    if command == "reexecute" and not bool(getattr(args, "dry_run", False)):
        for option in ("model_dir", "manifest", "device"):
            if getattr(args, option, None) is None:
                _emit_error(
                    args,
                    CatalogError(
                        "USAGE",
                        f"--{option.replace('_', '-')} is required unless --dry-run is used",
                    ),
                )
                return 2

    data_root = resolve_data_root(args.data_root)
    try:
        if command == "status":
            # Read-only: the status answer must not create or migrate a database.
            _emit(args, semantic_index_status(catalog_db_path(data_root)))
            return 0
        if command == "reexecute":
            dry_run = bool(getattr(args, "dry_run", False))
            encoder = (
                None
                if dry_run
                else _image_encoder(
                    model_dir=args.model_dir,
                    manifest=args.manifest,
                    device=args.device,
                    device_policy=args.device_policy,
                )
            )
            with Catalog.open(data_root=data_root) as catalog:
                payload = reexecute_reclaimed_artifacts(
                    catalog,
                    encoder=encoder,
                    limit=(
                        int(args.limit)
                        if args.limit is not None
                        else DEFAULT_REEXECUTION_LIMIT
                    ),
                    dry_run=dry_run,
                )
            _emit(args, payload)
            return 0
        with Catalog.open(data_root=data_root) as catalog:
            payload = drain_semantic_outbox(
                catalog,
                limit=int(args.limit) if args.limit is not None else DEFAULT_LIMIT,
                batch_size=batch_size,
                repair=bool(args.repair),
                retry_limit=int(args.retry_limit),
                retry_backoff_ms=int(args.retry_backoff_ms),
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


__all__ = ["COMMANDS", "build_parser", "main", "run_command"]
