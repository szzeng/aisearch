"""P3-06: the server-side face crop endpoint, driven through the real API.

The endpoint is the one place where the page receives *pixels*. These cases check that the
crop really comes from the authoritative box of that one face, that it is bounded and never
enlarged, that the photo's other faces are untouched, and that every refusal stays a stable
code without a local path. Everything is synthetic and lives under ``%TEMP%``.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from conftest import stamp
from httpx import AsyncClient
from p3_persons_page_fixtures import insert_face, mark_location_missing, seed_library
from PIL import Image, ImageOps

JPEG_MAGIC = b"\xff\xd8\xff"


def write_two_tone(path: Path, *, size: tuple[int, int] = (320, 240)) -> Path:
    """A photo whose left half is red and right half is blue, so a crop proves its region."""
    image = Image.new("RGB", size, (255, 0, 0))
    pixels = image.load()
    for x in range(size[0] // 2, size[0]):
        for y in range(size[1]):
            pixels[x, y] = (0, 0, 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=95)
    return stamp(path)


def mean_channels(payload: bytes) -> tuple[float, float]:
    with Image.open(io.BytesIO(payload)) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        red = 0
        blue = 0
        for x in range(width):
            for y in range(height):
                r, _g, b = rgb.getpixel((x, y))
                red += r
                blue += b
        total = float(width * height)
        return red / total, blue / total


def seeded_two_faces(env: Any) -> dict[str, Any]:
    """One two-tone photo with one face on the red half and one on the blue half."""
    write_two_tone(env.library / "双色.jpg")
    source = env.register_source("library")
    env.scan(int(source["source_id"]))
    row = env.service.catalog.read_one(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.location_id AS location_id "
        "  FROM assets AS a JOIN file_locations AS l ON l.asset_id = a.asset_id "
        " WHERE l.availability = 'PRESENT'"
    )
    left = insert_face(
        env.service.catalog,
        asset_id=int(row["asset_id"]),
        sha256=str(row["sha256"]),
        bbox=(20, 60, 100, 140),
        ordinal=0,
        location_id=int(row["location_id"]),
    )
    right = insert_face(
        env.service.catalog,
        asset_id=int(row["asset_id"]),
        sha256=str(row["sha256"]),
        bbox=(220, 60, 300, 140),
        ordinal=1,
        location_id=int(row["location_id"]),
    )
    return {
        "asset_id": int(row["asset_id"]),
        "sha256": str(row["sha256"]),
        "left": left,
        "right": right,
    }


def test_the_crop_comes_from_that_faces_own_box(api_env) -> None:
    seeded = seeded_two_faces(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        left = await client.get(f"/api/v1/faces/{seeded['left']}/thumbnail")
        right = await client.get(f"/api/v1/faces/{seeded['right']}/thumbnail")
        again = await client.get(
            f"/api/v1/faces/{seeded['left']}/thumbnail",
            headers={"If-None-Match": left.headers["ETag"]},
        )
        head = await client.head(f"/api/v1/faces/{seeded['left']}/thumbnail")
        return {"left": left, "right": right, "again": again, "head": head}

    result = api_env.scenario(scenario)
    left = result["left"]
    assert left.status_code == 200, left.text
    assert left.headers["Content-Type"] == "image/jpeg"
    assert left.content.startswith(JPEG_MAGIC)
    assert "immutable" in left.headers["Cache-Control"]
    assert left.headers["ETag"].startswith('"face-crop@1:')
    assert int(left.headers["Content-Length"]) == len(left.content)

    # Bounded: at most 256 px on the long edge, and never an upscale of a smaller crop.
    with Image.open(io.BytesIO(left.content)) as image:
        assert max(image.size) <= 256
        assert image.size[0] > 0 and image.size[1] > 0

    # The left face is on the red half and the right face on the blue half: the crop really
    # comes from this face's own box, and the photo's other face is not folded in.
    left_red, left_blue = mean_channels(left.content)
    right = result["right"]
    assert right.status_code == 200, right.text
    right_red, right_blue = mean_channels(right.content)
    assert left_red > left_blue, (left_red, left_blue)
    assert right_blue > right_red, (right_red, right_blue)
    assert left.content != right.content

    # A published crop is reused, and a conditional request answers 304 with the same tag.
    assert result["again"].status_code == 304
    assert result["again"].headers["ETag"] == left.headers["ETag"]
    assert result["head"].status_code == 200
    assert result["head"].headers["ETag"] == left.headers["ETag"]
    assert result["head"].content == b""

    # Two faces produced two cached crops inside the rebuildable cache of the data root.
    crops = sorted((api_env.data_root / "cache" / "face-crops" / "face-crop@1").rglob("*.jpg"))
    assert len(crops) == 2
    assert any(path.read_bytes() == left.content for path in crops)
    # The frozen preview it was rendered from really exists as well.
    previews = sorted(
        (api_env.data_root / "previews" / "catalog-preview@1").rglob("*.jpg")
    )
    assert len(previews) == 1


def test_a_small_face_is_not_enlarged(api_env) -> None:
    seeded = seed_library(api_env, {"小图.jpg": [(40, 30, 60, 50)]}, size=(120, 90))
    face_id = seeded["小图.jpg"]["faces"][0]

    async def scenario(client: AsyncClient) -> Any:
        await api_env.authenticate(client)
        return await client.get(f"/api/v1/faces/{face_id}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 200, response.text
    with Image.open(io.BytesIO(response.content)) as image:
        # A 20 px face in a 120x90 preview: 20 * 1.7 (the margin) = 34 px, and the box is
        # rounded outward, so the crop keeps its real extent instead of being blown up to the
        # 256 px bound.
        assert image.size == (34, 34), image.size


def test_a_rotated_photo_is_cropped_in_its_upright_frame(api_env) -> None:
    """The stored box is in the *upright* frame, so the crop has to follow the EXIF rotation.

    The stored photo is 200x100 with orientation 6 (displayed 100x200), its stored top half
    red and bottom half blue. Which side of the *upright* frame ends up red is read from
    Pillow's own ``exif_transpose`` instead of being asserted from a hand-derived rotation, so
    this case checks the frame mapping rather than my arithmetic.
    """
    path = api_env.library / "旋转.jpg"
    image = Image.new("RGB", (200, 100), (255, 0, 0))
    pixels = image.load()
    for x in range(200):
        for y in range(50, 100):
            pixels[x, y] = (0, 0, 255)
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(path, "JPEG", quality=95, exif=exif)
    stamp(path)
    source = api_env.register_source("library")
    api_env.scan(int(source["source_id"]))
    row = api_env.service.catalog.read_one(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256, l.location_id AS location_id "
        "  FROM assets AS a JOIN file_locations AS l ON l.asset_id = a.asset_id "
        " WHERE l.availability = 'PRESENT'"
    )
    left = insert_face(
        api_env.service.catalog,
        asset_id=int(row["asset_id"]),
        sha256=str(row["sha256"]),
        # A 20 px box well inside the upright frame's left half: with the frozen margin the
        # square crop (34 px) stays inside one half, so the colour it should show is
        # unambiguous.
        bbox=(10, 90, 30, 110),
        ordinal=0,
        location_id=int(row["location_id"]),
    )
    right = insert_face(
        api_env.service.catalog,
        asset_id=int(row["asset_id"]),
        sha256=str(row["sha256"]),
        bbox=(70, 90, 90, 110),
        ordinal=1,
        location_id=int(row["location_id"]),
    )

    with Image.open(path) as opened:
        upright = ImageOps.exif_transpose(opened).convert("RGB")
    assert upright.size == (100, 200)

    def dominant(image: Image.Image, *, left_half: bool) -> str:
        width, height = image.size
        xs = range(0, width // 2) if left_half else range(width // 2, width)
        red = blue = 0
        for x in xs:
            for y in range(height):
                r, _g, b = image.getpixel((x, y))
                red += r
                blue += b
        return "red" if red > blue else "blue"

    expected_left = dominant(upright, left_half=True)
    expected_right = dominant(upright, left_half=False)
    assert expected_left != expected_right

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        return {
            "left": await client.get(f"/api/v1/faces/{left}/thumbnail"),
            "right": await client.get(f"/api/v1/faces/{right}/thumbnail"),
        }

    result = api_env.scenario(scenario)
    for key in ("left", "right"):
        assert result[key].status_code == 200, (key, result[key].text)
    with Image.open(io.BytesIO(result["left"].content)) as crop:
        assert dominant(crop.convert("RGB"), left_half=True) == expected_left
    with Image.open(io.BytesIO(result["right"].content)) as crop:
        # A whole avatar crop is inside one half, so "the left half of the crop" is still the
        # single colour of that face's region.
        assert dominant(crop.convert("RGB"), left_half=True) == expected_right


def test_an_offline_source_is_a_stable_refusal(api_env) -> None:
    seeded = seed_library(api_env, {"离线.jpg": [(20, 20, 80, 80)]})
    face_id = seeded["离线.jpg"]["faces"][0]
    mark_location_missing(api_env.service.catalog, seeded["离线.jpg"]["asset_id"])

    async def scenario(client: AsyncClient) -> Any:
        await api_env.authenticate(client)
        return await client.get(f"/api/v1/faces/{face_id}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "SOURCE_FILE_MISSING"
    assert response.headers["X-Request-ID"]
    assert "C:\\" not in response.text and "D:\\" not in response.text
    # Nothing was published for an unusable source.
    crops = list((api_env.data_root / "cache" / "face-crops").rglob("*.jpg"))
    assert crops == []


def test_a_missing_face_is_a_structured_404(api_env) -> None:
    seeded_two_faces(api_env)

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        unknown = await client.get("/api/v1/faces/" + "b" * 64 + "/thumbnail")
        return {"unknown": unknown}

    result = api_env.scenario(scenario)
    unknown = result["unknown"]
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "FACE_NOT_FOUND"
    assert unknown.headers["Content-Type"].startswith("application/json")


def test_bad_media_is_reported_without_a_crop_or_a_path(api_env) -> None:
    # A one-byte "photo": the scan records it, the media pipeline can never decode it.
    (api_env.library).mkdir(parents=True, exist_ok=True)
    corrupt = api_env.library / "corrupt.jpg"
    corrupt.write_bytes(b"\xff")
    stamp(corrupt)
    source = api_env.register_source("library")
    api_env.scan(int(source["source_id"]))
    row = api_env.service.catalog.read_one(
        "SELECT a.asset_id AS asset_id, a.sha256 AS sha256 FROM assets AS a "
        "JOIN file_locations AS l ON l.asset_id = a.asset_id WHERE l.availability = 'PRESENT'"
    )
    face_id = insert_face(
        api_env.service.catalog,
        asset_id=int(row["asset_id"]),
        sha256=str(row["sha256"]),
        bbox=(4, 4, 20, 20),
        ordinal=0,
    )

    async def scenario(client: AsyncClient) -> Any:
        await api_env.authenticate(client)
        return await client.get(f"/api/v1/faces/{face_id}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] in ("DECODE_FAILED", "FORMAT_UNSUPPORTED"), body
    assert str(api_env.library) not in response.text
    assert "C:\\" not in response.text and "D:\\" not in response.text
    assert list((api_env.data_root / "cache" / "face-crops").rglob("*.jpg")) == []


def test_a_broken_geometry_row_is_refused_instead_of_guessed(api_env) -> None:
    seeded = seed_library(api_env, {"坏框.jpg": [(20, 20, 80, 80)]})
    broken = "c" * 64
    with api_env.service.catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'face-buffalo-l-scrfd-arcface-v1', 'det', 7, ?, NULL, '[1,2,3]', "
            "'[[1,2]]', 0.9, 100.0, 200.0, 'OK', NULL, 'artifact', 1, 1, 1)",
            (
                broken,
                seeded["坏框.jpg"]["asset_id"],
                seeded["坏框.jpg"]["sha256"],
            ),
        )

    async def scenario(client: AsyncClient) -> Any:
        await api_env.authenticate(client)
        return await client.get(f"/api/v1/faces/{broken}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "FACE_STATE_CONFLICT"
    assert body["details"]["reason"] == "bbox_arity"
    assert list((api_env.data_root / "cache" / "face-crops").rglob("*.jpg")) == []


def test_the_crop_endpoint_needs_a_session(api_env) -> None:
    seeded = seeded_two_faces(api_env)

    async def scenario(client: AsyncClient) -> Any:
        return await client.get(f"/api/v1/faces/{seeded['left']}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_REQUIRED"
