from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClien

from photo_search.api.settings import ApiSettings
from photo_search.app import create_app


def test_random_photo_api_is_session_protected_bounded_and_read_only(tmp_path: Path) -> None:
    token = "random-browse-session-token"
    app = create_app(settings=ApiSettings.create(data_root=tmp_path, session_token=token))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bootstrap = client.get("/api/v1/session", params={"token": token})
        assert bootstrap.status_code == 200
        csrf = client.cookies.get("ps_csrf")
        headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf or ""}
        sampled = client.post("/api/v1/photo-random-sample", json={"count": 10}, headers=headers)
        assert sampled.status_code == 200, sampled.tex
        assert sampled.json()["asset_ids"] == []
        assert sampled.json()["candidate_count"] == 0

        summary = client.post("/api/v1/photo-random-summaries", json={"asset_ids": []}, headers=headers)
        assert summary.status_code == 200
        assert summary.json() == {"assets": [], "requested_count": 0, "skipped_count": 0}

        invalid = client.post("/api/v1/photo-random-sample", json={"count": 1001}, headers=headers)
        assert invalid.status_code == 422

    unauthenticated = create_app(settings=ApiSettings.create(data_root=tmp_path / "other"))
    with TestClient(unauthenticated, base_url="http://127.0.0.1") as client:
        refused = client.post("/api/v1/photo-random-sample", json={"count": 10})
        assert refused.status_code == 401
