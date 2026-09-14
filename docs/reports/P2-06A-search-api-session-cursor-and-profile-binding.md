# P2-06A — 搜索协议、`POST /api/v1/search`、搜索会话/游标与 Profile 绑定报告

日期：2026-09-14。状态：**PASS（B 级完整 unit/integration + C 级打包 smoke + D 级迁移/激活/过期故障注入）**。

本任务只实现主架构 §9.2/§9.5、§10.2、§11.3.5 与 ADR-0003 §8/§9、ADR-0005 §10 已冻结的语义：
协议形状、会话/游标绑定、profile 原子激活与 `catalog_filter_revision` 过期判定都不是本任务重新定义的。
全部夹具都在 `%TEMP%` 与本次创建的 D 盘 smoke 目录内；没有读取、移动或删除任何真实图库，没有联网，
没有新增依赖，也没有把任何真实模型权重接入应用路径。

> 本卡实现过程中**没有**出现需要 C2 的协议歧义：`SearchRequest`/`SearchResult` 字段、会话/游标绑定、
> profile 激活入口都按卡与冻结 schema 落地。§6 记录了卡中未逐字规定的三处判定，以及为什么按此实现。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `27cb006`（P2-05），开始时 `git status --porcelain` 为空 |
| 本次实现提交 | 见 `git log -1 --format=%H -- docs/reports/P2-06A-search-api-session-cursor-and-profile-binding.md`（提交信息以 `P2-06A:` 开头；未 push，远端仍待用户确认） |
| 前置 | P2-05 PASS；`SUPPORTED_SCHEMA_VERSION = 6`、migration 连续 `0001`～`0006`；P2-05 服务层（`search.service.search_assets`、`retrieval`、`fusion`、`embedding`）与 P1-04 session/Host/Origin/CSRF 边界可用 |
| 新 migration | `catalog/migrations/0007_search_profiles.sql`，**`SUPPORTED_SCHEMA_VERSION` 推进到 7**，`0001`～`0006` 逐字节未改 |
| 新生产模块 | `src/photo_search/search/{contracts,sessions,profile}.py` |
| 改动生产文件 | `search/{service,retrieval}.py`（协议编排 + `MAX_TOP_K` 单一来源）、`api/{routes,models,errors}.py`、`catalog/{schema,errors}.py`、`scheduler/projection.py`（只读摘要）、**`api/context.py` 与 `app.py`**（见下） |
| 新增测试 | 4 个文件、**52 个用例**（contracts 23、sessions 5、profile 迁移 6、API/协议 18）+ 共享夹具 `tests/p2_search_api_fixtures.py` |
| 版本耦合测试的机械更新 | 4 个既有文件（`test_catalog_migrations.py`、`test_p2_scheduler_recovery.py`、`test_p2_search_migration.py`、`test_p2_search_authority.py`），只改「当前版本号 / 表清单 / 迁移步长」，未改任何语义断言 |

**超出卡内「允许修改」清单的两处（有意、最小）**：`api/context.py` 与 `app.py`。理由是搜索会话必须是
**进程内**状态（architecture §9.5），而本仓库唯一「一个应用实例所拥有的一切」的宿主就是 `ApiContext`；
同时本卡没有把真实查询编码器接进应用进程（ADR-0003 §7 的 adapter 属后续卡），因此需要一个与既有
`service_factory`/`runner_executor` 同风格的 `encoder_factory` 注入缝。两处改动都是加性的（`ApiContext`
新增 `search_sessions`/`query_encoder()`/`encoder_factory` 形参，`create_app` 多转发一个形参），不改任何既有行为。

未修改：ADR-0001～0005 与冻结 JSON schema、`0001`～`0006`、五字段 job 身份、lease/attempts/迟到提交、
资产/位置/缺失判定、P2-03A/B 人工锁/排除/幂等/批量/撤销与触发矩阵、P2-04 tokenizer 世代/MATCH 策略/
`place_text` 来源规则、P2-05 RRF 公式/并列规则/`hit_reason`/通道权重、`space_id` 语义、`places/**`、
`geo/**`、`ingestion/**`、`workers/**`、打包入口、前端资源、`pyproject.toml`、`uv.lock`。

## 2. 协议（`search/contracts.py`）

