"""P2-07 unit tests: the multipart reader, the upload limits and the image query vector.

Everything here is a pure function or a temp-directory fixture; no catalog, no HTTP client
and no model is involved, so each branch of the upload boundary is reachable cheaply.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest
from p2_search_fixtures import DIMENSION, SPACE, FakeImageEncoder, embedding_spec
from PIL import Image

from photo_search.api.routes import _image_search_request
from photo_search.catalog.errors import CatalogError
from photo_search.search.contracts import SearchRequest
from photo_search.search.upload import (
    DEFAULT_LIMITS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_PIXELS,
    UPLOAD_DECODE_LONG_EDGE,
    UploadLimits,
    checked_query_vector,
    encode_image_query,
    image_encoder_of,
    image_query_identity,
    parse_multipart_form,
    stage_upload,
)


def multipart(
    parts: list[dict[str, object]], *, boundary: str = "b0undary"
) -> tuple[bytes, str]:
    """One hand-built multipart body; this keeps every malformed variant explicit."""
    chunks: list[bytes] = []
    for part in parts:
        disposition = f'Content-Disposition: form-data; name="{part["name"]}"'
        if "filename" in part:
            disposition += f'; filename="{part["filename"]}"'
        header = f"--{boundary}\r\n{disposition}\r\n"
        if "content_type" in part:
            header += f"Content-Type: {part['content_type']}\r\n"
        chunks.append((header + "\r\n").encode("utf-8"))
        value = part.get("value", b"")
        chunks.append(value if isinstance(value, bytes) else str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def jpeg_bytes(
    tmp_path: Path, *, size: tuple[int, int] = (24, 18), name: str = "a.jpg"
) -> bytes:
    path = tmp_path / name
    Image.new("RGB", size, (12, 34, 56)).save(path, "JPEG", quality=90)
    return path.read_bytes()


# ------------------------------------------------------------------ multipart reader
def test_the_multipart_reader_returns_fields_and_file_parts() -> None:
    body, content_type = multipart(
        [
            {"name": "request_id", "value": "abc"},
            {"name": "page_size", "value": "7"},
            {
                "name": "image",
                "filename": "照片 one.jpg",
                "content_type": "image/jpeg",
                "value": b"\xff\xd8\xff\xe0binary",
            },
        ]
    )
    form = parse_multipart_form(content_type, body, max_body_bytes=1_000_000)
    assert dict(form.fields) == {"request_id": "abc", "page_size": "7"}
    upload = form.files["image"]
    assert upload.filename == "照片 one.jpg"
    assert upload.content_type == "image/jpeg"
    assert upload.payload == b"\xff\xd8\xff\xe0binary"


@pytest.mark.parametrize(
    ("content_type", "problem"),
    [
        (None, "multipart/form-data"),
        ("application/json", "multipart/form-data"),
        ("multipart/form-data", "boundary"),
        ("multipart/form-data; boundary=", "boundary"),
        ("multipart/form-data; boundary=bad boundary", "boundary"),
    ],
)
def test_a_body_that_is_not_a_well_formed_multipart_request_is_422(
    content_type: str | None, problem: str
) -> None:
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(content_type, b"whatever", max_body_bytes=1_000)
    assert failure.value.code == "VALIDATION_ERROR"
    assert problem in failure.value.message


@pytest.mark.parametrize(
    "body",
    [
        b"preamble--b0undary\r\n",
        b'--b0undaryContent-Disposition: form-data; name="a"\r\n\r\nx\r\n--b0undary--\r\n',
        b'--b0undary\r\nContent-Disposition: form-data; name="a"\r\nx\r\n--b0undary--\r\n',
        b'--b0undary\r\nname="a"\r\n\r\nx\r\n--b0undary--\r\n',
        b'--b0undary\r\nContent-Disposition: attachment; name="a"\r\n\r\nx\r\n--b0undary--\r\n',
        b'--b0undary\r\nContent-Disposition: form-data\r\n\r\nx\r\n--b0undary--\r\n',
    ],
)
def test_a_malformed_part_is_refused_instead_of_guessed(body: bytes) -> None:
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(
            "multipart/form-data; boundary=b0undary", body, max_body_bytes=1_000
        )
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.details == {"field": "image"}


def test_a_duplicate_field_name_is_refused() -> None:
    body, content_type = multipart(
        [{"name": "request_id", "value": "a"}, {"name": "request_id", "value": "b"}]
    )
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(content_type, body, max_body_bytes=1_000)
    assert failure.value.code == "VALIDATION_ERROR"


def test_a_non_utf8_field_value_is_refused() -> None:
    body, content_type = multipart([{"name": "request_id", "value": b"\xff\xfe"}])
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(content_type, body, max_body_bytes=1_000)
    assert failure.value.code == "VALIDATION_ERROR"


def test_an_oversized_multipart_body_is_refused_before_it_is_scanned() -> None:
    body, content_type = multipart([{"name": "request_id", "value": "a"}])
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(content_type, body, max_body_bytes=4)
    assert failure.value.code == "UPLOAD_TOO_LARGE"
    assert failure.value.details == {"byte_size": len(body), "max_bytes": 4}


def test_too_many_parts_are_refused() -> None:
    parts = [{"name": f"field{index}", "value": "x"} for index in range(40)]
    body, content_type = multipart(parts)
    with pytest.raises(CatalogError) as failure:
        parse_multipart_form(content_type, body, max_body_bytes=1_000_000)
    assert failure.value.code == "VALIDATION_ERROR"
    assert "at most" in failure.value.message


# -------------------------------------------------------------------- staging/limits
def test_the_shipped_limits_are_the_documented_ones() -> None:
    assert DEFAULT_LIMITS.max_bytes == MAX_UPLOAD_BYTES == 16 * 1024 * 1024
    assert DEFAULT_LIMITS.max_pixels == MAX_UPLOAD_PIXELS == 60_000_000
    assert DEFAULT_LIMITS.decode_long_edge == UPLOAD_DECODE_LONG_EDGE == 1600
    assert DEFAULT_LIMITS.decode_budget_ms == 20_000
    assert DEFAULT_LIMITS.as_dict() == {
        "max_bytes": MAX_UPLOAD_BYTES,
        "max_pixels": MAX_UPLOAD_PIXELS,
        "decode_budget_ms": 20_000,
        "decode_long_edge": 1_600,
    }


def test_an_accepted_upload_reports_its_measured_facts_and_is_removed_afterwards(
    tmp_path: Path,
) -> None:
    payload = jpeg_bytes(tmp_path)
    with stage_upload(payload) as staged:
        directory = staged.directory
        assert directory.is_dir()
        assert staged.path.is_file()
        assert staged.facts.byte_size == len(payload)
        assert (staged.facts.width, staged.facts.height) == (24, 18)
        assert staged.facts.format == "JPEG"
        assert staged.facts.digest == hashlib.sha256(payload).hexdigest()
        assert staged.facts.decode_ms >= 0
        assert staged.image.size == (24, 18)
    assert not directory.exists()


def test_exif_orientation_is_applied_before_the_image_reaches_the_adapter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (24, 18), (12, 34, 56))
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(path, "JPEG", quality=90, exif=exif)
    with stage_upload(path.read_bytes()) as staged:
        # Orientation 6 means the upright frame is the stored one rotated, so the upright
        # size is the swapped one: the adapter sees exactly the pixels the user sees.
        assert (staged.facts.width, staged.facts.height) == (18, 24)


def test_an_empty_upload_is_a_caller_error() -> None:
    with pytest.raises(CatalogError) as failure:
        with stage_upload(b""):
            pass  # pragma: no cover - the context manager refuses before yielding
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.details == {"field": "image"}


def test_a_payload_over_the_byte_ceiling_is_413_and_leaves_no_temp_directory(
    tmp_path: Path,
) -> None:
    before = set(Path(tempfile.gettempdir()).glob("photo-search-upload-*"))
    limits = UploadLimits(max_bytes=32)
    with pytest.raises(CatalogError) as failure:
        with stage_upload(jpeg_bytes(tmp_path), limits=limits):
            pass  # pragma: no cover - refused before staging
    assert failure.value.code == "UPLOAD_TOO_LARGE"
    assert failure.value.details["max_bytes"] == 32
    assert set(Path(tempfile.gettempdir()).glob("photo-search-upload-*")) == before


def test_a_pixel_count_over_the_ceiling_is_refused_before_decoding(tmp_path: Path) -> None:
    before = set(Path(tempfile.gettempdir()).glob("photo-search-upload-*"))
    limits = UploadLimits(max_pixels=100)
    with pytest.raises(CatalogError) as failure:
        with stage_upload(jpeg_bytes(tmp_path), limits=limits):
            pass  # pragma: no cover - refused during validation
    assert failure.value.code == "UPLOAD_TOO_MANY_PIXELS"
    assert failure.value.details == {
        "width": 24,
        "height": 18,
        "pixels": 432,
        "max_pixels": 100,
    }
    assert set(Path(tempfile.gettempdir()).glob("photo-search-upload-*")) == before


def test_a_decode_over_the_time_budget_is_a_named_decode_failure(tmp_path: Path) -> None:
    limits = UploadLimits(decode_budget_ms=-1)
    with pytest.raises(CatalogError) as failure:
        with stage_upload(jpeg_bytes(tmp_path), limits=limits):
            pass  # pragma: no cover - refused during validation
    assert failure.value.code == "DECODE_FAILED"
    assert failure.value.details["reason"] == "DECODE_TIME_BUDGET_EXCEEDED"
    assert failure.value.details["budget_ms"] == -1


def test_a_format_outside_the_frozen_whitelist_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "page.tif"
    Image.new("RGB", (24, 18), (12, 34, 56)).save(path, "TIFF")
    with pytest.raises(CatalogError) as failure:
        with stage_upload(path.read_bytes()):
            pass  # pragma: no cover - refused during validation
    assert failure.value.code == "FORMAT_UNSUPPORTED"
    assert failure.value.details["format"] == "TIFF"
    assert "JPEG" in failure.value.details["supported_formats"]


def test_an_unreadable_payload_is_a_decode_failure_without_any_path() -> None:
    with pytest.raises(CatalogError) as failure:
        with stage_upload(b"this is not an image at all"):
            pass  # pragma: no cover - refused during validation
    assert failure.value.code == "DECODE_FAILED"
    assert failure.value.details == {"field": "image"}
    assert "\\" not in failure.value.message and "/" not in failure.value.message


def test_a_recognised_but_unsupported_family_is_not_reported_as_a_decode_failure() -> None:
    # An AVIF box: Pillow cannot identify it, and the P1-03 policy refuses the family
    # explicitly instead of calling it corrupt. P4-02T moved the HEIF family into the
    # whitelist, so this test states the invariant for a family that is still refused.
    payload = b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1" + b"\x00" * 32
    with pytest.raises(CatalogError) as failure:
        with stage_upload(payload):
            pass  # pragma: no cover - refused during validation
    assert failure.value.code == "FORMAT_UNSUPPORTED"
    assert failure.value.details["format"] == "AVIF"


# ------------------------------------------------------------------- image encoding
def test_only_an_adapter_with_an_image_tower_counts_as_an_image_encoder() -> None:
    spec = embedding_spec()
    assert image_encoder_of(None) is None
    assert image_encoder_of(object()) is None
    encoder = FakeImageEncoder(spec, vectors={})
    assert image_encoder_of(encoder) is encoder


def test_the_image_vector_is_validated_like_the_text_vector(tmp_path: Path) -> None:
    spec = embedding_spec()
    encoder = FakeImageEncoder(spec, vectors={}, image_vector=[1.0, 0.0, 0.0])
    with stage_upload(jpeg_bytes(tmp_path)) as staged:
        vector = encode_image_query(
            encoder, staged.image, space_id=SPACE, dimension=DIMENSION
        )
    assert vector == [1.0, 0.0, 0.0]
    assert encoder.image_sizes == [(24, 18)]

    wrong_dimension = FakeImageEncoder(spec, vectors={}, image_vector=[1.0, 0.0])
    with stage_upload(jpeg_bytes(tmp_path)) as staged:
        with pytest.raises(CatalogError) as failure:
            encode_image_query(
                wrong_dimension, staged.image, space_id=SPACE, dimension=DIMENSION
            )
    assert failure.value.code == "RUNTIME_RESOLUTION_MISMATCH"

    wrong_space = FakeImageEncoder(spec, vectors={}, image_vector=[1.0, 0.0, 0.0])
    with stage_upload(jpeg_bytes(tmp_path)) as staged:
        with pytest.raises(CatalogError) as failure:
            encode_image_query(
                wrong_space, staged.image, space_id="other-space", dimension=DIMENSION
            )
    assert failure.value.code == "SPACE_MISMATCH"


def test_a_failing_or_non_finite_image_encoder_is_refused(tmp_path: Path) -> None:
    spec = embedding_spec()
    broken = FakeImageEncoder(spec, vectors={}, image_error=RuntimeError("boom"))
    with stage_upload(jpeg_bytes(tmp_path)) as staged:
        with pytest.raises(CatalogError) as failure:
            encode_image_query(broken, staged.image, space_id=SPACE, dimension=DIMENSION)
    assert failure.value.code == "RUNTIME_RESOLUTION_MISMATCH"
    assert failure.value.details == {"reason": "RuntimeError"}

    for raw in ([1.0, float("nan"), 0.0], [1.0, float("inf"), 0.0], [1.0, True, 0.0]):
        with pytest.raises(CatalogError) as failure:
            checked_query_vector(raw, dimension=DIMENSION)
        assert failure.value.code == "RUNTIME_RESOLUTION_MISMATCH"
    with pytest.raises(CatalogError) as failure:
        checked_query_vector("not a vector", dimension=DIMENSION)
    assert failure.value.details["actual_dimension"] is None


def test_the_image_query_identity_is_the_uploaded_bytes(tmp_path: Path) -> None:
    payload = jpeg_bytes(tmp_path)
    with stage_upload(payload) as staged:
        identity = image_query_identity(staged.facts)
        other = image_query_identity(
            type(staged.facts)(
                byte_size=1, width=1, height=1, format="JPEG", digest="b" * 64, decode_ms=0
            )
        )
    assert identity == "image:" + hashlib.sha256(payload).hexdigest()
    assert identity != other


# ------------------------------------------------------------------ form conversion
def test_the_multipart_fields_become_the_frozen_search_envelope() -> None:
    request = _image_search_request(
        {
            "request_id": "r1",
            "source_id": "2",
            "availability": "present",
            "place_ids": "3,4,4",
            "place_state": "assigned",
            "include_descendants": "false",
            "page_size": "5",
            "top_k": "10",
        }
    )
    assert isinstance(request, SearchRequest)
    assert request.text is None
    assert request.request_id == "r1"
    assert request.source_id == 2
    assert request.place_ids == (3, 4)
    assert request.place_state == "assigned"
    assert request.include_descendants is False
    assert request.page_size == 5 and request.top_k == 10


def test_a_text_field_is_not_part_of_the_image_endpoint() -> None:
    with pytest.raises(CatalogError) as failure:
        _image_search_request({"request_id": "r1", "text": "海滩"})
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.details == {"fields": ["text"]}


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"request_id": ""},
        {"request_id": "r", "page_size": "0"},
        {"request_id": "r", "page_size": "101"},
        {"request_id": "r", "top_k": "5001"},
        {"request_id": "r", "dimension": "0"},
        {"request_id": "r", "dimension": "three"},
        {"request_id": "r", "place_ids": "3,x"},
        {"request_id": "r", "availability": "sometimes"},
        {"request_id": "r", "include_descendants": "maybe"},
        {"request_id": "r", "contract_version": "p1-contracts.v1"},
    ],
)
def test_an_invalid_image_form_is_a_structured_422(fields: dict[str, str]) -> None:
    with pytest.raises(CatalogError) as failure:
        _image_search_request(fields)
    assert failure.value.code == "VALIDATION_ERROR"


def test_a_partially_bound_image_form_is_refused_by_the_frozen_triple_rule() -> None:
    """The image endpoint reuses P2-05's all-or-nothing binding, not a second rule."""
    request = _image_search_request({"request_id": "r", "space_id": "s"})
    with pytest.raises(CatalogError) as failure:
        request.explicit_binding
    assert failure.value.code == "VALIDATION_ERROR"
    assert failure.value.details == {"missing": ["dimension", "image_generation_id"]}


def test_an_empty_place_id_list_is_an_empty_filter() -> None:
    assert _image_search_request({"request_id": "r", "place_ids": ""}).place_ids == ()
