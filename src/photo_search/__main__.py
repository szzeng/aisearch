"""Local development entry point and the offline Chinese-CLIP demo."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import logging
import multiprocessing
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from photo_search.api.context import ApiContext
from photo_search.api.session import bootstrap_session_credential
from photo_search.api.settings import ApiSettings, BindNotAllowed
from photo_search.app import create_app
from photo_search.backup import create_backup, restore_backup
from photo_search.catalog.errors import CatalogError
from photo_search.config import resolve_data_root
from photo_search.ingestion.cli import add_catalog_parser, run_catalog_command
from photo_search.models.embedding.cli import add_embedding_parser, run_embedding_command
from photo_search.models.embedding.registry import registry_for
from photo_search.runtime.device import DevicePolicy
from photo_search.storage import storage_clean, storage_inspect
from photo_search.workers.client import AiWorkerClient
from photo_search.workers.query_encoder import (
    EncoderWiring,
    default_model_dir,
    resolve_provider_encoder,
    resolve_query_encoder,
)

#: A cold CUDA start on a power-gated laptop GPU can take minutes; validation runs use
#: generous deadlines so a slow start is never mistaken for a hung worker.
COLD_START_TIMEOUT_SECONDS = 900.0

#: Overrides for the pinned embedding model of a *normal* run (P4-02N). They exist so the
#: model can live outside the default data root without compiling a user path into the
#: source; an explicit command line value always wins over the environment.
MODEL_DIR_ENV = "PHOTO_SEARCH_MODEL_DIR"
MODEL_MANIFEST_ENV = "PHOTO_SEARCH_MODEL_MANIFEST"
MODEL_DEVICE_ENV = "PHOTO_SEARCH_MODEL_DEVICE"

#: Query parameters that must never reach a log file, even in an access log line.
_SECRET_QUERY = re.compile(r"(?i)([?&](?:token|csrf_token)=)[^&\s\"]*")


def redact_token_query(text: str) -> str:
    """Replace a bootstrap credential in a request line with a placeholder."""
    return _SECRET_QUERY.sub(r"\1<redacted>", str(text))


class TokenRedactingFilter(logging.Filter):
    """Keep the one-time credential out of ``uvicorn.access`` lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_token_query(record.msg)
        if record.args:
            record.args = tuple(
                redact_token_query(value) if isinstance(value, str) else value
                for value in record.args
            )
        return True


