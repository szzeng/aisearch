# P1-05 — 来源页、时间浏览、详情、离线提示

状态：**PASS**（B 级验收真实完成；按任务卡追加 **C 级打包验收**（PyInstaller onedir 实际构建 + 打包产物
smoke），并完成任务卡要求的**真实浏览器 smoke**（Codex 内置浏览器，真实端口，9 个必需场景 + 边界场景全部实测）。
基线：**`e1545ec76a954e93c1fed319ad86ef2342fcec81`**（P1-04 收口提交；`git log -1 --format=%H --
docs/reports/P1-04-*.md` = `4ed62233da7cbd50495f51d6733c17c2964bb2d4`）。
执行模型（记录用）：**DS-L / DeepSeek V4.1 Flash / 低推理**（与任务卡推荐一致）。模型切换始终由用户在
Codex 中手动完成，本任务未尝试、也未声称自动切换。

冻结契约：**ADR-0002 未修改**；`schema_version` 仍为 **1**（未新增迁移、未新增表、未改动
`0001_initial.sql`）；`pyproject.toml` / `uv.lock` **未改动**（`uv lock --check` → `Resolved 95 packages`）；
未改动 `models/**`、`runtime/**`、`workers/**`、模型清单、P0/P1-01~P1-04 报告；P1-02/P1-03/P1-04 的
稳定窗口、缺失判定、产物契约、原子发布、无参数 API 行为、cookie 属性、Host/Origin/CSRF 规则与受控路径
四层守卫全部保持不变；既有 P1-04 测试**未修改**（见 §4 的计数核对）。

---

## 1. 交付物清单

| 交付物 | 位置 |
|---|---|
| 前端静态资源（外壳 / 脚本 / 样式） | `src/photo_search/web/{__init__.py,index.html,app.js,styles.css}` |
| 静态服务路由 + 包内路径白名单解析 | `src/photo_search/api/web.py` |
| 会话 `next`（303）与 `assets` 的 `order` 加性参数 | `src/photo_search/api/routes.py` |
| `time_desc` 键集分页、独立游标空间 | `src/photo_search/api/queries.py` |
| `AssetOrder` 与响应回显字段 `order` | `src/photo_search/api/models.py` |
| `safe_redirect_target`（`next` 白名单校验） | `src/photo_search/api/session.py` |
| 新错误码 `WEB_ASSET_NOT_FOUND`（404） | `src/photo_search/api/errors.py` |
| 装配静态路由 | `src/photo_search/app.py` |
| 启动器会话 URL 追加 `next=/` | `src/photo_search/__main__.py` |
| 打包产物 smoke 扩展（外壳/脚本/跳转断言 + 正确解析打印链接） | `tools/diagnostics/packaged_api_smoke.py` |
| 单元测试（前端资源、next 校验、双游标空间） | `tests/unit/test_api_frontend.py`（42） |
| 集成测试（外壳/静态/会话跳转） | `tests/integration/test_api_frontend_routes.py`（4） |
| 集成测试（`order=time_desc` 分页与游标互斥） | `tests/integration/test_api_time_order.py`（4） |
| 本报告、状态更新、下一任务卡 | `docs/reports/P1-05-*.md`、`docs/development-status.md`、`docs/tasks/P1-06.md` |

新增测试 **50** 个。`tools/packaging/build_onedir.py` **无需修改**：P1-04 已加入的
`--collect-data photo_search` 会把 `web/**` 一并打进产物（§7 实测）。

## 2. 开始前必须先检查（任务卡第 1-5 项）的实测结论

1. **静态服务与打包**：现有业务路由前缀为 `/api/v1`（`APIRouter(prefix="/api/v1")`）；
   `SecurityMiddleware._guard` 只对 `path.startswith("/api/v1/")` 生效，`UNPROTECTED_PATHS` 仅含
   `/api/v1/health` 与 `/api/v1/session`，因此 `/` 与 `/web/*` 天然免会话；`build_onedir.py` 已含
   `--collect-data photo_search`（P1-04 §6.2），包内 `web/**` 随包分发（§7 打包 smoke 实测
   `GET /web/app.js` → 200、33,516 B）。**无需补充**。
2. **会话引导**：`GET /api/v1/session`（不带 `next`）仍为 200 + JSON + 两个 cookie，逐字节未变
   （既有 P1-04 测试 `test_session_exchange_sets_cookies_and_unlocks_reads` 未修改即通过）；
   `ps_csrf` 为非 HttpOnly（测试断言 + 浏览器内 `document.cookie` 读取成功并驱动 POST 成功）。