两个不可变（`frozen=True`）信封，`contract_version = "p2-contracts.v1"`，**拒绝未知字段**并逐项校验形状；
失败一律是结构化 `VALIDATION_ERROR`（HTTP 422），不是 traceback。只做形状校验：**不解析自然语言**、
不把 `text` 拆成条件，`text` 原样交给 P2-04 的 `build_match`。

| `SearchRequest` 字段 | 类型/默认 | 说明 |
|---|---|---|
| `contract_version` | 必填 `"p2-contracts.v1"` | 与 P2-01 同一常量 |
| `request_id` | 非空字符串，必填 | 仅回显与幂等对账，不参与查询 |
| `text` | `str \| null = null` | `null` = 纯筛选查询 |
| `profile_id` | `str \| null = null` | 给出后请求固定该 profile |
| `space_id` / `image_generation_id` / `dimension` | 可空；三者必须同时给出或同时不给 | 未激活 profile 时的 P2-05 显式绑定 |
| `source_id` | `int ≥ 1 = null` | 来源条件 |
| `availability` | `"present"/"missing"/"any" = "any"` | 复用 P2-05 scope |
| `place_ids` / `include_descendants` / `place_state` | `int[]`（去重、≥1）/ `true` / `"any"`… | ADR-0005 §10 地点条件 |
| `top_k` | `1..1000`，默认 200 | 单通道候选上界 |
| `page_size` | `1..100`，默认 20 | 一页的候选切片长度 |
| `search_session_id` / `cursor` | 可空 | 续页必须两者同时给出 |

| `SearchResult` 字段 | 说明 |
|---|---|
| `contract_version` / `request_id` | 回显 |
| `search_session_id` | 本次查询的会话；续页必须回传 |
| `profile_id` / `image_generation_id` / `space_id` / `dimension` | 会话所固定的绑定（未激活 profile 时 `profile_id = null`） |
| `catalog_filter_revision` / `suggestion_revision` | 会话绑定的两个版本 |
| `fusion_config` | **实际生效**的融合配置（未激活 profile 时为冻结 RRF 默认；激活时回显 profile 声明值，而 profile 声明值在激活时已核对为同一配置） |
| `query` | `text`/`normalized_text`/`terms`/`match_expression`/`tokenizer_version`/`fts_generation_id`；纯筛选为 `null` |
| `filters` | 生效条件回显 + `expanded_place_ids` + `binding`（generation/space/dimension/state/`semantic_complete`） |
| `hits` | 本页逐行 `asset_id`/`rank`/`score`/`matched_by`/`hit_reason`/`matched_text`/`semantic_ready` |
| `page_size` / `page_offset` / `next_cursor` / `has_more` | 分页语义与 `GET /assets` 一致：`has_more = page_offset + page_size < total_candidates` |
| `total_candidates` / `candidates_truncated` | 会话缓存的候选总数（≤1000）与是否被上界截断 |
| `channels` | `fts`/`vector` 两通道的 `engaged`/`candidates`/`truncated`（+ 向量 `engine_blocks`/`reason`）与 `authoritative` 的 `total`/`listed`/`truncated`/`order` |
| `pending_semantic` | P2-05 的滞后补查块（纯筛选或未绑定 generation 时为 `null`） |
| `search_projection` | P2-04 的派生文本投影状态（`state`/`queued_assets`/`processed_assets`/…） |
| `state` | `OK` 或 `FILTER_REVISION_CHANGED`（查询期间权威筛选变化；行已按最新 SQLite 复核） |

`tests/unit/test_p2_search_contracts.py::test_the_http_models_are_field_for_field_the_frozen_envelopes`
把 pydantic 请求/响应模型与这两个信封**逐字段**绑定（`SearchRequest` 16 字段、`SearchResult` 23 字段、
`SearchHit` 7 字段），并断言 `Literal["p2-contracts.v1"]` 与冻结常量一致；另有 16 个参数化正反例覆盖越界、
未知字段、错误类型与部分绑定。

## 3. 会话与游标（`search/sessions.py`）

| 常量 | 值 | 依据 |
|---|---|---|
| `MAX_SESSION_IDS` | 1000 | architecture §9.5「缓存最多 1000 个有序 ID」 |
| `SESSION_TTL_MS` | 600_000（10 分钟） | architecture §9.5 |
| `MAX_SESSIONS` | 64 | 进程内上界；超限先淘汰过期、再按 LRU（`last_used_ms`，并列用 session_id） |
| `CURSOR_PREFIX` / `MAX_CURSOR_CHARS` | `"s1"` / 512 | 令牌版本化 + 长度上界 |

