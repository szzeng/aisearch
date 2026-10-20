from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from httpx import AsyncClient


def _seed_asset(catalog, asset_id: int, *, locations=(), state: str = "ACTIVE") -> None:
    sha = f"{asset_id:064x}"
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,?,?,?)",
            (asset_id, sha, 1, 1, state),
        )
        for index, (source_id, availability) in enumerate(locations):
            connection.execute(
                "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,last_seen_scan_id,availability) "
                "VALUES(?,?,?,?,1,1,NULL,?)",
                (source_id, f"asset-{asset_id}-{index}.jpg", f"asset-{asset_id}-{index}.jpg", asset_id, availability),
            )


def _seed_retention_eligible(catalog, asset_id: int, source_id: int, *, now_ms: int) -> None:
    from photo_search.retention import OBSERVATION_MS

    first_missing = int(now_ms) - OBSERVATION_MS - 1_000
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) VALUES(?,?,1,1,'ACTIVE')",
            (asset_id, f"{asset_id:064x}"),
        )
        connection.execute(
            "INSERT INTO file_locations(source_id,relative_path,path_key,asset_id,size,mtime_ns,"
            "last_seen_scan_id,availability) VALUES(?,?,?, ?,1,1,NULL,'MISSING')",
            (source_id, f"retention-{asset_id}.jpg", f"retention-{asset_id}.jpg", asset_id),
        )
        cursor = connection.execute(
            "INSERT INTO scan_runs(source_id,started_at,finished_at,state,error) "
            "VALUES(?,?,?,'SUCCEEDED',NULL)",
            (source_id, first_missing + OBSERVATION_MS, now_ms),
        )
        scan_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE source_roots SET last_successful_scan=? WHERE source_id=?",
            (scan_id, source_id),
        )
        connection.execute(
            "INSERT INTO asset_retention_source_evidence(asset_id,source_id,scan_id,"
            "missing_confirmed_at_ms) VALUES(?,?,?,?)",
            (asset_id, source_id, scan_id, now_ms),
        )
        connection.execute(
            "INSERT INTO asset_retention_state(asset_id,first_missing_at_ms,last_qualified_at_ms,"
            "state,updated_at_ms) VALUES(?,?,?,'ELIGIBLE',?)",
            (asset_id, first_missing, now_ms, now_ms),
        )


def test_library_retention_preview_is_bounded_read_only_and_keyset_paged(api_env) -> None:
    catalog = api_env.service.catalog
    path_a, path_b = api_env.tmp_path / "source-a", api_env.tmp_path / "source-b"
    path_a.mkdir()
    path_b.mkdir()
    source_a = api_env.register_source("A", path_a)
    source_b = api_env.register_source("B", path_b)
    a, b = int(source_a["source_id"]), int(source_b["source_id"])
    _seed_asset(catalog, 1, locations=((a, "MISSING"), (b, "PRESENT")))
    _seed_asset(catalog, 2, locations=((a, "MISSING"),))
    _seed_asset(catalog, 3, locations=((a, "MISSING"),))
    for asset_id in range(4, 55):
        _seed_asset(catalog, asset_id)
    with catalog.write() as connection:
        connection.execute("DELETE FROM search_projection_queue WHERE asset_id = 2")
        connection.execute(
            "INSERT INTO jobs(subject_type,subject_id,input_revision,task_type,processor_id,state,run_after,created_at,updated_at) "
            "VALUES('asset','3','r1','semantic','test-v1','QUEUED',1,1,1)"
        )

    before = catalog.connection.total_changes

    async def scenario(client: AsyncClient) -> dict:
        denied = await client.get("/api/v1/storage/library-retention")
        await api_env.authenticate(client)
        first = await client.get("/api/v1/storage/library-retention")
        next_cursor = first.json()["next_cursor"]
        second = await client.get("/api/v1/storage/library-retention", params={"after_asset_id": next_cursor})
        return {"denied": denied, "first": first, "second": second}

    result = api_env.scenario(scenario)
    first, second = result["first"].json(), result["second"].json()
    assert result["denied"].status_code == 401
    assert first["read_only"] is True
    assert first["unknown_reference_schema"] is False
    assert first["counts"] == {
        "missing_locations": 3,
        "assets_without_present": 53,
        "zero_location_assets": 51,
        "assets_with_present_copy": 1,
    }
    rows = first["items"] + second["items"]
    assert len(first["items"]) == 50
    assert len(second["items"]) == 4
    assert len({row["asset_id"] for row in rows}) == 54
    by_id = {row["asset_id"]: row for row in rows}
    assert by_id[1]["classification"] == "已有有效副本"
    assert by_id[1]["has_human_or_protected_references"] is True
    assert by_id[2]["classification"] == "全局无可用位置但证据不足"
    assert "观察期起点未知" in by_id[2]["retention_eligibility"]
    assert by_id[3]["classification"] == "包含人工信息或受保护引用"
    assert by_id[3]["has_human_or_protected_references"] is True
    assert by_id[4]["classification"] == "来源已取消登记/零位置"
    assert all("path" not in key for row in rows for key in row)
    assert catalog.connection.total_changes == before


