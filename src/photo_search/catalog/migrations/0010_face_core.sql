-- P3-02 face layer: the current detection facts of every asset, the canonical face vector
-- store, the unnamed person groups with their automatic links, and an independent face
-- candidate index. Additive to 0001..0009; no frozen table, column, key, trigger or index
-- of the earlier migrations is redefined, and no earlier file is touched.
--
-- Column ownership (architecture §4.2, §7.1 and §8.3):
--
-- * ``faces`` is the *authority* of the current detection facts: one row per face of one
--   asset for one detection generation (``spec_revision``), with the orientation-normalised
--   bbox, the five landmarks, the detection score and the quality measures. Re-running the
--   same content with the same generation replaces this asset's rows instead of adding a
--   second set, so "one face" is never counted twice.
-- * ``face_embeddings`` carries the canonical 512-d vector of every face. The vector is a
--   machine product of the pinned weights: it never replaces the picture's own embedding
--   space and never enters ``index_generations``/``index_entries``, whose identity is the
--   *asset* and whose space is the image/text space.
-- * ``persons`` holds the P3-02 unnamed groups (``name``/``name_key`` stay NULL here).
--   Naming, same-name merging and redirects are P3-03; the columns exist because the
--   architecture fixes them, not because this build may write them.
-- * ``face_person_links`` is the one *effective* link per face plus the machine
--   suggestions. A ``SUGGESTED`` row is not an effective assignment (architecture §4.2),
--   which the partial unique index makes a database fact rather than a convention.
-- * ``face_person_links.assignment_kind = 'MANUAL'``, ``face_assignment_controls`` and
--   ``face_exclusions`` belong to P3-03/P3-04. No row of this build is written with
--   ``MANUAL``; the automatic paths read those facts when they exist and refuse to touch a
--   face under human control (that ordering is implemented in ``photo_search.face``).
-- * ``face_index_generations``/``face_index_entries``/``face_index_outbox``/
--   ``face_candidates`` are the *independent* face candidate index: generation-scoped,
--   face-keyed, published outside the authority transaction and confirmed afterwards in
--   SQLite, exactly like the P2-03 image index but in its own namespace.
-- * ``face_association_queue`` is the durable "these facts still need a conservative
--   association pass" set. It is written in the same transaction as the face facts and
--   drained by the reconciliation step, so a crash right after the facts never loses the
--   association work and never silently reports a face as grouped.

CREATE TABLE faces (
    face_id        TEXT PRIMARY KEY
        CHECK(length(face_id) = 64 AND face_id NOT GLOB '*[^0-9a-f]*'),
    asset_id       INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    --: The detection generation: a new detector/preprocessing revision makes new rows
    --: instead of rewriting the old ones, so a matching-based upgrade stays possible.
    spec_revision  TEXT NOT NULL CHECK(spec_revision <> ''),
    detector_id    TEXT NOT NULL CHECK(detector_id <> ''),
    ordinal        INTEGER NOT NULL CHECK(ordinal >= 0),
    --: The content revision this detection was produced for (``assets.sha256``).
    input_revision TEXT NOT NULL
        CHECK(length(input_revision) = 64 AND input_revision NOT GLOB '*[^0-9a-f]*'),
    location_id    INTEGER REFERENCES file_locations(location_id) ON DELETE RESTRICT,
    --: Orientation-normalised source coordinates, so a bbox is always upright.
    bbox_json      TEXT NOT NULL CHECK(json_valid(bbox_json)),
    landmarks_json TEXT NOT NULL CHECK(json_valid(landmarks_json)),
    det_score      REAL NOT NULL CHECK(det_score >= 0.0 AND det_score <= 1.0),
    --: Quality measures of this face, in source pixels / unitless sharpness.
    face_pixels    REAL NOT NULL CHECK(face_pixels >= 0.0),
    blur_variance  REAL NOT NULL CHECK(blur_variance >= 0.0),
    quality_state  TEXT NOT NULL CHECK(quality_state IN ('OK', 'LOW')),
    quality_reason TEXT,
    artifact_id    TEXT NOT NULL CHECK(artifact_id <> ''),
    face_revision  INTEGER NOT NULL CHECK(face_revision >= 1),
    created_at     INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at     INTEGER NOT NULL CHECK(updated_at >= 0),
    --: One ordinal per asset and generation: the direct encoding of "a re-run must not
    --: produce a second set of faces".
    UNIQUE (asset_id, spec_revision, ordinal),
    CHECK(quality_state = 'OK' OR quality_reason IS NOT NULL)
);

