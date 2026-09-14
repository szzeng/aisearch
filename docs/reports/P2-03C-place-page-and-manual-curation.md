# P2-03C — 静态地点页与人工整理交互报告

日期：2026-09-14。状态：**PASS（B 级 + 真实浏览器 smoke + C 级打包 smoke）**。

契约由 ADR-0005 与 `contracts/p2-place-contracts.v1.json` 冻结；本任务只做页面与人工整理交互，
没有重新定义状态、错误码、事务边界，也没有新增后端能力。所有夹具都是 `%TEMP%` 下的合成图片与
合成地名包，没有读取、移动或删除真实图库/真实地名包，没有联网。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `f4959a200c0ec1d19b3d6b4fe41807e6207edcde`（P2-03B），开始时工作树 clean |
| 前置 | `SUPPORTED_SCHEMA_VERSION = 5`，migration 连续 0001～0005，P2-03A/B 的目录、GPS、建议、人工归属、批量、撤销、数据包 API 已可用 |
| 新页面 | `/places` ← `src/photo_search/web/places.html` + `places.js` + `places.css`（既有 package-data 目录） |
| 页面入口 | `src/photo_search/api/web.py`：`PLACES_NAME = "places.html"` 与 `GET /places`；`/api/v1/places` 不受影响 |
| 外壳改动 | `index.html` 导航新增「地点整理」链接；`styles.css` 新增 `.nav-link` |
| 新增测试 | `tests/integration/test_p2_place_page.py` 9 例；`tests/unit/test_api_frontend.py` +8 例；`tests/integration/test_api_frontend_routes.py` +1 例 |
| 新增/改动工具 | `tools/diagnostics/places_smoke_fixture.py`（新）；`tools/diagnostics/packaged_api_smoke.py`（地点页检查） |
| 文档 | `docs/diagnostics/frontend-smoke-checklist.md` 新增 §6「地点整理页」 |

未修改：ADR-0001～0005 与冻结 JSON schema、`catalog/migrations/0001～0005`、`schema_version`、
五字段 job 身份、lease/attempts/迟到提交、资产/位置/缺失判定、P2-03A/B 的人工锁/排除/幂等/批量/
撤销语义、`catalog_filter_revision`/`suggestion_revision` 触发矩阵、候选 `SUGGESTION_ONLY` 语义、
错误码、`pyproject.toml`、`uv.lock`、`workers/**`、打包入口。没有实现 FTS（P2-04）、语义融合/
search session（P2-05/06）、地点合并、reparent、行政边界自动判断或景点自动识别。

## 2. 页面与路由清单

| 路由 | 会话 | 说明 |
|---|---|---|
| `GET /places` | 不需要 | 地点整理页文档；`Cache-Control: no-cache`；无外链、无用户数据、无本机路径 |
| `GET /web/places.js`、`/web/places.css` | 不需要 | 页面脚本/样式，`max-age=300`（与既有 `app.js`/`styles.css` 同规则） |
| `GET /web/{index.html,places.html}` | 不需要 | 两个文档都按外壳缓存策略（`no-cache`）重校 |
| `GET /api/v1/places…` 等全部地点接口 | 需要 session | 未改动；页面只是调用方 |

页面自身调用的接口（全部经 `/api/v1`，无第二个入口）：

`GET /capabilities`、`GET /places`（name/kind/lifecycle/parent/ancestor/limit/cursor）、
`POST /places`、`PATCH /places/{id}`、`GET /places/datasets`、`GET /assets?place_state=suggested…`、
`GET /assets/{asset_id}/place`、`PUT /assets/{asset_id}/place-assignment`、
`POST /place-assignments/batch`、`GET /place-operations`、`POST /place-operations/{id}/undo`。
`GET /assets/{asset_id}/geo` **不在**调用清单内（见 §6 步骤 10 与 §5 隐私断言）。

## 3. 交互实现要点

