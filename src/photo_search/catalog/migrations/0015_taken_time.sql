-- P4-00B capture-time layer (ADR-0011). One additive table holds the *derived* taken-time
-- fact of an asset, plus one new library-level rule version key. Nothing frozen is touched:
-- ``assets``, ``file_locations`` (the observation facts: ``size``/``mtime_ns``/
-- ``availability``), ``asset_geo_facts`` (the GPS authority of ADR-0005 §4.5) and every
-- P1/P2/P3 table keep their exact shape, and this migration backfills nothing.
--
-- Column contract (ADR-0011 §2, primary key + six columns, no more and no fewer):
--
-- * ``taken_at_raw`` is the *internal* evidence text (the EXIF spelling, or the UTC ISO
--   rendering of the chosen ``mtime_ns``). It never enters a response, an error, a log or
--   a URL; ADR-0011 §8.4 freezes that boundary.
-- * ``taken_at_sort`` is the non-negative *civil* key of ADR-0011 §3.1 - the wall-clock
--   fields read as if they were UTC. It is NOT an instant, and the recorded
--   ``timezone_offset`` never participates in its computation.
-- * ``time_source``/``time_confidence`` are the closed vocabularies of ADR-0011 §4.1, and
--   the table-level CHECK admits exactly the five frozen pairs - ``EXIF_ORIGINAL`` may be
--   ``HIGH`` (offset recorded) or ``MEDIUM`` (offset missing/invalid).
-- * ``taken_at_sort`` is NULL exactly for the ``FILE_MTIME`` fallback, whose truth is
--   "this asset has no usable capture time"; every EXIF family row must carry a civil key.
-- * ``timezone_offset`` is minutes east of UTC (-720..840) and only ever non-NULL for an
--   EXIF family source; ``FILE_MTIME`` is always NULL.
-- * ``camera`` is the normalized ``Make Model`` display string of ADR-0011 §8.4 (1..128
--   characters) and is served by ``GET /api/v1/assets/{id}`` only.
--
-- A row exists iff the asset has been processed by the taken-time extraction; a row whose
-- ``time_source`` is ``FILE_MTIME`` is the honest "no capture time" answer. Rows are
-- written *only* by that extraction path, in its own transaction, never by ``catalog scan``
-- and never by a read path.

CREATE TABLE asset_metadata (
    asset_id          INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
    taken_at_raw      TEXT,
    taken_at_sort     INTEGER CHECK(
        taken_at_sort IS NULL
        OR (taken_at_sort >= 0 AND taken_at_sort <= 315537897599999)),
    time_source       TEXT NOT NULL CHECK(time_source IN (
        'EXIF_ORIGINAL', 'EXIF_DIGITIZED', 'EXIF_ROOT', 'FILE_MTIME')),
    time_confidence   TEXT NOT NULL CHECK(time_confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    timezone_offset   INTEGER CHECK(
        timezone_offset IS NULL OR (timezone_offset BETWEEN -720 AND 840)),
    camera            TEXT CHECK(camera IS NULL OR length(camera) BETWEEN 1 AND 128),
    -- The five frozen ``(time_source, time_confidence)`` pairs of ADR-0011 §4.1. Written as
    -- an OR of equalities because SQLite forbids a row-value IN list in a CHECK constraint
    -- ("subqueries prohibited in CHECK constraints"); the accepted set is exactly the five.
    CHECK(
        (time_source = 'EXIF_ORIGINAL' AND time_confidence IN ('HIGH', 'MEDIUM'))
        OR (time_source = 'EXIF_DIGITIZED' AND time_confidence = 'MEDIUM')
        OR (time_source = 'EXIF_ROOT' AND time_confidence = 'LOW')
        OR (time_source = 'FILE_MTIME' AND time_confidence = 'LOW')),
    -- ``taken_at_sort`` is NULL exactly for the FILE_MTIME fallback.
    CHECK((time_source = 'FILE_MTIME') = (taken_at_sort IS NULL)),
    -- A file time never claims a timezone.
    CHECK(time_source <> 'FILE_MTIME' OR timezone_offset IS NULL)
);

-- One index serves both the ``capture_desc`` ordering and the capture range condition: the
-- row set is always "the capture/family sources with their civil key", ordered by key.
CREATE INDEX idx_asset_metadata_capture
    ON asset_metadata(time_source, taken_at_sort, asset_id);

-- ADR-0011 §2: the library-level rule version. A build whose rules differ from this stored
-- value must not trust (or filter on) the rows until the extraction has run again; the
-- status block of ADR-0011 §8.1 normalizes its counts to zero in that case.
INSERT INTO metadata(key, value) VALUES ('taken_time_rules_version', '1');
