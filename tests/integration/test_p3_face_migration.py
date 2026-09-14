"""P3-02: the 0010 step of the migration chain, on a real populated v9 database."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from photo_search.catalog import schema
from photo_search.catalog.errors import (
    MIGRATION_FAILED,
    SCHEMA_INCONSISTENT,
    SCHEMA_TOO_NEW,
    CatalogError,
)
from photo_search.catalog.migrate import inspect_database, migrate_database
from photo_search.catalog.sqlite_io import connect_rw, open_readonly

REAL_MIGRATIONS_DIR = Path(schema.__file__).resolve().parent / "migrations"
SUPPORTED = int(schema.SUPPORTED_SCHEMA_VERSION)
#: This file is about the 0010 step itself. Pinning the target keeps "v9 -> v10" meaning the
#: same thing after later additive migrations append to the chain.
V10 = 10

FACE_TABLES = {
    "faces",
    "face_embeddings",
    "persons",
    "face_person_links",
    "face_association_queue",
    "face_index_generations",
    "face_index_entries",
    "face_index_outbox",
    "face_candidates",
}

FROZEN_V9_ROW_SQL = (
    "SELECT asset_id, sha256, byte_size, created_at, state FROM assets ORDER BY asset_id",
    "SELECT location_id, relative_path, availability, asset_id FROM file_locations "
    "ORDER BY location_id",
    "SELECT asset_id, filename_text, ocr_text, place_text, tokenizer_version, fts_generation_id "
    "FROM search_documents ORDER BY asset_id",
    "SELECT asset_id, cause, queued_at FROM search_projection_queue ORDER BY asset_id",
    # The P3-01 layer is the most recent thing 0010 must not disturb.
    "SELECT asset_id, ocr_document_id, input_revision, region_count, search_text, tokenizer_version "
    "FROM asset_ocr_text ORDER BY asset_id",
    "SELECT asset_id, region_index, text_revision, box_json, confidence FROM asset_ocr_regions "
    "ORDER BY asset_id, region_index",
    "SELECT generation_id, space_id, processor_id, dimension, state, is_active FROM index_generations "
    "ORDER BY generation_id",
    "SELECT space_id, asset_id, generation_id, artifact_id, state FROM index_entries "
    "ORDER BY space_id, asset_id",
    # The version key is the one row a migration is allowed to change.
    "SELECT key, value FROM metadata WHERE key <> 'schema_version' ORDER BY key",
)


def data_root(tmp_path: Path) -> Path:
    return tmp_path / "PhotoSearchData"


def db_path(tmp_path: Path) -> Path:
    return data_root(tmp_path) / "catalog" / "catalog.db"


def migrations_upto(tmp_path: Path, version: int) -> Path:
    directory = tmp_path / f"migrations-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    for number, source in schema.migration_files():
        if number <= int(version):
            shutil.copy2(source, directory / source.name)
    return directory


def build_v9(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a real, populated v9 database using only the frozen 0001..0009 scripts."""
    path = db_path(tmp_path)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, 9))
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", 9)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO source_roots(display_name, root_path, volume_hint, status) "
            "VALUES ('照片 库', 'C:\\photos 中文', 'C:', 'ONLINE')"
        )
        connection.execute(
            "INSERT INTO assets(sha256, byte_size, created_at, state) VALUES (?, 4096, 1000, 'ACTIVE')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id, relative_path, path_key, asset_id, size, "
            "mtime_ns, last_seen_scan_id, availability) "
            "VALUES (1, '2024 年/发票 一.jpg', '2024 年/发票 一.jpg', 1, 4096, 7, NULL, 'PRESENT')"
        )
        connection.execute(
            "INSERT INTO search_documents(asset_id, filename_text, user_text, ocr_text, "
            "caption_text, tag_text, place_text, source_fingerprint, tokenizer_version, "
            "fts_generation_id, updated_at) VALUES (1, '发 票 一 jpg', '', '', '', '', '', ?, 1, "
            "'fts-v1', 1500)",
            (hashlib.sha256(b"frozen-v9").hexdigest(),),
        )
        connection.execute(
            "INSERT INTO asset_ocr_text(asset_id, ocr_document_id, input_revision, location_id, "
            "text_revision, processor_id, spec_revision, model_fingerprint, artifact_id, "
            "region_count, min_confidence, mean_confidence, max_confidence, search_text, "
            "tokenizer_version, created_at, updated_at) "
            "VALUES (1, 'ocr:1', ?, 1, 1, 'ocr-text@1', 'rev', ?, 'artifact', 1, 0.9, 0.9, 0.9, "
            "'发 票', 1, 1600, 1600)",
            ("a" * 64, "b" * 64),
        )
        connection.execute(
            "INSERT INTO asset_ocr_regions(asset_id, region_index, text_revision, box_json, "
            "confidence) VALUES (1, 0, 1, '[1,2,3,4]', 0.9)"
        )
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id, cause, queued_at) VALUES (1, "
            "'OCR_TEXT_CHANGED', 1234) ON CONFLICT(asset_id) DO UPDATE SET "
            "cause = excluded.cause, queued_at = excluded.queued_at"
        )
        connection.execute(
            "INSERT INTO index_generations(generation_id, space_id, processor_id, dimension, "
            "state, is_active, created_at, updated_at) VALUES ('image-gen-1', 'clip:1', "
            "'clip@1', 512, 'READY', 1, 1700, 1700)"
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)
    return path


