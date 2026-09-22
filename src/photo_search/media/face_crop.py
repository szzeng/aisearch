"""Bounded face-avatar crops rendered from the frozen preview artifact (P3-06).

Design rules this module keeps:

* **No caller-supplied geometry and no caller-supplied path.** The endpoint names one
  ``face_id``; the authoritative box comes from ``faces.bbox_json`` and the pixels come from
  the already published P1-03 preview, which the controlled-file boundary has already
  resolved. Nothing here opens a path a request named.
* **The frame mapping is one uniform factor.** ``faces.bbox_json`` is stored in the
  orientation-normalised *original* pixel frame (P3-02 scales the detector box back with
  ``working_scale``), while the preview is that same frame scaled so its long edge is at most
  ``PREVIEW_LONG_EDGE``. The preview therefore maps to the original by
  ``preview_width / original_width``; the original's orientation-normalised size is read
  from the image header only (no pixel decode of the original, and the EXIF orientations that
  rotate by 90 degrees swap the two numbers, exactly as ``load_normalized`` does).
* **Bounded work.** The preview is capped at 1600 px on the long edge by its own contract, so
  decoding it is bounded; the output is capped at ``FACE_CROP_LONG_EDGE`` and is never
  enlarged. The published file name is the digest of (processor, face, geometry, content),
  so a re-detection that moves a box cannot reuse the previous pixels.

The residual approximation is recorded instead of hidden: the JPEG preview rounds its
dimensions to whole pixels, so the mapping can be off by well under one preview pixel. An
avatar crop does not depend on sub-pixel accuracy, and the authoritative geometry stays in
``faces`` untouched.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, UnidentifiedImageError

from photo_search.catalog.errors import (
    DECODE_FAILED,
    FACE_STATE_CONFLICT,
    FILE_READ_ERROR,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.media.contract import (
    FACE_CROP_JPEG_QUALITY,
    FACE_CROP_LONG_EDGE,
    FACE_CROP_MARGIN,
    FACE_CROP_PROCESSOR_ID,
)
from photo_search.media.decode import prepare_output_mode, read_orientation

#: EXIF orientations that rotate by 90 degrees, i.e. the ones that swap width and height.
SWAPPED_ORIENTATIONS = frozenset({5, 6, 7, 8})
#: The smallest crop this build renders; a degenerate box is refused instead of padded.
MIN_CROP_PIXELS = 8


@dataclass(frozen=True)
class FaceBox:
    """One authoritative face box in orientation-normalised source pixels."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return float(self.right) - float(self.left)

    @property
    def height(self) -> float:
        return float(self.bottom) - float(self.top)

    def as_list(self) -> list[float]:
        return [float(self.left), float(self.top), float(self.right), float(self.bottom)]


def parse_bbox(raw: Any) -> FaceBox:
    """Validate one stored ``bbox_json``; a broken row is a state conflict, never a guess.

    Two shapes are read, and both are produced by this repository:

    * a plain ``[left, top, right, bottom]`` list, which is what every P3-06 fixture and the
      crop cache key have always used, and
    * ``{"bbox": [...]}``, which is the shape the P3-02 processor writes into
      ``faces.bbox_json`` through ``_upsert_face()``.

    Before this tolerance existed the two disagreed, so a face written by the real detection
    pipeline could never be cropped (``FACE_STATE_CONFLICT``/``bbox_not_a_list``); a
    production row is therefore accepted here instead of being reported as a catalog defect.
    Anything else keeps its frozen refusal.
    """
    try:
        payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CatalogError(
            FACE_STATE_CONFLICT,
            "the recorded face geometry is not readable",
            details={"reason": "bbox_not_json"},
        ) from error
    if isinstance(payload, Mapping):
        payload = payload.get("bbox")
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        raise CatalogError(
            FACE_STATE_CONFLICT,
            "the recorded face geometry is not a four-number box",
            details={"reason": "bbox_not_a_list"},
        )
    values = list(payload)
    if len(values) != 4:
        raise CatalogError(
            FACE_STATE_CONFLICT,
            "the recorded face geometry is not a four-number box",
            details={"reason": "bbox_arity"},
        )
    numbers: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise CatalogError(
                FACE_STATE_CONFLICT,
                "the recorded face geometry is not a four-number box",
                details={"reason": "bbox_not_numeric"},
            ) from error
        if not math.isfinite(number):
            raise CatalogError(
                FACE_STATE_CONFLICT,
                "the recorded face geometry is not finite",
                details={"reason": "bbox_not_finite"},
            )
        numbers.append(number)
    box = FaceBox(left=numbers[0], top=numbers[1], right=numbers[2], bottom=numbers[3])
    if box.width <= 0 or box.height <= 0:
        raise CatalogError(
            FACE_STATE_CONFLICT,
            "the recorded face geometry has no positive area",
            details={"reason": "bbox_degenerate"},
        )
    return box


def normalize_original_size(
    size: tuple[int, int], orientation: int
) -> tuple[int, int]:
    """The orientation-normalised size of an image with the given EXIF orientation."""
    width, height = int(size[0]), int(size[1])
    if int(orientation) in SWAPPED_ORIENTATIONS:
        return height, width
    return width, height


