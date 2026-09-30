"""P2-07 upload-search input boundary: form parsing, hard limits, temp-file lifecycle.

Architecture §10.3 freezes the boundary this module owns: *"上传图有大小、像素数和解码时间
限制，临时文件自动回收"*, and *"文件接口只接受资产 ID"* — an upload is the one deliberate
exception, and even then it is only ever read as bytes: nothing about it is added to the
catalog, no asset row is created and no index row is written.

Three facts shape the implementation:

1. **No new runtime dependency.** ``python-multipart`` is not installed in this build and
   this task may not add one, so :func:`parse_multipart_form` is a small, strict RFC 7578
   reader that refuses anything it does not fully understand. It never allocates more than
   the byte ceiling it is given, and it is a pure function over ``(content_type, body)``, so
   the API layer stays the only place that knows about HTTP.
2. **The catalog is never touched here.** The upload is staged in a private temporary
   directory, validated there, and that directory is removed again on *both* the success and
   the failure path (:func:`stage_upload`).
3. **The image tower stays behind the ADR-0003 §7 adapter.** Encoding is done by the
   deployment-supplied adapter (``encode_images``). A process without one - the default
   packaging, or a machine with no registered pinned model - answers a structured
   ``RUNTIME_CAPABILITY_UNAVAILABLE``/``IMAGE_ENCODER_UNAVAILABLE`` instead of pretending
   the text channel found something (P4-02N wires the real adapter when the model exists).

The limits themselves are data (:class:`UploadLimits`) so a test can drive every branch
without generating a 16 MiB photo, and so the served page can state the exact numbers the
server enforces.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from PIL import Image, UnidentifiedImageError

from photo_search.catalog.errors import (
    DECODE_FAILED,
    FORMAT_UNSUPPORTED,
    RUNTIME_RESOLUTION_MISMATCH,
    UPLOAD_TOO_LARGE,
    UPLOAD_TOO_MANY_PIXELS,
    CatalogError,
)
from photo_search.media import heif
from photo_search.media.contract import refused_input_formats
from photo_search.media.decode import HEADER_BYTES, sniff_format, upright_image
from photo_search.scheduler.contracts import ProcessorSpec
from photo_search.search.embedding import check_encoder_binding
from photo_search.search.tokens import VALIDATION_ERROR

#: Hard ceiling for one uploaded image (architecture §10.3). 16 MiB is far above a camera
#: JPEG and far below anything that would make the staging copy a problem.
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
#: Hard ceiling for the *source* pixel count, checked on the header before any pixel is
#: decoded. 60 MP covers a 48 MP phone photo and a 50 MP full-frame file.
MAX_UPLOAD_PIXELS = 60_000_000
#: The wall-clock budget of one decode. Measured, and reported in the refusal, rather than
#: enforced by killing a thread — the same honest "bounded wait, not preemption" rule the
#: P2-06B rerank adapter uses.
DECODE_BUDGET_MS = 20_000
#: JPEG inputs are decoded with Pillow's DCT shortcut at most this long edge. This is the
#: same order as the frozen P1-03 preview contract (1600) and only ever *reduces* the
#: resolution of an oversized input; the adapter's ``image_preprocess_version`` owns the
#: final resize.
UPLOAD_DECODE_LONG_EDGE = 1600
#: The private staging directory prefix; every one of them is removed before the response
#: is sent, so a leftover directory is a defect a test can see.
TEMP_DIRECTORY_PREFIX = "photo-search-upload-"
#: The whitelist of this *query* input, amended by P4-02T. It is still a deliberate, literal
#: list and not a follow of the library's decode capability - an upload is never catalogued,
#: so accepting one more family stays an explicit protocol change P2-07's assertions state.
#: What changed is the *set*: the library already indexes and displays ``MPO`` (first frame,
#: P4-02S) and the HEIF family (P4-00C-A), so refusing a phone's own portrait/burst photo
#: here made "upload search" unusable on the very files the grid shows.
UPLOAD_INPUT_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "GIF", "MPO", "HEIC", "HEIF"})
#: A multipart body may carry at most this many parts (one image plus the declared fields).
MULTIPART_MAX_PARTS = 32
#: The body ceiling includes the multipart framing, so it is the image ceiling plus room
#: for the headers and the form fields.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
_BOUNDARY_PATTERN = re.compile(r"^[0-9A-Za-z'()+\-_,./:=?]{1,70}$")
_PARAMETER_PATTERN = re.compile(r';\s*([A-Za-z0-9_-]+)\s*=\s*(?:"([^"]*)"|([^;]*))')
_HEADER_LINE_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+:[ \t]*[^\r\n]*$")


@dataclass(frozen=True)
class UploadLimits:
    """Every hard bound this build enforces on one uploaded image."""

    max_bytes: int = MAX_UPLOAD_BYTES
    max_pixels: int = MAX_UPLOAD_PIXELS
    decode_budget_ms: int = DECODE_BUDGET_MS
    decode_long_edge: int = UPLOAD_DECODE_LONG_EDGE

    @property
    def max_body_bytes(self) -> int:
        """The multipart body ceiling: the image ceiling plus framing room."""
        return int(self.max_bytes) + MULTIPART_OVERHEAD_BYTES

    def as_dict(self) -> dict[str, int]:
        return {
            "max_bytes": int(self.max_bytes),
            "max_pixels": int(self.max_pixels),
            "decode_budget_ms": int(self.decode_budget_ms),
            "decode_long_edge": int(self.decode_long_edge),
        }


DEFAULT_LIMITS = UploadLimits()


@dataclass(frozen=True)
class MultipartFile:
    """One uploaded file part: its declared name and its bytes, never its local path."""

    field_name: str
    payload: bytes
    filename: str | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class MultipartForm:
    """One parsed ``multipart/form-data`` body: plain fields plus file parts."""

    fields: Mapping[str, str] = field(default_factory=dict)
    files: Mapping[str, MultipartFile] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadedImage:
    """The measured facts of one accepted upload; contains no path and no coordinates."""

    byte_size: int
    width: int
    height: int
    format: str
    digest: str
    decode_ms: int

    def to_envelope(self, *, limits: UploadLimits = DEFAULT_LIMITS) -> dict[str, Any]:
        return {
            "byte_size": int(self.byte_size),
            "width": int(self.width),
            "height": int(self.height),
            "format": str(self.format),
            "digest": str(self.digest),
            "decode_ms": int(self.decode_ms),
            "limits": limits.as_dict(),
        }


@dataclass(frozen=True)
class StagedUpload:
    """One validated upload living in a private temporary directory."""

    directory: Path
    path: Path
    facts: UploadedImage
    image: Image.Image


@runtime_checkable
class ImageEncoder(Protocol):
    """The image half of an ADR-0003 §7 ``EmbeddingAdapter``."""

    def spec(self) -> ProcessorSpec:
        """The immutable ProcessorSpec of the model that produces this space."""

    def encode_images(
        self, asset_inputs: Sequence[Any], cancellation: Any = None
    ) -> list[list[float]]:
        """Encode images; the result must carry the spec's space and dimension."""


