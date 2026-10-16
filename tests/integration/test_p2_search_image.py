"""P2-07 integration tests for ``POST /api/v1/search/image``.

The upload endpoint is served by the same frozen ranking path as a text query, so these
tests check three things that only an integration test can prove: the page comes back in the
``SearchResult`` shape with the binding and the authoritative re-check intact, the uploaded
image never enters the catalog, and every hard bound of architecture §10.3 is enforced with
its own structured refusal and no leftover temporary file.

The image tower itself is a deterministic stand-in injected through the documented
``encoder_factory`` seam; this build ships no encoder at all, which is exactly what
``plain_env`` serves.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from p2_search_api_fixtures import QUERY_VECTORS, SearchEnv, build_search_env
from p2_search_fixtures import (
    FakeImageEncoder,
    assign_place,
    confirm_pending,
    embedding_spec,
    install_source_places,
)
from PIL import Image

from photo_search.api import routes
from photo_search.catalog.errors import RUNTIME_CAPABILITY_UNAVAILABLE
from photo_search.search.upload import UploadLimits

UNKNOWN_FIELD_MESSAGE = "does not define those multipart fields"


@pytest.fixture()
def image_env(tmp_path: Path):
    encoder = FakeImageEncoder(
        embedding_spec(), vectors=QUERY_VECTORS, image_vector=[1.0, 0.0, 0.0]
    )
    env = build_search_env(tmp_path, image_encoder=encoder)
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


@pytest.fixture()
def text_only_env(tmp_path: Path):
    """This build's adapter has the text tower but no image tower at all."""
    env = build_search_env(tmp_path)
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


def jpeg(
    tmp_path: Path,
    *,
    colour: tuple[int, int, int] = (200, 40, 40),
    size: tuple[int, int] = (32, 24),
    name: str = "upload.jpg",
) -> bytes:
    path = tmp_path / name
    Image.new("RGB", size, colour).save(path, "JPEG", quality=90)
    return path.read_bytes()


def upload(
    client: AsyncClient,
    env: SearchEnv,
    csrf: str | None,
    *,
    payload: bytes,
    fields: dict[str, str] | None = None,
    binding: bool = True,
    filename: str = "上传 图片.jpg",
    content_type: str = "image/jpeg",
    headers: dict[str, str] | None = None,
) -> Any:
    """One multipart upload; httpx builds the body, the server parses it without a library."""
    resolved = SearchEnv.post_headers(csrf) if headers is None and csrf else (headers or {})
    data = dict(fields or {})
    # No product API activates a profile yet, so a served upload normally carries the
    # explicit P2-05 triple; the tests that check the refusal pass ``binding=False``.
    if binding and "space_id" not in data:
        data.update(env.explicit_binding())
    return client.post(
        "/api/v1/search/image",
        data=data,
        files={"image": (filename, payload, content_type)},
        headers=resolved,
    )


def snapshot(env: SearchEnv) -> dict[str, Any]:
    """Everything the upload must leave exactly as it was."""
    catalog = env.catalog
    counts: dict[str, int] = {}
    for table in (
        "assets",
        "file_locations",
        "artifacts",
        "index_entries",
        "index_outbox",
        "search_documents",
        "search_projection_queue",
        "search_feedback",
    ):
        counts[table] = int(catalog.read_one(f"SELECT COUNT(*) AS value FROM {table}")["value"])
    return {
        "counts": counts,
        "catalog_filter_revision": int(
            catalog.read_one(
                "SELECT revision FROM catalog_filter_revision WHERE revision_id = 1"
            )["revision"]
        ),
        "suggestion_revision": int(
            catalog.read_one(
                "SELECT revision FROM place_suggestion_revision WHERE revision_id = 1"
            )["revision"]
        ),
        "assets": [
            (int(row["asset_id"]), int(row["created_at"]))
            for row in catalog.read_all("SELECT asset_id, created_at FROM assets ORDER BY asset_id")
        ],
    }


def upload_directories() -> set[str]:
    return {str(path) for path in Path(tempfile.gettempdir()).glob("photo-search-upload-*")}


