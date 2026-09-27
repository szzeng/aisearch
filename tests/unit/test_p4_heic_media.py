"""P4-00C-A: HEIC/HEIF inside the frozen P1-03 media path (business layer only).

Every fixture is synthetic and lives under pytest's ``tmp_path``; the pinned pillow-heif
encoder writes it, so no test here depends on a real photo library or on a downloaded
sample. The C-level work (PyInstaller, libheif collection) is P4-00C-B and is not touched.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from p4_heic_fixtures import (
    block_colour,
    build_exif,
    gps_fields,
    quadrants,
    write_exif_heic,
    write_exif_jpeg,
    write_image,
    write_plain_heic,
)
from PIL import Image

from photo_search.catalog.errors import DECODE_FAILED, FORMAT_UNSUPPORTED, CatalogError
from photo_search.geo.exif import STATUS_VALID, read_gps_fields
from photo_search.geo.exif import analyze as analyze_gps
from photo_search.media import contract, decode, heif
from photo_search.media.face_crop import probe_original_frame
from photo_search.search import upload
from photo_search.taken_time.exif import read_time_fields

#: Independent ground truth for the EXIF orientations that swap the two dimensions.
ORIENTATION_TRANSFORMS = {
    1: lambda image: image,
    6: lambda image: image.transpose(Image.Transpose.ROTATE_270),
    8: lambda image: image.transpose(Image.Transpose.ROTATE_90),
}


def unregister_heif(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Pillow forget the HEIF plugin, as a build without pillow-heif would look."""
    monkeypatch.delitem(Image.OPEN, "HEIF", raising=False)
    monkeypatch.setattr(Image, "ID", [name for name in Image.ID if name != "HEIF"])
    monkeypatch.setattr(heif, "ensure_heif_support", lambda: False)