3. **分页现状**：默认仍是 `asset_id` 递增键集，游标 `base64url("v1:<asset_id>")`
   （`src/photo_search/api/queries.py`）。新增 `time_desc` 使用 `base64url("t1:<created_at>:<asset_id>")`，
   两种游标前缀不同 → 互相使用一律 422 `INVALID_CURSOR`（不做静默重解释）。
4. **数据语义**：`assets.created_at` 由 `INSERT INTO assets (sha256, byte_size, created_at, state)` 写入，
   即**入库时间**。本任务**未**新增 `taken_at` 列、**未**新增迁移；页面标题、页头说明、列表行与详情
   均标注「加入时间」并显式写出「不是 EXIF 拍摄时间」。
5. **离线语义**：列表用 `availability`（图库记录，来自最近一次完整扫描），详情用 `media[*].state/code`
   （产物状态），图片请求用响应头 `X-Original-Available`（实时可用性）。三者含义不同，页面分别使用；
   当实时可用性与图库记录不一致时（删除原图但尚未重扫），页面同时呈现两个事实并把图库字段标注为
   「图库记录（最近一次扫描）」，不把旧记录冒充当前状态。

## 3. API 与页面契约

### 3.1 服务器端（全部为加性扩展）

| 方法 | 路径 | 会话 | 说明 |
|---|---|---|---|
| GET | `/` | 免 | 返回包内 `index.html`；`Cache-Control: no-cache`；不含任何个人数据或本机路径 |
| GET | `/web/{path}` | 免 | 返回包内静态资源；短 `Cache-Control: max-age=300`；未知路径 404 `WEB_ASSET_NOT_FOUND` |
| GET | `/api/v1/session?token=&next=/sources` | 免 | 有安全 `next` → **303** + `Location: <next>` + 同样两个 cookie；无 `next` → 与 P1-04 完全一致的 200 JSON |
| GET | `/api/v1/assets?order=time_desc` | 需 | `(created_at DESC, asset_id DESC)` 键集分页；响应回显 `order`；`order=asset_id` 为默认且行为不变 |

- `next` 白名单（`safe_redirect_target`）：必须以单个 `/` 开头、不得为 `//` 开头、不得含 scheme
  （`://`）、不得含 CR/LF、反斜杠或任何控制字符；**不安全时在燃烧凭证之前**返回结构化 422
  `VALIDATION_ERROR`（`details={"param":"next"}`）且不带 `Location`，因此一次误拼链接不会消耗用户的一次性凭证。
- `order` 取值非法（如 `captured_at`）→ 422 `VALIDATION_ERROR`；`limit` 上限/下限行为与 P1-04 一致。
- 静态路由的路径解析只走 `importlib.resources.files("photo_search.web")`，每段必须匹配
  `^[A-Za-z0-9][A-Za-z0-9._-]*$`，因此 `..`、绝对路径、盘符、UNC、反斜杠与百分号解码后的分隔符
  都无法离开包目录（§5 场景 1 实测 404）。

### 3.2 页面（四块，纯静态、离线可用、无构建链）

- **来源页**：来源清单（显示名、状态、资产/在库位置/缺失位置计数、最近成功扫描的 scan #）、新增来源表单
  （`root_path` 仅由用户手工输入/粘贴，前端不枚举本机文件系统）、每来源「扫描」按钮 →
  `POST /sources/{id}/scan`（接受 202 与复用语义）→ 轮询 `GET /jobs?source_id=&limit=200` 显示
  `state`/`attempts`/`progress`/`last_error_code`，终态后刷新来源并按同一行位置留下终态摘要。
- **时间浏览**：`order=time_desc` 网格，默认 50/页，「加载更多」按钮 + `IntersectionObserver` 哨兵自动翻页，
  缩略图 `loading="lazy"` + 观察器按需 `fetch`；可按来源筛选；空库/加载中/错误三态均有明确文案；
  该维度全程标注为「加入时间」。
- **详情**：预览（`/preview` 失败回退 `/thumbnail`，两者的失败 code 都可见）、全部位置
  （来源显示名、相对路径、可用性、大小、mtime）、每种产物状态与结构化 `code`、「打开原图」链接指向
  `/api/v1/assets/{id}/original`（同源导航天然带 cookie，实测 409 `SOURCE_FILE_MISSING`）。