# ------------------------------------------------------------------ multipart parsing
def parse_multipart_form(
    content_type: str | None, body: bytes, *, max_body_bytes: int
) -> MultipartForm:
    """Read one ``multipart/form-data`` body, or refuse it as a structured 422.

    The reader is deliberately strict and small: it accepts exactly the shape the front end
    (and any ordinary client) sends, and refuses a preamble, an unknown disposition, a
    non-text field value, a folded header, a duplicate part name or anything that does not
    terminate. ``max_body_bytes`` is checked first so an oversized body is refused before it
    is scanned.
    """
    if len(body) > int(max_body_bytes):
        raise _too_large(len(body), max_body_bytes=int(max_body_bytes))
    boundary = _boundary_of(content_type)
    delimiter = b"--" + boundary
    if not body.startswith(delimiter):
        raise _invalid("the multipart body does not start with its declared boundary")

    fields: dict[str, str] = {}
    files: dict[str, MultipartFile] = {}
    segments = body.split(delimiter)
    parts = 0
    for raw in segments[1:]:
        if raw.startswith(b"--"):
            # The closing delimiter; any epilogue after it is ignored, as RFC 7578 allows.
            break
        parts += 1
        if parts > MULTIPART_MAX_PARTS:
            raise _invalid(f"a multipart body may carry at most {MULTIPART_MAX_PARTS} parts")
        if not raw.startswith(b"\r\n"):
            raise _invalid("a multipart part must begin with CRLF")
        head, separator, content = raw[2:].partition(b"\r\n\r\n")
        if not separator:
            raise _invalid("a multipart part has no header terminator")
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = _part_headers(head)
        name, filename = _disposition(headers.get("content-disposition"))
        if name in fields or name in files:
            raise _invalid(f"the multipart field {name!r} appears more than once")
        if filename is None:
            fields[name] = _decode_text(content, name)
            continue
        files[name] = MultipartFile(
            field_name=name,
            payload=bytes(content),
            filename=filename,
            content_type=headers.get("content-type"),
        )
    return MultipartForm(fields=fields, files=files)


