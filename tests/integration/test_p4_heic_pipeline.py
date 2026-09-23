"""P4-00C-A: HEIC through the real scanner, media batch, GPS/time extractors and API.

Every fixture is synthetic and lives under pytest's ``tmp_path``; the work runs through the
production scanner, the production media batch, the production P2-02 scheduler with the real
GPS / taken-time executors, and the real HTTP application. No real photo library, no model
and no GPU is involved, and no packaging/spec change is touched (that is P4-00C-B).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from p2_dataset_fixtures import PY_PROVIDER
from p2_search_api_fixtures import QUERY_VECTORS, SearchEnv, build_search_env
from p2_search_fixtures import FakeImageEncoder, embedding_spec
from p4_heic_fixtures import (
    QUIET_AGE_MS,
    WALL_BASE_MS,
    block_colour,
    gps_fields,
    quadrants,
    write_exif_heic,
    write_exif_jpeg,
    write_heic,
)
from p4_time_fixtures import assets_by_basename, python_capability, row_for, run_extraction
from PIL import Image

from photo_search.catalog.errors import DECODE_FAILED, FORMAT_UNSUPPORTED
from photo_search.geo import extract as geo_extract
from photo_search.geo.exif import STATUS_VALID
from photo_search.jobs import queue as jobs
from photo_search.media import pipeline
from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
from photo_search.scheduler import engine

HEIC_TWIN = "西湖 原图.heic"
JPEG_TWIN = "西湖 底片.jpg"


def scan(api_env: Any, *, name: str = "library") -> int:
    source = api_env.register_source(name=name)
    source_id = int(source["source_id"])
    outcome = api_env.scan(source_id)
    assert outcome["state"] == "SUCCEEDED", outcome
    return source_id


def artifact_for(api_env: Any, sha256: str, *, kind: str = "thumbnail") -> Path:
    spec = THUMBNAIL_SPEC if kind == "thumbnail" else PREVIEW_SPEC
    return api_env.service.media_cache().artifact_path(spec=spec, sha256=sha256)


def sha_of(api_env: Any, asset_id: int) -> str:
    row = api_env.service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
    )
    assert row is not None
    return str(row["sha256"])


def quadrant_blocks(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    return [
        block_colour(image, (width // 4, height // 4)),
        block_colour(image, (3 * width // 4, height // 4)),
        block_colour(image, (width // 4, 3 * height // 4)),
        block_colour(image, (3 * width // 4, 3 * height // 4)),
    ]


def expected_blocks(orientation: int) -> list[tuple[int, int, int]]:
    """The upright frame of a 4:3 fixture with the given EXIF orientation."""
    stored = quadrants(1600, 1200)
    transform = {
        1: lambda image: image,
        6: lambda image: image.transpose(Image.Transpose.ROTATE_270),
        8: lambda image: image.transpose(Image.Transpose.ROTATE_90),
    }[orientation](stored)
    return quadrant_blocks(transform)


# ------------------------------------------------------------------ media batch (B level)
@pytest.mark.parametrize("container_orientation", [True, False])
def test_a_heic_photo_publishes_an_upright_thumbnail_and_preview(
    api_env: Any, container_orientation: bool
) -> None:
    library = api_env.library
    for orientation in (6, 8):
        write_exif_heic(
            library / f"竖拍-{orientation}-{container_orientation}.heic",
            orientation=orientation,
            container_orientation=container_orientation,
            image=quadrants(1600, 1200),
        )
    write_exif_jpeg(
        library / JPEG_TWIN, orientation=6, image=quadrants(1600, 1200)
    )
    scan(api_env)
    names = assets_by_basename(api_env.service.catalog)

    summary = api_env.service.generate_thumbnail()
    assert summary["generated"] == 3, summary
    assert summary["failed"] == 0 and summary["unsupported"] == 0

    for orientation in (6, 8):
        name = f"竖拍-{orientation}-{container_orientation}.heic"
        artifact = artifact_for(api_env, sha_of(api_env, names[name]))
        assert artifact.is_file()
        with Image.open(artifact) as rendered:
            assert rendered.format == "JPEG"
            assert max(rendered.size) <= 320
            got = quadrant_blocks(rendered)
        for value, expected in zip(got, expected_blocks(orientation), strict=True):
            assert value == pytest.approx(expected, abs=24)

    preview = api_env.service.generate_preview()
    assert preview["generated"] == 3, preview
    for name in (*[f"竖拍-{o}-{container_orientation}.heic" for o in (6, 8)], JPEG_TWIN):
        path = artifact_for(api_env, sha_of(api_env, names[name]), kind="preview")
        assert path.is_file()
        with Image.open(path) as rendered:
            assert max(rendered.size) <= 1600


def test_a_corrupt_heic_is_isolated_next_to_healthy_photos(api_env: Any) -> None:
    """D-1: the batch keeps going and every bad file gets its own structured reason."""
    library = api_env.library
    good_heic = write_heic(library / "好照片.heic", size=(640, 480))
    write_exif_jpeg(library / "普通照片.jpg", image=quadrants(640, 480))
    payload = good_heic.read_bytes()
    (library / "截断.heic").write_bytes(payload[: len(payload) // 2])
    (library / "空壳.heic").write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1")
    (library / "现代.avif").write_bytes(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1")
    scan(api_env)

    summary = api_env.service.generate_thumbnail()

    assert summary["generated"] == 2, summary
    assert summary["failed"] == 2, summary
    assert summary["unsupported"] == 1, summary
    assert {entry["code"] for entry in summary["failures"]} == {
        DECODE_FAILED,
        FORMAT_UNSUPPORTED,
    }
    names = assets_by_basename(api_env.service.catalog)
    assert artifact_for(api_env, sha_of(api_env, names["好照片.heic"])).is_file()
    assert artifact_for(api_env, sha_of(api_env, names["普通照片.jpg"])).is_file()
    for broken in ("截断.heic", "空壳.heic", "现代.avif"):
        record_path = api_env.service.media_cache().failure_path(
            spec=THUMBNAIL_SPEC, sha256=sha_of(api_env, names[broken])
        )
        assert record_path.is_file()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert str(library) not in json.dumps(record, ensure_ascii=False)


@pytest.mark.parametrize("damage", ["replaced", "deleted"])
def test_a_heic_changed_while_its_thumbnail_is_built_is_never_published(
    api_env: Any, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """D-2: the observation guard rejects the attempt and leaves nothing behind."""
    library = api_env.library
    path = write_heic(library / "变动.heic", size=(800, 600))
    original_bytes = path.read_bytes()
    scan(api_env)
    names = assets_by_basename(api_env.service.catalog)
    artifact = artifact_for(api_env, sha_of(api_env, names["变动.heic"]))

    healthy = pipeline.load_normalized
    state = {"done": False}

    def damaging(candidate: Path, *, long_edge: int):
        decoded = healthy(candidate, long_edge=long_edge)
        if not state["done"]:
            state["done"] = True
            if damage == "deleted":
                Path(candidate).unlink()
            else:
                with Path(candidate).open("ab") as stream:
                    stream.write(b"\x00" * 16)
        return decoded

    monkeypatch.setattr(pipeline, "load_normalized", damaging)
    summary = api_env.service.generate_thumbnail()

    assert summary["generated"] == 0, summary
    assert summary["artifacts_written"] == 0
    assert not artifact.is_file()
    assert summary["input_unstable"] == (1 if damage == "replaced" else 0)
    assert summary["source_missing"] == (0 if damage == "replaced" else 1)

    # 留待下次: restoring the exact content observation lets the same asset succeed.
    monkeypatch.undo()
    path.write_bytes(original_bytes)
    moment = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
    os.utime(path, ns=(moment, moment))
    again = api_env.service.generate_thumbnail()
    assert again["generated"] == 1, again
    assert artifact.is_file()


# --------------------------------------------------------------- determinism (D level)
def test_identical_heic_content_at_two_locations_is_deterministic(api_env: Any) -> None:
    """D-3: one content, two positions, one artifact; the lowest location_id is used."""
    library = api_env.library
    first = write_heic(library / "甲 原图.heic", image=quadrants(900, 700))
    second = library / "乙 副本.heic"
    shutil.copyfile(first, second)
    moment = (WALL_BASE_MS - QUIET_AGE_MS) * 1_000_000
    os.utime(second, ns=(moment, moment))
    write_exif_jpeg(library / JPEG_TWIN, image=quadrants(900, 700))
    scan(api_env)

    rows = api_env.service.catalog.read_all(
        "SELECT l.location_id, l.relative_path, a.sha256 FROM file_locations AS l "
        "JOIN assets AS a ON a.asset_id = l.asset_id ORDER BY l.location_id"
    )
    heic_rows = [row for row in rows if str(row["relative_path"]).endswith(".heic")]
    assert len(heic_rows) == 2
    assert str(heic_rows[0]["sha256"]) == str(heic_rows[1]["sha256"])
    assert len({str(row["sha256"]) for row in rows}) == 2  # the JPEG twin is its own asset
    artifact = artifact_for(api_env, str(heic_rows[0]["sha256"]))

    first_run = api_env.service.generate_thumbnail()
    assert first_run["generated"] == 2, first_run
    assert first_run["failures"] == []
    published = artifact.read_bytes()

    second_run = api_env.service.generate_thumbnail()
    assert second_run["generated"] == 0 and second_run["already_published"] == 2, second_run
    assert artifact.read_bytes() == published

    # The first position disappears: the same content is rebuilt from the second one and the
    # bytes are identical, which is what a deterministic position order has to mean.
    artifact.unlink()
    first.unlink()
    third_run = api_env.service.generate_thumbnail()
    assert third_run["generated"] == 1 and third_run["already_published"] == 1, third_run
    assert artifact.read_bytes() == published


# ------------------------------------------------------------------- GPS evidence (B/D)
def build_gps_scheduler(catalog: Any) -> engine.Scheduler:
    spec = geo_extract.gps_processor_spec()
    scheduler = engine.Scheduler(catalog, [spec], lease_seconds=60)
    scheduler.record_capability(python_capability(now_ms=1_000), now_ms=1_000)
    scheduler.record_qualification(
        processor_id=spec.processor_id,
        spec_revision=spec.spec_revision,
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name=PY_PROVIDER,
        runtime_package_id=spec.runtime_package_id,
        now_ms=1_000,
    )
    return scheduler


def run_gps(api_env: Any, asset_ids: list[int]) -> None:
    catalog = api_env.service.catalog
    scheduler = build_gps_scheduler(catalog)
    with catalog.write() as connection:
        for asset_id in asset_ids:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=str(int(asset_id)),
                input_revision=sha_of(api_env, asset_id),
                task_type="geo_extract",
                processor_id=geo_extract.GPS_PROCESSOR_ID,
                now_ms=1_000,
            )
    steps = scheduler.run_until_idle(
        geo_extract.gps_executor(catalog, clock=lambda: 1_000),
        worker_id="p4-heic",
        now_ms=1_100,
    )
    assert steps == len(asset_ids), steps


def gps_row(api_env: Any, asset_id: int) -> Any:
    row = api_env.service.catalog.read_one(
        "SELECT * FROM asset_geo_facts WHERE asset_id = ?", (int(asset_id),)
    )
    assert row is not None
    return row


def test_gps_facts_match_between_a_jpeg_and_a_heic_twin(api_env: Any) -> None:
    library = api_env.library
    for name, writer in ((JPEG_TWIN, write_exif_jpeg), (HEIC_TWIN, write_exif_heic)):
        writer(library / name, gps=gps_fields(), make="ACME", model="X1")
    scan(api_env)
    names = assets_by_basename(api_env.service.catalog)
    run_gps(api_env, [names[HEIC_TWIN], names[JPEG_TWIN]])

    heic = gps_row(api_env, names[HEIC_TWIN])
    jpeg = gps_row(api_env, names[JPEG_TWIN])

    for row in (heic, jpeg):
        assert row["status"] == STATUS_VALID
        assert row["datum_status"] == "WGS84"
        assert row["source"] == "EXIF_GPS"
    assert float(heic["latitude"]) == pytest.approx(float(jpeg["latitude"]), abs=1e-9)
    assert float(heic["longitude"]) == pytest.approx(float(jpeg["longitude"]), abs=1e-9)
    assert heic["raw_gps_json"] == jpeg["raw_gps_json"]
    assert heic["raw_fields_checksum_sha256"] == jpeg["raw_fields_checksum_sha256"]


def test_an_invalid_gps_block_is_invalid_for_both_formats(api_env: Any) -> None:
    """The frozen decisions are reused for HEIC instead of being taken a second time."""
    library = api_env.library
    broken = {1: "N", 2: (30.0, 15.0, 0.0), 3: "X", 4: (120.0, 10.0, 0.0)}
    write_exif_jpeg(library / "坏方向.jpg", gps=broken)
    write_exif_heic(library / "坏方向.heic", gps=broken)
    scan(api_env)
    names = assets_by_basename(api_env.service.catalog)
    run_gps(api_env, [names["坏方向.jpg"], names["坏方向.heic"]])

    jpeg = gps_row(api_env, names["坏方向.jpg"])
    heic = gps_row(api_env, names["坏方向.heic"])
    assert jpeg["status"] == heic["status"] == "INVALID"
    assert jpeg["datum_status"] == heic["datum_status"]
    assert heic["latitude"] is None and heic["longitude"] is None
    assert heic["raw_gps_json"] == jpeg["raw_gps_json"]


def test_a_missing_gps_block_is_absent_for_both_formats(api_env: Any) -> None:
    library = api_env.library
    write_exif_heic(library / HEIC_TWIN, original="2022:07:05 18:31:07")
    write_exif_jpeg(library / JPEG_TWIN, original="2022:07:05 18:31:07")
    scan(api_env)
    names = assets_by_basename(api_env.service.catalog)
    run_gps(api_env, [names[HEIC_TWIN], names[JPEG_TWIN]])
    for name in (HEIC_TWIN, JPEG_TWIN):
        row = gps_row(api_env, names[name])
        assert row["status"] == "ABSENT" and row["datum_status"] == "ABSENT"
        assert row["latitude"] is None and row["longitude"] is None


# --------------------------------------------------------------- taken time (B level)
def time_facts(row: Any) -> tuple[Any, ...]:
    """Every derived time fact of one ``asset_metadata`` row, without its identity."""
    return (
        row.taken_at_raw,
        row.taken_at_sort,
        row.time_source,
        row.time_confidence,
        row.timezone_offset,
        row.camera,
    )


def test_taken_time_matches_between_a_jpeg_and_a_heic_twin(api_env: Any) -> None:
    library = api_env.library
    cases: dict[str, dict[str, Any]] = {
        "有偏移.jpg": {
            "original": "2022:07:05 18:31:07",
            "offset_original": "+08:00",
            "subsec_original": "12345",
            "digitized": "2022:07:05 18:31:08",
            "offset_digitized": "+08:00",
            "make": "ACME",
            "model": "X1",
        },
        "无偏移.jpg": {"original": "2022:07:05 18:31:07"},
        "只有根时间.jpg": {"root": "2021:05:06 07:08:09", "offset_root": "+08:00"},
    }
    for jpeg_name, options in cases.items():
        write_exif_jpeg(library / jpeg_name, **options)
        write_exif_heic(library / jpeg_name.replace(".jpg", ".heic"), **options)
    scan(api_env)

    catalog = api_env.service.catalog
    run_extraction(catalog)
    names = assets_by_basename(catalog)
    for jpeg_name in cases:
        heic_name = jpeg_name.replace(".jpg", ".heic")
        jpeg_row = row_for(catalog, names[jpeg_name])
        heic_row = row_for(catalog, names[heic_name])
        assert jpeg_row is not None and heic_row is not None
        # ``asset_id`` is the only field that may differ: every derived time fact is equal.
        assert time_facts(heic_row) == time_facts(jpeg_row)

    with_offset = row_for(catalog, names["有偏移.jpg"])
    assert with_offset is not None
    assert (with_offset.time_source, with_offset.time_confidence) == ("EXIF_ORIGINAL", "HIGH")
    without_offset = row_for(catalog, names["无偏移.jpg"])
    assert without_offset is not None and without_offset.time_confidence == "MEDIUM"
    root_only = row_for(catalog, names["只有根时间.jpg"])
    assert root_only is not None and root_only.time_source == "EXIF_ROOT"


# ------------------------------------------------------------------------ API surfaces
def test_the_capabilities_endpoint_advertises_heic(api_env: Any) -> None:
    async def scenario(client: AsyncClient) -> Any:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 200, response.text
        return response.json()

    body = api_env.scenario(scenario)
    assert "HEIC" in body["supported_input_formats"]
    assert "HEIF" in body["supported_input_formats"]
    assert "TIFF" not in body["supported_input_formats"]
    assert "HEIC" in body["media"]["thumbnail"]["supported_input_formats"]
    assert "HEIC" in body["media"]["preview"]["supported_input_formats"]


@pytest.fixture()
def image_env(tmp_path: Path):
    """The served application with a deterministic image tower (the P2-07 test seam)."""
    encoder = FakeImageEncoder(
        embedding_spec(), vectors=QUERY_VECTORS, image_vector=[1.0, 0.0, 0.0]
    )
    env = build_search_env(tmp_path, image_encoder=encoder)
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


def test_the_upload_endpoint_refuses_a_heic_with_a_structured_409(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    """The recorded real-HTTP sample of the P4-00C-A upload ruling."""
    heic_bytes = write_exif_heic(tmp_path / "上传 原图.heic", orientation=6).read_bytes()

    async def scenario(client: AsyncClient):
        csrf = await image_env.authenticate(client)
        return await client.post(
            "/api/v1/search/image",
            data={**image_env.explicit_binding(), "request_id": "p4-00c-a-upload"},
            files={"image": ("上传 原图.heic", heic_bytes, "image/heic")},
            headers=SearchEnv.post_headers(csrf),
        )

    response = image_env.scenario(scenario)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == FORMAT_UNSUPPORTED
    assert body["details"]["format"] == "HEIF"
    assert "HEIC" not in body["details"]["supported_formats"]
