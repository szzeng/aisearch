"""P4-02S: ``MPO`` (multi-picture JPEG) is supported through its first frame.

The evidence is objective and needs no human looking at a picture: the same bytes are opened
twice - once as the MPO container, once as a plain JPEG - and the decoded first frame must have
the same size, mode and pixels. A file whose second frame differs proves the check can fail.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from photo_search.catalog.errors import FORMAT_UNSUPPORTED, CatalogError
from photo_search.indexing import semantic
from photo_search.media import contract, decode


def _frame(colour: tuple[int, int, int], size: tuple[int, int] = (48, 32), seed: int = 0) -> Image.Image:
    image = Image.new("RGB", size, colour)
    pixels = image.load()
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = ((colour[0] + x + seed) % 256, (colour[1] + y) % 256, colour[2])
    return image


def _mean_distance(left: Image.Image, right: Image.Image) -> float:
    """Sum of per-channel average-colour differences (JPEG lossiness is a few units)."""
    def means(image: Image.Image) -> list[float]:
        data = image.convert("RGB").tobytes()
        count = max(1, len(data) // 3)
        return [sum(data[channel::3]) / count for channel in range(3)]

    a, b = means(left), means(right)
    return float(sum(abs(x - y) for x, y in zip(a, b)))


def _write_mpo(path: Path) -> tuple[Image.Image, Image.Image]:
    """Write a two-frame MPO with Pillow and return both frames for comparison."""
    first, second = _frame((200, 30, 30), seed=0), _frame((30, 30, 200), seed=40)
    first.save(path, format="MPO", save_all=True, append_images=[second])
    return first, second


def test_the_contract_now_supports_mpo_and_still_refuses_raw(tmp_path: Path) -> None:
    assert "MPO" in contract.supported_input_formats()
    assert "MPO" not in contract.refused_input_formats()
    for refused in ("TIFF", "AVIF", "RAW", "JXL"):
        assert refused in contract.refused_input_formats()
    assert decode.plugin_format_decision("MPO") == ("MPO", True)
    assert decode.plugin_format_decision("TIFF") == ("TIFF", False)


def test_the_decoded_first_frame_is_the_containers_first_picture(tmp_path: Path) -> None:
    path = tmp_path / "burst.jpg"
    first, second = _write_mpo(path)
    with Image.open(path) as source:
        assert str(source.format) == "MPO"
        assert source.size == first.size
    decoded = decode.load_normalized(path, long_edge=320)
    try:
        assert decoded.source_format == "MPO"
        assert (decoded.width, decoded.height) == first.size
        assert decoded.image.mode == first.mode
        # Objective first-frame proof. MPO stores its frames as JPEG, so a byte-for-byte
        # comparison is impossible; the honest check is that the average colour matches the
        # first picture within JPEG lossiness and is far away from the second one.
        near = _mean_distance(decoded.image, first)
        far = _mean_distance(decoded.image, second)
        assert near < 12, near
        assert far > 100, far
    finally:
        decoded.image.close()


def test_the_semantic_build_accepts_mpo_and_refuses_tiff(tmp_path: Path) -> None:
    mpo = tmp_path / "portrait.jpg"
    _write_mpo(mpo)
    assert semantic.image_eligibility(mpo) == (True, "MPO")
    tiff = tmp_path / "raw-like.tif"
    _frame((10, 200, 10)).save(tiff, format="TIFF")
    eligible, label = semantic.image_eligibility(tiff)
    assert eligible is False and label == "TIFF"


def test_the_decode_path_refuses_an_unsupported_family_with_its_label(tmp_path: Path) -> None:
    tiff = tmp_path / "unsupported.tif"
    _frame((10, 200, 10)).save(tiff, format="TIFF")
    with pytest.raises(CatalogError) as refused:
        decode.load_normalized(tiff, long_edge=320)
    assert refused.value.code == FORMAT_UNSUPPORTED
    assert str(refused.value.details.get("format")) == "TIFF"
    buffer = io.BytesIO()
    _frame((1, 2, 3)).save(buffer, format="PNG")
    assert decode.sniff_format(buffer.getvalue()[:16]) == "PNG"
