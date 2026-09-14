-- P2-06A search profiles: the immutable query configuration (ADR-0003 §8) and the single
-- active pointer (architecture §11.3.5). Additive to 0001..0006; no frozen table, column,
-- key or trigger is touched.
--
-- The column set is the frozen ``$defs/SearchProfile`` document field by field. The two
-- deliberate spellings the companion schema cannot express are:
--   * ``fusion_config`` is stored as ``fusion_config_json`` (TEXT) so SQLite can enforce
--     ``json_valid`` plus the required-key shape;
--   * ``fts_tokenizer_version`` is a *string* in the contract while
--     ``search_documents.tokenizer_version`` is an integer. The frozen schema wins: the
--     DDL below stores the string and the service maps it to the integer generation
--     (``"1"`` -> 1) with a structured refusal when no mapping exists.

CREATE TABLE search_profiles (
    profile_id               TEXT PRIMARY KEY CHECK(profile_id <> ''),
    embedding_processor_id   TEXT NOT NULL CHECK(embedding_processor_id <> ''),
    space_id                 TEXT NOT NULL CHECK(space_id <> ''),
    image_generation_id      TEXT NOT NULL
        REFERENCES index_generations(generation_id) ON DELETE RESTRICT,
    query_preprocess_version TEXT NOT NULL CHECK(query_preprocess_version <> ''),
    query_parser_version     TEXT NOT NULL CHECK(query_parser_version <> ''),
    fts_tokenizer_version    TEXT NOT NULL CHECK(fts_tokenizer_version <> ''),
    fts_generation_id        TEXT NOT NULL CHECK(fts_generation_id <> ''),
    reranker_processor_id    TEXT
        CHECK(reranker_processor_id IS NULL OR reranker_processor_id <> ''),
    fusion_config_json       TEXT NOT NULL CHECK(
        CASE WHEN json_valid(fusion_config_json) THEN
            (json_type(fusion_config_json) = 'object'
             -- ``IS`` rather than ``=``: a missing key makes ``json_type`` return NULL, and
             -- a CHECK that evaluates to NULL would *pass*. The closed shape must fail.
             AND json_type(fusion_config_json, '$.revision') IS 'text'
             AND json_type(fusion_config_json, '$.method') IS 'text'
             AND json_type(fusion_config_json, '$.parameters') IS 'object')
        ELSE 0 END),
    runtime_package_id       TEXT NOT NULL CHECK(runtime_package_id <> ''),
    created_at               INTEGER NOT NULL CHECK(created_at >= 0)
);

-- ADR-0003 §8: "SearchProfile 创建后不可变". A changed configuration is published as a new
-- profile_id, so every content column (and created_at) is write-once and the only legal
-- statement against this table is INSERT. A physical delete stays possible only while
-- nothing references the row (the two RESTRICT foreign keys below and in 0003 do the
-- protection), which keeps a half-written migration recoverable.
CREATE TRIGGER trg_search_profiles_immutable
BEFORE UPDATE ON search_profiles
BEGIN
    SELECT RAISE(ABORT, 'search_profiles rows are immutable; publish a new profile_id');
END;

CREATE TABLE active_search_profile (
    state_id   INTEGER PRIMARY KEY CHECK(state_id = 1),
    profile_id TEXT REFERENCES search_profiles(profile_id) ON DELETE RESTRICT,
    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
);

-- The single pointer starts empty: with no activated profile the protocol requires the
-- caller to bind the generation explicitly (the P2-05 path), and no configuration is
-- silently presented as active.
INSERT INTO active_search_profile(state_id, profile_id, updated_at) VALUES (1, NULL, 0);