# --------------------------------------------------------------------- success path
def test_upload_passes_original_exif_bytes_to_the_selected_adapter(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    path = tmp_path / "orientation-6.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (40, 24), (40, 90, 150)).save(path, "JPEG", exif=exif)
    payload = path.read_bytes()
    before = snapshot(image_env)

    async def scenario(client: AsyncClient):
        csrf = await image_env.authenticate(client)
        return await upload(
            client, image_env, csrf, payload=payload, fields={"request_id": "original-exif"}
        )

    response = image_env.scenario(scenario)
    assert response.status_code == 200, response.text
    facts = response.json()["image_query"]
    assert (facts["width"], facts["height"]) == (24, 40)
    encoder = image_env.encoder
    assert encoder.image_payloads == [payload]
    assert encoder.image_sizes == [(40, 24)]
    assert len(encoder.image_paths) == 1
    assert not encoder.image_paths[0].exists()
    assert snapshot(image_env) == before


def test_an_uploaded_image_reuses_the_frozen_ranking_and_writes_nothing(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    payload = jpeg(tmp_path)
    before = snapshot(image_env)
    directories_before = upload_directories()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        response = await upload(
            client, image_env, csrf, payload=payload, fields={"request_id": "img-1"}
        )
        return {"response": response}

    result = image_env.scenario(scenario)
    response = result["response"]
    assert response.status_code == 200, response.text
    body = response.json()

    # The page is the frozen envelope, produced by the same retrieval, RRF and authoritative
    # re-check a text query uses.
    assert body["contract_version"] == "p2-contracts.v1"
    assert body["state"] == "OK"
    assert body["query"] is None
    assert body["image_generation_id"] == image_env.generation
    assert body["space_id"] == "test-space:a1"
    assert body["filters"]["binding"]["generation_id"] == image_env.generation
    assert body["channels"]["fts"]["engaged"] is False
    assert body["channels"]["fts"]["candidates"] == 0
    assert body["channels"]["vector"]["engaged"] is True
    assert body["rerank_state"] == "NOT_REQUESTED"
    assert body["reranked"] is None

    # The pinned image vector is [1, 0, 0]: "beach sunset" is the exact match, so the image
    # query ranks by the same distance ordering the vector channel proven in P2-05 uses.
    hits = body["hits"]
    assert [row["asset_id"] for row in hits][0] == image_env.assets["beach sunset.jpg"]
    assert sorted(row["asset_id"] for row in hits) == sorted(image_env.assets.values())
    for row in hits:
        assert row["hit_reason"] == "SEMANTIC"
        assert row["matched_text"] == []
        assert row["semantic_ready"] is True
        assert list(row["matched_by"]) == ["vector"]

    image = body["image_query"]
    assert image["byte_size"] == len(payload)
    assert (image["width"], image["height"]) == (32, 24)
    assert image["format"] == "JPEG"
    assert image["digest"] == hashlib.sha256(payload).hexdigest()
    assert image["decode_ms"] >= 0
    assert image["limits"] == {
        "max_bytes": 16 * 1024 * 1024,
        "max_pixels": 60_000_000,
        "decode_budget_ms": 20_000,
        "decode_long_edge": 1_600,
    }

    # No path, coordinate or token anywhere in the answer.
    assert str(image_env.data_root) not in response.text
    assert str(image_env.library.root) not in response.text
    assert "latitude" not in response.text

    # The upload is a pure read: nothing at all was added or moved.
    after = snapshot(image_env)
    assert after == before
    # And its temporary file is gone, on the accepting path as well.
    assert upload_directories() == directories_before


def test_the_upload_page_is_bound_to_the_image_it_was_minted_for(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    first = jpeg(tmp_path, name="first.jpg", colour=(10, 10, 200))
    second = jpeg(tmp_path, name="second.jpg", colour=(200, 200, 10))

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        start = await upload(
            client,
            image_env,
            csrf,
            payload=first,
            fields={"request_id": "img-1", "page_size": "1"},
        )
        body = start.json()
        same = await upload(
            client,
            image_env,
            csrf,
            payload=first,
            fields={
                "request_id": "img-2",
                "page_size": "1",
                "search_session_id": body["search_session_id"],
                "cursor": body["next_cursor"],
            },
        )
        other = await upload(
            client,
            image_env,
            csrf,
            payload=second,
            fields={
                "request_id": "img-3",
                "page_size": "1",
                "search_session_id": body["search_session_id"],
                "cursor": body["next_cursor"],
            },
        )
        return {"start": start, "same": same, "other": other}

    result = image_env.scenario(scenario)
    start = result["start"]
    assert start.status_code == 200, start.text
    assert start.json()["has_more"] is True
    assert start.json()["total_candidates"] == len(image_env.assets)

    same = result["same"]
    assert same.status_code == 200, same.text
    assert same.json()["search_session_id"] == start.json()["search_session_id"]
    assert same.json()["page_offset"] == 1
    assert not set(row["asset_id"] for row in same.json()["hits"]) & set(
        row["asset_id"] for row in start.json()["hits"]
    )
    assert same.json()["image_query"]["digest"] == start.json()["image_query"]["digest"]

    # A different image cannot continue the first image's frozen ranking: the session digest
    # is bound to the uploaded bytes, not merely to "an upload".
    other = result["other"]
    assert other.status_code == 422, other.text
    assert other.json()["code"] == "INVALID_CURSOR"


def test_a_lagging_semantic_entry_is_named_instead_of_hidden(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    """A re-classified asset cannot be ranked semantically, so it must be named.

    ADR-0005 §10: filtering old results can only remove stale hits, never add back the asset
    whose projection is late. A manual place assignment re-projects that asset (the P2-03A
    refresh path), so its ``index_entries`` row is no longer CONFIRMED - exactly the state a
    real re-classification leaves behind. The image answer then has to say "semantic index
    still catching up" with a count instead of pretending the photo does not exist.
    """
    late_asset = image_env.assets["beach sunset.jpg"]
    place_id = install_source_places(image_env.catalog)["west_lake"]
    confirm_pending(image_env.catalog)
    assign_place(
        image_env.catalog,
        asset_id=late_asset,
        place_id=place_id,
        request_id="upload-smoke-assign",
    )
    payload = jpeg(tmp_path)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        response = await upload(
            client, image_env, csrf, payload=payload, fields={"request_id": "img-1"}
        )
        return {"response": response}

    result = image_env.scenario(scenario)
    response = result["response"]
    assert response.status_code == 200, response.text
    body = response.json()
    pending = body["pending_semantic"]
    assert pending is not None
    assert pending["count"] >= 1
    assert late_asset in [entry["asset_id"] for entry in pending["entries"]]
    assert pending["reason"] == "ENTRY_NOT_CONFIRMED"
    # The vector channel really cannot rank it, and no fake score was invented for it.
    assert late_asset not in [row["asset_id"] for row in body["hits"]]
    assert all(row["asset_id"] != late_asset for row in body["hits"])


def test_a_profile_binds_an_image_query_without_an_explicit_triple(
    image_env: SearchEnv, tmp_path: Path
) -> None:
    payload = jpeg(tmp_path)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        unbound = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-1"},
            binding=False,
        )
        image_env.create_profile("profile-1")
        activation = await client.post(
            "/api/v1/search-profile/activate",
            json={"profile_id": "profile-1"},
            headers=SearchEnv.post_headers(csrf),
        )
        bound = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-2"},
            binding=False,
        )
        explicit = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-3", **image_env.explicit_binding()},
        )
        return {"unbound": unbound, "activation": activation, "bound": bound, "explicit": explicit}

    await_scenario = image_env.scenario(scenario)
    # Without a profile and without an explicit triple there is no space to encode into.
    unbound = await_scenario["unbound"]
    assert unbound.status_code == 422, unbound.text
    assert unbound.json()["code"] == "VALIDATION_ERROR"
    assert unbound.json()["details"]["field"] == "profile_id"

    assert await_scenario["activation"].status_code == 200, await_scenario["activation"].text
    bound = await_scenario["bound"]
    assert bound.status_code == 200, bound.text
    assert bound.json()["profile_id"] == "profile-1"
    assert bound.json()["image_generation_id"] == image_env.generation
    assert bound.json()["channels"]["vector"]["engaged"] is True
    # An explicit triple that agrees with the profile is still accepted (P2-06A §6.3).
    assert await_scenario["explicit"].status_code == 200


# ------------------------------------------------------------------ capability gates
def test_without_an_image_encoder_the_endpoint_refuses_instead_of_faking_a_result(
    text_only_env: SearchEnv, tmp_path: Path
) -> None:
    payload = jpeg(tmp_path)
    before = snapshot(text_only_env)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await text_only_env.authenticate(client)
        response = await upload(
            client,
            text_only_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-1", **text_only_env.explicit_binding()},
        )
        text = await client.post(
            "/api/v1/search",
            json={"request_id": "txt-1", "text": "sunset", **text_only_env.explicit_binding()},
            headers=SearchEnv.post_headers(csrf),
        )
        return {"image": response, "text": text}

    result = text_only_env.scenario(scenario)
    refusal = result["image"]
    assert refusal.status_code == 409, refusal.text
    assert refusal.json()["code"] == RUNTIME_CAPABILITY_UNAVAILABLE
    assert refusal.json()["details"]["reason"] == "IMAGE_ENCODER_UNAVAILABLE"
    assert "hits" not in refusal.json()
    # The very same build *can* run the text channel: the image endpoint refuses anyway,
    # because "no image tower" must never be answered with a text-channel result.
    assert result["text"].status_code == 200, result["text"].text
    assert result["text"].json()["channels"]["vector"]["engaged"] is True
    assert str(text_only_env.data_root) not in refusal.text
    # The text query legitimately catches up the *derived* P2-04 projection, so only the
    # authoritative facts are compared here; the image path's full no-write assertion is in
    # the first test of this file.
    after = snapshot(text_only_env)
    for table in (
        "assets",
        "file_locations",
        "artifacts",
        "index_entries",
        "index_outbox",
        "search_feedback",
    ):
        assert after["counts"][table] == before["counts"][table]
    assert after["assets"] == before["assets"]
    assert after["catalog_filter_revision"] == before["catalog_filter_revision"]
    assert after["suggestion_revision"] == before["suggestion_revision"]


# ----------------------------------------------------------------------- hard limits
def test_a_body_over_the_byte_ceiling_is_413_without_staging_anything(
    image_env: SearchEnv,
) -> None:
    payload = b"x" * (16 * 1024 * 1024 + 1)
    before = upload_directories()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        response = await upload(
            client, image_env, csrf, payload=payload, fields={"request_id": "img-1"}
        )
        return {"response": response, "health": await client.get("/api/v1/health")}

    result = image_env.scenario(scenario)
    response = result["response"]
    assert response.status_code == 413, response.text
    assert response.json()["code"] == "UPLOAD_TOO_LARGE"
    assert response.json()["details"]["max_bytes"] == 16 * 1024 * 1024
    assert upload_directories() == before


def test_the_pixel_ceiling_is_enforced_before_the_pixels_are_decoded(
    image_env: SearchEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shipped ceiling is asserted by the unit tests; this injection only makes the
    # branch reachable without a 60-megapixel fixture.
    monkeypatch.setattr(routes, "DEFAULT_LIMITS", UploadLimits(max_pixels=100))
    payload = jpeg(tmp_path)
    before = upload_directories()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        return await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-1", **image_env.explicit_binding()},
        )

    response = image_env.scenario(scenario)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "UPLOAD_TOO_MANY_PIXELS"
    assert response.json()["details"] == {
        "width": 32,
        "height": 24,
        "pixels": 768,
        "max_pixels": 100,
    }
    assert upload_directories() == before


def test_the_decode_budget_is_enforced_and_reported(
    image_env: SearchEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "DEFAULT_LIMITS", UploadLimits(decode_budget_ms=-1))
    payload = jpeg(tmp_path)
    before = upload_directories()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        return await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-1", **image_env.explicit_binding()},
        )

    response = image_env.scenario(scenario)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "DECODE_FAILED"
    assert response.json()["details"]["reason"] == "DECODE_TIME_BUDGET_EXCEEDED"
    assert upload_directories() == before