- **离线提示**：`availability=missing` 或 `X-Original-Available: false` 的资产仍显示已有缩略图并标注
  「原文件不可用」；无产物且原图不可用时显示 `SOURCE_FILE_MISSING`/`DECODE_FAILED`/`FORMAT_UNSUPPORTED`
  code，不出现破图或空白占位（图片解码失败也会落到 `IMAGE_UNREADABLE` 文本）。

### 3.3 前端会话与安全

- CSRF 只从 `ps_csrf` cookie 读取并放入 `X-CSRF-Token`；所有请求 `credentials: "same-origin"`；
  变更请求带 `Origin`（同源自动带，未伪造）。
- 令牌不写入 `localStorage`/`sessionStorage`/URL hash/控制台（`app.js` 内**没有** `console.`、
  `localStorage`、`sessionStorage` 字样，单元测试断言）。
- 页面只渲染来源显示名与来源相对路径；`root_path` 只出现在用户自己填写的输入框里；静态资源与外壳
  不含任何 `http://`/`https://` 外部引用（无 CDN、无在线字体、无内联脚本），favicon 用 `data:,` 抑制请求。
- 收到 401 `SESSION_REQUIRED`/`SESSION_INVALID` → 显示「请使用启动器打印的一次性链接建立会话」，
  不自动重试、不隐藏错误；所有结构化错误都把 `code`/`message`/`request_id` 显示出来。

### 3.4 一处显式偏离任务卡字面、理由与等价性（重要）

任务卡写「启动器打印的会话 URL **追加 `&next=/`**」，字面形式是
`?token=<token>&next=/`。本实现打印 **`?next=/&token=<token>`**（`next` 在前、凭证在后）。原因：
P1-04 既有测试 `tests/integration/test_api_server.py` 用
`bootstrap.split("token=", 1)[1].strip()` 提取凭证并与 `<data_root>/runtime/session.json` 逐字节比较；
若把凭证放在中间，该断言会读到 `"<token>&next=/"` 而失败，而任务卡的**不变量**要求「既有 P1-04 测试
不修改即通过」「不带 `next` 时行为与 P1-04 既有测试完全一致」。两种顺序在浏览器里完全等价（都是 303 →
`/`），凭证仍是最后一个查询参数，访问日志脱敏规则 `[?&](token|csrf_token)=` 对 `&token=` 同样生效
（实测 access 行为 `GET /api/v1/session?next=%2F&token=<redacted>`），打包 smoke 已把「打印链接可直接用」
作为断言（`session_exchange=303`、`session_redirect_location="/"`、`bootstrap_next="/"`）。

## 4. 真实执行的命令与退出码

| 命令 | 退出码 | 摘要 |
|---|---|---|
| `uv run pytest -q tests/unit tests/integration` | 0 | 收集 **304**（单元 196 + 集成 108），**303 passed, 1 skipped**（skip 为既有 `PHOTO_SEARCH_GPU_TESTS` 门禁用例） |
| focused：`tests/unit/test_api_frontend.py`、`tests/integration/test_api_frontend_routes.py`、`tests/integration/test_api_time_order.py` | 0 | `42 + 4 + 4 = 50 passed` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | 无空白错误 |
| `uv lock --check` | 0 | `Resolved 95 packages`（依赖未变） |
| `node --check src/photo_search/web/app.js` | 0 | 前端脚本语法有效 |
| `uv run python tools/packaging/build_onedir.py`（C 级） | 0 | ~62 s，`dist/PhotoSearch/PhotoSearch.exe` 34,020,895 B，随包 21 个 NVIDIA DLL |
| `uv run python tools/diagnostics/packaged_api_smoke.py`（C 级） | 0 | `status=ok`、`problems=[]`，启动 1.0 s（§7 明细） |
| 真实浏览器 smoke（Codex 内置浏览器，真实端口） | — | 9 个必需场景全部实测（§6） |

计数核对（诚实记录）：P1-04 报告把 `tests/integration/test_api_access.py` 记为 9 个用例，实际收集 **10** 个，
因此继承基线是 **253 passed / 1 skipped（254 collected）**，加上本任务 50 个即 304 collected / 1 skipped /
303 passed。仓库文件为准，未修改任何既有用例。

## 5. 任务卡要求真实执行场景与映射

