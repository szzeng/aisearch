"""Provider-independent embedding descriptions and the one persistent ``ProcessorSpec`` builder.

ADR-0003 §4 fixes exactly one persisted spec document (``ProcessorSpec``) and one adapter
semantics (``spec / load / encode_queries / encode_images / health / close``). P4-Embedding-Arch
adds a second *model* without adding a second protocol: a model is described by a lightweight
manifest JSON, every manifest is parsed without importing a framework, and this module turns that
description into the single frozen ``ProcessorSpec`` the index, the profile and the query path
already share.

Two rules decide what is written here:

* **Identity is derived, never guessed.** ``processor_id``/``space_id``/``dimension`` come from
  the manifest facts the adapter really loads; a change of weights, preprocessing, instruction,
  pooling, native or output dimension produces a *different* space, because the digest covers
  those facts. Equal dimensions never mean "the same space".
* **The historical Chinese-CLIP manifest keeps its exact identity.** Its old manifest has no
  ``provider`` block; the frozen compatibility mapping below is the only path that derives the
  historical ``chinese-clip-vit-b-16@<rev>``/``chinese-clip-vit-b-16:<sha16>`` pair, so an
  already-published index is never orphaned by this refactor.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from photo_search.catalog.errors import EMBEDDING_MANIFEST_INVALID, CatalogError
from photo_search.runtime.device import DevicePolicy
from photo_search.scheduler.contracts import CONTRACT_VERSION, ProcessorSpec, canonical_json

#: The two adapter kinds this build ships. A new single-vector image-text model adds one more.
CLIP_ADAPTER = "chinese_clip_onnx"
QWEN_ADAPTER = "qwen3_vl"
KNOWN_ADAPTERS = (CLIP_ADAPTER, QWEN_ADAPTER)

#: The per-adapter ``spec_revision`` the frozen ``ProcessorSpec`` records.
SPEC_REVISIONS = {
    CLIP_ADAPTER: "chinese-clip-vit-b-16-query-v1",
    QWEN_ADAPTER: "qwen3-vl-embedding-2b-query-v1",
}

#: The two preprocessing/pooling version labels of the shipped Chinese-CLIP adapter. They are
#: part of the published spec, so they are constants rather than recomputed strings.
CLIP_IMAGE_PREPROCESS_VERSION = "clip-224-shortest-bicubic-rgb-v1"
NORMALIZED_ORIENTATION = "exif-transpose-v1"
CLIP_NORMALIZED_IMAGE_PREPROCESS_VERSION = "clip-224-shortest-bicubic-exif-rgb-v2"
CLIP_QUERY_PREPROCESS_VERSION = "bert-maxlen52-truncate-right-v1"
CLIP_INSTRUCTION_TEMPLATE_VERSION = "none-v1"
NORMALIZATION_VERSION = "L2-v1"

#: The space digest of the frozen P0-04 Chinese-CLIP configuration (model id, embedding,
#: preprocessing, tokenizer and ONNX records - not the resolved revision). It is the *only*
#: configuration that keeps the historical ``chinese-clip-vit-b-16:<image sha>`` space: any other
#: Chinese-CLIP configuration derives its space from this digest instead, so a changed weight,
#: image size, tokenizer or dimension can never silently continue an already published index.
LEGACY_CLIP_SPACE_HASH = "313ce683ba2bf2f4f7a911c7fceea5d3861fb5e785cbdb1a255fc46d7e15550d"

_DEVICE_POLICY_SPELLING = {
    DevicePolicy.GPU_REQUIRED: "GPU_REQUIRED",
    DevicePolicy.CPU_ALLOWED: "CPU_ALLOWED",
    DevicePolicy.CPU_PREFERRED: "CPU_PREFERRED",
}

#: The whole compatibility surface of the pre-provider manifests: a model id that was already
#: published under a fixed provider id, adapter and data-root-relative model directory. Only a
#: manifest whose *complete* configuration still matches keeps this identity; anything else must
#: publish a new provider block (and therefore a new space).
LEGACY_PROVIDERS: dict[str, dict[str, Any]] = {
    "OFA-Sys/chinese-clip-vit-base-patch16": {
        "provider_id": "chinese-clip-vit-b-16",
        "adapter": CLIP_ADAPTER,
        "display_name": "Chinese-CLIP ViT-B-16",
        "model_dir": ("models", "chinese_clip", "vit-b-16"),
        "device_policy": DevicePolicy.CPU_ALLOWED.value,
    }
}

#: Every key a ``provider`` block must carry. ``display_name`` is optional.
_PROVIDER_REQUIRED = ("provider_id", "adapter", "model_dir", "device_policy")
_PROVIDER_OPTIONAL = ("display_name",)


def _invalid(message: str, **details: Any) -> CatalogError:
    return CatalogError(EMBEDDING_MANIFEST_INVALID, message, details=dict(details))


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Parse one model manifest as JSON, without importing any framework."""
    manifest_path = Path(path)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise _invalid("the model manifest does not exist", manifest=str(manifest_path)) from error
    except (OSError, ValueError) as error:
        raise _invalid(
            "the model manifest is not readable JSON",
            manifest=str(manifest_path),
            reason=type(error).__name__,
        ) from error
    if not isinstance(document, dict):
        raise _invalid("the model manifest must be a JSON object", manifest=str(manifest_path))
    return document


