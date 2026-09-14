"""P2-06A fixtures: a scanned temp library, one READY generation, a real app and sessions.

Everything lives under pytest's ``tmp_path`` (``%TEMP%``). The photos go through the
production scanner, the vectors through the production artifact/outbox/index path and the
profiles through the migration's own DDL; only the query *encoding* is a deterministic
stand-in for the ADR-0003 adapter, and it is injected through the documented
``encoder_factory`` seam (this build ships no embedding adapter inside the API process).

There is no product API that *creates* a search profile (the model-upgrade entry points
belong to a later task), so ``SearchEnv.create_profile`` inserts the row the way a
diagnostic tool would: the schema is the authority, not a fabricated shortcut.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from httpx import ASGITransport, AsyncClient
from p2_search_fixtures import (
    DIMENSION,
    SPACE,
    FakeEncoder,
    build_library,
    configured_scheduler,
    embedding_spec,
    index_vector,
)

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.artifacts.store import generation_id_for
from photo_search.search.profile import DEFAULT_FUSION_CONFIG
from photo_search.search.sessions import SearchSessionStore

PHOTOS = ("杭州西湖 照片.jpg", "beach sunset.jpg", "IMG_0001.JPG")
SESSION_TOKEN = "p2-06a-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
#: The query texts the deterministic fake adapter can encode.
QUERY_VECTORS: dict[str, list[float]] = {
    "sunset": [1.0, 0.0, 0.0],
    "jpg": [0.5, 0.5, 0.0],
    "西湖": [0.0, 1.0, 0.0],
    "hangzhou": [0.0, 1.0, 0.0],
}
T = TypeVar("T")


@dataclass
class SearchEnv:
    """A temp library with one READY generation plus the application that serves it."""

    library: Any
    spec: Any
    scheduler: Any
    generation: str
    #: The served ADR-0003 §7 adapter. ``FakeEncoder`` has only the text half; P2-07 tests
    #: inject ``FakeImageEncoder`` (which has both) to exercise the upload path.
    encoder: Any
    app: Any
    settings: ApiSettings
    #: P2-06B: the deterministic reranker adapter this application was built with (if any).
    reranker: Any = None
    session_token: str = SESSION_TOKEN
    session_id: str | None = None
    csrf_token: str | None = None

    @property
    def catalog(self) -> Any:
        return self.library.catalog

    @property
    def assets(self) -> dict[str, int]:
        return self.library.assets

    @property
    def context(self) -> Any:
        return self.app.state.api

    @property
    def data_root(self) -> Path:
        return Path(self.library.data_root)

    def explicit_binding(self) -> dict[str, Any]:
        """The P2-05 explicit triple, as one request body fragment."""
        return {
            "space_id": SPACE,
            "image_generation_id": self.generation,
            "dimension": DIMENSION,
        }

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        )

    def scenario(self, func: Callable[[AsyncClient], Any]) -> T:
        async def main() -> T:
            async with self.client() as client:
                return await func(client)

        return asyncio.run(main())

    async def authenticate(self, client: AsyncClient, *, token: str | None = None) -> str:
        if token is None and self.session_id and self.csrf_token:
            client.cookies.set("ps_session", self.session_id, domain="127.0.0.1")
            client.cookies.set("ps_csrf", self.csrf_token, domain="127.0.0.1")
            return self.csrf_token
        response = await client.get(
            "/api/v1/session", params={"token": self.session_token if token is None else token}
        )
        assert response.status_code == 200, response.text
        self.session_id = client.cookies.get("ps_session")
        self.csrf_token = str(response.json()["csrf_token"])
        return self.csrf_token

    @staticmethod
    def post_headers(csrf: str, *, origin: str = "http://127.0.0.1") -> dict[str, str]:
        return {"X-CSRF-Token": csrf, "Origin": origin}

    # ------------------------------------------------------------------- fixtures
    def create_generation(
        self,
        *,
        generation_id: str,
        space_id: str = SPACE,
        processor_id: str | None = None,
        state: str = "READY",
        dimension: int = DIMENSION,
    ) -> str:
        """Insert one index generation row (the shape P2-03 writes) with a chosen state."""
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO index_generations(generation_id, space_id, processor_id, "
                "dimension, state, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 1000, 1000)",
                (
                    str(generation_id),
                    str(space_id),
                    str(processor_id or self.spec.processor_id),
                    int(dimension),
                    str(state),
                ),
            )
        return str(generation_id)

    def create_profile(
        self,
        profile_id: str = "profile-1",
        *,
        generation_id: str | None = None,
        space_id: str = SPACE,
        processor_id: str | None = None,
        runtime_package_id: str | None = None,
        fts_tokenizer_version: str | None = None,
        fts_generation_id: str | None = None,
        fusion_config: dict[str, Any] | None = None,
        reranker_processor_id: str | None = None,
        created_at: int = 1_700_000_000_000,
    ) -> str:
        """Insert one immutable profile row exactly as the 0007 DDL stores it."""
        with self.catalog.write() as connection:
            connection.execute(
                "INSERT INTO search_profiles(profile_id, embedding_processor_id, space_id, "
                "image_generation_id, query_preprocess_version, query_parser_version, "
                "fts_tokenizer_version, fts_generation_id, reranker_processor_id, "
                "fusion_config_json, runtime_package_id, created_at) "
                "VALUES (?, ?, ?, ?, 'query-v1', 'parser-v1', ?, ?, ?, ?, ?, ?)",
                (
                    str(profile_id),
                    str(processor_id or self.spec.processor_id),
                    str(space_id),
                    str(generation_id or self.generation),
                    str(fts_tokenizer_version or "1"),
                    str(fts_generation_id or "fts-v1"),
                    reranker_processor_id,
                    json.dumps(fusion_config or DEFAULT_FUSION_CONFIG, sort_keys=True),
                    str(runtime_package_id or self.spec.runtime_package_id),
                    int(created_at),
                ),
            )
        return str(profile_id)

    def pointer(self) -> str | None:
        row = self.catalog.read_one(
            "SELECT profile_id FROM active_search_profile WHERE state_id = 1"
        )
        return None if row is None or row["profile_id"] is None else str(row["profile_id"])

    def generation_state(self) -> dict[str, bool]:
        return {
            str(row["generation_id"]): bool(row["is_active"])
            for row in self.catalog.read_all(
                "SELECT generation_id, is_active FROM index_generations ORDER BY generation_id"
            )
        }


def build_search_env(
    tmp_path: Path, *, reranker: Any = None, image_encoder: Any = None
) -> SearchEnv:
    """Write three photos, scan them, vectorise them and build the application.

    ``reranker`` is injected through the documented ``reranker_factory`` seam exactly like
    the query encoder: it is a deterministic stand-in, never a real model.

    ``image_encoder`` (P2-07) replaces the served adapter with one that also has the image
    half of ADR-0003 §7; without it this application behaves like the packaged build, which
    has no image tower at all.
    """
    library = build_library(tmp_path, PHOTOS)
    spec = embedding_spec()
    scheduler = configured_scheduler(library.catalog, [spec])
    assets = dict(library.assets)
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["beach sunset.jpg"],
        vector=[1.0, 0.0, 0.0],
        artifact_id="art-sunset",
    )
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["杭州西湖 照片.jpg"],
        vector=[0.0, 1.0, 0.0],
        artifact_id="art-lake",
    )
    index_vector(
        library.catalog,
        scheduler,
        spec,
        asset_id=assets["IMG_0001.JPG"],
        vector=[0.1, 0.1, 0.9],
        artifact_id="art-image",
    )
    encoder: Any = (
        FakeEncoder(spec, vectors=QUERY_VECTORS) if image_encoder is None else image_encoder
    )
    settings = ApiSettings(
        data_root=library.data_root,
        bind_host="127.0.0.1",
        bind_port=80,
        session_token=SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(
        settings=settings,
        encoder_factory=lambda: encoder,
        reranker_factory=(None if reranker is None else (lambda: reranker)),
    )
    return SearchEnv(
        library=library,
        spec=spec,
        scheduler=scheduler,
        generation=generation_id_for(SPACE, spec.processor_id),
        encoder=encoder,
        app=app,
        settings=settings,
        reranker=reranker,
    )


def fresh_app(env: SearchEnv) -> Any:
    """A second application over the same data root: a restarted process, no sessions."""
    return create_app(
        settings=env.settings,
        encoder_factory=lambda: env.encoder,
        reranker_factory=(None if env.reranker is None else (lambda: env.reranker)),
    )


def record_reranker_evidence(env: SearchEnv, spec: Any, *, now_ms: int = 1_000) -> None:
    """Qualify one reranker processor on this machine (the P2-02 evidence row).

    The healthy capability snapshot for the runtime package is already recorded by
    ``configured_scheduler``; this adds the per-processor/device/precision evidence the
    ADR-0003 §5 gate needs before a profile naming that reranker may be activated.
    """
    env.scheduler.record_qualification(
        processor_id=str(spec.processor_id),
        spec_revision=str(spec.spec_revision),
        device="cpu",
        precision=str(spec.supported_precisions[0]),
        provider_name="CPUExecutionProvider",
        runtime_package_id=str(spec.runtime_package_id),
        now_ms=int(now_ms),
    )


def controllable_sessions(env: SearchEnv) -> dict[str, int]:
    """Swap in a store whose clock this test drives, and return the clock."""
    clock = {"now_ms": 1_000_000}
    env.context.search_sessions = SearchSessionStore(now_ms=lambda: clock["now_ms"])
    return clock