一次搜索把**有序候选**（`SessionEntry`：`asset_id` + 已算好的 `score`/`matched_by`/`hit_reason`/
`matched_text`/`semantic_ready`）冻结在会话里，因此续页是同一份排序的切片，不需要重跑检索、也不可能
换一套排序。会话绑定：`query_hash`、`profile_id`、`image_generation_id`、`space_id`、`dimension`、
`catalog_filter_revision`、`suggestion_revision`、`created_at`。

`cursor = base64url("s1:<session_id>:<offset>")`，**不透明**且与会话绑定。续页判定矩阵（全部实测）：

| 情况 | 结果 |
|---|---|
| 正常续页（同 body + session + cursor） | 200，同一 `search_session_id`，`page_offset` 前进 `page_size` |
| `search_session_id` 与 cursor 内的会话不同 | 422 `INVALID_CURSOR` |
| 会话不存在或已过期（TTL/重启） | 409 `SEARCH_SESSION_EXPIRED`（`details.reason=SESSION_NOT_LIVE`、`ttl_ms`） |
| 同一会话但查询/筛选/`top_k`/`page_size` 不同（查询哈希不同） | 422 `INVALID_CURSOR` |
| offset 越界（含被篡改令牌） | 422 `INVALID_CURSOR` |
| 非 base64 / 非法 utf-8 / 前缀不符 / offset 非数字 / 空 / 超长 | 422 `INVALID_CURSOR`（绝不是 500） |
| 只给 `search_session_id` 不给 `cursor` | 422 `INVALID_CURSOR` |
| 绑定变化（profile/generation/space/dimension 任一不同） | 409 `PROFILE_GENERATION_MISMATCH`（`reason=SESSION_BINDING_CHANGED`） |
| `catalog_filter_revision` 变化 | 409 `CATALOG_FILTER_REVISION_STALE`（`reason=CATALOG_FILTER_REVISION_CHANGED`） |
| `place_state=suggested` 且 `suggestion_revision` 变化 | 409 `PLACE_SUGGESTION_REVISION_STALE` |

**每页返回前**仍按当前 SQLite 权威条件复核该页 `asset_id`：被排除的行不出现在页里，但 offset 仍按
`page_size` 前进，因此既不跳页也不改变后续 id 顺序（`availability` 变化不推进
`catalog_filter_revision`，这正是必须逐页复核的原因）。存储用 `threading.RLock` 保护（API 可能在
工作线程里继续翻页），并有 24 线程并发用例。

## 4. Profile 与绑定（`search/profile.py` + `0007_search_profiles.sql`）

`search_profiles` 的列与冻结 `$defs/SearchProfile` **逐项一致**（`fusion_config` 落在
`fusion_config_json` TEXT，`created_at` 为 UTC epoch 毫秒）；`active_search_profile` 是
`state_id = 1` 的单行指针，`profile_id` 可空并以 `ON DELETE RESTRICT` 外键引用 profile。迁移把指针初始化为
`(1, NULL, 0)`：**未激活时不冒充任何配置**。

**不可变**：`trg_search_profiles_immutable` 拒绝任何 `UPDATE`（内容列与 `created_at` 都算），新配置必须
发布新 `profile_id`；`fusion_config_json` 的形状由 DDL 的 `json_valid` + 必需键检查固定（用 `IS` 而非
`=`，否则缺失键会让 CHECK 求值为 NULL 而被 SQLite 放行——本任务就是靠这条实测发现的）。

**激活**（`POST /api/v1/search-profile/activate`，一个 `BEGIN IMMEDIATE` 事务）依次核对：

1. profile 存在（否则 `PROFILE_GENERATION_MISMATCH`/`PROFILE_NOT_FOUND`）；
2. 引用的 `image_generation_id` 存在、其 `space_id`/`processor_id` 与 profile 一致、状态为 `READY`
   （`GENERATION_NOT_FOUND`/`GENERATION_SPACE_MISMATCH`/`GENERATION_PROCESSOR_MISMATCH`/`GENERATION_NOT_READY`）；
   维度**只从 generation 行读取**（冻结 profile 文档没有 dimension 字段）；