def _boundary_of(content_type: str | None) -> bytes:
    text = "" if content_type is None else str(content_type)
    media_type, _, parameters = text.partition(";")
    if media_type.strip().lower() != "multipart/form-data":
        raise _invalid("this endpoint accepts multipart/form-data only")
    boundary = ""
    for match in _PARAMETER_PATTERN.finditer(";" + parameters):
        key = match.group(1).lower()
        if key == "boundary":
            boundary = match.group(2) if match.group(2) is not None else (match.group(3) or "")
    if not _BOUNDARY_PATTERN.match(boundary):
        raise _invalid("the multipart boundary is missing or malformed")
    return boundary.encode("ascii")


def _part_headers(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid("a multipart part header is not valid UTF-8") from error
    headers: dict[str, str] = {}
    for line in text.split("\r\n"):
        if not _HEADER_LINE_PATTERN.match(line):
            raise _invalid("a multipart part header line is malformed")
        name, _, value = line.partition(":")
        key = name.strip().lower()
        if key in headers:
            raise _invalid(f"the multipart header {key} appears more than once")
        headers[key] = value.strip()
    return headers


def _disposition(raw: str | None) -> tuple[str, str | None]:
    if raw is None:
        raise _invalid("a multipart part has no Content-Disposition header")
    kind, _, parameters = raw.partition(";")
    if kind.strip().lower() != "form-data":
        raise _invalid("a multipart part must be form-data")
    name: str | None = None
    filename: str | None = None
    for match in _PARAMETER_PATTERN.finditer(";" + parameters):
        key = match.group(1).lower()
        value = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        if key == "name":
            name = value
        elif key == "filename":
            filename = value
    if not name:
        raise _invalid("a multipart part must declare a field name")
    return name, filename


def _decode_text(content: bytes, name: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid(f"the multipart field {name!r} is not valid UTF-8") from error


# ---------------------------------------------------------------- staging and validation
@contextmanager
def stage_upload(
    payload: bytes, *, limits: UploadLimits | None = None
) -> Iterator[StagedUpload]:
    """Validate one uploaded image and remove its temporary file afterwards, always.

    The directory is created with :func:`tempfile.mkdtemp` (never inside a source root and
    never inside the catalog), and the ``finally`` clause runs for a refused input exactly as
    it does for an accepted one, so no failure path can leave a temporary file behind.
    """
    bound = limits or DEFAULT_LIMITS
    if not payload:
        raise _invalid_upload("the uploaded image is empty")
    if len(payload) > int(bound.max_bytes):
        raise _too_large(len(payload), max_body_bytes=int(bound.max_bytes))
    directory = Path(tempfile.mkdtemp(prefix=TEMP_DIRECTORY_PREFIX))
    image: Image.Image | None = None
    try:
        path = directory / "upload.bin"
        path.write_bytes(payload)
        facts, image = _decode_upload(path, payload, limits=bound)
        yield StagedUpload(directory=directory, path=path, facts=facts, image=image)
    finally:
        if image is not None:
            image.close()
        shutil.rmtree(directory, ignore_errors=True)


def _decode_upload(
    path: Path, payload: bytes, *, limits: UploadLimits
) -> tuple[UploadedImage, Image.Image]:
    """Decode and measure one staged upload, or refuse it with a stable code."""
    started = time.monotonic()
    try:
        # The single registration point: without it a HEIC upload would decode only when some
        # earlier request happened to register the plugin in this process (P4-02T).
        heif.ensure_heif_support()
        with Image.open(path) as source:
            label = str(source.format or "").upper()
            if label not in UPLOAD_INPUT_FORMATS:
                raise _unsupported(label or "UNKNOWN")
            width, height = int(source.size[0]), int(source.size[1])
            pixels = width * height
            if pixels > int(limits.max_pixels):
                raise CatalogError(
                    UPLOAD_TOO_MANY_PIXELS,
                    "the uploaded image has more pixels than this build decodes",
                    details={
                        "width": width,
                        "height": height,
                        "pixels": pixels,
                        "max_pixels": int(limits.max_pixels),
                    },
                )
            upright = upright_image(source, long_edge=int(limits.decode_long_edge))
            decoded_width, decoded_height = int(upright.size[0]), int(upright.size[1])
    except CatalogError:
        raise
    except UnidentifiedImageError as error:
        sniffed = _sniff(path)
        if sniffed is not None and sniffed in upload_refused_families():
            raise _unsupported(sniffed) from error
        raise _decode_failed(
            f"the upload is not a readable image ({type(error).__name__})"
        ) from error
    except Image.DecompressionBombError as error:
        raise CatalogError(
            UPLOAD_TOO_MANY_PIXELS,
            "the uploaded image has more pixels than this build decodes",
            details={"max_pixels": int(limits.max_pixels)},
        ) from error
    except MemoryError as error:
        raise _decode_failed("there was not enough memory to decode the upload") from error
    except OSError as error:
        raise _decode_failed(
            f"the upload could not be decoded ({type(error).__name__})"
        ) from error

    decode_ms = int((time.monotonic() - started) * 1000)
    if decode_ms > int(limits.decode_budget_ms):
        upright.close()
        raise CatalogError(
            DECODE_FAILED,
            "decoding the uploaded image exceeded the time budget",
            details={
                "reason": "DECODE_TIME_BUDGET_EXCEEDED",
                "decode_ms": decode_ms,
                "budget_ms": int(limits.decode_budget_ms),
            },
        )
    return (
        UploadedImage(
            byte_size=len(payload),
            width=decoded_width,
            height=decoded_height,
            format=label,
            digest=hashlib.sha256(payload).hexdigest(),
            decode_ms=decode_ms,
        ),
        upright,
    )


def _sniff(path: Path) -> str | None:
    """Classify an unidentifiable upload from its magic bytes, never from its name."""
    try:
        with path.open("rb") as stream:
            return sniff_format(stream.read(HEADER_BYTES))
    except OSError:  # pragma: no cover - the file was just written by this process
        return None


def upload_refused_families() -> frozenset[str]:
    """Families this boundary recognises and refuses with ``FORMAT_UNSUPPORTED``.

    P4-02T: this is exactly the media contract's refused set. The HEIF family used to be
    added here because it was decodable by the library yet outside this endpoint's
    whitelist; now that the whitelist covers it, a *broken* HEIC upload is honestly a
    ``DECODE_FAILED`` (the rule the media path documents) instead of a bogus "this build
    does not support HEIC".
    """
    return refused_input_formats()


# ------------------------------------------------------------------- image query vector
def image_encoder_of(encoder: Any) -> Any | None:
    """The image half of an injected ADR-0003 adapter, or ``None`` when it has none.

    The frozen adapter is one object with both towers, so an encoder that cannot encode an
    image is simply the honest answer "this process has no image tower" — the endpoint then
    refuses structurally instead of falling back to the text channel.
    """
    if encoder is None or not callable(getattr(encoder, "encode_images", None)):
        return None
    return encoder


def encode_image_query(
    encoder: Any, image: Image.Image, *, space_id: str, dimension: int
) -> list[float]:
    """One validated query vector for the uploaded image.

    The result is checked exactly like P2-05's text query vector (one finite vector of the
    bound dimension, and only for the bound space), so the image tower can never rank inside
    a space it does not produce.
    """
    check_encoder_binding(encoder.spec(), space_id=space_id, dimension=int(dimension))
    try:
        vectors = encoder.encode_images([image])
    except CatalogError:
        raise
    except Exception as error:
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the image encoder failed",
            details={"reason": type(error).__name__},
        ) from error
    if not isinstance(vectors, (list, tuple)) or len(vectors) != 1:
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "one uploaded image must produce exactly one query vector",
            details={"vectors": None if not isinstance(vectors, (list, tuple)) else len(vectors)},
        )
    return checked_query_vector(vectors[0], dimension=int(dimension))


