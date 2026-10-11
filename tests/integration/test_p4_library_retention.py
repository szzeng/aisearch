from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
            "const els = { libraryRetentionStatus: { textContent: '' }, libraryRetentionMore: { hidden: true, disabled: false } };",
            "function text(node, value) { node.textContent = value; }",
            "function formatBytes(value) { return value + ' bytes'; }",
            renderer,
            "renderLibraryRetention();",
            "process.stdout.write(JSON.stringify({ text: els.libraryRetentionStatus.textContent, hidden: els.libraryRetentionMore.hidden }));",
        ]
    )
    result = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, encoding="utf-8")
    rendered = json.loads(result.stdout)
    assert "MISSING 位置 2" in rendered["text"]
    assert "待核实/受保护" in rendered["text"]
    assert "首次失效时间未知" in rendered["text"]
    assert "128 bytes" in rendered["text"]
    assert rendered["hidden"] is False


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
