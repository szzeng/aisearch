"""P4-02Y §2/§3/§10: the fast refresh's scan half - reuse, re-hash, deltas, batching.

Everything runs on ``%TEMP%`` fixtures through the production scanner and catalog; the only
stand-in is the deterministic clock. These are the §14 items 1-10 and 13-14 on the scan
surface; the derived stages and the HTTP entry live in
``test_p4_02y_library_refresh.py``.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from p2_search_fixtures import WALL_BASE_MS, build_library, stamp, write_jpeg
from PIL import Image

from photo_search.catalog.errors import PATH_KEY_COLLISION, CatalogError
from photo_search.ingestion.scanner import (
    SCAN_MODE_FAST,
    SCAN_MODE_FULL,
    FileObservation,
    ScanInterrupted,
    execute_scan,
)

NAMES = ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG")


@pytest.fixture()
def library(tmp_path: Path):
    value = build_library(tmp_path, NAMES)
    try:
        yield value
    finally:
        value.close()


class Result:
    """The scan outcome as tests read it: counters by name, routing ids as sets."""

    def __init__(self, outcome) -> None:
        self.data = outcome.as_dict()
        self.content_delta, self.location_delta = outcome.deltas()

    def __getitem__(self, key: str):
        return self.data[key]

    @property
    def content(self) -> set[int]:
        return set(self.content_delta)

    @property
    def locations(self) -> set[int]:
        return set(self.location_delta)


def scan(library, *, mode: str = SCAN_MODE_FAST, batch: int | None = None, **options) -> Result:
    if batch is not None:
        options["observation_batch"] = batch
    return Result(execute_scan(library.service, library.source_id, mode=mode, **options))


def full(library, **options) -> Result:
    return scan(library, mode=SCAN_MODE_FULL, **options)


def location_rows(library) -> list[tuple]:
    return [
        (
            int(row["location_id"]),
            str(row["relative_path"]),
            row["asset_id"],
            int(row["size"]),
            int(row["mtime_ns"]),
            str(row["availability"]),
        )
        for row in library.catalog.read_all(
            "SELECT location_id, relative_path, asset_id, size, mtime_ns, availability "
            "FROM file_locations ORDER BY location_id"
        )
    ]


def asset_rows(library) -> list[tuple]:
    return [
        (int(row["asset_id"]), str(row["sha256"]), int(row["byte_size"]))
        for row in library.catalog.read_all(
            "SELECT asset_id, sha256, byte_size FROM assets ORDER BY asset_id"
        )
    ]


def location_of(library, relative_path: str):
    return library.catalog.read_one(
        "SELECT location_id, asset_id, availability FROM file_locations WHERE relative_path = ?",
        (relative_path,),
    )


def sha_of(library, asset_id: int) -> str:
    return str(
        library.catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))[
            "sha256"
        ]
    )


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ §14.1 no-op refresh
def test_fast_no_op_reuses_every_confirmed_binding_without_reading_originals(library) -> None:
    first = full(library)
    assert first["hash_targets"] == 3
    locations_before = location_rows(library)
    assets_before = asset_rows(library)

    outcome = scan(library)

    assert outcome["state"] == "SUCCEEDED"
    assert outcome["mode"] == SCAN_MODE_FAST
    assert outcome["enumerated"] == 3
    assert outcome["files_seen"] == 3
    assert outcome["unchanged_reused"] == 3
    assert outcome["hash_targets"] == 0
    assert outcome["hash_bytes"] == 0
    assert outcome["content_confirmed"] == 0
    assert outcome["new_assets"] == 0
    assert outcome["rebound_assets"] == 0
    assert outcome["content_delta_count"] == 0
    assert outcome["location_delta_count"] == 0
    assert outcome["delta_overflow"] is False
    assert outcome.content == set()
    assert outcome.locations == set()
    assert location_rows(library) == locations_before
    assert asset_rows(library) == assets_before


def test_fast_refresh_keeps_the_frozen_success_contract(library) -> None:
    outcome = scan(library)
    assert outcome["locations_pending_content"] == 0
    assert outcome["locations_missing"] == 0
    assert library.service.counts(source_id=library.source_id)["locations_pending_content"] == 0
    scan_state = library.catalog.read_one(
        "SELECT state FROM scan_runs ORDER BY scan_id DESC LIMIT 1"
    )
    assert str(scan_state["state"]) == "SUCCEEDED"
    assert library.service.get_source(library.source_id)["last_successful_scan"] is not None


# ------------------------------------------------------------- §14.2 a genuinely new file
def test_fast_refresh_hashes_only_the_new_file_and_routes_it_as_content_delta(library) -> None:
    full(library)
    fresh = write_jpeg(library.root / "new arrival.jpg", index=99)

    outcome = scan(library)

    assert outcome["enumerated"] == 4
    assert outcome["unchanged_reused"] == 3
    assert outcome["hash_targets"] == 1
    assert outcome["hash_bytes"] == fresh.stat().st_size
    assert outcome["new_assets"] == 1
    assert outcome["content_delta_count"] == 1
    assert outcome["location_delta_count"] == 1
    new_asset = int(location_of(library, "new arrival.jpg")["asset_id"])
    assert outcome.content == {new_asset}
    assert outcome.locations == {new_asset}
    assert sha_of(library, new_asset) == digest_of(fresh)


# --------------------------------------------------------- §14.3 changed size or mtime
def test_fast_refresh_rehashes_a_changed_size(library) -> None:
    full(library)
    target = library.root / "beach sunset.jpg"
    old_asset = int(location_of(library, "beach sunset.jpg")["asset_id"])
    before = target.stat()
    image = Image.new("RGB", (48, 36), (10, 20, 30))
    image.save(target, "JPEG", quality=90)
    stamp(target)
    replaced = target.stat()
    assert replaced.st_size != before.st_size

    outcome = scan(library)

    assert outcome["hash_targets"] == 1
    assert outcome["hash_bytes"] == replaced.st_size
    assert outcome["unchanged_reused"] == 2
    assert outcome["new_assets"] == 1
    row = location_of(library, "beach sunset.jpg")
    assert int(row["asset_id"]) != old_asset
    assert sha_of(library, int(row["asset_id"])) == digest_of(target)
    assert outcome.content == {int(row["asset_id"])}


def test_fast_refresh_rehashes_a_changed_mtime_only(library) -> None:
    """Identical bytes with a newer ``mtime`` are a freshness hint that cannot be reused."""
    full(library)
    target = library.root / "beach sunset.jpg"
    original = target.read_bytes()
    old_asset = int(location_of(library, "beach sunset.jpg")["asset_id"])
    os.utime(target, ns=((WALL_BASE_MS - 7_200_000) * 1_000_000,) * 2)

    outcome = scan(library)

    assert outcome["hash_targets"] == 1
    assert outcome["hash_bytes"] == len(original)
    assert outcome["new_assets"] == 0
    assert outcome["rebound_assets"] == 1
    row = location_of(library, "beach sunset.jpg")
    assert int(row["asset_id"]) == old_asset
    assert sha_of(library, old_asset) == digest_of(target)
    assert outcome.content == {old_asset}


# ------------------------------------------------------- §14.4 MISSING -> PRESENT recovery
def test_fast_refresh_reconfirms_a_recovered_position(library) -> None:
    full(library)
    target = library.root / "IMG_0001.JPG"
    original = target.read_bytes()
    moment = target.stat().st_mtime_ns
    target.unlink()

    missing = scan(library)
    assert missing["locations_missing"] == 1
    assert missing["hash_targets"] == 0
    assert missing["location_delta_count"] == 1
    row = location_of(library, "IMG_0001.JPG")
    assert str(row["availability"]) == "MISSING"
    kept_asset = int(row["asset_id"])
    assert missing.locations == {kept_asset}

    target.write_bytes(original)
    os.utime(target, ns=(moment, moment))
    recovered = scan(library)

    assert recovered["hash_targets"] == 1
    assert recovered["hash_bytes"] == len(original)
    assert recovered["locations_missing"] == 0
    assert recovered["rebound_assets"] == 1
    after = location_of(library, "IMG_0001.JPG")
    assert str(after["availability"]) == "PRESENT"
    assert int(after["asset_id"]) == kept_asset
    assert recovered.content == {kept_asset}


# ------------------------------------------------------ §14.5 a duplicate copy of content
def test_fast_refresh_binds_a_duplicate_copy_to_the_existing_asset(library) -> None:
    full(library)
    assets_before = len(asset_rows(library))
    source = library.root / "beach sunset.jpg"
    copy = library.root / "copy of beach sunset.jpg"
    copy.write_bytes(source.read_bytes())
    stamp(copy)
    source_asset = int(location_of(library, "beach sunset.jpg")["asset_id"])

    outcome = scan(library)

    assert outcome["hash_targets"] == 1
    assert outcome["new_assets"] == 0
    assert outcome["rebound_assets"] == 1
    assert len(asset_rows(library)) == assets_before
    assert int(location_of(library, "copy of beach sunset.jpg")["asset_id"]) == source_asset
    assert outcome.content == {source_asset}
    assert outcome.locations == {source_asset}


# ------------------------------------------------------------- §14.6 rename and move
def test_fast_refresh_keeps_the_asset_across_a_rename_and_reports_both_truths(library) -> None:
    full(library)
    moved_asset = int(location_of(library, "杭州西湖 照片.jpg")["asset_id"])
    os.rename(library.root / "杭州西湖 照片.jpg", library.root / "renamed lake.jpg")

    outcome = scan(library)

    # A new path is a new position: it is hashed and then bound to the *same* asset, while
    # the vanished old spelling becomes the MISSING one. No new asset, no new content.
    assert outcome["hash_targets"] == 1
    assert outcome["new_assets"] == 0
    assert outcome["rebound_assets"] == 1
    assert outcome["locations_missing"] == 1
    assert int(location_of(library, "renamed lake.jpg")["asset_id"]) == moved_asset
    assert str(location_of(library, "杭州西湖 照片.jpg")["availability"]) == "MISSING"
    assert outcome.content == {moved_asset}
    assert outcome.locations == {moved_asset}


def test_fast_refresh_moves_a_file_into_a_new_directory_without_new_content(library) -> None:
    full(library)
    moved_asset = int(location_of(library, "IMG_0001.JPG")["asset_id"])
    nested = library.root / "trip" / "IMG_0001.JPG"
    nested.parent.mkdir(parents=True, exist_ok=True)
    os.rename(library.root / "IMG_0001.JPG", nested)

    outcome = scan(library)

    assert outcome["new_assets"] == 0
    assert int(location_of(library, "trip\\IMG_0001.JPG")["asset_id"]) == moved_asset
    assert outcome["locations_missing"] == 1


# ------------------------------------------------------------- §14.7 delete is final only once
def test_fast_refresh_marks_a_deletion_missing_without_dropping_the_asset(library) -> None:
    full(library)
    assets_before = asset_rows(library)
    (library.root / "IMG_0001.JPG").unlink()

    outcome = scan(library)

    assert outcome["locations_missing"] == 1
    assert outcome["state"] == "SUCCEEDED"
    assert asset_rows(library) == assets_before
    row = location_of(library, "IMG_0001.JPG")
    assert str(row["availability"]) == "MISSING"
    assert row["asset_id"] is not None
    assert outcome.locations == {int(row["asset_id"])}


def test_an_offline_source_never_produces_a_missing_fact(library, monkeypatch) -> None:
    full(library)
    library.root.rename(library.root.with_suffix(".gone"))
    try:
        with pytest.raises(CatalogError) as raised:
            scan(library)
        assert raised.value.code == "SOURCE_OFFLINE"
        for row in library.catalog.read_all(
            "SELECT relative_path, availability FROM file_locations"
        ):
            assert str(row["availability"]) == "PRESENT"
    finally:
        library.root.with_suffix(".gone").rename(library.root)


# ------------------------------------------------------------- §14.8 same-path replacement
def test_fast_refresh_rebinds_a_same_path_replacement_without_inheriting_derivations(
    library,
) -> None:
    full(library)
    target = library.root / "beach sunset.jpg"
    old_asset = int(location_of(library, "beach sunset.jpg")["asset_id"])
    write_jpeg(target, index=42)

    outcome = scan(library)

    assert outcome["hash_targets"] == 1
    assert outcome["new_assets"] == 1
    new_asset = int(location_of(library, "beach sunset.jpg")["asset_id"])
    assert new_asset != old_asset
    assert outcome.content == {new_asset}
    assert (
        library.catalog.read_one(
            "SELECT COUNT(*) AS n FROM file_locations WHERE asset_id = ?", (old_asset,)
        )["n"]
        == 0
    )
    # The replaced bytes are a new asset with no derived row at all: nothing of the previous
    # content's work can be inherited by the new identity.
    assert (
        library.catalog.read_one(
            "SELECT COUNT(*) AS n FROM index_entries WHERE asset_id = ?", (new_asset,)
        )["n"]
        == 0
    )
    assert (
        library.catalog.read_one(
            "SELECT COUNT(*) AS n FROM assets WHERE asset_id = ?", (old_asset,)
        )["n"]
        == 1
    )


# ---------------------------------------- §14.9/§14.10 the complete-verification scan
def test_full_scan_still_hashes_every_seen_position(library) -> None:
    full(library)
    sizes = sum(int(row["size"]) for row in library.catalog.read_all("SELECT size FROM file_locations"))

    outcome = full(library)

    assert outcome["mode"] == SCAN_MODE_FULL
    assert outcome["hash_targets"] == 3
    assert outcome["hash_bytes"] == sizes
    assert outcome["unchanged_reused"] == 0
    assert outcome["content_confirmed"] == 3


def test_full_scan_corrects_a_replacement_that_disguises_its_size_and_mtime(library) -> None:
    full(library)
    target = library.root / "beach sunset.jpg"
    original = target.read_bytes()
    moment = target.stat().st_mtime_ns
    disguised = original[:-1] + bytes([original[-1] ^ 0xFF])
    assert len(disguised) == len(original) and disguised != original
    target.write_bytes(disguised)
    os.utime(target, ns=(moment, moment))

    # Documented limit of the freshness hint (P4-02Y §2): a same-size/same-mtime swap is
    # invisible to the fast refresh...
    fast = scan(library)
    assert fast["hash_targets"] == 0
    assert fast["unchanged_reused"] == 3
    assert sha_of(library, int(location_of(library, "beach sunset.jpg")["asset_id"])) == (
        hashlib.sha256(original).hexdigest()
    )

    # ...and the complete-verification scan corrects it.
    corrected = full(library)
    assert corrected["hash_targets"] == 3
    assert sha_of(library, int(location_of(library, "beach sunset.jpg")["asset_id"])) == (
        hashlib.sha256(disguised).hexdigest()
    )


# ------------------------------------------------------------------ §14.13/§14.14 batching
def library_state(library) -> tuple:
    return (
        asset_rows(library),
        location_rows(library),
        [
            (int(row["scan_id"]), str(row["state"]))
            for row in library.catalog.read_all("SELECT scan_id, state FROM scan_runs ORDER BY scan_id")
        ],
    )


def test_batched_observation_is_equivalent_to_the_per_file_baseline(tmp_path: Path) -> None:
    states = []
    names: Sequence[str] = ("alpha.jpg", "beta.jpg", "gamma.jpg", "delta.jpg", "epsilon.jpg")
    for batch in (1, 128, 256):
        local = tmp_path / f"batch-{batch}"
        local.mkdir(parents=True, exist_ok=True)
        library = build_library(local, names)
        try:
            full(library, batch=batch)
            no_op = scan(library, batch=batch)
            write_jpeg(library.root / "zeta.jpg", index=17)
            (library.root / "beta.jpg").unlink()
            os.rename(library.root / "alpha.jpg", library.root / "alpha-renamed.jpg")
            changed = scan(library, batch=batch)
            states.append(
                (
                    no_op["hash_targets"],
                    no_op["unchanged_reused"],
                    changed["hash_targets"],
                    changed["locations_missing"],
                    changed["unchanged_reused"],
                    changed["new_assets"],
                    changed.content,
                    library_state(library),
                )
            )
        finally:
            library.close()
    assert states[0] == states[1] == states[2], states
    # The no-op refresh hashes nothing; the change set is one new file, one rename and one
    # deletion, with three reusable positions left.
    assert states[0][:6] == (0, 5, 2, 2, 3, 1)


def test_batched_observation_still_detects_a_path_key_collision(tmp_path: Path) -> None:
    library = build_library(tmp_path, ("one.jpg", "two.jpg"))
    try:
        full(library)
        target = library.root / "one.jpg"
        size, mtime = target.stat().st_size, target.stat().st_mtime_ns
        injected = [
            FileObservation("one.jpg", target, size, mtime, "one.jpg"),
            FileObservation("ONE.JPG", target, size, mtime, "one.jpg"),
        ]
        with pytest.raises(CatalogError) as raised:
            execute_scan(
                library.service,
                library.source_id,
                mode=SCAN_MODE_FAST,
                observation_batch=128,
                observations=injected,
            )
        assert raised.value.code == PATH_KEY_COLLISION
        assert library.catalog.read_one("SELECT COUNT(*) AS n FROM file_locations")["n"] == 2
    finally:
        library.close()


def test_batched_observation_clears_a_changed_binding_in_the_same_transaction(
    tmp_path: Path,
) -> None:
    library = build_library(tmp_path, ("one.jpg", "two.jpg"))
    try:
        full(library)
        target = library.root / "one.jpg"
        size = target.stat().st_size
        mtime = target.stat().st_mtime_ns
        changed = FileObservation("one.jpg", target, size + 1, mtime + 1, "one.jpg")
        with pytest.raises(ScanInterrupted):
            execute_scan(
                library.service,
                library.source_id,
                mode=SCAN_MODE_FAST,
                observation_batch=128,
                observations=[changed],
                interrupt_after=0,
            )
        row = location_of(library, "one.jpg")
        assert row["asset_id"] is None, "a changed observation must clear the binding immediately"
        assert str(row["availability"]) == "PRESENT"
        assert library.service.counts(source_id=library.source_id)["locations_pending_content"] == 1
    finally:
        library.close()


def test_an_interrupted_fast_refresh_recovers_the_same_scan(library) -> None:
    full(library)
    write_jpeg(library.root / "later.jpg", index=51)
    with pytest.raises(ScanInterrupted):
        scan(library, interrupt_after=0)

    assert library.service.counts(source_id=library.source_id)["locations_pending_content"] == 1
    assert library.catalog.read_one(
        "SELECT COUNT(*) AS n FROM file_locations WHERE relative_path = 'later.jpg'"
    )["n"] == 1

    library.clock.advance(61_000)
    recovered = scan(library)
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["scan_id"] == 3
    assert recovered["reused_scan_request"] is True
    assert recovered["hash_targets"] == 1
    assert library.service.counts(source_id=library.source_id)["locations_pending_content"] == 0
    assert library.catalog.read_one("SELECT COUNT(*) AS n FROM jobs")["n"] == 3


def test_a_delta_larger_than_the_cap_is_reported_as_an_overflow(tmp_path: Path, monkeypatch) -> None:
    from photo_search.ingestion import scanner as scanner_module

    monkeypatch.setattr(scanner_module, "DELTA_LIMIT", 2)
    library = build_library(tmp_path, ("a.jpg", "b.jpg"))
    try:
        full(library)
        for index, name in enumerate(("c.jpg", "d.jpg", "e.jpg")):
            write_jpeg(library.root / name, index=100 + index)

        outcome = scan(library)

        assert outcome["hash_targets"] == 3
        assert outcome["content_delta_count"] == 3
        assert outcome["delta_overflow"] is True
        assert len(outcome.content) == 2
        assert "content_delta" not in outcome.data
        assert "location_delta" not in outcome.data
    finally:
        library.close()


def test_the_fast_scan_never_reads_an_unchanged_original(library, monkeypatch) -> None:
    """The core §3 guarantee: a no-op refresh must not open a single original file."""
    full(library)
    hashed: list[str] = []
    real_hash = scanner_module_hash()

    def recording_hash(path: Path) -> str:
        hashed.append(str(path))
        return real_hash(path)

    monkeypatch.setattr("photo_search.ingestion.scanner.sha256_file", recording_hash)
    outcome = scan(library)

    assert hashed == []
    assert outcome["hash_bytes"] == 0
    assert outcome["unchanged_reused"] == 3


def scanner_module_hash():
    import photo_search.ingestion.scanner as scanner_module

    return scanner_module.sha256_file


__all__ = ["NAMES"]
