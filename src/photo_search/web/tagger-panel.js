/* Optional local recognition control. All writes use the existing session/CSRF boundary. */
(function () {
  "use strict";
  var panel = document.getElementById("tagger-panel");
  if (!panel) return;
  var status = document.getElementById("tagger-status");
  var confirm = document.getElementById("tagger-exclusive");
  var start = document.getElementById("tagger-start");
  var pause = document.getElementById("tagger-pause");
  var resume = document.getElementById("tagger-resume");
  var refresh = document.getElementById("tagger-refresh");
  var current = null, busy = false, timer = null, controller = null;
  var startRequest = null;
  var labels = {RUNNING: "正在识别", PAUSE_REQUESTED: "正在完成当前照片后暂停",
    PAUSED: "已暂停", INTERRUPTED: "上次识别已中断，可继续", COMPLETED: "本批识别完成",
    FAILED: "识别遇到问题，可重试"};

  function stopPolling() {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  }
  function render() {
    var task = current && current.task;
    var running = task && (task.status === "RUNNING" || task.status === "PAUSE_REQUESTED");
    var blocked = task && task.recoverable === false;
    var waiting = task && ["PAUSED", "INTERRUPTED", "FAILED"].indexOf(task.status) >= 0;
    start.hidden = Boolean(running || blocked);
    start.textContent = waiting ? "用当前模型开始新一轮" : "识别尚未处理的照片";
    start.disabled = busy || blocked || !confirm.checked || !(current && current.installed);
    pause.hidden = !running;
    pause.disabled = busy || !task || task.status !== "RUNNING";
    resume.hidden = !waiting || blocked;
    resume.disabled = busy || !confirm.checked || !(current && current.installed);
    refresh.disabled = busy;
    if (!current) return;
    if (blocked) {
      status.textContent = "识别任务记录需要检查，暂时不能继续；记录已保留，普通搜索可继续使用。";
      return;
    }
    if (!current.installed) {
      status.textContent = "识别模型尚未安装或未通过校验；普通搜索可继续使用。";
      return;
    }
    var count = current.coverage ? Number(current.coverage.completed_count || 0) : 0;
    var message = "已识别 " + count.toLocaleString() + " 张照片。";
    if (waiting) message += " 可继续原任务，或开始新一轮；已有识别结果会保留。";
    var skipped = task && task.last_error && task.last_error.code === "IMAGES_SKIPPED";
    if (skipped) message += " 本轮已结束，部分照片未识别；修复来源后点击识别尚未处理照片，可重试。";
    else if (task) message += " " + (labels[task.status] || "状态待确认") + "。";
    var counts = current.coverage && current.coverage.job_counts || {};
    var failed = Number(counts.FAILED || 0) + Number(counts.BLOCKED || 0);
    if (failed) message += " 有 " + failed + " 张暂未完成。";
    if (task && task.last_error && !skipped) {
      var reasons = {MODEL_RESOURCE_BUSY: "其他模型任务正在运行，请稍后继续",
        PROCESS_INTERRUPTED: "上次程序退出时任务尚未完成",
        TAGGER_MODEL_INVALID: "识别模型校验失败，请检查模型包",
        TAGGER_RUNTIME_UNAVAILABLE: "识别运行环境暂不可用",
        TAGGER_DEVICE_UNAVAILABLE: "CUDA 运行环境暂不可用",
        TAGGER_MODEL_LOAD_FAILED: "识别模型加载失败，请检查模型包和显存",
        TAGGER_INFERENCE_FAILED: "识别计算失败，请检查运行环境和显存"};
      message += " 原因：" + (reasons[task.last_error.code] || String(task.last_error.code || "UNKNOWN")) + "。";
    }
    status.textContent = message;
  }
  function schedule() {
    stopPolling();
    var task = current && current.task;
    if (panel.open && !document.hidden && task &&
        ["RUNNING", "PAUSE_REQUESTED"].indexOf(task.status) >= 0) {
      timer = window.setTimeout(load, 5000);
    }
  }
  async function request(path, body) {
    controller = new AbortController();
    var timeout = window.setTimeout(function () { if (controller) controller.abort(); }, 8000);
    try {
      var headers = {};
      if (body !== undefined) {
        headers["Content-Type"] = "application/json";
        var match = document.cookie.match(/(?:^|;\s*)ps_csrf=([^;]*)/);
        if (match) headers["X-CSRF-Token"] = decodeURIComponent(match[1]);
      }
      var response = await fetch("/api/v1/tagging/" + path, {
        method: body === undefined ? "GET" : "POST", credentials: "same-origin",
        cache: "no-store", headers: headers, signal: controller.signal,
        body: body === undefined ? undefined : JSON.stringify(body)
      });
      var value = await response.json();
      if (!response.ok) throw new Error(value.message || ("请求未成功（" + response.status + "）"));
      return value;
    } finally {
      window.clearTimeout(timeout);
      controller = null;
    }
  }
  async function load() {
    stopPolling();
    if (busy || !panel.open || document.hidden) return;
    busy = true; render();
    try {
      current = await request("status");
      if (current.task && current.task.request_id === startRequest) startRequest = null;
      busy = false; render(); schedule();
    } catch (error) {
      current = null; busy = false; render();
      status.textContent = "状态读取失败，请点击刷新状态。" + (error.name === "AbortError" ? "请求超时。" : error.message);
    }
  }
  async function act(action) {
    if (busy || !current || !current.installed) return;
    if (action !== "pause" && !confirm.checked) return;
    var task = current.task;
    if (action !== "start" && !task) return;
    stopPolling(); busy = true; render();
    var path = action === "start" ? "build" : "tasks/" + encodeURIComponent(task.task_id) + "/" + action;
    if (action === "start" && !startRequest) startRequest = crypto.randomUUID();
    try {
      var body = {exclusive_model_use_confirmed: confirm.checked};
      if (action === "start") {
        body.request_id = startRequest;
        if (task && ["PAUSED", "INTERRUPTED", "FAILED"].indexOf(task.status) >= 0) {
          body.replace_task_id = task.task_id;
        }
      }
      current.task = await request(path, body);
      startRequest = null;
      busy = false; render();
      await load();
    } catch (error) {
      current = null; busy = false; render();
      status.textContent = "操作结果尚未确认，请刷新状态后继续。" +
        (error.name === "AbortError" ? "请求超时。" : error.message);
      // A timed-out POST may have reached the server. Never retry it automatically.
    }
  }
  panel.addEventListener("toggle", function () { if (panel.open) load(); else stopPolling(); });
  document.addEventListener("visibilitychange", function () { if (document.hidden) stopPolling(); else load(); });
  window.addEventListener("pagehide", function () { stopPolling(); if (controller) controller.abort(); });
  confirm.addEventListener("change", render);
  refresh.addEventListener("click", load);
  start.addEventListener("click", function () { act("start"); });
  pause.addEventListener("click", function () { act("pause"); });
  resume.addEventListener("click", function () { act("resume"); });
  render();
}());