def checked_query_vector(raw: Any, *, dimension: int) -> list[float]:
    """Re-validate a vector produced outside the text-encoding path (P2-07 image query)."""
    if not isinstance(raw, (list, tuple)) or len(raw) != int(dimension):
        raise CatalogError(
            RUNTIME_RESOLUTION_MISMATCH,
            "the image query vector does not match the bound dimension",
            details={
                "expected_dimension": int(dimension),
                "actual_dimension": None if not isinstance(raw, (list, tuple)) else len(raw),
            },
        )
    values: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the image query vector contains a value that is not a finite number",
            )
        number = float(value)
        if not math.isfinite(number):
            raise CatalogError(
                RUNTIME_RESOLUTION_MISMATCH,
                "the image query vector contains a value that is not a finite number",
            )
        values.append(number)
    return values


def image_query_identity(facts: UploadedImage) -> str:
    """The per-image part of a search-session digest, so pages cannot be re-bound.

    A cursor minted for one uploaded image must never be replayed against another: the image
    itself has no text, so its SHA-256 stands in for the query in the frozen ``query_hash``
    (which is documented as the set of conditions that must stay identical across pages).
    """
    return f"image:{facts.digest}"


# --------------------------------------------------------------------------- refusals
def _invalid(message: str) -> CatalogError:
    return CatalogError(VALIDATION_ERROR, message, details={"field": "image"})


