"""Bounded background pre-generation of the covers used on place-album first screens."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from photo_search.api.errors import ApiError
from photo_search.api.files import resolve_present_location
from photo_search.api.media_generation import generate_one_in_worker
from photo_search.media.contract import THUMBNAIL
from photo_search.places import albums as place_albums

logger = logging.getLogger("photo_search.api.place_cover_warmer")

PLACE_ALBUM_KINDS = ("city", "province", "district", "country")
COVERS_PER_KIND = 4
CLOSE_TIMEOUT_SECONDS = 5.0


class PlaceCoverWarmer:
    """Coalesce startup/scan triggers onto one worker that owns its catalog service."""

    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._condition = threading.Condition()
        self._pending = False
        self._running = False
        self._closed = False
        self._thread = threading.Thread(
            target=self._loop, name="photo-search-place-cover-warmer", daemon=True
        )
        self._thread.start()

    def request(self) -> bool:
        """Queue one bounded warm pass; repeated triggers collapse into at most one rerun."""
        with self._condition:
            if self._closed:
                return False
            already_pending = self._pending
            self._pending = True
            self._condition.notify_all()
            return not already_pending

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait for a synthetic test or controlled shutdown to observe queued work."""
        end = time.monotonic() + float(timeout)
        with self._condition:
            while self._pending or self._running:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(remaining, 0.1))
            return True

    def close(self, *, timeout: float = CLOSE_TIMEOUT_SECONDS) -> None:
        with self._condition:
            self._closed = True
            self._pending = False
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=float(timeout))
            if self._thread.is_alive():
                logger.warning("place-cover warmer is still active after %.1fs", float(timeout))

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                self._pending = False
                self._running = True
            try:
                self._warm_once()
            except Exception:  # noqa: BLE001 - a warm pass must never affect app availability
                logger.exception("pre-generating place-album covers failed")
            finally:
                with self._condition:
                    self._running = False
                    self._condition.notify_all()

    def _warm_once(self) -> None:
        service = self._service_factory()
        try:
            seen: set[int] = set()
            targets: list[tuple[str, int]] = []
            for kind in PLACE_ALBUM_KINDS:
                if self._is_closed():
                    return
                try:
                    result = place_albums.albums(service.catalog, service.data_root, kind=kind)
                    covers = result.get("albums", [])[:COVERS_PER_KIND]
                except Exception:  # noqa: BLE001 - one category cannot suppress other covers
                    logger.exception("reading %s album covers for pre-generation failed", kind)
                    continue
                for album in covers:
                    if self._is_closed():
                        return
                    asset_id = int(album["cover_asset_id"])
                    if asset_id in seen:
                        continue
                    seen.add(asset_id)
                    try:
                        resolve_present_location(service.catalog, asset_id)
                        targets.append((kind, asset_id))
                    except ApiError as error:
                        logger.info(
                            "place cover pre-generation skipped asset_id=%s code=%s",
                            asset_id,
                            error.code,
                        )
                    except Exception:  # noqa: BLE001 - isolate malformed/unavailable covers
                        logger.exception(
                            "place cover pre-generation failed asset_id=%s", asset_id
                        )
            if targets and not self._is_closed():
                with ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="photo-search-place-cover"
                ) as executor:
                    futures = {
                        executor.submit(generate_one_in_worker, service, asset_id, THUMBNAIL): (
                            kind,
                            asset_id,
                        )
                        for kind, asset_id in targets
                        if not self._is_closed()
                    }
                    for future in as_completed(futures):
                        kind, asset_id = futures[future]
                        try:
                            future.result()
                        except ApiError as error:
                            logger.info(
                                "place cover pre-generation skipped kind=%s asset_id=%s code=%s",
                                kind,
                                asset_id,
                                error.code,
                            )
                        except Exception:  # noqa: BLE001 - isolate malformed/unavailable covers
                            logger.exception(
                                "place cover pre-generation failed kind=%s asset_id=%s",
                                kind,
                                asset_id,
                            )
            logger.info("place cover warm pass checked %s unique covers", len(seen))
        finally:
            service.close()

    def _is_closed(self) -> bool:
        with self._condition:
            return self._closed