def remove_heif_registrations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget the plugin in Pillow only: the real ``ensure_heif_support`` still runs."""
    monkeypatch.delitem(Image.OPEN, "HEIF", raising=False)
    monkeypatch.setattr(Image, "ID", [name for name in Image.ID if name != "HEIF"])


# --------------------------------------------------------------------------- the policy
def test_heic_joins_the_supported_set_and_the_capability_report() -> None:
    assert "HEIC" in contract.supported_input_formats()
    assert "HEIC" not in contract.refused_input_formats()
    assert decode.plugin_format_decision("HEIF") == ("HEIF", True)
    assert decode.plugin_format_decision("HEIC") == ("HEIC", True)

    probe = decode.probe_capabilities()
    assert probe["supported_input_formats"] == sorted(contract.supported_input_formats())
    assert "HEIC" in probe["supported_input_formats"]
    assert probe["heif_support"]["available"] is True
    assert probe["heif_support"]["formats"] == ["HEIC", "HEIF"]
    # The formats this build still refuses did not move.
    #: P4-02S moved ``MPO`` out of this list (first-frame semantics verified); every other
    #: recognised-but-unverified family stays refused.
    for refused in ("AVIF", "TIFF", "JXL", "RAW", "PSD"):
        assert refused in probe["refused_input_formats"]


def test_the_media_spec_advertises_the_same_set() -> None:
    advertised = contract.THUMBNAIL_SPEC.as_dict()["supported_input_formats"]
    assert advertised == sorted(contract.supported_input_formats())
    assert "HEIC" in advertised


def test_the_heif_plugin_is_registered_once_and_idempotently() -> None:
    """One registration point: repeating the call never re-registers a second factory."""
    heif.ensure_heif_support()
    first_factory = Image.OPEN["HEIF"][0]
    assert Image.OPEN["HEIF"][0] is first_factory
    assert heif.ensure_heif_support() is True
    assert heif.ensure_heif_support() is True
    assert Image.OPEN["HEIF"][0] is first_factory
    assert Image.ID.count("HEIF") == 1


def test_only_one_source_module_talks_to_pillow_heif() -> None:
    """``pillow_heif`` is named in exactly one production module (the registration point)."""
    root = Path(__file__).resolve().parents[2] / "src" / "photo_search"
    mentions = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "pillow_heif" in path.read_text(encoding="utf-8")
    )
    assert mentions == ["media/heif.py"]


def test_heic_is_decided_by_content_and_not_by_the_extension(tmp_path: Path) -> None:
    heic_bytes = write_plain_heic(tmp_path / "real.heic").read_bytes()
    mislabelled = tmp_path / "actually-a-heic.jpg"
    mislabelled.write_bytes(heic_bytes)
    decoded = decode.load_normalized(mislabelled, long_edge=320)
    assert decoded.source_format == "HEIF"
    decoded.image.close()

    quadrants(64, 48).save(tmp_path / "actually-a-jpeg.heic", "JPEG", quality=90)
    decoded = decode.load_normalized(tmp_path / "actually-a-jpeg.heic", long_edge=320)
    assert decoded.source_format == "JPEG"
    decoded.image.close()


# ------------------------------------------------------------------- decoding and size
def test_heic_thumbnail_and_preview_honour_the_frozen_long_edge(tmp_path: Path) -> None:
    photo = write_plain_heic(tmp_path / "big.heic", size=(2400, 1800))

    thumbnail = decode.load_normalized(photo, long_edge=contract.THUMBNAIL_LONG_EDGE)
    payload = decode.render_jpeg(
        thumbnail.image,
        long_edge=contract.THUMBNAIL_LONG_EDGE,
        quality=contract.THUMBNAIL_JPEG_QUALITY,
    )
    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.format == "JPEG"
        assert rendered.size == (320, 240)
    thumbnail.image.close()

    preview = decode.load_normalized(photo, long_edge=contract.PREVIEW_LONG_EDGE)
    payload = decode.render_jpeg(
        preview.image,
        long_edge=contract.PREVIEW_LONG_EDGE,
        quality=contract.PREVIEW_JPEG_QUALITY,
    )
    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.size == (1600, 1200)
    preview.image.close()


def test_a_small_heic_is_never_enlarged(tmp_path: Path) -> None:
    photo = write_plain_heic(tmp_path / "small.heic", size=(40, 20))
    decoded = decode.load_normalized(photo, long_edge=320)
    payload = decode.render_jpeg(decoded.image, long_edge=320, quality=85)
    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.size == (40, 20)


# --------------------------------------------------------------------------- orientation
def test_the_pinned_encoder_really_writes_both_orientation_shapes(tmp_path: Path) -> None:
    """The discriminator the decoder relies on is observable, not assumed."""
    exif = build_exif(orientation=6)
    with_container = write_image(
        tmp_path / "with-container.heic", image_format="HEIF", exif=exif
    )
    without_container = write_image(
        tmp_path / "exif-only.heic",
        image_format="HEIF",
        exif=exif,
        container_orientation=False,
    )

    heif.ensure_heif_support()
    with Image.open(with_container) as source:
        assert heif.recorded_orientation(source) == 6
        assert heif.container_orientation_applied(source) is True
        assert source.size == (200, 320)
    with Image.open(without_container) as source:
        assert heif.recorded_orientation(source) == 6
        assert heif.container_orientation_applied(source) is False
        assert source.size == (320, 200)


@pytest.mark.parametrize("orientation", [6, 8])
@pytest.mark.parametrize("container_orientation", [True, False])
def test_every_heic_orientation_shape_decodes_upright(
    tmp_path: Path, orientation: int, container_orientation: bool
) -> None:
    """Container ``irot`` and EXIF-only orientation must end in the same upright frame."""
    stored = quadrants(320, 200)
    expected = ORIENTATION_TRANSFORMS[orientation](stored)
    path = write_exif_heic(
        tmp_path / f"oriented-{orientation}-{container_orientation}.heic",
        orientation=orientation,
        container_orientation=container_orientation,
        image=stored,
    )

    decoded = decode.load_normalized(path, long_edge=320)
    assert decoded.source_format == "HEIF"
    assert (decoded.width, decoded.height) == expected.size
    assert decoded.orientation == (1 if container_orientation else orientation)
    # HEVC is lossy, so the frame is checked through the four quadrant colours: a missing
    # rotation, the wrong direction and a double rotation all rearrange them differently.
    width, height = decoded.image.size
    centres = [
        (width // 4, height // 4),
        (3 * width // 4, height // 4),
        (width // 4, 3 * height // 4),
        (3 * width // 4, 3 * height // 4),
    ]
    for centre in centres:
        assert block_colour(decoded.image, centre) == pytest.approx(
            expected.getpixel(centre)[:3], abs=24
        )
    decoded.image.close()


def test_read_orientation_and_the_header_probe_agree_with_the_decoder(tmp_path: Path) -> None:
    for container_orientation in (True, False):
        path = write_exif_heic(
            tmp_path / f"header-{container_orientation}.heic",
            orientation=6,
            container_orientation=container_orientation,
        )
        with Image.open(path) as source:
            # ``heif`` is only reachable through the plugin this process registered.
            heif.ensure_heif_support()
            orientation = decode.read_orientation(source)

        decoded = decode.load_normalized(path, long_edge=320)
        assert probe_original_frame(path) == (decoded.width, decoded.height)
        assert (decoded.width, decoded.height) == (200, 320)
        assert orientation == decoded.orientation
        decoded.image.close()


# -------------------------------------------------------------------------坏文件隔离
def test_truncated_and_header_only_heic_are_decode_failures(tmp_path: Path) -> None:
    healthy = write_plain_heic(tmp_path / "healthy.heic", size=(320, 200))
    payload = healthy.read_bytes()
    truncated = tmp_path / "truncated.heic"
    truncated.write_bytes(payload[: len(payload) // 2])
    header_only = tmp_path / "header-only.heic"
    header_only.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1")

    for path in (truncated, header_only):
        with pytest.raises(CatalogError) as error:
            decode.load_normalized(path, long_edge=320)
        assert error.value.code == DECODE_FAILED
        assert error.value.details["path"] == str(path)


def test_the_other_refused_families_are_still_refused(tmp_path: Path) -> None:
    avif = tmp_path / "modern.avif"
    avif.write_bytes(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1")
    with pytest.raises(CatalogError) as error:
        decode.load_normalized(avif, long_edge=320)
    assert error.value.code == FORMAT_UNSUPPORTED
    assert error.value.details["format"] == "AVIF"
    assert "HEIC" in error.value.details["supported_formats"]


def test_the_pixel_safety_limit_is_not_relaxed_for_heic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_plain_heic(tmp_path / "photo.heic", size=(320, 200))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000)
    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)
    assert error.value.code == DECODE_FAILED
    assert "pixel safety limit" in error.value.message


# --------------------------------------------------------------------- plugin missing
def test_a_build_without_the_heif_plugin_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_plain_heic(tmp_path / "photo.heic", size=(64, 48))
    jpeg = tmp_path / "photo.jpg"
    quadrants(64, 48).save(jpeg, "JPEG", quality=90)
    unregister_heif(monkeypatch)

    assert heif.heif_available() is False
    assert "HEIC" not in contract.supported_input_formats()
    assert "HEIF" in contract.refused_input_formats()
    probe = decode.probe_capabilities()
    assert probe["heif_support"]["available"] is False
    assert probe["heif_support"]["formats"] == []
    assert "HEIC" not in probe["supported_input_formats"]
    assert decode.plugin_format_decision("HEIF") == ("HEIF", False)

    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)
    assert error.value.code == FORMAT_UNSUPPORTED
    assert error.value.details["format"] == "HEIF"
    assert "HEIC" not in error.value.details["supported_formats"]

    # A build without the plugin is still a complete media build for every other format.
    decoded = decode.load_normalized(jpeg, long_edge=320)
    assert decoded.source_format == "JPEG"
    decoded.image.close()


def test_a_registration_that_fails_after_installing_the_opener_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P4-00C-B degraded case: the opener is installed, then the runtime read fails.

    ``pillow_heif.register_heif_opener()`` registers the Pillow opener *before* it asks the C
    extension about its codecs, so a bundle whose libheif DLL was removed raises only after
    the opener exists. Without a rollback that half-registered plugin answers
    ``DECODE_FAILED`` ("this file is broken") instead of the structured
    ``FORMAT_UNSUPPORTED`` the frozen degradation contract demands. This test reproduces the
    exact failure shape the packaged degraded smoke reported, in-process.
    """
    import pillow_heif
    from pillow_heif import as_plugin as heif_plugin
    from pillow_heif import heif as heif_module
    from pillow_heif._deffered_error import DeferredError

    path = write_plain_heic(tmp_path / "photo.heic", size=(64, 48))
    remove_heif_registrations(monkeypatch)
    monkeypatch.setattr(heif, "_PROBED", False)
    monkeypatch.setattr(heif, "_AVAILABLE", False)
    monkeypatch.setattr(heif, "_ERROR", None)
    # In the degraded bundle every module that talks to the C extension holds a
    # ``DeferredError``, so ``Image.open`` fails *inside* Pillow rather than at import time.
    broken = DeferredError(
        ImportError("DLL load failed while importing _pillow_heif: 找不到指定的模块。")
    )
    monkeypatch.setattr(heif_module, "_pillow_heif", broken)
    monkeypatch.setattr(heif_plugin, "_pillow_heif", broken)

    def register_then_fail(**kwargs: Any) -> None:
        heif_plugin.Image.register_open(
            heif_plugin.HeifImageFile.format,
            heif_plugin.HeifImageFile,
            heif_plugin._is_supported_heif,
        )
        heif_plugin._pillow_heif.get_lib_info()

    monkeypatch.setattr(pillow_heif, "register_heif_opener", register_then_fail)

    assert heif.ensure_heif_support() is False
    assert "HEIF" not in Image.OPEN
    assert "HEIF" not in Image.ID
    assert "DLL load failed" in str(heif.runtime_fact()["error"])

    with pytest.raises(CatalogError) as error:
        decode.load_normalized(path, long_edge=320)
    assert error.value.code == FORMAT_UNSUPPORTED
    assert error.value.details["format"] == "HEIF"
    assert "HEIC" not in error.value.details["supported_formats"]