def _invalid_upload(message: str) -> CatalogError:
    return CatalogError(VALIDATION_ERROR, message, details={"field": "image"})


def _too_large(byte_size: int, *, max_body_bytes: int) -> CatalogError:
    return CatalogError(
        UPLOAD_TOO_LARGE,
        "the uploaded image is larger than this build accepts",
        details={"byte_size": int(byte_size), "max_bytes": int(max_body_bytes)},
    )


def _unsupported(label: str) -> CatalogError:
    return CatalogError(
        FORMAT_UNSUPPORTED,
        f"format {label} is not supported by this build yet",
        details={
            "format": str(label),
            "supported_formats": sorted(UPLOAD_INPUT_FORMATS),
        },
    )


def _decode_failed(message: str) -> CatalogError:
    """A decode failure with no path in it: the upload has no library location at all."""
    return CatalogError(DECODE_FAILED, message, details={"field": "image"})


__all__ = [
    "DEFAULT_LIMITS",
    "DECODE_BUDGET_MS",
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_PIXELS",
    "MULTIPART_MAX_PARTS",
    "MULTIPART_OVERHEAD_BYTES",
    "TEMP_DIRECTORY_PREFIX",
    "UPLOAD_INPUT_FORMATS",
    "UPLOAD_DECODE_LONG_EDGE",
    "ImageEncoder",
    "MultipartFile",
    "MultipartForm",
    "StagedUpload",
    "UploadLimits",
    "UploadedImage",
    "checked_query_vector",
    "encode_image_query",
    "image_encoder_of",
    "image_query_identity",
    "parse_multipart_form",
    "stage_upload",
    "upload_refused_families",
]