def snapshot(path: Path) -> dict[str, list[tuple]]:
    connection = open_readonly(path)
    try:
        return {
            f"q{index}": [tuple(row) for row in connection.execute(sql)]
            for index, sql in enumerate(FROZEN_V9_ROW_SQL)
        }
    finally:
        connection.close()


def test_migration_scripts_are_continuous_and_0010_is_the_face_layer() -> None:
    versions = [number for number, _ in schema.migration_files()]
    assert versions == list(range(1, SUPPORTED + 1))
    assert versions[9] == 10
    assert (REAL_MIGRATIONS_DIR / "0010_face_core.sql").is_file()
    assert SUPPORTED == 10


def test_v9_to_v10_is_continuous_backed_up_and_lossless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v9(tmp_path, monkeypatch)
    assert inspect_database(path) == 9
    before = snapshot(path)
    before_bytes = path.read_bytes()

    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V10)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", migrations_upto(tmp_path, V10))
    result = migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert result.previous_version == 9 and result.schema_version == V10
    assert result.applied == (10,)
    assert result.backup_path is not None
    backup = open_readonly(result.backup_path)
    try:
        assert schema.read_schema_version(backup) == 9
        assert FACE_TABLES.isdisjoint(schema.business_tables(backup))
    finally:
        backup.close()

    live = open_readonly(path)
    try:
        assert schema.read_schema_version(live) == V10
        assert FACE_TABLES <= set(schema.business_tables(live))
        schema.verify_schema(live, V10)
        # The face layer starts empty: nothing is fabricated for the existing assets.
        for table in sorted(FACE_TABLES):
            assert live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        live.close()
    assert snapshot(path) == before
    assert path.read_bytes() != before_bytes

    again = migrate_database(path, data_root=data_root(tmp_path))
    assert again.applied == tuple(range(V10 + 1, SUPPORTED + 1))
    assert again.previous_version == V10


