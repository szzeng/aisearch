# P2-06B — 显式相关性反馈与精排适配器接入报告

日期：2026-09-14。状态：**PASS（B 级完整 unit/integration + C 级打包 smoke + D 级迁移/幂等/门禁故障注入）**。

本任务只实现主架构 §9.2（"可选 VLM 精排单独请求、单独超时，基础结果先返回"）、§10.2、§11.3.5
与 ADR-0003 §4.2/§7/§8 已冻结的语义。反馈与精排都**不是**权威分类：本任务没有让反馈改变可见性、
地点/人物判定或下一轮排序，也没有把精排分数混入 RRF/BM25/距离。全部夹具都在 `%TEMP%` 与本次创建的
D 盘 smoke 目录内；没有读取、移动或删除任何真实图库，没有联网，没有新增依赖，也没有把任何真实模型
权重接入应用路径。

> 本卡实现过程中**没有**出现需要 C2 的协议歧义：反馈的存储/幂等契约、精排分数与 RRF 的关系、
> `reranker_processor_id` 的资格门禁都能直接由 §9.2/§10.2 与 ADR-0003 §4.2/§7/§8 推出。§6 记录了
> 卡中未逐字规定的四处判定，以及为什么按此实现。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `75d467b`（P2-06A），开始时 `git status --porcelain` 为空 |
| 本次实现提交 | 见 `git log -1 --format=%H -- docs/reports/P2-06B-search-feedback-and-rerank-adapter.md`（提交信息以 `P2-06B:` 开头；未 push，远端仍待用户确认） |
| 前置 | P2-06A PASS；`SUPPORTED_SCHEMA_VERSION = 7`、migration 连续 `0001`～`0007`；`search_profiles`/`active_search_profile`、`POST /api/v1/search` 与会话/游标语义可用；P2-02 的 capability/evidence 与 P2-03 的 artifact/outbox/index 状态机未改 |
| 新 migration | `catalog/migrations/0008_search_feedback.sql`，**`SUPPORTED_SCHEMA_VERSION` 推进到 8**，`0001`～`0007` 逐字节未改 |
| 新生产模块 | `src/photo_search/search/rerank.py` |
| 改动生产文件 | `search/{contracts,service,sessions,profile}.py`、`api/{models,routes,errors}.py`、`catalog/{schema,errors}.py`、**`api/context.py` 与 `app.py`**（见下） |
| 新增测试 | 5 个文件、**49 个用例**（反馈契约 19、精排边界 15、反馈迁移 6、反馈 API 3、精排/门禁 API 6） |
| 夹具扩展 | `tests/p2_search_fixtures.py`（`reranker_spec`/`FakeReranker`）、`tests/p2_search_api_fixtures.py`（`reranker_factory` 注入、`record_reranker_evidence`） |
| 版本耦合测试的机械更新 | 3 个既有文件（`test_catalog_migrations.py`、`test_p2_scheduler_recovery.py` 补 `search_feedback` 表名；`test_p2_search_profile_migration.py` 的 `applied` 期望改为 `range(7, SUPPORTED+1)`），未改任何语义断言 |

**超出卡内「允许修改」清单的两处（有意、最小，与 P2-06A 同因）**：`api/context.py` 与 `app.py`。
理由是 ADR-0003 §7 要求业务层不 import ONNX/PyTorch，精排适配器只能像 P2-06A 的查询编码器那样由部署
（或测试）注入；本仓库唯一"一个应用实例所拥有的一切"的宿主就是 `ApiContext`。两处改动都是加性的
（`ApiContext` 新增 `reranker_factory`/`reranker()`，`create_app` 多转发一个形参），不改任何既有行为。

未修改：ADR-0001～0005 与冻结 JSON schema、`0001`～`0007`、五字段 job 身份、lease/attempts/迟到提交、
资产/位置/缺失判定、P2-03A/B 人工锁/排除/幂等/批量/撤销语义、P2-04 tokenizer 世代/MATCH 策略/
`place_text` 来源规则、P2-05 RRF 公式/并列规则/`hit_reason`/通道权重、P2-06A 会话/游标/profile 绑定
语义、`space_id` 语义、`scheduler/eligibility.py` 的资格矩阵、`places/**`、`geo/**`、`ingestion/**`、
`workers/**`、打包入口、前端资源、`pyproject.toml`、`uv.lock`。`scheduler/capability.py` 也**未改**：
reranker 门禁只调用既有的 `latest_capability`/`qualification_evidence` 读取函数。

