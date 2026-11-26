"""Conservative Sony ARW recognition using TIFF structure and LibRaw."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from PIL import Image, TiffImagePlugin

from photo_search.catalog.errors import DECODE_FAILED, CatalogError
from photo_search.media import raw_cr2


def _sony_raw_structure(path: str | Path) -> bool:
    """Require Sony identity, a Sony RAW CFA sub-IFD, and no DNG version tag."""
    try:
        with Path(path).open("rb") as stream:
            header = stream.read(8)
            if header[:4] not in (b"II*\x00", b"MM\x00*"):
                return False
            stream.seek(0)
            exif = Image.Exif()
            exif.load_from_fp(stream)
            if str(exif.get(271, "")).strip().upper() != "SONY":
                return False
            model = str(exif.get(272, "")).strip()
            if not model or model.upper() == "DSC-R1" or exif.get(50706) is not None:
                return False
            offsets = exif.get(330)
            if isinstance(offsets, int):
                offsets = (offsets,)
            if not isinstance(offsets, (tuple, list)) or len(offsets) > 16:
                return False
            size = Path(path).stat().st_size
            for offset in offsets:
                if not isinstance(offset, int) or not 8 <= offset < size - 2:
                    continue
                stream.seek(offset)
                ifd = TiffImagePlugin.ImageFileDirectory_v2(exif.head)
                ifd.load(stream)
                if (
                    ifd.get(28672) is not None
                    and ifd.get(262) == 32803
                    and ifd.get(50706) is None
                ):
                    return True
    except Exception:  # malformed TIFF directories must never abort a batch
        return False
    return False


def is_arw_candidate(path: str | Path) -> bool:
    """Cheap structure-only gate; it never establishes that a file is decodable."""
    return Path(path).suffix.lower() == ".arw" and _sony_raw_structure(path)


def is_arw(path: str | Path) -> bool:
    """Check real Sony RAW structure and confirm that LibRaw can unpack the file."""
    if not is_arw_candidate(path):
        return False
    try:
        rawpy = importlib.import_module("rawpy")
        with raw_cr2._RAW_LOCK:
            raw = rawpy.RawPy()
            try:
                raw.open_file(str(path))
                width, height = int(raw.sizes.iwidth), int(raw.sizes.iheight)
                if width <= 0 or height <= 0 or width * height > raw_cr2.MAX_RAW_PIXELS:
                    return False
                raw.unpack()
                return True
            finally:
                raw.close()
    except Exception:  # malformed files and unsupported runtime fail closed
        return False


def decode(path: str | Path, *, max_pixels: int | None = None):
    if not _sony_raw_structure(path):
        raise CatalogError(DECODE_FAILED, "file is not a verified Sony ARW", details={"format": "ARW"})
    return raw_cr2._decode_verified(Path(path), max_pixels=max_pixels, raw_format="ARW")


@contextmanager
def open_exif(path: str | Path):
    """Read EXIF from the original TIFF container, without decoding embedded JPEG."""
    with Path(path).open("rb") as stream:
        exif = Image.Exif()
        exif.load_from_fp(stream)
        yield exif


@contextmanager
def encoder_input(path: str | Path, *, temp_root: str | Path) -> Iterator[Path]:
    """Provide canonical RAW pixels to path-only encoders and always remove the JPEG."""
    import os
    import tempfile

    directory = Path(temp_root) / "tmp" / "arw-encoder"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="canonical-", suffix=".jpg", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    try:
        image, _, _ = decode(path)
        try:
            image.save(temporary, format="JPEG", quality=95, optimize=False)
        finally:
            image.close()
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)
