# P1-04 — sources/assets API、受控文件访问、本机会话保护

状态：**PASS**（B 级验收全部真实完成，另按任务卡要求执行真实端口 smoke test 与 10 个场景；
因本任务改动了启动器，追加执行 **C 级打包验收**：PyInstaller onedir 实际构建 + 打包产物 API smoke，
并在该门禁上发现并修复了一个既有打包缺陷，见 §6）。
基线：**bb531384725c91de032bd3e280e76dfcf3b20054**（P1-03 收口提交，
`git log -1 --format=%H -- docs/reports/P1-03-orientation-thumbnails-cache-and-isolation.md`）。
执行模型（记录用）：**DS-H / DeepSeek V4.1 Flash / 高推理**。模型切换始终由用户在 Codex 中手动完成，
本任务未尝试、也未声称自动切换。

冻结契约：**ADR-0002 未修改**；`schema_version` 仍为 **1**（未新增迁移、未新增表）；未改动
`0001_initial.sql`、`models/**`、`runtime/**`、`workers/**`、模型清单与 P0/P1-01~P1-03 报告。
`pyproject.toml` / `uv.lock` 的改动只有一处：把 API 直接 `import pydantic` 所需的
`pydantic>=2.9,<3` 显式写入依赖（FastAPI 已依赖该范围，锁定版本未变化）。

---

## 1. 交付物清单

| 交付物 | 位置 |
|---|---|
| API 包（设置、会话、安全中间件、错误信封、Pydantic 模型、只读查询、受控文件访问、后台执行者、路由） | `src/photo_search/api/{__init__,settings,session,security,errors,handlers,models,queries,files,runner,routes}.py` |
| 应用装配（`create_app` 零副作用、`/api/v1/openapi.json` 受会话保护、无 `/docs`） | `src/photo_search/app.py` |
| 启动器（`--host/--port/--allow-non-loopback/--data-root`、凭证引导、访问日志令牌脱敏） | `src/photo_search/__main__.py` |
| 运行期目录与凭证路径（`<data_root>/runtime/`） | `src/photo_search/config.py` |
| 媒体状态单一判定（`asset_media_states`，CLI/API 共用） | `src/photo_search/media/status.py`、`media/__init__.py` |
| 复用 P1-03 的 stat 守卫（公开别名） | `src/photo_search/media/pipeline.py`（新增 `require_unchanged = _require_unchanged`） |
| 单元测试（会话/凭证/主机与范围解析/路径展示/游标） | `tests/unit/test_api_session.py`（29）、`tests/unit/test_api_queries.py`（13） |
| 集成测试（会话与预检、来源/扫描/分页、文件字节、真实端口） | `tests/integration/test_api_access.py`（9）、`test_api_sources_assets.py`（9）、`test_api_files.py`（8）、`test_api_server.py`（3） |
| 共享夹具（`%TEMP%`、确定性时钟） | `tests/conftest.py` |
| C 级打包门禁发现与修复：迁移脚本未进包导致「静默空库」 | `tools/packaging/build_onedir.py`（新增 `--collect-data photo_search`）、`src/photo_search/catalog/schema.py`（`migration_files()` 要求覆盖 0001..支持版本）、`tests/unit/test_catalog_migrations.py`（新增 1 个用例） |
| 打包产物只读 smoke 脚本 | `tools/diagnostics/packaged_api_smoke.py` |
| 本报告、状态更新、下一任务卡 | `docs/reports/P1-04-*.md`、`docs/development-status.md`、`docs/tasks/P1-05.md` |

新增测试 **72** 个；全量套件从 180 passed / 1 skipped 变为 **252 passed / 1 skipped**。

## 2. 开始前必须先检查（任务卡第 1-5 项）的实测结论

### 2.1 现有入口与版本（实测，非记忆）

`uv run python -c "import fastapi, pydantic, starlette, uvicorn, httpx, sys; ..."`：

