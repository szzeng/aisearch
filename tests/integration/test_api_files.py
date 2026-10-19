"""P1-04 scenarios 5-8: media bytes, controlled originals, offline and bad inputs.

Every fixture is created under ``tmp_path`` (``%TEMP%``); no real photo library is read.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import random
import stat
import subprocess
import time
from pathlib import Path

from httpx import AsyncClient
from PIL import Image

from photo_search.api import place_cover_warmer
from photo_search.api.place_cover_warmer import PlaceCoverWarmer
from photo_search.ingestion.service import CatalogService
from photo_search.media.cache import MediaCache
from photo_search.media.contract import THUMBNAIL_SPEC

OLD_MTIME_NS = (1_700_000_000_000 - 3_600_000) * 1_000_000
ARTIFACT_CACHE = "private, no-cache, must-revalidate"


def _stamp_old(path: Path, now_ms: int | None = None) -> Path:
    """Give a fixture file a quiet, deterministic ``mtime`` (outside the stability window)."""
    moment = OLD_MTIME_NS if now_ms is None else (int(now_ms) - 3_600_000) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def _tree_state(root: Path) -> dict[str, tuple[int, int, bool]]:
    state: dict[str, tuple[int, int, bool]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        info = path.stat()
        state[str(path.relative_to(root))] = (
            int(info.st_size),
            int(info.st_mtime_ns),
            bool(info.st_mode & stat.S_IWRITE),
        )
    return state


def _inject_asset(service: CatalogService, target: Path) -> tuple[int, int, int]:
    """Create an asset row for a file the scanner never indexed."""
    payload = target.read_bytes()
    info = target.stat()
    sha256 = hashlib.sha256(payload).hexdigest()
    with service.catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO assets (sha256, byte_size, created_at, state) VALUES (?, ?, ?, 'ACTIVE')",
            (sha256, len(payload), 1),
        )
    return int(cursor.lastrowid), int(info.st_size), int(info.st_mtime_ns)


def _inject_location(
    service: CatalogService,
    *,
    source_id: int,
    relative_path: str,
    path_key_value: str,
    asset: tuple[int, int, int],
) -> int:
    """Insert a location row directly, bypassing the scanner's path-key validation.

    This models a corrupt or hostile catalog row (or a source tree tampered with after the
    scan). It is the only way to prove the read layer refuses such a row on its own rather
    than relying on the scanner never having produced one.
    """
    asset_id, size, mtime_ns = asset
    with service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO file_locations "
            "(source_id, relative_path, path_key, asset_id, size, mtime_ns, last_seen_scan_id, "
            " availability) VALUES (?, ?, ?, ?, ?, ?, NULL, 'PRESENT')",
            (
                int(source_id),
                relative_path,
                path_key_value,
                asset_id,
                size,
                mtime_ns,
            ),
        )
    return asset_id


def _make_junction(link: Path, target: Path) -> bool:
    """Create a directory junction if this machine allows it."""
    try:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


def _image_size(payload: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(payload)) as image:
        return int(image.width), int(image.height)


def _asset_sha(api_env, asset_id: int) -> str:
    row = api_env.service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),)
    )
    return str(row["sha256"])


def _asset_ids_by_name(api_env) -> dict[str, int]:
    rows = api_env.service.catalog.read_all(
        "SELECT a.asset_id, l.relative_path FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id"
    )
    return {str(row["relative_path"]): int(row["asset_id"]) for row in rows}


def test_thumbnail_and_preview_are_generated_once_and_cached(api_env) -> None:
    api_env.add_photo("big.jpg", size=(2400, 1800), color=(30, 120, 200))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()
    sha256 = _asset_sha(api_env, asset_id)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        first = await client.get(f"/api/v1/assets/{asset_id}/thumbnail")
        second = await client.get(f"/api/v1/assets/{asset_id}/thumbnail")
        conditional = await client.get(
            f"/api/v1/assets/{asset_id}/thumbnail",
            headers={"If-None-Match": first.headers["ETag"]},
        )
        head = await client.head(f"/api/v1/assets/{asset_id}/thumbnail")
        preview = await client.get(f"/api/v1/assets/{asset_id}/preview")
        detail = await client.get(f"/api/v1/assets/{asset_id}")
        return {
            "first": first,
            "second": second,
            "conditional": conditional,
            "head": head,
            "preview": preview,
            "detail": detail.json(),
        }

    result = api_env.scenario(scenario)
    first = result["first"]
    assert first.status_code == 200
    assert first.headers["Content-Type"] == "image/jpeg"
    assert first.headers["Cache-Control"] == ARTIFACT_CACHE
    assert first.headers["X-Original-Available"] == "true"
    assert first.headers["ETag"] == f'"catalog-thumbnail@1:{sha256}"'
    assert _image_size(first.content) == (320, 240)

    second = result["second"]
    assert second.status_code == 200
    assert second.content == first.content
    assert second.headers["ETag"] == first.headers["ETag"]
    assert result["conditional"].status_code == 304
    assert result["head"].status_code == 200
    assert result["head"].content == b""
    assert result["head"].headers["Content-Length"] == first.headers["Content-Length"]

    preview = result["preview"]
    assert preview.status_code == 200
    assert preview.headers["ETag"] == f'"catalog-preview@1:{sha256}"'
    assert _image_size(preview.content) == (1600, 1200)

    media = result["detail"]["media"]
    assert media["thumbnail"]["state"] == "ready"
    assert media["preview"]["state"] == "ready"
    assert media["thumbnail"]["long_edge"] == 320
    assert media["preview"]["long_edge"] == 1600

    # Exactly one thumbnail artifact exists: a repeated request never recomputes.
    published = sorted(path.name for path in (api_env.data_root / "thumbnails").rglob("*.jpg"))
    assert published == [f"{sha256}.jpg"]


def test_continue_build_backfills_cr2_preview_and_open_does_not_decode_raw(
    api_env, monkeypatch
) -> None:
    """The build page warms the 1600px cache; the detail request only reads it."""
    from types import SimpleNamespace

    from photo_search.media import pipeline as media_pipeline
    from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
    from photo_search.media.decode import render_jpeg
    from photo_search.refresh.pipeline import _thumbnail_stage

    source = api_env.library / "historical.cr2"
    source.write_bytes(b"II*\x00\x08\x00\x00\x00CR\x02\x00" + bytes(16))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()
    calls: list[int] = []
    canonical = Image.effect_noise((6000, 4000), 60).convert("RGB")
    expected = {
        THUMBNAIL_SPEC.kind: render_jpeg(
            canonical.copy(), long_edge=THUMBNAIL_SPEC.long_edge,
            quality=THUMBNAIL_SPEC.jpeg_quality,
        ),
        PREVIEW_SPEC.kind: render_jpeg(
            canonical.copy(), long_edge=PREVIEW_SPEC.long_edge,
            quality=PREVIEW_SPEC.jpeg_quality,
        ),
    }

    def decode(path: Path, *, long_edge: int):
        assert path == source
        calls.append(long_edge)
        return SimpleNamespace(
            image=canonical.copy(),
            width=6000,
            height=4000,
            orientation=1,
            source_format="CR2",
        )

    monkeypatch.setattr(media_pipeline, "load_normalized", decode)
    started = time.perf_counter()
    built = _thumbnail_stage(api_env.service, {asset_id}, None, retry_cr2_failures=True)
    cold_ms = (time.perf_counter() - started) * 1000
    assert built["generated"] == 1
    assert built["cr2_previews_generated"] == 1
    assert calls == [1600]
    cache = api_env.service.media_cache()
    asset = api_env.service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id=?", (asset_id,)
    )
    assert asset is not None
    for spec in (THUMBNAIL_SPEC, PREVIEW_SPEC):
        artifact = cache.artifact_path(spec=spec, sha256=str(asset["sha256"]))
        assert artifact.read_bytes() == expected[spec.kind]

    started = time.perf_counter()
    second = _thumbnail_stage(api_env.service, {asset_id}, None, retry_cr2_failures=True)
    warm_ms = (time.perf_counter() - started) * 1000
    assert second["generated"] == 0
    assert second["cr2_previews_generated"] == 0
    assert calls == [1600]
    print(
        "CR2 paired-cache synthetic evidence:",
        {"cold_ms": round(cold_ms, 2), "warm_ms": round(warm_ms, 2),
         "decode_calls_cold": 1, "decode_calls_added_warm": 0,
         "artifacts_cold": 2, "artifacts_added_warm": 0,
         "artifact_bytes": sum(len(payload) for payload in expected.values())},
    )

    def forbidden_decode(*_args, **_kwargs):
        raise AssertionError("a cache-warmed first detail open must not decode the RAW")

    monkeypatch.setattr(media_pipeline, "load_normalized", forbidden_decode)

    async def scenario(client: AsyncClient) -> None:
        await api_env.authenticate(client)
        response = await client.get(f"/api/v1/assets/{asset_id}/preview")
        assert response.status_code == 200, response.text
        assert _image_size(response.content) == (1600, 1067)

    api_env.scenario(scenario)


def test_continue_build_spends_old_cr2_format_retry_once(api_env, monkeypatch) -> None:
    from photo_search.catalog.errors import FORMAT_UNSUPPORTED, CatalogError
    from photo_search.media import pipeline as media_pipeline
    from photo_search.media.contract import PREVIEW_SPEC, THUMBNAIL_SPEC
    from photo_search.refresh.pipeline import _thumbnail_stage

    source = api_env.library / "old-failure.cr2"
    source.write_bytes(b"II*\x00\x08\x00\x00\x00CR\x02\x00" + bytes(16))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()
    asset = api_env.service.catalog.read_one(
        "SELECT sha256, byte_size FROM assets WHERE asset_id = ?", (asset_id,)
    )
    info = source.stat()
    cache = api_env.service.media_cache()
    for spec in (THUMBNAIL_SPEC, PREVIEW_SPEC):
        cache.write_failure(
            spec=spec,
            sha256=str(asset["sha256"]),
            record={
                "schema": 1,
                "kind": spec.kind,
                "processor_id": spec.processor_id,
                "sha256": str(asset["sha256"]),
                "byte_size": int(asset["byte_size"]),
                "state": "UNSUPPORTED",
                "code": FORMAT_UNSUPPORTED,
                "message": "synthetic historical decoder refusal",
                "input_size": int(info.st_size),
                "input_mtime_ns": int(info.st_mtime_ns),
            },
        )

    calls: list[int] = []

    def refused(_path: Path, *, long_edge: int):
        calls.append(long_edge)
        raise CatalogError(FORMAT_UNSUPPORTED, "synthetic decoder refusal")

    monkeypatch.setattr(media_pipeline, "load_normalized", refused)
    first = _thumbnail_stage(api_env.service, {asset_id}, None, retry_cr2_failures=True)
    assert calls == [1600]
    for spec in (THUMBNAIL_SPEC, PREVIEW_SPEC):
        failure = cache.read_failure(spec=spec, sha256=str(asset["sha256"]))
        assert failure["code"] == FORMAT_UNSUPPORTED
        assert failure["cr2_format_retry"]["attempted"] is True
        assert failure["cr2_format_retry"]["prior_failure"]["message"] == (
            "synthetic historical decoder refusal"
        )

    second = _thumbnail_stage(api_env.service, {asset_id}, None, retry_cr2_failures=True)
    assert calls == [1600]
    assert second["cr2_retry_assets"] == 0
    assert first["cr2_retry_assets"] == 1


def test_cold_thumbnail_generation_does_not_block_other_async_requests(api_env, monkeypatch) -> None:
    api_env.add_photo("async-cover.jpg", size=(1200, 900), color=(20, 100, 180))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()

    from photo_search.api import routes

    original_generate_one = routes._generate_one

    def delayed_generate_one(service, selected_asset_id, kind):
        time.sleep(0.2)
        original_generate_one(service, selected_asset_id, kind)

    monkeypatch.setattr(routes, "_generate_one", delayed_generate_one)

    async def scenario(client: AsyncClient) -> None:
        await api_env.authenticate(client)
        task = asyncio.create_task(client.get(f"/api/v1/assets/{asset_id}/thumbnail"))
        await asyncio.sleep(0.02)
        assert not task.done(), "cold generation should still be running in a worker"
        response = await task
        assert response.status_code == 200, response.text

    api_env.scenario(scenario)


def test_place_cover_warmer_generates_and_reuses_the_existing_thumbnail_cache(
    api_env, monkeypatch
) -> None:
    api_env.add_photo("place-cover.jpg", size=(640, 480), color=(80, 120, 180))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()
    row = api_env.service.catalog.read_one(
        "SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)
    )
    cache = MediaCache.open(api_env.data_root)
    artifact = cache.artifact_path(spec=THUMBNAIL_SPEC, sha256=str(row["sha256"]))
    assert not artifact.exists()

    monkeypatch.setattr(
        place_cover_warmer.place_albums,
        "albums",
        lambda _catalog, _root, *, kind: {
            "albums": [{"cover_asset_id": asset_id}] if kind == "city" else []
        },
    )

    def service_factory() -> CatalogService:
        return CatalogService.open(data_root=api_env.data_root, disk_usage=api_env.disk_usage)

    warmer = PlaceCoverWarmer(service_factory)
    try:
        assert warmer.request()
        assert warmer.wait_idle(timeout=10)
        assert artifact.is_file()
        first_mtime_ns = artifact.stat().st_mtime_ns
        first_bytes = artifact.read_bytes()

        assert warmer.request()
        assert warmer.wait_idle(timeout=10)
        assert artifact.stat().st_mtime_ns == first_mtime_ns
        assert artifact.read_bytes() == first_bytes
    finally:
        warmer.close()


def test_original_bytes_ranges_and_headers(api_env) -> None:
    api_env.add_photo("普通 空格.jpg", size=(240, 160), color=(10, 200, 10))
    api_env.scan(api_env.register_source(name="库 一")["source_id"])
    asset_id = api_env.first_asset_id()
    payload = (api_env.library / "普通 空格.jpg").read_bytes()
    size = len(payload)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        path = f"/api/v1/assets/{asset_id}/original"
        full = await client.get(path)
        conditional = await client.get(path, headers={"If-None-Match": full.headers["ETag"]})
        head = await client.head(path)
        head_range = await client.head(path, headers={"Range": "bytes=0-9"})
        return {
            "full": full,
            "conditional": conditional,
            "head": head,
            "head_range": head_range,
            "first": await client.get(path, headers={"Range": "bytes=0-9"}),
            "tail": await client.get(path, headers={"Range": "bytes=-10"}),
            "from_middle": await client.get(path, headers={"Range": "bytes=10-"}),
            "beyond": await client.get(path, headers={"Range": f"bytes={size}-"}),
            "multi": await client.get(path, headers={"Range": "bytes=0-1,4-5"}),
            "broken": await client.get(path, headers={"Range": "bytes=abc-def"}),
            "bad_unit": await client.get(path, headers={"Range": "items=0-9"}),
        }

    result = api_env.scenario(scenario)
    full = result["full"]
    assert full.status_code == 200
    assert full.content == payload
    assert hashlib.sha256(full.content).hexdigest() == _asset_sha(api_env, asset_id)
    assert full.headers["Content-Type"] == "image/jpeg"
    assert full.headers["Accept-Ranges"] == "bytes"
    assert full.headers["Content-Length"] == str(size)
    assert full.headers["Cache-Control"] == "private, max-age=0, must-revalidate"
    assert "Last-Modified" in full.headers
    assert full.headers["ETag"].startswith(f'"{_asset_sha(api_env, asset_id)}:')

    disposition = full.headers["Content-Disposition"]
    assert disposition.startswith("inline; filename=")
    assert "filename*=UTF-8''" in disposition
    assert "/" not in disposition.split("filename*=")[0].replace("inline; ", "")
    assert "\\" not in disposition

    assert result["conditional"].status_code == 304
    assert result["head"].status_code == 200
    assert result["head"].content == b""
    assert result["head"].headers["Content-Length"] == str(size)
    assert result["head_range"].status_code == 206
    assert result["head_range"].content == b""
    assert result["head_range"].headers["Content-Range"] == f"bytes 0-9/{size}"

    assert result["first"].status_code == 206
    assert result["first"].content == payload[:10]
    assert result["first"].headers["Content-Range"] == f"bytes 0-9/{size}"
    assert result["tail"].content == payload[-10:]
    assert result["tail"].headers["Content-Range"] == f"bytes {size - 10}-{size - 1}/{size}"
    assert result["from_middle"].content == payload[10:]
    assert result["from_middle"].headers["Content-Range"] == f"bytes 10-{size - 1}/{size}"
    # An unsupported range unit is ignored, as RFC 7233 §3.1 requires.
    assert result["bad_unit"].status_code == 200
    assert result["bad_unit"].content == payload
    for refused in ("beyond", "multi", "broken"):
        response = result[refused]
        assert response.status_code == 416, refused
        assert response.json()["code"] in {"INVALID_RANGE", "RANGE_NOT_SATISFIABLE"}
        assert response.headers["Content-Range"] == f"bytes */{size}"
        assert response.content != payload


def test_large_original_is_streamed_in_chunks_with_a_matching_hash(api_env) -> None:
    encoded = io.BytesIO()
    Image.frombytes("RGB", (1000, 500), random.Random(20260912).randbytes(1_500_000)).save(
        encoded, format="BMP"
    )
    payload = encoded.getvalue()
    target = api_env.library / "big.bmp"
    target.write_bytes(payload)
    _stamp_old(target)
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        response = await client.get(f"/api/v1/assets/{asset_id}/original")
        return {"status": response.status_code, "body": response.content, "headers": response.headers}

    result = api_env.scenario(scenario)
    assert result["status"] == 200
    assert len(result["body"]) == len(payload)
    assert result["headers"]["Content-Length"] == str(len(payload))
    assert result["headers"]["Content-Type"] == "image/bmp"
    assert hashlib.sha256(result["body"]).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_traversal_rows_and_reparse_points_are_refused(api_env) -> None:
    outside = api_env.tmp_path / "outside"
    outside.mkdir()
    api_env.add_photo("photos/ok.jpg")
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    source_id = int(source["source_id"])

    def make_secret(label: str, *, name: str | None = None) -> Path:
        path = outside / (name or f"secret-{label}.jpg")
        path.write_bytes(f"secret-bytes-{label}".encode("ascii"))
        return _stamp_old(path)

    forms: dict[str, tuple[str, str, Path]] = {}
    for label, relative_path in (
        ("parent", r"..\outside\secret-parent.jpg"),
        ("parent-deep", r"..\..\outside\secret-parent-deep.jpg"),
    ):
        target = make_secret(label)
        forms[label] = (relative_path, f"evil-{label}", target)
    absolute_target = make_secret("absolute")
    forms["absolute"] = (str(absolute_target), "evil-absolute", absolute_target)
    unc_target = make_secret("unc")
    forms["unc"] = (
        r"\\localhost\c$\windows\system32\config\sam",
        "evil-unc",
        unc_target,
    )
    drive_target = make_secret("drive")
    forms["drive"] = (r"D:photos\secret.jpg", "evil-drive", drive_target)

    link = api_env.library / "linked-dir"
    junction_asset: int | None = None
    if _make_junction(link, outside):
        junction_target = make_secret("junction")
        forms["junction"] = (
            f"linked-dir\\{junction_target.name}",
            "evil-junction",
            link / junction_target.name,
        )

    injected: dict[str, int] = {}
    payloads: dict[str, bytes] = {}
    for label, (relative_path, path_key_value, target) in forms.items():
        payloads[label] = target.read_bytes()
        asset = _inject_asset(api_env.service, target)
        injected[label] = _inject_location(
            api_env.service,
            source_id=source_id,
            relative_path=relative_path,
            path_key_value=path_key_value,
            asset=asset,
        )
        if label == "junction":
            junction_asset = injected[label]

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        results: dict[str, tuple] = {}
        for label, asset_id in injected.items():
            results[label] = (
                await client.get(f"/api/v1/assets/{asset_id}/original"),
                await client.get(f"/api/v1/assets/{asset_id}/thumbnail"),
                await client.get(f"/api/v1/assets/{asset_id}"),
            )
        if junction_asset is not None:
            results["junction"] = (
                await client.get(f"/api/v1/assets/{junction_asset}/original"),
                await client.get(f"/api/v1/assets/{junction_asset}/thumbnail"),
                await client.get(f"/api/v1/assets/{junction_asset}"),
            )
        return results

    result = api_env.scenario(scenario)
    assert "junction" in result
    codes = {}
    for label, (original, thumbnail, detail) in result.items():
        assert original.status_code in (403, 409), (label, original.status_code, original.text)
        assert original.json()["code"] in {"ACCESS_DENIED", "SOURCE_FILE_MISSING"}, label
        codes[label] = original.json()["code"]
        assert payloads[label] not in original.content, label
        assert thumbnail.status_code in (403, 409), label
        assert payloads[label] not in thumbnail.content, label
        # The catalog row stays visible; only the file access is refused.
        assert detail.status_code == 200, label
        assert str(outside) not in original.text
        assert str(outside) not in thumbnail.text
    assert result["junction"][0].json()["code"] == "ACCESS_DENIED"
    # The valid asset of the same source keeps working.
    ok_asset = _asset_ids_by_name(api_env)["photos\\ok.jpg"]

    async def check_ok(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return {"status": (await client.get(f"/api/v1/assets/{ok_asset}/original")).status_code}

    assert api_env.scenario(check_ok)["status"] == 200


def test_offline_source_keeps_artifacts_and_reports_the_missing_original(api_env) -> None:
    first = api_env.add_photo("keep.jpg", size=(400, 300), color=(200, 40, 40))
    second = api_env.add_photo("gone.jpg", size=(400, 300), color=(40, 40, 200))
    source = api_env.register_source()
    api_env.scan(source["source_id"])
    ids = _asset_ids_by_name(api_env)

    async def generate(client: AsyncClient) -> int:
        await api_env.authenticate(client)
        response = await client.get(f"/api/v1/assets/{ids['keep.jpg']}/thumbnail")
        assert response.status_code == 200, response.text
        return response.status_code

    assert api_env.scenario(generate) == 200
    first.unlink()
    second.unlink()

    async def offline(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return {
            "kept": await client.get(f"/api/v1/assets/{ids['keep.jpg']}/thumbnail"),
            "gone": await client.get(f"/api/v1/assets/{ids['gone.jpg']}/thumbnail"),
            "original": await client.get(f"/api/v1/assets/{ids['gone.jpg']}/original"),
        }

    result = api_env.scenario(offline)
    assert result["kept"].status_code == 200
    assert result["kept"].headers["X-Original-Available"] == "false"
    assert result["gone"].status_code == 409
    assert result["gone"].json()["code"] == "SOURCE_FILE_MISSING"
    assert result["original"].status_code == 409
    assert result["original"].json()["code"] == "SOURCE_FILE_MISSING"

    api_env.clock.advance(1000)
    api_env.wall.advance(1000)
    api_env.scan(source["source_id"])

    async def after_rescan(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return {
            "assets": (await client.get("/api/v1/assets")).json(),
            "missing": (
                await client.get("/api/v1/assets", params={"availability": "missing"})
            ).json(),
            "media": await client.get(f"/api/v1/assets/{ids['keep.jpg']}/thumbnail"),
            "original": await client.get(f"/api/v1/assets/{ids['keep.jpg']}/original"),
            "detail": (await client.get(f"/api/v1/assets/{ids['keep.jpg']}")).json(),
        }

    final = api_env.scenario(after_rescan)
    assert len(final["assets"]["assets"]) == 2
    assert len(final["missing"]["assets"]) == 2
    assert final["media"].status_code == 200
    assert final["media"].headers["X-Original-Available"] == "false"
    assert final["original"].json()["code"] == "SOURCE_FILE_MISSING"
    assert final["detail"]["availability"] == "missing"
    assert final["detail"]["media"]["thumbnail"]["state"] == "ready"
    assert final["detail"]["media"]["preview"]["state"] == "source_missing"


def test_an_original_replaced_after_the_scan_is_a_structured_conflict(api_env) -> None:
    target = api_env.add_photo("changing.jpg", size=(320, 240), color=(90, 90, 90))
    api_env.scan(api_env.register_source()["source_id"])
    asset_id = api_env.first_asset_id()

    original_bytes = target.read_bytes()
    target.write_bytes(original_bytes + b"appended-after-the-scan")
    _stamp_old(target)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return {
            "original": await client.get(f"/api/v1/assets/{asset_id}/original"),
            "media": await client.get(f"/api/v1/assets/{asset_id}/thumbnail"),
        }

    result = api_env.scenario(scenario)
    assert result["original"].status_code == 409
    assert result["original"].json()["code"] == "INPUT_UNSTABLE"
    assert b"appended-after-the-scan" not in result["original"].content
    assert result["media"].status_code == 409
    assert result["media"].json()["code"] == "INPUT_UNSTABLE"


def test_corrupt_and_unsupported_inputs_report_recorded_codes(api_env) -> None:
    api_env.add_text_file("broken.jpg", "this is not an image at all")
    tiff = api_env.library / "real.tiff"
    Image.new("RGB", (32, 24), (5, 100, 200)).save(tiff, "TIFF")
    _stamp_old(tiff)
    api_env.scan(api_env.register_source()["source_id"])
    ids = _asset_ids_by_name(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        broken_id = ids["broken.jpg"]
        return {
            "broken": await client.get(f"/api/v1/assets/{broken_id}/thumbnail"),
            "broken_preview": await client.get(f"/api/v1/assets/{broken_id}/preview"),
            "unsupported": await client.get(f"/api/v1/assets/{ids['real.tiff']}/thumbnail"),
            "detail": (await client.get(f"/api/v1/assets/{broken_id}")).json(),
            "stats": (await client.get("/api/v1/stats")).json(),
        }

    result = api_env.scenario(scenario)
    assert result["broken"].status_code == 409
    assert result["broken"].json()["code"] == "DECODE_FAILED"
    assert result["broken"].json()["message"]
    assert result["broken_preview"].status_code == 409
    assert result["broken_preview"].json()["code"] == "DECODE_FAILED"
    assert result["unsupported"].status_code == 409
    assert result["unsupported"].json()["code"] == "FORMAT_UNSUPPORTED"
    states = result["detail"]["media"]
    assert states["thumbnail"]["state"] == "failed"
    assert states["thumbnail"]["code"] == "DECODE_FAILED"
    assert states["preview"]["state"] == "failed"
    assert result["stats"]["media"]["thumbnail"]["failed"] == 1
    assert result["stats"]["media"]["thumbnail"]["unsupported"] == 1
    # No blank image is ever served in place of a failed decode.
    assert result["broken"].headers["Content-Type"] == "application/json"
    assert result["broken"].content[:1] == b"{"


def test_failed_media_records_do_not_expose_source_paths_in_api_projections(api_env) -> None:
    """Recorded decoder errors stay useful without exposing source-root spellings."""
    api_env.add_text_file("broken.jpg", "this is not an image at all")
    tiff = api_env.library / "real.tiff"
    Image.new("RGB", (32, 24), (5, 100, 200)).save(tiff, "TIFF")
    _stamp_old(tiff)
    api_env.scan(api_env.register_source()["source_id"])
    ids = _asset_ids_by_name(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        # Generate the persisted failure records before reading their projections.
        await client.get(f"/api/v1/assets/{ids['broken.jpg']}/thumbnail")
        await client.get(f"/api/v1/assets/{ids['real.tiff']}/thumbnail")
        return {
            "detail": await client.get(f"/api/v1/assets/{ids['broken.jpg']}"),
            "assets": await client.get("/api/v1/assets"),
            "jobs": await client.get("/api/v1/jobs"),
        }

    result = api_env.scenario(scenario)
    root = str(api_env.library)
    escaped_root = root.replace("\\", "\\\\")
    for response in result.values():
        assert root not in response.text
        assert escaped_root not in response.text

    media = result["detail"].json()["media"]["thumbnail"]
    assert media["code"] in {"DECODE_FAILED", "FORMAT_UNSUPPORTED"}
    assert media["message"]


def test_unicode_space_and_long_paths_with_read_only_originals(api_env) -> None:
    root = api_env.tmp_path / "照片 库 只读"
    long_dir = root / "子 目录" / ("long-segment-0123456789" * 6)
    long_file = long_dir / "c 图片 一.JPG"
    assert len(str(long_file)) > 220
    long_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (120, 30, 200)).save(long_file, "JPEG", quality=90)
    _stamp_old(long_file)
    read_only = root / "readonly 图片.jpg"
    Image.new("RGB", (400, 300), (10, 180, 90)).save(read_only, "JPEG", quality=90)
    _stamp_old(read_only)
    os.chmod(read_only, stat.S_IREAD)
    before = _tree_state(root)
    try:
        source = api_env.service.register_source(display_name="只读 照片库", root_path=root)
        api_env.scan(source["source_id"])
        rows = api_env.service.catalog.read_all("SELECT asset_id FROM assets ORDER BY asset_id")
        asset_ids = [int(row["asset_id"]) for row in rows]
        assert len(asset_ids) == 2

        async def scenario(client: AsyncClient) -> dict:
            await api_env.authenticate(client)
            return {
                "assets": (await client.get("/api/v1/assets")).json(),
                "sources": (await client.get("/api/v1/sources")).json(),
                "media": [
                    (
                        await client.get(f"/api/v1/assets/{value}/thumbnail"),
                        await client.get(f"/api/v1/assets/{value}/preview"),
                        await client.get(f"/api/v1/assets/{value}/original"),
                    )
                    for value in asset_ids
                ],
            }

        result = api_env.scenario(scenario)
        assert result["assets"]["has_more"] is False
        assert len(result["assets"]["assets"]) == 2
        assert result["sources"]["sources"][0]["display_name"] == "只读 照片库"
        for thumbnail, preview, original in result["media"]:
            assert thumbnail.status_code == 200, thumbnail.text
            assert preview.status_code == 200
            assert original.status_code == 200, original.text
            assert _image_size(thumbnail.content) == (320, 240)
            digest = hashlib.sha256(original.content).hexdigest()
            assert original.headers["ETag"].startswith(f'"{digest}:')
        # The user's originals and their tree are untouched, including the read-only flag.
        assert _tree_state(root) == before
        assert not (read_only.stat().st_mode & stat.S_IWRITE)
    finally:
        os.chmod(read_only, stat.S_IWRITE | stat.S_IREAD)
