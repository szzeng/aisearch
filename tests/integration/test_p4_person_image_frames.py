"""Actual draft decode and verified document publication, with synthetic pixels only."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import stamp
from PIL import Image, ImageDraw

from photo_search.artifacts.store import ArtifactStore
from photo_search.face import contract
from photo_search.face.engine import DetectedFace, analyze_image
from photo_search.face.processor import (
    _decode,
    artifact_ref,
    build_payload,
    persist_face_document,
    validate_payload,
)
from photo_search.media.contract import PREVIEW_SPEC
from photo_search.media.face_crop import crop_key, parse_bbox, probe_original_frame, render_crop
from photo_search.scheduler.contracts import ArtifactRef


class SyntheticEngine:
    def detect(self, blob):
        return []

    def recognize(self, aligned):
        return (1.0,) + (0.0,) * 511


def publish_draft_face(env, *, rotated=False):
    path = env.library / "draft.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (4000, 3000), (90, 90, 90))
    ImageDraw.Draw(image).rectangle((2700, 1900, 3300, 2600), fill=(5, 10, 240))
    exif = Image.Exif()
    exif[0x0112] = 6 if rotated else 1
    image.save(path, "JPEG", quality=95, exif=exif)
    stamp(path)
    env.scan(env.register_source()["source_id"])
    asset = env.service.catalog.read_one("SELECT asset_id, sha256 FROM assets LIMIT 1")
    decoded = _decode(path)
    assert max(decoded.image.size) == 2000  # Real Pillow JPEG draft; full source is 4000.
    # Blue rectangle centre in the normalised working raster (long edge 1280).
    cx, cy = (240, 960) if rotated else (960, 720)
    face = DetectedFace(
        0,
        (cx - 55, cy - 55, cx + 55, cy + 55),
        ((cx - 25, cy - 15), (cx + 25, cy - 15), (cx, cy), (cx - 20, cy + 25), (cx + 20, cy + 25)),
        0.99,
    )
    with patch("photo_search.face.engine.decode_detections", return_value=[face]):
        document = analyze_image(SyntheticEngine(), decoded.image)
    payload = build_payload(
        input_revision=str(asset["sha256"]),
        location_id=None,
        document=document,
        decode_format="JPEG",
    )
    validate_payload(payload)
    catalog = env.service.catalog
    with catalog.write() as connection:
        job_id = connection.execute(
            "INSERT INTO jobs(subject_type, subject_id, input_revision, task_type, "
            "processor_id, state, run_after, created_at, updated_at, dispatch_spec_revision) "
            "VALUES ('asset', ?, ?, ?, ?, 'SUCCEEDED', 1, 1, 1, ?)",
            (
                str(asset["asset_id"]),
                str(asset["sha256"]),
                contract.FACE_TASK_TYPE,
                contract.FACE_PROCESSOR_ID,
                contract.FACE_SPEC_REVISION,
            ),
        ).lastrowid
    store = ArtifactStore(catalog.data_root)
    prepared = [store.prepare(ArtifactRef.from_envelope(artifact_ref(payload)))]
    job = SimpleNamespace(
        job_id=job_id, subject_type="asset", subject_id=str(asset["asset_id"])
    )
    result = SimpleNamespace(input_revision=str(asset["sha256"]))
    spec = SimpleNamespace(processor_id=contract.FACE_PROCESSOR_ID)
    with catalog.write() as connection:
        store.persist_result(
            connection,
            job=job,
            result=result,
            spec=spec,
            prepared=prepared,
            now_ms=1,
        )
        persist_face_document(
            connection,
            job=job,
            result=result,
            prepared=prepared,
            now_ms=1,
        )
    return dict(catalog.read_one("SELECT * FROM faces LIMIT 1")), str(asset["sha256"])


@pytest.mark.parametrize("rotated", [False, True])
def test_draft_frame_crop_is_centred_without_rewriting_faces(api_env, rotated):
    face, sha = publish_draft_face(api_env, rotated=rotated)
    catalog = api_env.service.catalog
    artifact = dict(
        catalog.read_one("SELECT * FROM artifacts WHERE artifact_id = ?", (face["artifact_id"],))
    )
    original_document = ArtifactStore(catalog.data_root).read(artifact)
    embeddings = tuple(tuple(row) for row in catalog.read_all("SELECT * FROM face_embeddings"))
    # Seed a wrong old crop, demonstrating the new namespace doesn't reuse/remove it.
    cache = api_env.service.media_cache()
    with patch("photo_search.media.face_crop.FACE_CROP_PROCESSOR_ID", "face-crop@1"):
        key = crop_key(face_id=face["face_id"], box=parse_bbox(face["bbox_json"]), sha256=sha)
    relative = cache.face_crop_path(key=key).relative_to(cache.face_crops_root)
    old = (
        cache.face_crops_root
        / "face-crop@1"
        / relative.parts[1]
        / relative.parts[2]
        / relative.name
    )
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old-wrong-crop")

    async def scenario(client):
        await api_env.authenticate(client)
        response = await client.get(f"/api/v1/faces/{face['face_id']}/thumbnail?v=person-media2")
        return response

    response = api_env.scenario(scenario)
    assert response.status_code == 200, response.text
    assert response.headers["ETag"].startswith('"face-crop@2:')
    assert response.headers["Cache-Control"] == "private, no-cache, must-revalidate"
    # Reproduce the old denominator on the exact same published preview.
    wrong = render_crop(
        cache.artifact_path(spec=PREVIEW_SPEC, sha256=sha),
        box=parse_bbox(face["bbox_json"]),
        source_size=probe_original_frame(api_env.library / "draft.jpg"),
    )
    with Image.open(io.BytesIO(wrong)) as image:
        r, _g, b = image.getpixel((image.width // 2, image.height // 2))
        assert abs(b - r) < 10  # Grey background, not the detected blue region.
    with Image.open(io.BytesIO(response.content)) as image:
        r, g, b = image.getpixel((image.width // 2, image.height // 2))
        assert b > r + 150 and b > g + 150  # Previously grey: wrong source/header scale.
        assert max(image.size) <= 256
    assert dict(catalog.read_one("SELECT * FROM faces LIMIT 1")) == face
    assert (
        tuple(tuple(row) for row in catalog.read_all("SELECT * FROM face_embeddings")) == embeddings
    )
    assert ArtifactStore(catalog.data_root).read(artifact) == original_document
    assert old.read_bytes() == b"old-wrong-crop"


@pytest.mark.parametrize("broken", ["checksum", "box", "content"])
def test_crop_refuses_unverified_frame_instead_of_guessing(api_env, broken):
    face, _ = publish_draft_face(api_env)
    catalog = api_env.service.catalog
    with catalog.write() as connection:
        if broken == "checksum":
            connection.execute("UPDATE artifacts SET checksum_sha256 = ?", ("a" * 64,))
        elif broken == "box":
            connection.execute(
                "UPDATE faces SET bbox_json = '[1,2,30,40]' WHERE face_id = ?", (face["face_id"],)
            )
        else:
            connection.execute(
                "UPDATE faces SET input_revision = ? WHERE face_id = ?", ("b" * 64, face["face_id"])
            )

    async def scenario(client):
        await api_env.authenticate(client)
        return await client.get(f"/api/v1/faces/{face['face_id']}/thumbnail")

    response = api_env.scenario(scenario)
    assert response.status_code == 409
    assert response.json()["code"] in {"FACE_STATE_CONFLICT", "ARTIFACT_MISSING"}
    assert str(api_env.data_root) not in response.text
    assert not list((api_env.data_root / "cache" / "face-crops").rglob("*.jpg"))