## 2. 显式相关性反馈：协议与持久化

`0008_search_feedback.sql` 新增一张**只追加**的审计表（`search_feedback`）：

| 列 | 约束 |
|---|---|
| `feedback_id` | `INTEGER PRIMARY KEY`（只追加，故单调） |
| `request_id` | `NOT NULL UNIQUE`，非空且 `length ≤ 64`（幂等键） |
| `search_session_id` | 非空且 `length ≤ 128`；**不是**外键——搜索会话是进程内状态（§9.5），审计行要比会话活得久 |
| `asset_id` | `REFERENCES assets(asset_id) ON DELETE RESTRICT`（反馈不能指向不存在的资产，也不能创建资产） |
| `rank` | `CHECK(rank >= 1)` |
| `relevance` | `CHECK(relevance IN ('RELEVANT','NOT_RELEVANT'))` |
| `note` | 可空，`CHECK(note IS NULL OR length(note) <= 500)` |
| `created_at` | `CHECK(created_at >= 0)` |

`trg_search_feedback_append_only_update` / `..._delete` 拒绝一切 `UPDATE`/`DELETE`；修正意见是携带新
`request_id` 的新行，历史保持可读。索引 `idx_search_feedback_session` 服务"某个会话的意见"读取；全局分页
走主键 keyset。

协议（`search/contracts.py`）是形状封闭的三个信封：`FeedbackRequest`（`request_id`/`search_session_id`/
`asset_id`/`rank`/`relevance`/`note`）、`FeedbackRecord`（落库行）、`FeedbackResponse`（`replayed` +
记录）与 `FeedbackPage`（`feedback`/`limit`/`next_cursor`/`has_more`）。`FeedbackRequest.payload_identity()`
固定"同一 `request_id` 必须复现的五个事实"，幂等比较与冲突诊断都以它为准。

幂等矩阵（全部实测）：

| 情况 | 结果 |
|---|---|
| 新 `request_id` | `201`，`replayed=false`，写入恰好一行 |
| 同 `request_id` + 同载荷 | `200`，`replayed=true`，返回**同一** `feedback_id`，零写、行数不变 |
| 同 `request_id` + 不同载荷 | `409 FEEDBACK_IDEMPOTENCY_CONFLICT`（`details.different_fields` 指出字段），零写，原判断保持不变 |
| 未知 `asset_id` | `422 VALIDATION_ERROR`（`field=asset_id`），零写 |
| 非法 `relevance` / `rank<1` / `note>500` / 缺字段 / 超长 id | `422`（pydantic 与 envelope 两层） |
| 无 session / 缺 CSRF / 外来 Origin | `401` / `403 CSRF_INVALID` / `403 ORIGIN_NOT_ALLOWED` |
| 任何 `UPDATE`/`DELETE`（含直接写库） | `sqlite3.IntegrityError`（触发器） |

`GET /api/v1/search/feedback` 是只读 keyset 分页（游标前缀 `fb1`），只返回反馈自身字段：没有地点、
没有坐标、没有路径、没有查询文本。反馈**不改变** `catalog_filter_revision`、`suggestion_revision`、
`search_documents`、`assets.created_at`、`index_entries`/`index_outbox` 行数或任何向量行；被标记
`NOT_RELEVANT` 的照片仍出现在下一次查询里（用例逐项对比前后快照，并对比两次同查询的 `hits`）。

## 3. 精排适配器边界（`search/rerank.py`，不实现真实模型）

`RerankerAdapter` 只有 ADR-0003 §7 的两个方法：`spec()` 与 `score(query, candidates, cancellation)`。
`RerankCandidate(asset_id, rank)` 是输入；`RerankRow(asset_id, rank, reranker_score, reranker_processor_id)`
是输出行——**行里没有 `score` 字段**，基础 RRF 分数不在其中、也不被改写。

| `rerank_state` | 含义 | 行 |
|---|---|---|
| `NOT_REQUESTED` | 调用方没有请求精排 | `null` |
| `OK` | 精排真实跑过 | 逐行候选分数 |
| `UNAVAILABLE` | 请求了，但本进程/本 profile 跑不了 | 恒为空 |
| `FAILED` | 跑了但没得到可用分数（异常/超时/结果违约/spec 违约） | 恒为空 |

`reranked` 块始终携带 `state`/`reason`/`processor_id`/`candidates`/`limit`/`timeout_ms`/`elapsed_ms`/
`rows`/`error`；`error` 只记 `code` 与异常**类型名**，不记 message（避免路径泄漏，沿用 P2-05 的规则）。