3. `fts_tokenizer_version`/`fts_generation_id` 能被本构建服务（`"1"` → `TOKENIZER_VERSION = 1`，
   `fts-v1`），否则 `FTS_GENERATION_UNSUPPORTED`；
4. `fusion_config` 就是本构建实际执行的冻结 RRF（`method=RRF`、`k=60`、`fts/vector=1.0`），否则
   `FUSION_CONFIG_UNSUPPORTED`；
5. P2-02 记录的运行事实：该 `runtime_package_id` 存在健康快照（`CAPABILITY_MISSING`/`RUNTIME_UNHEALTHY`），
   且存在该 `embedding_processor_id` + runtime package 的专项 evidence，其 provider/device/precision 在该快照中
   **可用且健康**（`QUALIFICATION_MISSING`）——都归入 409 `RUNTIME_CAPABILITY_UNAVAILABLE`，**不选设备**；
6. 通过后才在**同一事务**内先清空同 `space_id` 的 `is_active`、再置本 generation 为 1，并更新活动指针
   （两条语句避免中途触碰 `uq_index_generations_active_space`）；`state` 与任何行都不删不改。

幂等：同一 profile 重复激活返回 `changed=false`/`state=ALREADY_ACTIVE`；若指针已是该 profile 但
`is_active` 漂移，则只做修复性写入（实测）。激活入口是**唯一**会移动 `index_generations.is_active` 的
产品路径，因此不会出现「编码器与索引错配」的可见状态。

## 5. API 变化

| 路径 | 说明 |
|---|---|
| `POST /api/v1/search` | body `SearchRequest` → `SearchResult`；首屏返回 `search_session_id`+`next_cursor` |
| `GET /api/v1/search-profile` | 只读投影：`profile_id`（未激活 = `null`）、`profile`（冻结文档形状）、`updated_at` |
| `POST /api/v1/search-profile/activate` | 幂等激活；返回 `changed`/`state`/`readiness`（capability、device、provider、precision、evidence_id） |
| `GET /api/v1/capabilities` | `search` 块**加性**补充：`active_profile_id`、`active_image_generation_id`、`active_fts_generation_id`、`active_runtime_package_id`、`active_generations`、`pending_projection`、`query_encoder_available`；P2-04 既有的 `documents`/`fts_generation_id`/`state` 等原样保留 |

全部走既有 session/Host/Origin/CSRF 边界（无 session → 401；POST 缺 Origin/CSRF → 403）；
错误映射新增 `SEARCH_SESSION_EXPIRED: 409`、`PROFILE_GENERATION_MISMATCH: 409`，并补上 P2-05 §11.1 遗留的
`FILTER_UNSUPPORTED: 409`；`INVALID_CURSOR` 的常量移入 `catalog/errors.py`（值不变，服务层因此无需 import HTTP 层）。

## 6. 卡中未逐字规定、需要写进报告的三处判定

1. **「缺 `profile_id` 且缺显式绑定」= 422**（按卡 §3 与 D-7）：无激活 profile 时，带 `text` 的请求
   **必须**自带 `space_id`+`image_generation_id`+`dimension`，否则结构化拒绝（`field="profile_id"`）。
   P2-05 的服务层函数 `search_assets(text=...)` 行为**未改**（既有 P2-05 测试原样通过），文本检索仍可直接
   在服务层或 `GET /assets?text=` 使用；但 `POST /search` 是语义检索入口，不接受「没有绑定也没有 profile」。
2. **纯筛选查询（`text = null`）不受绑定要求限制**：即使未激活任何 profile、或请求带了绑定，也按
   P2-05 的「纯筛选走 SQLite、无须文本向量」路径返回权威列表（`hit_reason = FILTER_ONLY`，向量通道
   `engaged=false`）。这是卡中「`text`（可空 = 纯筛选查询）」与 §3 的两条要求同时成立的唯一解释。
3. **续页时显式 pin 住旧 profile 仍可继续**：只有「请求回到活动指针」才会报
   `PROFILE_GENERATION_MISMATCH/SESSION_BINDING_CHANGED`（D-3 实测路径）；如果客户端在续页请求里显式给出
   仍然可服务的旧 `profile_id`，会话就按它自己的绑定继续（§11.3.5「每次请求在开始时固定一个 profile」）。
   另外，`fusion_config` 目前只按冻结 RRF 校验+回显，**不**用它改变融合数学（改权重需要先改 P2-05 冻结语义）。

## 7. D 级故障注入（逐项实测）