def provider_slug(value: str) -> str:
    """The stable ``provider_id`` spelling: lowercase ascii, digits, ``-`` and ``.``."""
    text = str(value).strip().lower()
    cleaned = "".join(char if char.isalnum() or char in "-." else "-" for char in text)
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    if not collapsed:
        raise _invalid("a provider id must contain at least one letter or digit")
    return collapsed


def _model_dir_parts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = tuple(part for part in value.replace("\\", "/").split("/") if part)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = tuple(str(part) for part in value)
    else:
        raise _invalid("provider.model_dir must be a path string or an array of parts")
    if not parts:
        raise _invalid("provider.model_dir must not be empty")
    for part in parts:
        if part in (".", "..") or ":" in part or "\\" in part or "/" in part:
            raise _invalid(
                "provider.model_dir must stay inside the data root",
                part=str(part),
            )
    return parts


def provider_block(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The normalized ``provider`` description of one manifest.

    A manifest without a ``provider`` block is only accepted when its model id is in the frozen
    legacy table - that is the documented compatibility path for an already published index. Any
    other manifest must describe itself explicitly, because guessing a provider id from a
    nickname or a dimension is exactly what this layer exists to prevent.
    """
    raw = manifest.get("provider")
    if raw is None:
        model = manifest.get("model")
        model_id = str(model.get("id") or "") if isinstance(model, Mapping) else ""
        legacy = LEGACY_PROVIDERS.get(model_id)
        if legacy is None:
            raise _invalid(
                "the manifest declares no provider block and its model id is not a known "
                "published configuration",
                model_id=model_id,
            )
        return dict(legacy)
    if not isinstance(raw, Mapping):
        raise _invalid("provider must be a JSON object")
    for key in _PROVIDER_REQUIRED:
        if key not in raw:
            raise _invalid("provider is missing a required key", key=str(key))
    for key in raw:
        if key not in (*_PROVIDER_REQUIRED, *_PROVIDER_OPTIONAL):
            raise _invalid("provider carries an unknown key", key=str(key))
    adapter = str(raw["adapter"]).strip()
    if adapter not in KNOWN_ADAPTERS:
        raise _invalid("provider.adapter is not implemented by this build", adapter=adapter)
    policy = str(raw["device_policy"]).strip().lower()
    if policy not in {item.value for item in DevicePolicy}:
        raise _invalid("provider.device_policy is not a device policy", device_policy=policy)
    return {
        "provider_id": provider_slug(str(raw["provider_id"])),
        "adapter": adapter,
        "display_name": str(raw.get("display_name") or raw["provider_id"]),
        "model_dir": _model_dir_parts(raw["model_dir"]),
        "device_policy": policy,
    }


def model_dir_for(data_root: str | Path, provider: Mapping[str, Any]) -> Path:
    """``<data_root>/<provider.model_dir>``; the data root is the only place a model may live."""
    parts = _model_dir_parts(provider["model_dir"])
    return Path(data_root).joinpath(*parts)


def effective_device_policy(*policies: str | DevicePolicy | None) -> DevicePolicy:
    """The strictest policy of the manifest and the caller's request.

    A model that may only run a strict CUDA path (``gpu_required``) can never be softened into a
    CPU run by the caller's default policy; the reverse - asking for CPU on a
    ``CPU_ALLOWED`` model - stays an explicit, reported degradation.
    """
    strictness = {
        DevicePolicy.CPU_PREFERRED: 0,
        DevicePolicy.CPU_ALLOWED: 1,
        DevicePolicy.GPU_REQUIRED: 2,
    }
    chosen = DevicePolicy.CPU_PREFERRED
    for policy in policies:
        if policy is None:
            continue
        candidate = DevicePolicy(policy)
        if strictness[candidate] > strictness[chosen]:
            chosen = candidate
    return chosen


@dataclass(frozen=True)
class Identity:
    """The derived identity of one model configuration: everything that defines its space."""

    provider_id: str
    adapter: str
    processor_id: str
    space_id: str
    dimension: int
    metric: str
    config_hash: str
    model_id: str
    model_revision: str
    weights: tuple[dict[str, Any], ...]
    image_preprocess_version: str
    query_preprocess_version: str
    tokenizer_version: str
    instruction_template_version: str
    normalization_version: str
    max_pixels: int
    max_tokens: int
    max_batch_size: int
    runtime_backend: str
    runtime_package: str
    device_policy: str

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        payload["weights"] = [dict(item) for item in self.weights]
        return payload


def _text(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("a required manifest value is missing", key=where)
    return value


def _integer(value: Any, *, where: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid("a manifest integer is out of range", key=where, value=value)
    return int(value)


def image_orientation_policy(manifest: Mapping[str, Any]) -> str | None:
    """Absent means the exact historical adapter behavior; new orientation needs a new provider.

    The registry resolves persisted processors by provider and model revision. Reusing either
    shipped legacy provider with changed orientation would make its existing generation
    unservable, even if the new space hash were different.
    """
    preprocessing = manifest.get("preprocessing", {})
    if "orientation" not in preprocessing:
        return None
    policy = preprocessing["orientation"]
    if policy != NORMALIZED_ORIENTATION:
        raise _invalid("preprocessing.orientation is not supported", orientation=policy)
    provider = provider_block(manifest)
    if provider["provider_id"] in {"chinese-clip-vit-b-16", "qwen3-vl-embedding-2b"}:
        raise _invalid("changed image orientation requires a new provider_id")
    return NORMALIZED_ORIENTATION


def _clip_identity(manifest: Mapping[str, Any], provider: Mapping[str, Any]) -> Identity:
    """The historical Chinese-CLIP derivation, byte for byte the pre-P4 identity."""
    model = manifest["model"]
    embedding = manifest["embedding"]
    tokenizer = manifest["tokenizer"]
    preprocessing = manifest["preprocessing"]
    runtime = manifest["runtime"]
    onnx = manifest["artifacts"]["onnx"]
    revision = str(model["resolved_revision"])
    provider_id = str(provider["provider_id"])
    config_hash = hashlib.sha256(
        canonical_json(
            {
                "model": manifest["model"],
                "embedding": manifest["embedding"],
                "preprocessing": manifest["preprocessing"],
                "tokenizer": manifest["tokenizer"],
                "onnx": manifest["onnx"],
            }
        ).encode("utf-8")
    ).hexdigest()
    # The representation space of a Chinese-CLIP configuration is its *content*: the image
    # weights (the historical digest), plus - for anything that is not the frozen configuration -
    # the model id, preprocessing, tokenizer and ONNX records. The resolved revision is
    # deliberately excluded: recording the same bytes under a newer revision changes the
    # ``processor_id`` (which is how a generation is namespaced) but not the space itself.
    space_hash = hashlib.sha256(
        canonical_json(
            {
                "model": {"id": str(model["id"])},
                "embedding": manifest["embedding"],
                "preprocessing": manifest["preprocessing"],
                "tokenizer": manifest["tokenizer"],
                "onnx": manifest["onnx"],
            }
        ).encode("utf-8")
    ).hexdigest()
    digest = str(onnx["image"]["sha256"])[:16]
    if space_hash != LEGACY_CLIP_SPACE_HASH:
        digest = space_hash[:16]
    return Identity(
        provider_id=provider_id,
        adapter=CLIP_ADAPTER,
        processor_id=f"{provider_id}@{revision[:12]}",
        space_id=f"{provider_id}:{digest}",
        dimension=_integer(embedding["dimension"], where="embedding.dimension"),
        metric=_text(embedding["metric"], where="embedding.metric"),
        config_hash=config_hash,
        model_id=_text(model["id"], where="model.id"),
        model_revision=revision,
        weights=tuple(
            {
                "name": Path(str(onnx[role]["path"])).name,
                "revision": f"{revision}#{str(onnx[role]['sha256'])[:12]}",
                "sha256": _text(onnx[role]["sha256"], where=f"artifacts.onnx.{role}.sha256"),
            }
            for role in ("image", "text")
        ),
        image_preprocess_version=(
            CLIP_NORMALIZED_IMAGE_PREPROCESS_VERSION
            if image_orientation_policy(manifest) else CLIP_IMAGE_PREPROCESS_VERSION
        ),
        query_preprocess_version=CLIP_QUERY_PREPROCESS_VERSION,
        tokenizer_version=f"bert-vocab-{str(tokenizer['vocab_sha256'])[:12]}",
        instruction_template_version=CLIP_INSTRUCTION_TEMPLATE_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        max_pixels=_integer(preprocessing["image_size"], where="preprocessing.image_size") ** 2,
        max_tokens=_integer(tokenizer["max_length"], where="tokenizer.max_length"),
        max_batch_size=_integer(manifest["onnx"]["batch"], where="onnx.batch"),
        runtime_backend=_text(runtime["backend"], where="runtime.backend"),
        runtime_package="onnxruntime-gpu",
        device_policy=str(provider["device_policy"]),
    )


def _qwen_identity(manifest: Mapping[str, Any], provider: Mapping[str, Any]) -> Identity:
    """The Qwen3-VL-Embedding identity: every fingerprint ingredient, explicit and versioned."""
    model = manifest["model"]
    embedding = manifest["embedding"]
    tokenizer = manifest["tokenizer"]
    preprocessing = manifest["preprocessing"]
    runtime = manifest["runtime"]
    instruction = manifest["instruction"]
    revision = _text(model["resolved_revision"], where="model.resolved_revision")
    provider_id = str(provider["provider_id"])
    native = _integer(embedding["native_dimension"], where="embedding.native_dimension")
    dimension = _integer(embedding["dimension"], where="embedding.dimension")
    if dimension > native:
        raise _invalid(
            "the output dimension of an MRL model cannot exceed its native dimension",
            dimension=dimension,
            native_dimension=native,
        )
    weights = manifest.get("weights")
    if not isinstance(weights, Sequence) or isinstance(weights, (str, bytes)) or not weights:
        raise _invalid("a Qwen manifest must pin at least one weight file")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(weights):
        if not isinstance(item, Mapping):
            raise _invalid("weights entries must be JSON objects", index=index)
        name = _text(item.get("name"), where=f"weights[{index}].name")
        digest = _text(item.get("sha256"), where=f"weights[{index}].sha256")
        records.append(
            {"name": name, "revision": f"{revision}#{digest[:12]}", "sha256": digest}
        )
    fingerprint = {
        # The resolved revision is provenance, not content: every pinned file hash is already in
        # the fingerprint, so recording the same bytes under a newer revision changes the
        # ``processor_id`` and not the space.
        "model": {"id": str(model["id"])},
        "embedding": {
            "dimension": dimension,
            "native_dimension": native,
            "metric": str(embedding["metric"]),
            "normalization": str(embedding["normalization"]),
            "mrl": str(embedding.get("mrl") or ""),
        },
        "instruction": {
            "text": str(instruction["text"]),
            "image": str(instruction["image"]),
            "query": str(instruction["query"]),
        },
        "preprocessing": dict(preprocessing),
        "tokenizer": {
            "max_length": int(tokenizer["max_length"]),
            "padding_side": str(tokenizer["padding_side"]),
            "sha256": str(tokenizer.get("sha256") or ""),
        },
        "runtime": {
            "backend": str(runtime["backend"]),
            "precision": str(runtime["precision"]),
            "attention": str(runtime["attention"]),
            "batch": int(runtime["batch"]),
            "pooling": str(runtime.get("pooling") or ""),
        },
        # Every pinned file (weights, config, tokenizer, chat template, preprocessing config)
        # is part of the space: replacing one of them would otherwise change the vectors while
        # the space id stayed the same.
        "files": [{"name": item["name"], "sha256": item["sha256"]} for item in records],
    }
    config_hash = hashlib.sha256(
        canonical_json(fingerprint).encode("utf-8")
    ).hexdigest()
    instruction_digest = hashlib.sha256(
        canonical_json(
            {
                "text": str(instruction["text"]),
                "image": str(instruction["image"]),
                "query": str(instruction["query"]),
            }
        ).encode("utf-8")
    ).hexdigest()
    return Identity(
        provider_id=provider_id,
        adapter=QWEN_ADAPTER,
        processor_id=f"{provider_id}@{revision[:12]}",
        space_id=f"{provider_id}:{config_hash[:16]}",
        dimension=dimension,
        metric=_text(embedding["metric"], where="embedding.metric"),
        config_hash=config_hash,
        model_id=_text(model["id"], where="model.id"),
        model_revision=revision,
        weights=tuple(records),
        image_preprocess_version=(
            "qwen3vl-min{min_pixels}-max{max_pixels}-patch{patch}-merge{merge}-rgb-v1".format(
                min_pixels=int(preprocessing["min_pixels"]),
                max_pixels=int(preprocessing["max_pixels"]),
                patch=int(preprocessing["patch_size"]),
                merge=int(preprocessing["merge_size"]),
            ) + ("+exif-transpose-v1" if image_orientation_policy(manifest) else "")
        ),
        query_preprocess_version="qwen3vl-chat-template-addgen-rightpad-v1",
        tokenizer_version=f"qwen3vl-tokenizer-{str(tokenizer.get('sha256') or '')[:12]}",
        instruction_template_version=f"qwen3vl-instruct-{instruction_digest[:12]}",
        normalization_version=NORMALIZATION_VERSION,
        max_pixels=_integer(preprocessing["max_pixels"], where="preprocessing.max_pixels"),
        max_tokens=_integer(tokenizer["max_length"], where="tokenizer.max_length"),
        max_batch_size=_integer(runtime["batch"], where="runtime.batch"),
        runtime_backend=_text(runtime["backend"], where="runtime.backend"),
        runtime_package=_text(runtime["package"], where="runtime.package"),
        device_policy=str(provider["device_policy"]),
    )


_IDENTITY_BUILDERS = {CLIP_ADAPTER: _clip_identity, QWEN_ADAPTER: _qwen_identity}


def identity_of(manifest: Mapping[str, Any]) -> Identity:
    """Derive the identity of one manifest without importing the adapter's framework."""
    provider = provider_block(manifest)
    return _IDENTITY_BUILDERS[provider["adapter"]](manifest, provider)


def analyze_manifest(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], Identity]:
    """One manifest's provider block plus its derived identity."""
    provider = provider_block(manifest)
    return provider, _IDENTITY_BUILDERS[provider["adapter"]](manifest, provider)