- **有界等待**：`score()` 在 daemon 线程里执行，最多等待 `timeout_ms`（默认 2000 ms）；超时记
  `FAILED`/`RERANK_TIMEOUT` 并立刻用基础结果作答，同一 deadline 以 `Cancellation` 传给适配器供其自查
  （ADR-0003 §7）。**没有**抢占式强杀执行线程，报告中不声称支持。
- **结果校验**：非候选资产、重复候选、非有限数值、缺字段、非 mapping 一律 `RERANKER_RESULT_INVALID`
  （零行），排序固定为 `(-reranker_score, asset_id)`，因此确定性假适配器可逐位复算。
- **reason 词汇**：`PROFILE_HAS_NO_RERANKER`、`NO_QUERY_TEXT`、`RERANKER_ADAPTER_UNAVAILABLE`、
  `RERANKER_PROCESSOR_MISMATCH`、`RERANKER_SPEC_INVALID`、`RERANK_TIMEOUT`、`RERANKER_FAILED`、
  `RERANKER_RESULT_INVALID`，以及门禁的 `RERANKER_QUALIFICATION_MISSING`/`CAPABILITY_MISSING`/
  `RUNTIME_UNHEALTHY`。

候选头有界：`DEFAULT_RERANK_CANDIDATES = 50`，硬上限 `MAX_RERANK_CANDIDATES = 100`，`candidates`/
`limit` 如实回报。精排候选来自会话冻结的有序候选，因此续页永远回显**同一块**（不重跑、不可能换序）。

## 4. Profile 门禁（reranker readiness）

`search/profile.py::validate_profile_readiness` 在原嵌入处理器核对之后**加性**补上：
`reranker_processor_id` 非空的 profile 必须能在同一 `runtime_package_id` 上看到健康 capability 快照，
并存在该 processor/device/precision 的专项 evidence，否则激活在**同一事务内**以
`409 RUNTIME_CAPABILITY_UNAVAILABLE`（`reason=RERANKER_QUALIFICATION_MISSING`）拒绝、**零写**：活动指针
与 `is_active` 都不动。`reranker_processor_id` 为空的 profile 完全不受影响（返回 `None`，不是拒绝）。
资格矩阵语义未改：reranker 用的是与嵌入处理器**完全相同**的那条规则，没有新增/放宽任何设备判定。

同一函数也被请求侧复用（`search/service.py::_rerank_outcome`）：只有本进程真的能证明该 reranker 健康
且有 evidence 时才会调用适配器，否则如实返回 `UNAVAILABLE`。

## 5. API 变化

| 路径 | 说明 |
|---|---|
| `POST /api/v1/search/feedback` | `201`（新）或 `200`（同载荷重放）；结构化冲突 `409` |
| `GET /api/v1/search/feedback` | 只读 keyset 分页（`limit` 默认 50、上限 200） |
| `POST /api/v1/search` | 请求新增可选 `rerank: bool = false`；响应新增 `rerank_state` 与 `reranked` |

三条路径全部走既有 session/Host/Origin/CSRF 边界（无 session → 401；缺 CSRF/外来 Origin → 403），
`FEEDBACK_IDEMPOTENCY_CONFLICT` 在 `api/errors.py` 映射为 `409`。P2-06A 的 `SearchRequest`/`SearchResult`
字段集合只**增加**了上述三处，`tests/unit/test_p2_search_contracts.py` 的逐字段守卫在没有改动的情况下
继续通过（新字段同时加在 dataclass、pydantic 模型与 envelope 上）。

## 6. 卡中未逐字规定、需要写进报告的四处判定

1. **未知 `asset_id` 记 `422 VALIDATION_ERROR`（`field=asset_id`），不用 `ASSET_NOT_FOUND`/404**：卡明确
   把"未知 asset"归入 422 调用方错误；`ASSET_NOT_FOUND` 在本仓库是"路径参数指向的资产不存在"的 404
   语义，混用会让两种失败不可区分。DDL 的 `ON DELETE RESTRICT` 外键是第二层防线。
2. **`rerank_state` 增加 `NOT_REQUESTED`**：卡的原文只要求"显式标注 `rerank_state=UNAVAILABLE`"。
   若不请求精排也返回 `UNAVAILABLE`，就无法区分"没要"和"要了但没有可用精排"。因此新增
   `NOT_REQUESTED` 并让 `reranked` 为 `null`；只有请求过精排的页面才带块。