| 组件 | 实测版本 |
|---|---|
| Python | 3.12.10 |
| fastapi | 0.141.1 |
| starlette | 1.6.0 |
| pydantic | 2.13.5 |
| uvicorn | 0.52.4 |
| httpx（dev） | 0.28.1 |

结论：`app.py` 原来只有 `GET /api/v1/health`，`__main__.py` 用 `uvicorn.run("photo_search.app:app",
host="127.0.0.1", port=8000)`；新应用保留 `photo_search.app:app` 名称与 health 行为，
`tests/unit/test_health.py` 未改动即通过（ASGI 内进程驱动继续可用）。

### 2.2 服务层复用

API 没有重写扫描、哈希、缩略图或身份判定：
`register_source` / `list_sources` / `get_source` / `counts` / `start_scan` / `run_scan` /
`generate_thumbnail` / `generate_preview` / `media_report` 与 `photo_search.media` 全部按原样复用。
派生产物仍由 P1-03 的 `MediaGenerator` 生成（同一 `catalog-thumbnail@1` /
`catalog-preview@1` 契约、同一内容寻址布局），API 只在调用前后做边界与状态判定。

### 2.3 受控读取语义

- 位置行来自 `file_locations`（`source_id`/`relative_path`/`path_key`/`size`/`mtime_ns`/`availability`），
  根来自 `source_roots.root_path`。
- 边界判定的唯一依据是 `photo_search.catalog.paths` 的 `root_key` / `path_key` / `is_same_or_ancestor`
  （大小写不敏感、不做 Unicode 归一化、不 `resolve()` 跟随链接）——API 未另造一套比较规则。
- `size`/`mtime_ns` 的「观测未变」守卫复用 P1-03 的 `_require_unchanged`（新增公开别名
  `require_unchanged`），因此缩略图、预览与原图不会对「是否仍然一致」给出不同答案。
- 在此之上新增三层独立防线：路径键合法性、重建路径的来源边界、根以下任一组件不得是 reparse point +
  `realpath` 物理包含性。

### 2.4 后台扫描的现状与结论（任务卡要求先决断的一点）

`execute_scan` 仍是同步、可中断、带 lease/心跳与迟到提交校验的唯一生产路径，
`jobs` 仍是五字段逻辑键 + 单写事务 + 状态机。API 的做法是：

1. 请求线程只做一次 `start_scan`（创建或复用同一非终态扫描），随即返回 202；
2. **单个**后台守护线程按 `CatalogService.run_scan`（即 `catalog scan` 的同一入口）执行，
   因此 lease/心跳/迟到提交资格/`reset_execution_coverage`/中断恢复全部沿用原路径；
3. 同一来源的重复请求与队列去重保证不会出现第二个并发 `scan_run`；
4. 进程被杀后，`jobs`/`scan_runs` 保持 `RUNNING` 且租约未消费，下一次请求在同一事务里接管
   （`claim_job` 的 takeover 分支），实测同一 `scan_id` 恢复为 `SUCCEEDED`、且 `attempts` 恰好 +1。

后台线程拥有**自己的** `Catalog` 连接（SQLite 连接是线程亲和的，不能跨线程共享），
请求线程的连接与该连接之间按 WAL + `BEGIN IMMEDIATE` + 5 s busy timeout 串行化。
这与 P1-02 报告里已经记录过的「跨进程写者」情形同类：本任务没有放宽任何冻结语义，
只把「同一进程两个连接」纳入实测。新增测试
`test_api_writes_do_not_collide_with_a_running_scan` 在 400 文件扫描真实进行期间发 36 次
请求（12 次写入尝试 + 12 次 stats + 12 次 jobs），观察到 `RUNNING`/`QUEUED` 状态，
返回集合为 `{200, 409}`、无 500，扫描最终 `SUCCEEDED`、400 个位置全部 PRESENT。
因此**无需升级到 C2**；残留风险见 §8。

### 2.5 数据目录约定

凭证与运行期文件写在 `<data_root>/runtime/session.json`（`config.runtime_dir` /
`session_credential_path`），不写安装目录、不写任何来源目录；派生产物仍写
`<data_root>/{thumbnails,previews,cache/media}`。

