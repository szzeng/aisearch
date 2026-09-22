# P3-08C 阶段 A 报告：P3-08B 的独立 DS-H 全量工程主审

- 执行模型：DS-H / DeepSeek V4.1 Flash / 高推理（模型切换由用户在 Codex 中手动完成）
- 起始基线：`c7fa68b`（P3-08A 收尾提交）**加上 P3-08B 的全部未提交改动**；开始时实测
  `git status --short` 为 **25 modified + 18 untracked = 43 项**（与本卡 §起始状态一致）
- 权威输入：ADR-0010（Accepted）、`contracts/p3-face-redetection-contracts.v1.json`、
  `contracts/p3-acceptance-matrix.v1.json`、`tests/unit/test_p3_face_redetection_contracts.py`（58 例）、
  [P3-08B 报告](P3-08B-face-redetection-mapping-and-fault-closure.md)、`docs/tasks/P3-08B.md`、
  `docs/development-status.md`
- 结论：**FAIL（附最小反例与修复范围）**。映射错误面的 `VALIDATION_ERROR` `details` 在**真实 HTTP 响应**上
  违反 ADR-0010 §8.2 已冻结的逐码闭集，且本卡自带 companion 校验器会拒绝这些响应（`$: oneOf matched 0`）。
  该缺陷不在 ADR/companion 里，而在 `persons/read.py` 与 `api/routes.py` 的实现面，修复面很小且完全在
  本卡允许的短审边界内（§5）。

本卡是审查卡：**未改任何生产代码、migration、ADR、companion 或既有测试语义**；只新增本报告、更新
`docs/development-status.md` 与本卡结论段。临时反例探针写在被 `.gitignore` 忽略的
`.pytest-basetemp-p308c/` 内（§5.2/§6.2），不进源码树。

## 1. 范围与总评

1. **改动面与报告对账**：P3-08B 报告 §2 的逐文件表与真实 diff 基本一致；唯一遗漏项是
   `.gitignore`（新增 `.pytest-basetemp-*/` 忽略规则，3 行），属文档完备性问题，不影响判定（§3）。
2. **迁移与 schema**：`0014` 的两张新表、外键/唯一/闭集、第三次日志重建、`0001`～`0013` 逐字节保留与
   `schema_version 13 → 14` 均成立（§4.1）。
3. **不变量对照**：ADR-0010 §2.1、§3.1～§3.4、§4、§5.1～§5.4、§6、§7、§10 的绝大多数条款在实现中被
   忠实落地，且都有本卡新增的真实证据；发现 **1 处冻结闭集违约（§5）** 与 **1 处 ADR 未规定、实现自选终态
   的语义空白（§6）**。
4. **测试证据**：独立复跑专项 108/0/0/0、完整 1450/0/0/48，Ruff / `git diff --check` / `uv lock --check`
   全 clean（§2），与报告声称一致。
5. **未验证项**：报告 §7 五条经逐条裁定后均可接受为本卡范围外或已批准的环境事实（§8）。

P3-08B 的 PASS 声明在本轮独立主审下**未被接受**：其生产面、迁移与绝大多数不变量合格，但公开错误面的
冻结闭集没有真正做到"逐码旁闭"。按本卡要求，"不得为让审查通过而放宽契约"，因此本轮不能写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`，也**未生成 P3-08D**。

## 2. 独立复跑证据

环境事实：受限令牌下 `.pytest-tmp` / 新 basetemp 的 `PermissionError [WinError 5]` 仍然存在（未改 ACL）。
本轮全部测试改用项目内新 basetemp `.pytest-basetemp-p308c/<fresh>`，并经由正常 Windows 权限路由执行。
（首次运行因 basetemp 父目录不存在而全量 setup error，属命令形态问题，不是产品失败；补齐父目录后重跑。）

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
# 专项批（本卡点名的 11 个文件）
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p3_face_redetection_contracts.py tests/unit/test_p3_face_redetection_matching.py `
  tests/unit/test_p3_face_redetection_decisions.py tests/unit/test_p3_face_redetection_api_privacy.py `
  tests/integration/test_p3_face_redetection_mapping.py tests/integration/test_p3_face_redetection_decisions.py `
  tests/integration/test_p3_face_redetection_hold.py tests/integration/test_p3_face_redetection_restart.py `
  tests/integration/test_p3_face_redetection_upgrade.py tests/integration/test_p3_person_undo_ordering.py `
  tests/integration/test_p3_person_place_interleave.py `
  --basetemp=.pytest-basetemp-p308c/review-a2 --junitxml=test-results/p3-08c-dsh-focused.xml

# 完整套件
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p308c/review-a-full --junitxml=test-results/p3-08c-full.xml

uv run --no-sync ruff check .
git diff --check
uv lock --check
```

