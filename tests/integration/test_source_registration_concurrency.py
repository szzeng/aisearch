"""Source overlap rejection remains read-only without weakening the write recheck."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from photo_search.catalog.errors import CATALOG_UNREADABLE, SOURCE_OVERLAP, CatalogError
from photo_search.catalog.paths import normalize_root
from photo_search.ingestion.service import CatalogService


@pytest.fixture
def services(tmp_path: Path):
    first = CatalogService.open(data_root=tmp_path / "app-data")
    second = CatalogService.open(data_root=tmp_path / "app-data")
    try:
        yield first, second
    finally:
        second.close()
        first.close()


def _assert_overlap(error: CatalogError, source: dict, requested: Path) -> None:
    assert error.code == SOURCE_OVERLAP
    assert error.message == f"source root overlaps the existing source {source['source_id']}"
    assert error.details == {
        "existing_source_id": source["source_id"],
        "existing_root_path": source["root_path"],
        "root_path": normalize_root(requested),
        "hint": "merge into the existing source or re-map it instead of adding a "
        "second overlapping source",
    }


@pytest.mark.parametrize("relation", ["same", "ancestor", "descendant"])
def test_overlap_is_refused_while_another_connection_holds_the_writer(
    services, tmp_path: Path, relation: str
) -> None:
    writer, contender = services
    root = tmp_path / "library" / "existing"
    root.mkdir(parents=True)
    source = writer.register_source(display_name="existing", root_path=root)
    requested = {"same": root, "ancestor": root.parent, "descendant": root / "child"}[relation]
    before = contender.list_sources()
    changes_before = contender.catalog.connection.total_changes
    # Do not use sleeps or depend on disk speed. Any attempt to acquire the busy
    # writer must fail immediately, while WAL readers can see the committed root.
    contender.catalog.connection.execute("PRAGMA busy_timeout = 0")
    with writer.catalog.write():
        with pytest.raises(CatalogError) as error:
            contender.register_source(display_name="overlap", root_path=requested)
        _assert_overlap(error.value, source, requested)
        assert writer.catalog.connection.in_transaction is True
        assert contender.catalog.connection.in_transaction is False
        assert contender.list_sources() == before
        assert contender.catalog.connection.total_changes == changes_before
    assert contender.list_sources() == before


@pytest.mark.parametrize("concurrent_change", ["insert", "remap"])
def test_a_conflict_committed_after_the_precheck_is_refused_in_the_write_transaction(
    services, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, concurrent_change: str
) -> None:
    contender, rival = services
    requested = tmp_path / "library" / "requested"
    requested.mkdir(parents=True)
    rival_source = None
    if concurrent_change == "remap":
        rival_source = rival.register_source(display_name="rival", root_path=tmp_path / "other")
    original_write = contender.catalog.write
    changes_before = contender.catalog.connection.total_changes
    interleavings = []

    @contextmanager
    def write_after_rival_commit():
        nonlocal rival_source
        # The optimistic read must end before a fresh write snapshot is acquired.
        assert contender.catalog.connection.in_transaction is False
        if concurrent_change == "insert":
            rival_source = rival.register_source(display_name="rival", root_path=requested)
        else:
            # There is no public remap endpoint yet. This competing transaction
            # represents a mapping change without changing the source identity.
            with rival.catalog.write() as connection:
                connection.execute(
                    "UPDATE source_roots SET root_path = ? WHERE source_id = ?",
                    (normalize_root(requested), rival_source["source_id"]),
                )
            rival_source = rival.get_source(rival_source["source_id"])
        interleavings.append(rival_source["source_id"])
        with original_write() as connection:
            yield connection

    monkeypatch.setattr(contender.catalog, "write", write_after_rival_commit)
    with pytest.raises(CatalogError) as error:
        contender.register_source(display_name="contender", root_path=requested)
    _assert_overlap(error.value, rival_source, requested)
    assert interleavings == [rival_source["source_id"]]
    assert contender.catalog.connection.in_transaction is False
    assert contender.catalog.connection.total_changes == changes_before
    rows = contender.list_sources()
    assert len(rows) == 1
    assert rows[0]["source_id"] == rival_source["source_id"]
    assert rows[0]["display_name"] == "rival"
    assert rows[0]["root_path"] == normalize_root(requested)


def test_registration_on_a_closed_catalog_keeps_the_structured_error(
    services, tmp_path: Path
) -> None:
    service, _ = services
    service.close()
    with pytest.raises(CatalogError) as error:
        service.register_source(display_name="closed", root_path=tmp_path / "library")
    assert error.value.code == CATALOG_UNREADABLE
    assert error.value.message == "catalog connection is closed"


@pytest.mark.parametrize("outer_transaction", ["write", "read_snapshot"])
def test_nested_registration_does_not_change_the_callers_transaction(
    services, tmp_path: Path, outer_transaction: str
) -> None:
    service, _ = services
    root = tmp_path / "library"
    service.register_source(display_name="existing", root_path=root)
    before = service.list_sources()
    changes_before = service.catalog.connection.total_changes
    with getattr(service.catalog, outer_transaction)():
        with pytest.raises(RuntimeError, match="catalog read snapshots? must not"):
            service.register_source(display_name="nested", root_path=root)
        assert service.catalog.connection.in_transaction is True
        assert service.list_sources() == before
        assert service.catalog.connection.total_changes == changes_before
    assert service.catalog.connection.in_transaction is False
    assert service.list_sources() == before
