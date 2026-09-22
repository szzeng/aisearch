"""In-process search sessions and the opaque continuation cursor (architecture §9.5).

One search answers its first page and caches the ordered candidate list (at most
:data:`MAX_SESSION_IDS` entries, each carrying the rank provenance it was computed with) so
that every later page of the *same* query, profile, generation and catalog revision is a
slice of one frozen ranking. Flipping to another page can therefore never mix two orderings,
two profiles or two catalog versions.

The store is deliberately process-local: a restarted application has no sessions, and a
continuation token from a previous process is answered with the identifiable
``SEARCH_SESSION_EXPIRED`` instead of an empty list or a silently new ranking. Nothing here
reads the database; SQLite authority stays in the service layer.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from photo_search.catalog.errors import INVALID_CURSOR, SEARCH_SESSION_EXPIRED, CatalogError

#: Architecture §9.5: one session caches at most 1000 ordered candidate ids.
MAX_SESSION_IDS = 1000
#: Architecture §9.5: the default lifetime of one search session.
SESSION_TTL_MS = 600_000
#: How many live sessions one process keeps before the least recently used one is dropped.
MAX_SESSIONS = 64
#: The cursor is an opaque, byte-stable token with a version tag so a future change cannot
#: be mistaken for the current one.
CURSOR_PREFIX = "s1"
#: A bound on the token itself: an absurdly long string is a malformed cursor, not work.
MAX_CURSOR_CHARS = 512


def _system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def query_hash(
    *,
    text: str | None,
    source_id: int | None,
    availability: str,
    place_ids: Sequence[int],
    include_descendants: bool,
    place_state: str,
    top_k: int,
    page_size: int,
    person_ids: Sequence[int] = (),
    person_match: str = "any",
    capture_from: str | None = None,
    capture_to_exclusive: str | None = None,
) -> str:
    """A stable digest of the conditions that must stay identical across the pages.

    The binding (profile/generation) is *not* part of this digest on purpose: a profile
    switch is a different failure with its own code (``PROFILE_GENERATION_MISMATCH``), and
    folding it in here would report it as a malformed cursor instead.

    ADR-0009 §6.2 adds the person condition, and it is the caller's job to pass the
    *canonical*, de-duplicated, ascending ids: two different person conditions that resolve
    to the same person must share one digest, while two different conditions must never.

    ADR-0011 §6.5 adds the two capture bounds to the frozen payload. They arrive already
    normalized by ``taken_time.contract.parse_capture_range`` (the fixed-width wall-clock
    spelling), so two spellings of the same interval share one digest and two different
    intervals never do. There is deliberately no time revision in this payload: the capture
    condition binds the one existing ``catalog_filter_revision``.
    """
    payload = {
        "text": None if text is None else str(text),
        "source_id": None if source_id is None else int(source_id),
        "availability": str(availability),
        "place_ids": [int(value) for value in place_ids],
        "include_descendants": bool(include_descendants),
        "place_state": str(place_state),
        "top_k": int(top_k),
        "page_size": int(page_size),
        "person_ids": [int(value) for value in person_ids],
        "person_match": str(person_match),
        "capture_from": None if capture_from is None else str(capture_from),
        "capture_to_exclusive": None
        if capture_to_exclusive is None
        else str(capture_to_exclusive),
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionEntry:
    """One ordered candidate and the provenance that was computed for it once."""

    asset_id: int
    score: float | None
    matched_by: dict[str, int]
    hit_reason: str
    matched_text: tuple[str, ...] = ()
    semantic_ready: bool = False


@dataclass
class SearchSession:
    """The frozen result of one search, plus the LRU bookkeeping of the store."""

    session_id: str
    query_hash: str
    profile_id: str | None
    image_generation_id: str | None
    space_id: str | None
    dimension: int | None
    catalog_filter_revision: int
    suggestion_revision: int
    created_at_ms: int
    last_used_ms: int
    entries: tuple[SessionEntry, ...]
    candidates_truncated: bool
    fusion_config: dict[str, Any]
    query: dict[str, Any] | None
    filters: dict[str, Any]
    channels: dict[str, Any]
    pending_semantic: dict[str, Any] | None
    search_projection: dict[str, Any]
    state: str
    #: P2-06B: the rerank block computed for this frozen ranking. A continuation page echo
    #: the very same block, so flipping pages can never look like a second rerank happened.
    rerank_state: str = "NOT_REQUESTED"
    reranked: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def binding(self) -> tuple[str | None, str | None, str | None, int | None]:
        """``(profile_id, generation_id, space_id, dimension)`` as one comparable key."""
        return (
            self.profile_id,
            self.image_generation_id,
            self.space_id,
            self.dimension,
        )


class SearchSessionStore:
    """A bounded, thread-safe, process-local collection of search sessions."""

    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        ttl_ms: int = SESSION_TTL_MS,
        max_sessions: int = MAX_SESSIONS,
        max_ids: int = MAX_SESSION_IDS,
    ) -> None:
        self._now_ms = now_ms or _system_now_ms
        self.ttl_ms = int(ttl_ms)
        self.max_sessions = max(1, int(max_sessions))
        self.max_ids = max(1, int(max_ids))
        self._sessions: dict[str, SearchSession] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ lifecycle
    def create(
        self,
        *,
        query_hash: str,
        entries: Sequence[SessionEntry],
        profile_id: str | None,
        image_generation_id: str | None,
        space_id: str | None,
        dimension: int | None,
        catalog_filter_revision: int,
        suggestion_revision: int,
        fusion_config: dict[str, Any],
        query: dict[str, Any] | None,
        filters: dict[str, Any],
        channels: dict[str, Any],
        pending_semantic: dict[str, Any] | None,
        search_projection: dict[str, Any],
        state: str,
        candidates_truncated: bool = False,
        rerank_state: str = "NOT_REQUESTED",
        reranked: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> SearchSession:
        """Build *and* register one search (the one-call form of the two steps below)."""
        session = self.build_session(
            query_hash=query_hash,
            entries=entries,
            profile_id=profile_id,
            image_generation_id=image_generation_id,
            space_id=space_id,
            dimension=dimension,
            catalog_filter_revision=catalog_filter_revision,
            suggestion_revision=suggestion_revision,
            fusion_config=fusion_config,
            query=query,
            filters=filters,
            channels=channels,
            pending_semantic=pending_semantic,
            search_projection=search_projection,
            state=state,
            candidates_truncated=candidates_truncated,
            rerank_state=rerank_state,
            reranked=reranked,
            extra=extra,
        )
        return self.register(session)

    def build_session(
        self,
        *,
        query_hash: str,
        entries: Sequence[SessionEntry],
        profile_id: str | None,
        image_generation_id: str | None,
        space_id: str | None,
        dimension: int | None,
        catalog_filter_revision: int,
        suggestion_revision: int,
        fusion_config: dict[str, Any],
        query: dict[str, Any] | None,
        filters: dict[str, Any],
        channels: dict[str, Any],
        pending_semantic: dict[str, Any] | None,
        search_projection: dict[str, Any],
        state: str,
        candidates_truncated: bool = False,
        rerank_state: str = "NOT_REQUESTED",
        reranked: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> SearchSession:
        """Construct one session **without** publishing it into the store.

        Only the first :attr:`max_ids` ordered entries are kept. A caller that still has to
        prove the page is answerable (the service re-reads the authority revisions after the
        first page has been rendered) prepares the session here and calls :meth:`register`
        only once it is sure - so a discarded attempt can never leave a session behind, and
        can never evict a live one either.
        """
        ordered = tuple(entries[: self.max_ids])
        truncated = bool(candidates_truncated) or len(entries) > self.max_ids
        now = int(self._now_ms())
        return SearchSession(
            session_id=secrets.token_urlsafe(18),
            query_hash=str(query_hash),
            profile_id=profile_id,
            image_generation_id=image_generation_id,
            space_id=space_id,
            dimension=dimension,
            catalog_filter_revision=int(catalog_filter_revision),
            suggestion_revision=int(suggestion_revision),
            created_at_ms=now,
            last_used_ms=now,
            entries=ordered,
            candidates_truncated=truncated,
            fusion_config=dict(fusion_config),
            query=None if query is None else dict(query),
            filters=dict(filters),
            channels=dict(channels),
            pending_semantic=None if pending_semantic is None else dict(pending_semantic),
            search_projection=dict(search_projection),
            rerank_state=str(rerank_state),
            reranked=None if reranked is None else dict(reranked),
            state=str(state),
            extra=dict(extra or {}),
        )

    def register(self, session: SearchSession) -> SearchSession:
        """Publish one prepared session and keep the store inside its bounds."""
        now = int(self._now_ms())
        session.last_used_ms = now
        with self._lock:
            self._evict_expired_locked(now)
            self._sessions[session.session_id] = session
            while len(self._sessions) > self.max_sessions:
                self._evict_oldest_locked()
        return session

    def get(self, session_id: str | None) -> SearchSession | None:
        """The live session, or ``None`` when it is unknown or past its TTL."""
        if not session_id:
            return None
        now = int(self._now_ms())
        with self._lock:
            session = self._sessions.get(str(session_id))
            if session is None:
                return None
            if now - session.created_at_ms > self.ttl_ms:
                self._sessions.pop(session.session_id, None)
                return None
            session.last_used_ms = now
            return session

    def require(self, session_id: str | None) -> SearchSession:
        """The live session, or the identifiable ``SEARCH_SESSION_EXPIRED`` refusal."""
        session = self.get(session_id)
        if session is None:
            raise CatalogError(
                SEARCH_SESSION_EXPIRED,
                "the search session is unknown or expired; run the query again",
                details={"reason": "SESSION_NOT_LIVE", "ttl_ms": self.ttl_ms},
            )
        return session

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def session_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._sessions)

    # --------------------------------------------------------------------- cursor
    def encode_cursor(self, session_id: str, offset: int) -> str:
        """One opaque continuation token: ``base64url("s1:<session>:<offset>")``."""
        raw = f"{CURSOR_PREFIX}:{str(session_id)}:{int(offset)}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(token: str | None) -> tuple[str, int]:
        """``(session_id, offset)`` or a structured ``INVALID_CURSOR`` (never a 500).

        A tampered, truncated, re-padded or foreign token is refused here; whether the
        named session is still alive and whether it belongs to *this* query is decided by
        the caller, so each distinct failure keeps its own stable code.
        """
        text = "" if token is None else str(token)
        if not text or len(text) > MAX_CURSOR_CHARS:
            raise _invalid_cursor("the cursor is empty or too long")
        padding = "=" * (-len(text) % 4)
        try:
            raw = base64.b64decode(text + padding, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as error:
            raise _invalid_cursor("the cursor is not a valid opaque token") from error
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _invalid_cursor("the cursor is not a valid opaque token") from error
        parts = decoded.split(":")
        if len(parts) != 3 or parts[0] != CURSOR_PREFIX or not parts[1]:
            raise _invalid_cursor("the cursor does not belong to this protocol")
        if not parts[2].isdigit():
            raise _invalid_cursor("the cursor carries no usable offset")
        return parts[1], int(parts[2])

    def cursor_for_offset(self, session: SearchSession, offset: int) -> str | None:
        """The next-page token, or ``None`` when the cached list has no more entries."""
        if int(offset) >= len(session.entries):
            return None
        return self.encode_cursor(session.session_id, int(offset))

    # ------------------------------------------------------------------ internals
    def _evict_expired_locked(self, now: int) -> None:
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.created_at_ms > self.ttl_ms
        ]
        for session_id in stale:
            self._sessions.pop(session_id, None)

    def _evict_oldest_locked(self) -> None:
        oldest = min(
            self._sessions.values(), key=lambda item: (item.last_used_ms, item.session_id)
        )
        self._sessions.pop(oldest.session_id, None)


def _invalid_cursor(message: str) -> CatalogError:
    return CatalogError(INVALID_CURSOR, message, details={"field": "cursor"})


__all__ = [
    "CURSOR_PREFIX",
    "MAX_SESSIONS",
    "MAX_SESSION_IDS",
    "SESSION_TTL_MS",
    "SearchSession",
    "SearchSessionStore",
    "SessionEntry",
    "query_hash",
]
