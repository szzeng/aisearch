"""Bounded face-avatar crops rendered from the frozen preview artifact (P3-06).

Design rules this module keeps:

* **No caller-supplied geometry and no caller-supplied path.** The endpoint names one
  ``face_id``; the authoritative box comes from ``faces.bbox_json`` and the pixels come from
  the already published P1-03 preview, which the controlled-file boundary has already
  resolved. Nothing here opens a path a request named.
* **The recorded frame is authoritative.** A JPEG draft decode can reduce the raster
  before face analysis, so a stored box is in the detection document's normalised
  ``original_size`` frame, which can be smaller than the full photo. Read that frame
  from the verified immutable document; never infer a scale from the face location.
  Rows without a detection document use the normalised source header as before.
  Map each axis separately to account for integer decode/preview rounding.
* **Bounded work.** The preview is capped at 1600 px on the long edge by its own contract, so
  decoding it is bounded; the output is capped at ``FACE_CROP_LONG_EDGE`` and is never
  enlarged. The published file name is the digest of (processor, face, geometry, content),
  so a re-detection that moves a box cannot reuse the previous pixels.

The authoritative geometry, face IDs, embeddings and manual assignments stay untouched.
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

from photo_search.artifacts.store import MAX_ARTIFACT_BYTES, ArtifactStore
from photo_search.catalog.errors import (
    DECODE_FAILED,
    FACE_STATE_CONFLICT,
    FILE_READ_ERROR,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SOURCE_FILE_MISSING,
    CatalogError,
)
from photo_search.media import heif, raw_cr2
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


def normalize_original_size(size: tuple[int, int], orientation: int) -> tuple[int, int]:
    """The orientation-normalised size of an image with the given EXIF orientation."""
    width, height = int(size[0]), int(size[1])
    if int(orientation) in SWAPPED_ORIENTATIONS:
        return height, width
    return width, height


def recorded_face_frame(
    data_root: Path, face: Any, artifact: Any, *, box: FaceBox, sha256: str
) -> tuple[int, int] | None:
    """Recover the existing box's frame without migrating any detection facts.

    Old manually seeded rows have no structured document and keep the header path.
    A real document must be published, checksum-valid and bound to this exact face
    and current content; a missing/corrupt document is refused rather than guessed.
    """
    from photo_search.face.contract import (
        FACE_DETAIL_ARTIFACT_REVISION,
        FACE_DETAIL_SPEC_REVISION,
        FACE_PROCESSOR_ID,
    )

    if artifact is None and not str(face["artifact_id"]).startswith("face-document:"):
        return None

    def invalid() -> CatalogError:
        return CatalogError(
            FACE_STATE_CONFLICT,
            "the recorded face coordinate frame cannot be verified",
            details={"reason": "face_frame_invalid"},
        )

    if (
        artifact is None
        or artifact["lifecycle_state"] != "PUBLISHED"
        or artifact["artifact_type"] != "structured"
        or artifact["artifact_revision"]
        not in {"face-document.v1", "face-document.v2", FACE_DETAIL_ARTIFACT_REVISION}
        or artifact["processor_id"] != FACE_PROCESSOR_ID
        or str(artifact["input_revision"]) != str(sha256)
        or str(face["input_revision"]) != str(sha256)
        or not 0 < int(artifact["byte_size"]) <= MAX_ARTIFACT_BYTES
    ):
        raise invalid()
    try:
        payload = json.loads(ArtifactStore(data_root).read(artifact))
        size = payload["original_size"]
        detail = artifact["artifact_revision"] == FACE_DETAIL_ARTIFACT_REVISION
        if (
            payload["processor_id"] != FACE_PROCESSOR_ID
            or payload["input_revision"] != sha256
            or (
                detail
                and (
                    payload.get("spec_revision") != FACE_DETAIL_SPEC_REVISION
                    or payload.get("artifact_revision") != FACE_DETAIL_ARTIFACT_REVISION
                    or face["spec_revision"] != FACE_DETAIL_SPEC_REVISION
                )
            )
            or not isinstance(size, list)
            or len(size) != 2
            or any(type(value) is not int or value <= 0 for value in size)
        ):
            raise invalid()
        recorded = next(
            item for item in payload["faces"] if item["face_id"] == str(face["face_id"])
        )
        if parse_bbox(recorded["bbox"]) != box:
            raise invalid()
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise invalid() from error
    return int(size[0]), int(size[1])


def probe_original_frame(path: str | Path) -> tuple[int, int]:
    """Read an original's orientation-normalised size from its header, or refuse honestly.

    Only the header is touched: Pillow reads the dimensions and the EXIF block without
    decoding pixels, so a 100-megapixel original costs the same as a small one here.
    """
    candidate = Path(path)
    if raw_cr2.is_cr2(candidate):
        if not raw_cr2.runtime_available():
            raise CatalogError(
                RUNTIME_CAPABILITY_UNAVAILABLE,
                "Canon CR2 frame measurement is unavailable in this runtime",
                details={"location": "original", "format": "CR2"},
            )
        size = raw_cr2.read_normalized_dimensions(candidate)
        if size is None or size[0] <= 0 or size[1] <= 0:
            raise CatalogError(
                DECODE_FAILED,
                "the CR2 source frame cannot be measured safely",
                details={"location": "original", "format": "CR2"},
            )
        return size
    # A HEIC original is only measurable once the one pillow-heif registration point has
    # run; the orientation rule itself is the shared one in ``media.decode`` (P4-00C-A).
    heif.ensure_heif_support()
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

    ``source_size`` is the recorded orientation-normalised face frame;
    ``target_size`` is the preview. Each axis uses its actual integer size.
    """
    source_width = float(source_size[0])
    source_height = float(source_size[1])
    target_width = int(target_size[0])
    target_height = int(target_size[1])
    if source_width <= 0 or source_height <= 0:
        raise ValueError("the source frame has no usable size")
    if target_width < 1 or target_height < 1:
        raise ValueError("the target frame has no usable size")
    factor_x = float(target_width) / source_width
    factor_y = float(target_height) / source_height
    center_x = (float(box.left) + float(box.right)) / 2.0 * factor_x
    center_y = (float(box.top) + float(box.bottom)) / 2.0 * factor_y
    side = max(float(box.width) * factor_x, float(box.height) * factor_y) * (
        1.0 + 2.0 * float(margin)
    )
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
            rect = crop_box(box, source_size=source_size, target_size=target_size)
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