1. **静态服务**（`GET /` 200、无外部引用、静态脚本 200、无令牌/绝对路径）：
   `test_the_shell_and_its_assets_are_public_while_the_api_is_not`（含 `%2e%2e` 与深层 `../` 404）、
   `tests/unit/test_api_frontend.py::test_the_shipped_shell_has_no_external_reference_or_credential`
   （对 `index.html`/`app.js`/`styles.css` 三个文件断言无 `http://`/`https://`）、打包 smoke
   （`shell_status=200`、`shell_content_type=text/html; charset=utf-8`、`shell_cache_control=no-cache`、
   `shell_has_no_external_reference=true`、`shell_has_no_local_path=true`、`web_script_status=200`、
   `web_style_status=200`、`web_missing_status=404`）+ 浏览器实测。
2. **会话**：`test_session_exchange_without_next_keeps_the_p1_04_json`、`test_session_exchange_with_next_redirects_onto_the_shell`、`test_an_unsafe_next_is_refused_without_burning_the_credential`（`http://evil.example`、`https://…`、`//evil.example`、`/\evil.example`、`/a\r\nb`、空串）+
   `tests/unit/test_api_frontend.py` 的 11 个参数化拒绝用例；浏览器实测 303 → `/` 落地。
3. **时间分页**：`tests/integration/test_api_time_order.py`（26 资产 `limit=7` 不重不漏且严格
   `(created_at DESC, asset_id DESC)`；同 `created_at` 按 `asset_id` 稳定；两种游标互用 → 422
   `INVALID_CURSOR`；空库/`limit=5000`→200/`limit=0`→422/非法 `order`→422/来源筛选）。
4. **来源流程**：既有 `test_sources_create_list_and_structured_rejections`（重叠 409 `SOURCE_OVERLAP`、
   数据目录内 409 `DATA_ROOT_INSIDE_SOURCE`）、`test_scan_request_returns_202_and_completes_in_the_background`
   （既有）+ 浏览器实测「注册 → 扫描 → 进度 → SUCCEEDED」。
5. **详情与离线**：`test_asset_detail_and_unknown_ids`（既有）、P1-04 的
   `test_offline_source_keeps_artifacts_and_reports_the_missing_original`（既有，覆盖
   `X-Original-Available: false` 与 409 `SOURCE_FILE_MISSING`）+ 浏览器实测（§6 步骤 5-7，含真实浏览器里
   打开 `/original` 得到 `{"code":"SOURCE_FILE_MISSING",...}`）。
6. **坏图/不支持格式的可见 code**：既有 `test_corrupt_and_unsupported_inputs_report_recorded_codes`
   + 浏览器实测（`DECODE_FAILED` 可见文本、无破图，§6 步骤 8）。
7. **边界**：空库/单资产/多页/中文/空格/长路径/只读原图沿用 P1-04 既有用例
   （`test_asset_paging_is_complete_ordered_and_bounded`、`test_unicode_space_and_long_paths_with_read_only_originals` 等）
   + 本任务 `test_order_bounds_and_empty_library` + 浏览器窄视口（640×900，无横向溢出、3 列重排）与键盘 Tab/Enter（§6 步骤 9）。
8. **真实浏览器 smoke**：§6（本会话使用 Codex 内置浏览器，真实端口；未标记 `VALIDATION PENDING`）。
9. **更高 `schema_version`**：既有 `test_higher_schema_version_is_structured_and_has_no_write_side_effects`
   （读接口 409 `SCHEMA_TOO_NEW`、无写入副作用、无 `-wal`/`-shm`）+ **本任务在真实浏览器实测页面提示**
   （§6 步骤 10）。

## 6. 真实浏览器 smoke：步骤与观察

环境：Codex 内置浏览器（In-app Browser）；`python -m photo_search --port <真实端口> --data-root
%TEMP%\P1-05 前端验收\PhotoSearchData 定稿`；来源库为 `%TEMP%\P1-05 前端验收\照片库 中文 空格`
（2061 个合成 JPEG：60 个独立文件 + 2000 个 `bulk/*.jpg`，另有 1 个 1 字节的 `corrupt.jpg`），
全部为 `%TEMP%` 下的合成夹具，**未读取任何真实照片库**。同一记录轮次内共用一个来源与数据目录。