CREATE INDEX idx_faces_asset ON faces(asset_id, spec_revision);
CREATE INDEX idx_faces_quality ON faces(spec_revision, quality_state);

CREATE TABLE face_embeddings (
    face_id       TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    processor_id  TEXT NOT NULL CHECK(processor_id <> ''),
    space_id      TEXT NOT NULL CHECK(space_id <> ''),
    dimension     INTEGER NOT NULL CHECK(dimension >= 1),
    metric        TEXT NOT NULL CHECK(metric = 'cosine'),
    normalization TEXT NOT NULL CHECK(normalization = 'L2'),
    --: Little-endian float32 values, ``dimension`` of them; the canonical face vector.
    vector_blob   BLOB NOT NULL,
    vector_sha256 TEXT NOT NULL
        CHECK(length(vector_sha256) = 64 AND vector_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at    INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at    INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY (face_id, processor_id)
);

CREATE TABLE persons (
    person_id      INTEGER PRIMARY KEY,
    name           TEXT CHECK(name IS NULL OR name <> ''),
    name_key       TEXT CHECK(name_key IS NULL OR name_key <> ''),
    avatar_face_id TEXT REFERENCES faces(face_id) ON DELETE RESTRICT,
    --: A merged person points at its target and keeps its id for links and history
    --: (architecture §8.3.3). P3-02 never writes a non-NULL value.
    merged_into    INTEGER REFERENCES persons(person_id) ON DELETE RESTRICT,
    revision       INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at     INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at     INTEGER NOT NULL CHECK(updated_at >= 0),
    CHECK((name IS NULL) = (name_key IS NULL)),
    CHECK(merged_into IS NULL OR merged_into <> person_id)
);

--: Architecture §8.3.1: the active, named persons have a unique name_key; the unnamed
--: groups of this build stay NULL and therefore never collide.
CREATE UNIQUE INDEX ux_persons_active_name_key
    ON persons(name_key) WHERE name_key IS NOT NULL AND merged_into IS NULL;

CREATE TABLE face_person_links (
    face_id         TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    person_id       INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
    assignment_kind TEXT NOT NULL CHECK(assignment_kind IN ('AUTO', 'MANUAL')),
    decision        TEXT NOT NULL CHECK(decision IN ('CONFIRMED', 'SUGGESTED')),
    confidence      REAL NOT NULL CHECK(confidence >= -1.0 AND confidence <= 1.0),
    --: Why a SUGGESTED row exists (borderline similarity, low quality, same-image
    --: conflict, near tie). NULL for a confirmed link.
    reason          TEXT,
    revision        INTEGER NOT NULL CHECK(revision >= 1),
    last_operation_id TEXT,
    created_at      INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at      INTEGER NOT NULL CHECK(updated_at >= 0),
    PRIMARY KEY (face_id, person_id, assignment_kind),
    CHECK((decision = 'SUGGESTED') = (reason IS NOT NULL))
);

--: "每脸最多一个当前有效人物" as a database fact: one face has at most one CONFIRMED link,
--: while any number of SUGGESTED candidates may coexist without being an assignment.
CREATE UNIQUE INDEX ux_face_person_links_effective
    ON face_person_links(face_id) WHERE decision = 'CONFIRMED';
CREATE INDEX idx_face_person_links_person ON face_person_links(person_id, decision);

CREATE TABLE face_association_queue (
    asset_id      INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE CASCADE,
    spec_revision TEXT NOT NULL CHECK(spec_revision <> ''),
    face_revision INTEGER NOT NULL CHECK(face_revision >= 1),
    queued_at     INTEGER NOT NULL CHECK(queued_at >= 0)
);

CREATE INDEX idx_face_association_queue_age
    ON face_association_queue(queued_at, asset_id);

CREATE TABLE face_index_generations (
    generation_id TEXT PRIMARY KEY CHECK(generation_id <> ''),
    space_id      TEXT NOT NULL CHECK(space_id <> ''),
    processor_id  TEXT NOT NULL CHECK(processor_id <> ''),
    spec_revision TEXT NOT NULL CHECK(spec_revision <> ''),
    dimension     INTEGER NOT NULL CHECK(dimension >= 1),
    metric        TEXT NOT NULL CHECK(metric = 'cosine'),
    normalization TEXT NOT NULL CHECK(normalization = 'L2'),
    state         TEXT NOT NULL CHECK(state IN ('PENDING', 'READY', 'RETIRED')),
    is_active     INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
    created_at    INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at    INTEGER NOT NULL CHECK(updated_at >= 0)
);

--: One active generation per face space: a half-switched face index is never readable.
CREATE UNIQUE INDEX ux_face_index_generations_active
    ON face_index_generations(space_id) WHERE is_active = 1;

CREATE TABLE face_index_entries (
    generation_id         TEXT NOT NULL REFERENCES face_index_generations(generation_id)
        ON DELETE RESTRICT,
    face_id               TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL CHECK(artifact_id <> ''),
    confirmation_revision INTEGER NOT NULL CHECK(confirmation_revision >= 0),
    confirmed_at          INTEGER,
    state                 TEXT NOT NULL CHECK(state IN ('PENDING_REPAIR', 'CONFIRMED')),
    PRIMARY KEY (generation_id, face_id),
    CHECK((state = 'CONFIRMED') = (confirmed_at IS NOT NULL))
);

CREATE TABLE face_index_outbox (
    event_id            TEXT PRIMARY KEY CHECK(event_id <> ''),
    event_type          TEXT NOT NULL CHECK(event_type IN ('UPSERT_FACE')),
    face_id             TEXT NOT NULL,
    space_id            TEXT NOT NULL,
    generation_id       TEXT NOT NULL,
    projection_revision INTEGER NOT NULL CHECK(projection_revision >= 0),
    payload_json        TEXT NOT NULL CHECK(json_valid(payload_json)),
    payload_checksum    TEXT NOT NULL
        CHECK(length(payload_checksum) = 64 AND payload_checksum NOT GLOB '*[^0-9a-f]*'),
    state               TEXT NOT NULL CHECK(state IN ('PENDING', 'IN_FLIGHT', 'CONFIRMED')),
    attempts            INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    last_error_code     TEXT,
    created_at          INTEGER NOT NULL CHECK(created_at >= 0),
    updated_at          INTEGER NOT NULL CHECK(updated_at >= 0)
);

CREATE INDEX idx_face_index_outbox_pending
    ON face_index_outbox(state, created_at, event_id);

--: The published candidate rows. Only a CONFIRMED entry may be used as a candidate, and
--: the row carries the vector as it was published, so a candidate search never has to
--: reopen the canonical store or the artifact.
CREATE TABLE face_candidates (
    generation_id   TEXT NOT NULL REFERENCES face_index_generations(generation_id)
        ON DELETE RESTRICT,
    face_id         TEXT NOT NULL REFERENCES faces(face_id) ON DELETE RESTRICT,
    is_representative INTEGER NOT NULL CHECK(is_representative IN (0, 1)),
    quality_state   TEXT NOT NULL CHECK(quality_state IN ('OK', 'LOW')),
    vector_blob     BLOB NOT NULL,
    vector_sha256   TEXT NOT NULL
        CHECK(length(vector_sha256) = 64 AND vector_sha256 NOT GLOB '*[^0-9a-f]*'),
    published_at    INTEGER NOT NULL CHECK(published_at >= 0),
    PRIMARY KEY (generation_id, face_id)
);

CREATE INDEX idx_face_candidates_representative
    ON face_candidates(generation_id, is_representative);
