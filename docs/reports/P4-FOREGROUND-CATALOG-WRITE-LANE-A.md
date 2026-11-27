# P4-FOREGROUND-CATALOG-WRITE-LANE-A 执行报告

状态：**SCOPED PASS / SOL HIGH REVIEW PASS**（2026-11-27）。任务卡：[P4-FOREGROUND-CATALOG-WRITE-LANE-A](../tasks/P4-FOREGROUND-CATALOG-WRITE-LANE-A.md)。

## 实施与边界

基线 `a752709030d39d5d68c88da5eba60048417fe5a8`，工作区干净；相对指定产品基线 `a2059ad` 有后续文档提交。复用 `PersonWriteWorker` 的白名单、一个执行加四个等待队列、线程内 CatalogService 及原关闭路径，增加普通操作 `CATALOG_WRITE_BUSY`。收藏、普通照片合集、人物合集、人物封面/收件箱可见性与来源改名共 15 条路由迁入；不迁移搜索保存、来源登记删除、扫描及其他复杂路径。不修改通用关闭、lease、Catalog.write、事务协议或数据库设置。

## 验证

Python 3.12 环境，使用项目 `.venv`、独立合成目录、JUnit 与不同 `--basetemp`。未访问真实图库或模型。以下均为实际 pytest 退出码 0 的组合证据，不是一次全仓通过：

- `test-results/foreground-write/affected.xml`：94 passed，包含 B1 Worker 关闭、收藏、照片合集、人物合集、人物相册、来源与 A0 锁序。
- `test-results/foreground-write/extra.xml`：56 passed，包含 A1 查询池生命周期、读取和 source-detail、收藏、人物 API、合集；1 条 JUnit 属性兼容性警告。
- `test-results/foreground-write/final-r3.xml`：14 passed，包括 5 个新增场景及现有人物封面测试。完整 ASGI lifespan 成功初始化请求线程服务后，收藏写在第二连接 `BEGIN IMMEDIATE` 锁下等待；健康、人物浏览、照片列表、来源摘要在释放锁前返回；验证独立线程及连接。覆盖新旧混排 1+4、两个忙错误、取消、正常及零宽限关闭时排空与 lease 保留；实际 API 覆盖照片合集改名、成员增删、版本冲突和删除。
- Ruff（`src tests tools`）、`compileall`、`git diff --check` 通过。未改前端，无 Node 检查。按受影响面执行 B/D 定向；未触发依赖、迁移、广泛合流或发布的全量条件。

原始 `new.xml` 为 1 passed / 1 failed：测试未运行应用 lifespan，写 Worker 已打开而请求线程服务未初始化，持锁期间首次人物浏览超时。正常 lifespan 在接收请求前初始化该服务；`final-r2.xml` 实际进入 lifespan 后通过。若启动恢复未能完成，首次读仍可能同步等待，未对该异常条件作通过结论。`final.xml` 的新增照片合集测试因误将 `collection.revision` 当顶层字段产生 1 个 KeyError；按已有响应契约修正测试后重跑同一组合 14/14，原始失败保留。

## 未完成

独立 Terra High 完整受影响面复核 PASS，无剩余 P0/P1/P2；审查者核验最终源码和保存的 JUnit，未独立重跑。审查提出的封面、合集 API、真实 lifespan 及取消写入证据缺口均已补齐。启动恢复失败时的冷读取未验。本任务不证明未迁移写路径无阻塞，不证明后台公平调度、真实图库容量或发行资格。未提交、推送或发布。

## 复现命令

设置 `$env:PYTHONPATH="$PWD;$PWD\src"`、`$env:PYTHONUTF8="1"` 和指向项目 `.tmp` 的隔离 `PHOTO_SEARCH_DATA_ROOT`，使用 `.venv\Scripts\python.exe -m pytest -q`，分别选择：

- `tests/integration/test_p4_person_write_worker.py tests/integration/test_p4_favorites_core.py tests/integration/test_p4_photo_collections.py tests/unit/test_photo_collections.py tests/integration/test_p4_person_collections.py tests/integration/test_p4_person_collection_manage.py tests/integration/test_p4_person_albums.py tests/integration/test_api_sources_assets.py tests/unit/test_catalog_worker_lock_order.py`。
- `tests/integration/test_p4_perf_catalog_worker_lifecycle.py tests/integration/test_p4_perf_catalog_reads.py tests/integration/test_p4_source_detail_stability.py tests/integration/test_p4_favorites_ux_b.py tests/integration/test_p4_favorites_search.py tests/integration/test_p4_merge_collections.py tests/integration/test_p3_person_api.py`。
- `tests/integration/test_p4_foreground_catalog_write_lane.py tests/integration/test_p4_person_cover.py`。