| 证据 | tests | failures | errors | skipped | 备注 |
|---|---:|---:|---:|---:|---|
| `test-results/p3-08c-dsh-focused.xml` | 108 | 0 | 0 | 0 | 审查者独立复跑，与报告 §4 的 108 一致 |
| `test-results/p3-08c-full.xml` | 1450 | 0 | 0 | 48 | 189.0 s；48 skip 为"未安装 pinned 权重"环境跳过 |
| Ruff / `git diff --check` / `uv lock --check` | — | — | — | — | 全 clean；`uv lock --check` 95 packages |

**关键观察：测试全绿只证明"被断言的面"，不证明"未被断言的面"。** §5 的两类违约恰好落在本卡测试的
断言盲区：`test_pagination_bounds_are_enforced` 只断言 `code == VALIDATION_ERROR` 与 `INVALID_CURSOR.details == {}`，
`test_mapping_endpoints_are_closed_over_http` 只断言 `bad_limit` 的 status 与 `code`，两个用例都没有读取
`limit`/`state` 越界响应的 `details` 键集（见 §5.3）。

## 3. 改动文件清单与逐项判定

对账结果：`git status --short` = 25 个 modified + 18 个 untracked（详细清单见 P3-08B 报告 §2 表格），
与 §2 表格逐项一致，**唯一未在表格中出现的是 `.gitignore`**（+3 行：`.pytest-basetemp-*/`）。

| 面 | 文件 | 判定 |
|---|---|---|
| migration | `catalog/migrations/0014_face_redetection_mapping.sql`（新） | 通过：两表 + 闭集 CHECK + 自映射 CHECK + `UNIQUE(old_face_id,new_face_id)` + `new_face_id` 无外键（有注释交代）；日志第三次重建保留列清单与别名引用；索引齐全 |
| schema | `catalog/schema.py` | 通过：`13 → 14`，文档与 ADR §9 对齐 |
| 错误码 | `catalog/errors.py` | 通过：7 个新码 + `MAX_MAPPING_DECISIONS=200` / `MAX_MAPPING_TARGETS=16` |
| 错误映射 | `api/errors.py` | 通过：404/409/422 逐码映射正确（`FACE_MAPPING_BATCH_TOO_LARGE` 422，其余冲突 409） |
| 候选生成 | `face/processor.py` | 通过：`persist_face_document()` 内调用候选生成（退休先于候选），`_retire_stale_faces()` 先调 `retire_mapping_faces()`；`human_controlled_face_ids()` 语义未变 |
| 关联写门 | `face/index.py` | 通过：`_mapping_hold_face_ids()` 与 `_manually_locked_face_ids()` 同事务同时点读取；动作区分 `PROVISIONAL_HOLD`；队列行由"无条件删除"改为保持/晚到冲突则保留 |
| 裁剪读取 | `media/face_crop.py` | 通过且有探针证明：同时接受 `[l,t,r,b]` 与 `{"bbox":[...]}`，坏行仍原码拒绝（§7.1） |
| 映射核心 | `persons/mapping.py`（新） | 主体通过：稳定键、五常量、四态、双向继承基数、保守未归类、肯定/否定冲突、目标受控拒绝、幂等先于 revision、一次确认一次 `PERSON_CHANGED`、`max(before,live)+1`、undo 非释放；**含 §6 的 STALE 终态自选语义** |
| 只读投影 | `persons/read.py` | **部分不通过**：`fm1` 空间、limit 校验、候选数组与 cross-field 约束正确；但 3 处 `VALIDATION_ERROR` 的 `details` 不在冻结闭集内（§5） |
| 操作日志路由 | `persons/operations.py` | 通过：`operation_family()` 的 `mapping` 分支、mapping 信封路由、`_replay()` 闭集冲突细节、ADR-0010 §10 顺序修复（§7.2） |
| 契约常量 | `persons/contracts.py`、`persons/service.py` | 通过：新类型/实体形状 + 三个薄门面 |
| API | `api/models.py`、`api/routes.py` | **部分不通过**：三条路径、`extra="forbid"` DTO、撤销 `response_model` 联合正确；清单路由的查询参数校验把通用 `{"errors":[...]}` 形状透传到冻结面（§5） |
| 能力计数 | `scheduler/projection.py` | 通过：仅在 0014 两表存在时加性加入 4 个计数，旧 schema 缺键而非报 0 |
| 前端 | `web/persons.{html,css,js}` | 通过（静态+记录式）：能力门控基于 `open_face_mappings` 存在性；面板默认 `hidden`；契约用例断言无私有字段名 |
| 测试 | `tests/**`、`tests/p3_redetection_fixtures.py`、`tools/diagnostics/face_mapping_smoke_fixture.py` | 通过：11 个本卡文件 + 夹具 + 诊断脚本；两个 `__init__.py` 用于消除同名测试模块冲突（全套 1450 例无 import mismatch） |
| 地点示例 | `tools/places/examples/mini-cities.{spec,manifest}.json` | 通过：上界 `13 → 14` 恰当。`content_sha256` 是**文件清单**哈希（相对路径/role/sha256/byte_size），与兼容区间无关，因此不变是正确的 |

