"""Explicit, manifest-pinned download of the shared Qwen embedding weights.

The Hugging Face Hub client only resolves the repository and immutable revision named by
the shipped manifest. Bytes land in an operation-owned staging directory, pass the same
fixed SHA/size verification as ``prepare-local``, then use that existing installer.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from photo_search.catalog.errors import CatalogError
from photo_search.config import resolve_data_root
from photo_search.models.embedding import cli as embedding_cli
from photo_search.models.embedding.base import (
    model_dir_for,
    provider_block,
    read_manifest,
    required_artifact_names,
)
from photo_search.models.embedding.qwen3_vl import verify_pinned_files
from photo_search.models.embedding.registry import shipped_manifest_dir

QWEN_PROVIDER_ID = "qwen3-vl-embedding-2b-exif-v2"
MIN_FREE_RESERVE_BYTES = 512 * 1024 * 1024


def download_qwen(
    data_root: str | Path | None,
    *,
    confirmed: bool,
    snapshot_download_fn: Callable[..., str] | None = None,
    disk_usage_fn: Callable[[str | Path], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    """Download the fixed Qwen revision after explicit confirmation and install it safely."""
    if not confirmed:
        raise CatalogError(
            "MODEL_DOWNLOAD_CONFIRMATION_REQUIRED",
            "confirm the fixed Qwen model download and its disk requirement before continuing",
        )

    root = resolve_data_root(data_root).resolve()
    # Download trust always comes from the read-only shipped manifest. A data-root manifest
    # may describe an installed provider, but it cannot redirect this download to another repo.
    template_path = shipped_manifest_dir() / f"{QWEN_PROVIDER_ID}.json"
    template = read_manifest(template_path)
    provider = provider_block(template)
    model = template.get("model") if isinstance(template.get("model"), dict) else {}
    repository = str(model.get("id") or "")
    revision = str(model.get("resolved_revision") or "")
    if (
        provider["provider_id"] != QWEN_PROVIDER_ID
        or provider["model_dir"] != ("models", "qwen3_vl_embedding")
        or repository != "Qwen/Qwen3-VL-Embedding-2B"
        or len(revision) != 40
        or any(char not in "0123456789abcdef" for char in revision.lower())
    ):
        raise CatalogError("EMBEDDING_MANIFEST_INVALID", "the shipped Qwen download pin is invalid")

    model_dir = model_dir_for(root, provider)
    names = required_artifact_names(template)
    shipped_dir = shipped_manifest_dir()
    installed_dir = root / "models" / "manifests"
    for provider_id in (
        QWEN_PROVIDER_ID,
        "qwen3-vl-embedding-2b-1024d-exif-v2",
        "qwen3-vl-embedding-2b",
    ):
        installed_path = installed_dir / f"{provider_id}.json"
        if not installed_path.is_file():
            continue
        expected = read_manifest(shipped_dir / f"{provider_id}.json")
        installed = read_manifest(installed_path)
        if not _same_pinned_provider(installed, expected):
            installed_revision = str(installed.get("model", {}).get("resolved_revision") or "")
            raise CatalogError(
                "EMBEDDING_MANIFEST_INVALID",
                "an installed Qwen provider differs from its shipped pin; shared weights will not be replaced",
                details={"installed_revision": installed_revision, "requested_revision": revision},
            )
        if provider_id == QWEN_PROVIDER_ID:
            try:
                verify_pinned_files(model_dir, template)
            except CatalogError:
                # A same-revision damaged install may be repaired only after the new staging
                # snapshot has passed every pinned hash; _prepare_local performs that copy.
                pass
            else:
                return {
                    "status": "already_ready",
                    "provider_id": QWEN_PROVIDER_ID,
                    "revision": revision,
                    "model_dir": str(model_dir),
                    "downloaded": False,
                }

    expected_bytes = sum(int(item["bytes"]) for item in template.get("weights", []))
    # One complete staged download plus one verified publish copy, with a small reserve.
    required_free = (expected_bytes * 2) + MIN_FREE_RESERVE_BYTES
    root.mkdir(parents=True, exist_ok=True)
    free_bytes = int(disk_usage_fn(root).free)
    if free_bytes < required_free:
        raise CatalogError(
            "MODEL_DOWNLOAD_DISK_SPACE_LOW",
            "not enough free space for a verified Qwen download and publish copy",
            details={"required_free_bytes": required_free, "available_free_bytes": free_bytes},
        )

    stage_parent = root / "models" / ".download-staging"
    stage_parent.mkdir(parents=True, exist_ok=True)
    operation_dir = Path(tempfile.mkdtemp(prefix="qwen-", dir=stage_parent))
    snapshot_dir = operation_dir / "snapshot"
    hub_cache_dir = operation_dir / "hub-cache"
    try:
        if snapshot_download_fn is None:
            from huggingface_hub import snapshot_download

            snapshot_download_fn = snapshot_download
        downloaded_path = Path(
            snapshot_download_fn(
                repo_id=repository,
                revision=revision,
                allow_patterns=names,
                local_dir=str(snapshot_dir),
                cache_dir=str(hub_cache_dir),
                max_workers=4,
            )
        )
        snapshot = downloaded_path.resolve()
        for name in names:
            candidate = (snapshot / name).resolve()
            if not candidate.is_relative_to(snapshot) or not candidate.is_file():
                raise CatalogError(
                    "EMBEDDING_WEIGHTS_MISSING",
                    "the fixed Qwen snapshot is missing a required file",
                    details={"filename": name},
                )
        verification = verify_pinned_files(snapshot, template)
        install_args = SimpleNamespace(
            data_root=root,
            provider=QWEN_PROVIDER_ID,
            source=snapshot,
            revision=revision,
            force=False,
        )
        installed = embedding_cli._prepare_local(install_args)
        return {
            "status": "ok",
            "provider_id": QWEN_PROVIDER_ID,
            "shared_model_dir": str(model_dir),
            "revision": revision,
            "downloaded": True,
            "required_files": list(names),
            "verification": verification,
            "installation": installed,
            "dimensions": [512, 1024],
        }
    except KeyboardInterrupt as error:
        raise CatalogError(
            "MODEL_DOWNLOAD_CANCELLED",
            "Qwen download cancelled; the staged files were discarded and no model was published",
        ) from error
    except CatalogError:
        raise
    except Exception as error:
        raise CatalogError(
            "MODEL_DOWNLOAD_FAILED",
            "the fixed Qwen download did not complete; no model was published",
            details={"exception_type": type(error).__name__},
        ) from error
    finally:
        shutil.rmtree(operation_dir, ignore_errors=True)


__all__ = ["MIN_FREE_RESERVE_BYTES", "QWEN_PROVIDER_ID", "download_qwen"]


def _same_pinned_provider(installed: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Ensure existing data-root metadata cannot redirect shared files or identity."""
    try:
        expected_model = expected["model"]
        installed_model = installed["model"]
        expected_files = [
            (str(item["name"]), int(item["bytes"]), str(item["sha256"]))
            for item in expected.get("weights", [])
        ]
        installed_files = [
            (str(item["name"]), int(item["bytes"]), str(item["sha256"]))
            for item in installed.get("weights", [])
        ]
        return (
            provider_block(installed) == provider_block(expected)
            and str(installed_model["id"]) == str(expected_model["id"])
            and str(installed_model["resolved_revision"])
            == str(expected_model["resolved_revision"])
            and installed_files == expected_files
        )
    except (CatalogError, KeyError, TypeError, ValueError):
        return False
