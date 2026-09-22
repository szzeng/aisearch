# P3-08B 报告：新旧人脸映射实现、merge/undo 顺序修复与 P3 故障/浏览器闭环

- 执行模型：DS-H / DeepSeek V4.1 Flash / 高推理（模型切换由用户在 Codex 中手动完成）
- 起始基线：`c7fa68b`（P3-08A 收尾提交，工作树干净）
- 数据库：`schema_version = 14`，migration 连续 `0001`～`0014`（`0014` 为本卡新增），依赖 95 包未变
- 结论：**PASS（B + D 级 + 一次记录的真实浏览器闭环；浏览器闭环中 `/search` 的人物条件一处未执行，
  见 §6 与 §7）**

## 1. 范围与总评

本卡按已冻结的 ADR-0010 与两份 companion 实现“模型重检测后新 `face_id` 的可审查映射”，并修复
P3-07B §6 的 merge/undo 顺序缺陷、补齐 P3 故障矩阵的 D 级故障与真实浏览器闭环。交付面：

1. `0014_face_redetection_mapping.sql`：`face_redetection_groups` / `face_redetection_mappings`
   两表，第三次重建共享 `person_operations` / `person_operation_items` 的闭合 `CHECK`
   （新增 `FACE_MAPPING_CONFIRM` / `FACE_MAPPING_REJECT` 与 `FACE_MAPPING_GROUP`）；
2. `face/processor.py`：候选生成/失效在**写人脸文档的同一事务内**执行，退休先于失效；
3. `face/index.py`：`_reconcile_asset()` 写门新增“OPEN 组候选”这一**临时保持**来源（与
   `_manually_locked_face_ids()` 同一事务同一时点读取），队列行改为保持期保留/释放后重排队；
4. `persons/mapping.py`（新）：`mapping_id` 稳定键、几何证据、候选状态、决策矩阵、幂等门、补偿撤销；
5. `persons/read.py` / `api/*`：`GET /face-mappings`、`GET /face-mappings/{old_face_id}`、
   `POST /face-mappings/decisions`，独立 `fm1` 游标空间、严格 `details` 闭集、四个能力计数；
6. `web/persons.{html,css,js}`：`/persons` 新增“重检测待确认”视图（能力门控、旧脸/新脸并排裁剪、
   逐候选确认、整组拒绝、决策后撤销入口）；
7. `tools/diagnostics/face_mapping_smoke_fixture.py`：合成库 + 可搜索数据根的浏览器 smoke 夹具。

设计上刻意收窄：映射行/组行**不是人物 authority**（人物事实仍只写在
`face_assignment_controls`/`face_person_links`/`face_exclusions`），服务端**永不自动确认**，
只有人工确认才按 §4 矩阵转移人工知识；一次确认只推进一次 `catalog_filter_revision`（cause 仍为
`PERSON_CHANGED`），**不写图片向量 `index_outbox`**。