def build_processor_spec(
    manifest: Mapping[str, Any],
    identity: Identity,
    *,
    facts: Mapping[str, Any] | None = None,
    device_policy: str | DevicePolicy | None = None,
) -> ProcessorSpec:
    """The one persisted ``ProcessorSpec`` document of one model configuration.

    ``facts`` are the *live* runtime facts of an actually loaded adapter; when they are absent
    the manifest's own record is used, which is what lets a catalog describe the space without
    loading a model (the query path still re-checks the live worker against this document).
    """
    facts = dict(facts or {})
    policy = effective_device_policy(identity.device_policy, device_policy)
    devices = tuple(
        dict.fromkeys(
            str(item)
            for item in (facts.get("supported_devices") or _manifest_devices(manifest, identity))
        )
    )
    precisions = tuple(
        dict.fromkeys(
            str(item)
            for item in (
                facts.get("supported_precisions")
                or (_manifest_precision(manifest, identity),)
            )
        )
    )
    runtime_backend = str(facts.get("runtime_backend") or identity.runtime_backend)
    runtime_package_id = str(
        facts.get("runtime_package_id") or _manifest_runtime_package(manifest, identity)
    )
    return ProcessorSpec.from_envelope(
        {
            "contract_version": CONTRACT_VERSION,
            "spec_revision": (
                SPEC_REVISIONS[identity.adapter].removesuffix("-v1") + "-exif-v2"
                if image_orientation_policy(manifest) else SPEC_REVISIONS[identity.adapter]
            ),
            "processor_id": identity.processor_id,
            "processor_kind": "embedding_model",
            "task_type": "image_embedding",
            "config_hash": identity.config_hash,
            "input_type": "asset",
            "output_type": "embedding",
            "device_policy": _DEVICE_POLICY_SPELLING[policy],
            "runtime_backend": runtime_backend,
            "runtime_package_id": runtime_package_id,
            "supported_devices": list(devices),
            "supported_precisions": list(precisions),
            "max_batch_size": identity.max_batch_size,
            "cancellation_supported": False,
            "resource_measurements": [],
            "model_id": identity.model_id,
            "model_revision": identity.model_revision,
            "weights": [dict(item) for item in identity.weights],
            "image_preprocess_version": identity.image_preprocess_version,
            "query_preprocess_version": identity.query_preprocess_version,
            "tokenizer_version": identity.tokenizer_version,
            "instruction_template_version": identity.instruction_template_version,
            "normalization_version": identity.normalization_version,
            "dimension": identity.dimension,
            "metric": identity.metric,
            "space_id": identity.space_id,
            "max_pixels": identity.max_pixels,
            "max_tokens": identity.max_tokens,
        }
    )


