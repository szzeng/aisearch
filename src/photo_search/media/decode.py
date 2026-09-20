"""Orientation-normalised decoding and JPEG rendering (architecture §5.4).

Every input is normalised by its EXIF orientation *before* any resize, so the artifact
and every later coordinate/derived result share one upright pixel frame. The module never
modifies the original file; it only opens it for reading.

The format policy is explicit: a format this build has not verified is reported as
``FORMAT_UNSUPPORTED`` instead of being decoded opportunistically, and a file that is not
decodable at all is ``DECODE_FAILED``. Neither is turned into a blank placeholder.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from photo_search.catalog.errors import (
    ACCESS_DENIED,
    DECODE_FAILED,
    FILE_READ_ERROR,
    FORMAT_UNSUPPORTED,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.media.contract import (
    SUPPORTED_INPUT_FORMATS,
    UNSUPPORTED_INPUT_FORMATS,
)

ORIENTATION_TAG = 0x0112
HEADER_BYTES = 32

#: JPEG-only DCT shortcut: when the stored image is at least this many times larger than
#: the target, Pillow decodes at a reduced scale, which bounds peak memory for very large
#: photos without ever decoding below the requested long edge.
_ISO_BMFF_BRANDS = {
    b"heic": "HEIF",
    b"heix": "HEIF",
    b"hevc": "HEIF",
    b"hevx": "HEIF",
    b"mif1": "HEIF",
    b"msf1": "HEIF",
    b"avif": "AVIF",
    b"avis": "AVIF",
    b"crx ": "RAW",
}
_MAGIC_FORMATS = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
    (b"IIRO", "TIFF"),
    (b"IIRS", "TIFF"),
    (b"IIU\x00", "TIFF"),
    (b"FUJIFILMCCD-RAW", "RAW"),
)


@dataclass(frozen=True)
class DecodedImage:
    """One decoded, orientation-normalised image plus its source facts."""

    image: Image.Image
    width: int
    height: int
    source_format: str
    orientation: int
    source_mode: str


def read_header(path: Path, *, size: int = HEADER_BYTES) -> bytes:
    """Read a bounded prefix of the file; used only to classify an unidentifiable file."""
    try:
        with path.open("rb") as stream:
            return stream.read(size)
    except PermissionError as error:
        raise _access_denied(path, error) from error
    except FileNotFoundError as error:
        raise _source_missing(path, error) from error
    except OSError as error:
        raise _read_error(path, error) from error


def sniff_format(header: bytes) -> str | None:
    """Best-effort label for a file Pillow refused to identify, or ``None``."""
    for magic, label in _MAGIC_FORMATS:
        if header.startswith(magic):
            return label
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return _ISO_BMFF_BRANDS.get(header[8:12], "ISO-BMFF")
    if header.startswith(b"BM"):
        return "BMP"
    return None


def plugin_format_decision(pil_format: str | None) -> tuple[str, bool]:
    """Return ``(label, decodable)`` for a format Pillow already identified.

    The file name/extension is never consulted: policy follows the content Pillow
    reported. A format outside both lists is refused rather than decoded
    opportunistically, because its orientation/colour handling has not been verified.
    """
    label = str(pil_format or "").upper()
    if label in SUPPORTED_INPUT_FORMATS:
        return label, True
    return (label or "UNKNOWN"), False


def load_normalized(path: Path, *, long_edge: int) -> DecodedImage:
    """Decode ``path``, apply its EXIF orientation and return the upright image.

    The returned image is fully loaded and owned by the caller, so the source file can be
    closed immediately and the image can be resized in place.
    """
    try:
        with Image.open(path) as source:
            label, decodable = plugin_format_decision(source.format)
            if not decodable:
                raise _unsupported(label)
            if label == "JPEG":
                # Must happen before ``exif_transpose`` loads the pixels.
                source.draft(source.mode, (long_edge, long_edge))
            orientation = _read_orientation(source)
            upright = ImageOps.exif_transpose(source)
            upright.load()
            width, height = upright.size
            mode = source.mode
    except CatalogError:
        raise
    except UnidentifiedImageError as error:
        # Pillow could not identify the file at all. Only a recognised-but-refused family
        # is a format statement; anything else (empty file, text, damaged header) is an
        # honest decode failure, never a silent "unsupported".
        sniffed = sniff_format(read_header(path))
        if sniffed is not None and sniffed in UNSUPPORTED_INPUT_FORMATS:
            raise _unsupported(sniffed, path=str(path)) from error
        raise _decode_failed(path, f"cannot identify the file as an image: {error}") from error
    except Image.DecompressionBombError as error:
        raise _decode_failed(path, f"image exceeds the pixel safety limit: {error}") from error
    except MemoryError as error:
        raise _decode_failed(path, f"not enough memory to decode the image: {error}") from error
    except PermissionError as error:
        raise _access_denied(path, error) from error
    except FileNotFoundError as error:
        raise _source_missing(path, error) from error
    except OSError as error:
        raise _decode_failed(path, f"cannot decode the image: {error}") from error
    except Exception as error:
        # The whole point of this boundary is isolation: a malformed file must degrade
        # into one honest DECODE_FAILED record, never into a batch-wide crash. The
        # exception type stays in the message so a real bug remains visible.
        raise _decode_failed(
            path, f"cannot decode the image ({type(error).__name__}): {error}"
        ) from error
    return DecodedImage(
        image=upright,
        width=int(width),
        height=int(height),
        source_format=label,
        orientation=orientation,
        source_mode=mode,
    )


def render_jpeg(image: Image.Image, *, long_edge: int, quality: int) -> bytes:
    """Resize to the long-edge contract and encode JPEG. Never enlarges.

    ``image`` is consumed: it is the orientation-normalised image owned by the caller.
    """
    prepared = prepare_output_mode(image)
    prepared.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    prepared.save(buffer, format="JPEG", quality=int(quality), optimize=False)
    return buffer.getvalue()


def prepare_output_mode(image: Image.Image) -> Image.Image:
    """Return an image whose mode JPEG can store, compositing transparency on white."""
    if image.mode in ("RGB", "L"):
        return image
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def probe_capabilities() -> dict[str, object]:
    """Measured Pillow capabilities this build relies on (report evidence, not guesses)."""
    from PIL import features

    return {
        "pillow_version": getattr(Image, "__version__", "unknown"),
        "webp_save": bool(features.check("webp")),
        "libtiff_read": bool(features.check("libtiff")),
        "jpeg": bool(features.check("jpg")),
        "zlib": bool(features.check("zlib")),
        "exif_orientation_read": hasattr(ImageOps, "exif_transpose"),
        "exif_writer": hasattr(Image, "Exif"),
        "max_image_pixels": Image.MAX_IMAGE_PIXELS,
        "jpeg_draft_reduce": True,
        "supported_input_formats": sorted(SUPPORTED_INPUT_FORMATS),
        "refused_input_formats": sorted(UNSUPPORTED_INPUT_FORMATS),
    }


def read_orientation(source: Image.Image) -> int:
    """The EXIF orientation of an opened image, normalised to ``1..8`` (``1`` when unknown).

    Public because the P3-06 face crop has to measure the *orientation-normalised* size of
    an original from its header alone, and it must apply exactly the rule
    :func:`load_normalized` applies to the pixels.
    """
    try:
        exif = source.getexif()
    except Exception:  # pragma: no cover - defensive: a broken EXIF block is not fatal
        return 1
    if not exif:
        return 1
    try:
        value = int(exif.get(ORIENTATION_TAG, 1))
    except (TypeError, ValueError):
        return 1
    return value if 1 <= value <= 8 else 1


def _read_orientation(source: Image.Image) -> int:
    return read_orientation(source)


def _unsupported(label: str, *, path: str | None = None) -> CatalogError:
    details: dict[str, object] = {
        "format": label,
        "supported_formats": sorted(SUPPORTED_INPUT_FORMATS),
    }
    if path is not None:
        details["path"] = path
    return CatalogError(
        FORMAT_UNSUPPORTED,
        f"format {label} is not supported by this build yet",
        details=details,
    )


def _decode_failed(path: Path, message: str) -> CatalogError:
    return CatalogError(DECODE_FAILED, message, details={"path": str(path)})


def _access_denied(path: Path, error: OSError) -> CatalogError:
    return CatalogError(
        ACCESS_DENIED,
        f"access denied while reading {path}: {error}",
        details={"path": str(path)},
    )


def _source_missing(path: Path, error: OSError) -> CatalogError:
    return CatalogError(
        SOURCE_FILE_MISSING,
        f"the source file is no longer readable: {path}",
        details={"path": str(path)},
    )


def _read_error(path: Path, error: OSError) -> CatalogError:
    return CatalogError(
        FILE_READ_ERROR,
        f"cannot read {path}: {error}",
        details={"path": str(path)},
    )
