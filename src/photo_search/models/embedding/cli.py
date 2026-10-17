"""The lightweight provider commands: list / prepare-local / build-candidate / select-profile.

These four commands are the whole operator surface of P4-Embedding-Arch, and they follow the
existing ``photo-search`` command style:

``photo-search embedding list``
    What this data root has installed, and which provider the activated profile serves.
``photo-search embedding prepare-local``
    Copy and verify a *local* model directory into ``<data_root>/models/...`` and write the
    data-root manifest. Nothing is downloaded; a missing or changed file is a structured refusal.
``photo-search embedding build-candidate``
    Build (or extend) the index of one provider without touching the served pointer.
``photo-search embedding select-profile``
    Publish one candidate profile after the authoritative coverage/health gate, inside one
    transaction, with the managed data-root lease held and an explicit ``--stopped`` declaration.

No command here can silently switch the served model: a build never activates another space, and
the selection is the only place a pointer moves - with the whole bundle re-checked in the same
``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from PIL import Image

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import SELECTION_WRITER_ACTIVE, CatalogError
from photo_search.config import catalog_db_path, resolve_data_root, vectors_dir
from photo_search.indexing import selection as selection_module
from photo_search.indexing import semantic
from photo_search.models.embedding.base import (
    provider_block,
    provider_id_of_processor,
    read_manifest,
    required_artifact_names,
)
from photo_search.models.embedding.qwen3_vl import verify_pinned_files
from photo_search.models.embedding.registry import model_manifests_dir, registry_for
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
from photo_search.search import profile as profile_module
from photo_search.workers.query_encoder import build_worker_query_encoder

HEALTH_TEXT = "\u5065\u5eb7\u68c0\u67e5"
HEALTH_IMAGE_SIZE = (64, 64)


class _HealthOnly:
    """The minimal adapter surface the runtime-facts reader of the catalog needs."""

    def __init__(self, health: dict[str, Any]) -> None:
        self._health = health

    def health(self) -> dict[str, Any]:
        return self._health


def add_embedding_parser(subparsers: Any) -> None:
    """Register the ``embedding`` command family on an ``argparse`` subparser set."""
    parser = subparsers.add_parser(
        "embedding", help="local semantic providers: list, prepare, build and select"
    )
    commands = parser.add_subparsers(dest="embedding_command")

    listing = commands.add_parser("list", help="show installed providers and the active profile")
    listing.add_argument("--data-root", type=Path, default=None)
    listing.add_argument("--json", action="store_true", help="emit one JSON document")

    prepare = commands.add_parser(
        "prepare-local", help="verify a local model directory and install it under the data root"
    )
    prepare.add_argument("--provider", required=True, help="the provider id to install")
    prepare.add_argument("--source", type=Path, required=True, help="the local model directory")
    prepare.add_argument(
        "--revision",
        default=None,
        help="the resolved revision to record (default: the one the shipped manifest pins)",
    )
    prepare.add_argument("--data-root", type=Path, default=None)
    prepare.add_argument(
        "--force", action="store_true", help="replace an installed manifest of the same provider"
    )
    prepare.add_argument("--json", action="store_true")

    build = commands.add_parser(
        "build-candidate", help="build one provider's index without activating it"
    )
    build.add_argument("--provider", required=True)
    build.add_argument("--data-root", type=Path, default=None)
    build.add_argument("--limit", type=int, default=semantic.DEFAULT_BUILD_LIMIT)
    build.add_argument("--source-id", type=int, default=None)
    build.add_argument("--device", default="auto", help="auto, cuda or cpu")
    build.add_argument(
        "--retry-failed",
        action="store_true",
        help="rewind this provider's FAILED jobs for the same input revision and try them again",
    )
    build.add_argument("--json", action="store_true")

    select = commands.add_parser(
        "select-profile", help="publish a candidate profile after the authoritative gate"
    )
    select.add_argument("--profile", required=True, help="the immutable profile id to publish")
    select.add_argument("--data-root", type=Path, default=None)
    select.add_argument(
        "--stopped",
        action="store_true",
        help="confirm that the service and every other writer of this data root are stopped",
    )
    select.add_argument("--json", action="store_true")


def run_embedding_command(args: argparse.Namespace) -> int:
    """Dispatch one ``embedding`` subcommand; returns the process exit code."""
    handlers = {
        "list": _list_providers,
        "prepare-local": _prepare_local,
        "build-candidate": _build_candidate,
        "select-profile": _select_profile,
    }
    handler = handlers.get(str(getattr(args, "embedding_command", "") or ""))
    if handler is None:
        raise CatalogError(
            "USAGE",
            "choose one of ['list', 'prepare-local', 'build-candidate', 'select-profile']",
        )
    try:
        payload = handler(args)
        code = 0
    except CatalogError as error:
        payload = {"status": "error", "error": error.as_dict()}
        code = 1
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        if getattr(args, "json", False)
        else json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    return code


def _list_providers(args: argparse.Namespace) -> dict[str, Any]:
    data_root = resolve_data_root(args.data_root)
    registry = registry_for(data_root)
    return {
        "status": "ok",
        "data_root": str(data_root),
        "manifests_dir": str(model_manifests_dir(data_root)),
        "providers": [entry.to_dict() for entry in registry.providers()],
        "skipped_manifests": list(registry.skipped),
        "active_profile": read_active_profile(data_root),
    }


def read_active_profile(data_root: Path) -> dict[str, Any] | None:
    """The activated profile as four plain facts, read without writing anything."""
    path = Path(catalog_db_path(data_root))
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT p.profile_id AS profile_id, p.embedding_processor_id AS processor_id, "
            "p.space_id AS space_id, p.image_generation_id AS generation_id "
            "FROM active_search_profile AS a JOIN search_profiles AS p "
            "ON p.profile_id = a.profile_id WHERE a.state_id = 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None or row["profile_id"] is None:
        return None
    return {
        "profile_id": str(row["profile_id"]),
        "processor_id": str(row["processor_id"]),
        "space_id": str(row["space_id"]),
        "generation_id": str(row["generation_id"]),
        "provider_id": provider_id_of_processor(str(row["processor_id"])),
    }


def _prepare_local(args: argparse.Namespace) -> dict[str, Any]:
    data_root = resolve_data_root(args.data_root)
    registry = registry_for(data_root)
    entry = registry.get(str(args.provider))
    source = Path(args.source)
    if not source.is_dir():
        raise CatalogError(
            "USAGE", "the local model directory does not exist", details={"source": str(source)}
        )
    template = read_manifest(entry.manifest_path)
    block = provider_block(template)
    verification = (
        verify_pinned_files(source, template)
        if str(block["adapter"]) == "qwen3_vl"
        else _verify_local_source(source, template)
    )
    target_manifest = model_manifests_dir(data_root) / f"{entry.provider_id}.json"
    installed = json.loads(json.dumps(template))
    # The installed manifest always describes its provider explicitly, so the data root is
    # self-describing even though the shipped Chinese-CLIP template predates provider blocks.
    installed["provider"] = {
        "provider_id": str(block["provider_id"]),
        "display_name": str(block["display_name"]),
        "adapter": str(block["adapter"]),
        "model_dir": "/".join(str(part) for part in block["model_dir"]),
        "device_policy": str(block["device_policy"]),
    }
    if args.revision:
        installed["model"]["resolved_revision"] = str(args.revision)
    installed["installed_from"] = str(source)
    replaced_revision = None
    if target_manifest.is_file():
        previous = read_manifest(target_manifest)
        replaced_revision = str(previous.get("model", {}).get("resolved_revision") or "")
        if replaced_revision != str(installed["model"]["resolved_revision"]) and not args.force:
            raise CatalogError(
                "EMBEDDING_MANIFEST_INVALID",
                "a different revision of this provider is already installed",
                details={
                    "installed_revision": replaced_revision,
                    "requested_revision": str(installed["model"]["resolved_revision"]),
                    "hint": "pass --force to install the new revision (a new space)",
                },
            )
    entry.model_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in required_artifact_names(template):
        destination = entry.model_dir / name
        if destination.is_file() and _same_content(source / name, destination):
            continue
        shutil.copy2(source / name, destination)
        copied.append(name)
    target_manifest.parent.mkdir(parents=True, exist_ok=True)
    target_manifest.write_text(
        json.dumps(installed, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    installed_entry = registry_for(data_root).get(entry.provider_id)
    return {
        "status": "ok",
        "provider_id": entry.provider_id,
        "adapter": entry.adapter,
        "source": str(source),
        "model_dir": str(entry.model_dir),
        "manifest": str(target_manifest),
        "copied_files": copied,
        "verification": verification,
        "processor_id": installed_entry.identity.processor_id,
        "space_id": installed_entry.identity.space_id,
        "dimension": installed_entry.identity.dimension,
        "model_revision": installed_entry.identity.model_revision,
        "replaced_revision": replaced_revision,
    }


def _verify_local_source(source: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Check one local model directory against every digest the manifest records.

    The historical Chinese-CLIP manifest records a sha256 per ONNX graph and for its tokenizer
    vocabulary, so the copy that becomes the installed model is verified byte for byte - the same
    rule the Qwen manifest enforces - instead of being accepted because a file with that name
    exists.
    """
    expected = _recorded_checksums(manifest)
    checked = 0
    total_bytes = 0
    hashed = 0
    for name in required_artifact_names(manifest):
        path = Path(source) / name
        if not path.is_file():
            raise CatalogError(
                "EMBEDDING_WEIGHTS_MISSING",
                "a required model file is missing from the local directory",
                details={"file": name, "source": str(source)},
            )
        checked += 1
        total_bytes += path.stat().st_size
        digest = expected.get(str(name))
        if digest and not _file_sha256(path) == str(digest):
            raise CatalogError(
                "EMBEDDING_WEIGHTS_HASH_MISMATCH",
                "a required model file has a different checksum than its manifest records",
                details={"file": name, "source": str(source)},
            )
        if digest:
            hashed += 1
    return {"files": checked, "bytes": total_bytes, "hashes_verified": hashed == checked}