| # | 步骤 | 实际观察 |
|---|---|---|
| 1 | 打开启动器打印的一次性链接（`/api/v1/session?next=/&token=…`） | 303 → 落在 `http://127.0.0.1:<port>/`，标题「本机图片搜索」，页头显示「版本 0.1.0 · 图库 schema 尚未初始化（uninitialised） · 只读受控访问」；来源页显示「还没有登记来源」引导文案，不是空白页 |
| 2 | 填写显示名 `示例图库` + 来源根目录（中文/空格绝对路径，手工粘贴），点「注册来源」 | `POST /sources` → 201；卡片出现「示例图库 / 来源编号 #1 / 状态 在线 / 资产 0 / 在库位置 0 / 缺失位置 0 / 最近成功扫描 尚无成功扫描」；**列表未渲染任何绝对路径**（表单输入框保留用户自己填写的内容） |
| 3 | 点「扫描」 | 先显示「扫描已排队（scan #1，job #1）」；随后同一行显示「扫描状态 RUNNING · 尝试 0/3 · 进度 14%」→（t+0.5 s）「…进度 65%」→（终态）「扫描已结束。扫描状态 SUCCEEDED · 尝试 0/3 · 进度 100%」，来源卡片刷新为「资产 312 / 在库位置 2054 / 缺失位置 0 / 最近成功扫描 扫描 #1」（312 个资产来自内容寻址：2000 个 `bulk` 文件按颜色公式每 251 个内容相同 → 多位置共享同一资产） |
| 4 | 切到「按加入时间浏览」 | 「已加载 50 张（按加入时间从新到旧），还有更多。」；首屏仅可见图块有 `img`（`IntersectionObserver` 按需加载生效），缩略图 `alt` 为「缩略图：bulk\b0250.jpg」（**来源相对路径**）；点「加载更多」→「已加载 100 张（按加入时间从新到旧），还有更多。」 |
| 5 | 点首图「查看详情」 | 预览渲染成功（`alt=预览：bulk\b0250.jpg`）、身份与时间（资产 #312 / 64 位指纹 / 1.1 KiB / 加入时间「（入库时间，不是拍摄时间）」/ 图库记录（最近一次扫描）在库 / 位置数量 7）、全部 7 个位置（来源显示名 + 相对路径 + 可用性（最近扫描）+ 大小 + mtime）、产物状态「缩略图/预览 已生成」、「打开原图」链接 href=`/api/v1/assets/312/original` |
| 6 | 删除该资产的 7 个原文件后点「刷新」（**未重扫**） | 首页第一块显示徽标「原文件不可用」**且缩略图仍在显示**（`img` 数量 1）；详情页徽标 + 「预览可用；原文件当前不可用。」，两件产物仍为「已生成」，同时「图库记录（最近一次扫描）」仍如实显示「在库」——实时事实与库记录两个口径分别呈现，不混淆 |
| 7 | 点「打开原图」（新标签） | 返回真实结构化错误：`{"code":"SOURCE_FILE_MISSING","message":"no registered position of this asset is currently marked present","request_id":"5cadbb7e6c52bc4d","details":{"asset_id":312,"positions":7}}`；随后点「扫描」重扫（扫描 #2）→ 来源卡片显示「缺失位置 7」，网格图块标注「原文件不可用」、详情「图库记录（最近一次扫描）」变为「原文件不可用」，7 个位置全部显示「缺失」 |
| 8 | 打开 `corrupt.jpg`（资产 #1）详情 | 无破图：显示「没有任何可用产物：DECODE_FAILED」；产物状态「缩略图/预览：生成失败：代码 DECODE_FAILED（错误详情包含本机路径，已省略。）」；对渲染后的 DOM 断言**不含任何绝对路径**（`/[A-Za-z]:\\|\/Users\//` → false） |
| 9 | 窄视口 640×900 + 键盘 | `document.documentElement.scrollWidth=625 ≤ innerWidth=640`（无横向溢出）、网格重排为 3 列（各 ~187 px）、50 块 + 状态文案正常；Tab 依次聚焦 `select`→「刷新」→各「查看详情/查看离线详情」按钮（DOM 顺序可达），对聚焦按钮按 Enter 打开详情（URL 变为 `#/asset/301`，预览 `alt=预览：bulk\b0239.jpg`）；图片 `alt` 文本始终存在 |
| 10 | 更高 `schema_version` 的库（`schema_version=2`） | 打开页面即显示结构化提示「图库的 schema 版本高于本程序支持的版本（SCHEMA_TOO_NEW）。请升级程序或改用匹配的数据目录；本页面不会显示空图库来误导你。（请求编号 …）」；来源页显示「读取来源失败：[SCHEMA_TOO_NEW] catalog schema_version 2 is newer than this build supports (1); refusing to write（请求编号 …）」而**不是**空列表；请求后 `catalog/` 目录仍只有 `catalog.db`（无 `-wal`/`-shm`） |

