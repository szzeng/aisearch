"""Opt-in category reranking of a bounded, already-authoritative candidate set.

RAM scores are machine evidence, not probabilities or a membership filter. Unprocessed
photos keep their positions; only processed photos are compared with one another.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from photo_search.catalog.errors import CatalogError
from photo_search.models.embedding.registry import EmbeddingRegistry
from photo_search.search.category_rank import unavailable
from photo_search.search.sessions import MAX_SESSION_IDS, SessionEntry
from photo_search.tagging.model import TagClass, read_model

VERSION = "ram-plus-v1"
LABEL = "物体与场景辅助（试用）"


def normalized(text: str | None) -> str:
    return " ".join((text or "").strip().casefold().split())


@lru_cache(maxsize=4)
def _names(classes: tuple[TagClass, ...]) -> dict[str, int]:
    choices: dict[str, set[int]] = {}
    for item in classes:
        for value in (item.english, item.chinese):
            choices.setdefault(normalized(value), set()).add(item.index)
    return {name: next(iter(ids)) for name, ids in choices.items() if len(ids) == 1}


@dataclass(frozen=True)
class TagQuery:
    identity: str
    class_id: str
    processor_id: str
    revision: int
    name: str
    completed_count: int

    def summary(self, *, applied: bool) -> dict[str, Any]:
        return {"requested": VERSION if applied else "off", "mode": VERSION,
                "applied": applied, "available": True, "reason": None,
                "label": LABEL, "identity": self.identity, "class_id": self.class_id,
                "processor_id": self.processor_id, "revision": self.revision,
                "category_name": self.name, "completed_count": self.completed_count,
                "candidate_limit": MAX_SESSION_IDS, "coverage_policy": "keep_unprocessed_positions"}


def resolve_tag_query(catalog: Any, data_root: Any, binding: Any, text: str | None) -> TagQuery:
    from photo_search.tagging import store
    from photo_search.tagging.policy import POLICY_ID

    # The current paired quality evidence covers this baseline. Other profiles continue
    # to work normally, but are not advertised as an evaluated tag-assisted combination.
    if (binding is None or binding.dimension != 512
            or not str(binding.space_id).startswith("qwen3-vl-embedding-2b-exif-v2:")):
        raise unavailable("INCOMPATIBLE_SPACE")
    try:
        provider = EmbeddingRegistry.discover(data_root).for_processor(str(binding.processor_id))
        if (provider.provider_id != "qwen3-vl-embedding-2b-exif-v2"
                or provider.identity.space_id != binding.space_id
                or provider.identity.dimension != 512):
            raise unavailable("INCOMPATIBLE_SPACE")
        model = read_model(data_root)
        tag_id = _names(model.classes).get(normalized(text))
        if tag_id is None:
            raise unavailable("UNSUPPORTED_QUERY")
        processor_id = store.processor_id_for(model.model_id, POLICY_ID)
        current = store.get_model(catalog.connection, processor_id)
        if current is None or int(current["completed_count"]) == 0:
            raise unavailable("TAGS_NOT_BUILT")
        revision = int(current["revision"])
        return TagQuery(f"{processor_id}:{revision}", str(tag_id), processor_id, revision,
                        model.classes[tag_id].chinese.strip(), int(current["completed_count"]))
    except CatalogError as error:
        if error.code == "CATEGORY_RANK_UNAVAILABLE":
            raise
        raise unavailable(error.code) from error


def availability(catalog: Any, data_root: Any, binding: Any, text: str | None) -> dict[str, Any]:
    try:
        return resolve_tag_query(catalog, data_root, binding, text).summary(applied=False)
    except CatalogError as error:
        return {"requested": "off", "mode": VERSION, "applied": False, "available": False,
                "reason": error.details.get("reason"), "label": LABEL,
                "identity": None, "class_id": None}


def require_current(catalog: Any, query: TagQuery) -> None:
    from photo_search.tagging import store

    store.require_revision(catalog.connection, (query.processor_id, query.revision))


def revision_binding(summary: Any) -> tuple[str, int] | None:
    """Recover a server-owned session binding for an atomic collection mutation."""
    if not isinstance(summary, dict) or summary.get("requested") != VERSION:
        return None
    processor_id, revision = summary.get("processor_id"), summary.get("revision")
    if (not isinstance(processor_id, str) or not processor_id
            or type(revision) is not int or revision < 0
            or summary.get("identity") != f"{processor_id}:{revision}"):
        raise CatalogError("INVALID_CURSOR", "物体识别搜索身份不完整，请重新搜索",
                           details={"reason": "CATEGORY_RANK_CHANGED"})
    return processor_id, revision


def rerank(catalog: Any, query: TagQuery, entries: Sequence[SessionEntry]) -> tuple[tuple[SessionEntry, ...], dict[str, int]]:
    from photo_search.tagging import store
    from photo_search.tagging.policy import FUSION_WEIGHT

    if len(entries) > MAX_SESSION_IDS:
        raise ValueError("tag reranking requires at most 5000 existing candidates")
    scores, covered = store.scores_for_assets(
        catalog.connection, processor_id=query.processor_id,
        asset_ids=[entry.asset_id for entry in entries], tag_id=int(query.class_id),
    )
    require_current(catalog, query)
    positions = [i for i, entry in enumerate(entries) if entry.asset_id in covered]
    metadata = {"covered_candidates": len(positions), "unprocessed_candidates": len(entries) - len(positions)}
    if len(positions) < 2:
        return tuple(entries), metadata
    if len({scores.get(entries[i].asset_id, 0.0) for i in positions}) == 1:
        return tuple(entries), metadata
    # Average ranks prevent FP16 ties and missing (zero) scores from manufacturing order.
    ordered = sorted(positions, key=lambda i: -scores.get(entries[i].asset_id, 0.0))
    tag_ranks: dict[int, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        score = scores.get(entries[ordered[start]].asset_id, 0.0)
        while end < len(ordered) and scores.get(entries[ordered[end]].asset_id, 0.0) == score:
            end += 1
        rank = (start + 1 + end) / 2.0
        for i in ordered[start:end]:
            tag_ranks[i] = rank
        start = end
    base_ranks: dict[int, float] = {}
    start = 0
    while start < len(positions):
        end = start + 1
        while end < len(positions) and entries[positions[end]].score == entries[positions[start]].score:
            end += 1
        for i in positions[start:end]:
            base_ranks[i] = (start + 1 + end) / 2.0
        start = end
    fused = {i: 1.0 / (60.0 + base_ranks[i]) + FUSION_WEIGHT / (60.0 + tag_ranks[i])
             for i in positions}
    ranked = sorted(positions, key=lambda i: (-fused[i], i))
    result = list(entries)
    for position, source in zip(positions, ranked, strict=True):
        # Preserve original hit reason/channel evidence; this is a new ordering of the
        # same matches. The response category block explains the additional machine signal.
        result[position] = replace(entries[source], score=fused[source])
    return tuple(result), metadata