def test_library_retention_cleanup_authorization_and_progress_are_persisted(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        before = await client.get("/api/v1/storage/library-retention")
        cleanup = await client.post(
            "/api/v1/storage/library-retention/cleanup", json={"confirmed": True},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        assert cleanup.status_code == 200, cleanup.text
        denied = await client.post(
            "/api/v1/storage/library-retention/cleanup", json={"confirmed": False},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        progress = await client.get(
            "/api/v1/storage/library-retention/runs/" + cleanup.json()["run_id"]
        )
        toggle = await client.post(
            "/api/v1/storage/library-retention/automatic", json={"enabled": True},
            headers=api_env.post_headers(api_env.csrf_token),
        )
        return {"before": before, "toggle": toggle, "cleanup": cleanup,
                "denied": denied, "progress": progress}

    result = api_env.scenario(scenario)
    assert result["before"].json()["automatic"]["automatic_enabled"] is False
    assert result["toggle"].status_code == 200
    assert result["toggle"].json()["automatic_enabled"] is True
    assert result["cleanup"].status_code == 200
    run = result["cleanup"].json()
    assert run["state"] in {"QUEUED", "RUNNING", "PARTIAL", "COMPLETE"}
    assert result["progress"].json()["run"]["state"] in {"QUEUED", "RUNNING", "PARTIAL", "COMPLETE"}
    assert result["progress"].json()["durable"] is True
    assert result["denied"].status_code in {400, 422}


def test_library_retention_worker_persists_revalidated_skip(api_env) -> None:
    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-worker"
    source_path.mkdir()
    source = api_env.register_source("worker", source_path)
    _seed_asset(catalog, 17, locations=((int(source["source_id"]), "MISSING"),))
    with catalog.write() as connection:
        connection.execute("DELETE FROM search_projection_queue WHERE asset_id=17")

    async def scenario(client: AsyncClient) -> dict:
        await api_env.authenticate(client)
        headers = api_env.post_headers(api_env.csrf_token)
        started = await client.post(
            "/api/v1/storage/library-retention/cleanup", json={"confirmed": True}, headers=headers
        )
        assert started.status_code == 200, started.text
        assert started.json().get("total_assets") == 1, started.json()
        run_id = started.json()["run_id"]
        progress = None
        for _ in range(80):
            response = await client.get("/api/v1/storage/library-retention/runs/" + run_id)
            progress = response.json()
            if progress["run"]["state"] not in {"QUEUED", "RUNNING"}:
                break
            await asyncio.sleep(0.025)
        return {"started": started, "progress": progress}

    result = api_env.scenario(scenario)
    progress = result["progress"]
    assert progress["durable"] is True
    assert progress["run"]["state"] == "PARTIAL", progress
    assert progress["run"]["skipped_assets"] == 1
    assert progress["items"][0]["state"] == "SKIPPED"
    assert "来源缺少匹配的成功失效复核证据" in progress["items"][0]["reason"]


def test_library_retention_resumes_partial_run_after_lock(api_env, monkeypatch) -> None:
    catalog = api_env.service.catalog
    now = int(catalog.now_ms())
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO library_retention_runs(run_id,mode,state,stage,total_assets,created_at_ms,updated_at_ms) "
            "VALUES('resume-manual','MANUAL','PARTIAL','可恢复待继续',1,?,?)", (now, now)
        )
        connection.execute(
            "INSERT INTO library_retention_run_items(run_id,asset_id,state,stage) "
            "VALUES('resume-manual',123,'QUEUED','等待向量删除回执')"
        )

    started: list[str] = []

    class Runner:
        def start(self, run_id: str) -> bool:
            started.append(run_id)
            return True

    monkeypatch.setattr(api_env.context, "retention_cleanup_runner", lambda: Runner())
    api_env.context.resume_retention_cleanup_after_lock()

    run = catalog.read_one(
        "SELECT state FROM library_retention_runs WHERE run_id='resume-manual'"
    )
    assert started == ["resume-manual"]
    assert run["state"] == "QUEUED"


def test_library_retention_preview_fails_closed_on_unknown_reference_table(api_env) -> None:
    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source = api_env.register_source("A", source_path)
    _seed_asset(catalog, 1, locations=((int(source["source_id"]), "MISSING"),))
    with catalog.write() as connection:
        connection.execute("CREATE TABLE future_asset_reference(asset_id INTEGER NOT NULL)")
        connection.execute("INSERT INTO future_asset_reference(asset_id) VALUES(1)")

    async def scenario(client: AsyncClient) -> int:
        await api_env.authenticate(client)
        response = await client.get("/api/v1/storage/library-retention")
        # The normal API schema guard refuses an unregistered catalog table.
        return response.status_code

    assert api_env.scenario(scenario) == 409
    from photo_search.library_retention import library_retention_preview
    result = library_retention_preview(catalog)
    assert result["unknown_reference_schema"] is True
    item = result["items"][0]
    assert item["classification"] == "待核实/受保护"
    assert item["has_human_or_protected_references"] is True
    assert "无法解释" in item["reason"]


def test_zero_location_asset_with_unknown_reference_stays_protected(api_env) -> None:
    from photo_search.library_retention import library_retention_preview

    catalog = api_env.service.catalog
    _seed_asset(catalog, 1)
    with catalog.write() as connection:
        connection.execute("CREATE TABLE future_asset_reference(asset_id INTEGER NOT NULL)")

    result = library_retention_preview(catalog)
    item = result["items"][0]
    assert result["unknown_reference_schema"] is True
    assert item["classification"] == "待核实/受保护"
    assert item["has_human_or_protected_references"] is True


def test_library_retention_renderer_displays_classification_counts_and_next_page() -> None:
    source = Path("src/photo_search/web/app.js").read_text(encoding="utf-8")
    start = source.index("function renderLibraryRetention()")
    end = source.index("\n  function loadLibraryRetention", start)
    renderer = source[start:end]
    model = {
        "overview": {
            "counts": {
                "missing_locations": 2,
                "assets_without_present": 3,
                "zero_location_assets": 1,
                "assets_with_present_copy": 4,
            },
            "retention_time_evidence": "首次失效时间未知，不具备清理资格。",
            "retention_counts": {"OBSERVING": 2, "ELIGIBLE": 1, "BLOCKED": 1},
            "unresolved_source_anomalies": 1,
            "source_anomalies": [{"source_id": 7, "scan_id": 11, "baseline_present_assets": 12, "newly_missing_assets": 10}],
            "storage": {"sqlite_catalog_file_bytes": 128, "sqlite_logical_page_bytes": 256},
            "next_cursor": 20,
        },
        "items": [
            {
                "asset_id": 19,
                "classification": "待核实/受保护",
                "availability_summary": "没有位置记录",
                "reason": "发现无法解释的资产引用类型",
                "has_human_or_protected_references": True,
                "retention_eligibility": "不具备清理资格（观察期起点未知）",
            }
        ],
    }
    harness = "\n".join(
        [
            f"const state = {{ libraryRetention: {json.dumps(model, ensure_ascii=False)} }};",
            "const els = { libraryRetentionStatus: { textContent: '' }, libraryRetentionMore: { hidden: true, disabled: false }, libraryRetentionAnomalies: { children: [], replaceChildren() { this.children = []; }, appendChild(node) { this.children.push(node); } } };",
            "const document = { createElement() { return { dataset: {}, children: [], append(...nodes) { this.children.push(...nodes); }, set textContent(value) { this.text = value; }, get textContent() { return this.text; } }; } };",
            "function text(node, value) { node.textContent = value; }",
            "function formatBytes(value) { return value + ' bytes'; }",
            renderer,
            "renderLibraryRetention();",
            "process.stdout.write(JSON.stringify({ text: els.libraryRetentionStatus.textContent, hidden: els.libraryRetentionMore.hidden, anomalies: els.libraryRetentionAnomalies.children.map(row => row.children.map(node => node.textContent || node.text).join(' ')) }));",
        ]
    )
    result = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, encoding="utf-8")
    rendered = json.loads(result.stdout)
    assert "MISSING 位置 2" in rendered["text"]
    assert "待核实/受保护" in rendered["text"]
    assert "首次失效时间未知" in rendered["text"]
    assert "来源批量异常待核实 1" in rendered["text"]
    assert "10/扫描前有效资产 12" in json.dumps(rendered, ensure_ascii=False)
    assert "128 bytes" in rendered["text"]
    assert rendered["hidden"] is False


def test_retention_observation_requires_each_source_and_post_expiry_success(api_env) -> None:
    from photo_search.retention import OBSERVATION_MS, record_successful_source_scan

    catalog = api_env.service.catalog
    source_path_a = api_env.tmp_path / "source-a"
    source_path_b = api_env.tmp_path / "source-b"
    source_path_a.mkdir()
    source_path_b.mkdir()
    source_a = int(api_env.register_source("A", source_path_a)["source_id"])
    source_b = int(api_env.register_source("B", source_path_b)["source_id"])
    _seed_asset(catalog, 901, locations=((source_a, "MISSING"), (source_b, "MISSING")))

    def successful_scan(source_id: int, stamp: int) -> int:
        with catalog.write() as connection:
            scan_id = int(connection.execute(
                "INSERT INTO scan_runs(source_id, started_at, finished_at, state) VALUES(?,?,?,'SUCCEEDED')",
                (source_id, stamp, stamp),
            ).lastrowid)
            connection.execute(
                "UPDATE source_roots SET status='ONLINE', last_successful_scan=? WHERE source_id=?",
                (scan_id, source_id),
            )
            record_successful_source_scan(
                connection, source_id=source_id, scan_id=scan_id, now_ms=stamp,
                baseline_present_assets=1, newly_missing_assets=0,
            )
            return scan_id

    first_a = successful_scan(source_a, 100_000)
    with catalog.read_snapshot() as connection:
        assert connection.execute("SELECT 1 FROM asset_retention_state WHERE asset_id=901").fetchone() is None
    first_b = successful_scan(source_b, 200_000)
    with catalog.read_snapshot() as connection:
        observing = connection.execute("SELECT * FROM asset_retention_state WHERE asset_id=901").fetchone()
        assert observing["state"] == "OBSERVING"
        assert observing["first_missing_at_ms"] == 200_000

    # Rechecking only one source after expiry cannot qualify an asset with a second source.
    successful_scan(source_a, 200_000 + OBSERVATION_MS + 10)
    with catalog.read_snapshot() as connection:
        assert connection.execute("SELECT state FROM asset_retention_state WHERE asset_id=901").fetchone()[0] == "OBSERVING"
    successful_scan(source_b, 200_000 + OBSERVATION_MS + 20)
    with catalog.read_snapshot() as connection:
        eligible = connection.execute("SELECT * FROM asset_retention_state WHERE asset_id=901").fetchone()
        assert eligible["state"] == "ELIGIBLE"
        assert eligible["last_qualified_at_ms"] == 200_000 + OBSERVATION_MS + 20
    assert first_a < first_b


def test_retention_anomaly_threshold_and_review_reset(api_env) -> None:
    from photo_search.retention import (
        is_bulk_missing_anomaly,
        record_successful_source_scan,
        resolve_source_anomaly,
    )

    assert is_bulk_missing_anomaly(baseline_present_assets=12, newly_missing_assets=10)
    assert not is_bulk_missing_anomaly(baseline_present_assets=12, newly_missing_assets=9)
    assert is_bulk_missing_anomaly(baseline_present_assets=1_000, newly_missing_assets=301)
    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source_id = int(api_env.register_source("A", source_path)["source_id"])
    _seed_asset(catalog, 902, locations=((source_id, "MISSING"),))
    with catalog.write() as connection:
        scan_id = int(connection.execute(
            "INSERT INTO scan_runs(source_id, started_at, finished_at, state) VALUES(?,?,?,'SUCCEEDED')",
            (source_id, 1, 2),
        ).lastrowid)
        record_successful_source_scan(
            connection, source_id=source_id, scan_id=scan_id, now_ms=2_000,
            baseline_present_assets=12, newly_missing_assets=10,
        )
        anomaly = connection.execute(
            "SELECT state FROM retention_source_anomalies WHERE source_id=? AND scan_id=?",
            (source_id, scan_id),
        ).fetchone()
        assert anomaly["state"] == "NEEDS_REVIEW"
        assert connection.execute(
            "SELECT state FROM asset_retention_state WHERE asset_id=902"
        ).fetchone()[0] == "BLOCKED"
        assert resolve_source_anomaly(connection, source_id=source_id, scan_id=scan_id, now_ms=3_000)
        state = connection.execute(
            "SELECT state, first_missing_at_ms FROM asset_retention_state WHERE asset_id=902"
        ).fetchone()
        assert tuple(state) == ("BLOCKED", None)


def test_retention_anomaly_api_acknowledges_without_deleting_data(api_env) -> None:
    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source_id = int(api_env.register_source("A", source_path)["source_id"])
    _seed_asset(catalog, 903, locations=((source_id, "MISSING"),))
    with catalog.write() as connection:
        scan_id = int(connection.execute(
            "INSERT INTO scan_runs(source_id, started_at, finished_at, state) VALUES(?,?,?,'SUCCEEDED')",
            (source_id, 1, 2),
        ).lastrowid)
        connection.execute(
            "INSERT INTO retention_source_anomalies(source_id,scan_id,baseline_present_assets,newly_missing_assets,state,created_at_ms) "
            "VALUES(?,?,12,10,'NEEDS_REVIEW',2)",
            (source_id, scan_id),
        )
        connection.execute(
            "INSERT INTO asset_retention_state(asset_id,state,updated_at_ms) VALUES(903,'BLOCKED',2)"
        )

    async def scenario(client: AsyncClient):
        csrf = await api_env.authenticate(client)
        return await client.post(
            f"/api/v1/storage/library-retention/anomalies/{source_id}/{scan_id}/resolve",
            json={}, headers=api_env.post_headers(csrf),
        )

    response = api_env.scenario(scenario)
    assert response.status_code == 200
    assert response.json() == {
        "source_id": source_id,
        "scan_id": scan_id,
        "resolved": True,
        "requires_new_successful_scan": True,
        "deletes_data": False,
    }
    assert catalog.read_one("SELECT asset_id FROM assets WHERE asset_id=903") is not None


def test_library_retention_preview_reference_query_failure_is_protected(api_env, monkeypatch) -> None:
    from photo_search import library_retention

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source = api_env.register_source("A", source_path)
    _seed_asset(catalog, 1, locations=((int(source["source_id"]), "MISSING"),))

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected reference read error")

    monkeypatch.setattr(library_retention, "_human_or_protected", fail)
    result = library_retention.library_retention_preview(catalog)
    item = result["items"][0]
    assert item["classification"] == "待核实/受保护"
    assert item["reason"] == "引用查询失败，按受保护处理"


def test_library_retention_marks_manual_place_faces_hidden_and_collection_refs(api_env) -> None:
    import hashlib

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source = api_env.register_source("A", source_path)
    source_id = int(source["source_id"])
    _seed_asset(catalog, 1, locations=((source_id, "MISSING"),))
    _seed_asset(catalog, 2, locations=((source_id, "MISSING"),))
    _seed_asset(catalog, 3, locations=((source_id, "MISSING"),))
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO place_operations(operation_id,request_id,payload_sha256,operation_type,operation_revision,actor,atomic,status,catalog_filter_revision,created_at,committed_at) "
            "VALUES('op-retention','req-retention',?,'UNASSIGN',1,'LOCAL_USER',1,'COMMITTED',0,1,1)",
            (hashlib.sha256(b"op-retention").hexdigest(),),
        )
        connection.execute(
            "INSERT INTO asset_place_assignments(asset_id,assignment_id,place_id,mode,origin,manual_lock,resolution_id,revision,record_state,actor,operation_id,created_at,updated_at) "
            "VALUES(1,'assignment-1',NULL,'MANUAL_UNASSIGNED','MANUAL',1,NULL,1,'ACTIVE','LOCAL_USER','op-retention',1,1)"
        )
        locations = {
            int(row["asset_id"]): int(row["location_id"])
            for row in connection.execute("SELECT asset_id,location_id FROM file_locations WHERE asset_id IN (2,3)")
        }
        face_ids = {asset_id: hashlib.sha256(f"retention-face-{asset_id}".encode()).hexdigest() for asset_id in (2, 3)}
        for asset_id, face_id in face_ids.items():
            connection.execute(
                "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,location_id,bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,quality_reason,artifact_id,face_revision,created_at,updated_at) "
                "VALUES(?,?, 'face-v1','det',0,?,?,'[0,0,10,10]','[[1,1],[2,2],[3,3],[4,4],[5,5]]',0.9,90,20,'OK',NULL,'face-artifact',1,1,1)",
                (face_id, asset_id, f"{asset_id:064x}", locations[asset_id]),
            )
        connection.execute("INSERT INTO face_inbox_hidden(face_id) VALUES(?)", (face_ids[3],))
        connection.execute(
            "INSERT INTO persons(name,name_key,revision,created_at,updated_at) VALUES('人像','nfc1:人像',1,1,1)"
        )
        person_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO face_person_links(face_id,person_id,assignment_kind,decision,confidence,revision,created_at,updated_at) "
            "VALUES(?,?,'MANUAL','CONFIRMED',1,1,1,1)",
            (face_ids[2], person_id),
        )
    from photo_search.persons.collections import create_collection
    collection = create_collection(catalog, "家庭")["collection"]
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO person_collection_members(collection_id,person_id) VALUES(?,?)",
            (collection["collection_id"], person_id),
        )

    from photo_search.library_retention import library_retention_preview
    result = library_retention_preview(catalog)
    by_id = {row["asset_id"]: row for row in result["items"]}
    assert by_id[1]["classification"] == "包含人工信息或受保护引用"
    assert by_id[1]["reason"] == "人工地点归属"
    assert by_id[2]["classification"] == "包含人工信息或受保护引用"
    assert by_id[2]["has_human_or_protected_references"] is True
    assert by_id[3]["reason"] == "忽略的人脸记录"