未出现失败；唯一需要说明的是：第一轮探索用 61 文件库时，扫描在浏览器第一次轮询（约 0.7 s）前就已
`SUCCEEDED`，因此改用 2061 文件夹具后重复第 3 步，真实观察到 `RUNNING 14% → 65% → SUCCEEDED 100%`。

## 7. 静态资源与打包证据（C 级）

打包命令与产物：`uv run python tools/packaging/build_onedir.py` 退出码 0（约 62 s），产物
`dist/PhotoSearch/PhotoSearch.exe` = **34,020,895 B**，`nvidia_dlls/` 21 个 DLL；未改动
`build_onedir.py`（P1-04 的 `--collect-data photo_search` 已经覆盖新增的 `web/**` 数据文件）。

`uv run python tools/diagnostics/packaged_api_smoke.py` 退出码 0，`status=ok`、`problems=[]`；工作目录
`%TEMP%\P1-05 打包页面验收\PhotoSearch 中文 空格`（中文 + 空格安装路径与中文 + 空格数据/来源目录）：

| 检查 | 实测 |
|---|---|
| 启动 | 1.0 s 后打印 listening + 一次性会话 URL（`bootstrap_next=/`） |
| 打包产物上的 `GET /` | 200，`text/html; charset=utf-8`，`Cache-Control: no-cache`，无外部引用、无本机路径 |
| 打包产物上的 `GET /web/app.js` / `styles.css` | 200 / 200（脚本 33,516 B，`text/javascript; charset=utf-8`） |
| 未知静态资源 `/web/does-not-exist.js` | 404 |
| 打印链接直接使用 | 303 → `Location: /`；`ps_session` 含 `HttpOnly`、`Path=/`；`ps_csrf` 可被前端读取（`csrf_cookie_readable=true`） |
| 未带会话读 `/api/v1/stats` | 401；同一凭证二次交换 401 |
| 来源注册 / 扫描 / 资产 | 201 / 202 → `SUCCEEDED` / 1 个资产 |
| 缩略图 | 200 `image/jpeg` 320×240、1,829 B、`X-Original-Available: true` |
| 删除原图后缩略图 / 原图 | 200 `X-Original-Available: false` / 409 `SOURCE_FILE_MISSING` |
| 访问日志 | 18 行 access 日志全部 `token=<redacted>`；stderr 无令牌 |
| 其他 | 健康检查 200、详情 200、capabilities 200 |

## 8. 未验证项、限制与「不可声称」

1. **API 侧脱敏漏网（发现的问题，未在本任务修改）**：P1-03 的媒体失败记录里，Pillow 的消息带的是
   **repr 转义**路径（`cannot identify image file 'C:\\Users\\…\\corrupt.jpg'`，分隔符是两个反斜杠字符），
   而 `Redactor` 只按登记根的单反斜杠字面量替换，所以 `GET /api/v1/assets/{id}` 的 `media[*].message`
   可能仍含绝对路径。任务卡的允许清单把 `api/**` 限制为三个加性扩展，故**未**改服务端脱敏；
   页面侧已自行拦截（`PATH_LIKE` 检测到盘符/UNC 形态即整条消息替换为「错误详情包含本机路径，已省略。」，
   单元测试断言该守卫存在）。建议后续由 **C1 / GPT-5.6 Terra / medium** 复核并决定是否在
   `errors.Redactor` 内补上「双反斜杠/repr 转义」匹配（属行为修复，不改变错误码与状态映射）。
2. **前端无自动化 DOM 测试**：仓库不允许引入 Node/npm 打包链，故页面的行为由静态断言（资源内容、
   无外部引用、无令牌存储）+ 本报告记录的真实浏览器 smoke 覆盖；没有可回归的 DOM 断言。
3. **窄视口 ≠ 真实 200% 缩放**：用 640×900 视口 + 无横向溢出作为代理，未做操作系统级 200% 缩放实测。
4. **`time_desc` 只按入库时间**：`created_at` 是入库时间；按拍摄时间排序需要按 ADR-0002 §7 的加列迁移
   （`taken_at`）+ C2 决策，本任务明确不做。
