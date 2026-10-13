"""Conservative Canon CR2 detection and one canonical LibRaw decode path.

CR2 files are TIFF containers, so Pillow can identify them as TIFF.  This adapter requires
both the CR2 container marker and a usable LibRaw runtime; the extension alone never grants
RAW decoding.  Until an embedded preview's crop relationship has been qualified against
real samples, the deterministic source is LibRaw's oriented sRGB output.
"""

from __future__ import annotations

import importlib
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from photo_search.catalog.errors import DECODE_FAILED, RUNTIME_CAPABILITY_UNAVAILABLE, CatalogError

CR2_MARKER = b"CR\x02\x00"
MAX_RAW_PIXELS = 120_000_000
_RAW_LOCK = threading.Lock()
_METRIC_LOCK = threading.Lock()
_METRICS = {"cr2_preview_used": 0, "cr2_raw_fallback": 0, "cr2_decode_failed": 0}


def metrics() -> dict[str, int]:
    """Process-local, non-sensitive CR2 path counts; no file paths or identifiers."""
    with _METRIC_LOCK:
        return dict(_METRICS)


def _record_metric(name: str) -> None:
    with _METRIC_LOCK:
        _METRICS[name] += 1


def has_cr2_header(header: bytes) -> bool:
    """Return true only for a TIFF header carrying the Canon CR2 marker."""
    return (
        len(header) >= 12
        and header[:4] in (b"II*\x00", b"MM\x00*")
        and header[8:12] == CR2_MARKER
    )


def is_cr2(path: str | Path) -> bool:
    """Bounded content probe; file suffixes are intentionally irrelevant."""
    try:
        with Path(path).open("rb") as stream:
            return has_cr2_header(stream.read(16))
    except OSError:
        return False


def runtime_available() -> bool:
    """Whether the pinned optional import can be loaded in this process."""
    try:
        importlib.import_module("rawpy")
    except (ImportError, OSError):
        return False
    return True


def read_normalized_dimensions(path: str | Path) -> tuple[int, int] | None:
    """Read LibRaw's effective image dimensions without demosaicing pixels."""
    try:
        rawpy = importlib.import_module("rawpy")
        with _RAW_LOCK:
            with rawpy.imread(str(path)) as raw:
                width = int(raw.sizes.iwidth)
                height = int(raw.sizes.iheight)
                flip = int(raw.sizes.flip)
        if width <= 0 or height <= 0:
            return None
        if flip in (5, 6, 7, 8):
            return height, width
        return width, height
    except Exception:  # header probing is an optional geometry fact
        return None


@contextmanager
def encoder_input(path: str | Path, *, temp_root: str | Path) -> Iterator[Path]:
    """Expose a CR2's canonical RGB pixels as a short-lived JPEG for path-only encoders."""
    candidate = Path(path)
    if not is_cr2(candidate):
        yield candidate
        return
    directory = Path(temp_root) / "tmp" / "cr2-encoder"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="canonical-", suffix=".jpg", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    try:
        image, _, _ = decode(candidate)
        try:
            image.save(temporary, format="JPEG", quality=95, optimize=False)
        finally:
            image.close()
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def decode(path: str | Path, *, max_pixels: int | None = None) -> tuple[Image.Image, int, int]:
    """Decode a CR2 into the canonical upright RGB image and report rawpy's source facts.

    LibRaw applies the camera orientation while postprocessing.  We return orientation 1 so
    Pillow does not apply an EXIF transform a second time.  RAW processing is serialized to
    bound concurrent full-resolution allocations.
    """
    candidate = Path(path)
    if not is_cr2(candidate):
        raise CatalogError(
            DECODE_FAILED,
            "the file does not contain a Canon CR2 header",
            details={"format": "CR2"},
        )
    try:
        rawpy = importlib.import_module("rawpy")
    except (ImportError, OSError) as error:
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "Canon CR2 decoding is unavailable in this runtime",
            details={"format": "CR2"},
        ) from error

    try:
        with _RAW_LOCK:
            with rawpy.imread(str(candidate)) as raw:
                sizes = raw.sizes
                source_width = int(sizes.iwidth)
                source_height = int(sizes.iheight)
                declared_pixels = source_width * source_height
                limit = min(MAX_RAW_PIXELS, int(max_pixels)) if max_pixels is not None else MAX_RAW_PIXELS
                if source_width <= 0 or source_height <= 0 or declared_pixels > limit:
                    raise CatalogError(
                        DECODE_FAILED,
                        "the CR2 image exceeds the bounded RAW pixel limit",
                        details={"format": "CR2", "pixels": declared_pixels, "max_pixels": limit},
                    )
                pixels = raw.postprocess(
                    output_color=rawpy.ColorSpace.sRGB,
                    output_bps=8,
                    use_camera_wb=True,
                    use_auto_wb=False,
                    no_auto_bright=True,
                )
        image = Image.fromarray(pixels).convert("RGB")
    except CatalogError as error:
        if error.code == DECODE_FAILED:
            _record_metric("cr2_decode_failed")
        raise
    except Exception as error:  # one corrupt CR2 must not abort the batch
        _record_metric("cr2_decode_failed")
        raise CatalogError(
            DECODE_FAILED,
            f"cannot decode the Canon CR2 file ({type(error).__name__})",
            details={"format": "CR2"},
        ) from error
    if image.width <= 0 or image.height <= 0:
        image.close()
        _record_metric("cr2_decode_failed")
        raise CatalogError(
            DECODE_FAILED,
            "the CR2 decoder returned no usable pixels",
            details={"format": "CR2"},
        )
    _record_metric("cr2_raw_fallback")
    return image, source_width, source_height
