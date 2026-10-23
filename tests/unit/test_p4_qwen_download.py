from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_search.catalog.errors import CatalogError
from photo_search.models.embedding import base as embedding_base
from photo_search.models.embedding import download

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "qwen3-vl-embedding-2b-exif-v2.json"


@pytest.fixture
def download_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    template = embedding_base.read_manifest(TEMPLATE_PATH)
    payloads = {
        item["name"]: ("small pinned test file: " + item["name"]).encode()
        for item in template["weights"]
    }
    for item in template["weights"]:
        item["bytes"] = len(payloads[item["name"]])
        item["sha256"] = hashlib.sha256(payloads[item["name"]]).hexdigest()
    root = tmp_path / "isolated-data"
    model_dir = root / "models" / "qwen3_vl_embedding"
    shipped_dir = tmp_path / "shipped"
    shipped_dir.mkdir()
    (shipped_dir / f"{download.QWEN_PROVIDER_ID}.json").write_text(
        json.dumps(template), encoding="utf-8"
    )
    monkeypatch.setattr(download, "shipped_manifest_dir", lambda: shipped_dir)

    response_status = {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.rsplit("/", 1)[-1]
            if response_status["code"] != 200:
                self.send_error(response_status["code"])
                return
            body = payloads.get(name, b"")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    calls: list[dict] = []

    def local_snapshot_download(**kwargs):
        calls.append(kwargs)
        snapshot = Path(kwargs["local_dir"])
        snapshot.mkdir(parents=True, exist_ok=True)
        for name in kwargs["allow_patterns"]:
            with urllib.request.urlopen(f"{endpoint}/files/{name}", timeout=2) as response:
                (snapshot / name).write_bytes(response.read())
        return str(snapshot)

    original_install = download.embedding_cli._prepare_local

    def local_install(args):
        source = Path(args.source)
        model_dir.mkdir(parents=True, exist_ok=True)
        for item in template["weights"]:
            (model_dir / item["name"]).write_bytes((source / item["name"]).read_bytes())
        target = root / "models" / "manifests" / f"{download.QWEN_PROVIDER_ID}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(template), encoding="utf-8")
        return {"status": "ok", "model_dir": str(model_dir)}

    monkeypatch.setattr(download.embedding_cli, "_prepare_local", local_install)
    yield SimpleNamespace(
        root=root,
        template=template,
        payloads=payloads,
        response_status=response_status,
        calls=calls,
        snapshot=local_snapshot_download,
        model_dir=model_dir,
    )
    server.shutdown()
    server.server_close()
    server_thread.join(timeout=2)
    download.embedding_cli._prepare_local = original_install


def enough_space(_path):
    return SimpleNamespace(free=100_000_000_000)


def test_qwen_download_uses_fixed_repo_commit_allowlist_and_publishes_verified_snapshot(
    download_fixture,
):
    fixture = download_fixture
    result = download.download_qwen(
        fixture.root,
        confirmed=True,
        snapshot_download_fn=fixture.snapshot,
        disk_usage_fn=enough_space,
    )
    assert result["status"] == "ok"
    assert result["dimensions"] == [512, 1024]
    assert fixture.calls[0]["repo_id"] == "Qwen/Qwen3-VL-Embedding-2B"
    assert fixture.calls[0]["revision"] == fixture.template["model"]["resolved_revision"]
    assert set(fixture.calls[0]["allow_patterns"]) == set(fixture.payloads)
    assert fixture.calls[0]["max_workers"] == 4
    assert not list((fixture.root / "models" / ".download-staging").iterdir())


def test_qwen_download_confirmation_is_required_before_filesystem_or_network(tmp_path):
    with pytest.raises(CatalogError) as error:
        download.download_qwen(tmp_path / "no-write", confirmed=False)
    assert error.value.code == "MODEL_DOWNLOAD_CONFIRMATION_REQUIRED"
    assert not (tmp_path / "no-write").exists()


def test_qwen_download_cancellation_discards_only_its_staging_directory(download_fixture):
    fixture = download_fixture

    def cancelled(**kwargs):
        stage = Path(kwargs["local_dir"])
        stage.mkdir(parents=True)
        (stage / "partial.tmp").write_bytes(b"partial")
        raise KeyboardInterrupt

    with pytest.raises(CatalogError) as error:
        download.download_qwen(
            fixture.root,
            confirmed=True,
            snapshot_download_fn=cancelled,
            disk_usage_fn=enough_space,
        )
    assert error.value.code == "MODEL_DOWNLOAD_CANCELLED"
    assert not fixture.model_dir.exists()
    assert not list((fixture.root / "models" / ".download-staging").iterdir())


def test_qwen_download_network_loss_leaves_provider_uninstalled(download_fixture):
    fixture = download_fixture
    fixture.response_status["code"] = 503
    with pytest.raises(CatalogError) as error:
        download.download_qwen(
            fixture.root,
            confirmed=True,
            snapshot_download_fn=fixture.snapshot,
            disk_usage_fn=enough_space,
        )
    assert error.value.code == "MODEL_DOWNLOAD_FAILED"
    assert not fixture.model_dir.exists()
    assert not (fixture.root / "models" / "manifests" / f"{download.QWEN_PROVIDER_ID}.json").exists()
    assert not list((fixture.root / "models" / ".download-staging").iterdir())


def test_qwen_download_hash_failure_does_not_publish(download_fixture):
    fixture = download_fixture
    first = next(iter(fixture.payloads))
    fixture.payloads[first] += b"tampered"
    with pytest.raises(CatalogError) as error:
        download.download_qwen(
            fixture.root,
            confirmed=True,
            snapshot_download_fn=fixture.snapshot,
            disk_usage_fn=enough_space,
        )
    assert error.value.code == "EMBEDDING_WEIGHTS_HASH_MISMATCH"
    assert not fixture.model_dir.exists()
    assert not list((fixture.root / "models" / ".download-staging").iterdir())


def test_qwen_download_refuses_insufficient_space_before_network(download_fixture):
    fixture = download_fixture
    with pytest.raises(CatalogError) as error:
        download.download_qwen(
            fixture.root,
            confirmed=True,
            snapshot_download_fn=fixture.snapshot,
            disk_usage_fn=lambda _path: SimpleNamespace(free=10),
        )
    assert error.value.code == "MODEL_DOWNLOAD_DISK_SPACE_LOW"
    assert fixture.calls == []
    assert not fixture.model_dir.exists()


def test_repeated_qwen_download_reports_ready_without_a_second_transfer(download_fixture):
    fixture = download_fixture
    download.download_qwen(
        fixture.root, confirmed=True, snapshot_download_fn=fixture.snapshot, disk_usage_fn=enough_space
    )
    result = download.download_qwen(
        fixture.root, confirmed=True, snapshot_download_fn=fixture.snapshot, disk_usage_fn=enough_space
    )
    assert result["status"] == "already_ready"
    assert len(fixture.calls) == 1


def test_data_root_manifest_cannot_redirect_the_official_download(download_fixture):
    fixture = download_fixture
    installed = json.loads(json.dumps(fixture.template))
    installed["model"]["id"] = "attacker/other-model"
    path = fixture.root / "models" / "manifests" / f"{download.QWEN_PROVIDER_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(installed), encoding="utf-8")
    with pytest.raises(CatalogError) as error:
        download.download_qwen(
            fixture.root,
            confirmed=True,
            snapshot_download_fn=fixture.snapshot,
            disk_usage_fn=enough_space,
        )
    assert error.value.code == "EMBEDDING_MANIFEST_INVALID"
    assert fixture.calls == []
    assert not fixture.model_dir.exists()
