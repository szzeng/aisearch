# P2-07 — 搜索页、上传搜图、地点筛选、命中原因与分项覆盖率报告

日期：2026-09-14。状态：**PASS（B 级完整 unit/integration + 真实浏览器 smoke + C 级打包 smoke）**。

本任务只实现主架构 §9.1（查询结构：原样交给后端）、§9.2（召回/融合/命中原因/可选精排）、
§9.5（分页与会话）、§10.1（搜索/浏览页面）、§10.2（`POST /api/v1/search/image`）、
§10.3（上传图的大小/像素/解码时间限制与临时文件回收）与 §12.3（分类别报告覆盖率，不共用
一个“准确率”）已冻结的语义。全部夹具都在 `%TEMP%` 与本次创建的 D 盘 smoke 目录内；没有读取、
移动或删除任何真实图库，没有联网，没有新增依赖，也没有把任何真实模型权重接入应用路径。

> 本卡实现过程中**没有**出现需要 C2 的协议歧义：命中原因、`matched_text`、`semantic_ready`、
> 会话/游标、profile 绑定与 RRF 都是既有冻结语义，本卡只把它们呈现出来；`SearchRequest`/
> `SearchResult` 字段集合**未改**。§6 记录了卡中未逐字规定的六处判定及理由。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `e281925`（P1-07，P2-06B 之后）；开始时 `git status --porcelain` 为空 |
| 本次实现提交 | 见 `git log -1 --format=%H -- docs/reports/P2-07-search-page-upload-and-coverage.md`（提交信息以 `P2-07:` 开头；未 push，远端仍待用户确认） |
| 前置 | P2-06B PASS；`SUPPORTED_SCHEMA_VERSION = 8`、migration 连续 `0001`～`0008`；`POST /api/v1/search`（含 `rerank`/`rerank_state`）、`GET /api/v1/search-profile`、`POST/GET /api/v1/search/feedback`、`GET /assets`（`text=`/地点筛选）、`GET /api/v1/places*` 与 `/geo` 可用；P1-05 静态前端与 P2-03C 地点页可用 |
| 新 migration | **无**（本卡不需要）；`schema_version` 仍为 8，`0001`～`0008` 逐字节未改 |
| 新生产模块 | `src/photo_search/search/upload.py`（上传边界：无依赖 multipart 解析、硬限制、临时目录生命周期、图像查询向量校验） |
| 新前端资源 | `src/photo_search/web/{search.html,search.js,search.css}` |
| 新增工具/文档 | `tools/diagnostics/search_smoke_fixture.py`、`docs/diagnostics/search-smoke-checklist.md` |
| 改动生产文件 | `api/web.py`（`/search` 文档路由）、`api/routes.py`（`POST /search/image`）、`api/models.py`（`ImageSearchResultModel` 等新响应模型）、`api/errors.py`（两个新码的 HTTP 映射）、`search/service.py`（**仅加性**：`search_assets(..., query_vector=)`、`search_request(..., query_vector=, query_identity=)`、`resolve_request_binding()`）、`catalog/errors.py`（新增 2 个码）、`web/index.html` 与 `web/places.html`（导航链接） |
| 新增测试 | 2 个文件、**57 个用例**（`tests/unit/test_p2_search_upload.py` 45、`tests/integration/test_p2_search_image.py` 12）+ 12 个前端静态契约用例 + 1 个路由用例 |
| 夹具扩展 | `tests/p2_search_fixtures.py`（`FakeImageEncoder`）、`tests/p2_search_api_fixtures.py`（`image_encoder` 注入形参） |

**超出卡内「允许修改」清单的两处（有意、最小）**：

1. `src/photo_search/search/upload.py`（新模块）。理由：卡要求 multipart 端点，但本构建**没有**
   `python-multipart`，而本卡又禁止新增运行时依赖，因此需要一个自有的严格 RFC 7578 读取器；
   它同时是「硬限制 + 临时文件回收 + 图像查询向量校验」的唯一所有者，放进 `service.py` 会把
   HTTP 形状的代码塞进检索层。
