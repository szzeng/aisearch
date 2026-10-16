"""P4 personal semantic indexing recovery, using only isolated synthetic catalogs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from p2_search_api_fixtures import SearchEnv, build_search_env

from photo_search.face.contract import FACE_SPEC_REVISION
from photo_search.indexing.maintenance import drain_semantic_outbox
from photo_search.persons.contracts import name_key_for
from photo_search.persons.members import MEMBER_PREDICATE
from photo_search.persons.service import PersonService


def _add_person(catalog: Any, name: str | None = None) -> int:
    with catalog.write() as connection:
        cursor = connection.execute(
            "INSERT INTO persons(name, name_key, avatar_face_id, merged_into, revision, "
            "created_at, updated_at) VALUES (?, ?, NULL, NULL, 1, 1, 1)",
            (name, None if name is None else name_key_for(name)),
        )
        return int(cursor.lastrowid)


def _add_face_link(catalog: Any, asset_id: int, person_id: int, suffix: str) -> None:
    face_id = (suffix * 64)[:64]
    asset = catalog.read_one("SELECT sha256 FROM assets WHERE asset_id = ?", (int(asset_id),))
    location = catalog.read_one(
        "SELECT location_id FROM file_locations WHERE asset_id = ? ORDER BY location_id LIMIT 1",
        (int(asset_id),),
    )
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO faces(face_id, asset_id, spec_revision, detector_id, ordinal, "
            "input_revision, location_id, bbox_json, landmarks_json, det_score, face_pixels, "
            "blur_variance, quality_state, quality_reason, artifact_id, face_revision, "
            "created_at, updated_at) VALUES (?, ?, ?, 'synthetic', 0, ?, ?, '[0,0,10,10]', "
            "'[[1,2],[2,3],[3,4],[4,5],[5,6]]', 0.9, 120, 200, 'OK', NULL, 'synthetic', "
            "1, 1, 1)",
            (face_id, int(asset_id), str(FACE_SPEC_REVISION), str(asset["sha256"]), int(location["location_id"])),
        )
        connection.execute(
            "INSERT INTO face_person_links(face_id, person_id, assignment_kind, decision, "
            "confidence, reason, revision, last_operation_id, created_at, updated_at) "
            "VALUES (?, ?, 'AUTO', 'CONFIRMED', 0.9, NULL, 1, NULL, 1, 1)",
            (face_id, int(person_id)),
        )


def _search(env: SearchEnv, person_id: int, request_id: str) -> Any:
    async def scenario(client: Any) -> Any:
        csrf = await env.authenticate(client)
        body = {
            "contract_version": "p2-contracts.v1",
            "request_id": request_id,
            **env.explicit_binding(),
            "text": "mountain",
            "person_ids": [int(person_id)],
            "person_match": "any",
            "page_size": 60,
            "top_k": 5000,
        }
        return await client.post(
            "/api/v1/search", json=body, headers=SearchEnv.post_headers(csrf)
        )

    return env.scenario(scenario)


@pytest.fixture()
def search_env(tmp_path: Path):
    env = build_search_env(tmp_path)
    try:
        yield env
    finally:
        env.context.close()
        env.library.close()


def test_indexing_cli_cold_start_and_zero_limit_are_safe(tmp_path: Path) -> None:
    data_root = tmp_path / "isolated-data"
    commands = [
        ["--help"],
        ["status", "--data-root", str(data_root), "--json"],
        ["drain", "--data-root", str(data_root), "--limit", "0", "--json"],
    ]
    payloads = []
    for arguments in commands:
        result = subprocess.run(
            [sys.executable, "-m", "photo_search.indexing.cli", *arguments],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        if arguments[-1] == "--json":
            payloads.append(json.loads(result.stdout))
    assert payloads[0]["status"] in {"ok", "not_initialized"}
    assert payloads[1]["status"] == "ok" and payloads[1]["applied"] == 0
    assert payloads[1]["write_transactions"] == 0


def test_person_rename_reprojects_existing_vectors_without_reencoding(search_env: SearchEnv) -> None:
    env = search_env
    catalog = env.catalog
    env.encoder._vectors["mountain"] = [1.0, 0.0, 0.0]
    person_id = _add_person(catalog)
    other_person_id = _add_person(catalog, "Other")
    sunset = int(env.assets["beach sunset.jpg"])
    second_member = int(env.assets["IMG_0001.JPG"])
    other = int(env.assets["杭州西湖 照片.jpg"])
    _add_face_link(catalog, sunset, person_id, "a")
    _add_face_link(catalog, second_member, person_id, "b")
    _add_face_link(catalog, other, other_person_id, "c")
    # Model a genuine NO_ENTRY member while retaining one searchable member vector.
    with catalog.write() as connection:
        connection.execute(
            "DELETE FROM index_entries WHERE generation_id = ? AND asset_id = ?",
            (env.generation, second_member),
        )

    before = _search(env, person_id, "before-name")
    assert before.status_code == 200, before.text
    assert sunset in [int(row["asset_id"]) for row in before.json()["hits"]]

    person_service = PersonService(catalog)
    prior_projection = {
        int(row["asset_id"]): int(row["revision"])
        for row in catalog.read_all(
            "SELECT asset_id, MAX(projection_revision) AS revision FROM index_outbox "
            "WHERE generation_id=? AND asset_id IN (?,?) GROUP BY asset_id",
            (env.generation, sunset, second_member),
        )
    }
    renamed = person_service.rename(
        person_id=person_id, name="Alice", request_id="semantic-rename", expected_revision=1
    )
    assert renamed["changed"] is True
    assert renamed["items"][0]["changed"] is True

    classified = catalog.read_all(
        "SELECT e.asset_id, e.state FROM index_entries e WHERE e.generation_id = ? "
        "AND e.asset_id IN (?, ?) ORDER BY e.asset_id",
        (env.generation, sunset, second_member),
    )
    assert len(classified) == 1
    assert classified[0]["state"] == "PENDING_REPAIR"
    generation = catalog.read_one(
        "SELECT generation_id, space_id FROM index_generations WHERE generation_id = ?",
        (env.generation,),
    )
    counts = catalog.read_one(
        "SELECT COUNT(DISTINCT f.asset_id) AS total, "
        "COUNT(DISTINCT CASE WHEN e.state = 'CONFIRMED' THEN f.asset_id END) AS confirmed, "
        "COUNT(DISTINCT CASE WHEN e.state = 'PENDING_REPAIR' THEN f.asset_id END) AS pending, "
        "COUNT(DISTINCT CASE WHEN e.state = 'STALE' THEN f.asset_id END) AS stale, "
        "COUNT(DISTINCT CASE WHEN e.asset_id IS NULL THEN f.asset_id END) AS no_entry "
        "FROM face_person_links l JOIN faces f ON f.face_id = l.face_id "
        "LEFT JOIN index_entries e ON e.asset_id = f.asset_id AND e.generation_id = ? "
        "AND e.space_id = ? WHERE l.person_id = ? AND " + MEMBER_PREDICATE,
        (generation["generation_id"], generation["space_id"], person_id),
    )
    classified_counts = tuple(
        int(counts[key]) for key in ("total", "confirmed", "pending", "stale", "no_entry")
    )
    assert classified_counts == (2, 0, 1, 0, 1)
    outbox = catalog.read_all(
        "SELECT o.asset_id, o.state, o.projection_revision, o.last_error_code "
        "FROM index_outbox o WHERE o.generation_id = ? AND o.asset_id IN (?, ?) "
        "AND o.projection_revision = (SELECT MAX(latest.projection_revision) "
        "FROM index_outbox latest WHERE latest.space_id = o.space_id "
        "AND latest.asset_id = o.asset_id AND latest.generation_id = o.generation_id) "
        "ORDER BY o.asset_id",
        (env.generation, sunset, second_member),
    )
    assert len(outbox) == 2
    assert {
        int(row["asset_id"]): (
            str(row["state"]), int(row["projection_revision"]), row["last_error_code"]
        )
        for row in outbox
    } == {
        sunset: ("PENDING", prior_projection[sunset] + 1, None),
        second_member: ("CONFIRMED", prior_projection[second_member], None),
    }

    replay = person_service.rename(
        person_id=person_id, name="Alice", request_id="semantic-rename", expected_revision=1
    )
    assert replay["replayed"] is True
    assert int(catalog.read_one("SELECT COUNT(*) FROM index_outbox WHERE state = 'PENDING'")[0]) == 1
    revision_event = catalog.read_one(
        "SELECT cause, changed_count FROM catalog_filter_revision_events "
        "ORDER BY revision DESC LIMIT 1"
    )
    assert (str(revision_event["cause"]), int(revision_event["changed_count"])) == (
        "PERSON_CHANGED",
        2,
    )

    missing = _search(env, person_id, "before-drain")
    assert missing.status_code == 200, missing.text
    missing_body = missing.json()
    assert missing_body["channels"]["authoritative"]["total"] == 2
    assert missing_body["channels"]["vector"]["engaged"] is True
    assert missing_body["channels"]["vector"]["engine_blocks"] == 0
    assert missing_body["channels"]["fts"]["candidates"] == 0
    assert missing_body["hits"] == []

    artifacts_before = int(catalog.read_one("SELECT COUNT(*) FROM artifacts")[0])
    drained = drain_semantic_outbox(catalog, limit=10)
    assert drained["status"] == "ok" and drained["applied"] == 1
    assert int(catalog.read_one("SELECT COUNT(*) FROM artifacts")[0]) == artifacts_before
    assert int(
        catalog.read_one(
            "SELECT COUNT(*) FROM index_entries WHERE generation_id = ? AND asset_id = ? "
            "AND state = 'CONFIRMED'",
            (env.generation, sunset),
        )[0]
    ) == 1
    assert catalog.read_one(
        "SELECT 1 FROM index_entries WHERE generation_id = ? AND asset_id = ?",
        (env.generation, second_member),
    ) is None

    restored = _search(env, person_id, "after-drain")
    assert restored.status_code == 200, restored.text
    hits = [int(row["asset_id"]) for row in restored.json()["hits"]]
    assert sunset in hits
    assert other not in hits
    assert len(env.encoder.calls) == 3


def test_album_empty_search_distinguishes_missing_semantic_index() -> None:
    node = shutil.which("node")
    assert node, "Node is required for the personal album behavior check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element{constructor(){this.children=[];this.attributes={};this.classList={add(){}};}append(){}addEventListener(){}setAttribute(){} }
const els=new Map(),get=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id);};
const ctx={URLSearchParams,crypto:{randomUUID:()=>"id"},setTimeout,clearTimeout,
 window:{location:{search:"",href:"http://local/persons"},addEventListener(){},confirm:()=>true},
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:()=>new Element()},
 fetch:async()=>({ok:true,json:async()=>({})})};
let code=fs.readFileSync("src/photo_search/web/persons-albums.js","utf8");
code=code.replace('  if (document.readyState === "loading")','  globalThis.testUi={emptySearchNote};\n  if (document.readyState === "loading")');
vm.createContext(ctx);vm.runInContext(code,ctx);
const missing={channels:{authoritative:{total:131},vector:{engaged:true,engine_blocks:0},fts:{candidates:0}}};
assert.equal(ctx.testUi.emptySearchNote(missing),"此人物的语义索引尚未就绪，可先查看全部照片。");
assert.equal(ctx.testUi.emptySearchNote({channels:{authoritative:{total:131},vector:{engaged:true,engine_blocks:2},fts:{candidates:0}}}),"没有找到匹配的照片。");
assert.equal(ctx.testUi.emptySearchNote({channels:{authoritative:{total:0},vector:{engaged:true,engine_blocks:0},fts:{candidates:0}}}),"没有找到匹配的照片。");
console.log("person album semantic empty-state checks passed");
"""
    subprocess.run([node, "-e", script], check=True, cwd=Path.cwd())