3. **续页回显同一块**：会话冻结的是同一份排序与同一份精排块（含 `elapsed_ms`），所以续页不重跑精排、
   也不可能出现"同一会话两次不同精排"。这与 `query`/`channels`/`fusion_config` 一致。
4. **reranker 的 `CAPABILITY_MISSING`/`RUNTIME_UNHEALTHY` 分支当前不可达**：profile 的
   `runtime_package_id` 只有一个，嵌入处理器与 reranker 共用它，而嵌入门的同类检查先执行。因此
   `reranker_readiness` 里这两条分支是防御性代码；可达且被 D-4 实测的是
   `RERANKER_QUALIFICATION_MISSING`。报告中不声称前者已实测。

## 7. D 级故障注入（逐项实测）

| # | 卡要求 | 实测（测试名） |
|---|---|---|
| 1 | v7→v8 迁移：备份、单事务、旧行不变；损坏/缺件/过新 | `test_p2_search_feedback_migration.py`：备份内 `schema_version=7` 且无 `search_feedback`、`applied=(8,)`、7 组冻结行（含已激活 profile 指针）逐项不变、二次迁移 `applied=()`；注入损坏 `0008` → `MIGRATION_FAILED` 且版本仍 7、无半成品表；`DROP TRIGGER trg_search_feedback_append_only_delete` → `SCHEMA_INCONSISTENT`；v9 → `SCHEMA_TOO_NEW` 且主库字节与目录项不变 |
| 2 | 反馈幂等：同载荷同结果/不同载荷冲突/422/触发器 | `test_p2_search_feedback_api.py`（201→200 同一 `feedback_id`、409 且 `different_fields=["relevance"]`、行仍为原值；未知 asset 422 `field=asset_id`；rank/relevance/note/缺字段 422；无 session 401；缺 CSRF 403；外来 Origin 403；行数 0）+ `test_the_feedback_log_is_append_only_for_any_writer`（同一连接上 `UPDATE`/`DELETE` 抛 `IntegrityError`，行逐字段不变）+ 迁移测试的 DDL 反例（重复 `request_id`、未知 asset、非法枚举、`rank=0`、`note=501`、空 `request_id`、负时间） |
| 3 | 反馈不影响权威 | `test_a_verdict_is_idempotent_auditable_and_changes_no_authority`：写入前后 `catalog_filter_revision`、`suggestion_revision`、`search_documents` 行、每个 `assets.created_at`、`index_entries`/`index_outbox` 行数逐项相同（只有 `feedback_rows` 0→2），且下一次同查询的 `hits` 与首次逐字段一致（`NOT_RELEVANT` 的照片仍在结果里） |
| 4 | 精排门禁：无 healthy capability/无 evidence 拒绝且零写；中断回滚 | `test_a_requested_rerank_is_a_separate_block_that_never_touches_scores`：带 reranker 的 profile 在无 evidence 时激活 409 `RERANKER_QUALIFICATION_MISSING`，指针 `None`、`is_active` 逐项不变；`test_an_interrupted_reranker_activation_rolls_back_both_writes`：注入 `activation_hook` 异常 → 指针与 `is_active` 全部回滚，补 evidence 后同一调用成功且 `readiness.reranker.processor_id` 正确 |
| 5 | 精排失败/超时：基础结果不变、状态如实 | `test_a_rerank_that_cannot_run_leaves_the_base_page_untouched`（3 例参数化：无适配器 → `UNAVAILABLE`/`RERANKER_ADAPTER_UNAVAILABLE`；适配器抛异常 → `FAILED`/`RERANKER_FAILED`；适配器 2500 ms 慢于 2000 ms 预算 → `FAILED`/`RERANK_TIMEOUT`）逐例对比 `hits`/`channels`/`total_candidates`/`state` 与不请求精排时**完全相同**，且 `rows == []` |
| 6 | 分数分离与确定性 | `test_p2_search_rerank.py`（行只含 `asset_id`/`rank`/`reranker_score`/`reranker_processor_id`、无 `score`、无百分比；同请求两次 `to_envelope()` 逐位相同；排序固定为分数降序 + `asset_id` 兜底；`rerank_score` 由确定性适配器复算一致）+ 端点用例断言 `reranked.hits == base.hits`、`reranked.rows` 顺序 `[0.9,0.5,0.1]` 而基础名次为 `[3,2,1]`（两套顺序可区分）、重复请求 `rows` 相同、续页块与首页相同 |
| 7 | 继承回归：P2-06A 会话/游标/profile 用例 | `tests/integration/test_p2_search_endpoint.py` 全部用例**未改一行**并原样通过（含 TTL/重启过期、游标隔离、切换 profile 后旧会话过期、激活拒绝零写、`catalog_filter_revision` 过期）；唯一改动是 `test_p2_search_profile_migration.py` 的 `applied` 期望从 `(7,)` 改为 `range(7, SUPPORTED+1)`（版本耦合，无语义变化） |

