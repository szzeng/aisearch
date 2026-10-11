# P4-LIBRARY-OPS-A（V2）执行报告

**状态：SCOPED PASS / USER-DIRECTED LUNA-H REVIEW ROUTE EXCEPTION**
**日期：2026-10-11**
**基线：** `master @ cc321f71e8615cc5e611d30c13fa5aa66a9b2e8b`（开工时工作树干净）

## 结果

- 首页全局入口现为“继续全部建库”和“同步全部变化”。两个动作省略 `source_ids`，不会受 64 项上限影响；无来源时禁用，脚本也阻止空范围提交。
- 来源摘要卡直接提供“建库/补齐”和“同步变化”，不请求来源详情即可操作。仅在用户展开详情后读取单来源进度；加载可靠状态后，未建/有 backlog/已建分别显示开始/继续建库或以同步为主。完整校验扫描、手动补齐和删除来源收在更多操作中，保留删除确认及原文件保护说明。
- 刷新器 FIFO 用 `None` 表示一个全范围 build 请求，作用范围在 worker 开始时由现有 pipeline 对当时已登记来源解析。原 `pending_builds` 形状保留，新增 `pending_all_build` 表示排队的全范围 build。重复全范围 build 去重；全范围 build 可排在来源 build 或 refresh 后；refresh 忙碌时仍拒绝第二个 refresh。关闭时排队项被丢弃。
- worker 执行时若全范围 build 发现没有登记来源，会以 `EMPTY_LIBRARY` 明确失败，不报告虚假完成。没有更改扫描、识别、持久进度或 schema 语义。

## 任务卡验收

- UI 行为桩检查全局 build/refresh 请求都省略 `source_ids`，来源级 build/refresh 各带一个 ID；空来源不发请求。65 个假来源仍只传 `None` 全范围标记。真实合成 API/扫描集成测试登记 3 个临时来源，确认全范围 build 扫描 3/3。
- FIFO 行为覆盖：来源 build 活跃时排入全范围 build、重复点击去重、refresh 活跃时排入全范围 build、重复 refresh 拒绝、关闭时清除全范围等待标记；全范围请求在执行时来源已空时安全失败。
- 复用的单来源集成覆盖确认现有 build backlog、已确认向量复用、来源增强不可用状态和 refresh 只处理变化的旧语义；没有因本次改动宣称真实模型或真实图库完成。
- 来源页集成断言全局入口、来源级操作、更多操作、禁用初态和状态字段存在；source summary 仍走 `/sources/summary`，没有新增批量详情请求。

## 验证

命令：

```powershell
uv run --locked ruff check src/photo_search/api/refresher.py src/photo_search/api/models.py src/photo_search/api/routes.py tests/unit/test_api_refresher_queue.py tests/unit/test_api_frontend.py tests/integration/test_p4_02y_i_source_build.py
node --check src/photo_search/web/app.js
python -m py_compile src/photo_search/api/refresher.py src/photo_search/api/models.py src/photo_search/api/routes.py
uv run --locked pytest -q tests/unit/test_api_refresher_queue.py tests/unit/test_api_frontend.py tests/integration/test_p4_02y_i_source_build.py::test_global_build_request_builds_every_registered_synthetic_source tests/integration/test_p4_02y_i_source_build.py::test_the_sources_page_carries_the_unified_build_and_sync_entries tests/integration/test_p4_02y_i_source_build.py::test_an_interrupted_build_continues_without_re_encoding_confirmed_assets tests/integration/test_p4_02y_i_source_build.py::test_the_progress_of_an_unavailable_processor_is_reported_not_guessed tests/integration/test_p4_02y_i_source_build.py::test_a_build_catches_up_the_enrichment_backlog_even_with_no_delta tests/integration/test_p4_02y_i_source_build.py::test_the_daily_source_refresh_still_only_processes_the_change --junitxml=.pytest-tmp/P4-LIBRARY-OPS-A-final.xml
git diff --check
```

结果：Ruff、Node 语法、Python 编译、diff-check 全部通过；JUnit **108/0/0/0**。JUnit 文件：`.pytest-tmp/P4-LIBRARY-OPS-A-final.xml`。只运行上述定向集合，未运行完整 integration/unit 回归。

## 未验证与复核交接

- 没有运行真实浏览器手测，没有访问真实照片目录、用户 `PhotoSearchData`、真实模型/GPU，也没有做 30 万压测或全量回归。
- 建议手测：登记三个隔离测试图库；先启动一个来源 build，再点“继续全部建库”并重复点击，确认只排一个全局任务、前任务结束后覆盖执行时所有来源；另验证“同步全部变化”仅增量运行、0 来源禁用、详情中的完整校验和删除确认可用，以及窄窗口按钮可见/可键盘操作。
- 用户明确指定的独立 LUNA / High 首轮复核发现一个 P1：首页在刷新运行时同时禁用了全局 build 和 refresh，阻断 refresh 活跃时提交全范围 build。现已修复为：有来源时 build 始终可用，只有 refresh 按运行状态禁用；队列 UI 回归断言 refresh 活跃时两个按钮的状态。最终任务定向集合重新通过 **108/0/0/0**。
- 子代理完成原 P1 修复边界的独立 LUNA / High 窄复核，结论 **resolved / finding=none**；复核者确认 JUnit 为 108/0/0/0，未重跑套件。按用户明确要求记录为 **LUNA / High user-directed review route exception**，不冒称 DS-H。V3.1 §2.4 默认要求 DS-H 全受影响面主审；本次首轮已完整检查改动，修复后完成原发现与新增差异短审。无未决阻塞，任务按此路由收口。没有证据命中 V3.1 §2.2 的 DS-MAX 升级条件；Sol 聚焦终审仅在出现高风险未决项时再判断。

下一任务卡已存在：[P4-LIBRARY-SYNC-B](../tasks/P4-LIBRARY-SYNC-B.md)。本轮不自动启动后续任务。