def _log_config() -> dict:
    """Uvicorn's default logging with the credential filter on the access logger."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["token_redaction"] = {"()": TokenRedactingFilter}
    config.setdefault("loggers", {}).setdefault("uvicorn.access", {})["filters"] = [
        "token_redaction"
    ]
    return config


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    """Open the one-time session URL once the local HTTP listener is accepting connections."""

    def _worker() -> None:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, int(port)), timeout=0.25):
                    webbrowser.open(url, new=2)
                    return
            except OSError:
                time.sleep(0.15)

    threading.Thread(target=_worker, name="photo-search-browser", daemon=True).start()


def _serve(args: argparse.Namespace) -> int:
    """Start the local API behind a fresh one-time session credential."""
    settings = ApiSettings.create(
        data_root=args.data_root,
        bind_host=args.host,
        bind_port=args.port,
        allow_non_loopback=args.allow_non_loopback,
    )
    try:
        settings.validate_bind()
    except BindNotAllowed as error:
        print(f"error: BIND_NOT_ALLOWED: {error}", file=sys.stderr)
        return 2
    bootstrap = bootstrap_session_credential(settings)
    # P4-02N: the running application gets the real, worker-backed query encoder when this
    # machine has the pinned model; without it the factory stays ``None`` and every semantic
    # request keeps its documented structured refusal.
    wiring = _encoder_wiring(args, settings.data_root)
    api = ApiContext(
        dataclasses.replace(settings, session_token=bootstrap.token),
        encoder_factory=wiring.factory,
        # P4-02Y: the one-click refresh embeds new content in the *same* pinned space the
        # query path already serves, so the manifest the encoder was resolved from is the
        # manifest the refresh must use.
        refresh_manifest_path=str(wiring.manifest) if wiring.available else None,
    )
    application = create_app(settings=api.settings, context=api)
    # The credential is printed to the console on purpose: it is the only bootstrap channel
    # the user has, and the access-log filter above keeps it out of every log line.
    print(f"photo-search: listening on http://{settings.bind_host}:{settings.bind_port}")
    print(f"photo-search: loopback_only={settings.loopback_only}")
    print(
        "photo-search: semantic channel "
        f"{wiring.reason} (model_dir={wiring.model_dir}, manifest={wiring.manifest})"
    )
    session_url = (
        f"http://{settings.browser_host()}:{settings.bind_port}/api/v1/session"
        f"?next=/&token={bootstrap.token}"
    )
    print(
        "photo-search: open this single-use session URL to start the local session:\n"
        f"  {session_url}"
    )
    if args.open_browser:
        _open_browser_when_ready(session_url, settings.browser_host(), settings.bind_port)
    if bootstrap.path is not None:
        print(f"photo-search: credential file {bootstrap.path} ({bootstrap.permission})")
    sys.stdout.flush()
    uvicorn.run(
        application,
        host=settings.bind_host,
        port=settings.bind_port,
        log_config=_log_config(),
    )
    return 0


def _encoder_wiring(args: argparse.Namespace, data_root: Path) -> Any:
    """Where this process looks for the pinned model, and whether it found it.

    P4-Embedding-Arch: the *active search profile* names the provider this process must serve,
    so the provider is resolved from that profile and from the registry of installed models
    rather than from a compiled-in model name. ``--model-dir``/``--model-manifest`` stay as an
    explicit single-provider override for diagnostics and tests, and both paths keep P4-02N's
    honest ``MODEL_DIR_MISSING``/``MANIFEST_MISSING`` answers.
    """
    device = args.model_device or os.environ.get(MODEL_DEVICE_ENV) or None
    requested = None if device in (None, "auto") else str(device)
    explicit_dir = args.model_dir or os.environ.get(MODEL_DIR_ENV)
    explicit_manifest = args.model_manifest or os.environ.get(MODEL_MANIFEST_ENV)
    if explicit_dir or explicit_manifest:
        return resolve_query_encoder(
            model_dir=Path(explicit_dir or _default_model_dir(data_root)),
            manifest_path=Path(explicit_manifest or _default_manifest()),
            device=requested,
        )
    registry = registry_for(data_root)
    activated = active_provider_id(data_root)
    provider = registry.find(activated) if activated is not None else registry.default()
    if provider is None:
        return EncoderWiring("PROVIDER_UNKNOWN", "", "", None, activated or "")
    return resolve_provider_encoder(provider=provider, device=requested)


def active_provider_id(data_root: Path) -> str | None:
    """The provider of this catalog's activated search profile, read without writing.

    The startup entry must not migrate or create a catalog just to look at one pointer, so this
    is a plain read-only query against the documented ``0007`` tables; anything unreadable means
    "no activated profile", which falls back to the historical default provider.
    """
    from photo_search.config import catalog_db_path
    from photo_search.models.embedding.base import provider_id_of_processor

    path = Path(catalog_db_path(data_root))
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        profile = connection.execute(
            "SELECT embedding_processor_id FROM search_profiles WHERE profile_id = ?",
            (str(row[0]),),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if profile is None or profile[0] is None:
        return None
    return provider_id_of_processor(str(profile[0]))


def _default_model_dir(data_root: Path | None = None) -> Path:
    """The pinned model directory under the resolved application data root (P0-04)."""
    return default_model_dir(data_root if data_root is not None else resolve_data_root())


def _default_manifest() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    return Path.cwd() / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"


def _demo_search(
    model_dir: Path,
    manifest: Path,
    device: str | None,
    device_policy: DevicePolicy,
    output: Path | None,
) -> int:
    """Run the deterministic fixture search inside the managed AI worker."""
    with AiWorkerClient(
        model_dir, manifest, device=device, device_policy=device_policy
    ) as worker:
        health = worker.health(timeout=COLD_START_TIMEOUT_SECONDS)
        demo = worker.demo_search(timeout=COLD_START_TIMEOUT_SECONDS)
        usage = worker.provider_usage(timeout=COLD_START_TIMEOUT_SECONDS)
    report = {
        "status": "ok",
        "device": demo["device"],
        "runtime": health.get("runtime"),
        "provider_usage": usage["sessions"],
        "query": demo["query"],
        "query_l2_norm": demo["query_l2_norm"],
        "images": demo["images"],
        "top_k": demo["top_k"],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal local photo search")
    subparsers = parser.add_subparsers(dest="command")
    demo = subparsers.add_parser("demo-search", help="run the offline Chinese-CLIP fixture search")
    demo.add_argument("--model-dir", type=Path, default=_default_model_dir())
    demo.add_argument("--manifest", type=Path, default=_default_manifest())
    demo.add_argument(
        "--device",
        default="cuda",
        help="cuda (default, GPU required) or cpu (explicit reported degradation)",
    )
    demo.add_argument("--device-policy", default=DevicePolicy.CPU_ALLOWED.value)
    demo.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the JSON report to this path as UTF-8 (console output still prints)",
    )
    add_catalog_parser(subparsers)
    add_embedding_parser(subparsers)
    backup = subparsers.add_parser("backup", help="stopped-service backup and restore")
    backup_commands = backup.add_subparsers(dest="backup_command")
    for name, help_text in (
        ("create", "create a consistent stopped-service backup"),
        ("restore", "restore a backup into an empty data root"),
    ):
        command = backup_commands.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true", help="emit one JSON document")
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--bundle", type=Path, required=True)
        if name == "create":
            command.add_argument(
                "--stopped",
                action="store_true",
                help="confirm all writers and catalog/Lance handles are stopped",
            )
    storage = subparsers.add_parser("storage", help="read-only storage accounting and stopped-service file GC")
    storage_commands = storage.add_subparsers(dest="storage_command")
    storage_inspect_parser = storage_commands.add_parser("inspect", help="inspect storage usage")
    storage_inspect_parser.add_argument("--data-root", type=Path, required=True)
    storage_inspect_parser.add_argument("--deep", action="store_true", help="bounded recursive scan; otherwise only inspect catalog and sidecars")
    storage_inspect_parser.add_argument("--json", action="store_true")
    storage_clean_parser = storage_commands.add_parser("clean", help="preview or clean derived files")
    storage_clean_parser.add_argument("--data-root", type=Path, required=True)
    storage_clean_parser.add_argument("--scope", choices=("artifacts", "media-cache", "previews", "face-crops"), required=True)
    storage_clean_parser.add_argument("--stopped", action="store_true", help="confirm all data-root writers are stopped")
    storage_clean_parser.add_argument("--apply", action="store_true", help="execute the fresh eligibility check; without this flag only preview")
    storage_clean_parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--host",
        default=None,
        help="bind address for the local API (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=None, help="bind port for the local API (default 8000)"
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="explicitly accept exposing the API beyond this machine (no LAN auth exists)",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="open the one-time local session URL in the system default browser after startup",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="override the application data root (diagnostics and tests)",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help=(
            "pinned embedding model directory to serve text/image search from "
            "(default: <data root>/models/chinese_clip/vit-b-16; "
            f"env {MODEL_DIR_ENV})"
        ),
    )
    parser.add_argument(
        "--model-manifest",
        type=Path,
        default=None,
        help=(
            "pinned model manifest (default: the shipped Chinese-CLIP manifest; "
            f"env {MODEL_MANIFEST_ENV})"
        ),
    )
    parser.add_argument(
        "--model-device",
        choices=("auto", "cuda", "cpu"),
        default=None,
        help=(
            "device for the query encoder: auto follows the ADR-0001 policy (default CUDA, "
            f"an explicit cpu request is a reported degradation); env {MODEL_DEVICE_ENV}"
        ),
    )
    args = parser.parse_args()
    if args.command == "catalog":
        raise SystemExit(run_catalog_command(args))
    if args.command == "embedding":
        raise SystemExit(run_embedding_command(args))
    if args.command == "backup":
        try:
            if args.backup_command == "create":
                payload = create_backup(
                    data_root=args.data_root, destination=args.bundle, stopped=args.stopped
                )
            elif args.backup_command == "restore":
                payload = restore_backup(bundle=args.bundle, data_root=args.data_root)
            else:
                raise CatalogError("USAGE", "choose one of ['create', 'restore']")
        except CatalogError as error:
            payload, code = {"status": "error", "error": error.as_dict()}, 1
        else:
            code = 0
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else payload)
        raise SystemExit(code)
    if args.command == "storage":
        try:
            if args.storage_command == "inspect":
                payload = storage_inspect(args.data_root, deep=args.deep)
            elif args.storage_command == "clean":
                payload = storage_clean(
                    args.data_root,
                    scopes=(args.scope,),
                    apply=args.apply,
                    stopped=args.stopped,
                )
            else:
                payload = {"status": "error", "code": "USAGE", "message": "choose storage inspect or clean"}
        except (OSError, ValueError, RuntimeError) as error:
            payload = {"status": "error", "code": "STORAGE_OPERATION_FAILED", "reason": type(error).__name__}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if args.json else json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0 if payload.get("status") in {"ok", "preview"} else 1)
    if args.command == "demo-search":
        raise SystemExit(
            _demo_search(
                args.model_dir,
                args.manifest,
                args.device,
                DevicePolicy(args.device_policy),
                args.output,
            )
        )
    raise SystemExit(_serve(args))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