## 3. API 契约

### 3.1 路由（本任务最小子集，全部在 `/api/v1`）

| 方法 | 路径 | 会话 | 说明 |
|---|---|---|---|
| GET | `/health` | 免 | 保持 P0-01 行为，body 仍为 `{"status":"ok","service":"photo-search"}` |
| GET | `/session?token=` | 免 | 一次性凭证换取 `HttpOnly` 会话 cookie + double-submit CSRF cookie |
| GET | `/capabilities` | 需 | 应用版本、`schema_version`、是否可写、媒体契约、受支持输入格式、只读读数固定 `true`、`loopback_only`/允许的 Host 列表 |
| GET | `/stats` | 需 | `counts()` + 每种产物的 published/pending/source_missing/failed/unsupported 汇总 |
| GET | `/sources`、`/sources/{id}` | 需 | 来源对象 + 计数 |
| POST | `/sources` | 需 | 复用 `register_source`，201 + 来源对象；重叠/数据目录/非法路径沿用既有结构化错误码 |
| POST | `/sources/{id}/scan` | 需 | 202 + `scan_id`/`job_id`/`state`/`reused`，不阻塞 |
| GET | `/jobs`、`/jobs/{id}` | 需 | job 状态、`attempts`、`progress`、`last_error_code` 与绑定的 scan 行；**不含** `lease_token`/`worker_id` |
| GET | `/assets` | 需 | keyset 分页（`source_id`/`availability`/`has_thumbnail`/`limit`/`cursor`） |
| GET | `/assets/{id}` | 需 | 资产行 + 全部位置 + 每种产物的状态与结构化 code |
| GET/HEAD | `/assets/{id}/thumbnail`、`/preview` | 需 | 按需生成并返回 JPEG 字节 |
| GET/HEAD | `/assets/{id}/original` | 需 | 受控原图字节，支持 Range |
| GET | `/openapi.json` | 需 | Pydantic 模型导出的 OpenAPI；`/docs`、`/redoc` 关闭 |

### 3.2 统一错误信封与状态映射

所有失败都是 `{"code","message","request_id"[,"details"]}`，并带 `X-Request-ID` 响应头
（沿用客户端提供的合法 `X-Request-ID`，否则生成 16 位十六进制）。没有任何路径返回 Python traceback：
未预期异常由安全中间件兜底为 `INTERNAL_ERROR`/500，traceback 只进服务端日志。

| HTTP | 语义 | 代表 code |
|---|---|---|
| 401 | 无/失效会话与凭证 | `SESSION_REQUIRED`、`SESSION_INVALID` |
| 403 | CSRF、Origin/Host、文件访问拒绝 | `CSRF_INVALID`、`ORIGIN_NOT_ALLOWED`、`HOST_NOT_ALLOWED`、`ACCESS_DENIED` |
| 404 | 目标不存在 | `ASSET_NOT_FOUND`、`SOURCE_NOT_FOUND`、`JOB_NOT_FOUND` |
| 409 | 冲突/不支持/不可用 | `SOURCE_OVERLAP`、`DATA_ROOT_INSIDE_SOURCE`、`SCHEMA_TOO_NEW`、`INPUT_UNSTABLE`、`SOURCE_FILE_MISSING`、`DECODE_FAILED`、`FORMAT_UNSUPPORTED`、`DISK_SPACE_INSUFFICIENT` 等 |
| 422 | 参数非法 | `VALIDATION_ERROR`、`INVALID_PATH`、`INVALID_CURSOR` |
| 416 | Range 不可满足 | `INVALID_RANGE`、`RANGE_NOT_SATISFIABLE`（带 `Content-Range: bytes */N`） |
| 500 | 本机安装级故障 | `CATALOG_UNREADABLE`、`MIGRATION_FAILED`、`INTERNAL_ERROR` |

### 3.3 本机会话与访问边界