def _recorded_checksums(manifest: dict[str, Any]) -> dict[str, str]:
    """Every ``name -> sha256`` pair one manifest records, whichever shape it uses."""
    expected: dict[str, str] = {}
    artifacts = (manifest.get("artifacts") or {}).get("onnx") or {}
    for role in ("image", "text"):
        item = artifacts.get(role) or {}
        if item.get("path") and item.get("sha256"):
            expected[str(item["path"])] = str(item["sha256"])
    tokenizer = manifest.get("tokenizer") or {}
    if tokenizer.get("vocab_file") and tokenizer.get("vocab_sha256"):
        expected[str(tokenizer["vocab_file"])] = str(tokenizer["vocab_sha256"])
    for item in manifest.get("weights") or ():
        if item.get("name") and item.get("sha256"):
            expected[str(item["name"])] = str(item["sha256"])
    return expected


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _same_content(left: Path, right: Path) -> bool:
    """Compare complete files in bounded memory, including multi-GB weights.

    Equal lengths (or timestamps) do not prove that a previously installed model
    is intact. A second prepare-local must repair same-size corruption too.
    """
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        if left.samefile(right):
            return True
        with left.open("rb") as source, right.open("rb") as installed:
            while block := source.read(1024 * 1024):
                if block != installed.read(len(block)):
                    return False
            return installed.read(1) == b""
    except OSError:
        return False