| # | 卡要求 | 实测（测试名） |
|---|---|---|
| 1 | v6→v7 迁移：备份、单事务、旧行不变；损坏/缺件/过新 | `test_p2_search_profile_migration.py`：备份内 `schema_version=6` 且无两张新表、`applied=(7,)`、旧 8 组行逐项不变、空指针行；注入损坏 `0007` → `MIGRATION_FAILED` 且版本仍 6、无半成品表/键；`DROP active_search_profile` → `SCHEMA_INCONSISTENT`；v8 → `SCHEMA_TOO_NEW` 且主库字节与目录项不变；不可变触发器/FK RESTRICT/`state_id` CHECK/非法 JSON 形状逐条拒绝 |
| 2 | 激活中断回滚；generation 非 READY 或 runtime 不健康 → 结构化拒绝且零写 | `test_an_interrupted_activation_rolls_back_both_writes`（`activation_hook` 注入：指针仍是 profile-1、两个 generation 的 `is_active` 全回滚、旧 profile 仍能查询）+ `test_every_activation_refusal_is_structured_and_writes_nothing`（8 个 profile：`PROFILE_NOT_FOUND`/`GENERATION_NOT_READY`/`CAPABILITY_MISSING`/`RUNTIME_UNHEALTHY`/`QUALIFICATION_MISSING`/`FTS_GENERATION_UNSUPPORTED`/`FUSION_CONFIG_UNSUPPORTED`/`GENERATION_SPACE_MISMATCH`，全部 409 且指针与 `is_active` 不变） |
| 3 | 切换后旧会话续页 → 过期码，不混版本 | `test_switching_profiles_moves_the_pointer_and_expires_the_old_session`：激活 profile-2 后指针与 `is_active` 同时移动（gen-1 `false`、gen-2 `true`），旧会话续页 409 `PROFILE_GENERATION_MISMATCH`/`SESSION_BINDING_CHANGED`；新查询用新绑定（`image_generation_id` = gen-2、向量通道 0 行、`pending_semantic` 指名该文本命中的未投影资产） |
| 4 | 会话过期/重启 → `SEARCH_SESSION_EXPIRED`；重搜得到完整第一页 | `test_a_ttl_expiry...`（可控时钟跨 TTL）：409 + 重搜 `page_offset=0` 且首屏 hits 与首查一致；`test_a_restart_has_no_sessions_and_the_old_cursor_is_identified`（同一 data root 的第二个应用实例）：409 `SEARCH_SESSION_EXPIRED` |
| 5 | 翻页期间人工改动 → 续页过期；重搜补回新命中 | `test_a_place_edit_between_pages_is_stale_and_a_research_finds_the_new_hit`：地点归属 +1 后续页 409 `CATALOG_FILTER_REVISION_STALE`；重搜 `text=hangzhou` 命中新归类资产且 `matched_text=["place_text"]` |
| 6 | 游标隔离（他会话/他查询/他筛选/篡改 base64） | `test_a_foreign_tampered_or_stale_cursor_is_never_reinterpreted`：7 种情况分别得到 `INVALID_CURSOR`(422) 或 `SEARCH_SESSION_EXPIRED`(409)，无一 500 |
| 7 | 上界与形状逐项断言 | `test_out_of_range_unknown_and_version_mismatched_bodies_are_422`（7 例参数化：`page_size` 0/101、`top_k` 0/1001、未知字段、错误 contract_version、空 `request_id`）+ `test_a_text_query_without_any_binding_is_refused_and_unusable_text_is_422`（缺绑定、半绑定、空/引号/超长文本 422；纯筛选 200） |

**会话/游标对照**：`test_paging_walks_one_frozen_order_without_repeats_or_gaps`（page_size=2 走完 3 个候选：
无重复无遗漏、`rank` 连续 1..3、与单页 `page_size=100` 的同一查询顺序完全一致、续页的
`channels/filters/fusion_config/query/search_projection` 与首屏逐项相同）；`test_an_explicitly_bound_text_search...`
（同一查询两次不翻页 → hits/channels 逐项一致，且查询文本只编码一次/请求）。

