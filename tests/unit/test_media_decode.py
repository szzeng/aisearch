"""P1-03: EXIF orientation, format policy and JPEG rendering (architecture §5.4)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageOps
from PIL.ExifTags import Base as ExifBase

from photo_search.catalog.errors import (
    DECODE_FAILED,
    FORMAT_UNSUPPORTED,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.media import contract, decode

#: Independent EXIF-orientation ground truth: the spec's transform for each tag value,
#: written with explicit pixel operations instead of reusing production code.
ORIENTATION_TRANSFORMS = {
    1: lambda image: image,
    2: ImageOps.mirror,
    3: lambda image: image.rotate(180),
    4: ImageOps.flip,
    5: lambda image: image.transpose(Image.Transpose.TRANSPOSE),
    6: lambda image: image.transpose(Image.Transpose.ROTATE_270),
    7: lambda image: image.transpose(Image.Transpose.TRANSVERSE),
    8: lambda image: image.transpose(Image.Transpose.ROTATE_90),
}


def quadrants(width: int = 320, height: int = 200) -> Image.Image:
    """Four distinct colours plus an off-centre marker, so every mirror/rotation differs."""
    image = Image.new("RGB", (width, height))
    image.paste((230, 20, 20), (0, 0, width // 2, height // 2))
    image.paste((20, 230, 20), (width // 2, 0, width, height // 2))
    image.paste((20, 20, 230), (0, height // 2, width // 2, height))
    image.paste((235, 235, 30), (width // 2, height // 2, width, height))
    image.paste((0, 0, 0), (8, 8, 56, 40))
    return image


def oriented_jpeg(path: Path, orientation: int, image: Image.Image | None = None) -> Path:
    exif = Image.Exif()
    exif[ExifBase.Orientation] = int(orientation)
    (image or quadrants()).save(
        path, "JPEG", quality=100, subsampling=0, exif=exif.tobytes()
    )
    return path


def block_colour(image: Image.Image, centre: tuple[int, int], *, box: int = 6) -> tuple[int, int, int]:
    half = box // 2
    pixels = [
        image.getpixel((x, y))
        for x in range(centre[0] - half, centre[0] + half)
        for y in range(centre[1] - half, centre[1] + half)
    ]
    channels = list(zip(*[pixel[:3] for pixel in pixels], strict=True))
    return tuple(int(round(sum(channel) / len(channel))) for channel in channels)  # type: ignore[return-value]


def quadrant_centres(image: Image.Image) -> list[tuple[int, int]]:
    width, height = image.size
    return [
        (width // 4, height // 4),
        (3 * width // 4, height // 4),
        (width // 4, 3 * height // 4),
        (3 * width // 4, 3 * height // 4),
    ]


def test_every_exif_orientation_is_normalised_before_any_resize(tmp_path: Path) -> None:
    for orientation in range(1, 9):
        path = oriented_jpeg(tmp_path / f"orientation-{orientation}.jpg", orientation)
        with Image.open(path) as stored:
            expected = ORIENTATION_TRANSFORMS[orientation](stored.copy())

        decoded = decode.load_normalized(path, long_edge=320)

        assert decoded.orientation == orientation
        assert decoded.source_format == "JPEG"
        assert (decoded.width, decoded.height) == expected.size
        # The normalised frame is the spec transform of the stored pixels, not the stored
        # frame: this is what every later coordinate and size result builds on.
        assert list(decoded.image.getdata()) == list(expected.getdata())
        decoded.image.close()


def test_thumbnail_and_preview_honour_the_long_edge_contract(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    quadrants(4032, 3024).save(photo, "JPEG", quality=90)

    thumbnail = decode.load_normalized(photo, long_edge=contract.THUMBNAIL_LONG_EDGE)
    payload = decode.render_jpeg(
        thumbnail.image,
        long_edge=contract.THUMBNAIL_LONG_EDGE,
        quality=contract.THUMBNAIL_JPEG_QUALITY,
    )
    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.format == "JPEG"
        assert max(rendered.size) == 320
        assert rendered.size == (320, 240)

    preview = decode.load_normalized(photo, long_edge=contract.PREVIEW_LONG_EDGE)
    payload = decode.render_jpeg(
        preview.image,
        long_edge=contract.PREVIEW_LONG_EDGE,
        quality=contract.PREVIEW_JPEG_QUALITY,
    )
    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.size == (1600, 1200)


def test_images_smaller_than_the_long_edge_are_never_enlarged(tmp_path: Path) -> None:
    path = tmp_path / "small.png"
    quadrants(40, 20).save(path, "PNG")

    decoded = decode.load_normalized(path, long_edge=320)
    payload = decode.render_jpeg(decoded.image, long_edge=320, quality=85)

    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.size == (40, 20)


@pytest.mark.parametrize(
    ("name", "mode", "expected_mode"),
    [
        ("photo.png", "P", "RGB"),
        ("photo.bmp", "RGB", "RGB"),
        ("photo.gif", "P", "RGB"),
        ("photo.webp", "RGB", "RGB"),
        ("grey.png", "L", "L"),
    ],
)
def test_supported_input_formats_are_decoded(
    tmp_path: Path, name: str, mode: str, expected_mode: str
) -> None:
    path = tmp_path / name
    source = quadrants(120, 80)
    if mode == "L":
        source = source.convert("L")
    source.save(path, format=Path(name).suffix.lstrip(".").upper())

    decoded = decode.load_normalized(path, long_edge=320)
    payload = decode.render_jpeg(decoded.image, long_edge=320, quality=85)

    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.mode == expected_mode
        assert rendered.size == (120, 80)


def test_transparency_is_composited_on_white(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (80, 40), (255, 0, 0, 255))
    image.paste((0, 0, 0, 0), (0, 0, 40, 40))
    image.save(path, "PNG")

    decoded = decode.load_normalized(path, long_edge=320)
    payload = decode.render_jpeg(decoded.image, long_edge=320, quality=90)

    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.mode == "RGB"
        assert block_colour(rendered, (10, 20)) == pytest.approx((255, 255, 255), abs=6)
        assert block_colour(rendered, (60, 20)) == pytest.approx((255, 0, 0), abs=6)


def test_unverified_formats_are_reported_as_unsupported(tmp_path: Path) -> None:
    tiff = tmp_path / "scan.tif"
    quadrants(60, 40).save(tiff, "TIFF")
    # RAW families: TIFF-based (NEF/ARW/DNG/CR2) and ISO-BMFF-based (CR3) are both
    # recognised and refused instead of being decoded opportunistically.
    nef = tmp_path / "camera.nef"
    nef.write_bytes(b"II*\x00" + b"\x00" * 28)
    cr3 = tmp_path / "camera.cr3"
    cr3.write_bytes(b"\x00\x00\x00\x18ftypcrx \x00\x00\x00\x00crx isom")
    avif = tmp_path / "modern.avif"
    avif.write_bytes(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1")

    for path, label in (
        (tiff, "TIFF"),
        (avif, "AVIF"),
        (nef, "TIFF"),
        (cr3, "RAW"),
    ):
        with pytest.raises(CatalogError) as error:
            decode.load_normalized(path, long_edge=320)
        assert error.value.code == FORMAT_UNSUPPORTED
        assert error.value.details["format"] == label
        assert "JPEG" in error.value.details["supported_formats"]


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("header-only.heic", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1"),
        ("empty.heic", b""),
        ("text.heic", b"this is not a HEIC file despite the extension"),
    ],
)
def test_a_supported_format_with_broken_bytes_is_a_decode_failure(
    tmp_path: Path, name: str, payload: bytes
) -> None:
    """P4-00C-A: HEIC is decodable now, so broken HEIC bytes are a *decode* failure.

    Reporting ``FORMAT_UNSUPPORTED`` here would claim this build cannot read HEIC at all,
    which is exactly the confusion the format policy exists to prevent.
    """
    path = tmp_path / name
    path.write_bytes(payload)

    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)

    assert error.value.code == DECODE_FAILED


@pytest.mark.parametrize("kind", ["truncated", "empty", "text"])
def test_undecodable_content_is_a_decode_failure(tmp_path: Path, kind: str) -> None:
    path = tmp_path / f"{kind}.jpg"
    if kind == "truncated":
        quadrants(400, 300).save(path, "JPEG")
        payload = path.read_bytes()
        path.write_bytes(payload[: len(payload) // 2])
    elif kind == "empty":
        path.write_bytes(b"")
    else:
        path.write_bytes(b"this is not an image at all, despite the extension")

    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)

    assert error.value.code == DECODE_FAILED
    assert error.value.details["path"] == str(path)


def test_missing_source_file_is_reported_as_unavailable_not_as_a_bad_image(
    tmp_path: Path,
) -> None:
    with pytest.raises(CatalogError) as error:
        decode.load_normalized(tmp_path / "gone.jpg", long_edge=320)

    assert error.value.code == SOURCE_FILE_MISSING


def test_pixel_safety_limit_becomes_a_structured_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "huge.jpg"
    quadrants(320, 200).save(path, "JPEG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000)

    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)

    assert error.value.code == DECODE_FAILED
    assert "pixel safety limit" in error.value.message


def test_plugin_format_policy_never_guesses_from_the_extension() -> None:
    assert decode.plugin_format_decision("JPEG") == ("JPEG", True)
    assert decode.plugin_format_decision("png") == ("PNG", True)
    assert decode.plugin_format_decision("HEIF") == ("HEIF", True)
    for refused in ("TIFF", "MPO", "AVIF", "PSD"):
        label, decodable = decode.plugin_format_decision(refused)
        assert (label, decodable) == (refused, False)
    assert decode.plugin_format_decision(None) == ("UNKNOWN", False)
    assert decode.plugin_format_decision("SOMETHING-ELSE")[1] is False


def test_sniff_format_recognises_known_families() -> None:
    assert decode.sniff_format(b"\xff\xd8\xff\xe0") == "JPEG"
    assert decode.sniff_format(b"\x89PNG\r\n\x1a\n") == "PNG"
    assert decode.sniff_format(b"II*\x00rest") == "TIFF"
    assert decode.sniff_format(b"MM\x00*rest") == "TIFF"
    assert decode.sniff_format(b"\x00\x00\x00\x18ftypavif") == "AVIF"
    assert decode.sniff_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "WEBP"
    assert decode.sniff_format(b"plain text file contents") is None


def test_capability_probe_reports_measured_facts() -> None:
    probe = decode.probe_capabilities()

    assert isinstance(probe["pillow_version"], str)
    assert isinstance(probe["webp_save"], bool)
    assert isinstance(probe["libtiff_read"], bool)
    assert probe["exif_orientation_read"] is True
    assert probe["supported_input_formats"] == sorted(contract.SUPPORTED_INPUT_FORMATS)
    assert "TIFF" in probe["refused_input_formats"]
    assert "HEIC" in probe["supported_input_formats"]
    assert probe["heif_support"]["available"] is True
    assert probe["heif_support"]["module_version"]
    assert probe["heif_support"]["libheif_version"]