def _build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    data_root = resolve_data_root(args.data_root)
    registry = registry_for(data_root)
    entry = registry.require_installed(str(args.provider))
    with Catalog.open(data_root=data_root) as catalog:
        encoder = build_worker_query_encoder(
            model_dir=entry.model_dir,
            manifest_path=entry.manifest_path,
            device=None if args.device in (None, "auto") else str(args.device),
            device_policy=entry.device_policy,
        )
        try:
            live_spec = encoder.spec()
            if str(live_spec.envelope["space_id"]) != str(entry.identity.space_id):
                raise CatalogError(
                    "SPACE_MISMATCH",
                    "the loaded model does not produce the space this manifest describes",
                    details={
                        "manifest_space_id": str(entry.identity.space_id),
                        "loaded_space_id": str(live_spec.envelope["space_id"]),
                    },
                )
            payload = semantic.build_index(
                catalog,
                encoder=encoder,
                manifest_path=entry.manifest_path,
                expected_space_id=str(entry.identity.space_id),
                expected_processor_id=str(entry.identity.processor_id),
                limit=None if args.limit is None else int(args.limit),
                source_id=None if args.source_id is None else int(args.source_id),
                device=str(args.device),
                device_policy=str(entry.device_policy),
                worker_id="embedding-build-candidate",
                retry_failed=bool(getattr(args, "retry_failed", False)),
            )
            spec = semantic.semantic_spec(
                entry.manifest_path,
                semantic.adapter_runtime_facts(encoder),
                device_policy=str(entry.device_policy),
            )
            profile = semantic.ensure_search_profile(
                catalog,
                spec,
                space_id=str(entry.identity.space_id),
                generation_id=str(payload["generation"]["generation_id"]),
            )
        finally:
            stop = getattr(encoder, "close", None)
            if callable(stop):
                stop()
    return {
        "status": "ok",
        "provider_id": entry.provider_id,
        "adapter": entry.adapter,
        "processor_id": str(entry.identity.processor_id),
        "space_id": str(entry.identity.space_id),
        "dimension": int(entry.identity.dimension),
        "build": payload,
        "profile": profile,
        "activated": bool(profile.get("activated")),
    }