def _manifest_precision(manifest: Mapping[str, Any], identity: Identity) -> str:
    runtime = manifest.get("runtime")
    if isinstance(runtime, Mapping) and runtime.get("precision"):
        return str(runtime["precision"])
    return "FP32" if identity.adapter == CLIP_ADAPTER else "FP16"


def _manifest_runtime_package(manifest: Mapping[str, Any], identity: Identity) -> str:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        return identity.runtime_package
    versions = runtime.get("versions")
    version = ""
    if isinstance(versions, Mapping):
        version = str(
            versions.get(str(runtime.get("package") or ""))
            or versions.get("onnxruntime")
            or versions.get("torch")
            or ""
        )
    elif runtime.get("version"):
        version = str(runtime["version"])
    # The historical Chinese-CLIP spec records the *runtime package* as ``onnxruntime`` even
    # though the installed distribution is ``onnxruntime-gpu``; a published profile references
    # that string, so it is kept byte for byte.
    default_package = "onnxruntime" if identity.adapter == CLIP_ADAPTER else identity.runtime_package
    package = str(runtime.get("package") or default_package)
    return f"{package}@{version}" if version else package


def _manifest_devices(manifest: Mapping[str, Any], identity: Identity) -> tuple[str, ...]:
    """The devices the manifest itself claims; the live adapter still has to prove them."""
    if identity.adapter == CLIP_ADAPTER:
        return ("gpu", "cpu")
    return ("gpu",)