def probe_original_frame(path: str | Path) -> tuple[int, int]:
    """Read an original's orientation-normalised size from its header, or refuse honestly.

    Only the header is touched: Pillow reads the dimensions and the EXIF block without
    decoding pixels, so a 100-megapixel original costs the same as a small one here.
    """
    candidate = Path(path)
    try:
        with Image.open(candidate) as image:
            size = (int(image.size[0]), int(image.size[1]))
            orientation = read_orientation(image)
    except FileNotFoundError as error:
        raise CatalogError(
            SOURCE_FILE_MISSING,
            "the source file is no longer readable",
            details={"location": "original"},
        ) from error
    except UnidentifiedImageError as error:
        raise CatalogError(
            DECODE_FAILED,
            "the source file is not an image this build can measure",
            details={"location": "original"},
        ) from error
    except PermissionError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            "the source file cannot be opened for reading",
            details={"location": "original"},
        ) from error
    except OSError as error:
        raise CatalogError(
            FILE_READ_ERROR,
            f"cannot read the source header ({type(error).__name__})",
            details={"location": "original"},
        ) from error
    normalized = normalize_original_size(size, orientation)
    if normalized[0] <= 0 or normalized[1] <= 0:
        raise CatalogError(
            DECODE_FAILED,
            "the source file reports no usable dimensions",
            details={"location": "original"},
        )
    return normalized


def crop_box(
    box: FaceBox,
    *,
    source_size: tuple[int, int],
    target_size: tuple[int, int],
    margin: float = FACE_CROP_MARGIN,
) -> tuple[int, int, int, int]:
    """Map one source-frame face box into a clamped, square-ish crop of the target frame.

    ``source_size`` is the orientation-normalised original, ``target_size`` the preview the
    crop is taken from; both share one aspect ratio, so a single factor maps them.
    """
    source_width = float(source_size[0])
    source_height = float(source_size[1])
    target_width = int(target_size[0])
    target_height = int(target_size[1])
    if source_width <= 0 or source_height <= 0:
        raise ValueError("the source frame has no usable size")
    if target_width < 1 or target_height < 1:
        raise ValueError("the target frame has no usable size")
    factor = float(target_width) / source_width
    center_x = (float(box.left) + float(box.right)) / 2.0 * factor
    center_y = (float(box.top) + float(box.bottom)) / 2.0 * factor
    side = max(float(box.width), float(box.height)) * factor * (1.0 + 2.0 * float(margin))
    side = max(side, float(MIN_CROP_PIXELS))
    half = side / 2.0
    left, right = _fit(
        int(math.floor(center_x - half)), int(math.ceil(center_x + half)), limit=target_width
    )
    top, bottom = _fit(
        int(math.floor(center_y - half)), int(math.ceil(center_y + half)), limit=target_height
    )
    return left, top, right, bottom


def _fit(start: int, end: int, *, limit: int) -> tuple[int, int]:
    """Slide one interval inside ``[0, limit]`` without changing its size when it fits."""
    if end - start >= limit:
        return 0, int(limit)
    if start < 0:
        end -= start
        start = 0
    if end > limit:
        start -= end - limit
        end = int(limit)
    return int(max(0, start)), int(min(limit, end))


def crop_key(*, face_id: str, box: FaceBox, sha256: str) -> str:
    """The digest that names one crop: processor, face, geometry and content."""
    payload = json.dumps(
        {
            "processor_id": FACE_CROP_PROCESSOR_ID,
            "long_edge": int(FACE_CROP_LONG_EDGE),
            "quality": int(FACE_CROP_JPEG_QUALITY),
            "margin": float(FACE_CROP_MARGIN),
            "face_id": str(face_id),
            "bbox": [round(float(value), 2) for value in box.as_list()],
            "sha256": str(sha256),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_crop(
    preview_path: str | Path,
    *,
    box: FaceBox,
    source_size: tuple[int, int],
    long_edge: int = FACE_CROP_LONG_EDGE,
    quality: int = FACE_CROP_JPEG_QUALITY,
) -> bytes:
    """Crop one face out of a published preview and encode the bounded JPEG avatar."""
    candidate = Path(preview_path)
    try:
        with Image.open(candidate) as preview:
            preview.load()
            image = prepare_output_mode(preview)
            target_size = (int(image.size[0]), int(image.size[1]))
            rect = crop_box(
                box, source_size=source_size, target_size=target_size
            )
            cropped = image.crop(rect)
            # The crop must own its pixels before the preview file handle is closed.
            cropped.load()
    except FileNotFoundError as error:
        raise CatalogError(
            SOURCE_FILE_MISSING,
            "the published preview is no longer readable",
            details={"location": "preview"},
        ) from error
    except OSError as error:
        raise CatalogError(
            DECODE_FAILED,
            f"cannot decode the published preview ({type(error).__name__})",
            details={"location": "preview"},
        ) from error
    crop = prepare_output_mode(cropped)
    crop.thumbnail((int(long_edge), int(long_edge)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=int(quality), optimize=False)
    return buffer.getvalue()


__all__ = [
    "FaceBox",
    "MIN_CROP_PIXELS",
    "SWAPPED_ORIENTATIONS",
    "crop_box",
    "crop_key",
    "normalize_original_size",
    "parse_bbox",
    "probe_original_frame",
    "render_crop",
]
