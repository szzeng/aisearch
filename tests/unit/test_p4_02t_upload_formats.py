"""P4-02T: the upload-query whitelist covers the formats the library already indexes.

Three questions, none of which needs a model, a GPU or a real photo:

* is the whitelist still an explicit, reviewable list - and is it exactly the verified set?
* does an MPO upload take the container's **first frame**, like the grid and the index do?
* does a rotated HEIC upload get the same orientation normalisation as the library path,
  instead of being encoded sideways with no error at all?

Fixtures come from ``tests/p4_heic_fixtures.py`` (the pinned pillow-heif encoder) and are
synthetic, so nothing here reads a real photo library.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from p4_heic_fixtures import block_colour, quadrants, write_exif_heic, write_plain_heic
from PIL import Image, ImageOps, ImageStat

from photo_search.catalog.errors import DECODE_FAILED, FORMAT_UNSUPPORTED, CatalogError
from photo_search.media import contract, heif
from photo_search.search import upload

#: Independent ground truth for the two EXIF orientations that swap the dimensions.
ORIENTATION_TRANSFORMS = {
    1: lambda image: image,
    6: lambda image: image.transpose(Image.Transpose.ROTATE_270),
    8: lambda image: image.transpose(Image.Transpose.ROTATE_90),
}
#: The explicit set P4-02T publishes: every family the library decodes, RAW excluded.
VERIFIED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "GIF", "MPO", "HEIC", "HEIF"})


def _mean(image: Image.Image) -> list[float]:
    return list(ImageStat.Stat(image.convert("RGB")).mean)


def test_the_whitelist_is_the_explicit_verified_set() -> None:
    assert upload.UPLOAD_INPUT_FORMATS == VERIFIED_FORMATS
    # Still a literal list, and now exactly the media contract's refused set - no HEIF
    # special case left behind.
    assert upload.upload_refused_families() == contract.refused_input_formats()
    assert not (upload.UPLOAD_INPUT_FORMATS & upload.upload_refused_families())
    for refused in ("TIFF", "AVIF", "JXL"):
        assert refused in upload.upload_refused_families()
        assert refused not in upload.UPLOAD_INPUT_FORMATS


def test_an_mpo_upload_is_decoded_from_its_first_frame(tmp_path: Path) -> None:
    first = Image.new("RGB", (64, 48), (220, 30, 30))
    second = Image.new("RGB", (64, 48), (30, 30, 220))
    path = tmp_path / "burst.jpg"
    first.save(path, format="MPO", save_all=True, append_images=[second])

    with upload.stage_upload(path.read_bytes()) as staged:
        assert staged.facts.format == "MPO"
        assert (staged.facts.width, staged.facts.height) == first.size
        # The second frame is a different colour, so decoding the wrong frame cannot pass.
        staged_mean = _mean(staged.image)
        assert staged_mean == pytest.approx(_mean(first), abs=12)
        assert abs(staged_mean[2] - _mean(second)[2]) > 150


@pytest.mark.parametrize("orientation", [6, 8])
@pytest.mark.parametrize("container_orientation", [True, False])
def test_a_rotated_heic_upload_is_decoded_upright(
    tmp_path: Path, orientation: int, container_orientation: bool
) -> None:
    stored = quadrants(320, 200)
    expected = ORIENTATION_TRANSFORMS[orientation](stored)
    path = write_exif_heic(
        tmp_path / f"upload-{orientation}-{container_orientation}.heic",
        orientation=orientation,
        container_orientation=container_orientation,
        image=stored,
    )

    with upload.stage_upload(path.read_bytes()) as staged:
        assert staged.facts.format == "HEIF"
        assert (staged.facts.width, staged.facts.height) == expected.size
        width, height = staged.image.size
        centres = [
            (width // 4, height // 4),
            (3 * width // 4, height // 4),
            (width // 4, 3 * height // 4),
            (3 * width // 4, 3 * height // 4),
        ]
        for centre in centres:
            assert block_colour(staged.image, centre) == pytest.approx(
                expected.getpixel(centre)[:3], abs=24
            )


def test_the_exif_only_heic_really_needs_the_restoration(tmp_path: Path) -> None:
    """Control: the fixture is genuinely stored sideways, so the test above can fail."""
    stored = quadrants(320, 200)
    path = write_exif_heic(
        tmp_path / "control.heic",
        orientation=6,
        container_orientation=False,
        image=stored,
    )
    assert heif.ensure_heif_support() is True
    with Image.open(path) as source:
        naive = ImageOps.exif_transpose(source)
        assert naive.size == (320, 200)
    naive.close()
    assert ORIENTATION_TRANSFORMS[6](stored).size == (200, 320)


def test_a_broken_heic_upload_is_a_decode_failure_not_a_format_statement(tmp_path: Path) -> None:
    complete = write_plain_heic(tmp_path / "complete.heic", size=(32, 24)).read_bytes()
    broken = tmp_path / "broken.heic"
    broken.write_bytes(complete[:64])

    with pytest.raises(CatalogError) as error:
        with upload.stage_upload(broken.read_bytes()):
            pass  # pragma: no cover - refused during validation
    # The family is supported now, so a damaged member of it is a decode failure - never a
    # bogus "this build does not support HEIC".
    assert error.value.code == DECODE_FAILED


def test_the_refused_families_are_still_refused_for_uploads(tmp_path: Path) -> None:
    tiff = tmp_path / "photo.tif"
    quadrants(64, 48).save(tiff, "TIFF")

    with pytest.raises(CatalogError) as error:
        with upload.stage_upload(tiff.read_bytes()):
            pass  # pragma: no cover - refused during validation
    assert error.value.code == FORMAT_UNSUPPORTED
    assert error.value.details["format"] == "TIFF"
    assert error.value.details["supported_formats"] == sorted(VERIFIED_FORMATS)