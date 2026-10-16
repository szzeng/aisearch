"""Shared ``%TEMP%`` helpers for the P2-08 adversarial acceptance matrix.

The acceptance matrix does not invent new production paths. It drives the ones P2-02 ..
P2-07 already prove - the scanner, the scheduler/lease protocol, the artifact/outbox/index
confirmation, the place core, the offline name package, the two recall channels and the
search session - and only adds the *combination*: one library where a scan, a projection
catch-up, a manual place edit, an offline source and a live search session all happen
around each other.

Everything here lives under pytest's ``tmp_path`` (``%TEMP%``): no real photo library is
read, moved or deleted, no model or GPU is involved and nothing touches the network. The
encoders are the deterministic stand-ins P2-05/P2-07 documented, the GPS values are real
EXIF written into synthetic JPEGs, and the name package is the synthetic mini city set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from p2_dataset_fixtures import (
    build_package,
    python_capability,
    write_gps_jpeg,
)
from p2_search_api_fixtures import QUERY_VECTORS, SearchEnv, build_search_env
from p2_search_fixtures import (
    FakeImageEncoder,
    embedding_spec,
    stamp,
    write_jpeg,
)

from photo_search.geo import extract as geo_extract
from photo_search.geo import resolver as resolver_module
from photo_search.indexing.store import IndexCoordinator
from photo_search.ingestion.service import CatalogService
from photo_search.jobs import queue as jobs
from photo_search.places import assignments as assignments_module
from photo_search.places import datasets as datasets_module
from photo_search.places import revision as revision_module
from photo_search.scheduler import engine

#: The deterministic query vectors this matrix's adapter answers. ``renamed`` and ``照片``
#: are the extra tokens cases 6/7/8 use to prove that the *first* query after a rename or a
#: manual assignment already finds the asset through the authoritative path.
ACCEPTANCE_VECTORS: dict[str, list[float]] = {
    **QUERY_VECTORS,
    "renamed": [1.0, 0.0, 0.0],
    "照片": [1.0, 0.0, 0.0],
}

GPS_PROVIDER = "python-stdlib"


# --------------------------------------------------------------------------- environment
def acceptance_env(tmp_path: Path, *, reranker: Any = None) -> SearchEnv:
    """A scanned three-photo library, one READY generation, a real app, no dataset."""
    encoder = FakeImageEncoder(
        embedding_spec(),
        vectors=dict(ACCEPTANCE_VECTORS),
        image_vector=[1.0, 0.0, 0.0],
    )
    return build_search_env(Path(tmp_path), reranker=reranker, image_encoder=encoder)


def reopen_service(env: SearchEnv, *, advance_ms: int = 0) -> CatalogService:
    """Close this process's catalog writer and reopen the same data root.

    This is the "the process was killed" step of the recovery cases: the durable rows are
    the only state that carries over, exactly as a new process would find them. The
    deterministic clock may be advanced first so a lease left by the killed process looks
    expired, which is what makes a takeover (not a second scan) the correct behaviour.
    """
    env.library.clock.advance(int(advance_ms))
    env.library.close()
    service = CatalogService.open(
        data_root=env.data_root,
        clock=env.library.clock,
        lease_seconds=60,
        wall_clock_ms=env.library.clock,
        stability_sleep=env.library.clock.advance,
    )
    env.library.service = service
    return service


def add_photo(env: SearchEnv, relative: str, *, index: int) -> Path:
    return write_jpeg(env.library.root / relative, index=int(index))


def add_gps_photo(
    env: SearchEnv,
    relative: str,
    *,
    position: tuple[float, float],
    colour: tuple[int, int, int] = (12, 34, 56),
) -> Path:
    return write_gps_jpeg(
        env.library.root / relative, position=position, color=colour
    )


def replace_photo(
    env: SearchEnv,
    relative: str,
    *,
    size: tuple[int, int] = (72, 54),
    colour: tuple[int, int, int] = (11, 22, 33),
) -> Path:
    """Overwrite one position with visibly different bytes, kept inside the quiet window.

    The size differs from the 24x18 fixtures, so ADR-0002 §5.1's fast ``size``/``mtime``
    hint *does* fire here: this case is about "the user replaced the file", not about the
    same-size/restored-mtime window P1-06 already recorded.
    """
    from PIL import Image

    path = env.library.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "JPEG", quality=90)
    return stamp(path)


def asset_for(env: SearchEnv, relative: str) -> int:
    """The confirmed ``asset_id`` of one source-relative position, right now."""
    wanted = str(relative).replace("\\", "/")
    for row in env.catalog.read_all(
        "SELECT asset_id, relative_path FROM file_locations ORDER BY location_id"
    ):
        if str(row["relative_path"]).replace("\\", "/") == wanted:
            assert row["asset_id"] is not None, f"{relative} has no confirmed content"
            return int(row["asset_id"])
    raise AssertionError(f"{relative} is not a known location")


def rescan(env: SearchEnv, **options: Any) -> dict[str, Any]:
    return env.library.service.run_scan(env.library.source_id, **options)


def latest_scan(catalog: Any) -> Any:
    return catalog.read_one("SELECT * FROM scan_runs ORDER BY scan_id DESC LIMIT 1")


def latest_job(catalog: Any) -> Any:
    return catalog.read_one("SELECT * FROM jobs ORDER BY job_id DESC LIMIT 1")


def sha_of(catalog: Any, asset_id: int) -> str:
    row = catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
    )
    assert row is not None, f"asset {asset_id} does not exist"
    return str(row["sha256"])


def table_counts(catalog: Any, tables: Sequence[str]) -> dict[str, int]:
    return {
        str(name): int(
            catalog.read_one(f"SELECT COUNT(*) AS value FROM {name}")["value"]
        )
        for name in tables
    }


def row_snapshot(catalog: Any, sql: str, parameters: tuple = ()) -> list[tuple]:
    return [tuple(row) for row in catalog.read_all(sql, parameters)]


# ------------------------------------------------------------------------------ places
def make_source_place(catalog: Any, name: str) -> int:
    """Install a real synthetic source choice before exercising new manual assignments."""
    dataset_id = "p2-acceptance-source-place"
    source = Path(catalog.data_root) / "test-fixtures" / dataset_id
    build_package(source, dataset_id=dataset_id, aliases=(), places=({
        "dataset_place_id": "acceptance-city",
        "kind": "city",
        "display_name": str(name),
        "country_code": "CN",
        "latitude": 30.2741,
        "longitude": 120.1551,
    },))
    staged = datasets_module.stage_package(catalog, source)
    validated = datasets_module.validate_dataset(catalog, str(staged["dataset_id"]))
    datasets_module.activate_dataset(catalog, str(validated["dataset_id"]))
    # Complete any setup projection work before measuring the separate manual edit's
    # pending/recovery effects.
    IndexCoordinator(catalog).process_all(now_ms=int(catalog.clock()))
    row = catalog.read_one(
        "SELECT place_id FROM geo_dataset_place_refs WHERE dataset_id=? AND external_id=?",
        (dataset_id, "acceptance-city"),
    )
    assert row is not None
    return int(row["place_id"])


def assignment_row(catalog: Any, asset_id: int) -> Any:
    return catalog.read_one(
        "SELECT * FROM asset_place_assignments WHERE asset_id = ?", (int(asset_id),)
    )


def place_action(
    catalog: Any,
    *,
    asset_id: int,
    action: str,
    request_id: str,
    target_place_id: int | None = None,
    now_ms: int = 1_600,
) -> dict[str, Any]:
    """One ASSIGN/UNASSIGN/RESTORE_AUTO through the production write path."""
    row = assignment_row(catalog, int(asset_id))
    expected = 0 if row is None else int(row["revision"])
    item: dict[str, Any] = {
        "asset_id": int(asset_id),
        "action": str(action),
        "expected_revision": expected,
    }
    if target_place_id is not None:
        item["target_place_id"] = int(target_place_id)
    with catalog.write() as connection:
        return assignments_module.apply_request(
            connection,
            items=[item],
            request_id=str(request_id),
            now_ms=int(now_ms),
            refresh=revision_module.projection_refresher(catalog),
        )


def filter_revision(catalog: Any) -> int:
    return int(revision_module.read_filter_revision(catalog.connection))


def suggestion_revision(catalog: Any) -> int:
    return int(revision_module.read_suggestion_revision(catalog.connection))


# ------------------------------------------------------------------------------ geo/datasets
def geo_scheduler(
    catalog: Any, *, now_ms: int = 1_000, lease_seconds: int = 60
) -> engine.Scheduler:
    """A scheduler that can run the deterministic CPU GPS and nearby-city processors."""
    specs = [
        geo_extract.gps_processor_spec(),
        resolver_module.resolver_processor_spec(),
    ]
    scheduler = engine.Scheduler(catalog, specs, lease_seconds=int(lease_seconds))
    scheduler.record_capability(python_capability(now_ms=int(now_ms)), now_ms=int(now_ms))
    for spec in specs:
        scheduler.record_qualification(
            processor_id=str(spec.processor_id),
            spec_revision=str(spec.spec_revision),
            device="cpu",
            precision=str(spec.supported_precisions[0]),
            provider_name=GPS_PROVIDER,
            runtime_package_id=str(spec.runtime_package_id),
            now_ms=int(now_ms),
        )
    return scheduler


def enqueue_job(
    catalog: Any,
    *,
    asset_id: int,
    processor_id: str,
    task_type: str,
    input_revision: str,
    now_ms: int = 1_000,
) -> int:
    with catalog.write() as connection:
        return int(
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(int(asset_id)),
                input_revision=str(input_revision),
                task_type=str(task_type),
                processor_id=str(processor_id),
                now_ms=int(now_ms),
            )
        )


def run_gps(
    env: SearchEnv,
    asset_ids: Sequence[int],
    *,
    scheduler: engine.Scheduler | None = None,
    now_ms: int = 1_100,
) -> int:
    catalog = env.catalog
    worker = scheduler or geo_scheduler(catalog)
    for asset_id in asset_ids:
        enqueue_job(
            catalog,
            asset_id=int(asset_id),
            processor_id=geo_extract.GPS_PROCESSOR_ID,
            task_type=geo_extract.GPS_TASK_TYPE,
            input_revision=sha_of(catalog, int(asset_id)),
        )
    return worker.run_until_idle(
        geo_extract.gps_executor(catalog, clock=env.library.clock),
        worker_id="w-gps",
        now_ms=int(now_ms),
    )


def run_resolver(
    env: SearchEnv,
    asset_ids: Sequence[int],
    *,
    scheduler: engine.Scheduler | None = None,
    now_ms: int = 1_200,
) -> int:
    catalog = env.catalog
    worker = scheduler or geo_scheduler(catalog)
    for asset_id in asset_ids:
        enqueue_job(
            catalog,
            asset_id=int(asset_id),
            processor_id=resolver_module.RESOLVER_PROCESSOR_ID,
            task_type=resolver_module.RESOLVER_TASK_TYPE,
            input_revision=sha_of(catalog, int(asset_id)),
        )
    return worker.run_until_idle(
        resolver_module.resolver_executor(
            catalog, data_root=env.data_root, clock=env.library.clock
        ),
        worker_id="w-resolver",
        now_ms=int(now_ms),
    )


def source_package(
    tmp_path: Path, name: str = "mini-1", **overrides: Any
) -> Path:
    directory = Path(tmp_path) / "package-sources" / str(name)
    build_package(directory, **overrides)
    return directory


def install_dataset(
    env: SearchEnv,
    *,
    tmp_path: Path,
    name: str = "mini-1",
    activate: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    source = source_package(tmp_path, name, **overrides)
    staged = datasets_module.stage_package(env.catalog, source)
    validated = datasets_module.validate_dataset(
        env.catalog, str(staged["dataset_id"])
    )
    if not activate:
        return validated
    return datasets_module.activate_dataset(env.catalog, str(validated["dataset_id"]))


def dataset_package_dir(env: SearchEnv, dataset_id: str) -> Path:
    return datasets_module.dataset_dir(env.data_root, str(dataset_id))


def dataset_status(env: SearchEnv) -> dict[str, Any]:
    return datasets_module.dataset_status(
        env.catalog.connection, data_root=env.data_root
    )


def dataset_row(env: SearchEnv, dataset_id: str) -> Any:
    return datasets_module.dataset_row(env.catalog.connection, str(dataset_id))