| 需求 | 实现 |
|---|---|
| 目录浏览 | 名称/别名、kind、lifecycle、父级、祖先与每页条数（10/25/50/200）；keyset 游标原样携带，服务端按目录 revision 校验 |
| 游标过期 | `CATALOG_FILTER_REVISION_STALE` → 「目录已变化，需重新加载」+ 结构化错误；不拼接两版结果 |
| 同名消歧 | 每行显示 `kind`、规范名、稳定 `#place_id`、生命周期、origin，以及**完整祖先链**（父级未载入时显示 `#id` 而不是猜测名称）；「只看它的后代」把 `ancestor` 过滤填入并清空名称/父级条件 |
| 单地点读取 | `GET /places?ancestor=<id>&limit=1`（冻结 API 没有 `GET /places/{id}`；后代展开是含自身的，且按 `place_id` 升序，因此首行就是该地点） |
| 创建 | 只提供 `city/attraction/custom`；`country` 由数据包提供（服务端同样拒绝手工创建 country） |
| 改名/别名 | 必须带 `expected_revision`；别名按行 `别名 \| 语言`，编辑即替换整组 ACTIVE 别名并提示「含来自数据包的别名」；冲突显示 `current_revision` 并要求重新载入 |
| 建议列表 | `place_state=suggested` + 列表绑定 `suggestion_revision`；候选行固定标注 `SUGGESTION_ONLY`，同时显示距离/依据/disposition 与「当前有效归属」「控制行 mode/origin/revision/manual_lock」 |
| 确认建议 | 单条：`PUT …/place-assignment`，`action=assign` + `origin=CONFIRMED_SUGGESTION`；批量：`POST /place-assignments/batch` 每批 ≤500、每批独立 `request_id`、整批原子、逐批结果 |
| 快照 | 「冻结全部结果快照」先遍历当前 `suggestion_revision` 下的结果冻结 `asset_id`（上限 2000），提交时不重跑动态查询 |
| 撤销 | 操作日志（按 revision 升序追加）→ 对 COMMITTED 操作发 `request_id` + `expected_operation_revision`；成功显示新 `UNDO` 操作并刷新目录/建议，`PLACE_OPERATION_NOT_UNDOABLE`/`PLACE_IDEMPOTENCY_CONFLICT` 都有可读提示 |
| 数据包面板 | 真实投影：`unavailable/installed/active/missing/invalid`、`reason`、`resolver_available`、活动包、已导入包与 `problems`、`suggestion_revision`；无包时不伪造 ACTIVE |
| 隐私 | 不渲染绝对路径（服务端文本先经 `PATH_LIKE` 过滤），不请求坐标端点，不写 `localStorage/sessionStorage`，不用 `innerHTML` |

