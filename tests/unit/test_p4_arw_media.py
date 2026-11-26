from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from photo_search.catalog.errors import CatalogError
from photo_search.geo.extract import _read_exif
from photo_search.indexing import maintenance, semantic
from photo_search.media import decode, raw_arw
from photo_search.media.face_crop import probe_original_frame
from photo_search.taken_time.exif import read_time_fields


def _sony_tiff(path: Path, *, make: bytes = b"SONY\0", tag: int = 0x7000) -> None:
    data = bytearray(120)
    struct.pack_into("<2sHI", data, 0, b"II", 42, 8)
    struct.pack_into("<H", data, 8, 3)
    struct.pack_into("<HHII", data, 10, 271, 2, len(make), 50)
    struct.pack_into("<HHII", data, 22, 272, 2, 8, 58)
    struct.pack_into("<HHII", data, 34, 330, 4, 1, 70)
    data[50:50 + len(make)] = make
    data[58:66] = b"ILCE-X\0\0"
    struct.pack_into("<H", data, 70, 2)
    struct.pack_into("<HHII", data, 72, 262, 3, 1, 32803)
    struct.pack_into("<HHII", data, 84, tag, 3, 1, 2)
    path.write_bytes(data)


class FakeRaw:
    sizes = SimpleNamespace(iwidth=40, iheight=20, flip=6)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def postprocess(self, **kwargs):
        assert kwargs["output_bps"] == 8
        return np.zeros((40, 20, 3), dtype=np.uint8)

    def open_file(self, _):
        return None

    def close(self):
        return None

    def unpack(self):
        return None


class FakeRawPy:
    ColorSpace = SimpleNamespace(sRGB="sRGB")
    RawPy = FakeRaw

    @staticmethod
    def imread(_):
        return FakeRaw()


def test_arw_requires_structure_identity_and_libraw(tmp_path: Path, monkeypatch) -> None:
    valid = tmp_path / "sample.arw"
    ordinary = tmp_path / "ordinary.arw"
    fake = tmp_path / "fake.arw"
    _sony_tiff(valid)
    _sony_tiff(ordinary, tag=0x7001)
    fake.write_bytes(b"II*\0" + bytes(40))
    monkeypatch.setattr(raw_arw.importlib, "import_module", lambda _: FakeRawPy)
    assert raw_arw.is_arw(valid)
    assert not raw_arw.is_arw(ordinary)
    assert not raw_arw.is_arw(fake)
    monkeypatch.setattr(FakeRaw, "open_file", lambda *_: (_ for _ in ()).throw(ValueError()))
    assert not raw_arw.is_arw(valid)


def test_jpeg_disguised_as_arw_is_not_accepted(tmp_path: Path) -> None:
    path = tmp_path / "fake.arw"
    Image.new("RGB", (16, 8), "red").save(path, "JPEG")
    assert not raw_arw.is_arw(path)
    assert semantic.image_eligibility(path) == (False, "ARW")
    assert decode.read_normalized_dimensions(path) is None
    with pytest.raises(CatalogError):
        decode.load_normalized(path, long_edge=320)
    with pytest.raises(CatalogError):
        probe_original_frame(path)


def test_dng_version_tag_is_rejected_even_with_sony_identity(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "other-raw.arw"
    _sony_tiff(path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<H", data, 8, 4)
    struct.pack_into("<HHII", data, 46, 50706, 1, 4, 0x00000401)
    data[58:62] = bytes(4)
    data[62:67] = b"SONY\0"
    data[67:75] = b"ILCE-X\0\0"
    struct.pack_into("<I", data, 18, 62)
    struct.pack_into("<I", data, 30, 67)
    struct.pack_into("<I", data, 42, 90)
    struct.pack_into("<H", data, 90, 2)
    struct.pack_into("<HHII", data, 92, 262, 3, 1, 32803)
    struct.pack_into("<HHII", data, 104, 0x7000, 3, 1, 2)
    path.write_bytes(data)
    monkeypatch.setattr(raw_arw.importlib, "import_module", lambda _: FakeRawPy)
    assert not raw_arw.is_arw(path)
    with pytest.raises(CatalogError):
        decode.load_normalized(path, long_edge=320)


def test_arw_decode_and_encoder_cleanup(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "sample.arw"
    _sony_tiff(path)
    monkeypatch.setattr(raw_arw.importlib, "import_module", lambda _: FakeRawPy)
    assert semantic.image_eligibility(path) == (True, "ARW")
    assert probe_original_frame(path) == (20, 40)
    image = decode.load_normalized(path, long_edge=320)
    try:
        assert (image.width, image.height, image.source_format) == (20, 40, "ARW")
        assert image.image.mode == "RGB"
    finally:
        image.image.close()
    with pytest.raises(RuntimeError):
        with raw_arw.encoder_input(path, temp_root=tmp_path) as converted:
            with Image.open(converted) as output:
                assert output.format == "JPEG"
            raise RuntimeError("encoder failed")
    assert not list((tmp_path / "tmp" / "arw-encoder").glob("*.jpg"))
    with monkeypatch.context() as limits:
        unpack_calls = []
        limits.setattr(FakeRaw, "unpack", lambda _: unpack_calls.append(1))
        with pytest.raises(CatalogError):
            raw_arw.decode(path, max_pixels=100)
        assert unpack_calls == []

    class Encoder:
        def encode_image(self, filename):
            assert Path(filename).is_file()
            assert filename != str(path)
            return [0.25, 0.75]

    assert semantic._validated_image_vector(
        Encoder(), path=path, dimension=2, temp_root=tmp_path
    ) == [0.25, 0.75]
    assert maintenance._validated_image_vector(
        Encoder(), path=path, dimension=2, temp_root=tmp_path, artifact_id="test"
    ) == [0.25, 0.75]
    assert not list((tmp_path / "tmp" / "arw-encoder").glob("*.jpg"))


def test_arw_metadata_uses_original_container(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "metadata.arw"
    exif = Image.Exif()
    exif[271], exif[272] = "SONY", "ILCE-TEST"
    exif[34665] = {36867: "2022:07:05 18:31:07", 36881: "+08:00", 37521: "123"}
    exif[34853] = {
        1: "N", 2: (IFDRational(30), IFDRational(30), IFDRational(0)),
        3: "E", 4: (IFDRational(120), IFDRational(15), IFDRational(0)),
    }
    path.write_bytes(exif.tobytes()[6:])
    monkeypatch.setattr(raw_arw, "is_arw", lambda _: True)
    fields = read_time_fields(path)
    assert (fields.original, fields.original_offset, fields.original_subsec) == (
        "2022:07:05 18:31:07", "+08:00", "123"
    )
    assert fields.camera == "SONY ILCE-TEST"
    gps = _read_exif(path)
    assert gps is not None and gps[1] == "N" and gps[3] == "E"


def test_face_and_ocr_decode_share_arw_frame(tmp_path: Path, monkeypatch) -> None:
    from photo_search.face import processor as face_processor
    from photo_search.ocr import processor as ocr_processor

    path = tmp_path / "sample.arw"
    _sony_tiff(path)
    monkeypatch.setattr(raw_arw.importlib, "import_module", lambda _: FakeRawPy)
    for decoded in (face_processor._decode(path), ocr_processor._decode(path)):
        try:
            assert (decoded.width, decoded.height, decoded.source_format) == (20, 40, "ARW")
        finally:
            decoded.image.close()