## 8. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **719** 例，进度行 1 个 `s` → **718 passed / 1 skipped**（P2-06A 为 670 例，本卡新增 49 例） |
| 本任务 5 个新文件 | exit 0，**49 passed**（契约 19、精排边界 15、迁移 6、反馈 API 3、精排/门禁 API 6） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages in 1ms`（依赖未变） |
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（≈61 s），`dist/PhotoSearch/PhotoSearch.exe` = 35,557,186 B |
| `uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P2-06B` | exit 0，`status=ok`、`problems=[]`、startup **1.0 s** |

本环境沿用既有事实：`uv` 默认缓存与 pytest basetemp 在沙箱内被拒绝（`os error 5`），因此上述命令在沙箱外执行
（无功能影响）；C 级构建与 smoke 的工作目录放在 **D 盘**（C 盘余量不足以复制约 3.4 GB bundle）。

## 9. C 级 bundle 审计与 smoke 证据

- **package-data 审计**（`pyi-archive_viewer -r -l dist/PhotoSearch/PhotoSearch.exe` 递归列出 PYZ）：
  `photo_search.search.rerank`、`photo_search.search.{service,contracts,sessions,profile}`、
  `photo_search.api.{routes,context}` 均入包；`_internal/photo_search/catalog/migrations/` 内
  **`0008_search_feedback.sql`** 与 `0001`～`0007` 同时存在。
- **smoke 新增检查（本次全部为真）**：`POST /api/v1/search/feedback` → **201**（`replayed=false`，
  `feedback_id=1`）；同载荷重放 → **200** 且返回同一记录；不同载荷 → **409
  `FEEDBACK_IDEMPOTENCY_CONFLICT`**；`GET /api/v1/search/feedback?limit=10` → **200**、1 行；
  三个响应体都不含工作目录/数据根/图库路径；无 session 的反馈请求 → **401**。
  `POST /api/v1/search` 带 `rerank: true`（本构建未激活 profile、无 reranker）→ **200**、
  `rerank_state=UNAVAILABLE`、`reason=PROFILE_HAS_NO_RERANKER`、`rows=[]`，且 `hits` 与不带 `rerank`
  的同一请求逐字段相同。
- **同时复验的既有断言**：shell/places 页面与资源、session 303 重定向与单次凭证、scan→SUCCEEDED、
  `GET /assets?text=` 文本检索与投影摘要、`POST /search` 的显式绑定/续页/无编码器 409、缩略图与原图
  离线语义（`SOURCE_FILE_MISSING`）、访问日志脱敏、`stats` 无 session → 401。
- **清理**：`D:\PhotoSearchSmoke\P2-06B` 与 `P2-06B-evidence` 两份约 3.5 GB 的 bundle 副本在 smoke
  结束后删除（D 盘可用空间 169.29 GB → 176.35 GB）；`dist/PhotoSearch` 属于常规构建产物，保留。
  **残留**：两个 112 字节的 `PhotoSearchData 数据 目录\runtime\session.json` 无法删除（见 §9.1），
  只留空目录与凭据文件本身，未做 takeown 或提权改写 ACL。

### 9.1 顺带复核：既有凭据文件 ACL 缺陷仍存在（未修复，不属本卡范围）

P2-06A §9.1 记录的 `api/session.py::restrict_credential_file`（`icacls ... /inheritance:r
/grant:r "<user>:(R,W)"` 使凭据文件缺少 DELETE 权限）未被本卡触碰。本次为取得专项证据在同一台机器上
对**两个不同工作目录**各跑了一次 smoke（都成功）；因此"同一 `data_root` 连续两次启动"的失败路径本次
**未复现也未修复**，仍按 P2-06A 报告升级为独立 C1 任务。

本次顺带得到一个**新的可观察症状**：该 DACL 让凭据文件对普通用户既不可删除也不可重新授权——
smoke 工作目录删除后只剩它自己。实测 `icacls "<...>\runtime\session.json"` 输出
`TCC_SZ\28288:(R,W)`（无 DELETE / WRITE_DAC，继承已去掉），`Remove-Item -Recurse -Force`
与 `icacls /grant "<user>:(F)"` 都被拒绝，只能靠 takeown 或管理员权限清理。这说明该缺陷不只影响
第二次启动，也让"用完即删"的临时数据目录无法完整回收；本卡未做 takeown，也未改 `api/session.py`。

## 10. 隐私与访问边界

- 反馈与精排响应里没有绝对路径、坐标或查询向量：用例对 POST/GET 反馈与精排响应断言工作目录、数据根、
  图库根与 `latitude`/`longitude` 均不出现；`reranked.error` 只带 `code` 与异常类型名（单元用例专门用
  一个"message 里含 `C:\photos\secret\...`"的异常证明它不会外泄）。
- 反馈表只存 `request_id`/会话 id/资产 id/名次/判断/备注/时间；没有地点、坐标、来源路径或查询文本。
- 引擎错误只记录异常类型名（沿用 P2-05）；本卡新增代码不新增日志路径或凭据输出。
- 未新增"无 session 可达"的端点：两条新路径都在 `/api/v1/` 保护范围内（实测 401/403）。

## 11. 未验证项与残留边界

1. **真实精排模型未接入**：本卡只实现 ADR-0003 §7 的边界与确定性假适配器，没有真实 VLM/reranker 权重、
   没有 GPU 精排延迟或质量数据。因此**不声称**精排效果、召回提升或任何排序质量结论；打包应用里
   `rerank: true` 会如实返回 `UNAVAILABLE`。
2. **在线学习/反馈驱动排序未实现**（也不是本卡目标）：反馈只被记录，不改变可见性、权威筛选或下一轮
   排序。把反馈用于训练或重排需要新的产品决策与契约。
3. **超时不是抢占式中断**：超时后请求立即用基础结果作答，但被放弃的适配器线程只能在它自己检查
   `Cancellation` 后退出；没有强杀机制，也没有对真实模型做超时实测。
4. **reranker 的 capability-missing/unhealthy 分支不可达**（见 §6.4）：只有共用 runtime package 的
   嵌入门先执行这一事实；`RERANKER_QUALIFICATION_MISSING` 已实测。
5. **反馈规模未测**：keyset 分页与唯一键只在个位数行上验证；没有大表规模、并发写入者或跨进程写入。
6. **精排 candidate 上界 50/100 只在 ≤4 候选夹具上验证**；`MAX_RERANK_CANDIDATES` 是硬上限，
   未在真实大页面上测延迟。
7. 会话仍是进程内状态（沿用 P2-06A §11.4）；反馈是持久表，两者语义不同，本卡未把二者混淆。
8. `GET /api/v1/search-profiles`（复数）、`POST /api/v1/index-generations` 等产品侧创建入口仍不存在；
   测试与 smoke 直接按 DDL 写 profile/generation 行。
9. P2-05 的 RRF 常数与通道权重仍是冻结值，本卡没有用它"吸收"反馈或精排分数。
10. 既有凭据文件 ACL 缺陷未修复（§9.1）；打包 smoke 每次仍必须使用**新建**的工作目录。

## 12. 结论与交接

P2-06B 的「只追加、按 `request_id` 幂等的显式相关性反馈（201/200/409 + 触发器的不可改写审计链）+
可选精排适配器边界（`spec()`/`score()`、有界等待、结构化状态、候选分数与 RRF 严格分离）+ 带 reranker
资格的 profile 激活门禁（零写拒绝、中断整体回滚）」闭环已按 **B + C + D** 真实验收，因此标记 PASS。
`schema_version = 8`，migration 连续 `0001`～`0008`；ADR/冻结 JSON schema/依赖/`places/**`/`geo/**`/
`ingestion/**`/`workers/**`/打包入口/前端均未改动（除 §1 说明的 `api/context.py`、`app.py` 两处加性注入缝）。

下一任务是 **P2-07 — 搜索页、上传搜图、地点筛选、命中原因与分项覆盖率**（`docs/tasks/P2-07.md`），
已按本卡要求生成：搜索页/上传搜图/地点筛选/依据与命中原因/分项覆盖率，验收 B + 真实浏览器 smoke，
发行资源变化再加 C；不得提前实现 P2-08 的综合故障验收。
