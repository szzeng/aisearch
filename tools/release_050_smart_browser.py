"""Synthetic TEXT/COMBINED smart refresh fixture for 0.5.0 browser acceptance.

The fixture is copied from the prior synthetic smart-browser run into a new task-local
directory. Only a deterministic query encoder is injected, through the normal launcher
adapter-wiring seam; auth, routes, refresh worker, lease, CAS and publication remain live.
No real model or real photo source is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / ".tmp" / "P4-RELEASE-050" / "browser-smart"
BASE = REPO / ".tmp" / "smart-followup-browser-20261019"
DATA = ROOT / "data"
LIBRARY = ROOT / "library"
FAIL_MARKER = ROOT / "fail-next-query.marker"
PORT = 18771
SPACE = "release-050-synthetic-smart-space"
PROCESSOR = "release-050-synthetic-smart-query@1"
PROFILE_ID = "release-050-synthetic-smart-profile"


def _copy_synthetic_fixture() -> None:
    if ROOT.exists():
        raise SystemExit(f"refusing to overwrite existing fixture: {ROOT}")
    if not (BASE / "data" / "catalog" / "catalog.db").is_file():
        raise SystemExit(f"synthetic seed fixture is missing: {BASE}")

    def ignore_runtime(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name == "runtime"}

    ROOT.mkdir(parents=True)
    shutil.copytree(BASE / "data", DATA, ignore=ignore_runtime)
    shutil.copytree(BASE / "library", LIBRARY)
    database = DATA / "catalog" / "catalog.db"
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT source_id,root_path FROM source_roots").fetchall()
        if not rows:
            raise SystemExit("copied synthetic catalog has no source roots")
        for source_id, _old_path in rows:
            connection.execute(
                "UPDATE source_roots SET root_path=? WHERE source_id=?",
                (str(LIBRARY.resolve()), int(source_id)),
            )
    for image in LIBRARY.rglob("*"):
        if image.is_file() and image.suffix.lower() not in {".jpg", ".jpeg"}:
            raise SystemExit(f"copied source contains an unexpected file: {image}")


def _synthetic_rule(kind: str, generation_id: str | None = None) -> dict[str, Any]:
    from photo_search.api.models import PlaceAwareSearchRequestModel

    request = PlaceAwareSearchRequestModel.model_validate({
        "request_id": f"release-050-{kind.lower()}-seed",
        "retrieval_mode": "text" if kind == "TEXT" else "combined",
        "original_query": "photo",
        "text": "photo",
        "content_query": "photo",
        "page_size": 50,
        "top_k": 50,
    })
    values = request.model_dump(exclude={"request_id", "search_session_id", "cursor"})
    rule: dict[str, Any] = {
        "version": 1,
        "execution_kind": kind,
        "request": values,
        "display_query": "photo",
        "content_query": "photo",
        "confirmed_execution_mode": "ranked_candidates",
        "confirmed_scope": None,
        "confirmed_place_scope": None,
        "confirmed_person_scope": None,
        "confirmed_filter_revision": None,
        "confirmed_suggestion_revision": None,
        "confirmed_favorites_revision": None,
        "search_binding": None,
    }
    if kind == "COMBINED":
        if generation_id is None:
            raise ValueError("combined rules require a synthetic generation")
        rule["search_binding"] = {
            "profile_id": PROFILE_ID,
            "space_id": SPACE,
            "image_generation_id": generation_id,
            "dimension": 3,
        }
    return rule


def _insert_active_profile(catalog: Any, spec: Any, generation_id: str) -> None:
    from photo_search.search.profile import DEFAULT_FUSION_CONFIG, activate_profile
    from photo_search.search.tokens import FTS_GENERATION_ID, TOKENIZER_VERSION

    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO search_profiles(profile_id,embedding_processor_id,space_id,image_generation_id,"
            "query_preprocess_version,query_parser_version,fts_tokenizer_version,fts_generation_id,"
            "reranker_processor_id,fusion_config_json,runtime_package_id,created_at) "
            "VALUES(?,?,?,?,?,'parser-v1',?,?,NULL,?,?,?)",
            (
                PROFILE_ID,
                str(spec.processor_id),
                SPACE,
                str(generation_id),
                str(spec.envelope.get("query_preprocess_version", "query-v1")),
                str(TOKENIZER_VERSION),
                FTS_GENERATION_ID,
                json.dumps(DEFAULT_FUSION_CONFIG, sort_keys=True),
                str(spec.runtime_package_id),
                int(catalog.now_ms()),
            ),
        )
    activate_profile(catalog, profile_id=PROFILE_ID)


def seed() -> None:
    _copy_synthetic_fixture()
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "tests" / "integration")]
    from p2_search_fixtures import (
        configured_scheduler,
        embedding_spec,
        index_vector,
    )

    from photo_search import photo_smart_collections
    from photo_search.ingestion.service import CatalogService

    service = CatalogService.open(data_root=DATA)
    try:
        catalog = service.catalog
        roots = catalog.read_all("SELECT source_id,root_path FROM source_roots ORDER BY source_id")
        if not roots or any(Path(str(row["root_path"])).resolve() != LIBRARY.resolve() for row in roots):
            raise RuntimeError(f"fixture source roots escape the new synthetic directory: {roots!r}")
        asset_rows = catalog.read_all(
            "SELECT a.asset_id,l.relative_path FROM assets a JOIN file_locations l USING(asset_id) "
            "WHERE l.availability='PRESENT' ORDER BY l.relative_path"
        )
        asset_ids = [int(row["asset_id"]) for row in asset_rows]
        if len(asset_ids) < 2:
            raise RuntimeError("synthetic fixture must contain at least two PRESENT photo assets")

        for row in catalog.read_all(
            "SELECT smart_id,instance_token,revision FROM photo_smart_collections ORDER BY smart_id"
        ):
            photo_smart_collections.delete_collection(
                catalog,
                int(row["smart_id"]),
                instance_token=str(row["instance_token"]),
                expected_revision=int(row["revision"]),
            )

        spec = embedding_spec(space_id=SPACE, processor_id=PROCESSOR)
        scheduler = configured_scheduler(catalog, [spec])
        for index, asset_id in enumerate(asset_ids):
            index_vector(
                catalog,
                scheduler,
                spec,
                asset_id=asset_id,
                vector=[1.0, 0.0, 0.0],
                artifact_id=f"release-050-synthetic-smart-{index + 1}",
            )
        generation_id = str(catalog.read_one(
            "SELECT generation_id FROM index_generations WHERE space_id=? AND is_active=1", (SPACE,)
        )["generation_id"])
        _insert_active_profile(catalog, spec, generation_id)

        created: dict[str, int] = {}
        for kind in ("TEXT", "COMBINED"):
            rule = _synthetic_rule(kind, generation_id if kind == "COMBINED" else None)
            smart = photo_smart_collections.create_collection(
                catalog,
                f"Release 0.5.0 {kind} synthetic",
                f"release-050-{kind.lower()}-create",
                rule,
                request_hash=f"release-050-{kind.lower()}-request",
                auth_hash=f"release-050-{kind.lower()}-seed-auth",
                source_hash=f"release-050-{kind.lower()}-seed-source",
            )["collection"]
            with catalog.read_snapshot() as connection:
                signature, indexing, _ = photo_smart_collections.dependency_signature(connection, rule)
            photo_smart_collections.publish_candidates(
                catalog,
                int(smart["smart_id"]),
                asset_ids,
                signature=signature,
                indexing=indexing,
            )
            created[kind] = int(smart["smart_id"])

        marker = ROOT / "fail-next-query.marker"
        manifest = {
            "data_root": str(DATA.resolve()),
            "source_root": str(LIBRARY.resolve()),
            "synthetic_source_files": [
                {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(LIBRARY.rglob("*")) if path.is_file()
            ],
            "schema_version": catalog.schema_version,
            "source_roots": [str(row["root_path"]) for row in roots],
            "asset_ids": asset_ids,
            "profile_id": PROFILE_ID,
            "space_id": SPACE,
            "generation_id": generation_id,
            "smart_ids": created,
            "fail_marker": str(marker.resolve()),
            "marker_action": "create the marker to fail the next synthetic COMBINED query encode once; it is consumed by the fake encoder",
        }
        (ROOT / "fixture.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    finally:
        service.close()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


class MarkerQueryEncoder:
    def __init__(self) -> None:
        sys.path.insert(0, str(REPO / "tests"))
        from p2_search_fixtures import embedding_spec

        self._spec = embedding_spec(space_id=SPACE, processor_id=PROCESSOR)

    def spec(self) -> Any:
        return self._spec

    def encode_queries(self, texts: Any, cancellation: Any = None) -> list[list[float]]:
        if FAIL_MARKER.is_file():
            FAIL_MARKER.unlink()
            raise RuntimeError("synthetic COMBINED query failure injected by marker")
        return [[1.0, 0.0, 0.0] for _ in texts]

    def health(self) -> dict[str, Any]:
        return {"health_status": "healthy", "provider": "release-050-synthetic-encoder"}

    def close(self) -> None:
        return None


def serve(port: int) -> None:
    if not (ROOT / "fixture.json").is_file():
        raise SystemExit("run seed first")
    from photo_search import __main__ as launcher
    from photo_search.workers.query_encoder import EncoderWiring

    def synthetic_wiring(_args: Any, _data_root: Path) -> EncoderWiring:
        return EncoderWiring(
            reason="SYNTHETIC_TEST_ENCODER_READY",
            model_dir=str(ROOT / "synthetic-query-encoder"),
            manifest=str(ROOT / "synthetic-query-encoder" / "manifest.json"),
            factory=MarkerQueryEncoder,
            provider_id="release-050-synthetic-query-encoder",
        )

    launcher._encoder_wiring = synthetic_wiring
    sys.argv = [
        "photo-search", "--host", "127.0.0.1", "--port", str(int(port)),
        "--data-root", str(DATA),
    ]
    launcher.main()


def audit() -> None:
    from photo_search import photo_smart_collections
    from photo_search.catalog.db import Catalog

    with Catalog.open(data_root=DATA) as catalog:
        result: dict[str, Any] = {
            "schema_version": catalog.schema_version,
            "source_roots": [dict(row) for row in catalog.read_all(
                "SELECT source_id,display_name,root_path,status FROM source_roots ORDER BY source_id"
            )],
            "profile": dict(catalog.read_one(
                "SELECT a.profile_id,p.space_id,p.image_generation_id,p.embedding_processor_id "
                "FROM active_search_profile a JOIN search_profiles p USING(profile_id) WHERE a.state_id=1"
            )),
            "smart_collections": [],
        }
        for row in catalog.read_all("SELECT smart_id,instance_token FROM photo_smart_collections ORDER BY smart_id"):
            detail = photo_smart_collections.detail(
                catalog, int(row["smart_id"]), instance_token=str(row["instance_token"])
            )
            cache = catalog.read_one(
                "SELECT build_id,candidate_count,dependency_signature,cache_rule_revision,refresh_status "
                "FROM photo_smart_cache_state WHERE smart_id=?", (int(row["smart_id"]),)
            )
            result["smart_collections"].append({**detail["collection"], "rule": detail["rule"], "cache": dict(cache)})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "serve", "audit"))
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.action == "seed":
        seed()
    elif args.action == "audit":
        audit()
    else:
        serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