## 2. 实际基线与逐文件改动

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/photo_search/catalog/migrations/0014_face_redetection_mapping.sql` | 新增 | 两表 + 第三次日志重建；`old_face_id` 有真外键（退休路径先删依赖行），`new_face_id` **故意无外键**（ADR §7 要求候选被删除后映射行仍以 `STALE` 存活）；`UNIQUE(old_face_id,new_face_id)`、自映射 `CHECK` |
| `src/photo_search/catalog/schema.py` | 改 | `SUPPORTED_SCHEMA_VERSION 13 → 14` + 文档 |
| `src/photo_search/catalog/errors.py` | 改 | 8 个新错误码 + 两个上界常量（200/16） |
| `src/photo_search/api/errors.py` | 改 | 新码导入与 HTTP 映射（404/409/422） |
| `src/photo_search/face/processor.py` | 改 | `persist_face_document()` 同事务调用 `generate_candidates_for_asset()`；`_retire_stale_faces()` 先调用 `retire_mapping_faces()`；**`human_controlled_face_ids()` 语义不变**（仍是检测行保留） |
| `src/photo_search/face/index.py` | 改 | 新增 `_mapping_hold_face_ids()`；`_reconcile_asset()` 写门读保持集、动作区分 `PROVISIONAL_HOLD`；pass 末尾队列行从“无条件删除”改为“保持/晚到冲突则保留重排队” |
| `src/photo_search/media/face_crop.py` | 改（最小邻接） | `parse_bbox()` 同时接受 `[l,t,r,b]` 与 `{"bbox":[...]}`（见 §5 接缝缺陷） |
| `src/photo_search/persons/mapping.py` | 新增 | 稳定键、`match_state_for`、`candidate_state_for`、候选生成/失效、保持谓词、决策与补偿撤销、能力计数 |
| `src/photo_search/persons/read.py` | 改 | `fm1` 编解码、`check_mapping_limit()`（1..200 **拒绝**而非夹紧）、`mapping_page()`/`mapping_group()` |
| `src/photo_search/persons/operations.py` | 改 | `operation_family()` 增加 `mapping` 分支；`_item_projection`/`operation_result` 路由到 mapping 信封；`_replay()` 支持 mapping 幂等冲突闭集；**ADR-0010 §10 链接恢复顺序修复** |
| `src/photo_search/persons/contracts.py` | 改 | `MAPPING_OPERATION_TYPES`、`ENTITY_TYPE_MAPPING_GROUP` |
| `src/photo_search/persons/service.py` | 改 | 三个映射方法的薄门面 |
| `src/photo_search/api/models.py` | 改 | 6 个映射 DTO（全部 `extra="forbid"`） |
| `src/photo_search/api/routes.py` | 改 | 三条路径 + 撤销 `response_model` 并入 `MappingOperationResponse` |
| `src/photo_search/scheduler/projection.py` | 改 | `face_summary()` 仅在 0014 两表存在时加性加入四个计数 |
| `src/photo_search/web/persons.{html,css,js}` | 改 | 待确认视图 + 能力门控 + 决策/撤销交互 |
| `tests/**`、`tests/p3_redetection_fixtures.py`、`tools/diagnostics/face_mapping_smoke_fixture.py` | 新增/改 | 11 个本卡文件 + 夹具 + 诊断夹具 |
| `tests/unit/test_catalog_migrations.py`、`tests/integration/test_p2_scheduler_recovery.py` | 改 | 表集合常量加入两新表 |
| `tests/integration/test_p3_person_split_migration.py` | 改 | 13→14 连续升级与幂等断言改为版本驱动 |
| `tests/integration/test_p3_person_search_filter.py` | 改 | 迁移链断言由“固定 13”改为 `SUPPORTED_SCHEMA_VERSION` |
| `tests/unit/test_api_frontend.py` | 改 | 新增待确认视图的前端契约断言 |
| `tools/places/examples/mini-cities.{spec,manifest}.json` | 改 | `compatible_catalog_schema.maximum 13 → 14`（地名包与 0001~0013 表无关，仍兼容；`content_sha256` 未变） |

未改：ADR-0010、两份 P3-08A companion、ADR-0002～0009 及其 companion、`uv.lock`、
`PhotoSearch.spec`、`0001`～`0013`（逐字节未动）、搜索排序/反馈协议、检测模型与阈值、
`space_id`、OCR/地点语义。

## 3. 实现对照

### 3.1 migration / schema / 持久权威

- 两表字段、状态闭集、`revision >= 1`、`UNIQUE(old_face_id,new_face_id)`、自映射 `CHECK` 逐项对应
  ADR §2～§3；索引覆盖 `(state, old_face_id)`、`(old_face_id, state)`、`(new_face_id)`。
- `person_operations.type` 新增两型；`person_operation_items.entity_type` 新增 `FACE_MAPPING_GROUP`
  （`entity_key = old_face_id`、`entity_id IS NULL`），并把“只有 `FACE_CONTROL` 带 entity_key”的
  旧闭集推广为“`FACE_CONTROL`/`FACE_MAPPING_GROUP` 带 entity_key、`PERSON_BUNDLE`/`SPLIT_GROUP`
  带 entity_id”。
- 迁移沿用 0012/0013 的备份、单事务、别名引用暂存、`PRAGMA defer_foreign_keys` 与高版本拒绝；
  `13→14`、空库连续升级、已有数据保留、失败回滚、高版本拒绝均由既有迁移套件覆盖（§4）。

### 3.2 候选生成、稳定身份与失效

- 只对**跨世代**（`old_spec_revision != new_spec_revision`）且旧侧带人工知识的行生成候选；
  同内容同 `spec_revision` 重跑零新脸/零映射行/零新 group；自映射与跨资产永不生成/不接受。
- `mapping_id = sha256(canonical JSON{asset_id,old_face_id,new_face_id,old_spec_revision,new_spec_revision})`；
  同对脸只有一行（`UNIQUE`），重复检测刷新证据（`match_state` 变化时 `revision+1`）。
- 判定顺序：**写/退休检测行 → 计算候选 → 失效置 `STALE`/重算 group → 关联遍计算保持**；
  零人脸文档也会为人工锚点保留 `NO_CANDIDATE` 组（“脸消失也要人工确认”）。
- 决策后再次重扫：`CONFIRMED` 映射与人工事实不回滚、`revision` 不回退、旧审计链保留。

### 3.3 临时保持与 `face_association_queue`

- 两个谓词严格分离：`human_controlled_face_ids()`（检测行保留）未改；自动关联写门读
  `_manually_locked_face_ids()` + 活动排除 + **OPEN 组候选**（`hold_face_ids()`）。
- 保持期内关联遍跳过该脸（动作 `PROVISIONAL_HOLD`）且**保留/回写队列行**；晚到控制冲突同样保留。
- 释放集合只有 `confirm`/`reject`；`undo → OPEN` **不是释放**：恢复保持 + 重排队。
- 失效先于保持：被删除的候选立即 `STALE`，不进入保持集合，也不被当成不可删除的人工锚点。

### 3.4 决策、幂等、revision 与补偿撤销

- `POST /api/v1/face-mappings/decisions`：1..200 组 × 每组 1..16 targets；整批先完成
  shape/去重/存在性/全部 `expected_review_revision`/目标合法性校验再写入；任一失败零写入。
- 幂等：`request_id` 门先于任何 revision 检查；同 key 同 hash replay 原 operation（`replayed=true`），
  同 key 不同 hash 答 `FACE_MAPPING_IDEMPOTENCY_CONFLICT`（details 仅 `asset_id`）。
- 矩阵：每旧脸至多一个 `inherits_person=true`（`multiple_targets_without_single_inheritor`）、
  每新脸至多一个继承者（`multiple_inheritors_for_new_face`）、无继承者的保守 `MANUAL_UNASSIGNED`、
  exclusion 并集、肯定/否定冲突整批拒绝（`human_affirm_deny_conflict`）、已被人工编辑的目标拒绝
  （`FACE_MAPPING_TARGET_CONTROLLED`）、`NO_CANDIDATE` 只允许 `reject`。
- 只有人工事实实际变化才推进一次 `catalog_filter_revision`（`PERSON_CHANGED`）+ 投影队列 +
  `PENDING_REPAIR`，不写图片向量 outbox；纯 reject/纯审核只改审核状态。
  **注意**：信封 `changed` 是“审核状态变化”，与“人工事实变化”不同（纯 reject 时 `changed=true`
  而 filter revision 不变），这是 ADR §6 与 companion 的两种事实，已在测试中显式固定。
- 撤销：既有 `POST /person-operations/{operation_id}/undo`；`operation_family() == "mapping"`；
  快照冲突整批 `FACE_MAPPING_OPERATION_NOT_UNDOABLE`（`conflicts` 仅
  `entity_type`/`entity_key`/`reason`），恢复 revision `max(before,live)+1`，不允许 undo an undo。

### 3.5 P3-07B §6 merge/undo 顺序缺陷

- 修复：撤销时先按**操作快照影响集合**对该集合内全部 `CONFIRMED` 行做一次删除，再按 before
  快照写回（`_faces_needing_link_restore()` + `_clear_effective_links()` + `forced_keys`）；
  任何语句边界每张脸至多一条有效行。
- 收窄保证向后兼容：只有“live 与 before 真的不同”的脸才进入该集合，因此**语义 no-op 的撤销
  仍然零改动**（`tests/integration/test_p3_person_undo.py::test_a_semantic_noop_is_undoable_and_moves_nothing`
  未改且通过）。未触及行逐字节不变，`MANUAL_UNASSIGNED` 恢复后仍无 confirmed link。

### 3.6 API、错误、能力与隐私闭集

- 三条路径 + 复用撤销路径；DTO 全部 `extra="forbid"`；`limit` 严格 1..200（缺省 50，越界 422），
  `state` 仅 `open|all`，`fm1` 游标为独立空间（外来/越界/前缀错一律既有 `INVALID_CURSOR`，details 空对象）。
- `details` 逐码闭集：11 个已实现码的全部实测 details 均落在所属闭集内；
  `FACE_NOT_FOUND` 至少一个 old/new ID；`FACE_MAPPING_OPERATION_NOT_UNDOABLE` 必带 `conflicts`；
  `PERSON_NOT_FOUND`/`PERSON_REDIRECT_CYCLE` 只由既有 canonical 解析器产生（映射模块源码中不出现
  这两个码，静态断言固定），对外仍是同一码与同一 details 形状。
- 响应/错误/访问日志不含 bbox、landmark、IoU、embedding、confidence、路径、EXIF/GPS、
  幂等 payload/hash 或 before/after 快照（§5、§6 的真实浏览器与日志扫描）。
- `/capabilities.face_summary` 加性四项计数，仅在 0014 两表存在时出现；旧 schema 直接缺键而非报 0。

### 3.7 `/persons` 审核 UI

- 新面板默认 `hidden`，只有 capabilities 明确给出四个映射计数（= schema ≥ 14 且 schema ok）时才显示，
  并在 `app-meta` 报“重检测待确认 N”。
- 行内：旧脸裁剪 + 状态/候选态/revision/控制投影/有效归属/候选数；展开后逐候选显示新脸裁剪、
  强弱匹配、质量、状态、revision，勾选“命中/继承人工人物”，可“确认所选新脸”“拒绝整组”“拒绝确认
  （人脸消失）”；决策后展示操作 id 与“撤销这次决策”，冲突时显示服务器 `code`+原因并刷新列表。
- 既有同名、待手动归类、单脸/批量纠错、拆分、操作日志/撤销功能未改（浏览器中同页交错使用正常）。

## 4. 验收等级与 JUnit 证据

等级：**B + D + 记录的真实浏览器闭环**。

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path

# 专项批（任务卡点名 11 个文件；basetemp 因本机 ACL 事实改用项目内新目录，见 §7）
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p3_face_redetection_contracts.py tests/unit/test_p3_face_redetection_matching.py `
  tests/unit/test_p3_face_redetection_decisions.py tests/unit/test_p3_face_redetection_api_privacy.py `
  tests/integration/test_p3_face_redetection_mapping.py tests/integration/test_p3_face_redetection_decisions.py `
  tests/integration/test_p3_face_redetection_hold.py tests/integration/test_p3_face_redetection_restart.py `
  tests/integration/test_p3_face_redetection_upgrade.py tests/integration/test_p3_person_undo_ordering.py `
  tests/integration/test_p3_person_place_interleave.py `
  --basetemp=.pytest-basetemp-p308b/focused4 --junitxml=test-results/p3-08b-focused.xml

# 完整套件
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p308b/full3 --junitxml=test-results/p3-08b-full.xml

uv run --no-sync ruff check .
git diff --check
uv lock --check
```

| 证据 | tests | failures | errors | skipped | 备注 |
|---|---:|---:|---:|---:|---|
| `test-results/p3-08b-focused.xml` | 108 | 0 | 0 | 0 | 本卡 11 个文件；`contracts 58 / matching 4 / unit-decisions 4 / api-privacy 6 / mapping 6 / decisions 9 / hold 5 / restart 4 / upgrade 4 / undo-ordering 5 / interleave 3` |
| `test-results/p3-08b-full.xml` | 1450 | 0 | 0 | 48 | 全套 199.1 s；48 skip 全部为“未安装 pinned 权重”的环境跳过（P0-04 事实） |
| Ruff / `git diff --check` / `uv lock --check` | — | — | — | — | 全 clean；`uv lock --check` 95 packages |

完整套件对比：P3-07C 基线 1341 → 1450（+108 本卡用例 +1 前端契约用例）。

## 5. 65 条矩阵的稳定 ID → 证据映射

> 状态列为本卡结束时的**真实**结论：`existing` 重核适用性并随全套重跑；`must_rerun` 实际重跑；
> `not_implemented` 由本卡新增真实证据。矩阵文件本身未修改。

| ID | 证据 | 结论 |
|---|---|---|
| MAP-001 | `integration/test_p3_face_redetection_mapping.py::test_cross_generation_redetection_creates_open_group` | 新增证据，通过 |
| MAP-002 | 同文件 `::test_high_confidence_candidate_is_never_auto_confirmed` | 新增证据，通过 |
| MAP-003 | `unit/test_p3_face_redetection_matching.py::test_ambiguous_group_has_no_default_target` | 新增证据，通过 |
| MAP-004 | `unit/test_p3_face_redetection_decisions.py::test_no_candidate_group_accepts_only_reject`、`integration/test_p3_face_redetection_decisions.py::test_reject_releases_the_hold_and_keeps_the_anchor` | 新增证据，通过 |
| MAP-005 | `integration/...mapping.py::test_cross_asset_pairs_are_never_generated` | 新增证据，通过 |
| MAP-006 | `unit/...matching.py::test_low_quality_never_reaches_high_match_state` | 新增证据，通过 |
| MAP-007 | `unit/...decisions.py::test_two_inheritors_for_one_target_is_refused` | 新增证据，通过 |
| MAP-008 | `integration/...decisions.py::test_split_mapping_keeps_one_inheritor_and_copies_exclusions` | 新增证据，通过 |
| MAP-009 | 同文件 `::test_many_to_one_mapping_merges_exclusions_once` | 新增证据，通过 |
| MAP-010 | 同文件 `::test_human_edited_target_is_never_overwritten` | 新增证据，通过 |
| MAP-011 | 同文件 `::test_affirm_deny_conflict_refuses_the_whole_request` | 新增证据，通过 |
| MAP-012 | `unit/...decisions.py::test_stale_review_revision_is_refused` | 新增证据，通过 |
| MAP-013 | `integration/...mapping.py::test_removed_candidate_becomes_stale` | 新增证据，通过 |
| MAP-014 | `unit/...decisions.py::test_decision_idempotency_gate_precedes_revision_checks` | 新增证据，通过 |
| MAP-015 | `integration/...decisions.py::test_later_human_edit_blocks_mapping_undo` | 新增证据，通过 |
| MAP-016 | 同文件 `::test_mapping_undo_restores_state_forward_only` | 新增证据，通过 |
| MAP-017 | `integration/test_p3_face_redetection_restart.py::test_open_review_survives_restart` | 新增证据，通过 |
| MAP-018 | 同文件 `::test_repeated_detection_is_idempotent_for_mappings` | 新增证据，通过 |
| MAP-019 | `integration/test_p3_face_redetection_upgrade.py::test_model_upgrade_creates_reviewable_groups` | 新增证据，通过 |
| MAP-020 | 同文件 `::test_failed_upgrade_leaves_mappings_untouched` | must_rerun → 已重跑，通过 |
| MAP-021 | 同文件 `::test_rollback_reverses_direction_without_rewinding_revisions`（方向反转在 mapping 层驱动，理由见 §7） | 新增证据，通过（范围受限） |
| MAP-022 | 同文件 `::test_late_worker_document_is_refused` | must_rerun → 已重跑，通过 |
| MAP-023 | `integration/...restart.py::test_backup_restore_keeps_mapping_states` | must_rerun → 已重跑，通过 |
| MAP-024 | `unit/test_p3_face_redetection_api_privacy.py::test_mapping_surface_has_no_private_fields` | 新增证据，通过 |
| MAP-025 | 同文件 `::test_capabilities_gate_is_honest` | 新增证据，通过 |
| MAP-026 | `unit/...matching.py::test_faces_without_human_knowledge_are_not_queued` | 新增证据，通过 |
| MAP-027 | `integration/...restart.py::test_unchanged_rerun_creates_no_mapping_rows` | must_rerun → 已重跑，通过 |
| MAP-028 | `unit/...matching.py::test_self_mapping_is_never_generated` | 新增证据，通过 |
| MAP-029 | `integration/test_p3_face_redetection_hold.py::test_hold_blocks_the_association_write_gate` | 新增证据，通过 |
| MAP-030 | 同文件 `::test_release_requeues_the_released_faces` | 新增证据，通过 |
| MAP-031 | 同文件 `::test_stale_is_decided_before_the_hold_set` | 新增证据，通过 |
| MAP-032 | 同文件 `::test_undo_reapplies_the_hold_and_requeues` | 新增证据，通过 |
| MAP-033 | `unit/...api_privacy.py::test_error_details_are_a_closed_set` | 新增证据，通过 |
| MAP-034 | 同文件 `::test_pagination_bounds_are_enforced` | 新增证据，通过 |
| MAP-035 | 同文件 `::test_candidate_state_matches_the_candidate_array` | 新增证据，通过 |
| PERS-008 | `tests/integration/test_p3_person_search_filter.py`（既有）+ 本卡映射后全套重跑 | existing → 通过 |
| PERS-014 | `tests/integration/test_p3_person_operations.py`（既有） | existing → 通过 |
| PERS-015 | `tests/integration/test_p3_face_correction_operations.py`（既有） | existing → 通过 |
| PERS-016 | 同（既有）+ `integration/test_p3_face_redetection_decisions.py`（交错） | existing → 通过 |
| PERS-017 | `test_p3_person_operations.py`、`test_p3_face_correction_operations.py`（既有） | existing → 通过 |
| PERS-018 | `integration/...decisions.py` + 既有 undo 套件 | existing → 通过 |
| PERS-019 | `tests/unit/test_p3_person_contract.py`（既有） | existing → 通过 |
| PERS-020 | `integration/...decisions.py::test_confirmation_transfers_anchor_knowledge` | 新增证据，通过 |
| PERS-021 | 既有 + `test_p3_person_place_interleave.py` | existing → 通过 |
| PERS-022 | 既有 + MAP-008 | existing → 通过 |
| PLACE-029 | `tests/integration/test_p2_place_gps_facts.py`（既有） | existing → 全套通过 |
| PLACE-030 | `test_p2_place_resolution.py`、`test_p3_ocr_documents.py`（既有） | existing → 全套通过 |
| PLACE-031 | `test_p2_place_directory.py`（既有） | existing → 全套通过 |
| PLACE-032 | `test_p3_person_search_filter.py`（既有）+ X-005 | existing → 全套通过 |
| PLACE-033 | `test_p2_place_dataset_lifecycle.py`（既有）+ 隐私扫描（§6） | existing → 全套通过 |
| X-001 | `integration/test_p3_person_place_interleave.py::test_authorities_do_not_overwrite_each_other` | must_rerun → 已重跑，通过 |
| X-002 | `test_p3_person_search_filter.py`（既有；矩阵建议的文件名在本仓库不存在，实际引用该套件） | existing → 通过 |
| X-003 | `interleave.py::test_undo_across_families_is_independent` | must_rerun → 已重跑，通过 |
| X-004 | `integration/...decisions.py::test_mapping_undo_conflicts_with_later_face_edit` | 新增证据，通过 |
| X-005 | `test_p3_person_search_filter.py`（既有会话 stale 机制）+ `interleave.py::test_a_mapping_confirmation_moves_the_shared_filter_revision`；浏览器侧见 §6 | must_rerun → API/计数器面通过；浏览器人物条件未执行（§6/§7） |
| DEF-001 | `integration/test_p3_person_undo_ordering.py::test_merge_undo_is_independent_of_person_id_order` | must_rerun → 修复后重跑，通过 |
| DEF-002 | 同文件 `::test_multi_source_undo_matches_before_snapshot` | 新增证据，通过 |
| DEF-003 | 同文件 `::test_undo_is_scoped_to_the_operation` | 新增证据，通过 |
| DEF-004 | 同文件 `::test_undo_keeps_control_and_exclusions` | must_rerun → 已重跑，通过 |
| DEF-005 | 同文件 `::test_later_edit_blocks_merge_undo` | existing → 通过 |
| BROW-001 | §6 步骤 1～3（确认） | 记录的真实浏览器 smoke，通过 |
| BROW-002 | §6 步骤 2、3、5、6、7、8 | **部分**：`/persons`→`/places`→`/search` 串联通过；`/search` 的**人物条件**在页面上不存在，未执行 |
| BROW-003 | §6 步骤 9 | 记录的真实浏览器 smoke，通过（360×640 覆盖下 `scrollWidth == clientWidth`） |
| BROW-004 | §6 步骤 10、11 | 记录的真实浏览器 smoke + 访问日志扫描，通过 |
| BROW-005 | §6 步骤 8 | 记录的真实浏览器 smoke，通过 |

## 6. 记录的真实浏览器闭环

夹具：`uv run --no-sync python tools/diagnostics/face_mapping_smoke_fixture.py --reset`
（`%TEMP%` 根 + 中文/空格数据根；内部先调用 `search_smoke_fixture` 生成可搜索目录与已激活
`SearchProfile`，再在其上 seed 两个人脸世代、两个审核组、一个人物、一个已人工指定的地点；
不下载模型、不读真实照片库）。
服务：`.venv\Scripts\python.exe -m photo_search --port 8811 --data-root "<数据根>"`。
浏览器：Codex 内置浏览器（`iab`），逐条执行；**这不是 Playwright/Selenium 测试套件的 PASS**，
只是本构建内置浏览器上的记录式 smoke。观测到的版本事实：`/capabilities` 报 `schema v14（ok）`、
`重检测待确认 2`。

| # | 操作 | 实际观察 | 结果 |
|---|---|---|---|
| 1 | 一次性链接 `next=/persons` | 303 落到 `/persons`；导航出现“重检测待确认”；面板列出 2 组（人工取消归类 / 人工指定），旧脸与新脸裁剪均真实渲染 96×72 | 通过 |
| 2 | 载入 MANUAL_ASSIGNED 组的候选 | “强匹配 / 质量 OK / 建议（未决定）/ revision 1”，候选复选框 + “把人工人物继承给这张新脸”复选框各 1 个（MANUAL_UNASSIGNED 组不出现继承项） | 通过 |
| 3 | 勾选命中+继承 → 确认所选新脸 | 日志 `[FACE_MAPPING_CONFIRM] changed=true · catalog_filter_revision=3 · operation_revision=1`；待确认列表 2→1；`/faces?person_id=1` 出现新脸 `人工指定（带锁）· 控制 revision 3 · 有效归属 #1`；操作日志新增“重检测映射确认” | 通过 |
| 4 | 切“全部（含已决定）” | 已确认组显示 `已确认 / revision 2`，未决定组仍 `待确认 / revision 1` | 通过 |
| 5 | 点“撤销这次决策” | “撤销成功：新操作 … · changed=true（再次确认前，该组回到「待确认」并继续保护新脸）”；操作日志出现 UNDO（revision 2）与“已被 … 补偿”的原操作；列表回到 2 组待确认 | 通过 |
| 6 | 再次确认 → 在人脸列表对同一新脸执行“批量取消归类” → 点“撤销这次决策” | 页面显示 `[FACE_MAPPING_OPERATION_NOT_UNDOABLE] the mapping was edited after this decision`；新脸离开爸爸的成员列表，撤销零写入 | 通过（冲突面） |
| 7 | 在待确认组点“整组拒绝”“拒绝确认（人脸消失）” | 日志 `[FACE_MAPPING_REJECT] changed=true · catalog_filter_revision=8 · operation_revision=6`；待确认列表变为“0 组 · 已到末尾”（保持被释放） | 通过 |
| 8 | `/places` 打开 → 目录载入 #1 → 改名“杭州西湖景区” → 提交 | 编辑面 `revision 1 → 2`、无错误；目录 revision 变为 `#7`；随后 `/search` 地点筛选 `place_id=1` 重搜得到 2 行（资产 #1、#6，`hit_reason=FILTER_ONLY`） | 通过（目录纠错）；**资产归属的页面入口只在“待确认建议”队列上，本夹具无 GPS 提示，因此该路径未在浏览器执行** |
| 9 | 每页 1 条建立会话 → 在第二标签页把人物 #2 改名为“叔叔A”（revision 5）→ 回旧页点“加载下一页” | 页面报 `[CATALOG_FILTER_REVISION_STALE] …` 并给出“旧会话不再有效；请重新搜索”提示；不再返回混版结果 | 通过 |
| 10 | 360×640 视口覆盖（`browser.capabilities.viewport`） | 实测 `clientWidth=379`、`scrollWidth=379`、无横向溢出；映射面板 6 个控件均在窄宽下可见 | 通过（覆盖非 OS 级设备模拟） |
| 11 | 键盘 Tab 走查 | 焦点顺序 `mappings-state → mappings-limit → mappings-reload → 载入候选 → 跳过链接/导航`；“加载更多”禁用时被正确跳过 | 通过 |
| 12 | DOM/URL/响应隐私扫描 | 无 `http(s)://` 外链、无 `C:\`/UNC 路径、无精确坐标、无 bbox/landmark/iou/embedding/similarity 字样；`img src` 仅 `/api/v1/faces/<64hex>/thumbnail`；页面 URL 无 token | 通过 |
| 13 | 服务端访问日志 | 全部请求在 `/api/v1/*`、`/persons`、`/places`、`/search`、`/web/*`；会话交换记成 `token=<redacted>`；无坐标、无绝对路径；映射三类路径各出现且返回 200（撤销冲突为 409） | 通过 |

（未保存截图：本次未要求交付截图，观察以 DOM/AX/响应/日志为准。）

## 7. 故障注入、恢复、隐私负向与未验证项

**D 级故障注入/恢复**：迁移 13→14 连续升级/空库/失败回滚/高版本拒绝（既有迁移套件）；
候选被后续检测删除 → `STALE` + group 重算且不被保持“钉住”；零人脸文档保留锚点与 `NO_CANDIDATE` 组；
决策中途失败（shape/冲突/目标非法）零写入（`library_snapshot()` 逐表相等）；晚到文档
`STALE_INPUT_REVISION`、损坏文档 `ARTIFACT_INVALID` 均零写入；重启后审核/保持/队列来自权威表；
文件级备份恢复到另一 data root 后 group/mapping/control 行逐字段相等；升级失败/回退方向反转
而 revision 不回退；撤销冲突整批拒绝且后续人工事实不变。

**发现的真实接缝缺陷（本卡最小邻接修复）**：`faces.bbox_json` 的生产写入形状
（`_upsert_face()` 写 `{"bbox":[...]}`）与 P3-06 裁剪读取（`parse_bbox()` 只接受 `[l,t,r,b]`）
不一致，导致**真实检测管线写出的人脸永远裁剪失败**（`FACE_STATE_CONFLICT/bbox_not_a_list`）。
这是浏览器闭环“旧/新脸并排裁剪”无法成立的直接原因，因此按任务卡允许的“最小邻接修改”在
`media/face_crop.py` 增加读取容忍（两种形状都接受，`[1,2,3]` 等坏行仍按原码拒绝），并在
`persons/mapping.py` 中对称读取两种形状。未改动生产写入形状、未改任何既有截面语义。

**未验证项（不得当作 PASS）**：

1. `/search` 页面的**人物条件**（`person_ids`/`person_match`）在 UI 上不存在——P3-07B 只交付了 API
   与页面级地点筛选；因此矩阵 BROW-002 的“人物+地点 AND 重搜”未在浏览器执行。API 级等价证据：
   `tests/integration/test_p3_person_search_filter.py`（全套重跑）+ 本卡
   `interleave.py::test_a_mapping_confirmation_moves_the_shared_filter_revision`。是否补一个
   `/search` 人物输入属于**新范围**（不在本卡允许修改的 `web/persons.*` 内），留给 P3-08C 裁定。
2. `/places` 的资产归属页面入口只服务“待确认建议”队列；本合成夹具无 GPS 事实，因此浏览器里
   未执行资产↔地点 assign；该事实由 `interleave.py`（API 级）+ 既有地点套件覆盖。
3. 30 万容量下的候选生成成本、审核队列长度、`0014` 迁移耗时、多进程并发、真实权重升级/回退、
   C 级打包 smoke 均未执行（C1/P4 范围）。
4. 矩阵建议的 `tests/integration/test_p3_person_search_sessions.py` 在本仓库不存在；X-002/X-005
   的实际证据指向既有 `test_p3_person_search_filter.py` 会话用例（已在 §5 注明）。
5. 窄宽检查使用内置浏览器 viewport 覆盖（非 OS 级设备模拟、非真实手机浏览器）。

**环境事实**：`.pytest-tmp` 对受限令牌仍是 `PermissionError [WinError 5]`（既有事实，未改 ACL）；
本卡全部测试改用项目内新 basetemp `.pytest-basetemp-p308b/<fresh>`（每次运行使用全新子目录），
并经由正常 Windows 权限路由执行；该目录是临时产物，不在提交范围内。

## 8. 隐私与不变量小结

- 人物 authority 仍只有三张表 + 操作日志；映射行/组行不复制控制 revision，也不写伪人工控制；
- 公开面只有 ID、状态、revision、计数与受控裁剪 URL；`details` 逐码闭集，冲突项只含
  `entity_type`/`entity_key`/`reason`；
- 服务端永不自动确认；未确认不转移任何人工知识；`AMBIGUOUS`/`NO_CANDIDATE` 无默认目标；
- 一次确认只推进一次 `catalog_filter_revision`，不写图片向量 outbox，不新增 queue cause；
- `undo → OPEN` 恢复保持；释放只发生在 confirm/reject；释放后资产已重新排队且决策事务不替用户选人。

## 9. 给 P3-08C DS-H 主审的压缩交接包

**基线**：`c7fa68b`（工作树干净）+ 本卡全部未提交改动（见 §2 表；`git status --short` 可复核）。

**完整改动文件**：见 §2 表格（生产 12 个文件 + 新增 2 个 → `persons/mapping.py`、
`catalog/migrations/0014_*.sql`；测试 11 个新文件/夹具 + 6 个既有测试的最小更新；
2 个诊断夹具/前端契约；1 个地名包示例 spec/manifest 上界）。

**关键不变量**：ADR-0010 §2.1（`mapping_id`/唯一/自映射）、§3.1（跨世代唯一生成面、同 spec 幂等）、
§3.2（五个冻结常量、LOW 永不 HIGH）、§3.3（四态无默认目标）、§3.4（两谓词分离、判定顺序、
队列保留/重排队、undo 非释放）、§4（双向继承基数、保守未归类、并集、肯定/否定冲突、
目标受控拒绝）、§5.1～§5.4（唯一操作日志、1..200×1..16、幂等先于 revision、`max(before,live)+1`、
不允许 undo an undo）、§6（一次 filter bump、不写图片 outbox）、§7（重启/重扫/升级失败/回退/
晚到/失效先于保持/备份恢复）、§8（闭集 API 与隐私）、§10（merge/undo 与人 id 顺序无关）。

**高风险 diff**：`face/index.py` 的写门与队列生命周期；`persons/operations.py` 的
`_faces_needing_link_restore()`/`_clear_effective_links()`/`forced_keys`（顺序修复兼 no-op 兼容）；
`persons/mapping.py` 的事务内校验→写入顺序与 `human_changed` 判定；`media/face_crop.py` 的
读取容忍（新增容忍是否掩盖真实坏数据）；`api/routes.py` 的三条路径与撤销 response_model 联合。

**最小反例（应被拒/应成立）**：一个旧脸两个 `inherits_person=true`；两旧脸对同一新脸都给肯定；
`NO_CANDIDATE` 提交 confirm；候选被删除后仍提交 confirm（须 `FACE_MAPPING_STALE`）；自映射与跨资产对；
已人工编辑的目标；later-edit 后的撤销；`limit=999`；外来 `pf1` 游标；`details` 串键。

**测试证据**：`test-results/p3-08b-focused.xml`（108/0/0/0）、`test-results/p3-08b-full.xml`
（1450/0/0/48）、Ruff/diff-check/lock-check clean；浏览器闭环见 §6。

**未验证项**：§7 五条（其中第 1 条是唯一的“浏览器步骤未执行”，需 P3-08C 裁定归属）。

**禁止扩大范围**：不得改 ADR-0010 与两份 companion、不得改 `0001`～`0013`、不得新增依赖、
不得把 `/search` 人物 UI 或 30 万容量/打包结论写成本卡 PASS。
