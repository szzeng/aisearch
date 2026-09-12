"""Shared fixture for the local-API tests.

Every path lives under pytest's ``tmp_path`` (``%TEMP%``): no fixture in this file ever
touches a real photo library, and the deterministic clocks keep lease/stability behaviour
reproducible without sleeping.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.ingestion.service import CatalogService

#: Fixed wall-clock origin so an old ``mtime`` is always well outside the quiet window.
WALL_BASE_MS = 1_700_000_000_000
QUIET_AGE_MS = 3_600_000
DEFAULT_SESSION_TOKEN = "unit-test-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})

T = TypeVar("T")


class Clock:
    """Deterministic monotone clock used for leases and job timestamps."""

    def __init__(self, start: int = WALL_BASE_MS) -> None:
        self.ms = int(start)

    def __call__(self) -> int:
        return self.ms

    def advance(self, delta_ms: int) -> None:
        self.ms += int(delta_ms)


class WallClock(Clock):
    """The wall clock the scanner compares file ``mtime`` against."""


class Usage:
    """Stand-in for ``shutil.disk_usage`` so a batch never fails on this machine."""

    def __init__(self, free: int = 10**12) -> None:
        self.total = 10**12
        self.used = 0
        self.free = int(free)

    def __call__(self, path: Any = None) -> Usage:
        return self


def stamp(path: Path, *, age_ms: int = QUIET_AGE_MS) -> Path:
    """Give a fixture file an ``mtime`` that is already outside the stability window."""
    moment = (WALL_BASE_MS - int(age_ms)) * 1_000_000
    os.utime(path, ns=(moment, moment))
    return path


def write_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (160, 120),
    color: tuple[int, int, int] = (200, 30, 30),
    orientation: int | None = None,
    noise: int = 0,
) -> Path:
    """Write a small real JPEG fixture; distinct noise keeps the content hash distinct."""
    image = Image.new("RGB", size, color)
    if noise:
        pixels = image.load()
        for x in range(size[0]):
            for y in range(size[1]):
                pixels[x, y] = (
                    (color[0] + noise * x) % 256,
                    (color[1] + noise * y) % 256,
                    (color[2] + noise * (x + y)) % 256,
                )
    path.parent.mkdir(parents=True, exist_ok=True)
    if orientation is None:
        image.save(path, "JPEG", quality=92)
    else:
        exif = Image.Exif()
        exif[0x0112] = int(orientation)
        image.save(path, "JPEG", quality=92, exif=exif)
    return stamp(path)


def write_text(path: Path, text: str = "not an image") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return stamp(path)


@dataclass
class ApiEnv:
    """A registered temp library plus the application built over its data root."""

    tmp_path: Path
    data_root: Path
    library: Path
    clock: Clock
    wall: WallClock
    service: CatalogService
    settings: ApiSettings
    app: Any
    session_token: str = DEFAULT_SESSION_TOKEN
    disk_usage: Usage = None  # type: ignore[assignment]
    session_id: str | None = None
    csrf_token: str | None = None

    @classmethod
    def build(
        cls,
        tmp_path: Path,
        *,
        session_token: str = DEFAULT_SESSION_TOKEN,
        runner_executor: Callable[[Any, int], Any] | None = None,
    ) -> ApiEnv:
        tmp_path = Path(tmp_path)
        data_root = tmp_path / "PhotoSearchData"
        library = tmp_path / "library"
        library.mkdir(parents=True, exist_ok=True)
        clock = Clock()
        wall = WallClock()
        usage = Usage()

        def factory() -> CatalogService:
            return CatalogService.open(
                data_root=data_root,
                clock=clock,
                lease_seconds=60,
                disk_usage=usage,
                wall_clock_ms=wall,
                stability_sleep=wall.advance,
            )

        service = factory()
        settings = ApiSettings(
            data_root=data_root,
            bind_host="127.0.0.1",
            bind_port=80,
            session_token=session_token,
            allowed_hosts=ALLOWED_HOSTS,
        )
        app = create_app(
            settings=settings, service_factory=factory, runner_executor=runner_executor
        )
        return cls(
            tmp_path=tmp_path,
            data_root=data_root,
            library=library,
            clock=clock,
            wall=wall,
            service=service,
            settings=settings,
            app=app,
            session_token=session_token,
            disk_usage=usage,
        )

    @property
    def context(self) -> Any:
        return self.app.state.api

    def client(self) -> AsyncClient:
        """An in-process client; ``Host`` is a loopback spelling, as a browser would use."""
        return AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://127.0.0.1",
            follow_redirects=False,
        )

    def scenario(self, func: Callable[[AsyncClient], Awaitable[T]]) -> T:
        """Run one async scenario against a fresh client."""

        async def main() -> T:
            async with self.client() as client:
                return await func(client)

        return asyncio.run(main())

    def close(self) -> None:
        self.context.close()
        self.service.close()

    # ------------------------------------------------------------------- fixtures
    def add_photo(self, relative_path: str, **options: Any) -> Path:
        return write_jpeg(self.library / relative_path, **options)

    def add_text_file(self, relative_path: str, text: str = "not an image") -> Path:
        return write_text(self.library / relative_path, text)

    def register_source(self, name: str = "library", path: Path | None = None) -> dict[str, Any]:
        return self.service.register_source(display_name=name, root_path=path or self.library)

    def scan(self, source_id: int) -> dict[str, Any]:
        """Run one complete scan synchronously (the CLI path, not the API runner)."""
        return self.service.run_scan(int(source_id))

    def first_asset_id(self) -> int:
        row = self.service.catalog.read_one("SELECT MIN(asset_id) AS value FROM assets")
        return int(row["value"])

    def asset_id_for(self, sha256: str) -> int:
        row = self.service.catalog.read_one(
            "SELECT asset_id FROM assets WHERE sha256 = ?", (sha256,)
        )
        return int(row["asset_id"])

    @staticmethod
    def post_headers(
        csrf: str, *, origin: str | None = "http://127.0.0.1", host: str | None = None
    ) -> dict[str, str]:
        headers = {"X-CSRF-Token": csrf}
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        return headers

    async def authenticate(self, client: AsyncClient, *, token: str | None = None) -> str:
        """Exchange the one-time credential once and reuse that session afterwards.

        The credential is single-use by design, so later scenarios of the same test reuse
        the session cookie instead of asking the API for a second one.
        """
        if token is None and self.session_id and self.csrf_token:
            client.cookies.set("ps_session", self.session_id, domain="127.0.0.1")
            client.cookies.set("ps_csrf", self.csrf_token, domain="127.0.0.1")
            return self.csrf_token
        response = await client.get(
            "/api/v1/session", params={"token": self.session_token if token is None else token}
        )
        assert response.status_code == 200, response.text
        csrf = str(response.json()["csrf_token"])
        self.session_id = client.cookies.get("ps_session")
        self.csrf_token = csrf
        return csrf


@pytest.fixture()
def api_env(tmp_path: Path) -> ApiEnv:
    env = ApiEnv.build(tmp_path)
    try:
        yield env
    finally:
        env.close()


@pytest.fixture()
def interrupted_env(tmp_path: Path) -> ApiEnv:
    """An environment whose first background scan attempt is interrupted after one file.

    This models a killed API process: the job and its ``scan_run`` stay ``RUNNING`` with an
    unconsumed lease, so the next request must take over the same logical scan instead of
    creating a second one (ADR-0002 §5.3).
    """
    state = {"interrupt_next": True}

    def executor(service: Any, source_id: int) -> Any:
        if state["interrupt_next"]:
            state["interrupt_next"] = False
            return service.run_scan(int(source_id), interrupt_after=1)
        return service.run_scan(int(source_id))

    env = ApiEnv.build(tmp_path, runner_executor=executor)
    try:
        yield env
    finally:
        env.close()
