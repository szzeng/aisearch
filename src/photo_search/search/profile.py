"""Immutable ``search_profiles`` reads, the readiness gate and the atomic activation.

ADR-0003 §8 and architecture §11.3.5 fix the rule this module enforces: a search profile is
an immutable bundle (encoder, space, image generation, query/parse/tokenizer generation,
reranker, fusion config, runtime package), one single-row pointer selects the served
profile, and activation verifies the whole bundle **inside one SQLite transaction** before
the pointer and ``index_generations.is_active`` move together. There is no API that swaps
"the model" and "the vector table" separately, so no reader can observe an encoder bound to
the wrong index.

Two spellings of the frozen ``$defs/SearchProfile`` document need a mapping the companion
JSON Schema cannot express, and neither is allowed to change that schema:

* ``fusion_config`` is stored as JSON text in ``search_profiles.fusion_config_json``;
* ``fts_tokenizer_version`` is a *string* in the contract while
  ``search_documents.tokenizer_version`` is an integer, so this build maps ``"1"`` to 1 and
  refuses any profile whose FTS generation this build cannot serve.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from photo_search.catalog.errors import (
    PROFILE_GENERATION_MISMATCH,
    RUNTIME_CAPABILITY_UNAVAILABLE,
    SCHEMA_INCONSISTENT,
    CatalogError,
)
from photo_search.scheduler.capability import latest_capability, qualification_evidence
from photo_search.scheduler.contracts import CONTRACT_VERSION
from photo_search.search.fusion import DEFAULT_CHANNEL_WEIGHTS, RRF_K
from photo_search.search.retrieval import VectorBinding
from photo_search.search.tokens import FTS_GENERATION_ID, TOKENIZER_VERSION

#: The one fusion configuration this build actually applies. A profile that declares
#: anything else is refused at activation instead of being echoed as if it had been used:
#: the RRF formula, its constant and the equal channel weights are frozen by P2-05.
DEFAULT_FUSION_CONFIG: dict[str, Any] = {
    "revision": "rrf-v1",
    "method": "RRF",
    "parameters": {"k": RRF_K, "weights": dict(DEFAULT_CHANNEL_WEIGHTS)},
}

#: The FTS generation this build's P2-04 projection was written with.
SUPPORTED_FTS_TOKENIZER_VERSION = str(TOKENIZER_VERSION)
SUPPORTED_FTS_GENERATION_ID = FTS_GENERATION_ID


def _refuse(code: str, message: str, **details: Any) -> CatalogError:
    return CatalogError(code, message, details={key: value for key, value in details.items()})


def _refuse_profile(message: str, reason: str, **details: Any) -> CatalogError:
    return _refuse(PROFILE_GENERATION_MISMATCH, message, reason=reason, **details)


@dataclass(frozen=True)
class SearchProfile:
    """One immutable profile, field by field the frozen ``$defs/SearchProfile`` document."""

    profile_id: str
    embedding_processor_id: str
    space_id: str
    image_generation_id: str
    query_preprocess_version: str
    query_parser_version: str
    fts_tokenizer_version: str
    fts_generation_id: str
    reranker_processor_id: str | None
    fusion_config: dict[str, Any]
    runtime_package_id: str
    created_at: int

    @classmethod
    def from_row(cls, row: sqlite3.Row | Mapping[str, Any]) -> SearchProfile:
        return cls(
            profile_id=str(row["profile_id"]),
            embedding_processor_id=str(row["embedding_processor_id"]),
            space_id=str(row["space_id"]),
            image_generation_id=str(row["image_generation_id"]),
            query_preprocess_version=str(row["query_preprocess_version"]),
            query_parser_version=str(row["query_parser_version"]),
            fts_tokenizer_version=str(row["fts_tokenizer_version"]),
            fts_generation_id=str(row["fts_generation_id"]),
            reranker_processor_id=(
                None
                if row["reranker_processor_id"] is None
                else str(row["reranker_processor_id"])
            ),
            fusion_config=json.loads(str(row["fusion_config_json"])),
            runtime_package_id=str(row["runtime_package_id"]),
            created_at=int(row["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """The frozen document shape: ``fusion_config`` (never the column name)."""
        return {
            "contract_version": CONTRACT_VERSION,
            "profile_id": str(self.profile_id),
            "embedding_processor_id": str(self.embedding_processor_id),
            "space_id": str(self.space_id),
            "image_generation_id": str(self.image_generation_id),
            "query_preprocess_version": str(self.query_preprocess_version),
            "query_parser_version": str(self.query_parser_version),
            "fts_tokenizer_version": str(self.fts_tokenizer_version),
            "fts_generation_id": str(self.fts_generation_id),
            "reranker_processor_id": self.reranker_processor_id,
            "fusion_config": dict(self.fusion_config),
            "runtime_package_id": str(self.runtime_package_id),
            "created_at": int(self.created_at),
        }


def load_profile(catalog: Any, profile_id: str | None) -> SearchProfile | None:
    """One profile by id, or ``None``. This never invents a default profile."""
    if profile_id is None:
        return None
    row = catalog.read_one(
        "SELECT * FROM search_profiles WHERE profile_id = ?", (str(profile_id),)
    )
    return None if row is None else SearchProfile.from_row(row)


def active_profile_id(connection: sqlite3.Connection) -> str | None:
    """The single pointer value; ``None`` means "no profile is activated"."""
    row = connection.execute(
        "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
    ).fetchone()
    if row is None:
        raise CatalogError(
            SCHEMA_INCONSISTENT,
            "the catalog has no active_search_profile row; the schema is not usable",
        )
    return None if row["profile_id"] is None else str(row["profile_id"])


def active_profile(catalog: Any) -> SearchProfile | None:
    """The activated profile, or ``None`` when none is activated."""
    return load_profile(catalog, active_profile_id(catalog.connection))


def profile_projection(catalog: Any) -> dict[str, Any]:
    """``GET /api/v1/search-profile``: the read-only projection of the single pointer."""
    row = catalog.read_one(
        "SELECT profile_id, updated_at FROM active_search_profile WHERE state_id = 1"
    )
    if row is None:
        raise CatalogError(
            SCHEMA_INCONSISTENT,
            "the catalog has no active_search_profile row; the schema is not usable",
        )
    profile = None if row["profile_id"] is None else load_profile(catalog, str(row["profile_id"]))
    return {
        "contract_version": CONTRACT_VERSION,
        "profile_id": None if profile is None else profile.profile_id,
        "profile": None if profile is None else profile.to_dict(),
        "updated_at": int(row["updated_at"]),
    }


def _require_supported_fusion(profile: SearchProfile) -> None:
    """This build applies exactly the frozen RRF configuration, or it serves nothing."""
    config = profile.fusion_config
    parameters = config.get("parameters")
    weights = parameters.get("weights") if isinstance(parameters, Mapping) else None
    expected_weights = {name: float(value) for name, value in DEFAULT_CHANNEL_WEIGHTS.items()}
    declared_weights = (
        {str(name): float(value) for name, value in weights.items()}
        if isinstance(weights, Mapping)
        else None
    )
    method_ok = str(config.get("method") or "") == str(DEFAULT_FUSION_CONFIG["method"])
    constant_ok = isinstance(parameters, Mapping) and int(parameters.get("k", 0)) == int(RRF_K)
    if not (method_ok and constant_ok and declared_weights == expected_weights):
        raise _refuse_profile(
            "this build serves only the frozen RRF fusion configuration",
            "FUSION_CONFIG_UNSUPPORTED",
            profile_id=profile.profile_id,
            declared_method=str(config.get("method") or ""),
        )


def _require_supported_fts(profile: SearchProfile) -> None:
    """Map the contract's string tokenizer version onto this build's integer generation."""
    if (
        str(profile.fts_tokenizer_version) != SUPPORTED_FTS_TOKENIZER_VERSION
        or str(profile.fts_generation_id) != SUPPORTED_FTS_GENERATION_ID
    ):
        raise _refuse_profile(
            "this build cannot serve the FTS generation this profile references",
            "FTS_GENERATION_UNSUPPORTED",
            profile_id=profile.profile_id,
            declared_tokenizer_version=str(profile.fts_tokenizer_version),
            declared_fts_generation_id=str(profile.fts_generation_id),
            supported_tokenizer_version=SUPPORTED_FTS_TOKENIZER_VERSION,
            supported_fts_generation_id=SUPPORTED_FTS_GENERATION_ID,
        )


def resolve_profile_binding(
    catalog: Any, profile: SearchProfile, *, connection: sqlite3.Connection | None = None
) -> VectorBinding:
    """Bind one profile to its image generation, or refuse the half-switched bundle.

    The generation must exist, hold the profile's own ``space_id`` and
    ``embedding_processor_id``, and be ``READY``. The dimension comes from the generation
    row, never from the profile (the frozen profile document has no dimension).
    """
    target = catalog.connection if connection is None else connection
    row = target.execute(
        "SELECT * FROM index_generations WHERE generation_id = ?",
        (str(profile.image_generation_id),),
    ).fetchone()
    if row is None:
        raise _refuse_profile(
            "the profile references an index generation that does not exist",
            "GENERATION_NOT_FOUND",
            profile_id=profile.profile_id,
            image_generation_id=profile.image_generation_id,
        )
    if str(row["space_id"]) != str(profile.space_id):
        raise _refuse_profile(
            "the profile's space_id does not match its index generation",
            "GENERATION_SPACE_MISMATCH",
            profile_id=profile.profile_id,
            profile_space_id=profile.space_id,
            generation_space_id=str(row["space_id"]),
        )
    if str(row["processor_id"]) != str(profile.embedding_processor_id):
        raise _refuse_profile(
            "the profile's embedding processor does not own its index generation",
            "GENERATION_PROCESSOR_MISMATCH",
            profile_id=profile.profile_id,
            profile_processor_id=profile.embedding_processor_id,
            generation_processor_id=str(row["processor_id"]),
        )
    state = str(row["state"])
    if state != "READY":
        raise _refuse_profile(
            "the profile's index generation is not READY",
            "GENERATION_NOT_READY",
            profile_id=profile.profile_id,
            generation_state=state,
        )
    return VectorBinding(
        generation_id=str(row["generation_id"]),
        space_id=str(row["space_id"]),
        dimension=int(row["dimension"]),
        processor_id=str(row["processor_id"]),
        state=state,
        is_active=bool(row["is_active"]),
    )


def check_profile_servable(
    catalog: Any, profile: SearchProfile, *, connection: sqlite3.Connection | None = None
) -> VectorBinding:
    """Everything a *serving* path must re-check before using one profile."""
    _require_supported_fts(profile)
    _require_supported_fusion(profile)
    return resolve_profile_binding(catalog, profile, connection=connection)


def validate_profile_readiness(
    connection: sqlite3.Connection, profile: SearchProfile, *, now_ms: int
) -> dict[str, Any]:
    """The P2-02 runtime facts an activation must see before it may publish a pointer.

    The embedding processor needs (a) a recorded capability snapshot for the profile's
    runtime package that is currently healthy and (b) qualification evidence for that
    processor whose provider/precision is available and healthy in that very snapshot. A
    listed provider alone is never enough (ADR-0003 §5), and no device is chosen here.

    P2-06B adds the same requirement, additively, for a profile that names a
    ``reranker_processor_id``: the optional reranker may only be published once this process
    can prove it healthy and qualified. A profile that names no reranker is unaffected, and
    the device-qualification matrix itself is not redefined - the reranker is checked with
    exactly the same rule as the embedding processor.
    """
    capability = latest_capability(connection, runtime_package_id=str(profile.runtime_package_id))
    if capability is None:
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "no runtime capability snapshot is recorded for this profile's runtime package",
            details={
                "reason": "CAPABILITY_MISSING",
                "runtime_package_id": profile.runtime_package_id,
            },
        )
    if str(capability.health_status) != "healthy":
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "the runtime capability snapshot for this profile is not healthy",
            details={
                "reason": "RUNTIME_UNHEALTHY",
                "runtime_package_id": profile.runtime_package_id,
                "health_status": str(capability.health_status),
            },
        )
    evidence = [
        row
        for row in qualification_evidence(
            connection, processor_id=str(profile.embedding_processor_id)
        )
        if str(row.runtime_package_id) == str(profile.runtime_package_id)
    ]
    healthy = {
        (provider.name, provider.device, precision)
        for provider in capability.providers
        if provider.available and provider.health == "healthy"
        for precision in provider.precisions
    }
    for row in evidence:
        if (str(row.provider_name), str(row.device), str(row.precision)) in healthy:
            readiness = {
                "capability_revision": str(capability.capability_revision),
                "device": str(row.device),
                "provider_name": str(row.provider_name),
                "precision": str(row.precision),
                "evidence_id": str(row.evidence_id),
                "checked_at": int(now_ms),
            }
            reranker = reranker_readiness(connection, profile)
            if reranker is not None:
                readiness["reranker"] = reranker
            return readiness
    raise CatalogError(
        RUNTIME_CAPABILITY_UNAVAILABLE,
        "no recorded qualification evidence covers this profile's embedding processor and "
        "runtime package on a healthy provider",
        details={
            "reason": "QUALIFICATION_MISSING",
            "processor_id": profile.embedding_processor_id,
            "runtime_package_id": profile.runtime_package_id,
            "capability_revision": str(capability.capability_revision),
        },
    )


def reranker_readiness(
    connection: sqlite3.Connection, profile: SearchProfile
) -> dict[str, Any] | None:
    """The reranker half of the readiness gate, or ``None`` when the profile names none.

    ``None`` is not a refusal: a profile without a reranker is a complete configuration.
    When a reranker *is* named, it needs exactly the same two facts the embedding processor
    needs - a healthy capability snapshot for the profile's runtime package, and
    qualification evidence for that processor whose provider/device/precision is available
    and healthy in that snapshot - and a failure is the same
    ``RUNTIME_CAPABILITY_UNAVAILABLE`` refusal with a reranker-specific ``reason``. The same
    function is used by the request-time rerank path, so a profile cannot be activated with
    a qualified reranker and then silently rerank with something else.
    """
    processor_id = profile.reranker_processor_id
    if processor_id is None or str(processor_id) == "":
        return None
    capability = latest_capability(connection, runtime_package_id=str(profile.runtime_package_id))
    if capability is None:
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "no runtime capability snapshot is recorded for this profile's runtime package",
            details={
                "reason": "CAPABILITY_MISSING",
                "runtime_package_id": profile.runtime_package_id,
                "processor_id": str(processor_id),
                "role": "reranker",
            },
        )
    if str(capability.health_status) != "healthy":
        raise CatalogError(
            RUNTIME_CAPABILITY_UNAVAILABLE,
            "the runtime capability snapshot for this profile is not healthy",
            details={
                "reason": "RUNTIME_UNHEALTHY",
                "runtime_package_id": profile.runtime_package_id,
                "health_status": str(capability.health_status),
                "processor_id": str(processor_id),
                "role": "reranker",
            },
        )
    healthy = {
        (provider.name, provider.device, precision)
        for provider in capability.providers
        if provider.available and provider.health == "healthy"
        for precision in provider.precisions
    }
    for row in qualification_evidence(connection, processor_id=str(processor_id)):
        if str(row.runtime_package_id) != str(profile.runtime_package_id):
            continue
        if (str(row.provider_name), str(row.device), str(row.precision)) in healthy:
            return {
                "capability_revision": str(capability.capability_revision),
                "device": str(row.device),
                "provider_name": str(row.provider_name),
                "precision": str(row.precision),
                "evidence_id": str(row.evidence_id),
                "processor_id": str(processor_id),
            }
    raise CatalogError(
        RUNTIME_CAPABILITY_UNAVAILABLE,
        "no recorded qualification evidence covers this profile's reranker on a healthy "
        "provider of its runtime package",
        details={
            "reason": "RERANKER_QUALIFICATION_MISSING",
            "processor_id": str(processor_id),
            "runtime_package_id": profile.runtime_package_id,
            "capability_revision": str(capability.capability_revision),
            "role": "reranker",
        },
    )


def activate_profile(
    catalog: Any,
    *,
    profile_id: str,
    now_ms: int | None = None,
    activation_hook: Callable[[sqlite3.Connection], None] | None = None,
    precondition: Callable[[sqlite3.Connection], None] | None = None,
) -> tuple[SearchProfile, bool, dict[str, Any]]:
    """Publish one profile as the served one, atomically and idempotently.

    ``activation_hook`` is the documented fault-injection point: it runs after the readiness
    checks and the two pointer updates but before ``COMMIT``, so a test can prove that an
    interruption leaves neither the active pointer nor ``is_active`` half-moved.

    ``precondition`` is the P4-Embedding-Arch extension for the *controlled* selection: it runs
    inside the very same ``BEGIN IMMEDIATE`` transaction, after the profile/bundle/readiness
    checks and **before** any pointer moves. It is the place where the authoritative coverage,
    outbox and artifact-record facts are re-read, so a check can never be performed outside the
    transaction that publishes the pointer (ADR-0003 §8). It must not perform model inference or
    any other long-running work: only the corresponding rows are re-verified here.
    """
    profile = load_profile(catalog, profile_id)
    if profile is None:
        raise _refuse_profile(
            "the requested search profile does not exist",
            "PROFILE_NOT_FOUND",
            profile_id=str(profile_id),
        )
    moment = int(catalog.now_ms() if now_ms is None else now_ms)
    with catalog.write() as connection:
        resolve_profile_binding(catalog, profile, connection=connection)
        _require_supported_fts(profile)
        _require_supported_fusion(profile)
        readiness = validate_profile_readiness(connection, profile, now_ms=moment)
        if precondition is not None:
            precondition(connection)
        previous = active_profile_id(connection)
        row = connection.execute(
            "SELECT is_active FROM index_generations WHERE generation_id = ?",
            (str(profile.image_generation_id),),
        ).fetchone()
        changed = previous != profile.profile_id
        if changed or not bool(row["is_active"]):
            # Two statements inside one transaction: clearing first can never trip the
            # ``uq_index_generations_active_space`` unique index mid-statement.
            connection.execute(
                "UPDATE index_generations SET is_active = 0, updated_at = ? "
                "WHERE space_id = ? AND is_active = 1",
                (moment, str(profile.space_id)),
            )
            connection.execute(
                "UPDATE index_generations SET is_active = 1, updated_at = ? "
                "WHERE generation_id = ?",
                (moment, str(profile.image_generation_id)),
            )
            connection.execute(
                "UPDATE active_search_profile SET profile_id = ?, updated_at = ? "
                "WHERE state_id = 1",
                (str(profile.profile_id), moment),
            )
        if activation_hook is not None:
            activation_hook(connection)
    return profile, changed, readiness


__all__ = [
    "DEFAULT_FUSION_CONFIG",
    "SUPPORTED_FTS_GENERATION_ID",
    "SUPPORTED_FTS_TOKENIZER_VERSION",
    "SearchProfile",
    "activate_profile",
    "active_profile",
    "active_profile_id",
    "check_profile_servable",
    "load_profile",
    "profile_projection",
    "reranker_readiness",
    "resolve_profile_binding",
    "validate_profile_readiness",
]