2. `docs/diagnostics/search-smoke-checklist.md`、`tools/diagnostics/search_smoke_fixture.py`
   （卡在「允许修改」里已列出 `tools/diagnostics/*` 与 `docs/diagnostics/*smoke-checklist*.md`）。

未修改：ADR-0001～0005 与冻结 JSON schema、`0001`～`0008`、五字段 job 身份、lease/attempts/
迟到提交、资产/位置/缺失判定、P2-03A/B 人工锁/排除/幂等/批量/撤销语义、P2-04 tokenizer 世代/
MATCH 策略/`place_text` 来源规则、P2-05 RRF 公式/并列规则/`hit_reason`/通道权重、P2-06A 会话/
游标/profile 绑定语义、P2-06B 反馈幂等与精排分数分离、`space_id` 语义、`places/**`、`geo/**`、
`ingestion/**`、`workers/**`、打包入口、`pyproject.toml`、`uv.lock`。

## 2. 搜索页（`/search`，静态前端，无 Node/npm）

`api/web.py` 新增 `SEARCH_NAME = "search.html"` 与 `GET /search`，沿用既有 per-segment 白名单与
`importlib.resources` 读取方式（未放宽任何路径校验）；`index.html` 与 `places.html` 也加上了
`/search` 导航链接，三个文档互相可达。页面零依赖、无外链、无绝对本地路径、窄视口可重排、键盘可达。

页面呈现的内容**全部**来自后端字段：

| 页面元素 | 数据来源 | 说明 |
|---|---|---|
| 查询框 | `POST /api/v1/search` 的 `{request_id, text}` | 文本原样提交，不做任何自然语言解析 |
| 来源 / 可用性 / 地点（`place_ids`、`include_descendants`、`place_state`）/ `page_size` | 同请求的筛选字段 | 地点语义与 P2-03A/B 一致 |
| 逐行命中原因 | `hit_reason` 的四个冻结取值 | `TEXT`/`SEMANTIC`/`TEXT+SEMANTIC`/`FILTER_ONLY`，逐行展示，不新造文案结论 |
| 命中文本列与语义就绪 | `matched_text`、`semantic_ready` | 直接显示字段本身（例如 `filename_text`/`place_text`） |
| 通道名次 | `matched_by` | 显示为「文本通道名次 / 向量通道名次 / 权威列表次序 + 名次」，不换算成百分比 |
| 按需缩略图 | `GET /assets/{id}/thumbnail` | 沿用 P1-05 的 `IntersectionObserver` + 结构化失败徽标 |
| 续页 | `search_session_id` + `next_cursor` | 同一会话、同一冻结排序的切片 |
| 详情入口 | `/#/asset/{id}` | 复用 P1-05 的详情视图（不在本页复制详情逻辑） |

必须显式呈现的**非成功**状态（全部有静态契约断言）：

- 没有查询编码器 → 显示服务端 `409 RUNTIME_CAPABILITY_UNAVAILABLE` / `QUERY_ENCODER_UNAVAILABLE`，
  文案明确写「语义通道未运行」，并声明「这不是「没有结果」」；查询文本保留，「按当前条件重搜」出现。
- 没有图像编码器 → `IMAGE_ENCODER_UNAVAILABLE`，文案声明没有用文本通道或「零命中」冒充结果。
- 会话/游标类拒绝 → `SEARCH_SESSION_EXPIRED`/`INVALID_CURSOR`/`PROFILE_GENERATION_MISMATCH`/
  `CATALOG_FILTER_REVISION_STALE`/`PLACE_SUGGESTION_REVISION_STALE` 各有自己的提示句，并保留查询。
- `search_projection.state=LAGGING` → 「文本投影仍在追赶：还有 N 个资产…」。
- `pending_semantic` → 「语义索引仍在追赶：… N 个权威候选还没有已确认的向量条目（reason=…）」。
- `candidates_truncated` 与 `channels.*.truncated` → 明确说明结果被上界截断；`authoritative.truncated`
  说明权威列表只发布了前 N 项。
- `state=FILTER_REVISION_CHANGED` → 说明查询期间权威条件变化，本页已按最新条件逐行复核。