- 默认 `127.0.0.1`；非回环绑定必须显式 `--allow-non-loopback`，否则启动即 `exit 2`（实测未创建凭证与库）。
- 凭证：`PHOTO_SEARCH_SESSION_TOKEN` 可覆盖（测试/诊断）；正常运行时生成
  `secrets.token_urlsafe(32)` 并原子写入 `<data_root>/runtime/session.json`（先写临时文件 + fsync + `os.replace`）。
- 交换：只允许一次；比较用 SHA-256 摘要 + `hmac.compare_digest`（定长、常量时间）；
  令牌不存在/已用/错误一律 `401 SESSION_INVALID`，不回显令牌内容。
- Cookie：`ps_session`（`HttpOnly; SameSite=Strict; Path=/`）与 `ps_csrf`（同属性但非 HttpOnly，供前端 double-submit）。
- Host：只接受本机回环拼写（或显式允许的绑定地址），端口必须属于本服务（缺省端口或 80/443 亦可），
  以抵御 DNS rebinding。
- 变更请求（POST/PUT/PATCH/DELETE）：必须带 `Origin`（host 属于允许集合、端口属于本服务）与
  `X-CSRF-Token`（同时与会话内值与 cookie 比对）。
- 不开放任何 CORS 头、不实现局域网鉴权、不提供任意路径文件接口。
- 日志：`uvicorn.access` 上安装脱敏过滤器，`?token=`/`?csrf_token=` 一律变成 `<redacted>`；凭证只出现在
  启动器的控制台引导行（用户唯一获取渠道）。

### 3.4 受控文件访问

`GET /assets/{id}/original` 只接受资产 ID，依次要求：

1. 该资产至少有一个 `availability='PRESENT'` 位置（多位置按 `location_id` 稳定顺序取第一个可用者）；
2. 存储的 `relative_path` 能通过冻结的 `path_key()`（拒绝 `..`、绝对路径、UNC、盘符、根相对）；
3. 重建后的绝对路径与来源根满足 `is_same_or_ancestor`，且不等于来源根本身；
4. 根以下每个组件 `lstat` 都不得是 reparse point（junction/符号链接），且 `realpath` 物理包含性仍然成立；
5. `size`/`mtime_ns` 与库中观测完全一致（复用 P1-03 守卫）。

响应头：`Content-Type`（按扩展名映射，未知回落 `application/octet-stream`）、`Content-Length`、
`Last-Modified`、`Accept-Ranges: bytes`、`Cache-Control: private, max-age=0, must-revalidate`、
`ETag="<sha256>:<size>:<mtime_ns>"`、`Content-Disposition: inline; filename="<ascii>"; filename*=UTF-8''<pct>`
（文件名只取最后一段并清除分隔符/控制字符/引号）。

thumbnail/preview 响应头：`Content-Type: image/jpeg`、`Content-Length`、
`Cache-Control: private, max-age=31536000, immutable`、`ETag="<processor_id>:<sha256>"`、
`X-Original-Available: true|false`；支持 `If-None-Match` → 304。
未生成且原图不可用/越界/已变化 → 409 + `SOURCE_FILE_MISSING` / `ACCESS_DENIED` / `INPUT_UNSTABLE`；
损坏或不支持格式 → 409 + P1-03 失败记录里的 `DECODE_FAILED` / `FORMAT_UNSUPPORTED`（不返回空白图）。

### 3.5 分页与状态投影

- `GET /assets`：`asset_id` 递增的 keyset 游标，游标是 `base64url("v1:<asset_id>")`；默认 `limit=50`、
  上限 200（超限夹取，`limit<1` → 422）；物理缓存过滤（`has_thumbnail`）在有界批次（128 行/批）内进行，
  从不把整库读入内存。响应含 `assets`/`limit`/`next_cursor`/`has_more`/`availability`/`has_thumbnail`/`source_id`。
- 行字段：`asset_id`、`sha256`、`byte_size`、`created_at`、`location_count`、`availability`、
  `thumbnail_ready`、`preview_ready`、`current_location{source_id, relative_path}`（仅来源相对展示路径）。