def test_library_retention_protects_index_outbox_and_offline_sources(api_env) -> None:
    import hashlib

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "source-a"
    source_path.mkdir()
    source = api_env.register_source("A", source_path)
    source_id = int(source["source_id"])
    _seed_asset(catalog, 1, locations=((source_id, "MISSING"),))
    _seed_asset(catalog, 2, locations=((source_id, "MISSING"),))
    with catalog.write() as connection:
        connection.execute("DELETE FROM search_projection_queue WHERE asset_id IN (1,2)")
        connection.execute("UPDATE source_roots SET status='OFFLINE' WHERE source_id=?", (source_id,))
        connection.execute(
            "INSERT INTO jobs(subject_type,subject_id,input_revision,task_type,processor_id,state,run_after,created_at,updated_at) "
            "VALUES('asset','1','r1','semantic','test-v1','SUCCEEDED',1,1,1)"
        )
        job_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO index_generations(generation_id,space_id,processor_id,dimension,state,is_active,created_at,updated_at) "
            "VALUES('gen-1','space-1','test-v1',2,'PENDING',0,1,1)"
        )
        digest = hashlib.sha256(b"retention-artifact").hexdigest()
        connection.execute(
            "INSERT INTO artifacts(artifact_id,processor_id,input_revision,artifact_type,artifact_revision,media_type,checksum_sha256,byte_size,storage_uri,space_id,dimension,job_id,lifecycle_state,created_at,updated_at) "
            "VALUES('art-1','test-v1','r1','embedding','a1','application/octet-stream',?,8,'artifacts/pending/a','space-1',2,?,'PENDING',1,1)",
            (digest, job_id),
        )
        connection.execute(
            "INSERT INTO index_outbox(event_id,event_type,artifact_id,space_id,asset_id,generation_id,projection_revision,payload_json,payload_checksum,state,attempts,created_at,updated_at) "
            "VALUES('evt-1','UPSERT_VECTOR','art-1','space-1',1,'gen-1',1,'{}',?,'PENDING',0,1,1)",
            (digest,),
        )

    from photo_search.library_retention import library_retention_preview
    result = library_retention_preview(catalog)
    by_id = {row["asset_id"]: row for row in result["items"]}
    assert by_id[1]["classification"] == "包含人工信息或受保护引用"
    assert by_id[1]["has_human_or_protected_references"] is True
    assert by_id[2]["classification"] == "全局无可用位置但证据不足"
    assert "离线" in by_id[2]["reason"]


