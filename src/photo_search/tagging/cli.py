"""Explicit bounded CLI building of optional object labels, under the data-root lease."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from photo_search.catalog.db import Catalog
from photo_search.catalog.errors import CatalogError
from photo_search.config import catalog_db_path, resolve_data_root
from photo_search.runtime.data_root_lease import DataRootLeaseBusy, acquire_data_root_lease
from photo_search.tagging import service, store
from photo_search.tagging.model import read_model
from photo_search.tagging.policy import POLICY_ID
from photo_search.tagging.runtime import RamTagger
from photo_search.tagging.task import read_state


def build(data_root: Path, *, limit: int | None = None, max_jobs: int | None = None,
          retry_failed: bool = False) -> dict:
    if (limit is not None and limit < 1) or (max_jobs is not None and max_jobs < 1):
        raise CatalogError("USAGE", "limit and max-jobs must be positive")
    with acquire_data_root_lease(data_root):
        previous = read_state(data_root)
        if previous and previous["status"] in {"RUNNING", "PAUSE_REQUESTED", "PAUSED", "INTERRUPTED"}:
            raise CatalogError("TAG_TASK_PENDING", "continue the app's existing tag task from its own controls")
        info = read_model(data_root)
        runtime = RamTagger.load(info)
        try:
            with Catalog.open(data_root=data_root) as catalog:
                model = service.register_model(catalog, info)
                scheduler = service.build_scheduler(catalog, runtime)
                executor = service.tag_executor(catalog, runtime)
                while service.recover_cli_leases(catalog, processor_id=model["processor_id"]):
                    pass
                while service.cancel_stale_waiting(catalog, processor_id=model["processor_id"]):
                    pass
                if retry_failed:
                    while service.retry_waiting(catalog, processor_id=model["processor_id"]):
                        pass
                upper = int(catalog.read_one("SELECT COALESCE(MAX(asset_id),0) FROM assets")[0])
                cursor = discovered = completed = 0
                stopped = "COMPLETED"
                while True:
                    if max_jobs is not None and completed >= max_jobs:
                        stopped = "JOB_LIMIT"
                        break
                    if limit is not None and completed >= limit:
                        stopped = "TARGET_LIMIT"
                        break
                    job = service.run_one(scheduler, executor, worker_id="image-tags-cli")
                    if job is not None:
                        completed += 1
                        if job.state != "SUCCEEDED":
                            if job.last_error_code in service.LOCAL_INPUT_ERRORS:
                                continue
                            stopped = job.state
                            break
                        continue
                    if scheduler.read_state().paused:
                        stopped = "SCHEDULER_PAUSED"
                        break
                    batch_limit = min(service.BATCH_LIMIT, limit - completed) if limit is not None else service.BATCH_LIMIT
                    targets = service.discover_targets(catalog, processor_id=model["processor_id"],
                        after_id=cursor, through_id=upper, limit=batch_limit)
                    if not targets:
                        counts = (store.status(catalog.connection, model["processor_id"]) or {}).get("job_counts", {})
                        if any(value for key, value in counts.items() if key in {"QUEUED", "RUNNING", "RETRY_WAIT"}):
                            stopped = "JOBS_PENDING"
                        elif any(value for key, value in counts.items() if key in {"FAILED", "BLOCKED"}):
                            stopped = "IMAGES_SKIPPED"
                        break
                    service.enqueue_targets(catalog, targets, processor_id=model["processor_id"])
                    discovered += len(targets)
                    cursor = targets[-1]["asset_id"]
                return {"state": stopped, "discovered": discovered, "processed": completed,
                        "model": store.status(catalog.connection, model["processor_id"])}
        finally:
            runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "build"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-jobs", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    root = resolve_data_root(args.data_root)
    try:
        if args.command == "build":
            result = build(root, limit=args.limit, max_jobs=args.max_jobs, retry_failed=args.retry_failed)
        else:
            info = read_model(root)
            result = {"model_id": info.model_id, "policy_id": POLICY_ID, "task": read_state(root), "coverage": None}
            if catalog_db_path(root).is_file():
                import sqlite3

                with sqlite3.connect(f"file:{catalog_db_path(root).as_posix()}?mode=ro", uri=True) as connection:
                    connection.row_factory = sqlite3.Row
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='machine_tag_models'").fetchone() is None:
                        result["state"] = "SCHEMA_UPGRADE_REQUIRED"
                    else:
                        result["coverage"] = store.status(connection, store.processor_id_for(info.model_id, POLICY_ID))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("state") not in {"FAILED", "BLOCKED", "JOBS_PENDING", "IMAGES_SKIPPED", "SCHEMA_UPGRADE_REQUIRED"} else 2
    except (CatalogError, DataRootLeaseBusy) as error:
        print(json.dumps({"state": "ERROR", "error": getattr(error, "code", "DATA_ROOT_BUSY")}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