## 4. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0，148.6 s；`--collect-only` 计 **564** 例，进度行 1 个 `s` → **563 passed / 1 skipped**（沿用既有 skip） |
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit/test_api_frontend.py tests/integration/test_api_frontend_routes.py tests/integration/test_p2_place_page.py` | exit 0，**68 passed**（P2-03C 新增 18 例：页面静态契约 8、页面路由 1、页面请求形状 9） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |

本环境已记录的同类事实沿用前几个任务：`uv` 默认缓存在沙箱内被拒绝（本次使用
`%TEMP%\uv-cache-p2-03c`），pytest 的 basetemp 在沙箱内被 ACL 拒绝，因此测试在沙箱外运行。

## 5. 新增测试覆盖（页面契约，不代表 DOM 自动化）

`tests/integration/test_p2_place_page.py`（9 例，全部走真实 `/api/v1`）：

1. 目录游标绑定 revision：分页成功 → 改名（`PLACE_NAME_CHANGED`）后旧游标 409
   `CATALOG_FILTER_REVISION_STALE`（`details.current_revision`）。
2. `?ancestor=<id>&limit=1` 能读回该地点本身；子树查询只列后代；未知祖先成功空列表。
3. 改名要求当前 revision（成功后 +1 且别名保留）；旧 revision 409 `PLACE_ASSIGNMENT_CONFLICT`
   （带 `current_revision`/`expected_revision`）；`parent_place_id` 422 `VALIDATION_ERROR`；
   手工创建 `country` 422。
4. 同名地点保持独立身份（两条 `place_id`、两个不同父级），无自动合并。
5. 建议列表绑定 `suggestion_revision`：确认一条后旧 revision 续页 409
   `PLACE_SUGGESTION_REVISION_STALE`；控制行 `mode=MANUAL_ASSIGNED`、`origin=CONFIRMED_SUGGESTION`、
   `manual_lock=true`；确认后的资产离开列表。
6. 批量 ≤500 原子性：2 项 `BATCH_ASSIGN` 一次提交只推进一次 `catalog_filter_revision`；同
   `request_id` + 同 payload 重放返回原 operation（`replayed=true`）且 revision 不变；过期
   `expected_revision` 409 且零写入；501 项 422 `PLACE_BATCH_TOO_LARGE`（`max_batch_size=500`）。
7. 撤销补偿：新 `UNDO` 操作、`undo_of_operation_id` 指向原操作、资产回到可见建议；
   目标被后续编辑后整批 409 `PLACE_OPERATION_NOT_UNDOABLE`；`expected_operation_revision` 不符同样 409；
   复用同一 `request_id` 换 payload 409 `PLACE_IDEMPOTENCY_CONFLICT`。
8. 缺包降级：未安装 `unavailable`/`PLACE_DATASET_NOT_INSTALLED`、无 `active_dataset`；删除活动包文件后
   `missing`/`PLACE_DATASET_MISSING`（`active_dataset.problems[0].code`），而人工归属、目录查询、
   新建地点继续可用，无 GPS 的照片也能人工归属。
9. 隐私与边界：`/places`、`/places/datasets`、`/assets/{id}/place`、`/place-operations` 的响应中
   没有任何坐标字段与绝对路径，而同一条 GPS 事实在 `/assets/{id}/geo` 上确实带
   `latitude/longitude`（证明断言非空）；缺 CSRF 403 `CSRF_INVALID`、缺 Origin 403
   `ORIGIN_NOT_ALLOWED`，且两次拒绝零写入（revision 不变、操作日志仍为空）。

`tests/unit/test_api_frontend.py` 新增 8 例：地点页三个资产可读、`/places` 不遮蔽 `/api/v1/places`、
无外链/无凭证落存储/无 `innerHTML`、`fetch(` 与 `fetch(API +` 计数相等、不出现 `/geo` 与坐标字段、
冻结词表（`CATALOG_FILTER_REVISION_STALE`、`PLACE_SUGGESTION_REVISION_STALE`、
`CONFIRMED_SUGGESTION`、`restore_auto`、`PLACE_OPERATION_NOT_UNDOABLE`、`PLACE_IDEMPOTENCY_CONFLICT`、
`PLACE_ASSIGNMENT_CONFLICT`、`PLACE_BATCH_TOO_LARGE`、`MAX_BATCH = 500`、`SUGGESTION_ONLY`、
`asset_id 快照`）与诚实的 provenance 文案。`tests/integration/test_api_frontend_routes.py` 新增 1 例：
`/places` 公开、`/web/places.js|css` 200、外壳链接到 `/places`、`/api/v1/places` 无会话 401。

## 6. 真实浏览器 smoke（内置浏览器 + 真实端口 127.0.0.1:8732）

夹具：`uv run --no-sync python tools/diagnostics/places_smoke_fixture.py --reset`
→ 14 张合成 JPEG（12 张杭州附近、1 张东海、1 张无 GPS）、真实扫描、安装并激活
`tools/places/examples/mini-cities.spec.json` 构建的合成包、跑真实 GPS 与「附近城市」处理器
（14 条 GPS 事实 / 14 条 resolution / 12 条可见建议），并预置目录（中国 #1、杭州 #2、上海 #3、
甲市 #4、乙市 #5、#6/#7 两个「同名地点」、我的标记 #8）。服务用
`uv run --no-sync python -m photo_search --port 8732 --data-root <夹具数据根>` 启动，走启动器打印的
一次性链接落地 `/`。

| # | 实际观察 | 结果 |
|---|---|---|
| 1 | 一次性链接 303 → `/`，标题「本机图片搜索」，导航出现「地点整理」；点击进入 `/places`，标题「地点整理」，地名包面板显示 `已激活（active）`、活动包 `photo-search-mini-cities-package @1`、`suggestion_revision #12`；页面无外链、无本机路径 | PASS |
| 2 | 新建 `city`「烟雨城」→ 目录出现 `城市 烟雨城 #9 / origin USER / revision 1`；载入 #9 后改名成功（revision 1→2，别名 `Yanyu City（en）` 保留，标题同步刷新为「烟雨城新区」）；再用旧 `expected_revision=1` 提交 → `[PLACE_ASSIGNMENT_CONFLICT]` + 「当前 revision 是 2，本次提交的 expected_revision 是 1。请点「重新载入」后重试。」 | PASS |
| 3 | 名称搜「同名地点」→ 两行 `#6`/`#7` 各自显示祖先链（`中国 #1 › 甲市 #4 › 同名地点 #6`、`中国 #1 › 乙市 #5 › 同名地点 #7`）与稳定 ID，无合并；「只看它的后代」清空名称条件、填入 `ancestor=6` 并列出 `#6` 与 `我的标记 #8` | PASS |
| 4 | 建议面板加载 12 条；展开首条显示「当前有效归属 无（只有建议…）」「无控制行（expected_revision 0）」「候选 1 · 杭州 · 0 m · NEARBY_CITY_POINT / DISTANCE_ONLY · SUGGESTION_ONLY · 本地 place #2」等；点「确认第一条建议」→ 结果区保留「单条确认：已确认建议：资产 #3 → place #2 … origin=CONFIRMED_SUGGESTION，operation_revision 2」，列表 11→10 条且 `suggestion_revision #13→#14` | PASS |
| 5 | 目标留空 + 未勾选 → 「请先勾选资产…」；勾选 9 项并搜索「杭州」选定目标 #2 后确认 → 「第 1 批（10 项）：已提交 BATCH_ASSIGN · operation op-… · changed 是 · 重放 否」，列表清空；用键盘清空目标输入框后再确认 → 「批量确认归属必须填写目标 place_id（可先用名称搜索填入）。」且结果区不新增、无请求发出 | PASS |
| 6 | 每页条数 10 加载（revision #16→#17 快照）→ 另一标签页确认一条建议（#16→#17）→ 回本页点「冻结全部结果快照」→ 「建议集合已变化，需重新加载：[PLACE_SUGGESTION_REVISION_STALE] …（请求编号 …）」，列表不拼接两版 | PASS |
| 7 | 操作日志 4→6 条（ASSIGN rev1/2、BATCH_ASSIGN rev3、UNDO rev4、ASSIGN rev5、BATCH_ASSIGN rev6，升序）；撤销 rev3 成功 →「新操作 op-…（UNDO）补偿了 op-…，changed 是」，建议 10 条回到列表（revision #16）；再对同一条（已 REVERSED）撤销 → `[PLACE_OPERATION_NOT_UNDOABLE]` + 「该操作已被撤销，或它的目标已被后续编辑改变，整批都不再可撤销…已刷新目录与建议。」 | PASS |
| 8 | 删除活动包 `places.jsonl` 后点「刷新地名包状态」→ 「地名包状态：包文件缺失（missing）」「解析器可用 不可用（新解析会被阻塞）」「state reason PLACE_DATASET_MISSING」+ `[PLACE_DATASET_MISSING] a declared dataset file is missing`，并提示「「没有建议」不等于「已归类」」；目录查询（无筛选 9 个地点）、改名、人工归属照常；既有 9 条建议仍可读（只有新解析阻塞，符合 P2-03B §10.6） | PASS |
| 9 | Tab 序列可达 `create-kind/name/parent/aliases`、`edit-*`、`suggested-limit`、`suggested-load` 等控件；对 `加载 / 重新加载（取最新 revision）` 按 Enter 成功重载建议（状态行刷新）。横向溢出：513 个元素中 **0** 个 `scrollWidth > clientWidth`；网格为 `minmax(min(100%, 24rem), 1fr)`，输入控件 `max-width: 100%`、长文本 `overflow-wrap: anywhere`。浏览器 `viewport` 覆盖未生效（`innerWidth` 仍 1280），640px 像素级证据见 §9 未验证项 | PASS（含 §9 限制说明） |
| 10 | 页面正文（4271 字符）无盘符/UNC 路径、无 `/Users/`、无坐标字段；服务端访问日志 145 条请求全部落在 `/api/v1/*`（外加文档 `/`、`/places`、`/web/*`），**`geo-hits = 0`**；访问日志中 `token=<redacted>`，原始凭证只出现在启动器有意打印的提示行 | PASS |

smoke 过程中发现并当场修好的三处页面问题（均在最终代码上重新核对）：

1. 改名成功后编辑标题没有同步 → 抽出 `paintEditHeading(place)`，保存成功与载入都刷新标题。
2. 「只看它的后代」保留了「名称」条件 → 会把同一地点的其它后代藏起来；改为清空名称与父级条件
   （按钮 `title` 说明该行为）。
3. 单条确认的结果只出现在随后被列表刷新替换的节点里 → 同时写入「逐批 / 单条结果」结果区，
   让 `origin=CONFIRMED_SUGGESTION` 等事实在页面上留存可读。

## 7. C 级判定与打包验收

按任务卡，C 触发条件是「新增依赖、`workers/**`、打包入口或**新增** package-data 目录」。本任务：
依赖未变（95 packages）、`workers/**` 未动、打包入口 `src/photo_search/__main__.py` 未动，新页面
落在**既有** `photo_search/web/` package-data 目录内，`api/web.py` 只新增一个文档路由（导入图未变）。
但页面是发行资源，因此仍执行了真实打包验收：

| 检查 | 结果 |
|---|---|
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（67.6 s），`dist/PhotoSearch`，21 个 NVIDIA DLL |
| bundle 内 package-data 审计 | `_internal/photo_search/web/` 含 `index.html`(3908) `app.js`(33516) `styles.css`(5912) `places.html`(12329) `places.js`(61297) `places.css`(2558)；`catalog/migrations/` 仍为 0001～0005 共 5 个 `.sql`；bundle 合计 3,541,244,472 B |
| `packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir %TEMP%\P2-03C 打包页面验收` | exit 0，`status=ok`、`problems=[]`：`/places` 200 `text/html` 无外链无本机路径且链回外壳、`/web/places.js` 200（61,297 B）、`/web/places.css` 200、外壳链接 `/places`、`/web/app.js` 33,516 B、未知资产 404、无会话 401；中文/空格安装路径下注册来源、扫描 `SUCCEEDED`、缩略图、离线原文件与访问日志脱敏全部通过；startup 1.0 s |
| C 盘与清理 | smoke 会把 3.5 GB bundle 复制到 `%TEMP%`；本次执行前 C 盘 5.97 GB 可用、执行后 2.56 GB，随后只删除**本任务创建**的 `%TEMP%\P2-03C 打包页面验收`（3,541,874,021 B）并确认 C 盘恢复 **5.87 GB**。该目录里 `runtime\session.json`（约 1 KB，应用用 `icacls` 收紧过 DACL）在沙箱内无法删除而残留，属于应用权限收紧生效的副作用 |

## 8. 无生产语义变更的证明

- `schema_version` 仍为 5；`catalog/migrations/**`、`catalog/**`、`places/**`、`geo/**`、
  `scheduler/**`、`ingestion/**`、`api/{routes,models,queries,errors,files,session,security}.py`
  全部未改动（`git status` 中只有 `web/**`、`api/web.py` 与测试/工具/文档）。
- `pyproject.toml`、`uv.lock`、`workers/**` 未改动；`uv lock --check` 通过。
- 现有 P1-04/P1-05/P2-03A/P2-03B 测试全部原样通过（564 例总数只增不改）。

## 9. 未验证项与残留边界

1. **640×900 像素级证据未取得**：本次内置浏览器的 `viewport` 能力覆盖没有改变实际窗口
   （`innerWidth` 恒为 1280，重载与新标签都试过），因此第 9 步用「0 个元素内部横向溢出 +
   `min(100%, …)` 网格 + `max-width: 100%` + `overflow-wrap: anywhere`」代理。需要真实 640px
   渲染证据时应换可设视口的浏览器或转 C1。
2. **批量确认的 provenance 只能是 `MANUAL`**：`POST /place-assignments/batch` 的请求体没有
   `origin` 字段（ADR-0005 §6.2 只冻结资产、动作与 revision），页面因此把这一点写在批量面板里，
   不把批量说成 `CONFIRMED_SUGGESTION`。若批量也要保留「确认机器建议」的来源，需要先由 C2 更新
   契约（属协议决策，不在本任务范围）。
3. **`PLACE_OPERATION_NOT_UNDOABLE` 的「目标被后续编辑」分支在页面上不可达**：人工归属后资产会
   离开建议列表，v1 页面没有逐资产地点编辑器，所以页面只能走到「操作已被撤销」这一分支（同一错误码、
   同一 UI 路径）；「目标被后续编辑」分支由 `test_undo_compensates_and_refuses_after_a_later_edit`
   在 API 层验收。
4. **页面不触发后台解析**：冻结包内没有常驻调度线程，候选只能来自已存在的机器产物（本夹具由
   `tools/diagnostics/places_smoke_fixture.py` 用真实处理器预置）。接入后台解析的任务必须重跑 C 并
   复核导入图（沿用 P2-03B §9 的约定）。
5. **没有 DOM 自动化**：仓库仍不引入 Node/npm，页面行为证据 = `tests/unit/test_api_frontend.py` 的
   静态契约 + `tests/integration/test_p2_place_page.py` 的请求形状 + §6 的人工清单。
6. **非快照隔离读**：`GET /assets` 与建议列表只绑定 `suggestion_revision`（P2-03A §10.2 /
   P2-03B §10.7）；会话级隔离仍属 P2-06。
7. **目录 revision 与新建**：新建地点不推进 `catalog_filter_revision`（P2-03A 已冻结的行为），
   因此「目录已变化」提示实测用的是改名路径；页面按服务端行为工作，本任务未改触发矩阵。
8. **坏包时既有建议仍可读**：符合 ADR-0005 §12 与 P2-03B §10.6；面板如实显示 `missing`，
   只有新解析阻塞。
9. 操作日志按 `operation_revision` 升序分页（keyset 语义），页面因此把面板命名为「操作日志与撤销」
   并注明最新一条在最下方。

## 10. 结论与交接

P2-03C 的「依赖-free 静态地点页 + 目录/同名消歧 + 创建/改名/别名 + 建议确认与批量整理 + 撤销 +
数据包状态面板 + 缺包降级 + 隐私边界」闭环已在 B 级测试、真实浏览器 smoke 与 C 级打包 smoke 上
真实验收，因此标记 PASS。`schema_version` 仍为 5，migration、冻结契约与依赖均未改动。

下一任务是 **P2-04 — 文件名/备注/有效地点 FTS、祖先/别名检索与改名重投影**，推荐
**DS-L（复杂同步 DS-H）**，验收 **B（涉及迁移或 outbox 恢复再加 D）**。任务卡见
[docs/tasks/P2-04.md](../tasks/P2-04.md)。
