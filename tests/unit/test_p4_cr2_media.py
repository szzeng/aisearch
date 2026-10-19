from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from photo_search.catalog.errors import DECODE_FAILED, CatalogError
from photo_search.indexing import semantic
from photo_search.media import raw_cr2
from photo_search.media.decode import load_normalized
from photo_search.media.face_crop import probe_original_frame


class _FakeRaw:
    sizes = SimpleNamespace(iwidth=40, iheight=20, flip=6)

    def __enter__(self) -> _FakeRaw:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def postprocess(self, **_: object) -> np.ndarray:
        return np.zeros((40, 20, 3), dtype=np.uint8)


class _FakeRawPy:
    ColorSpace = SimpleNamespace(sRGB="sRGB")

    @staticmethod
    def imread(_: str) -> _FakeRaw:
        return _FakeRaw()


def _write_cr2_header(path) -> None:
    path.write_bytes(b"II*\x00\x08\x00\x00\x00CR\x02\x00" + bytes(16))


def test_cr2_detection_requires_container_magic_not_extension(tmp_path) -> None:
    impostor = tmp_path / "not-raw.cr2"
    impostor.write_bytes(b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00" + bytes(12))
    valid = tmp_path / "named-anything.bin"
    _write_cr2_header(valid)

    assert not raw_cr2.is_cr2(impostor)
    assert raw_cr2.is_cr2(valid)
    with pytest.raises(CatalogError) as failure:
        raw_cr2.decode(impostor)
    assert failure.value.code == DECODE_FAILED


def test_cr2_raw_decode_is_oriented_rgb_and_dimension_probe_is_header_only(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "sample.CR2"
    _write_cr2_header(source)
    monkeypatch.setattr(raw_cr2.importlib, "import_module", lambda _: _FakeRawPy)

    image, source_width, source_height = raw_cr2.decode(source)
    try:
        assert image.mode == "RGB"
        assert image.size == (20, 40)
        assert (source_width, source_height) == (40, 20)
    finally:
        image.close()
    assert raw_cr2.read_normalized_dimensions(source) == (20, 40)
    assert probe_original_frame(source) == (20, 40)
    decoded = load_normalized(source, long_edge=320)
    assert (decoded.width, decoded.height, decoded.orientation, decoded.source_format) == (
        20,
        40,
        1,
        "CR2",
    )
    decoded.image.close()


def test_semantic_eligibility_is_cr2_header_and_runtime_gated(tmp_path, monkeypatch) -> None:
    source = tmp_path / "raw.cr2"
    _write_cr2_header(source)
    monkeypatch.setattr(raw_cr2, "runtime_available", lambda: True)
    assert semantic.image_eligibility(source) == (True, "CR2")

    monkeypatch.setattr(raw_cr2, "runtime_available", lambda: False)
    assert semantic.image_eligibility(source) == (False, "CR2")

    impostor = tmp_path / "impostor.cr2"
    impostor.write_bytes(b"not a raw image")
    assert semantic.image_eligibility(impostor) == (False, "UNIDENTIFIED")


def test_encoder_input_cleans_temporary_cr2_conversion_after_failure(tmp_path, monkeypatch) -> None:
    source = tmp_path / "sample.cr2"
    _write_cr2_header(source)
    monkeypatch.setattr(raw_cr2.importlib, "import_module", lambda _: _FakeRawPy)
    root = tmp_path / "app-data"

    with pytest.raises(RuntimeError):
        with raw_cr2.encoder_input(source, temp_root=root) as converted:
            assert converted.exists()
            raise RuntimeError("encoder refused")
    assert list((root / "tmp" / "cr2-encoder").glob("*.jpg")) == []


def test_semantic_encoder_receives_canonical_temp_and_it_is_removed(tmp_path, monkeypatch) -> None:
    source = tmp_path / "sample.cr2"
    _write_cr2_header(source)
    monkeypatch.setattr(raw_cr2.importlib, "import_module", lambda _: _FakeRawPy)

    class Encoder:
        def encode_image(self, path: str) -> list[float]:
            assert path != str(source)
            assert Path(path).is_file()
            return [0.25, 0.75]

    root = tmp_path / "app-data"
    vector = semantic._validated_image_vector(
        Encoder(), path=source, dimension=2, temp_root=root
    )
    assert vector == [0.25, 0.75]
    assert list((root / "tmp" / "cr2-encoder").glob("*.jpg")) == []


def test_continue_build_retries_only_cr2_format_unsupported_cache(tmp_path) -> None:
    from photo_search.refresh.pipeline import _registered_cr2_format_failures

    valid = tmp_path / "valid.cr2"
    impostor = tmp_path / "impostor.cr2"
    _write_cr2_header(valid)
    impostor.write_bytes(b"not cr2")

    class Catalog:
        def read_all(self, *_):
            return [
                {"asset_id": 1, "sha256": "sha-1", "relative_path": "valid.cr2", "root_path": str(tmp_path)},
                {"asset_id": 2, "sha256": "sha-2", "relative_path": "impostor.cr2", "root_path": str(tmp_path)},
            ]

    class Cache:
        def artifact_path(self, *, spec, sha256):
            return Path(f"{sha256}-{spec.kind}.jpg")

        def is_published(self, _path):
            return False

        def read_failure(self, *, spec, sha256):
            if sha256 == "sha-1" and spec.kind == "thumbnail":
                return {"code": "FORMAT_UNSUPPORTED"}
            if sha256 == "sha-2" and spec.kind == "preview":
                return {"code": "FORMAT_UNSUPPORTED"}
            if sha256 == "sha-1" and spec.kind == "preview":
                return {"code": "DECODE_FAILED"}
            if sha256 == "sha-2" and spec.kind == "thumbnail":
                return {
                    "code": "FORMAT_UNSUPPORTED",
                    "cr2_format_retry": {"attempted": True},
                }
            return None

    service = SimpleNamespace(catalog=Catalog(), media_cache=lambda: Cache())
    assert _registered_cr2_format_failures(service, [1, 2]) == {
        "thumbnail": [1],
        "preview": [],
    }
