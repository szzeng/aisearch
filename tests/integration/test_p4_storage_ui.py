"""P4-STORAGE-UI-A: local API security and user-controlled storage actions."""

from __future__ import annotations

import threading

from httpx import AsyncClient


def test_manual_backup_is_only_created_by_csrf_protected_button_action(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        no_session = await client.post("/api/v1/storage/backup", json={})
        csrf = await api_env.authenticate(client)
        no_csrf = await client.post("/api/v1/storage/backup", json={}, headers={"Origin": "http://127.0.0.1"})
        settings = await client.get("/api/v1/storage/backup")
        before = list(api_env.tmp_path.rglob("catalog-*.db"))
        backup = await client.post("/api/v1/storage/backup", json={}, headers=api_env.post_headers(csrf))
        after = list(api_env.tmp_path.rglob("catalog-*.db"))
        return {"no_session": no_session, "no_csrf": no_csrf, "settings": settings, "before": before, "backup": backup, "after": after}

    result = api_env.scenario(scenario)
    assert result["no_session"].status_code == 401
    assert result["no_csrf"].status_code == 403
    assert result["settings"].status_code == 200
    assert result["before"] == []
    assert result["backup"].json()["status"] == "ok"
    assert result["before"] == []
    assert len(result["after"]) == 1  # the file appears only after the explicit POST
    assert not (api_env.data_root / "backups").exists()


def test_unreviewed_maintenance_endpoint_never_queues_an_operation(api_env, monkeypatch) -> None:
    monkeypatch.setattr("photo_search.storage_ui.MAINTENANCE_REVIEWED", False)
    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        bad = await client.post(
            "/api/v1/storage/maintenance/request",
            json={"operation": "arbitrary-path-delete", "confirmed": True},
            headers=api_env.post_headers(csrf),
        )
        queued = await client.post("/api/v1/storage/maintenance/request", json={"operations": ["clean:previews", "vacuum"], "confirmed": True}, headers=api_env.post_headers(csrf))
        preview = await client.get("/api/v1/storage/maintenance/preview")
        return {"bad": bad.json(), "queued": queued.json(), "preview": preview.json()}

    result = api_env.scenario(scenario)
    assert result["bad"]["status"] == "error"
    assert result["queued"]["code"] == "INDEPENDENT_REVIEW_REQUIRED"
    assert result["preview"]["execution_enabled"] is False
    assert result["preview"]["pending"] is None


def test_reviewed_maintenance_api_batch_reaches_startup_consumer(api_env, monkeypatch) -> None:
    from photo_search.storage_ui import execute_pending_maintenance

    calls: list[str] = []

    def run(_data_root, operation):
        calls.append(operation)
        return {"status": "ok", "deleted_files": 1, "freed_bytes": 128}

    monkeypatch.setattr("photo_search.storage_ui._run_maintenance", run)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        preview = await client.get("/api/v1/storage/maintenance/preview")
        queued = await client.post(
            "/api/v1/storage/maintenance/request",
            json={"operations": ["vacuum", "clean:artifacts"], "confirmed": True},
            headers=api_env.post_headers(csrf),
        )
        pending = await client.get("/api/v1/storage/maintenance/preview")
        return {"preview": preview.json(), "queued": queued.json(), "pending": pending.json()}

    result = api_env.scenario(scenario)
    assert result["preview"]["execution_enabled"] is True
    assert result["queued"]["operations"] == ["clean:artifacts", "vacuum"]
    assert result["pending"]["pending"]["request_id"] == result["queued"]["request_id"]

    startup_result = execute_pending_maintenance(api_env.data_root)
    assert startup_result and startup_result["status"] == "ok"
    assert calls == ["clean:artifacts", "vacuum"]
    assert [item["deleted_files"] for item in startup_result["items"]] == [1, 1]
    assert execute_pending_maintenance(api_env.data_root) is None


def test_storage_panel_exposes_separate_read_check_backup_and_maintenance_controls(api_env) -> None:
    async def scenario(client: AsyncClient) -> dict:
        shell = await client.get("/")
        script = await client.get("/web/app.js")
        summary = await client.get("/api/v1/storage/summary")
        return {"shell": shell, "script": script, "summary": summary}

    result = api_env.scenario(scenario)
    shell = result["shell"].text
    script = result["script"].text
    assert "存储与维护" in shell
    assert "storage-deep-check" in shell
    assert "storage-backup-create" in shell
    assert "storage-maintenance-confirm" in shell
    assert '/web/app.js?v=p4-storage-ui-a-2' in shell
    assert shell.count('type="checkbox" name="storage-maintenance-operation"') == 6
    assert 'id="storage-maintenance-confirm" disabled' in shell
    assert 'body: { operations: operations, confirmed: true }' in script
    assert 'button.dataset.requestPending === "true"' in script
    assert 'function storageMaintenanceItemDetails(item)' in script
    assert 'item.deleted_files !== undefined' in script
    assert 'item.freed_bytes !== undefined' in script
    assert 'item.actual_bytes_freed !== undefined' in script
    assert 'item.actual_reclaimed_bytes !== undefined' in script
    assert '阶段实际释放 ' in script
    assert 'request("/storage/inspect", { method: "POST"' in script
    assert 'request("/storage/backup", { method: "POST"' in script
    assert result["summary"].status_code == 401


def test_manual_deep_inspection_runs_off_the_http_event_loop(api_env, monkeypatch) -> None:
    import photo_search.storage as storage

    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def inspect(_data_root, *, deep=False):
        worker_threads.append(threading.get_ident())
        assert deep is True
        return {"status": "ok", "completed_at_ms": 1, "directories": {}, "partial": False}

    monkeypatch.setattr(storage, "storage_inspect", inspect)

    async def scenario(client: AsyncClient) -> dict:
        csrf = await api_env.authenticate(client)
        response = await client.post("/api/v1/storage/inspect", json={}, headers=api_env.post_headers(csrf))
        return response.json()

    result = api_env.scenario(scenario)
    assert result["status"] == "ok"
    assert worker_threads and worker_threads[0] != event_loop_thread