def test_the_face_tables_enforce_the_p3_02_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v9(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")

        def add_face(face_id: str, ordinal: int, *, input_revision: str = "a" * 64) -> None:
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
                "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
                "created_at, updated_at) VALUES (?, 1, 'rev', 'det', ?, ?, 1, '[0,0,10,10]', "
                "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 60.0, 200.0, ?, ?, 'artifact', 1, 1, 1)",
                (
                    face_id,
                    ordinal,
                    input_revision,
                    "OK",
                    None,
                ),
            )

        add_face("f" * 64, 0)
        # The same generation cannot describe two different faces at one ordinal.
        with pytest.raises(Exception):
            add_face("e" * 64, 0)
        # A face_id must be a lowercase sha256-shaped identifier.
        with pytest.raises(Exception):
            add_face("NOT-A-SHA", 1)
        # A low-quality face has to say why.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
                "input_revision, bbox_json, landmarks_json, det_score, face_pixels, "
                "blur_variance, quality_state, artifact_id, face_revision, created_at, updated_at) "
                "VALUES (?, 1, 'rev', 'det', 5, ?, '[0,0,10,10]', '[]', 0.5, 10.0, 1.0, 'LOW', "
                "'a', 1, 1, 1)",
                ("d" * 64, "a" * 64),
            )

        connection.execute(
            "INSERT INTO face_embeddings(face_id, processor_id, space_id, dimension, metric, "
            "normalization, vector_blob, vector_sha256, created_at, updated_at) "
            "VALUES (?, 'face-model@1', 'face:1', 512, 'cosine', 'L2', x'00', ?, 1, 1)",
            ("f" * 64, "c" * 64),
        )
        # A vector of a foreign space or a non-cosine metric is refused.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_embeddings(face_id, processor_id, space_id, dimension, metric, "
                "normalization, vector_blob, vector_sha256, created_at, updated_at) "
                "VALUES (?, 'other', 'face:2', 512, 'l2', 'L2', x'00', ?, 1, 1)",
                ("f" * 64, "c" * 64),
            )
        for person_id in (7, 8, 9):
            connection.execute(
                "INSERT INTO persons(person_id, revision, created_at, updated_at) "
                "VALUES (?, 1, 1, 1)",
                (person_id,),
            )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, created_at, updated_at) "
            "VALUES (?, 7, 'AUTO', 'SUGGESTED', 0.31, 'BORDERLINE', 1, 1, 1)",
            ("f" * 64,),
        )
        # A suggestion without a reason is not a suggestion, and a confirmed link must not
        # carry one.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, revision, created_at, updated_at) "
                "VALUES (?, 7, 'AUTO', 'SUGGESTED', 0.31, 1, 1, 1)",
                ("f" * 64,),
            )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, revision, created_at, updated_at) "
            "VALUES (?, 8, 'AUTO', 'CONFIRMED', 0.9, 1, 1, 1)",
            ("f" * 64,),
        )
        # "每脸最多一个当前有效人物": a second *effective* link for the same face is refused by
        # the partial unique index, while the suggestion row above stays as evidence.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
                "confidence, revision, created_at, updated_at) "
                "VALUES (?, 9, 'AUTO', 'CONFIRMED', 0.9, 1, 1, 1)",
                ("f" * 64,),
            )
        # A person cannot be merged into itself, and an unnamed group may not carry a key.
        with pytest.raises(Exception):
            connection.execute("UPDATE persons SET merged_into = 7 WHERE person_id = 7")
        with pytest.raises(Exception):
            connection.execute("UPDATE persons SET name_key = 'x' WHERE person_id = 7")
        # The face rows are referenced, so they cannot simply be deleted.
        with pytest.raises(Exception):
            connection.execute("DELETE FROM faces WHERE face_id = ?", ("f" * 64,))
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_the_independent_face_index_keeps_its_own_generation_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v9(tmp_path, monkeypatch)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO face_index_generations(generation_id, space_id, processor_id, "
            "spec_revision, dimension, metric, normalization, state, is_active, created_at, "
            "updated_at) VALUES ('face-gen-1', 'face:1', 'face-model@1', 'rev', 512, 'cosine', "
            "'L2', 'PENDING', 0, 1, 1)"
        )
        connection.execute(
            "INSERT INTO face_index_generations(generation_id, space_id, processor_id, "
            "spec_revision, dimension, metric, normalization, state, is_active, created_at, "
            "updated_at) VALUES ('face-gen-2', 'face:1', 'face-model@1', 'rev', 512, 'cosine', "
            "'L2', 'PENDING', 1, 1, 1)"
        )
        # One active generation per face space: a half-switched index is never readable.
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO face_index_generations(generation_id, space_id, processor_id, "
                "spec_revision, dimension, metric, normalization, state, is_active, created_at, "
                "updated_at) VALUES ('face-gen-3', 'face:1', 'face-model@1', 'rev', 512, "
                "'cosine', 'L2', 'PENDING', 1, 1, 1)"
            )
        connection.execute(
            "INSERT INTO face_index_outbox(event_id, event_type, face_id, space_id, "
            "generation_id, projection_revision, payload_json, payload_checksum, state, attempts, "
            "created_at, updated_at) VALUES ('e1', 'UPSERT_FACE', ?, 'face:1', 'face-gen-1', 1, "
            "'{}', ?, 'PENDING', 0, 1, 1)",
            ("f" * 64, "d" * 64),
        )
        # The image index is untouched: the face index is a separate namespace.
        image_generations = connection.execute(
            "SELECT COUNT(*) FROM index_generations WHERE space_id = 'clip:1'"
        ).fetchone()[0]
        assert image_generations == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM index_entries WHERE generation_id LIKE 'face-gen%'"
        ).fetchone()[0] == 0
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_an_interrupted_0010_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = build_v9(tmp_path, monkeypatch)
    before = snapshot(path)
    directory = migrations_upto(tmp_path, V10)
    broken = directory / "0010_face_core.sql"
    broken.write_text(
        broken.read_text(encoding="utf-8")
        + "\nSELECT * FROM table_that_does_not_exist;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", V10)
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", directory)

    with pytest.raises(CatalogError) as error:
        migrate_database(path, data_root=data_root(tmp_path))
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", REAL_MIGRATIONS_DIR)
    monkeypatch.setattr(schema, "SUPPORTED_SCHEMA_VERSION", SUPPORTED)

    assert error.value.code == MIGRATION_FAILED
    assert inspect_database(path) == 9
    assert snapshot(path) == before
    connection = open_readonly(path)
    try:
        assert FACE_TABLES.isdisjoint(schema.business_tables(connection))
    finally:
        connection.close()


def test_a_missing_face_table_is_inconsistent_and_a_newer_schema_is_refused(
    tmp_path: Path,
) -> None:
    path = db_path(tmp_path)
    migrate_database(path, data_root=data_root(tmp_path))
    connection = connect_rw(path)
    connection.execute("DROP TABLE face_embeddings")
    connection.close()
    with pytest.raises(CatalogError) as error:
        inspect_database(path)
    assert error.value.code == SCHEMA_INCONSISTENT
    assert "face_embeddings" in error.value.details["missing_objects"][0]

    connection = connect_rw(path)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SUPPORTED + 1),),
    )
    connection.close()
    before = path.read_bytes()
    with pytest.raises(CatalogError) as too_new:
        migrate_database(path, data_root=data_root(tmp_path))
    assert too_new.value.code == SCHEMA_TOO_NEW
    assert path.read_bytes() == before