未改动（核对通过）：ADR-0010 与两份 companion、ADR-0002～0009 及其 companion、`0001`～`0013`（逐字节）、
`uv.lock`、`PhotoSearch.spec`、搜索/反馈协议、检测模型与阈值、`space_id`、OCR/地点语义。

## 4. ADR-0010 预期 vs 实际差异表

| 条款 | 预期 | 实际 | 判定 |
|---|---|---|---|
| §2.1 稳定键/唯一/自映射 | `mapping_id` = 5 字段 canonical JSON 的 sha256；同对脸一行；自映射不生成且提交答 `FACE_MAPPING_NOT_FOUND` | `mapping_id_for()` 一致；`UNIQUE(old_face_id,new_face_id)` + `CHECK(old<>new)`；提交路径显式拒自对 | 一致 |
| §2.2 资产/世代锚点 | 跨资产永不生成；不读图、不依赖 EXIF | 生成按 `asset_id` 限定；提交按 `faces.asset_id` 复核；几何取 `bbox_json`/`landmarks_json` | 一致 |
| §2.3 审查状态 | 映射行 4 态、group 3 态 + 4 个 candidate_state；**都不是人物 authority** | 表结构与 CHECK 一致；人物事实只写 `face_assignment_controls`/`face_person_links`/`face_exclusions` | 一致 |
| §3.1 生成时机与范围 | 写文档同事务；只跨世代；同内容同 spec 重跑零新脸/零映射行/零新 group；同 spec 变化文档按既有词表拒绝 | `generate_candidates_for_asset()` 在 `persist_face_document()` 内、退休之后；`--old` 侧 `spec_revision <> current`；重跑 upsert 幂等（`test_unchanged_rerun_creates_no_mapping_rows`） | 一致 |
| §3.2 五个冻结常量 | 0.30/0.25/0.50/0.12 + LOW 不得 HIGH | 常量与分支一致；`test_low_quality_never_reaches_high_match_state` 通过 | 一致 |
| §3.3 候选状态 | 由**合格候选集合**推导；`NO_CANDIDATE` 表示"本次检测没有匹配脸"；不给默认目标 | 由映射行（排除 `STALE`）推导 → 在"同对脸被再次写入"的窄路径上与"合格候选集合"不等价；**实测 `NO_CANDIDATE` 而存在存活且合格的候选** | **差异（§6）** |
| §3.4 临时保持/队列 | 两谓词分离；判定顺序"写/退休 → 候选 → 失效 → 保持"；保持期保留队列、释放后重排队；释放集合只有 confirm/reject；`undo → OPEN` 不是释放 | `_mapping_hold_face_ids()` 在写事务内与人工锁/活动排除同点读取；`history` 测试 5 例全过（含 `test_stale_is_decided_before_the_hold_set`、`test_undo_reapplies_the_hold_and_requeues`） | 一致 |
| §4 转移矩阵 | 双向继承基数、保守 `MANUAL_UNASSIGNED`、exclusion 并集、肯定/否定冲突、目标受控拒绝、`NO_CANDIDATE` 只可 reject、永不自动确认 | `decide_face_mappings()` 整批先校验再写；`multiple_targets_without_single_inheritor`/`multiple_inheritors_for_new_face`/`human_affirm_deny_conflict`/`FACE_MAPPING_TARGET_CONTROLLED` 均有真实反例测试 | 一致 |
| §5.1 唯一操作权威 | 复用 `person_operations`/`person_operation_items`；每旧脸恰好一项；快照不含几何 | 第三次重建只放宽闭集；`entity_key = old_face_id`、`entity_id IS NULL`；`group_bundle()` 仅 id/state/revision | 一致 |
| §5.2 请求/批量/幂等 | 1..200 × 1..16；payload hash 对排序后 decisions；同 key 同 hash 先于 revision replay | `MAX_MAPPING_*` + 排序后的 canonical digest + `_check_idempotency()` 位于一切 revision 检查之前（`test_decision_idempotency_gate_precedes_revision_checks`） | 一致 |
| §5.3 决策语义 | confirm 命中/未命中矩阵；reject 不改人工事实；重复决策冲突；隔离与队列效应 | 与表格逐行一致；`test_reject_releases_the_hold_and_keeps_the_anchor` 等通过 | 一致 |
| §5.4 revision/补偿撤销 | `expected_review_revision` 实时比对；`max(before,live)+1`；不允许 undo an undo；快照不符整批 `FACE_MAPPING_OPERATION_NOT_UNDOABLE` + 安全 `conflicts` | 实现与 `test_mapping_undo_restores_state_forward_only`/`test_later_human_edit_blocks_mapping_undo` 一致 | 一致 |
| §6 提交顺序 | operation → group/映射 → 人工事实 → **仅人工事实真变化时**一次 `PERSON_CHANGED`；不写图片向量 outbox | `human_changed` 仅在 `item_answer()["changed"]` 为真时置位；`publish_derived(..., refresh=None)`；纯 reject 的 `changed=true` 而 filter revision 不变（测试显式固定） | 一致 |
| §7 重启/重扫/升级/回退/失效/备份 | 重启稳定；重跑幂等；升级建新组；回退方向反转不回滚人工事实；候选失效先于保持；崩溃后保持由权威推导 | 重启、备份恢复、晚到文档、升级失败 4 例 + 回退 1 例全部通过；失效先于保持见 `test_stale_is_decided_before_the_hold_set` | 一致（回退后的"同对脸重现"见 §6） |
| §8.1 只读面与公开边界 | 两读一写；`fm1` 独立空间；`limit` 1..200 拒绝而非夹紧；`state` 仅 open/all；投影无几何 | 路径、DTO、游标空间、limit 拒绝、`state` 取值全部存在；投影 `_scan()` 在真实响应上无私有字段 | 一致 |
| §8.2 冻结错误闭集 | 12 码；**逐码 `details` 闭集**，出现其他键即契约违规；`VALIDATION_ERROR` 必填 `reason`；`INVALID_CURSOR` 空对象 | 11 个已实现码的**目录面**细节合规；但映射面上 4 个真实 `VALIDATION_ERROR` 响应被 companion 拒绝 | **不通过（§5）** |
| §8.3 诚实自描述 | 四计数仅在 `schema_version >= 14 && ok` 时出现；旧 schema 缺键 | `_mapping_counts()` 仅在两表存在时 update；`test_capabilities_gate_is_honest` 用真实旧库（drop 两表）验证缺键 | 一致 |
| §10 merge/undo 顺序 | 与人 id 顺序无关；影响集合严格来自快照；每脸任一语句边界至多一条有效行；`max(before,live)+1`；语义 no-op 仍零改动 | `_faces_needing_link_restore()` + `_clear_effective_links()` + `forced_keys`/`revision_hints`；`test_p3_person_undo_ordering.py` 5 例 + 既有 `test_a_semantic_noop_is_undoable_and_moves_nothing` 全过 | 一致（§7.2） |