每组使用独立 `--basetemp=.pytest-tmp/<唯一名称>` 和 `--junitxml=test-results/foreground-write/<唯一名称>.xml`。静态检查：`.venv\Scripts\python.exe -m ruff check src tests tools`、`.venv\Scripts\python.exe -m compileall -q src tests/integration/test_p4_foreground_catalog_write_lane.py`、`git diff --check`。

## Sol High 独立复核（2026-11-27）

**CHANGES REQUIRED / P2**：若启动时 smart refresh 恢复因 SQLite 写锁暂缓，应用仍可进入 lifespan；此后共享写队列满时，`ApiError` 错误处理经 `context.redactor()` → `service()` 同步重试恢复，`recover_interrupted_refreshes()` 请求 SQLite 写锁。合成库真实 lifespan/API 探针中，第二次 `BEGIN IMMEDIATE` 持锁、队列 1+4 满后发起收藏，拒绝响应的请求线程立即执行 `BEGIN IMMEDIATE`；外锁约 563 ms 后释放，独立健康请求约 641 ms 才获调度，最终 503 和 Retry-After 正常。首阶段为缩短故障注入临时调低 SQLite timeout，第二阶段请求连接 timeout 为 2 秒；非实际延迟预算。同一状态下，其他经相同错误处理器的业务 `ApiError` / `CatalogError` 也可能触发；例如无效收藏的业务拒绝。不能只验证或特判 503。复现脚本及输出分别为忽略目录 `test-results/foreground-write/sol-overflow-deferred-probe.py`、`sol-overflow-deferred-probe.txt`；再次回放退出 0，锁释放约 562 ms 后健康请求约 734 ms 才获调度。该失败路径须修复并重测，现阶段不得标任务 PASS。

按复核发现的 B1 覆盖缺口补跑原受影响文件 19 个，`test-results/foreground-write/sol-review-b1.xml` 为 181 passed、0 failed、0 skipped，实际退出 0；与此前 94 + 56 + 14 为增量证据，不能声称一次完整套件通过。首个临时探针因 PYTHONPATH 未含 tests 退出 1，修正环境后复现成功；原始证据保留在忽略目录。复核未修改产品代码；未访问真实图库或模型。

## Sol 复核问题修复（2026-11-27）

错误处理器改用仅访问已打开请求线程 Catalog 的脱敏入口；该入口不调用 `service()`、不启动服务或重试 smart refresh 恢复。已打开 Catalog 保留已注册来源路径脱敏，未打开或读取失败时保留 data-root 和通用 Windows 本地路径回退；常规非错误路径不变。未修改通用关闭、lease 或事务。

真实 lifespan 注入启动恢复延迟，第二 SQLite 连接持写锁；新测试验证 `ApiError`、`CatalogError` 和满队列 503 返回时锁仍持有，来源及数据目录已脱敏，健康请求响应，且请求线程未执行 `BEGIN IMMEDIATE`。修复前 `error-before2.xml` 的看门狗断言失败，修复后 `error-after.xml` 1/1；更早 `error-before.xml` 是测试误将业务 404 当 422，原始失败均保留。新增单测覆盖未初始化、已打开及读取失败时脱敏。

`test-results/foreground-write/error-fix-affected.xml`：94 passed，0 failed/errors/skipped，实际退出 0；包含新测试、B1 关闭、错误脱敏、会话、来源、人物 API 和封面。其余 unchanged 业务与 B1 证据复用此前 JUnit，不称作一次全套通过。`ruff check src tests tools`、相关 `compileall`、`git diff --check` 通过；独立 Sol High 复审 PASS：原 P2 已关闭，无新增阻塞项；审查核对源码和 JUnit，未独立重跑。

复现：按上方隔离环境，运行 `.venv\Scripts\python.exe -m pytest -q tests/integration/test_p4_foreground_catalog_write_lane.py tests/integration/test_p4_person_write_worker.py tests/unit/test_api_redaction.py tests/unit/test_api_session.py tests/integration/test_api_sources_assets.py tests/integration/test_p3_person_api.py tests/integration/test_p3_face_redetection_decisions.py tests/integration/test_p4_person_cover.py --junitxml=test-results/foreground-write/<唯一名称>.xml --basetemp=.pytest-tmp/<唯一名称>`。

复审建议补充的精确 ApiError/CatalogError 错误码与 ADR-0010 闭合详情测试：`test-results/foreground-write/error-review.xml`，2 passed、退出 0。其为此前 94 项的增量测试，按组合证据记录。普通读取在启动恢复暂缓时仍可能触发同步恢复；不属于错误响应修复及本卡结论，不声称已解决。