- `GET /jobs`：`job_id` 递增 keyset，字段直接来自 `jobs`/`scan_runs` 既有列，`scan.error` 解析出
  `error_code`/`error_message`（消息经过路径脱敏）；新增表：**无**。
- 媒体状态（`ready`/`pending`/`source_missing`/`failed`/`unsupported`）由
  `media.status.asset_media_states` 单点判定，CLI `media-status`、`GET /stats` 与资产详情共用同一规则
  （P1-03 的 19 个媒体集成测试与 27 个解码/缓存测试全通过，数字未变）。

## 4. 真实执行的命令与退出码

| 命令 | 退出码 | 摘要 |
|---|---|---|
| `uv run pytest -q tests/unit tests/integration` | 0 | `252 passed, 1 skipped in 122.25s`（skip 为既有 `PHOTO_SEARCH_GPU_TESTS` 门禁用例） |
| `uv run ruff check .` | 0 | 无告警 |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 0 | `Resolved 95 packages` |
| `uv run python -c "import fastapi, pydantic, starlette, uvicorn, httpx, sys; ..."` | 0 | 见 §2.1 版本表 |
| 真实端口 smoke（`tests/integration/test_api_server.py`，子进程启动 `python -m photo_search`） | 0 | health 200、交换 200、Cookie 属性、CSRF 保护 POST 201、访问日志脱敏、非回环 exit 2 |
| `uv run python tools/packaging/build_onedir.py`（C 级） | 0 | 57 s，`dist/PhotoSearch/PhotoSearch.exe` 34,013,879 B，随包 21 个 NVIDIA DLL |
| `uv run python tools/diagnostics/packaged_api_smoke.py`（C 级） | 0 | `status=ok`、`problems=[]`（33 项记录 / 12 条判定全部通过，§6.3） |

## 5. 任务卡要求真实执行的 10 个场景与映射

1. **会话流程**：`test_business_endpoints_require_a_session`、`test_session_exchange_sets_cookies_and_unlocks_reads`、
   `test_bad_missing_and_reused_tokens_are_indistinguishable`、`test_state_changing_requests_need_origin_and_csrf`、
   `test_launcher_serves_the_api_on_loopback_and_redacts_the_credential`。
2. **来源**：`test_sources_create_list_and_structured_rejections`（成功 201、重叠 409、数据目录内 409、
   非目录 422、空名 422、未知来源 404）。
3. **扫描**：`test_scan_request_returns_202_and_completes_in_the_background`（202 + ids + jobs/stats/sources 一致）、
   `test_interrupted_scan_is_recovered_as_the_same_scan_run`（中断后同一 `scan_run` 恢复、`attempts` +1、无并发扫描）、
   `test_api_writes_do_not_collide_with_a_running_scan`（运行期写入不互相破坏）。
4. **资产分页**：`test_asset_paging_is_complete_ordered_and_bounded`（26 资产、`limit=7` 遍历不重不漏、
   `limit=1`/`2000`/`0`、非法 cursor、非法过滤器）。
5. **媒体字节**：`test_thumbnail_and_preview_are_generated_once_and_cached`（320/1600 长边、JPEG、ETag 稳定、
   二次请求命中同一产物、304、HEAD、磁盘上只有一个产物）、`test_original_bytes_ranges_and_headers`（逐字节一致 + SHA-256）。
