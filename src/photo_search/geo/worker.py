"""Bounded, restartable classification of stored GPS facts; no photo-file access."""

from __future__ import annotations

import logging
import threading

from photo_search.catalog.db import Catalog
from photo_search.geo.boundaries import resolve_batch

logger = logging.getLogger(__name__)


class BoundaryWorker:
    def __init__(self, *, data_root, db_path):
        self.stop = threading.Event()
        self.data_root, self.db_path = data_root, db_path
        self.thread = threading.Thread(target=self._run, name="place-boundaries", daemon=True)
        self.thread.start()

    def _run(self):
        catalog = None
        cache = {}
        try:
            catalog = Catalog.open(self.db_path, data_root=self.data_root)
            while not self.stop.is_set():
                try:
                    paused = catalog.read_one("SELECT paused FROM scheduler_state WHERE state_id=1")
                    handled = 0 if paused and paused[0] else resolve_batch(catalog, cache=cache)
                except Exception as exc:
                    # Exact coordinates and imported geometry are never logged.
                    logger.warning("Offline place classification deferred: %s", type(exc).__name__)
                    handled = 0
                if self.stop.wait(0.05 if handled else 2):
                    break
        finally:
            if catalog is not None:
                catalog.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