命中原因与分数语义保持不变：页面**从不读取** `hit.score`/`reranker_score`，没有百分比换算，
也没有把精排分数与 RRF 相加（静态断言 `.score`/`reranker_score`/`toFixed` 均不出现）。

## 3. 上传搜图（`POST /api/v1/search/image`）

- **端点形状**：`multipart/form-data`，文件字段 `image`，其余字段是 `SearchRequest` 的同名子集
  （`request_id`、`profile_id` 或显式 `space_id`/`image_generation_id`/`dimension`、`source_id`、
  `availability`、`place_ids`、`include_descendants`、`place_state`、`top_k`、`page_size`、
  `search_session_id`、`cursor`）。这些字段被转换成**同一个**冻结信封并由
  `SearchRequest.from_envelope` 校验一次（闭集字段、边界、全有或全无的绑定三元组），因此上传搜图
  不需要第二套参数规则。`text` 不属于该端点（用图片查询时不允许混入文本）。
- **绑定规则**：与文本查询完全相同——有激活 profile 就用 profile 的 `space_id`/`image_generation_id`/
  `dimension`（`check_profile_servable`），否则必须显式给出三元组；两者都缺 → `422 VALIDATION_ERROR`
  （`field=profile_id`）。编码出的向量再按 P2-05 的规则校验（恰好一个、维度一致、有限值、只属于绑定 space）。
- **排序路径**：`search_assets(..., query_vector=…)` 复用同一条通道/RRF/权威复核代码；上传图**没有**
  另造排序。图片查询只让向量通道参与（`channels.fts.engaged=false`、`channels.vector.engaged=true`），
  `hit_reason=SEMANTIC`、`matched_text=[]`、`semantic_ready=true`；向量通道没有命中时**不会**退化成
  「权威列表」，而是如实回答「没有语义上接近的候选」。
- **硬限制**（服务端执行，逐项实测，均可在响应 `image_query.limits` 看到实际生效值）：

| 限制 | 值 | 失败码 |
|---|---|---|
| 请求体字节上限 | 16 MiB（`MAX_UPLOAD_BYTES`） | `413 UPLOAD_TOO_LARGE` |
| 源像素上限（在解码前按文件头判定） | 60,000,000（`MAX_UPLOAD_PIXELS`） | `422 UPLOAD_TOO_MANY_PIXELS` |
| 解码时间预算 | 20,000 ms（`DECODE_BUDGET_MS`） | `409 DECODE_FAILED`（`reason=DECODE_TIME_BUDGET_EXCEEDED`） |
| 格式白名单 | 与 P1-03 `supported_input_formats` 完全一致（JPEG/PNG/WEBP/BMP/GIF） | `409 FORMAT_UNSUPPORTED` |
| 解码失败分类 | 无法识别/损坏 | `409 DECODE_FAILED` |
| 解码长边（JPEG DCT 缩放上界） | 1600（`UPLOAD_DECODE_LONG_EDGE`，与 P1-03 预览同量级） | — |

  上传的像素在交给 adapter 之前按 EXIF Orientation 归一化（用例断言 orientation=6 的 24×18 输入
  以 18×24 交给 adapter），因此编码器看到的就是用户看到的画面。
- **临时文件生命周期**：每次请求在自己的 `tempfile.mkdtemp("photo-search-upload-")` 目录内落盘、
  校验、删除；`stage_upload` 的 `finally` 在成功与失败两条路径都执行。用例在成功、超限、像素超限、
  解码超时、格式不支持、解码失败六条路径上都做「调用前后临时目录集合相同」的断言。
- **不写库**：整条路径不新增 `assets`/`file_locations`/`artifacts`/`index_entries`/`index_outbox`/
  `search_documents`/`search_feedback` 行，不改 `catalog_filter_revision`/`suggestion_revision`、
  也不改任何 `assets.created_at`（用例对全量快照逐项相等断言）。
- **会话绑定**：上传图的字节 SHA-256 进入会话摘要，因此同一个「上传搜图」会话续页必须重传**同一张图**
  （不同图片 → `422 INVALID_CURSOR`）；续页与首屏是同一冻结排序的切片。