def _select_profile(args: argparse.Namespace) -> dict[str, Any]:
    data_root = resolve_data_root(args.data_root)
    if not args.stopped:
        raise CatalogError(
            "USAGE",
            "the selection requires --stopped: the service and every other writer must be closed",
        )
    try:
        lease = acquire_data_root_lease(data_root)
    except DataRootLeaseBusy as error:
        raise CatalogError(
            SELECTION_WRITER_ACTIVE,
            "another managed process owns this data root; stop it before selecting a profile",
            details={"reason": "DATA_ROOT_LEASE_BUSY", "lease": str(error)},
        ) from error
    with lease, Catalog.open(data_root=data_root) as catalog:
        profile = profile_module.load_profile(catalog, str(args.profile))
        if profile is None:
            raise CatalogError(
                "PROFILE_GENERATION_MISMATCH",
                "the requested profile does not exist",
                details={"reason": "PROFILE_NOT_FOUND", "profile_id": str(args.profile)},
            )
        registry = registry_for(data_root)
        entry = registry.require_installed(
            provider_id_of_processor(profile.embedding_processor_id)
        )
        encoder = build_worker_query_encoder(
            model_dir=entry.model_dir,
            manifest_path=entry.manifest_path,
            device_policy=entry.device_policy,
        )
        try:
            health = encoder.health()
            check = _candidate_health(encoder)
            spec, evidence = _record_candidate_runtime(catalog, entry, health)
            if str(spec.envelope["space_id"]) != str(profile.space_id):
                raise CatalogError(
                    "SPACE_MISMATCH",
                    "the installed model does not produce the space this profile binds",
                    details={
                        "profile_space_id": str(profile.space_id),
                        "installed_space_id": str(spec.envelope["space_id"]),
                    },
                )
        finally:
            stop = getattr(encoder, "close", None)
            if callable(stop):
                stop()
        store = ArtifactStore.open(data_root)
        report, lance = selection_module.selection_report(
            catalog,
            store,
            generation_id=str(profile.image_generation_id),
            space_id=str(profile.space_id),
            processor_id=str(profile.embedding_processor_id),
            vectors_root=vectors_dir(data_root),
        )
        selection_module.require_selection_ready(report, lance=lance)
        published, changed, readiness = profile_module.activate_profile(
            catalog,
            profile_id=str(profile.profile_id),
            precondition=selection_module.selection_precondition(report, lance=lance),
        )
    return {
        "status": "ok",
        "profile_id": str(published.profile_id),
        "changed": bool(changed),
        "space_id": str(published.space_id),
        "processor_id": str(published.embedding_processor_id),
        "generation_id": str(published.image_generation_id),
        "runtime_package_id": str(published.runtime_package_id),
        "readiness": readiness,
        "coverage": report.to_dict(),
        "lance": lance,
        "model_health": check,
        "runtime_evidence": evidence,
    }


def _candidate_health(encoder: Any) -> dict[str, Any]:
    """One local text and one local image encode through the *candidate* adapter.

    This is the "the candidate really runs here, now" check of the controlled selection - not a
    quality measurement. Both vectors are validated like a real query vector (dimension, finite,
    non-zero), so an adapter that loads but cannot encode is refused before the pointer moves.
    """
    import tempfile

    dimension = int(encoder.spec().envelope["dimension"])
    text_vector = encoder.encode_queries([HEALTH_TEXT])[0]
    with tempfile.TemporaryDirectory(prefix="photo-search-select-") as raw:
        path = Path(raw) / "health.png"
        Image.new("RGB", HEALTH_IMAGE_SIZE, (120, 60, 200)).save(path)
        image_vector = encoder.encode_images([path])[0]
    return {
        "dimension": dimension,
        "text": _vector_facts(text_vector, dimension),
        "image": _vector_facts(image_vector, dimension),
    }


def _vector_facts(vector: Any, dimension: int) -> dict[str, Any]:
    import math

    values = [float(value) for value in vector]
    if len(values) != dimension or not all(math.isfinite(value) for value in values):
        raise CatalogError(
            "RUNTIME_RESOLUTION_MISMATCH",
            "the candidate model returned an unusable health-check vector",
            details={"expected_dimension": dimension, "actual_dimension": len(values)},
        )
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        raise CatalogError(
            "RUNTIME_RESOLUTION_MISMATCH",
            "the candidate model returned a zero health-check vector",
        )
    return {"dimension": len(values), "l2_norm": round(norm, 6), "finite": True}


def _record_candidate_runtime(
    catalog: Catalog, entry: Any, health: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """Record the capability snapshot and qualification of the device that just ran."""
    from photo_search.scheduler import engine as scheduler_engine

    facts = semantic.adapter_runtime_facts(_HealthOnly(health))
    spec = semantic.semantic_spec(
        entry.manifest_path, facts, device_policy=str(entry.device_policy)
    )
    scheduler = scheduler_engine.Scheduler(catalog, [spec], lease_seconds=catalog.lease_seconds)
    evidence = semantic.record_runtime_evidence(
        scheduler, spec, facts, now_ms=catalog.now_ms()
    )
    return spec, evidence


__all__ = [
    "HEALTH_IMAGE_SIZE",
    "HEALTH_TEXT",
    "add_embedding_parser",
    "read_active_profile",
    "run_embedding_command",
]