@pytest.mark.parametrize("with_location", [False, True])
def test_archived_face_soft_anchor_remains_protected_after_face_and_content_change(
    api_env, with_location: bool,
) -> None:
    from photo_search.library_retention import library_retention_preview
    from photo_search.persons.collections import create_collection

    catalog = api_env.service.catalog
    locations = ()
    if with_location:
        source_path = api_env.tmp_path / "source-archive"
        source_path.mkdir()
        source = api_env.register_source("Archived", source_path)
        locations = ((int(source["source_id"]), "MISSING"),)
    for asset_id in (1, 2):
        _seed_asset(catalog, asset_id, locations=locations)
    collection = create_collection(catalog, "Archived faces")["collection"]
    with catalog.write() as connection:
        connection.execute("DELETE FROM search_projection_queue WHERE asset_id IN (1,2)")
        # No face row remains, and the archive's content SHA differs from the
        # current asset. Neither condition may erase this manual reference.
        connection.execute(
            "INSERT INTO person_collection_face_members"
            "(collection_id,source_face_id,asset_id,input_revision,created_at) VALUES(?,?,?,?,1)",
            (collection["collection_id"], "f" * 64, 1, "a" * 64),
        )
    before = catalog.connection.total_changes
    result = library_retention_preview(catalog)
    by_id = {row["asset_id"]: row for row in result["items"]}
    assert result["unknown_reference_schema"] is False
    assert by_id[1]["has_human_or_protected_references"] is True
    assert by_id[2]["has_human_or_protected_references"] is False
    if with_location:
        assert by_id[1]["classification"] == "包含人工信息或受保护引用"
        assert by_id[1]["reason"] == "人脸合集收录"
    assert "不具备清理资格" in by_id[1]["retention_eligibility"]
    assert catalog.connection.total_changes == before