6. **受控路径**：`test_traversal_rows_and_reparse_points_are_refused`（`..\`、深层 `..\`、绝对路径、UNC、
   盘符相对，以及**真实目录联接** `mklink /J` 指向来源外——全部拒绝且不返回字节；同来源的正常资产仍可读）。
7. **离线/缺失**：`test_offline_source_keeps_artifacts_and_reports_the_missing_original`（已有产物仍返回并标注
   `X-Original-Available: false`；未生成 → 409 `SOURCE_FILE_MISSING`；重扫后 `availability=missing` 仍可列出）、
   `test_an_original_replaced_after_the_scan_is_a_structured_conflict`（409 `INPUT_UNSTABLE`）。
8. **中文/空格/长路径 + 只读原图**：`test_unicode_space_and_long_paths_with_read_only_originals`
   （`>220` 字符路径、中文与空格、只读文件，接口全成功且来源树（含只读位）逐项未变）。
   `test_corrupt_and_unsupported_inputs_report_recorded_codes` 覆盖损坏 JPEG → `DECODE_FAILED`、
   真实 TIFF → `FORMAT_UNSUPPORTED`。
9. **更高 `schema_version`**：`test_higher_schema_version_is_structured_and_has_no_write_side_effects`
   （`schema_version=2` 的库上 capabilities/stats/sources/assets/jobs 与两个 POST 全部 409 `SCHEMA_TOO_NEW`；
   health 与 session 仍可用；目录清单与运行前完全一致，未产生 `-wal`/`-shm`）。
10. **真实端口 smoke**：`test_launcher_serves_the_api_on_loopback_and_redacts_the_credential`、
    `test_launcher_refuses_a_non_loopback_bind_by_default`、`test_token_redaction_filter_covers_the_known_query_spellings`。

另外两个单元文件覆盖会话管理器、凭证文件、绑定策略、Host/端口解析、路径脱敏、错误信封/状态映射、
文件名与 Range 解析、游标编码与边界。

## 6. C 级打包验收（启动器/打包资源改动触发的升级）

任务卡的验收条款写明「若本任务改动打包/启动器/PyInstaller 资源，则升级到 C」。
本任务按允许清单修改了 `src/photo_search/__main__.py`（绑定参数、凭证引导、以应用对象而非
`"module:app"` 字符串启动 uvicorn），因此追加执行了 C 级门禁。

### 6.1 门禁上发现的既有缺陷（重要）

第一次打包 smoke 失败：打包产物在新数据目录上执行 `POST /api/v1/sources` →
`500 INTERNAL_ERROR`，服务端 traceback 为
`sqlite3.OperationalError: no such table: source_roots`。

根因：`photo_search.catalog.migrations/0001_initial.sql` 是包内 **数据文件**，PyInstaller 的
`build_onedir.py`（P0-05 交付）只收集了模型清单、`tokenizers` 数据与 NVIDIA DLL，没有收集它。
于是冻结产物里 `schema.migration_files()` 返回空列表，`expected_objects()` 与
`schema_objects()` 的比较退化为「空 == 空」并判定通过：`migrate_database()` 成功返回，
但库中一张表都没有，直到第一次业务查询才以 `no such table` 爆出 500。
这是 P0-05 起就存在的**打包缺陷**（此前没有任何打包产物执行过 catalog/扫描路径，所以从未暴露），
不是 P1-04 引入的回归；但它同时暴露了 P1-01 迁移自检的一个空集盲区。

### 6.2 本任务所做的最小修复

1. **打包资源**（`tools/packaging/build_onedir.py`）：新增 `--collect-data photo_search`，
   把包内数据文件（今天只有 `catalog/migrations/*.sql`，P1-05 的静态前端资源也将受益）打进产物。
2. **迁移自检**（`src/photo_search/catalog/schema.py`）：`migration_files()`
   原先只要求「已存在的脚本编号连续」，现在要求脚本集合**恰好覆盖 `0001..SUPPORTED_SCHEMA_VERSION`**；
   缺少脚本时立即抛 `SCHEMA_INCONSISTENT`（ADR-0002 §7.1 第 7 条），
   不再退化成「空库也算一致」。该改动不放宽任何冻结语义，只把静默损坏变成显式拒绝。
   新增用例 `test_a_build_without_migration_scripts_fails_loudly`
   （空迁移目录 → `SCHEMA_INCONSISTENT`；`Catalog.open` 亦拒绝，且不产生任何业务表）。
3. ADR-0002、既有迁移 `0001_initial.sql`、`schema_version` 基线、表/主键/唯一键/身份/缺失判定语义：
   **均未改动**。

### 6.3 打包产物 smoke 结果（`dist/PhotoSearch` → `%TEMP%\P1-04 打包 API 验收\PhotoSearch 中文 空格`）

脚本 `tools/diagnostics/packaged_api_smoke.py` 复制整包到**中文+空格**安装路径，用**中文+空格**
的数据目录与来源目录驱动真实 HTTP 流程，输出 `status=ok`、`problems=[]`，检查项：

| 检查 | 实测 |
|---|---|
| 启动（含 `_internal` 加载） | 1.02 s 后打印 listening + 一次性会话 URL |
| 中文/空格安装路径 | 是（`...\PhotoSearch 中文 空格\PhotoSearch.exe`，34,013,879 B） |
| 凭证文件 | `<data_root>\runtime\session.json` 存在 |
| `GET /api/v1/health` | 200 `{"status":"ok","service":"photo-search"}` |
| 未带会话读 `GET /api/v1/stats` | 401 |
| `GET /api/v1/session?token=` | 200，`Set-Cookie` 含 `HttpOnly` 与 `Path=/` |
| 同一令牌再次交换 | 401 |
| `POST /api/v1/sources`（中文名 + 中文/空格来源根） | 201 |
| `POST /api/v1/sources/{id}/scan` | 202 → 轮询 `GET /api/v1/jobs` 到 `SUCCEEDED` |
| `GET /api/v1/assets` | 1 个资产、`has_more=false`、`availability=present` |
| `GET /thumbnails` | 200 `image/jpeg`，320×240，1,829 B，`X-Original-Available: true` |
| 删除原图后 `GET /thumbnail` | 200，`X-Original-Available: false`（已有产物继续可用） |
| 删除原图后 `GET /original` | 409 `SOURCE_FILE_MISSING` |
| `GET /assets/{id}` / `/capabilities` | 200 / 200 |
| 访问日志 | 14 条 access 行全部 `token=<redacted>`，无原始令牌；stderr 无令牌 |

结论：C 级门禁真实执行完毕并通过；打包产物上的中文/空格/离线场景均为实测（非推断）。
建议由 **C1 / GPT-5.6 Terra / medium** 复核第 6.1-6.2 节的打包修复与冻结语义解释。

## 7. 会话与受控访问证据（按场景）

- **凭证不落日志**：真实端口 smoke 中，控制台引导行含一次性 URL（用户唯一获取渠道），
  而 `uvicorn.access` 的请求行为 `GET /api/v1/session?token=<redacted> HTTP/1.1`；
  测试断言所有 access 行都不含原始令牌，stderr 亦不含。
- **凭证文件权限**：写入 `<data_root>/runtime/session.json`；本机实测收紧结果为
  `inherited:icacls-exit-5`（沙箱拒绝修改 DACL），已在 §8 记为未验证项，不声称已收紧。
- **Cookie 属性**：真实端口下 `ps_session=...; path=/; samesite=strict; HttpOnly`，
  `ps_csrf=...; path=/; samesite=strict`（无 HttpOnly），与 ASGI 内进程断言一致。
- **一次性**：同一令牌第二次交换 → 401 `SESSION_INVALID`；错误/缺失令牌同样只返回该 code。
- **越界**：五个注入行 + 一个真实 junction 全部 403 `ACCESS_DENIED`（junction 场景），
  其余为 403/409，响应体与头部均不含 `outside` 目录的绝对路径，也未泄漏任何字节。
- **不可用原图**：产物已存在时仍 200 且 `X-Original-Available: false`；未生成 → 409；
  变更后 → 409 `INPUT_UNSTABLE`；不返回空白图或伪造成功。
- **只读/长路径**：来源树状态（`size`/`mtime_ns`/只读位）在全部请求前后逐项相同。
- **大文件**：1.5 MB 原图经分块流式返回，长度与 SHA-256 与源文件一致。

## 8. 未验证项、限制与「不可声称」

1. **凭证文件 DACL**：本机（沙箱）无法修改 ACL（`icacls` 返回 5），因此「Windows 上已收紧到仅当前用户」
   未验证。代码走的是 best-effort 路径（先落盘再尝试 `/inheritance:r /grant:r <user>:(R,W)`），
   失败只记录字符串。真实用户机上是否成功需要在非沙箱环境复验（建议 C1 关注）。
2. **同进程双连接竞争**：只验证了「一个扫描 + 顺序 API 写入」的竞争窗口（36 次请求、无 500）。
   未做多客户端并发写入、长事务阻塞、或 `busy_timeout` 边界（5 s）耗尽时的行为验证。
3. **会话生命周期**：会话仅存在于内存，随进程结束失效，无 TTL、无主动登出接口、无 cookie 刷新；
   不是面向多用户或局域网的鉴权设计。
4. **单请求阻塞**：缩略图/预览按需生成在事件循环线程上同步执行（单资产，实测数十毫秒；
   P1-03 记录的 108 MP 预览约 170 ms），大图密集请求会短暂阻塞其它请求。有界解码池仍是 P2 项。
5. **`stats` 与 `media-status` 为 O(assets)**：`GET /api/v1/stats` 复用 P1-03 的只读状态计算，
   会遍历全部资产并对未生成产物做存在性探针；没有增量状态索引（P1-03 已记录）。
6. **`assets` 的时间语义**：`created_at` 是**入库时间**，不是 EXIF 拍摄时间；
   本任务没有新增 `taken_at` 列（那需要按 ADR-0002 §7 的加列迁移 + C2 决策）。
   P1-05 的「时间浏览」必须按入库时间呈现并如实标注，不得冒充拍摄时间。
7. **`has_thumbnail` 过滤**：只能后置过滤，最坏情况会按批扫描整个库（内存有界但 IO 与存在性探针增多）；
   未做缓存状态索引。
8. **Range**：只支持单段 `bytes=`；多段与非法语法返回 416，不做 multipart/byteranges。
9. **内容变化检测**：原图被「同 size 同 mtime」替换时不会被发现（既有 P1-02/P1-03 限制，
   需要低优先级完整性复查）。
10. **打包产物的模型路径未复测**：本次打包 smoke 只覆盖 API/catalog/媒体路径（P0-05 的模型推理与
    GPU 打包 smoke 未重跑，产物也未被复制到 GPU 机器上验证）。`--collect-data photo_search` 只增加数据文件，
    不改变 DLL/ONNX Runtime 收集，但这一判断本身未在 GPU 路径上实测。
11. **未实现**（任务卡明确不做）：搜索/以图搜图、人物与人脸、OCR、模型/索引/outbox/升级回退 API、
    调度器 pause/resume、P2 全量任务协议、前端页面（P1-05）、重复/断盘/重映射验收（P1-06）。
12. **真实照片库**：本任务所有夹具都在 `%TEMP%` 下，未读取、未修改、未移动、未删除任何真实照片库。
13. **临时产物**：C 级 smoke 的工作目录（含一份 3.0 GB 的包副本与夹具）留在
    `%TEMP%\P1-04 打包 API 验收\`，可随时删除；`dist/PhotoSearch/` 为本次构建产物（已被 `.gitignore` 忽略）。

## 9. 交接记录

- `docs/development-status.md` 已更新：P1-04 = PASS，当前任务指向 **P1-05**。
- 下一任务卡：`docs/tasks/P1-05.md`（**P1-05 — 来源页、时间浏览、详情、离线提示**）。
- 推荐模型：**DS-L / DeepSeek V4.1 Flash / 低推理**（与本次 DS-H 不同 → 由用户手动切换）。
- 需 C1 复核：§6 的打包修复与「迁移脚本空集」判定的冻结语义解释（不影响 ADR-0002，但涉及打包与迁移自检）。
- 跨任务需要的信息：API 契约（§3）、受控访问的四层守卫（§3.4）、后台执行者与恢复语义（§2.4）、
  未验证项（§8）都写在本报告与任务卡里，新会话按
  `AGENTS.md → docs/development-status.md → docs/tasks/P1-05.md` 即可恢复上下文。