## 8. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **670** 例，进度行 1 个 `s` → **669 passed / 1 skipped**（P2-05 时为 618 / 617 passed，本卡新增 52 例） |
| 本任务 4 个新文件 | exit 0，**52 passed**（contracts 23、sessions 5、migration 6、endpoint 18） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages in 1ms`（依赖未变） |
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（≈55 s），`D:\SZ\project\aisearch\dist\PhotoSearch\PhotoSearch.exe` = 35,537,192 B |
| `uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P2-06A` | exit 0，`status=ok`、`problems=[]`、startup **1.01 s** |

本环境沿用既有事实：`uv` 默认缓存与 pytest basetemp 在沙箱内被拒绝（`os error 5`），因此上述命令在沙箱外执行
（无功能影响）；C 级构建与 smoke 的工作目录放在 **D 盘**（C 盘余量仅 8.75 GB，不足以复制约 3.3 GB bundle）。

## 9. C 级 bundle 审计与 smoke 证据

- **package-data 审计**（`pyi-archive_viewer -r -l dist/PhotoSearch/PhotoSearch.exe` 递归列出 PYZ）：
  新增模块全部入包 —— `photo_search.search.contracts`/`.sessions`/`.profile`/`.service`/`.retrieval`/
  `.fusion`/`.embedding`/`.documents`/`.query`/`.tokens`；`photo_search.scheduler.{contracts,capability,projection,state}`
  与 `photo_search.places.*` 也在包内；`_internal/photo_search/catalog/migrations/` 内 **`0007_search_profiles.sql`(3474 B)**
  与 `0001`～`0006` 同时存在。`scheduler.engine`/`eligibility`/`geo.extract`/`geo.resolver` 仍未入包（与应用路径无关，未变）。
- **smoke 新增检查（本次全部为真）**：`GET /api/v1/search-profile` → 200 且 `profile_id = null`；
  `POST /api/v1/search`（未激活 profile、显式绑定 `space_id=smoke-space:v1`/`image-gen-smoke`/3，纯筛选）
  → **200 + `state=OK`**，回显 `image_generation_id=image-gen-smoke`、`profile_id=null`、
  `channels.vector.engaged=false`、`has_more=true`、`search_hits=[1]`；带 `cursor` 续页 → 200、同一
  `search_session_id`、`page_offset=1`、`search_continuation_hits=[2]`（与首页无重复）；带文本的请求因本
  进程没有查询编码器 → **409 `RUNTIME_CAPABILITY_UNAVAILABLE` / `QUERY_ENCODER_UNAVAILABLE`**（不是静默的文本页）；
  无 session 的 `POST /search` → **401**；响应体不含工作目录/数据根/图库路径。
- **同时复验的既有断言**：shell/places 页面与资源、session 303 重定向与单次凭证、scan→SUCCEEDED、
  `GET /assets?text=` 文本检索与投影摘要、缩略图与原图离线语义（`SOURCE_FILE_MISSING`）、访问日志脱敏、
  `stats` 无 session → 401。`asset_count` 期望按夹具改为 2（第二张照片用于真实翻页）。
- **清理**：本次创建的 `D:\PhotoSearchSmoke\P2-06A`（3.54 GB）在 smoke 结束后删除；D 盘可用空间
  160.98 GB → **164.29 GB**；C 盘未写入本次 bundle（仍 8.75 GB 可用）。`dist/PhotoSearch` 属于常规构建产物，保留。

### 9.1 C 级顺带发现的既有缺陷（未修复，需后续任务）

**同一 `data_root` 第二次启动打包应用会失败**：`api/session.py::restrict_credential_file` 用
`icacls <file> /inheritance:r /grant:r "<user>:(R,W)"` 收紧凭据文件后，文件 DACL 只剩 `(R,W)`（实测
`icacls` 输出：`session.json TCC_SZ\28288:(R,W)`），**没有 DELETE 权限**；下一次启动用
`os.replace(tmp, session.json)` 轮换凭据时因此抛
`PermissionError: [WinError 5]`，启动器直接退出。

复现：对同一个 `--data-root` 连续运行两次 `packaged_api_smoke.py`（第一次成功、第二次在
`bootstrap_session_credential` 崩溃），本次实测即由此触发（第一次 smoke 因另一处问题失败后，第二次在旧
data root 上复现）。这是 P1-04 的既有行为，**不在本卡允许修改面内**（`api/session.py`、本地访问边界、
Windows ACL），因此保持原样并升级建议：需要 C1/C2 决策的独立任务（建议 ID `P1-07`，C1，验收 B+C），
候选修法是让凭据写入走「先删除目标/换名备份」或把 DELETE 权限一并授予（`(R,W,D)`），并加一条
「同一 data root 连续两次启动」的打包 smoke 断言。本卡未删除、未改写该凭据文件逻辑。

## 10. 隐私与访问边界

- 响应/错误里没有绝对路径、坐标或查询向量：用例对 `POST /search`、`GET /search-profile`、
  激活拒绝与打包 smoke 的响应体断言 `<library root>`、`C:\\`、`latitude`、会话令牌均不出现；
  `pending_semantic`/`channels`/`filters` 只带 id、状态与计数。
- 引擎错误只记录异常类型名（沿用 P2-05）；本卡新增代码不新增任何日志路径或凭据输出。
- 未新增「无 session 可达」的端点：三条新路径全部在 `/api/v1/` 保护范围内（实测 401/403）。

## 11. 未验证项与残留边界

1. **真实查询编码器未接入应用进程**：本卡没有实现 ADR-0003 §7 的真实 adapter，`POST /search` 的文本
   查询在打包应用里会以 409 `QUERY_ENCODER_UNAVAILABLE` 拒绝（而不是降级为纯文本页）。因此本卡**不声称**
   端到端语义检索可用；`GET /capabilities.search.query_encoder_available` 如实为 `false`。接入真实 adapter
   属后续卡（P2-06B 的精排/反馈亦不解决查询塔），必须重跑 C 级。
2. `search_profiles` 目前没有产品侧**创建**入口（模型升级/索引管理属后续卡）：测试与 smoke 直接按 DDL
   写入 profile/generation 行；`GET /api/v1/search-profiles`（复数）与 `POST /api/v1/index-generations`
   仍不存在。
3. profile 的 `fusion_config` 只做冻结 RRF 校验与回显；`reranker_processor_id` 只存储不启用（P2-06B）。
4. 会话是**进程内**缓存：重启/多进程（多 worker）之间不共享，也未测多进程并发下的会话隔离；
   `MAX_SESSIONS=64` 的 LRU 行为只在单进程单元测试中验证。
5. 候选上界与规模：`top_k ≤ 1000`、`MAX_SESSION_IDS = 1000` 是卡定上界；夹具只有 2～3 个候选，
   因此「`page_size` 达到上界且 `has_more=true`」只验证了公式与满页情形，未在大库规模验证。
6. `GET /assets` 仍非快照隔离读（沿用 P1-04/P2-03A 记录）；本卡的搜索页内每行复核也不是快照读，
   只是「同一会话同一 revision + 逐行权威复核」。
7. 未验证并发写入者与搜索的竞争（会话内 revision 变化只报过期，不自动补命中；沿用 P2-05 §11.7）。
8. 打包 smoke 的搜索检查使用**纯筛选**请求（本卡无编码器，见 §11.1）；文本路径只在注入确定性假编码器的
   unit/integration 中验收，不代表真实模型的召回质量。
9. §9.1 的凭据文件 ACL 缺陷未修复，同一 data root 的二次启动会失败（影响 `packaged_api_smoke.py` 的重复运行，
   也影响真实用户的重复启动）；本卡的 smoke 每次都使用新建的工作目录。

## 12. 结论与交接

P2-06A 的「`SearchRequest`/`SearchResult` 冻结协议 + `POST /api/v1/search` + 进程内搜索会话与不透明游标
（查询/位置绑定、TTL、LRU、逐页权威复核）+ 不可变 `search_profiles`/单行活动指针与**一个事务内**的
profile↔generation↔runtime 核对与 `is_active` 联动 + `catalog_filter_revision`/`suggestion_revision` 过期判定」
闭环已按 **B + C + D** 真实验收，因此标记 PASS。`schema_version = 7`，migration 连续 `0001`～`0007`；
ADR/冻结 JSON schema/依赖/`places/**`/`geo/**`/`ingestion/**`/`workers/**`/打包入口/前端均未改动
（除 §1 说明的 `api/context.py`、`app.py` 两处加性注入缝）。

下一任务是 **P2-06B — 显式相关性反馈与精排适配器接入**（`docs/tasks/P2-06B.md`，已存在且与本卡实现一致：
依赖 `SUPPORTED_SCHEMA_VERSION = 7`、`search_profiles`/`active_search_profile`、`POST /api/v1/search` 与会话/游标语义），
推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**。