def test_retention_barrier_is_durable_and_blocks_asset_jobs_and_projection(api_env) -> None:
    from photo_search.catalog.errors import ASSET_RETENTION_BLOCKED, LEASE_CONFLICT, CatalogError
    from photo_search.jobs import queue as jobs
    from photo_search.retention import activate_asset_retention_barrier
    from photo_search.search.documents import sync_document

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "barrier-source"
    source_path.mkdir()
    source_id = int(api_env.register_source("barrier", source_path)["source_id"])
    _seed_asset(catalog, 904, locations=((source_id, "MISSING"),))
    with catalog.write() as connection:
        job_id = jobs.enqueue(
            connection,
            subject_type="asset",
            subject_id="904",
            input_revision="sha-904",
            task_type="embedding",
            processor_id="test-embed@1",
            now_ms=10,
        )
        location_id = int(connection.execute(
            "SELECT location_id FROM file_locations WHERE asset_id=904"
        ).fetchone()[0])
        location_job_id = jobs.enqueue(
            connection,
            subject_type="location",
            subject_id=str(location_id),
            input_revision="loc-904",
            task_type="ocr",
            processor_id="test-ocr@1",
            now_ms=10,
        )
        face_id = hashlib.sha256(b"retention-face-904").hexdigest()
        connection.execute(
            "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,"
            "location_id,bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,"
            "quality_reason,artifact_id,face_revision,created_at,updated_at) "
            "VALUES(?,904,'retention-test-v1','retention-detector',0,?,?,'{}','{}',0.9,100,10,'OK',NULL,?,1,1,1)",
            (face_id, "a" * 64, location_id, "retention-face-artifact"),
        )
        face_job_id = jobs.enqueue(
            connection,
            subject_type="face",
            subject_id=face_id,
            input_revision="face-904",
            task_type="face_embedding",
            processor_id="test-face@1",
            now_ms=10,
        )
        connection.execute(
            "INSERT INTO search_projection_queue(asset_id,cause,queued_at) "
            "VALUES(904,'OCR_TEXT_CHANGED',11) ON CONFLICT(asset_id) DO UPDATE "
            "SET cause=excluded.cause,queued_at=excluded.queued_at"
        )
        running = jobs.claim_job(
            connection,
            job_id=job_id,
            worker_id="retention-test",
            now_ms=11,
            lease_seconds=60,
        )

    with catalog.write() as connection:
        barrier_id = activate_asset_retention_barrier(connection, asset_id=904, now_ms=12)
        cancelled = jobs.get_job(connection, job_id)
        assert jobs.get_job(connection, location_job_id).state == jobs.STATE_CANCELLED
        assert jobs.get_job(connection, face_job_id).state == jobs.STATE_CANCELLED
    assert cancelled.state == jobs.STATE_CANCELLED
    assert cancelled.lease_token is None

    with catalog.write() as connection:
        with pytest.raises(CatalogError) as blocked_enqueue:
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id="904",
                input_revision="sha-904-v2",
                task_type="ocr",
                processor_id="test-ocr@1",
                now_ms=13,
            )
        assert blocked_enqueue.value.code == ASSET_RETENTION_BLOCKED
        for kind, subject, task in (("location", str(location_id), "ocr"), ("face", face_id, "face_embedding")):
            with pytest.raises(CatalogError) as blocked_child_enqueue:
                jobs.enqueue(
                    connection,
                    subject_type=kind,
                    subject_id=subject,
                    input_revision=f"{kind}-904-after",
                    task_type=task,
                    processor_id="test-child@1",
                    now_ms=13,
                )
            assert blocked_child_enqueue.value.code == ASSET_RETENTION_BLOCKED
        opaque_ids = {
            jobs.enqueue(
                connection,
                subject_type="asset",
                subject_id=subject_id,
                input_revision=f"opaque-{subject_id}",
                task_type="maintenance",
                processor_id="test-opaque@1",
                now_ms=13,
            )
            for subject_id in ("904abc", "00904")
        }
        assert opaque_ids <= {
            int(row["job_id"])
            for row in jobs.claimable_candidates(
                connection,
                now_ms=14,
                processor_ids=None,
                aging_window_ms=60_000,
                limit=10,
                task_types=("maintenance",),
            )
        }
        with pytest.raises(CatalogError) as blocked_claim:
            jobs.claim_job(
                connection,
                job_id=job_id,
                worker_id="stale-worker",
                now_ms=13,
                lease_seconds=60,
            )
        assert blocked_claim.value.code == ASSET_RETENTION_BLOCKED
        for child_job_id in (location_job_id, face_job_id):
            with pytest.raises(CatalogError) as blocked_child_claim:
                jobs.claim_job(
                    connection,
                    job_id=child_job_id,
                    worker_id="stale-child-worker",
                    now_ms=13,
                    lease_seconds=60,
                )
            assert blocked_child_claim.value.code == ASSET_RETENTION_BLOCKED
        with pytest.raises(CatalogError) as stale_submission:
            jobs.validate_submission(
                connection,
                job_id=job_id,
                lease_token=running.lease_token or "",
                now_ms=13,
                input_revision="sha-904",
                processor_id="test-embed@1",
            )
        assert stale_submission.value.code == LEASE_CONFLICT
        assert sync_document(connection, 904, now_ms=14) == "blocked"

    with catalog.read_snapshot() as connection:
        stored = connection.execute(
            "SELECT barrier_id,state FROM asset_retention_barriers WHERE asset_id=904"
        ).fetchone()
        assert stored["barrier_id"] == barrier_id
        assert stored["state"] == "ACTIVE"
        assert connection.execute(
            "SELECT COUNT(*) FROM search_documents WHERE asset_id=904"
        ).fetchone()[0] == 0
        from photo_search.search.documents import catch_up, projection_summary, queue_depth

        assert queue_depth(connection) == 0
        assert projection_summary(connection)["queued_assets"] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM search_projection_queue WHERE asset_id=904"
        ).fetchone()[0] == 1

    projection = catch_up(catalog)
    assert projection["state"] == "SYNCED"
    assert projection["remaining"] == 0
    with catalog.write() as connection:
        # Even a stale direct requeue cannot make an asset job claimable after restart.
        connection.execute(
            "UPDATE jobs SET state='QUEUED',run_after=0,lease_token=NULL,lease_expires_at=NULL,"
            "worker_id=NULL WHERE job_id=?",
            (job_id,),
        )
        assert jobs.claim_next(
            connection,
            worker_id="restart-worker",
            now_ms=20,
            lease_seconds=60,
            task_type="embedding",
        ) is None
        assert jobs.claimable_candidates(
            connection,
            now_ms=20,
            processor_ids=None,
            aging_window_ms=1,
            limit=10,
            task_types=("embedding",),
        ) == []
        connection.execute(
            "UPDATE jobs SET state='CANCELLED',updated_at=20 WHERE job_id=?", (job_id,)
        )


