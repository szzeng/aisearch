"""Regressions for search concurrency, image identity and bounded selection."""
from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from photo_search.api.context import ApiContext
from photo_search.api.settings import ApiSettings
from photo_search.catalog.errors import CatalogError
from photo_search.catalog.selection import available_assets
from photo_search.indexing.semantic import semantic_selection
from photo_search.media.decode import load_normalized
from photo_search.models.embedding.manifest import DEFAULT_MANIFEST, pinned_embedding_spec
from photo_search.search.retrieval import text_column_matches
from photo_search.search.upload import DEFAULT_LIMITS, _decode_upload
from photo_search.workers.client import AiWorkerClient
from photo_search.workers.query_encoder import WorkerQueryEncoder


@pytest.mark.parametrize("orientation", range(2, 9))
def test_library_and_upload_decode_the_same_upright_pixels(tmp_path, orientation):
    photo = Image.new("RGB", (300, 200), "white")
    photo.paste("red", (0, 0, 80, 100))
    exif = Image.Exif()
    exif[274] = orientation
    path = tmp_path / "rotated.jpg"
    photo.save(path, exif=exif)
    library = load_normalized(path, long_edge=DEFAULT_LIMITS.decode_long_edge)
    _, uploaded = _decode_upload(path, path.read_bytes(), limits=DEFAULT_LIMITS)
    try:
        assert uploaded.size == library.image.size
        assert uploaded.tobytes() == library.image.tobytes()
    finally:
        uploaded.close()
        library.image.close()


def test_preprocessing_change_has_a_new_embedding_identity(tmp_path):
    original = pinned_embedding_spec(DEFAULT_MANIFEST)
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    manifest["preprocessing"]["max_num_patches"] += 1
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    changed = pinned_embedding_spec(path)
    assert original.processor_id != changed.processor_id
    assert original.envelope["space_id"] != changed.envelope["space_id"]


def test_failed_encoder_initialization_can_recover(tmp_path):
    calls = []
    encoder = object()
    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        return encoder
    context = ApiContext(ApiSettings(data_root=tmp_path), encoder_factory=factory)
    try:
        assert context.query_encoder() is None
        assert context.query_encoder() is encoder
        assert context.query_encoder() is encoder
        assert len(calls) == 2
    finally:
        context.close()


def test_restarted_worker_cannot_return_vectors_from_another_space():
    spec = pinned_embedding_spec(DEFAULT_MANIFEST)
    changed = {**spec.to_envelope(), "space_id": "other-space"}
    client = SimpleNamespace(
        encode_text=lambda _: {"spec": changed, "vector": [0.0] * 768},
        health=lambda **_: {"spec": changed, "loaded": True},
    )
    encoder = WorkerQueryEncoder(client, spec)
    for operation in (encoder.health, lambda: encoder.encode_queries(["猫"])):
        with pytest.raises(CatalogError) as caught:
            operation()
        assert caught.value.code == "SPACE_MISMATCH"


def test_search_leaves_event_loop_live_and_owns_its_connection(tmp_path):
    owner = threading.get_ident()
    events = []
    def factory():
        events.append(("open", threading.get_ident()))
        return SimpleNamespace(close=lambda: events.append(("close", threading.get_ident())))
    context = ApiContext(ApiSettings(data_root=tmp_path), service_factory=factory)
    started, release = threading.Event(), threading.Event()
    def search():
        context.service()
        started.set()
        assert release.wait(2)
        return 42
    async def scenario():
        task = asyncio.create_task(context.run_search(search))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set()
        # Only a responsive loop can release the blocked search.
        release.set()
        assert await task == 42
    try:
        asyncio.run(scenario())
        assert events[0][1] == events[1][1] != owner
    finally:
        release.set()
        context.close()


def test_worker_health_and_encoding_share_one_exchange_lock(tmp_path):
    client = AiWorkerClient(tmp_path, tmp_path / "manifest.json")
    client._close_queues()
    client._requests, client._responses = queue.Queue(), queue.Queue()
    client.start = lambda: None
    client._process = SimpleNamespace(is_alive=lambda: True)
    values, errors = {}, []
    def call(name, method):
        try:
            values[name] = method(timeout=2)
        except Exception as error:
            errors.append(error)
    health = threading.Thread(target=call, args=("health", client.health))
    query = threading.Thread(target=call, args=("query", lambda **kw: client.encode_text("猫", **kw)))
    health.start()
    assert client._requests.get(timeout=1)["action"] == "health"
    query.start()
    assert client._lock.acquire(blocking=False) is False
    client._responses.put({"status": "ok", "result": {"loaded": True}})
    assert client._requests.get(timeout=1)["action"] == "encode_text"
    client._responses.put({"status": "ok", "result": {"vector": [1.0]}})
    health.join(2)
    query.join(2)
    client._cleanup_stderr_file()
    assert not errors
    assert values == {"health": {"loaded": True}, "query": {"vector": [1.0]}}


def test_source_duplicates_and_sql_page_bounds(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, sha256 TEXT);
        CREATE TABLE file_locations(location_id INTEGER PRIMARY KEY, asset_id INTEGER,
            source_id INTEGER, relative_path TEXT, availability TEXT);
        CREATE INDEX location_asset ON file_locations(asset_id);
        CREATE TABLE source_roots(source_id INTEGER PRIMARY KEY, root_path TEXT);
        CREATE TABLE index_entries(asset_id INTEGER, space_id TEXT, generation_id TEXT,
            state TEXT, artifact_id TEXT);
        CREATE TABLE artifacts(artifact_id TEXT, input_revision TEXT);
        INSERT INTO source_roots VALUES(1, '/a'),(2, '/b');
    """)
    db.executemany("INSERT INTO assets VALUES (?, 'sha')", ((n,) for n in range(1, 1001)))
    db.executemany("INSERT INTO file_locations VALUES(?,?,1,'x.jpg','PRESENT')",
                   ((n, n) for n in range(1, 1001)))
    db.execute("INSERT INTO file_locations VALUES(1001,1,2,'copy.jpg','PRESENT')")
    reads = []
    def read_all(sql, args):
        rows = db.execute(sql, args).fetchall()
        reads.append(len(rows))
        return rows
    catalog = SimpleNamespace(read_all=read_all)
    monkeypatch.setattr("photo_search.indexing.semantic.image_eligibility", lambda _: (True, "JPEG"))
    selected = semantic_selection(catalog, space_id="s", processor_id="p", source_id=2, limit=10)
    assert selected["targets"] == [{"asset_id": 1, "input_revision": "sha"}]
    reads.clear()
    selected = semantic_selection(catalog, space_id="s", processor_id="p", limit=10)
    assert len(selected["targets"]) == 10 and not selected["coverage_complete"]
    assert sum(reads) == 11
    reads.clear()
    assert available_assets(catalog, asset_ids=[999]) == [{"asset_id": 999, "input_revision": "sha"}]
    assert reads == [1]
    db.close()


def test_ocr_only_match_has_an_explanation():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE VIRTUAL TABLE search_documents_fts USING fts5(filename_text,place_text,ocr_text);
        INSERT INTO search_documents_fts(rowid,ocr_text) VALUES(1,'ORDER123');
    """)
    catalog = SimpleNamespace(read_all=lambda sql, args: db.execute(sql, args).fetchall())
    assert text_column_matches(catalog, SimpleNamespace(expression='"ORDER123"'), [1]) == {
        1: ["ocr_text"]
    }
    db.close()