def provider_id_of_processor(processor_id: str) -> str:
    """The provider half of a ``processor_id`` (``<provider_id>@<revision>``)."""
    text = str(processor_id)
    head, separator, _ = text.partition("@")
    return provider_slug(head) if separator else provider_slug(text)


def required_artifact_names(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The data-root-relative files one manifest needs before its adapter can load.

    This is the "is it installed?" question the registry answers without loading a model: the
    Chinese-CLIP manifest names its two ONNX graphs plus its tokenizer vocabulary, and every
    declared weight of a PyTorch manifest must exist. A missing file is reported as a structured
    configuration fact, never repaired by downloading something at run time.
    """
    provider = provider_block(manifest)
    if provider["adapter"] == CLIP_ADAPTER:
        onnx = manifest["artifacts"]["onnx"]
        return (
            str(onnx["image"]["path"]),
            str(onnx["text"]["path"]),
            str(manifest["tokenizer"]["vocab_file"]),
        )
    return tuple(str(item["name"]) for item in manifest["weights"])


__all__ = [
    "CLIP_ADAPTER",
    "CLIP_IMAGE_PREPROCESS_VERSION",
    "CLIP_NORMALIZED_IMAGE_PREPROCESS_VERSION",
    "CLIP_INSTRUCTION_TEMPLATE_VERSION",
    "CLIP_QUERY_PREPROCESS_VERSION",
    "KNOWN_ADAPTERS",
    "LEGACY_PROVIDERS",
    "NORMALIZATION_VERSION",
    "NORMALIZED_ORIENTATION",
    "QWEN_ADAPTER",
    "SPEC_REVISIONS",
    "Identity",
    "analyze_manifest",
    "build_processor_spec",
    "effective_device_policy",
    "identity_of",
    "image_orientation_policy",
    "model_dir_for",
    "provider_block",
    "provider_id_of_processor",
    "provider_slug",
    "read_manifest",
    "required_artifact_names",
]