- 没有图像编码器时返回结构化 `409 RUNTIME_CAPABILITY_UNAVAILABLE` / `IMAGE_ENCODER_UNAVAILABLE`；
  输入限制**仍然先执行**（因此坏图/超大图得到的是它们自己的码，而不是被能力拒绝掩盖）。

## 4. 分项覆盖率（分类别，不共用准确率）

按卡的「优先复用」选择**不新增覆盖率端点**：页面用三个既有只读接口组装视图
（`GET /api/v1/capabilities`、`GET /api/v1/places/coverage`、`GET /api/v1/stats` +
`GET /api/v1/sources`），四项各自列出并各自写明口径：

| 卡片 | 字段 | 口径（页面原文摘要） |
|---|---|---|
| 1. 文本投影覆盖 | `search.documents`、`queued_assets`、`state`、`tokenizer_version`、`fts_generation_id` | 从图库权威记录重新派生的文本索引条数与滞后队列；权威筛选不依赖它 |
| 2. 语义索引覆盖 | `indexing.semantic_ready`/`pending_repair`/`pending`/`confirmed`、`active_generations`、`search.pending_projection` | 只有 `index_entries.state=CONFIRMED` 才进入语义召回；outbox 未确认说明「已算但还没提交」 |
| 3. 地点覆盖 | `assets_total`、`gps_*`、`places_assigned`/`suggested`/`manual_unassigned`/`unclassified`、`resolver_available` | 机器候选始终是「待确认建议」，不参与有效地点过滤 |
| 4. 来源可用性 | `counts.sources`/`assets`/`locations_present`/`locations_missing` + 每个来源的 `ONLINE/OFFLINE` | 位置 `MISSING` 时原文件不可用，但已生成的缩略图仍可显示 |

页面最后一行显式声明「以上四项各自独立，本页不把任何一个换算成「整体 AI 准确率」或加权总分。」
覆盖率视图全部是纯读：不写任何表，只返回 id/计数/状态，不含路径与坐标（用例与 C 级 smoke 均断言）。

## 5. API 变化

| 路径 | 说明 |
|---|---|
| `POST /api/v1/search/image` | 新增。multipart 上传一张图片 → 与 `POST /search` 同形状的一页结果 + `image_query` 块 |
| `GET /search` | 新增文档路由（`api/web.py`），与 `POST /api/v1/search` 不冲突 |

`ImageSearchResultModel = SearchResultModel + image_query{byte_size,width,height,format,digest,decode_ms,limits}`，
即冻结的 `SearchResult` 字段**一个不改**，只在图片端点外加一个描述这次上传的块。
`api/errors.py` 新增两个映射：`UPLOAD_TOO_LARGE → 413`、`UPLOAD_TOO_MANY_PIXELS → 422`。

## 6. 卡中未逐字规定、需要写进报告的六处判定

1. **两个新错误码**（`catalog/errors.py` + `api/errors.py`）：字节超限与像素超限在冻结码里没有诚实
   分类（它们既不是 `DECODE_FAILED` 也不是 `FORMAT_UNSUPPORTED`，也不该被 `RUNTIME_CAPABILITY_UNAVAILABLE`
   掩盖）。值固定为 `UPLOAD_TOO_LARGE`/`UPLOAD_TOO_MANY_PIXELS`，HTTP 分别 413/422。
2. **`UPLOAD_TOO_MANY_PIXELS` 归 422 而不是 413**：请求体本身合法（大小在字节上限内），被拒绝的是
   图像尺寸这一调用方事实，因此按「调用方形状错误」处理；413 只用于真正超体的请求。
3. **解码时间预算用「测量后拒绝」**：与 P2-06B 的超时语义一致，20 s 预算是实测后如实拒绝
   （`reason=DECODE_TIME_BUDGET_EXCEEDED`），不是抢占式强杀；同时用 JPEG `draft()` 与像素上限把
   峰值内存与耗时先压住。
