"""Synthetic accounting tests for build status, independent of models and real photos."""
import sqlite3

from photo_search.artifacts.store import generation_id_for
from photo_search.ocr.contract import OCR_ACTIVE_PROCESSOR_IDS, OCR_TASK_TYPE
from photo_search.refresh.progress import ocr_enrichment_counts, semantic_breakdown
from photo_search.refresh.state import RefreshStatus


class Catalog:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE assets(asset_id INTEGER, sha256 TEXT);
        CREATE TABLE file_locations(asset_id INTEGER, source_id INTEGER, availability TEXT);
        CREATE TABLE jobs(subject_id TEXT, subject_type TEXT, input_revision TEXT,
                          task_type TEXT, processor_id TEXT, state TEXT);
        CREATE TABLE artifacts(artifact_id INTEGER, input_revision TEXT);
        CREATE TABLE index_entries(asset_id INTEGER, artifact_id INTEGER, space_id TEXT,
                                   generation_id TEXT, state TEXT);
        CREATE TABLE asset_ocr_text(asset_id INTEGER, input_revision TEXT);
        """)

    def read_one(self, sql, parameters):
        return self.db.execute(sql, parameters).fetchone()


def test_semantic_counts_unique_current_confirmed_generation_and_present_scope():
    c = Catalog()
    c.db.executemany("INSERT INTO assets VALUES (?, ?)", [(i, f"rev{i}") for i in range(1, 5)])
    c.db.executemany("INSERT INTO file_locations VALUES (?, 1, ?)",
                     [(1, "PRESENT"), (2, "PRESENT"), (3, "PRESENT"), (4, "MISSING")])
    c.db.executemany("INSERT INTO jobs VALUES (?, 'asset', ?, 'image_embedding', 'p', 'SUCCEEDED')",
                     [(str(i), f"rev{i}") for i in range(1, 5)])
    c.db.executemany("INSERT INTO artifacts VALUES (?, ?)", [(1, "rev1"), (2, "old"), (3, "rev2"), (4, "rev4")])
    generation = generation_id_for("s", "p")
    c.db.executemany("INSERT INTO index_entries VALUES (?, ?, 's', ?, 'CONFIRMED')", [
        (1, 1, generation), (1, 1, generation), (2, 2, generation),
        (2, 3, "other"), (4, 4, generation),
    ])
    counts = semantic_breakdown(c, space_id="s", processor_id="p", source_id=1)
    assert counts["total"] == 3
    assert counts["confirmed"] == 1
    assert counts["confirmed"] <= counts["total"]


def test_ocr_precedence_is_disjoint_and_ignores_old_revision():
    c = Catalog()
    c.db.executemany("INSERT INTO assets VALUES (?, ?)", [(i, f"r{i}") for i in range(1, 8)])
    c.db.executemany("INSERT INTO file_locations VALUES (?, 1, 'PRESENT')", [(i,) for i in range(1, 8)])
    processor = OCR_ACTIVE_PROCESSOR_IDS[0]
    rows = [(1, 'RUNNING'), (2, 'RUNNING'), (3, 'QUEUED'), (4, 'RETRY_WAIT'),
            (5, 'BLOCKED'), (6, 'FAILED')]
    c.db.executemany("INSERT INTO jobs VALUES (?, 'asset', ?, ?, ?, ?)",
                     [(str(i), f"r{i}", OCR_TASK_TYPE, processor, state) for i, state in rows])
    c.db.execute("INSERT INTO jobs VALUES ('7', 'asset', 'old', ?, ?, 'RUNNING')", (OCR_TASK_TYPE, processor))
    # Competing active states on the other active processor must still yield one class.
    c.db.execute("INSERT INTO jobs VALUES ('2', 'asset', 'r2', ?, ?, 'QUEUED')",
                 (OCR_TASK_TYPE, OCR_ACTIVE_PROCESSOR_IDS[1]))
    c.db.execute("INSERT INTO jobs VALUES ('3', 'asset', 'r3', ?, ?, 'RETRY_WAIT')",
                 (OCR_TASK_TYPE, OCR_ACTIVE_PROCESSOR_IDS[1]))
    c.db.execute("INSERT INTO asset_ocr_text VALUES (1, 'r1')")
    counts = ocr_enrichment_counts(c, source_id=1)
    for key in ('succeeded', 'running', 'queued', 'retry_wait', 'blocked', 'failed', 'unattempted'):
        assert counts[key] == 1
    assert counts['pending'] == 3
    assert sum(counts[key] for key in ('succeeded', 'running', 'queued', 'retry_wait', 'blocked', 'failed', 'unattempted')) == counts['total']


def test_duplicate_activity_does_not_refresh_progress_clock():
    times = iter([1000, 2000, 40000])
    status = RefreshStatus(refresh_id='x', source_ids=None, batch_size=1,
                           started_at_ms=0, now_ms=lambda: next(times))
    assert status.snapshot()['progress_updated_at_ms'] is None
    status.set_activity(phase='semantic', detail='encoding_images', completed=1, total=2)
    status.set_activity(phase='semantic', detail='encoding_images', completed=1, total=2)
    assert status.snapshot()['progress_updated_at_ms'] == 1000
    status.set_activity(phase='semantic', detail='encoding_images', completed=0, total=2)
    assert status.snapshot()['activity']['completed'] == 0
    assert status.snapshot()['progress_updated_at_ms'] == 40000


def test_frontend_statuses_and_candidate_area():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    assert node
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert/strict");
let source = fs.readFileSync("src/photo_search/web/app.js", "utf8");
source = source.replace('  if (document.readyState === "loading") {',
  '  globalThis.ui = {ocrProgressText, refreshActivityText, renderCandidateTaskArea};\n  if (document.readyState === "loading") {');
const elements = {};
function element() { return {children: [], textContent: "", className:"", disabled:false,
  replaceChildren() {this.children = [];}, appendChild(x) {this.children.push(x);}, addEventListener() {}}; }
const context = {document: {readyState:"loading", addEventListener(){}, createElement:element,
  getElementById(id){return elements[id] || null;}}, Date:{now:()=>100000}};
vm.createContext(context); vm.runInContext(source, context);
const ui = context.ui;
let text = ui.ocrProgressText({state:"IN_PROGRESS", pending:3, running:1, queued:1, retry_wait:1});
assert.match(text, /正在识别/); assert.match(text, /排队中/); assert.match(text, /等待重试/);
text = ui.ocrProgressText({state:"IN_PROGRESS", pending:3});
assert.match(text, /等待处理或执行/); assert.doesNotMatch(text, /正在识别/);
let status = {running:true, worker_alive:true, progress_updated_at_ms:65000,
 activity:{phase:"semantic", detail:"encoding_images", completed:1, total:2}};
assert.match(ui.refreshActivityText(status).join(" "), /长时间无进度，请检查/);
status.activity.waiting_reason="MODEL_RESOURCE";
assert.match(ui.refreshActivityText(status).join(" "), /等待模型资源/);
assert.doesNotMatch(ui.refreshActivityText(status).join(" "), /长时间无进度/);
status.worker_alive=false; assert.match(ui.refreshActivityText(status).join(" "), /后台线程已退出/);
status.worker_alive=true; status.progress_updated_at_ms=null; status.updated_at_ms=65000;
assert.match(ui.refreshActivityText(status).join(" "), /暂未报告/);
const provider={provider_id:"candidate",installed:true,index_state:"READY"};
const area=elements["candidate-task-candidate"]=element();
const active=elements["active-search-model"]=element(); active.textContent="当前搜索模型：active；有效向量：5";
ui.renderCandidateTaskArea(provider,{provider_id:"candidate",status:"RUNNING",confirmed_entries:2});
assert.match(area.children[0].textContent, /RUNNING/); assert.equal(area.children[2].textContent,"暂停");
const pauseButton = area.children[2];
ui.renderCandidateTaskArea(provider,{provider_id:"candidate",status:"RUNNING",confirmed_entries:3});
assert.equal(area.children[2], pauseButton);
ui.renderCandidateTaskArea(provider,{provider_id:"candidate",status:"FAILED",confirmed_entries:2});
assert.match(area.children[0].textContent, /FAILED/); assert.equal(area.children[2].textContent,"继续建库");
assert.equal(active.textContent,"当前搜索模型：active；有效向量：5");
'''
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr


def test_worker_liveness_is_separate_from_running_flag():
    from photo_search.api.refresher import LibraryRefresher

    class Worker:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    refresher = LibraryRefresher(lambda: None)
    refresher._running = True
    refresher._thread = Worker(True)
    assert refresher.status()["running"] is True
    assert refresher.status()["worker_alive"] is True
    refresher._thread.alive = False
    assert refresher.status()["running"] is True
    assert refresher.status()["worker_alive"] is False
    refresher._running = False
    assert refresher.status()["running"] is False