## 5. 必须修复项：映射面 `VALIDATION_ERROR` 的 `details` 不在冻结闭集内

### 5.1 契约依据与反例

ADR-0010 §8.2 明确："**`details` 是闭集，不是自由对象。** 每个错误码只允许下表列出的键……出现任何其他键
（含 `bbox`、`landmark`、`iou`、`path`、`internal_snapshot`、幂等 payload/hash）即视为契约违规，实现必须
拒绝而不是透传。" 同节的逐码表把 `VALIDATION_ERROR` 的允许键固定为
`reason` / `old_face_id` / `new_face_id` / `asset_id`，**必填 `reason`**；companion 用严格 `oneOf` 实现
（每个分支同时冻结 `code`、允许键与必填键，`additionalProperties: false`）。

### 5.2 可执行反例（真实 HTTP 响应）

探针：`.pytest-basetemp-p308c/probe_api_closure.py`（被 `.gitignore` 忽略的临时脚本，驱动真实 ASGI 应用并
用本卡自带校验器 `test_p3_face_redetection_contracts.contract_errors(body, "MappingApiError")` 校验）：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python .pytest-basetemp-p308c/probe_api_closure.py
```

实测（2026-09-22，本机）：

| 请求 | 实测 HTTP body（截断） | companion 判定 |
|---|---|---|
| `GET /api/v1/face-mappings?limit=999` | `{"code":"VALIDATION_ERROR","details":{"field":"limit","maximum":200}}` | `['$: oneOf matched 0']` |
| `GET /api/v1/face-mappings?limit=many` | `{"code":"VALIDATION_ERROR","details":{"errors":[{"loc":["query","limit"],...}]}}` | `['$: oneOf matched 0']` |
| `GET /api/v1/face-mappings?state=decided` | `{"code":"VALIDATION_ERROR","details":{"errors":[{"loc":["query","state"],...}]}}` | `['$: oneOf matched 0']` |
| `GET /api/v1/face-mappings/not-a-face-id` | `{"code":"VALIDATION_ERROR","message":"old_face_id must be a 64-character lowercase hex id"}`（**无 `details`**） | `['$: oneOf matched 0']` |

四个响应分别代表三类违约：

1. **新造的非冻结键**（`field`、`maximum`）：`persons/read.py:157-167` `check_mapping_limit()`；
2. **缺必填 `reason`、甚至连 `details` 都没有**：`persons/read.py:429-439` `mapping_group()` 把
   `CatalogError(VALIDATION_ERROR, reason="old_face_id")` 转成 `ApiError(VALIDATION_ERROR, message, status=422)`
   时丢掉了 `reason`；
3. **通用传输层形状透传**（`{"errors":[...]}`）：`api/routes.py:955-962` 的 `state: Literal["open","all"]`
   与 `limit: int | None` 让 FastAPI 的 `RequestValidationError` 在冻结面上先答（全局 handler
   `api/handlers.py:76-97` 固定返回 `details={"errors":[...]}`）。

P3-08B 在写接口上已经识别过这一类问题（`POST /face-mappings/decisions` 故意用 `Body(default=None)` 让
mapping 模块校验，注释写明"every refusal answers in the frozen per-code details closure"），但读接口没有
沿用同一纪律，因此这不是"契约本身不完整"，而是"实现没做到逐码旁闭"。

### 5.3 为什么现有测试没拦住

- `tests/unit/test_p3_face_redetection_api_privacy.py:373-390`：只断言 `code == VALIDATION_ERROR`；
  真正读取 `details` 的 `test_error_details_are_a_closed_set`（同文件 230-370 行）只驱动**决策路径**，
  从未把 `limit`/`state`/坏 `old_face_id` 的响应喂给它自己的 `allowed`/`required` 映射。
- `tests/unit/test_p3_face_redetection_api_privacy.py:520-593`：`bad_limit` 只断言 status 与 code。
- `tests/unit/test_p3_face_redetection_contracts.py` 的
  `test_error_details_are_closed_per_code[VALIDATION_ERROR]` 只校验**手写样本**，不校验真实响应。

### 5.4 最小修复范围（固定边界，不得扩大）

改 `src/photo_search/persons/read.py`：

- `check_mapping_limit()`（157-168 行）：改答 `details={"reason": "limit"}`（`reason` 为必填，且是唯一允许
  表示调用方形状错误的键）；不要在 `details` 里保留 `field`/`maximum`。
- `mapping_page()` 的 state 校验（389-397 行）：改答 `details={"reason": "state"}`。
- `mapping_group()`（429-439 行）：把 `CatalogError` 的 `reason` 透出为
  `ApiError(VALIDATION_ERROR, message, status=422, details={"reason": <reason>})`。

改 `src/photo_search/api/routes.py`：

- `list_face_mappings()`（951-972 行）：查询参数改为在 mapping/read 层校验而不让传输层先答
  （例如 `state: str = "open"`、`limit: str | None = None`，由 `check_mapping_limit()` 解析并统一以
  `reason` 回答），使该路径的 422 始终是 §8.2 的冻结形状；不得改动其它既有端点的通用约定。

补 `tests/unit/test_p3_face_redetection_api_privacy.py`：

- 在 `test_pagination_bounds_are_enforced` 与 `test_mapping_endpoints_are_closed_over_http` 中读取这些
  真实 422 的 `details`，断言键集 ⊆ `{reason, old_face_id, new_face_id, asset_id}` 且 `reason` 存在
  （直接复用本卡 companion 校验器即可，不必重写规则）。

不得改 ADR-0010、两份 companion、migration/schema、依赖、`web/**`、其它 mapping 语义或既有例外清单；
不得通过放宽 companion 来"消除"反例。修复后只需对上述边界做一次短审（不需要重开全量主审）。

## 6. 需裁定项：`STALE` 终态导致"存活且合格的候选"被判为 `NO_CANDIDATE`

### 6.1 现象与反例

探针：`.pytest-basetemp-p308c/probe_min.py`：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python .pytest-basetemp-p308c/probe_min.py
```

实测（合成库，无真实照片）：

```text
1) after upgrade: UNIQUE_HIGH SUGGESTED
2) after candidate removed: NO_CANDIDATE STALE
3) same pair written again: rebuilt == new_face: True | face row alive: True
     | mapping state: STALE | group candidate_state: NO_CANDIDATE
   live_candidate_rows: []
   detail candidates: []
   confirm refused: FACE_MAPPING_STALE {...expected_review_revision: 2, current: 2}
```

即：同一对脸（同一 `old_face_id`/`new_face_id`、同一几何）在 `STALE` 之后**被再次写入**（回退到旧世代或
同世代文档重现）时，映射行永久停留在 `STALE`（`live_candidate_rows()`：330-341 行排除 `STALE`；
`_upsert_mapping_row()`：496-515 行只刷新证据/`updated_at`，不再进入候选集），于是 group 报
`candidate_state = NO_CANDIDATE`、候选数组为空，且该对脸**无法确认**（目标不在实时候选集内）。

### 6.2 争议点

- ADR-0010 §3.3 把 `NO_CANDIDATE` 定义为"旧人工锚点仍存活但**本次检测没有匹配脸**"，而 §3.2 用几何定义
  "合格候选"；§3.1/§7 也没有把 `STALE` 写成不可逆终态（§7 回退条写的是"方向反转并为'当时的存活行 ×
  本次写入行'**重新建候选**"）。按字面，此时的 `candidate_state` 应为 `UNIQUE_HIGH`，候选数组应含该脸。
- 但 ADR 也确实**没有**规定"失效后重现"的复活路径；P3-08B 把它实现为终态并写进注释
  （`mapping.py:496`："never silently re-opens a `STALE` pair"），且用
  `test_removed_candidate_becomes_stale` 的末段断言固定（"Re-writing the same geometry later does not
  silently revive the superseded pair"）。

### 6.3 为何不作为本轮 FAIL 的主因，但必须裁定

该行为是**保守的**：不转移、不创建、不删除任何人工 authority，不写 filter revision，不写图片向量
outbox，不泄露任何私有数据；影响面是"这条窄路径上人只能 reject、不能 confirm"，以及"UI 文案与 §3.3 的
定义不完全一致"。它不破坏 §4/§5/§10 的任何不变量，因此不应单凭它判定实现不合格；但也**不能在没有记录
的情况下保留现状**。

建议在修复轮内二选一并落盘证据：

1. **在 ADR-0010 注明** `STALE` 为对该对脸的终态语义（需要契约轮，不能由本卡改 ADR）；或
2. **实现复活**：`_upsert_mapping_row()` 在"对脸存活 + 本次合格 + 当前 `STALE`"时把该行移回 `SUGGESTED`
   并 `revision + 1`（保留 `CONFIRMED`/`REJECTED` 的状态与审计链，不倒退 revision），同时补一条
   "失效→重现→可确认"的真实用例。

两者都不能借机改变 §5.3 的"释放集合只含 confirm/reject"和"undo 非释放"语义。

## 7. 已核验为非问题的项

### 7.1 `media/face_crop.py` 的读取容忍不掩盖坏数据

`parse_bbox()` 新增 `Mapping` 分支后，两种形状都接受，坏行仍按原码拒绝。实测（同一探针脚本）：

| 输入 | 结果 |
|---|---|
| `{"bbox":[1,2,11,12]}` | 接受（生产写入形状） |
| `[1,2,11,12]` | 接受（P3-06 既有夹具/缓存键形状） |
| `{"bbox":[1,2,3]}` / `[1,2,3]` | `FACE_STATE_CONFLICT/bbox_arity` |
| `{"bbox":"nope"}` / `{}` | `FACE_STATE_CONFLICT/bbox_not_a_list` |
| `{"bbox":[0,0,0,0]}` | `FACE_STATE_CONFLICT/bbox_degenerate` |

结论：该改动修复了真实的接缝缺陷（生产写入 `{"bbox":[...]}` 与 P3-06 读取约定不一致），没有引入静默
掩盖。生产写入形状与既有截面语义未改。

### 7.2 §10 顺序修复与"语义 no-op 仍零改动"共存

`_faces_needing_link_restore()` 只用 before/after 快照求差集（影响集合严格来自快照，未波及其它脸）；
`_clear_effective_links()` 在任何写回前一次性删除这些脸的 `CONFIRMED` 行并留下 per-face revision 下限；
`forced_keys` 只覆盖"该脸已在本轮被清除且 wanted 为 `CONFIRMED`"的键，因此不会因为"live 快照看起来
相同"而漏写。既有 `test_a_semantic_noop_is_undoable_and_moves_nothing` 未改且通过，`DEF-001`～`DEF-005`
与反向 id 顺序、多源用例全部通过。

### 7.3 地名包示例上界、能力门控与前端

- `mini-cities.{spec,manifest}.json` 的 `compatible_catalog_schema.maximum 13 → 14` 恰当：0014 不触碰地点/
  tokenizer 数据面，示例包在 schema 14 上仍兼容；`content_sha256` 是文件清单哈希（P2-03B 口径），与兼容
  区间无关，保持不变正确。
- `face_summary()` 的四计数是加性的、仅在两张 0014 表存在时出现；旧库缺键而非报 0，前端只在
  `open_face_mappings` 存在时显示面板。记录式浏览器闭环观察到 `/capabilities` 报 `schema v14（ok）`、
  `重检测待确认 2`，与本轮静态审查一致（本轮**未**重跑浏览器闭环，见 §8）。

## 8. 未验证项裁定（P3-08B 报告 §7 五条）

| # | 未验证项 | 裁定 | 理由 |
|---|---|---|---|
| 1 | `/search` 页面**没有人物条件输入**，矩阵 `BROW-002` 的"人物+地点 AND 浏览器重搜"未执行（API 级等价证据为既有搜索套件 + 本卡 `interleave.py`） | **本卡范围内可接受；补 UI 需新任务** | P3-08B 允许改动面明确限定 `web/persons.*`，`/search` 人物输入是 P3-07B 未交付的**新界面范围**，不属于本卡。API/计数级等价证据已存在（`P3-08B` 报告 §5 已诚实标注"部分"），不得写成浏览器 PASS。若要补，应作为独立小任务（建议 P3-08D 候选），不能在本卡夹带 |
| 2 | `/places` 的资产归属页面入口只服务"待确认建议"队列，合成夹具无 GPS，浏览器未执行 assign | 可接受 | 该路径属地点功能面，与映射 authority 无交集；API 级证据 `interleave.py` + 既有地点套件已覆盖"互不覆盖/共同 revision" |
| 3 | 30 万容量候选成本、`0014` 实测迁移耗时、多进程、真实权重升级/回退、**C 级打包**未执行 | 可接受 | 本卡验收等级是 **B + D + 记录式浏览器闭环**，C 级打包与容量属 C1/P4 范围；报告已把它们列为未验证并**未**标 PASS |
| 4 | 矩阵建议的 `tests/integration/test_p3_person_search_sessions.py` 在本仓库不存在，X-002/X-005 指向既有 `test_p3_person_search_filter.py` | 可接受 | §5 中已注明替换关系且证据真实通过；矩阵文件本身未改，属文档指向与仓库现实的差异 |
| 5 | 窄宽检查用内置浏览器 viewport 覆盖（非 OS 级设备模拟） | 可接受 | 属记录式 smoke 的固有边界，报告已明确不称 Playwright/Selenium PASS |

结论：五条都不构成"把未执行步骤写成 PASS"，因此不影响本轮判定；但第 1 条需要一个独立任务的落点，建议在
P3-08D 规划时显式登记。

## 9. 压缩交接包（下一轮 DS-H 修复 + 随后的 Sol 短审）

**基线**：`c7fa68b` + P3-08B 全部未提交改动；`git status --short` = 25 M + 18 ??（另加本报告、本卡结论段
与状态更新）。ADR-0010 保持 Accepted，`schema_version = 14`，migration `0001`～`0014` 连续，依赖 95 包。

**结论**：**FAIL**。生产面/迁移/§2.1～§5.4、§6、§7、§10 的不变量基本达标；公开错误面
`VALIDATION_ERROR` 的 `details` 未做到逐码旁闭（§5），另有一处 `STALE` 终态语义空白待裁定（§6）。

**本轮证据**：专项 `test-results/p3-08c-dsh-focused.xml` 108/0/0/0；完整
`test-results/p3-08c-full.xml` 1450/0/0/48；Ruff / `git diff --check` / `uv lock --check`（95 packages）
clean；反例探针 `.pytest-basetemp-p308c/probe_api_closure.py`、`.pytest-basetemp-p308c/probe_min.py`
（均在 gitignored 目录内）。

**必须修的边界（§5）**：`persons/read.py:157-168`（`limit` → `details={"reason":"limit"}`）、
`persons/read.py:389-397`（`state` → `details={"reason":"state"}`）、`persons/read.py:429-439`
（坏 `old_face_id` 必须带 `reason`）、`api/routes.py:951-972`（查询参数改由 mapping/read 层校验，避免
通用 `{"errors":[...]}` 形状进入冻结面）；同步补上述两处测试的真实响应 `details` 断言。

**必须裁定的边界（§6）**：`_upsert_mapping_row()` 对"存活且合格的对再现"保持 `STALE` 终态；选择
"写进 ADR 注明终态"或"实现复活 + 新用例"，二者都要落盘。

**未决高风险项（给 Sol high 的短审输入）**：① 修复后映射面 4 个真实 422 响应是否全部满足 §8.2 的逐码
闭集（含 `details.reason` 必填、无 `errors`/`field`/`maximum` 等键）；② `STALE` 终态裁定是否被记录且与
§3.3/§7 自洽；③ 保持/释放/队列与 §10 顺序修复未被本轮改动波及（短审只需复跑 hold/undo-ordering/
api-privacy 的具名用例）。

**未验证项**：见 §8（含 `BROW-002` 的 `/search` 人物输入属新范围）。

**禁止扩大的范围**：不改 ADR-0010 与两份 companion（若认为契约本身需改，必须另开契约轮）、不改
`0001`～`0013`、不加依赖、不改前端与其它端点约定、不把未执行的浏览器/容量/打包步骤写成 PASS、不因
"测试绿"而跳过上述两条边界的补证。

## 10. 本轮不做的判定

- **不标记 P3-08C PASS**，不生成 `docs/tasks/P3-08D.md`；
- 不升级 DeepSeek max：两条问题都有最小反例与明确修复面，未命中 V3.1 §2.2 任何具名条件；
- 不代表 Sol 阶段：Sol 只在高风险边界修复后做一次短审（§9 的未决高风险项）。
