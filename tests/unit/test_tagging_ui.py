"""Exercise the real optional-tagger panel handlers without a browser dependency."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
class Element {
  constructor() { this.events = {}; this.open = false; this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  fire(name) { for (const callback of this.events[name] || []) callback({target: this}); }
  click() { if (!this.disabled && !this.hidden) this.fire('click'); }
}
function harness(task = null) {
  const nodes = new Map(), get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const calls = [], timers = new Map(), docEvents = {}, winEvents = {};
  let nextTimer = 1, nextUuid = 1, handler = null;
  let state = {installed: true, coverage: {completed_count: 10, job_counts: {}}, task};
  const response = value => ({ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(value))});
  const pendingUntilAbort = signal => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); }, {once: true});
  });
  const context = {
    AbortController, console, encodeURIComponent, decodeURIComponent,
    crypto: {randomUUID: () => 'request-' + nextUuid++},
    document: {hidden: false, cookie: 'other=x; ps_csrf=fixture%2Ftoken', getElementById: get,
      addEventListener(name, callback) { (docEvents[name] ||= []).push(callback); }},
    window: {setTimeout(callback, ms) { const id = nextTimer++; timers.set(id, {callback, ms}); return id; },
      clearTimeout(id) { timers.delete(id); },
      addEventListener(name, callback) { (winEvents[name] ||= []).push(callback); }},
    fetch: async (url, options) => {
      const call = {url, options, body: options.body === undefined ? null : JSON.parse(options.body)};
      calls.push(call);
      if (handler) return handler(call, response, pendingUntilAbort);
      if (options.method === 'GET') return response(state);
      if (url.endsWith('/build')) state.task = {task_id: 'task/path?', request_id: call.body.request_id, status: 'RUNNING', recoverable: true};
      else if (url.endsWith('/pause')) state.task.status = 'PAUSED';
      else if (url.endsWith('/resume')) state.task.status = 'RUNNING';
      else throw new Error('unexpected endpoint ' + url);
      return response(state.task);
    }
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  return {get, calls, timers, context, pendingUntilAbort,
    setHandler(value) { handler = value; },
    get state() { return state; }, set state(value) { state = value; },
    async open(value = true) { get('tagger-panel').open = value; get('tagger-panel').fire('toggle'); await flush(); },
    async confirm(value = true) { get('tagger-exclusive').checked = value; get('tagger-exclusive').fire('change'); await flush(); },
    async click(name) { get('tagger-' + name).click(); await flush(); },
    async hidden(value) { context.document.hidden = value; for (const callback of docEvents.visibilitychange || []) callback(); await flush(); },
    async pagehide() { for (const callback of winEvents.pagehide || []) callback(); await flush(); },
    async tick(ms) { for (const [id, value] of [...timers]) { if (value.ms === ms) { timers.delete(id); value.callback(); } } await flush(); },
    countTimers(ms) { return [...timers.values()].filter(value => value.ms === ms).length; },
    posts() { return calls.filter(call => call.options.method === 'POST'); }
  };
}
const running = () => ({task_id: 'task/path?', request_id: 'existing', status: 'RUNNING', recoverable: true});
const scenarios = {
  async collapsed_confirmation_csrf() {
    const h = harness(); await flush();
    assert.equal(h.calls.length, 0, 'default collapsed panel must not fetch');
    assert.equal(h.timers.size, 0);
    await h.open(); assert.equal(h.calls.length, 1);
    assert.equal(h.get('tagger-start').disabled, true);
    h.get('tagger-start').fire('click'); await flush();
    assert.equal(h.posts().length, 0, 'even a synthetic click cannot bypass unchecked confirmation');
    await h.confirm(); assert.equal(h.get('tagger-start').disabled, false);
    await h.click('start');
    assert.equal(h.posts().length, 1);
    const post = h.posts()[0];
    assert.equal(post.url, '/api/v1/tagging/build');
    assert.equal(post.body.exclusive_model_use_confirmed, true);
    assert.equal(post.body.request_id, 'request-1');
    assert.equal(post.options.headers['X-CSRF-Token'], 'fixture/token');
    assert.equal(post.options.headers['Content-Type'], 'application/json');
    assert.equal(post.options.credentials, 'same-origin');
    assert.equal(post.options.cache, 'no-store');
    assert.equal(h.get('tagger-pause').hidden, false);
  },
  async pause_resume_without_duplicate_requests() {
    const h = harness(running()); await h.open();
    assert.equal(h.get('tagger-pause').disabled, false);
    await h.click('pause');
    assert.equal(h.posts().at(-1).url, '/api/v1/tagging/tasks/task%2Fpath%3F/pause');
    assert.equal(h.posts().at(-1).body.exclusive_model_use_confirmed, false, 'pause never requires new exclusive permission');
    assert.equal(h.get('tagger-resume').hidden, false);
    assert.equal(h.get('tagger-resume').disabled, true);
    h.get('tagger-resume').fire('click'); await flush(); assert.equal(h.posts().length, 1);
    await h.confirm(); await h.click('resume');
    assert.equal(h.posts().length, 2);
    assert.equal(h.posts().at(-1).url, '/api/v1/tagging/tasks/task%2Fpath%3F/resume');
    assert.match(h.get('tagger-status').textContent, /正在识别/);
    assert.equal(h.countTimers(5000), 1);
  },
  async collapse_and_hidden_stop_polling() {
    const h = harness(running()); await h.open();
    assert.equal(h.countTimers(5000), 1);
    await h.tick(5000); assert.equal(h.calls.length, 2); assert.equal(h.countTimers(5000), 1);
    await h.open(false); assert.equal(h.countTimers(5000), 0);
    await h.tick(5000); assert.equal(h.calls.length, 2);
    await h.open(); assert.equal(h.calls.length, 3);
    await h.hidden(true); assert.equal(h.countTimers(5000), 0);
    await h.tick(5000); assert.equal(h.calls.length, 3);
    await h.hidden(false); assert.equal(h.calls.length, 4); assert.equal(h.countTimers(5000), 1);
    await h.open(false); await h.hidden(true); await h.hidden(false);
    assert.equal(h.calls.length, 4, 'returning to a hidden panel must not issue a request');
  },
  async timeout_requires_refresh_and_manual_retry_keeps_identity() {
    const h = harness(); await h.open(); await h.confirm();
    h.setHandler((call, response, pending) => pending(call.options.signal));
    await h.click('start'); assert.equal(h.posts().length, 1);
    await h.tick(8000);
    assert.match(h.get('tagger-status').textContent, /操作结果尚未确认/);
    assert.match(h.get('tagger-status').textContent, /请求超时/);
    assert.equal(h.get('tagger-start').disabled, true);
    assert.equal(h.get('tagger-refresh').disabled, false);
    await h.tick(5000); await h.tick(8000); assert.equal(h.posts().length, 1);
    h.setHandler(null); await h.click('refresh'); await h.click('start');
    assert.equal(h.posts().length, 2);
    assert.equal(h.posts()[1].body.request_id, h.posts()[0].body.request_id, 'unconfirmed request may be retried manually with same identity');
  },
  async nonrecoverable_task_disables_build_and_resume() {
    const h = harness({...running(), status: 'FAILED', recoverable: false});
    await h.confirm(); await h.open();
    assert.equal(h.get('tagger-start').disabled, true);
    assert.equal(h.get('tagger-resume').hidden || h.get('tagger-resume').disabled, true);
    assert.equal(h.get('tagger-pause').hidden, true);
    assert.match(h.get('tagger-status').textContent, /记录已保留，普通搜索可继续使用/);
    await h.click('start'); await h.click('resume'); assert.equal(h.posts().length, 0);
    assert.equal(h.get('tagger-refresh').disabled, false);
  },
  async stopped_task_can_be_explicitly_replaced_using_its_identity() {
    for (const status of ['PAUSED', 'INTERRUPTED', 'FAILED']) {
      const h = harness({...running(), status}); await h.open();
      assert.equal(h.get('tagger-start').hidden, false);
      assert.equal(h.get('tagger-start').disabled, true);
      assert.match(h.get('tagger-status').textContent, /已有识别结果会保留/);
      await h.confirm(); await h.click('start');
      assert.equal(h.posts().length, 1);
      assert.equal(h.posts()[0].body.replace_task_id, 'task/path?');
      assert.notEqual(h.posts()[0].body.request_id, 'existing');
    }
  },
  async skipped_images_explain_retry_without_claiming_complete_coverage() {
    const h = harness({...running(), status: 'COMPLETED', last_error: {code: 'IMAGES_SKIPPED'}});
    h.state.coverage.job_counts = {FAILED: 2, BLOCKED: 1}; await h.confirm(); await h.open();
    const text = h.get('tagger-status').textContent;
    assert.match(text, /部分照片未识别/); assert.match(text, /修复来源后/); assert.match(text, /可重试/);
    assert.match(text, /3 张暂未完成/); assert.doesNotMatch(text, /本批识别完成/);
    assert.equal(h.get('tagger-start').hidden, false); assert.equal(h.get('tagger-start').disabled, false);
    assert.equal(h.countTimers(5000), 0);
  },
  async pagehide_aborts_inflight_request_without_background_retry() {
    const h = harness();
    h.setHandler((call, response, pending) => pending(call.options.signal));
    await h.open(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].options.signal.aborted, false);
    await h.pagehide(); assert.equal(h.calls[0].options.signal.aborted, true); assert.equal(h.timers.size, 0);
    await h.tick(5000); assert.equal(h.calls.length, 1);
  },
  async confirmed_timed_out_build_allows_a_new_batch_after_completion() {
    const h = harness(); await h.open(); await h.confirm();
    h.setHandler((call, response, pending) => {
      h.state.task = {task_id: 'accepted-task', request_id: call.body.request_id, status: 'COMPLETED', recoverable: true};
      return pending(call.options.signal);
    });
    await h.click('start'); await h.tick(8000); assert.equal(h.posts().length, 1);
    h.setHandler(null); await h.click('refresh');
    assert.equal(h.get('tagger-start').hidden, false);
    await h.click('start'); assert.equal(h.posts().length, 2);
    assert.notEqual(h.posts()[1].body.request_id, h.posts()[0].body.request_id,
      'status already confirmed the prior timed-out request; next batch needs a new request identity');
  }
};
(async () => {
  assert.ok(Object.hasOwn(scenarios, process.argv[1]), 'known scenario');
  await scenarios[process.argv[1]]();
})().catch(error => {console.error(error); process.exitCode = 1;});
"""


@pytest.mark.parametrize("scenario", [
    "collapsed_confirmation_csrf",
    "pause_resume_without_duplicate_requests",
    "collapse_and_hidden_stop_polling",
    "timeout_requires_refresh_and_manual_retry_keeps_identity",
    "nonrecoverable_task_disables_build_and_resume",
    "stopped_task_can_be_explicitly_replaced_using_its_identity",
    "skipped_images_explain_retry_without_claiming_complete_coverage",
    "pagehide_aborts_inflight_request_without_background_retry",
    "confirmed_timed_out_build_allows_a_new_batch_after_completion",
])
def test_tagging_panel_handlers(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for tagging panel interaction checks"
    html = (ROOT / "src/photo_search/web/search.html").read_text(encoding="utf-8")
    assert '<details id="tagger-panel" class="search-explainer">' in html
    assert '/web/tagger-panel.js?' in html
    result = subprocess.run(
        [node, "-e", HARNESS, scenario, str(ROOT / "src/photo_search/web/tagger-panel.js")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
