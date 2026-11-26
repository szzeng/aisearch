"""WebP exercises the existing JPEG and original metadata paths."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from photo_search.catalog.errors import CatalogError
from photo_search.geo import extract as geo_extract
from photo_search.media import decode
from photo_search.search import upload
from photo_search.taken_time.exif import read_time_fields


def test_webp_transparency_and_upload_keep_jpeg_strategy(tmp_path: Path) -> None:
    path = tmp_path / "alpha.webp"
    image = Image.new("RGBA", (20, 10), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (10, 0, 20, 10))
    image.save(path, "WEBP", lossless=True)
    decoded = decode.load_normalized(path, long_edge=320)
    try:
        payload = decode.render_jpeg_from_source(decoded.image, long_edge=320, quality=95)
    finally:
        decoded.image.close()
    with Image.open(io.BytesIO(payload)) as result:
        assert result.format == "JPEG"
        assert result.getpixel((2, 5)) == pytest.approx((255, 255, 255), abs=12)
        assert result.getpixel((17, 5)) == pytest.approx((255, 0, 0), abs=12)
    with upload.stage_upload(path.read_bytes()) as staged:
        assert staged.facts.format == "WEBP"
        assert (staged.facts.width, staged.facts.height) == (20, 10)


def test_webp_exif_and_gps_are_read_from_original(tmp_path: Path) -> None:
    path = tmp_path / "metadata.webp"
    exif = Image.Exif()
    exif[271] = "Camera Make"
    exif[272] = "Model"
    exif[34665] = {36867: "2022:07:05 18:31:07", 36881: "+08:00", 37521: "123"}
    exif[34853] = {
        1: "N", 2: (IFDRational(30), IFDRational(30), IFDRational(0)),
        3: "E", 4: (IFDRational(120), IFDRational(15), IFDRational(0)),
    }
    Image.new("RGB", (20, 10), "blue").save(path, "WEBP", exif=exif.tobytes())
    fields = read_time_fields(path)
    assert (fields.original, fields.original_offset, fields.original_subsec) == (
        "2022:07:05 18:31:07", "+08:00", "123"
    )
    assert fields.camera == "Camera Make Model"
    gps = geo_extract._read_exif(path)
    assert gps is not None
    assert gps[1] == "N"
    assert gps[3] == "E"
    assert tuple(float(value) for value in gps[2]) == (30, 30, 0)
    assert tuple(float(value) for value in gps[4]) == (120, 15, 0)


def test_corrupt_webp_fails_without_affecting_a_valid_decode(tmp_path: Path) -> None:
    bad = tmp_path / "broken.webp"
    bad.write_bytes(b"RIFF\x10\x00\x00\x00WEBPVP8 ")
    good = tmp_path / "good.webp"
    Image.new("RGB", (16, 8), "green").save(good, "WEBP")
    with pytest.raises(CatalogError):
        decode.load_normalized(bad, long_edge=320)
    decoded = decode.load_normalized(good, long_edge=320)
    try:
        assert decoded.image.size == (16, 8)
    finally:
        decoded.image.close()