4. **`image_query` 作为图片端点的附加块**：卡的「允许修改」只允许加性改动 `search/{service,contracts}.py`
   并要求「如确实需要新的 `SearchResult` 字段，必须先停下报告」。本卡不新增 `SearchResult` 字段，
   而是在图片端点自己的响应模型上追加 `image_query`；文本端点与 `GET /assets` 完全不受影响。
5. **图片会话摘要用上传字节的 SHA-256**：`query_hash` 是「翻页期间必须保持不变的查询条件」，图片查询
   没有文本，因此把 `image:<sha256>` 放进该槽位（`query_identity`）。这不是新协议字段，也不进 URL/日志；
   没有它，两个不同图片会共享一个游标空间。
6. **覆盖率不新增端点**：卡优先要求复用既有只读接口，因此页面客户端组装四项；若将来需要服务端
   一次性视图，应新增一个纯读端点而不是让页面拼装更多请求（列入 §11）。

## 7. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **791** 例，进度行 1 个 `s` → **790 passed / 1 skipped**（P2-06B 719 + P1-07 2 = 721 起点，本卡新增 70 例） |
| 本任务新文件 | exit 0：`test_p2_search_upload.py` **45 passed**、`test_p2_search_image.py` **12 passed** |
| 前端静态契约（P2-07 段） | exit 0：`test_api_frontend.py` 共 **66 passed**（新增 12 例）、`test_api_frontend_routes.py` 共 **6 passed**（新增 1 例） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（≈61 s），`dist/PhotoSearch/PhotoSearch.exe` = **35,576,110 B** |
| `uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P2-07-final` | exit 0，`status=ok`、`problems=[]`、startup **1.0 s** |

本环境沿用既有事实：`uv` 默认缓存与 pytest basetemp 在沙箱内被拒绝（`os error 5`），因此上述命令在沙箱外执行
（无功能影响）；C 级构建与 smoke 的工作目录放在 **D 盘**（C 盘余量约 10.37 GB，不足以复制约 3.3 GB bundle）。

## 8. 真实浏览器 smoke（Codex 内置浏览器 + `%TEMP%` 夹具 + 真实端口）

夹具：`tools/diagnostics/search_smoke_fixture.py --photos 6 --reset` → 6 张合成照片、2 个已确认向量
（资产 1、6）、4 个待确认（资产 2–5）、地点 `#1 杭州西湖` → 资产 6、激活 profile
`search-smoke-profile` → 世代 `image-gen-7ee29839419503a3f311cfbe`（READY）。
服务：`uv run --no-sync python -m photo_search --port 58166/58167 --data-root "<夹具数据目录>"`
（仅 127.0.0.1），浏览器打开启动器一次性链接。逐步观察：