def test_the_upload_boundary_now_accepts_the_heif_family(tmp_path: Path) -> None:
    """P4-00C-A kept this list its own; P4-02T amended the *set* it holds.

    It is still a literal list (a new family is still an explicit protocol change), but it
    now covers every family the library already indexes and displays, so a phone's own HEIC
    is a query instead of a structured refusal.
    """
    heic_bytes = write_plain_heic(tmp_path / "photo.heic", size=(64, 48)).read_bytes()
    assert upload.UPLOAD_INPUT_FORMATS == frozenset(
        {"JPEG", "PNG", "WEBP", "BMP", "GIF", "MPO", "HEIC", "HEIF"}
    )
    assert "HEIF" not in upload.upload_refused_families()

    with upload.stage_upload(heic_bytes) as staged:
        assert staged.facts.format == "HEIF"
        assert staged.facts.byte_size == len(heic_bytes)
        assert (staged.facts.width, staged.facts.height) == (64, 48)


def test_gps_and_time_tags_are_visible_on_a_heic(tmp_path: Path) -> None:
    """The HEIF EXIF block exposes the same two sub-IFDs the JPEG path already reads."""
    options = {
        "original": "2022:07:05 18:31:07",
        "offset_original": "+08:00",
        "make": "ACME",
        "model": "X1",
        "gps": gps_fields(),
    }
    heic = write_exif_heic(tmp_path / "photo.heic", **options)
    jpeg = write_exif_jpeg(tmp_path / "photo.jpg", **options)

    heic_fields = read_time_fields(heic)
    jpeg_fields = read_time_fields(jpeg)
    assert heic_fields.original == jpeg_fields.original == "2022:07:05 18:31:07"
    assert heic_fields.original_offset == jpeg_fields.original_offset == "+08:00"
    assert heic_fields.camera == jpeg_fields.camera == "ACME X1"

    heif.ensure_heif_support()
    with Image.open(heic) as source:
        heic_gps = read_gps_fields(source.getexif())
    with Image.open(jpeg) as source:
        jpeg_gps = read_gps_fields(source.getexif())
    assert heic_gps is not None and jpeg_gps is not None
    assert sorted(heic_gps) == sorted(jpeg_gps)
    heic_decision = analyze_gps(heic_gps)
    jpeg_decision = analyze_gps(jpeg_gps)
    assert heic_decision.status == jpeg_decision.status == STATUS_VALID
    assert heic_decision.datum_status == jpeg_decision.datum_status
    assert heic_decision.normalized_wgs84 == jpeg_decision.normalized_wgs84
