"""P2-03 artifact payload, controlled-path and cleanup contracts."""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from photo_search.artifacts.store import ArtifactStore
from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import ARTIFACT_INVALID, CatalogError
from photo_search.scheduler.contracts import ArtifactRef, canonical_json


def artifact(
    kind: str,
    payload: dict,
    *,
    space_id: str | None = None,
    dimension: int | None = None,
    data: bytes | None = None,
) -> ArtifactRef:
    content = data if data is not None else canonical_json(payload).encode("utf-8")
    return ArtifactRef(
        artifact_id=f"{kind}-artifact",
        artifact_type=kind,
        artifact_revision=f"{kind}.v1",
        media_type="application/octet-stream" if kind == "binary" else "application/json",
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        storage_uri=None,
        payload=payload,
        space_id=space_id,
        dimension=dimension,
    )


def test_structured_binary_and_embedding_payloads_stage_under_the_controlled_root(
    tmp_path: Path,
) -> None:
    store = ArtifactStore.open(tmp_path / "PhotoSearchData")
    binary = b"\x00\x01binary"
    refs = [
        artifact("structured", {"state": "ABSENT"}),
        artifact("binary", {"base64": base64.b64encode(binary).decode()}, data=binary),
        artifact(
            "embedding",
            {"vector": [0.1, 0.2, 0.3]},
            space_id="space-a",
            dimension=3,
        ),
    ]

    prepared = [store.prepare(ref) for ref in refs]
    try:
        assert all(item.temporary and item.temporary.is_file() for item in prepared)
        assert all(item.target.is_relative_to(store.root) for item in prepared)
        assert all(not Path(item.storage_uri).is_absolute() for item in prepared)
    finally:
        store.discard(prepared)


@pytest.mark.parametrize("field", ["checksum", "size"])
def test_checksum_or_size_mismatch_is_rejected_without_a_staged_file(
    tmp_path: Path, field: str
) -> None:
    store = ArtifactStore.open(tmp_path / "PhotoSearchData")
    ref = artifact("structured", {"ok": True})
    values = ref.__dict__.copy()
    values["checksum_sha256" if field == "checksum" else "byte_size"] = (
        "f" * 64 if field == "checksum" else ref.byte_size + 1
    )

    with pytest.raises(CatalogError) as failure:
        store.prepare(ArtifactRef(**values))

    assert failure.value.code == ARTIFACT_INVALID
    assert not list(store.root.rglob("*.tmp")) if store.root.exists() else True


def test_worker_chosen_storage_uri_is_rejected(tmp_path: Path) -> None:
    store = ArtifactStore.open(tmp_path / "PhotoSearchData")
    ref = artifact("structured", {"ok": True})
    values = {**ref.__dict__, "storage_uri": "C:/private/result.json"}
    with pytest.raises(CatalogError) as failure:
        store.prepare(ArtifactRef(**values))
    assert failure.value.code == ARTIFACT_INVALID


def test_temporary_cleanup_respects_the_grace_period(tmp_path: Path) -> None:
    store = ArtifactStore.open(tmp_path / "PhotoSearchData")
    prepared = store.prepare(artifact("structured", {"ok": True}))
    temporary = prepared.temporary
    assert temporary is not None
    assert store.prune_temporary(min_age_ms=10_000, now_ms=20_000) == []
    os.utime(temporary, (0, 0))
    assert store.prune_temporary(min_age_ms=10_000, now_ms=20_000) == [temporary]


def test_v3_sqlite_constraints_reject_invalid_artifacts_and_two_active_generations(
    tmp_path: Path,
) -> None:
    catalog = Catalog.open(tmp_path / "PhotoSearchData" / "catalog" / "catalog.db")
    try:
        with catalog.write() as connection:
            connection.execute(
                "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
                "state, is_active, created_at, updated_at) VALUES ('g1', 's1', 'p1', 3, 'READY', 1, 0, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
                    "state, is_active, created_at, updated_at) "
                    "VALUES ('g2', 's1', 'p2', 3, 'READY', 1, 0, 0)"
                )
        with pytest.raises(sqlite3.IntegrityError):
            with catalog.write() as connection:
                connection.execute(
                    "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
                    "state, is_active, created_at, updated_at) "
                    "VALUES ('bad', 's2', 'p2', 0, 'PENDING', 0, 0, 0)"
                )
    finally:
        catalog.close()
