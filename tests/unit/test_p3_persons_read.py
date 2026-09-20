"""Pure-function tests for the P3-06 read model and its face-crop geometry.

No database, no API and no model: these cases pin the cursor token spaces, the bounding-box
validation and the frame mapping the crop depends on, so a later change has to say out loud
that it moved one of them.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from conftest import write_jpeg
from PIL import Image

from photo_search.api.errors import ApiError
from photo_search.catalog.errors import FACE_STATE_CONFLICT
from photo_search.media.contract import FACE_CROP_LONG_EDGE, FACE_CROP_MARGIN
from photo_search.media.face_crop import (
    FaceBox,
    crop_box,
    crop_key,
    normalize_original_size,
    parse_bbox,
    probe_original_frame,
    render_crop,
)
from photo_search.persons.read import (
    clamp_page_limit,
    decode_face_cursor,
    decode_operation_cursor,
    decode_person_cursor,
    encode_face_cursor,
    encode_operation_cursor,
    encode_person_cursor,
)


def _blob(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")


# --------------------------------------------------------------------------------- cursors
def test_the_three_cursor_spaces_round_trip() -> None:
    assert decode_person_cursor(encode_person_cursor(7)) == 7
    assert decode_operation_cursor(encode_operation_cursor(12)) == 12
    face_id = "a" * 64
    assert decode_face_cursor(encode_face_cursor(face_id)) == face_id
    for decode in (decode_person_cursor, decode_operation_cursor, decode_face_cursor):
        assert decode(None) is None
        assert decode("") is None


def test_a_cursor_never_crosses_a_list() -> None:
    person_cursor = encode_person_cursor(7)
    face_cursor = encode_face_cursor("a" * 64)
    operation_cursor = encode_operation_cursor(3)
    assert len({person_cursor, face_cursor, operation_cursor}) == 3
    with pytest.raises(ApiError) as failure:
        decode_face_cursor(person_cursor)
    assert failure.value.code == "INVALID_CURSOR"
    with pytest.raises(ApiError) as failure:
        decode_person_cursor(operation_cursor)
    assert failure.value.code == "INVALID_CURSOR"
    with pytest.raises(ApiError) as failure:
        decode_operation_cursor(face_cursor)
    assert failure.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!!",
        _blob("pp1:"),
        _blob("pp1:1:2"),
        _blob("pp1:-1"),
        _blob("pp1:x"),
        _blob("pf1:" + "z" * 64),
        _blob("pf1:" + "a" * 63),
        _blob("po1:0"),
    ],
)
def test_malformed_cursors_are_refused(cursor: str) -> None:
    for decode in (decode_person_cursor, decode_face_cursor, decode_operation_cursor):
        with pytest.raises(ApiError) as failure:
            decode(cursor)
        assert failure.value.code == "INVALID_CURSOR"
        assert failure.value.status == 422


def test_the_page_limit_is_clamped_and_bounded() -> None:
    assert clamp_page_limit(None) == 50
    assert clamp_page_limit(10) == 10
    assert clamp_page_limit(5000) == 200
    with pytest.raises(ApiError) as failure:
        clamp_page_limit(0)
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.status == 422


# ---------------------------------------------------------------------------- face geometry
def test_a_stored_box_is_validated_and_never_guessed() -> None:
    box = parse_bbox("[10.0, 20.0, 60.0, 90.0]")
    assert (box.left, box.top, box.right, box.bottom) == (10.0, 20.0, 60.0, 90.0)
    assert box.width == 50.0 and box.height == 70.0
    for raw, reason in (
        ("not json", "bbox_not_json"),
        ("[1, 2, 3]", "bbox_arity"),
        ('["a", 2, 3, 4]', "bbox_not_numeric"),
        ("[20, 20, 10, 10]", "bbox_degenerate"),
    ):
        with pytest.raises(Exception) as failure:
            parse_bbox(raw)
        assert failure.value.code == FACE_STATE_CONFLICT
        assert failure.value.details["reason"] == reason


def test_the_original_frame_follows_the_exif_orientation(tmp_path: Path) -> None:
    upright = write_jpeg(tmp_path / "upright.jpg", size=(320, 240))
    assert probe_original_frame(upright) == (320, 240)
    for orientation in (5, 6, 7, 8):
        rotated = write_jpeg(
            tmp_path / f"rotated-{orientation}.jpg", size=(320, 240), orientation=orientation
        )
        # The face boxes are stored in the *upright* frame, so the probe has to swap the two
        # numbers for exactly the orientations that rotate by 90 degrees.
        assert probe_original_frame(rotated) == (240, 320)
    assert normalize_original_size((320, 240), 1) == (320, 240)
    assert normalize_original_size((320, 240), 3) == (320, 240)
    assert normalize_original_size((320, 240), 6) == (240, 320)


def test_the_crop_box_is_margin_sized_square_and_clamped() -> None:
    box = FaceBox(left=100.0, top=100.0, right=200.0, bottom=200.0)
    left, top, right, bottom = crop_box(box, source_size=(400, 300), target_size=(400, 300))
    # A 100 px face with the 0.35 margin is a 170 px square, rounded outward by at most a
    # pixel on each side.
    full_size = right - left
    assert 170 <= full_size <= 172
    assert 170 <= bottom - top <= 172
    assert left < 100 and top < 100

    # A face at the border is slid inside the frame instead of producing an out-of-range box.
    border = FaceBox(left=0.0, top=0.0, right=40.0, bottom=40.0)
    left, top, right, bottom = crop_box(border, source_size=(400, 300), target_size=(400, 300))
    assert left == 0 and top == 0
    assert right <= 400 and bottom <= 300
    assert right - left == bottom - top

    # The preview frame is the source frame scaled, so the factor is the width ratio.
    scaled = crop_box(box, source_size=(400, 300), target_size=(200, 150))
    assert scaled[2] - scaled[0] == pytest.approx(full_size / 2, abs=2)


def test_the_crop_key_covers_face_geometry_and_content() -> None:
    box = FaceBox(left=1.0, top=2.0, right=30.0, bottom=40.0)
    base = crop_key(face_id="a" * 64, box=box, sha256="b" * 64)
    assert len(base) == 64
    assert crop_key(face_id="a" * 64, box=box, sha256="b" * 64) == base
    assert crop_key(face_id="c" * 64, box=box, sha256="b" * 64) != base
    assert crop_key(face_id="a" * 64, box=box, sha256="d" * 64) != base
    moved = FaceBox(left=5.0, top=2.0, right=30.0, bottom=40.0)
    assert crop_key(face_id="a" * 64, box=moved, sha256="b" * 64) != base


def test_rendering_a_crop_takes_only_the_face_region(tmp_path: Path) -> None:
    # A 400x300 preview whose left half is red and right half is blue, rendered by the very
    # same encoder the media pipeline uses (plain JPEG bytes on disk).
    frame = Image.new("RGB", (400, 300), (255, 0, 0))
    pixels = frame.load()
    for x in range(200, 400):
        for y in range(300):
            pixels[x, y] = (0, 0, 255)
    preview = tmp_path / "preview.jpg"
    frame.save(preview, "JPEG", quality=95)

    box = FaceBox(left=40.0, top=100.0, right=140.0, bottom=200.0)
    payload = render_crop(preview, box=box, source_size=(400, 300))
    with Image.open(io.BytesIO(payload)) as image:
        assert max(image.size) <= FACE_CROP_LONG_EDGE
        rgb = image.convert("RGB")
        red = blue = 0
        for x in range(rgb.size[0]):
            for y in range(rgb.size[1]):
                r, _g, b = rgb.getpixel((x, y))
                red += r
                blue += b
    assert red > blue * 3, (red, blue)
    # A 100 px face with the frozen margin is a 170 px crop, never an upscaled 256 px one.
    assert image.size[0] == pytest.approx(100 * (1 + 2 * FACE_CROP_MARGIN), abs=2)