| # | 操作 | 实测观察 |
|---|---|---|
| 1 | 启动器链接 → `/` → 点导航「搜索图片」 | 303 落到 `/`，schema v8；导航含「搜索图片」「地点整理」；进入 `/search` 标题「搜索图片」，导航含「返回「本机图片搜索」」「地点整理」「上传搜图」「分项覆盖率」 |
| 2 | 读页面顶部提示 | 「当前搜索配置 search-smoke-profile · 图像世代 image-gen-7ee29839419503a3f311cfbe 本进程没有查询编码器（query_encoder_available=false）：文本查询的语义通道不会运行，服务端会以 RUNTIME_CAPABILITY_UNAVAILABLE / QUERY_ENCODER_UNAVAILABLE 明确拒绝，页面不会把这种情况显示成「没有结果」。」 |
| 3 | 查询文本「西湖」→「搜索」 | 状态行 `[RUNTIME_CAPABILITY_UNAVAILABLE] this process has no query encoder for the bound embedding space（请求编号 a3a69e9acbcfbdad）`；结果区提示「语义通道未运行：本进程没有查询编码器…（RUNTIME_CAPABILITY_UNAVAILABLE / QUERY_ENCODER_UNAVAILABLE）。这不是「没有结果」。」；查询文本保留，「按当前条件重搜」出现 |
| 4 | 清空文本 →「搜索」 | 6 行，每行：缩略图 + 「第 N 名 资产 #N 仅筛选（本次没有查询文本，两条通道都没有参与排序） 语义就绪：否（本次没有已确认的向量条目） 通道名次：权威列表次序 N」+「查看详情」；状态行「已显示 6 行 · 候选共 6 个 · 已到末尾」 |
| 5 | 读结果区提示 | 「文本投影仍在追赶：还有 6 个资产等待重新派生文本索引（本次结果已按图库权威记录逐行复核）。」与「本次没有查询文本：没有生成任何查询向量，结果由图库权威列表给出（hit_reason=FILTER_ONLY）。」（语义通道未运行时 `pending_semantic` 提示的边界见 §11.3） |
| 6 | 每页数量 2 → 搜索 → 「加载下一页」 | 首屏「已显示 2 行 · 候选共 6 个 · 还有下一页」；续页追加资产 #3/#4，状态行「已显示 4 行 · 候选共 6 个 · 还有下一页」，无重复行 |
| 7 | 地点状态 →「已归类」→ 搜索 | 只剩 1 行：资产 #6（夹具人工归属到「杭州西湖」的那张） |
| 8 | 选本地图片 →「用这张图搜索」 | 状态行 `[RUNTIME_CAPABILITY_UNAVAILABLE] this process has no image encoder for the bound embedding space（请求编号 9b79a12d6773d771）`；结果区提示「本进程没有图像编码器，无法用上传图做语义检索（RUNTIME_CAPABILITY_UNAVAILABLE / IMAGE_ENCODER_UNAVAILABLE）；服务端没有用文本通道或「零命中」冒充结果。」；上传状态行「上传图查询没有成功，详见上面的说明。」（见 §8.2） |
| 9 | 读分项覆盖率 | 四项分别渲染：文本投影覆盖（已投影文档 0 / 待投影资产 6 / 状态 仍在追赶）、语义索引覆盖（已确认向量条目 1 / 待修复 1 / 未确认 outbox 1 / 已确认 outbox 2 / 活动世代 …（READY）/ 投影状态 LAGGING / 投影滞后事件 1）、地点覆盖（资产总数 6 / 已归类 1 / 未归类 5 …）、来源可用性（登记来源 1 / 在线 1 / 在库位置 6 / 缺失 0）；每项都有「口径」，末行声明不合成「整体 AI 准确率」 |
| 10 | 640×900 视口 + Tab | `innerWidth=640`、`clientWidth=625`、`scrollWidth=625`（无横向滚动），结果网格折成 3 列（176 px）；Tab 焦点顺序：导航链接 ×4 → 查询文本 → 来源 → …（`document.activeElement` 实测），焦点环可见 |

服务访问日志同时记录到：`GET /api/v1/session?next=/&token=<redacted>`（303）、`POST /api/v1/search`
（409 无编码器）、`POST /api/v1/search`（200 纯筛选）、续页 200、地点筛选 200、
`POST /api/v1/search/image`（409）×2。

### 8.1 浏览器 smoke 发现并修复的真实缺陷

第一次上传 smoke 里，页面在上传失败后仍显示「上传图查询成功（结果见上面的网格）。」——
`submitUpload` 把 `sendSearch` 的 promise 当作成功链（失败分支没有把结果标记为失败）。这是
「用成功文案掩盖非成功状态」的真实缺陷，已修复：`sendSearch` 现在把成功/失败解析为布尔值，
上传入口据此分别写「上传图查询成功…」或「上传图查询没有成功，详见上面的说明。」，并加了静态契约
断言防止回归。修复后在**新端口**（绕过 `max-age=300` 的脚本缓存）重新执行第 8 步，实测显示
「上传图查询没有成功，详见上面的说明。」。这也说明浏览器 smoke 不是形式：它抓到了一个集成测试
看不到的前端缺陷。

### 8.2 浏览器侧的已知限制

- 语义通道命中的行（`SEMANTIC`/`TEXT+SEMANTIC`）与「语义索引仍在追赶」提示在**本构建的浏览器里**
  不可达：它们需要本进程有编码器，而本构建按 ADR-0003 §7 没有接入任何 adapter。该数据路径由
  `test_a_lagging_semantic_entry_is_named_instead_of_hidden`（人工归属触发重投影 → 该资产不再有
  CONFIRMED 条目 → 图片查询如实报告 `pending_semantic.count>=1` 且该资产不进向量命中）覆盖。
