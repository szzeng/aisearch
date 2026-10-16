"""Original upload bytes, frozen legacy pixels and separately versioned upright spaces.

No weights are loaded. Pixel comparisons use decoded fixture pixels plus independent EXIF
transforms, and CLIP's actual resize/normalization path; HEIF covers both EXIF-only and irot.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from p4_heic_fixtures import quadrants, write_exif_heic
from PIL import Image

from photo_search.catalog.errors import EMBEDDING_MANIFEST_INVALID, CatalogError
from photo_search.media.heif import heif_available
from photo_search.models.embedding import base
from photo_search.models.embedding.chinese_clip_onnx import ChineseClipOnnxAdapter
from photo_search.models.embedding.qwen3_vl import Qwen3VLEmbeddingAdapter
from photo_search.models.embedding.registry import registry_for
from photo_search.search.upload import stage_upload

MANIFESTS = Path(__file__).resolve().parents[2] / "tools" / "prepare_models" / "manifests"
TRANSFORMS = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
FAMILIES = ("chinese-clip-vit-b-16", "qwen3-vl-embedding-2b")


def adapter(tmp_path: Path, family: str, *, normalized: bool):
    provider = family + ("-exif-v2" if normalized else "")
    cls = ChineseClipOnnxAdapter if family == FAMILIES[0] else Qwen3VLEmbeddingAdapter
    return cls(tmp_path / "unused-model-directory", MANIFESTS / f"{provider}.json")


def pixels(encoder, path: Path) -> np.ndarray:
    if isinstance(encoder, ChineseClipOnnxAdapter):
        return encoder._preprocess(path)
    with encoder._read_local_image(path) as image:
        return np.asarray(image).copy()


def canonical_file(tmp_path: Path, image: Image.Image, name: str) -> Path:
    path = tmp_path / name
    # Explicit empty EXIF prevents a second rotation on the already upright PNG.
    image.save(path, "PNG", exif=b"")
    return path


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("orientation", range(1, 9))
def test_jpeg_orientation_is_normalized_only_in_its_versioned_space(
    tmp_path: Path, family: str, orientation: int
) -> None:
    path = tmp_path / "source.jpg"
    exif = Image.Exif()
    exif[0x0112] = orientation
    quadrants().save(path, "JPEG", quality=94, exif=exif)
    with Image.open(path) as source:
        stored = source.convert("RGB")
    upright = stored.transpose(TRANSFORMS[orientation]) if orientation != 1 else stored
    upright_path = canonical_file(tmp_path, upright, "upright.png")
    stored_path = canonical_file(tmp_path, stored, "stored.png")
    old, new = (adapter(tmp_path, family, normalized=flag) for flag in (False, True))

    expected_legacy = stored_path if family == FAMILIES[0] else upright_path
    np.testing.assert_array_equal(pixels(old, path), pixels(old, expected_legacy))
    np.testing.assert_array_equal(pixels(new, path), pixels(new, upright_path))
    with stage_upload(path.read_bytes()) as staged:
        assert staged.path.read_bytes() == path.read_bytes()
        assert staged.image.size == upright.size
        # This is the same path contract as library indexing, in either selected space.
        np.testing.assert_array_equal(pixels(old, staged.path), pixels(old, path))
        np.testing.assert_array_equal(pixels(new, staged.path), pixels(new, path))


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("orientation", [6, 8])
@pytest.mark.parametrize("container_orientation", [False, True])
def test_heif_exif_only_and_container_rotation_are_applied_once(
    tmp_path: Path, family: str, orientation: int, container_orientation: bool
) -> None:
    if not heif_available():
        pytest.skip("pinned HEIF runtime unavailable")
    path = write_exif_heic(
        tmp_path / "source.heic", orientation=orientation,
        container_orientation=container_orientation,
    )
    with Image.open(path) as source:
        stored = source.convert("RGB")
    upright = stored if container_orientation else stored.transpose(TRANSFORMS[orientation])
    upright_path = canonical_file(tmp_path, upright, "upright.png")
    stored_path = canonical_file(tmp_path, stored, "stored.png")
    old, new = (adapter(tmp_path, family, normalized=flag) for flag in (False, True))
    # Both historical adapters saw plugin-decoded pixels with its stripped EXIF tag.
    np.testing.assert_array_equal(pixels(old, path), pixels(old, stored_path))
    np.testing.assert_array_equal(pixels(new, path), pixels(new, upright_path))
    with stage_upload(path.read_bytes()) as staged:
        np.testing.assert_array_equal(pixels(old, staged.path), pixels(old, path))
        np.testing.assert_array_equal(pixels(new, staged.path), pixels(new, path))


@pytest.mark.parametrize("family", FAMILIES)
def test_versioned_provider_uses_same_weights_but_distinct_complete_identity(
    tmp_path: Path, family: str
) -> None:
    old_doc = base.read_manifest(MANIFESTS / f"{family}.json")
    new_doc = base.read_manifest(MANIFESTS / f"{family}-exif-v2.json")
    old, new = (base.identity_of(doc) for doc in (old_doc, new_doc))
    assert old.processor_id != new.processor_id
    assert old.space_id != new.space_id
    assert old.config_hash != new.config_hash
    assert old.image_preprocess_version != new.image_preprocess_version
    assert old.weights == new.weights
    assert old.dimension == new.dimension
    expected_preprocessing = copy.deepcopy(old_doc["preprocessing"])
    expected_preprocessing["orientation"] = base.NORMALIZED_ORIENTATION
    assert new_doc["preprocessing"] == expected_preprocessing
    for flag, identity in ((False, old), (True, new)):
        reported = adapter(tmp_path, family, normalized=flag).spec()
        assert reported.processor_id == identity.processor_id
        assert reported.space_id == identity.space_id
    old_spec = base.build_processor_spec(old_doc, old)
    new_spec = base.build_processor_spec(new_doc, new)
    assert old_spec.spec_revision == base.SPEC_REVISIONS[old.adapter]
    assert new_spec.spec_revision.endswith("-exif-v2")
    registry = registry_for(tmp_path)
    assert registry.get(family).model_dir == registry.get(family + "-exif-v2").model_dir
    assert registry.for_processor(new.processor_id).identity == new


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("policy", [None, "", "rotate-v9", 1])
def test_unknown_orientation_policies_fail_closed(family: str, policy) -> None:
    document = base.read_manifest(MANIFESTS / f"{family}-exif-v2.json")
    document["preprocessing"]["orientation"] = policy
    with pytest.raises(CatalogError) as caught:
        base.identity_of(document)
    assert caught.value.code == EMBEDDING_MANIFEST_INVALID


@pytest.mark.parametrize("family", FAMILIES)
def test_a_corrected_manifest_cannot_replace_a_legacy_processor(family: str) -> None:
    document = base.read_manifest(MANIFESTS / f"{family}.json")
    document["preprocessing"]["orientation"] = base.NORMALIZED_ORIENTATION
    with pytest.raises(CatalogError, match="new provider_id"):
        base.identity_of(document)


def test_nonlegacy_clip_configuration_reports_the_shared_derived_space(tmp_path: Path) -> None:
    document = base.read_manifest(MANIFESTS / "chinese-clip-vit-b-16-exif-v2.json")
    document["preprocessing"]["image_size"] = 336
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    encoder = ChineseClipOnnxAdapter(tmp_path, path)
    assert encoder.spec().space_id == base.identity_of(document).space_id


def test_large_upload_validation_does_not_feed_a_draft_decode_to_the_encoder(tmp_path: Path) -> None:
    path = tmp_path / "large.jpg"
    quadrants(6000, 4000).save(path, "JPEG", quality=80)
    with stage_upload(path.read_bytes()) as staged:
        assert staged.image.size != (6000, 4000)  # the validation-only JPEG DCT shortcut
        assert staged.path.read_bytes() == path.read_bytes()
        with Image.open(staged.path) as original:
            assert original.size == (6000, 4000)