@pytest.mark.parametrize(
    ("payload_name", "content_type", "code"),
    [
        ("page.tif", "image/tiff", "FORMAT_UNSUPPORTED"),
        ("broken.bin", "application/octet-stream", "DECODE_FAILED"),
        ("empty.bin", "application/octet-stream", "VALIDATION_ERROR"),
    ],
)
def test_the_format_whitelist_and_decode_classification_are_honest(
    image_env: SearchEnv,
    tmp_path: Path,
    payload_name: str,
    content_type: str,
    code: str,
) -> None:
    payloads: dict[str, bytes] = {"broken.bin": b"definitely not an image", "empty.bin": b""}
    if payload_name == "page.tif":
        path = tmp_path / payload_name
        Image.new("RGB", (32, 24), (1, 2, 3)).save(path, "TIFF")
        payloads[payload_name] = path.read_bytes()
    payload = payloads[payload_name]
    before = upload_directories()

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        return await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-1", **image_env.explicit_binding()},
            content_type=content_type,
        )

    response = image_env.scenario(scenario)
    expected_status = {"FORMAT_UNSUPPORTED": 409, "DECODE_FAILED": 409, "VALIDATION_ERROR": 422}
    assert response.status_code == expected_status[code], response.text
    assert response.json()["code"] == code
    assert str(image_env.data_root) not in response.text
    assert upload_directories() == before


