"""Frozen P1-03 media contract: processor versions, sizes and the input-format policy.

The values here *are* the processing version of a derived artifact (architecture §5.4):
they appear in the artifact path, so changing any of them makes every artifact of the old
version distinguishable and unusable by the new version instead of silently mixing
outputs. Bumping a processor id is therefore part of any change to these numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from photo_search.media import heif

THUMBNAIL = "thumbnail"
PREVIEW = "preview"
KINDS = (THUMBNAIL, PREVIEW)

#: Versioned processor identities. ``@1`` freezes: long edge, JPEG output, quality,
#: LANCZOS resample, EXIF-orientation normalisation, JPEG DCT-scaled decode when the
#: source is larger than the target (never an upscale).
THUMBNAIL_PROCESSOR_ID = "catalog-thumbnail@1"
PREVIEW_PROCESSOR_ID = "catalog-preview@1"
#: P3-06 face avatars. The crop is rendered from the published preview, so its own identity
#: covers the geometry and the source content: a re-detection that moves a face box produces
#: a different cache entry instead of reusing the old pixels.
FACE_CROP_PROCESSOR_ID = "face-crop@1"

THUMBNAIL_LONG_EDGE = 320
PREVIEW_LONG_EDGE = 1600
THUMBNAIL_JPEG_QUALITY = 85
PREVIEW_JPEG_QUALITY = 88
#: A square-ish avatar bound: at most 256 px on the long edge, never enlarged, JPEG q85.
FACE_CROP_LONG_EDGE = 256
FACE_CROP_JPEG_QUALITY = 85
#: How much margin (as a fraction of the face box) is kept around the face before cropping.
#: It only ever widens the request; the crop is clamped into the preview frame afterwards.
FACE_CROP_MARGIN = 0.35

JPEG_SUFFIX = ".jpg"
FAILURE_SUFFIX = ".json"
TEMP_SUFFIX = ".tmp"

#: Input formats this build decodes with Pillow alone, all measured on this machine
#: (P1-03 report §4). P4-02S adds ``MPO`` - the multi-picture JPEG many phones write for
#: portrait/burst shots. What is shown and embedded is the container's **first frame**; P4-02S
#: verified that first frame against opening the same bytes as JPEG (size, mode and pixel
#: bytes) before the format moved out of the refused list.
BASE_SUPPORTED_INPUT_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "GIF", "MPO"})

#: Formats that are recognised and deliberately refused until their handling is verified.
#: The contract forbids reporting them as a finished thumbnail (architecture §5.4), even
#: where Pillow happens to be able to read them (it can read TIFF). ``AVIF`` stays here on
#: purpose: pillow-heif's opener accepts only the HEIF brands, so an AVIF file is still a
#: recognised-but-refused family rather than a decode attempt (P4-00C-A report §2).
REFUSED_INPUT_FORMATS = frozenset(
    {
        "TIFF",
        "AVIF",
        "JXL",
        "ICO",
        "PSD",
        "TGA",
        "PPM",
        "PGM",
        "PBM",
        "PDF",
        "EPS",
        "RAW",
    }
)


def supported_input_formats() -> frozenset[str]:
    """Every format this build can *decode*, never just recognise (architecture §5.4).

    HEIC/HEIF join the set only when the pinned ``pillow-heif`` plugin and its libheif
    runtime really loaded in this process (P4-00C-A). A build that cannot decode HEIC must
    not advertise it, so the capability lists and the refusal detail read this live value.
    """
    if heif.heif_available():
        return BASE_SUPPORTED_INPUT_FORMATS | heif.HEIF_INPUT_FORMATS
    return BASE_SUPPORTED_INPUT_FORMATS


def refused_input_formats() -> frozenset[str]:
    """Every format that is recognised and refused with a structured error."""
    if heif.heif_available():
        return REFUSED_INPUT_FORMATS
    return REFUSED_INPUT_FORMATS | heif.HEIF_INPUT_FORMATS


#: Import-time snapshots of the two lists above. They are what most callers want; the
#: functions exist for the capability report, which has to state the live answer.
SUPPORTED_INPUT_FORMATS = supported_input_formats()
UNSUPPORTED_INPUT_FORMATS = refused_input_formats()


@dataclass(frozen=True)
class MediaSpec:
    """One processing version of one derived media kind."""

    kind: str
    processor_id: str
    long_edge: int
    jpeg_quality: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "processor_id": self.processor_id,
            "long_edge": self.long_edge,
            "output_format": "JPEG",
            "jpeg_quality": self.jpeg_quality,
            "supported_input_formats": sorted(supported_input_formats()),
        }


THUMBNAIL_SPEC = MediaSpec(
    kind=THUMBNAIL,
    processor_id=THUMBNAIL_PROCESSOR_ID,
    long_edge=THUMBNAIL_LONG_EDGE,
    jpeg_quality=THUMBNAIL_JPEG_QUALITY,
)
PREVIEW_SPEC = MediaSpec(
    kind=PREVIEW,
    processor_id=PREVIEW_PROCESSOR_ID,
    long_edge=PREVIEW_LONG_EDGE,
    jpeg_quality=PREVIEW_JPEG_QUALITY,
)
SPECS = {THUMBNAIL: THUMBNAIL_SPEC, PREVIEW: PREVIEW_SPEC}


def spec_for(kind: str) -> MediaSpec:
    try:
        return SPECS[str(kind)]
    except KeyError as error:  # pragma: no cover - argparse restricts the choices
        raise ValueError(f"unknown media kind: {kind!r}") from error