5. **缩略图缓存与实时可用性**：前端对产物请求使用 `cache: "no-cache"`（每次条件复核，服务端对 304
   仍返回最新的 `X-Original-Available`），因此删除原图后无需重扫即可看到「原文件不可用」；代价是每次
   浏览会做一次 304 复核（本机开销可忽略）。产物字节本身仍是 `immutable`。若未来要求离线浏览已缓存
   缩略图而不触碰服务端，需要单独设计。
6. **详情页产物状态是快照**：`media[*].state` 来自详情请求时刻；若该次请求后才产生失败记录，
   产物区仍显示「待生成」，而图上方的可见 code（来自产物响应）显示 `DECODE_FAILED`；重新渲染后
   产物区即为「生成失败：代码 DECODE_FAILED」（已实测）。未做失败后的局部增量刷新。
7. **完全离线资产在网格里没有相对路径**：`GET /assets` 的 `current_location` 只在存在 PRESENT 位置时
   给出，因此全部位置缺失的资产在图块里回退为「资产 #N」（详情页仍列出全部位置的相对路径）。
   未新增字段（不越出允许的加性扩展范围）。
8. **`jobs.attempts` 语义**：首次即成功的扫描显示「尝试 0/3」（该字段只在重试排队时自增），页面原样展示，
   不做美化。
9. **继承 P1-04 的未验证项**：会话仅内存、无 TTL/登出、无局域网鉴权；`GET /stats` 仍为 O(assets)；
   媒体生成仍在事件循环线程；同进程双连接只做顺序竞争验证；`assets.width/height/format` 仍为 NULL。
   （本次在**非沙箱**运行下 `icacls` 成功，凭证文件权限实测为 `windows-restricted-acl`，可作为 P1-04 §8.1
   的补充证据，但仍需在真实用户机复验。）
10. **本任务未实现**：搜索/以图搜图、人物与人脸、OCR、模型/索引/outbox/升级回退 API、调度器
    pause/resume、P2 全量任务协议、重复/断盘/重映射验收（P1-06）。
11. **临时产物**：浏览器 smoke 与打包 smoke 的夹具/产物留在
    `%TEMP%\P1-05 前端验收\`、`%TEMP%\P1-05 打包页面验收\`（含一份包副本）与 `dist/PhotoSearch/`
    （已被 `.gitignore` 忽略），可随时删除；**未**读取、修改、移动或删除任何真实照片库。

## 9. 环境偏差（沙箱限定，非仓库缺陷）

1. `uv run` 默认缓存目录 `%LOCALAPPDATA%\uv\cache` 在本沙箱不可写
   （`failed to open file …\.git: 拒绝访问 (os error 5)`），故本次所有 `uv run` 均显式设置
   `UV_CACHE_DIR=%TEMP%\uv-cache-aisearch`。
2. 沙箱内创建/列举 `%TEMP%` 下 `mode 0700` 目录被拒（`WinError 5`），pytest 的 `tmp_path` 因此不可用；
   B 级套件在沙箱外（escalated）执行，夹具仍落在 `%TEMP%`，符合任务卡要求。
3. 仓库 `.git` 与既有 `.pytest_cache` 在本沙箱为只读，故 `git diff --check` / 提交与
   `ruff`（写 `.ruff_cache`）在沙箱外执行。

## 10. 交接记录

- `docs/development-status.md` 已更新：P1-05 = PASS，当前任务指向 **P1-06**。
- 下一任务卡：`docs/tasks/P1-06.md`（**P1-06 — 重复、移动、替换、断盘、重映射、重启验收**）。
- 推荐模型：**DS-H / DeepSeek V4.1 Flash / 高推理**（与本任务 DS-L 不同 → 由用户手动切换）。
- 建议 C1 复核项：§8.1 的脱敏漏网（repr 转义路径）与 §7 的打包静态资源收集是否需要在
  `build_onedir.py` 之外再加固（当前实测已进包）。
- 跨任务需要的信息：API 契约（§3.1）、页面契约与四块功能（§3.2）、前端安全规则（§3.3）、
  打印链接参数顺序的偏离与理由（§3.4）、未验证项（§8）均写在本报告与任务卡中；新会话按
  `AGENTS.md → docs/development-status.md → docs/tasks/P1-06.md` 即可恢复上下文。