def test_retention_barrier_supports_bounded_synthetic_bulk_and_primary_key_lookups(api_env) -> None:
    from photo_search.retention import activate_asset_retention_barrier

    catalog = api_env.service.catalog
    with catalog.write() as connection:
        connection.executemany(
            "INSERT INTO assets(asset_id,sha256,byte_size,created_at,state) "
            "VALUES(?,?,1,1,'ACTIVE')",
            [
                (asset_id, f"{asset_id:064x}")
                for asset_id in range(20_000, 21_000)
            ],
        )
        for asset_id in range(20_000, 21_000):
            activate_asset_retention_barrier(
                connection, asset_id=asset_id, now_ms=asset_id
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM asset_retention_barriers "
            "WHERE asset_id BETWEEN 20000 AND 20999"
        ).fetchone()[0] == 1_000
        plan = " ".join(
            str(row["detail"])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT barrier_id FROM asset_retention_barriers "
                "WHERE asset_id=?",
                (20_500,),
            )
        ).upper()
        assert "INTEGER PRIMARY KEY" in plan or "PRIMARY KEY" in plan


def test_retention_cleanup_waits_for_vectors_then_removes_machine_rows(api_env) -> None:
    from photo_search.library_retention import (
        begin_asset_retention_cleanup,
        finalize_asset_retention_cleanup,
    )

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "cleanup-source"
    source_path.mkdir()
    source_id = int(api_env.register_source("cleanup", source_path)["source_id"])
    _seed_retention_eligible(catalog, 905, source_id, now_ms=100_000_000_000)
    face_id = hashlib.sha256(b"retention-cleanup-face-905").hexdigest()
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_metadata(asset_id,time_source,time_confidence) "
            "VALUES(905,'FILE_MTIME','LOW')"
        )
        location_id = int(connection.execute(
            "SELECT location_id FROM file_locations WHERE asset_id=905"
        ).fetchone()[0])
        connection.execute(
            "INSERT INTO faces(face_id,asset_id,spec_revision,detector_id,ordinal,input_revision,"
            "location_id,bbox_json,landmarks_json,det_score,face_pixels,blur_variance,quality_state,"
            "quality_reason,artifact_id,face_revision,created_at,updated_at) "
            "VALUES(?,905,'retention-test-v1','retention-detector',0,?,?,'[10,10,40,40]','{}',0.9,100,10,'OK',NULL,?,1,1,1)",
            (face_id, "a" * 64, location_id, "retention-face-artifact-905"),
        )
        connection.execute(
            "INSERT INTO face_embeddings(face_id,processor_id,space_id,dimension,metric,normalization,"
            "vector_blob,vector_sha256,created_at,updated_at) VALUES(?, 'face-test@1','face:test',1,"
            "'cosine','L2',?,?,1,1)",
            (face_id, b"\x00\x00\x80?", hashlib.sha256(b"\x00\x00\x80?").hexdigest()),
        )
        connection.execute(
            "INSERT INTO face_index_generations(generation_id,space_id,processor_id,spec_revision,"
            "dimension,metric,normalization,state,is_active,created_at,updated_at) "
            "VALUES('retention-face-gen','face:test','face-test@1','retention-test-v1',1,"
            "'cosine','L2','READY',0,1,1)"
        )
        connection.execute(
            "INSERT INTO face_candidates(generation_id,face_id,is_representative,quality_state,"
            "vector_blob,vector_sha256,published_at) VALUES('retention-face-gen',?,0,'OK',?,?,1)",
            (face_id, b"\x00\x00\x80?", hashlib.sha256(b"\x00\x00\x80?").hexdigest()),
        )
        connection.execute(
            "INSERT INTO face_index_entries(generation_id,face_id,artifact_id,confirmation_revision,"
            "confirmed_at,state) VALUES('retention-face-gen',?,'retention-face-artifact-905',1,1,'CONFIRMED')",
            (face_id,),
        )
        connection.execute(
            "INSERT INTO face_index_outbox(event_id,event_type,face_id,space_id,generation_id,"
            "projection_revision,payload_json,payload_checksum,state,attempts,created_at,updated_at) "
            "VALUES('retention-face-event-905','UPSERT_FACE',?,'face:test','retention-face-gen',1,"
            "'{}',?, 'PENDING',0,1,1)",
            (face_id, hashlib.sha256(b"{}").hexdigest()),
        )

    started = begin_asset_retention_cleanup(catalog, asset_id=905, now_ms=100_000_000_001)
    assert started["state"] == "READY_TO_FINALIZE"
    completed = finalize_asset_retention_cleanup(catalog, asset_id=905, now_ms=100_000_000_002)
    assert completed["state"] == "COMPLETE"
    with catalog.read_snapshot() as connection:
        assert connection.execute("SELECT 1 FROM assets WHERE asset_id=905").fetchone() is None
        assert connection.execute("SELECT 1 FROM file_locations WHERE asset_id=905").fetchone() is None
        assert connection.execute("SELECT 1 FROM asset_metadata WHERE asset_id=905").fetchone() is None
        assert connection.execute("SELECT 1 FROM faces WHERE asset_id=905").fetchone() is None
        assert connection.execute("SELECT 1 FROM face_embeddings WHERE face_id=?", (face_id,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM face_candidates WHERE face_id=?", (face_id,)).fetchone() is None
        assert connection.execute("SELECT 1 FROM face_index_outbox WHERE face_id=?", (face_id,)).fetchone() is None
        barrier = connection.execute(
            "SELECT state,media_sha256,face_crop_keys_json FROM asset_retention_barriers WHERE asset_id=905"
        ).fetchone()
        assert barrier[0] == "COMPLETE"
        assert barrier[1] == f"{905:064x}"
        from photo_search.media.face_crop import crop_key, parse_bbox
        assert json.loads(barrier[2]) == [
            crop_key(face_id=face_id, box=parse_bbox("[10,10,40,40]"), sha256=barrier[1])
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_retention_cleanup_allows_explicit_early_confirmation_but_keeps_human_data(api_env) -> None:
    from photo_search.catalog.errors import ASSET_RETENTION_BLOCKED, CatalogError
    from photo_search.library_retention import begin_asset_retention_cleanup

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "early-cleanup-source"
    source_path.mkdir()
    source_id = int(api_env.register_source("early-cleanup", source_path)["source_id"])
    early_now = 110_000_000_000
    _seed_retention_eligible(catalog, 906, source_id, now_ms=early_now)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE asset_retention_state SET first_missing_at_ms=?,state='OBSERVING' "
            "WHERE asset_id=906",
            (early_now - 24 * 60 * 60 * 1000,),
        )
    started = begin_asset_retention_cleanup(
        catalog, asset_id=906, now_ms=early_now + 1, manual_early=True
    )
    assert started["state"] == "READY_TO_FINALIZE"

    _seed_retention_eligible(catalog, 907, source_id, now_ms=120_000_000_000)
    with catalog.write() as connection:
        connection.execute(
            "INSERT INTO asset_favorites(asset_id,added_at_ms) VALUES(907,1)"
        )
    with pytest.raises(CatalogError) as blocked:
        begin_asset_retention_cleanup(catalog, asset_id=907, now_ms=120_000_000_001)
    assert blocked.value.code == ASSET_RETENTION_BLOCKED
    assert catalog.read_one(
        "SELECT COUNT(*) FROM asset_retention_barriers WHERE asset_id=907"
    )[0] == 0
    assert catalog.read_one("SELECT COUNT(*) FROM asset_favorites WHERE asset_id=907")[0] == 1


def test_retention_cleanup_rejects_evidence_from_another_source_scan(api_env) -> None:
    from photo_search.catalog.errors import ASSET_RETENTION_BLOCKED, CatalogError
    from photo_search.library_retention import begin_asset_retention_cleanup

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "evidence-source"
    other_path = api_env.tmp_path / "evidence-other-source"
    source_path.mkdir()
    other_path.mkdir()
    source_id = int(api_env.register_source("evidence-source", source_path)["source_id"])
    other_source_id = int(api_env.register_source("evidence-other-source", other_path)["source_id"])
    _seed_retention_eligible(catalog, 910, source_id, now_ms=150_000_000_000)
    with catalog.write() as connection:
        other_scan = int(connection.execute(
            "INSERT INTO scan_runs(source_id,started_at,finished_at,state,error) "
            "VALUES(?,?,?,'SUCCEEDED',NULL)",
            (other_source_id, 1, 2),
        ).lastrowid)
        connection.execute(
            "UPDATE asset_retention_source_evidence SET scan_id=? WHERE asset_id=910 AND source_id=?",
            (other_scan, source_id),
        )

    with pytest.raises(CatalogError) as blocked:
        begin_asset_retention_cleanup(catalog, asset_id=910, now_ms=150_000_000_001)
    assert blocked.value.code == ASSET_RETENTION_BLOCKED
    assert "来源缺少匹配的成功失效复核证据" in blocked.value.details["blockers"]
    assert catalog.read_one(
        "SELECT COUNT(*) FROM asset_retention_barriers WHERE asset_id=910"
    )[0] == 0


def test_retention_cleanup_cancels_if_a_copy_is_restored_before_finalize(api_env) -> None:
    from photo_search.library_retention import (
        begin_asset_retention_cleanup,
        finalize_asset_retention_cleanup,
    )

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "restore-before-finalize-source"
    source_path.mkdir()
    source_id = int(api_env.register_source("restore-before-finalize", source_path)["source_id"])
    _seed_retention_eligible(catalog, 908, source_id, now_ms=130_000_000_000)
    begin_asset_retention_cleanup(catalog, asset_id=908, now_ms=130_000_000_001)
    with catalog.write() as connection:
        connection.execute(
            "UPDATE file_locations SET availability='PRESENT' WHERE asset_id=908"
        )
    result = finalize_asset_retention_cleanup(catalog, asset_id=908, now_ms=130_000_000_002)
    assert result["state"] == "CANCELLED_RESTORED"
    assert catalog.read_one("SELECT 1 FROM assets WHERE asset_id=908") is not None
    assert catalog.read_one(
        "SELECT 1 FROM asset_retention_barriers WHERE asset_id=908"
    ) is None


def test_retention_cleanup_database_failure_rolls_back_and_retries(api_env) -> None:
    from photo_search.library_retention import (
        begin_asset_retention_cleanup,
        finalize_asset_retention_cleanup,
    )

    catalog = api_env.service.catalog
    source_path = api_env.tmp_path / "cleanup-retry-source"
    source_path.mkdir()
    source_id = int(api_env.register_source("cleanup-retry", source_path)["source_id"])
    _seed_retention_eligible(catalog, 909, source_id, now_ms=140_000_000_000)
    begin_asset_retention_cleanup(catalog, asset_id=909, now_ms=140_000_000_001)
    with catalog.write() as connection:
        connection.execute(
            "CREATE TEMP TRIGGER reject_asset_cleanup BEFORE DELETE ON assets "
            "WHEN OLD.asset_id=909 BEGIN SELECT RAISE(ABORT,'injected cleanup failure'); END"
        )
    with pytest.raises(Exception, match="injected cleanup failure"):
        finalize_asset_retention_cleanup(catalog, asset_id=909, now_ms=140_000_000_002)
    assert catalog.read_one("SELECT 1 FROM assets WHERE asset_id=909") is not None
    assert catalog.read_one(
        "SELECT state FROM asset_retention_barriers WHERE asset_id=909"
    )[0] == "ACTIVE"
    with catalog.write() as connection:
        connection.execute("DROP TRIGGER reject_asset_cleanup")
    assert finalize_asset_retention_cleanup(catalog, asset_id=909, now_ms=140_000_000_003)["state"] == "COMPLETE"
