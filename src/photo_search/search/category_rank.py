"""Small, explicitly selected category query directions for one verified image space.

The files describe a ranking direction, never a probability or a new embedding model.
Only fixed local filenames are read; no model framework or evaluation module is imported.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.models.embedding.registry import EmbeddingRegistry

VERSION = "album-qwen512-v1"
LABEL = "类别优先（试用）"
MAX_PACKAGE_BYTES = 262_144
CLASSES = ("cat", "dog", "bird", "car", "bicycle", "person", "chair", "teddy_bear")
ALIASES = dict(zip(("猫", "狗", "鸟", "汽车", "自行车", "人", "椅子", "玩具熊"), CLASSES))
_FIELDS = {"schema", "version", "processor_id", "space_id", "dimension",
           "source_manifest_sha256", "weights_sha256", "export_evidence_sha256", "classes", "aliases"}


def unavailable(reason: str) -> CatalogError:
    return CatalogError("CATEGORY_RANK_UNAVAILABLE", "类别优先暂不可用，请关闭此选项使用普通搜索",
                        details={"reason": reason})


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _safe_read(path: Path, *, root: Path, limit: int) -> bytes:
    # Reject NTFS junctions/reparse points as well as symbolic links at every managed level.
    current = path
    while current != root:
        info = current.lstat()
        if current.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked head package")
        current = current.parent
        if current == current.parent and current != root:
            raise ValueError("outside package root")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("oversized or non-file head package")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = path.stat()
    if len(raw) > limit or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("changed head package")
    return raw


@lru_cache(maxsize=4)
def _parse_package(manifest_raw: bytes, weights_raw: bytes) -> tuple[dict[str, Any], tuple[tuple[float, ...], ...]]:
    manifest, weights = _json(manifest_raw), _json(weights_raw)
    if (not isinstance(manifest, dict) or set(manifest) != _FIELDS
            or manifest["schema"] != "photo-search-category-head-v1"
            or manifest["version"] != VERSION or type(manifest["dimension"]) is not int
            or manifest["dimension"] != 512 or manifest["classes"] != list(CLASSES)
            or manifest["aliases"] != ALIASES):
        raise ValueError("head manifest contract mismatch")
    for key in ("source_manifest_sha256", "weights_sha256", "export_evidence_sha256"):
        value = manifest[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("invalid head checksum")
    if manifest["weights_sha256"] != _sha(weights_raw):
        raise ValueError("head weights checksum mismatch")
    if (not isinstance(weights, dict) or set(weights) != {"schema", "weights"}
            or weights["schema"] != "photo-search-category-weights-v1"
            or not isinstance(weights["weights"], dict) or set(weights["weights"]) != set(CLASSES)):
        raise ValueError("invalid category weights")
    directions = []
    for name in CLASSES:
        values = weights["weights"][name]
        if (not isinstance(values, list) or len(values) != 512
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
            raise ValueError("invalid category direction")
        # hypot avoids overflow for a malicious but finite coefficient.
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm <= 1e-12:
            raise ValueError("empty category direction")
        directions.append(tuple(float(v) / norm for v in values))
    return manifest, tuple(directions)


@dataclass(frozen=True)
class CategoryQuery:
    identity: str
    class_id: str
    vector: tuple[float, ...]

    def summary(self, *, applied: bool) -> dict[str, Any]:
        return {"requested": VERSION if applied else "off", "applied": applied,
                "available": True, "reason": None, "label": LABEL,
                "identity": self.identity, "class_id": self.class_id}


def resolve_category_query(data_root: Path, binding: Any, text: str | None) -> CategoryQuery:
    class_id = ALIASES.get((text or "").strip())
    if class_id is None:
        raise unavailable("UNSUPPORTED_QUERY")
    if binding is None or int(binding.dimension) != 512:
        raise unavailable("INCOMPATIBLE_SPACE")
    try:
        provider = EmbeddingRegistry.discover(data_root).for_processor(str(binding.processor_id))
        if (provider.provider_id != "qwen3-vl-embedding-2b-exif-v2"
                or provider.identity.space_id != binding.space_id
                or provider.identity.dimension != 512):
            raise unavailable("INCOMPATIBLE_SPACE")
        root = Path(data_root).absolute()
        directory = root / "models" / "search_heads" / "qwen512-category-v1"
        manifest_raw = _safe_read(directory / "manifest.json", root=root, limit=MAX_PACKAGE_BYTES)
        weights_raw = _safe_read(directory / "weights.json", root=root,
                                 limit=MAX_PACKAGE_BYTES - len(manifest_raw))
        manifest, directions = _parse_package(manifest_raw, weights_raw)
        if (manifest["processor_id"] != str(binding.processor_id)
                or manifest["space_id"] != str(binding.space_id)
                or manifest["source_manifest_sha256"] != _sha(provider.manifest_path.read_bytes())):
            raise unavailable("INCOMPATIBLE_SPACE")
        return CategoryQuery(_sha(manifest_raw), class_id, directions[CLASSES.index(class_id)])
    except CatalogError as error:
        if error.code == "CATEGORY_RANK_UNAVAILABLE":
            raise
        raise unavailable("INCOMPATIBLE_SPACE") from error
    except FileNotFoundError as error:
        raise unavailable("HEAD_NOT_INSTALLED") from error
    except (OSError, ValueError, TypeError, OverflowError, KeyError) as error:
        raise unavailable("HEAD_INVALID") from error


def availability(data_root: Path, binding: Any, text: str | None) -> dict[str, Any]:
    try:
        return resolve_category_query(data_root, binding, text).summary(applied=False)
    except CatalogError as error:
        return {"requested": "off", "applied": False, "available": False,
                "reason": error.details.get("reason", "INCOMPATIBLE_SPACE"), "label": LABEL,
                "identity": None, "class_id": None}
