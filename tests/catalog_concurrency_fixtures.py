"""A second real Catalog connection whose writes keep SQLite thread ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

from photo_search.catalog.db import Catalog


@contextmanager
def second_catalog_writer(
    owner: Catalog,
) -> Iterator[tuple[Catalog, Callable[[Callable[[], Any]], Any]]]:
    """Commit an injected race on its owner thread before the query continues.

    The query may run on the event loop or a Catalog worker. Both submit the whole
    mutation to this independent writer; SQLite's normal thread check stays on.
    The same thread opens, executes against and closes the second connection.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="fixture-catalog-writer") as pool:
        second = pool.submit(
            Catalog.open, db_path=owner.db_path, data_root=owner.data_root,
            clock=owner.clock, lease_seconds=owner.lease_seconds,
        ).result()

        def run_commit(operation: Callable[[], Any]) -> Any:
            return pool.submit(operation).result()

        try:
            yield second, run_commit
        finally:
            pool.submit(second.close).result()