# --------------------------------------------------------------- request-shape gates
def test_the_endpoint_refuses_a_request_it_cannot_serve(image_env: SearchEnv, tmp_path: Path) -> None:
    payload = jpeg(tmp_path)

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        csrf = await image_env.authenticate(client)
        headers = SearchEnv.post_headers(csrf)
        binding = image_env.explicit_binding()
        missing_file = await client.post(
            "/api/v1/search/image",
            data={"request_id": "img-1", **binding},
            headers=headers,
        )
        unknown_field = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-2", "text": "海滩", **binding},
        )
        partial = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={"request_id": "img-3", "space_id": "test-space:a1"},
            binding=False,
        )
        wrong_generation = await upload(
            client,
            image_env,
            csrf,
            payload=payload,
            fields={
                "request_id": "img-4",
                "space_id": "test-space:a1",
                "image_generation_id": "does-not-exist",
                "dimension": "3",
            },
        )
        async with AsyncClient(
            transport=ASGITransport(app=image_env.app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        ) as anonymous_client:
            anonymous = await anonymous_client.post(
                "/api/v1/search/image",
                data={"request_id": "img-5", **binding},
                files={"image": ("a.jpg", payload, "image/jpeg")},
            )
        no_csrf = await client.post(
            "/api/v1/search/image",
            data={"request_id": "img-6", **binding},
            files={"image": ("a.jpg", payload, "image/jpeg")},
            headers={"Origin": "http://127.0.0.1"},
        )
        return {
            "missing_file": missing_file,
            "unknown_field": unknown_field,
            "partial": partial,
            "wrong_generation": wrong_generation,
            "anonymous": anonymous,
            "no_csrf": no_csrf,
        }

    result = image_env.scenario(scenario)
    assert result["missing_file"].status_code == 422
    assert result["missing_file"].json()["code"] == "VALIDATION_ERROR"
    assert result["missing_file"].json()["details"] == {"field": "image"}

    assert result["unknown_field"].status_code == 422
    assert UNKNOWN_FIELD_MESSAGE in result["unknown_field"].json()["message"]
    assert result["unknown_field"].json()["details"]["fields"] == ["text"]

    assert result["partial"].status_code == 422
    assert result["partial"].json()["details"]["missing"] == [
        "dimension",
        "image_generation_id",
    ]

    assert result["wrong_generation"].status_code == 409
    assert result["wrong_generation"].json()["code"] == "PROCESSOR_MISMATCH"
    assert result["wrong_generation"].json()["details"]["reason"] == "GENERATION_NOT_FOUND"

    # The endpoint is inside the /api/v1 boundary: no session is 401, no CSRF is 403.
    assert result["anonymous"].status_code == 401
    assert result["anonymous"].json()["code"] == "SESSION_REQUIRED"
    assert result["no_csrf"].status_code == 403
    assert result["no_csrf"].json()["code"] == "CSRF_INVALID"