- 内置浏览器的文件选择器在自动化下第一次等待超时（3 s 上限），重试（AX 聚焦文件控件后再等待）成功；
  上传端点的结构性证据仍以 C 级 `packaged_api_smoke.py` 的真实 multipart 请求为准。

## 9. C 级 bundle 审计与 smoke 证据

- **package-data 审计**（`pyi-archive_viewer -r -l dist/PhotoSearch/PhotoSearch.exe` 递归列出 PYZ）：
  `photo_search.search.upload`、`photo_search.search.{service,contracts,sessions,profile}`、
  `photo_search.api.{routes,context}` 均入包；`_internal/photo_search/web/` 下
  `search.html`(6,971 B)/`search.js`(37,953 B)/`search.css`(2,479 B) 与既有页面同时在包内
  （smoke 的 `GET /search`、`/web/search.js`、`/web/search.css` 均为 200，是「页面在冻结包内」的直接证据）。
- **smoke 新增检查（本次全部为真）**：`search_page_status=200`（`text/html`、`no-cache`、无外链、无本地路径）、
  `search_page_links_back_to_shell=true`、`search_page_links_to_places=true`、
  `search_script_status=200`（37,953 B）、`search_style_status=200`、`shell_links_to_search=true`、
  `places_page_links_to_search=true`；`upload_status=409`、`upload_code=RUNTIME_CAPABILITY_UNAVAILABLE`、
  `upload_reason=IMAGE_ENCODER_UNAVAILABLE`、`upload_added_no_asset=true`、`upload_without_session=401`、
  `upload_has_no_local_path=true`；`coverage_capabilities_status=200`、`coverage_places_status=200`、
  `coverage_stats_status=200`、`coverage_has_no_local_path=true`（且 `latitude` 不出现）、
  `coverage_search_block={"documents":2,"queued_assets":0,"state":"SYNCED","query_encoder_available":false}`。
- **同时复验的既有断言**：shell/places 页面与资源、session 303 重定向与单次凭证、同一 data root 二次启动、
  scan→SUCCEEDED、`GET /assets?text=` 文本检索与投影摘要、`POST /search` 的显式绑定/续页/无编码器 409、
  反馈 201/200/409 与只读分页、`rerank:true → UNAVAILABLE` 且基础页不变、缩略图与原图离线语义
  （`SOURCE_FILE_MISSING`）、访问日志脱敏、`stats`/`search`/`image` 无 session → 401。
- **清理**：本次两次 C 级 smoke 创建的 `D:\PhotoSearchSmoke\P2-07`、`P2-07b`、`P2-07-final`
  三份约 3.3 GB 的 bundle 副本在 smoke 结束后删除（D 盘可用空间回到 163.76 GB），只保留
  `P2-07b-report.json` 与 `P2-07-final-report.json` 两份 JSON 证据。
  `dist/PhotoSearch` 属常规构建产物，保留。**残留**：`D:\PhotoSearchSmoke\P2-06B*` 两个目录里各留一个
  112 B 的 `session.json`（旧 `(R,W)` DACL 遗留，非本次产生，未做 takeown 或提权改写 ACL）。

## 10. 隐私与访问边界

- 页面与响应里没有绝对路径、坐标、会话令牌或查询向量：`/search` 页面不含工作目录/数据根/图库路径，
  `/search/image` 的 409 与覆盖率三类响应都不含这些，且 `latitude` 不出现（用例 + smoke 均断言）。
  上传图的错误消息**特意不含任何路径**（与 P1-03 的 `DECODE_FAILED` 不同，因为上传图没有图库位置）。
- 页面从不读取 `hit.score`/`reranker_score`，不把任何分数换算成百分比或「准确率」；精排分数与 RRF 分数
  保持两套含义（静态断言）。
- 上传图不写库、不落永久文件：只在私有临时目录中存在到本次请求结束，成功与失败路径都会删除。
- 新增端点仍在 `/api/v1/` 保护范围内：无 session → 401，缺 CSRF/外来 Origin → 403（用例与 C 级 smoke 实测）。
- 未新增任何「无 session 可达」的数据端点：`GET /search` 只是文档，不含用户数据。

## 11. 未验证项与残留边界

1. **真实编码器仍未接入**（沿用 P2-06A §11.1 / P2-06B §11.1）：文本查询在打包/开发进程里以 409
   `QUERY_ENCODER_UNAVAILABLE` 拒绝，上传搜图以 409 `IMAGE_ENCODER_UNAVAILABLE` 拒绝。因此本卡**不声称**
   端到端语义检索、以图搜图质量或任何召回结论；`hit_reason=SEMANTIC`/`TEXT+SEMANTIC` 的真实浏览器观察
   留待接入真实 adapter 后的任务。
2. **覆盖率是客户端组装的三次只读请求**：页面需要 4 个 HTTP 请求（含 `/sources`）才能画完四项；
   没有服务端一次性视图，也没有对外承诺的快照隔离（沿用 P1-04/P2-03A 的「非快照隔离读」记录）。
3. **`pending_semantic`（「语义索引仍在追赶」）在浏览器里不可达**（§8.2）：它需要本进程有编码器；
   本卡用集成用例覆盖其数据路径，页面的渲染由静态契约断言覆盖。
4. **上传限制的规模与耗时未测**：16 MiB / 60 MP / 20 s 三个上限只在 32×24 与注入的小上限夹具上验证；
   没有真实 16 MiB、60 MP 或接近 20 s 的输入；JPEG `draft()` 的峰值内存未实测（沿用 P1-03 的记录）。
5. **解码时间预算不是抢占式中断**：预算在解码完成后测量并拒绝，超预算的那次解码已经把资源花掉；
   与 P2-06B 的超时语义一致。
6. **`python-multipart` 未引入**：本构建用自有严格读取器（拒绝 preamble、折叠头、未知 disposition、
   重复字段、非 UTF-8、超长 boundary、超过 32 个 part）。它只被本机的 `fetch(FormData)` 与测试用
   `httpx files=` 驱动，未与其他第三方客户端的怪癖（如 `Content-Transfer-Encoding`、RFC 5987
   `filename*`）做过互操作验证。
7. **分页与上界只在小夹具验证**：`page_size` 达到 100、`top_k` 达到 1000、候选被 1000 上界截断的
   提示只在 6 个资产的夹具上验证公式与满页情形，未在大库规模验证。
8. **页面没有 DOM 自动化测试**（沿用 P1-05B/P2-03C）：静态契约 + 本卡记录的真实浏览器 smoke 是全部前端证据。
9. **搜索页不是快照隔离读**（沿用 P1-04/P2-03A/P2-06A）：每页在返回前用最新权威条件复核，
   但没有跨请求一致快照。
10. **上传图的 EXIF/色彩语义未扩展**：沿用 P1-03 的解码契约（不做色彩管理、透明合成到白色），
    只做方向归一化；HEIC/RAW 仍按 `FORMAT_UNSUPPORTED` 拒绝。

## 12. 结论与交接

P2-07 的「搜索页（查询/筛选/命中原因/续页/非成功状态如实呈现）+ `POST /api/v1/search/image`
（无依赖 multipart、字节/像素/解码时间/格式白名单硬限制、临时文件双路径回收、与文本查询同一绑定与
RRF/权威路径、不写库）+ 地点筛选与分项覆盖率（四项各自口径、不合成准确率）+ 三文档互链」
闭环已按 **B + 真实浏览器 smoke + C** 真实验收，因此标记 PASS。`schema_version = 8`，
migration 连续 `0001`～`0008` 未改；ADR/冻结 JSON schema/依赖/`places/**`/`geo/**`/`ingestion/**`/
`workers/**`/打包入口未改。

下一任务是 **P2-08 — 恢复、过滤、增量、后台竞争与地点人工锁/投影/缺包综合验收**
（`docs/tasks/P2-08.md`，已按本卡要求生成），推荐 **DS-H 执行 + C2 high 独立审查**，
验收 **B + D（按升级/恢复场景需要再加 C）**。
