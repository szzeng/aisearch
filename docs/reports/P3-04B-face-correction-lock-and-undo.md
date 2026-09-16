# P3-04B — 单脸纠错、合照与人工锁定：DS-H 实现

## 1. 交付物与边界

按 [ADR-0007](../decisions/ADR-0007-face-correction-lock-exclusion-contract.md) 实现以 `face_id` 为最小单位的
取消归类／手动指定／恢复自动、人工排除、幂等、revision、批量、补偿撤销、自动晚到门禁、权威→派生顺序、
四条 `/api/v1` 路径、稳定 `FACE_*` 错误与四个只读计数。**B + D + C** 三级验收。

| 项 | 内容 |
|---|---|
| 开始前 HEAD | `5a17418`（P3-04A 契约冻结），工作树干净 |
| 新 migration | `catalog/migrations/0012_face_correction_operations.sql`，**`schema_version` 11 → 12** |
| 新增源码 | `src/photo_search/persons/face_correction.py` |
| 加性修改 | `persons/{contracts,operations,service}.py`、`face/index.py`、`catalog/{schema,errors}.py`、`scheduler/projection.py`、`api/{models,routes,errors}.py` |
| 未改动 | `0001`～`0011` 逐字节、ADR-0001～0007、冻结 JSON 契约、`pyproject.toml`、`uv.lock`（95 包）、`PhotoSearch.spec`、`places/**`、`geo/**`、`ingestion/**`、`workers/**`、前端资源 |
| 新增用例 | 50 例（lifecycle 21、undo 11、migration 6、api 5、unit contract 7） |

## 2. 迁移 0012：扩展闭合 CHECK，不新增事实表

0011 的 `face_assignment_controls`/`face_exclusions` 形状已经够用，本任务**不加任何表、不加任何无默认值
NOT NULL 列**；必须迁移只是因为 0011 把 `person_operations.type` 与
`person_operation_items.entity_type/entity_id` 写成了闭合 CHECK。0012 只重建这两张表：

- `type` 增加 `FACE_ASSIGN`/`FACE_UNASSIGN`/`FACE_RESTORE_AUTO`/`BATCH_FACE_EDIT`（`UNDO` 不变）；
- `entity_type` 增加 `FACE_CONTROL`，并新增 `entity_key`：`PERSON_BUNDLE` 用正整数 `entity_id`、
  `FACE_CONTROL` 用 `entity_key = face_id` 且 `entity_id IS NULL`，两种形状用两个 CHECK 互斥；
- `PERSON_BUNDLE` 行的 `after_revision >= 1` 下限**保留**，`FACE_CONTROL` 允许 0（无控制行即
  `AUTO_ALLOWED`/revision 0）。

**重建顺序（本任务最需要证据的部分）**：`foreign_keys` 在迁移事务内无法关闭，而 SQLite 不允许在仍有
存活子行引用父表时 `DROP` 父表（`defer_foreign_keys` 只在 COMMIT 时校验，且不会因终态一致而放行）。因此
0012 先把 `person_aliases.last_operation_id` 停放到备份表并置 NULL、把两张操作表整体备份、按“先子后父”
删除、重建、逐列回填、再原样写回 alias 引用，最后重建 `idx_person_operations_committed` 与
`idx_person_operation_items_entity`。整个过程在既有单事务迁移里完成，`search_projection_queue` 一字未动
（`PERSON_CHANGED` 已准确覆盖，不新增 cause）。

迁移证据（`tests/integration/test_p3_face_correction_migration.py`，6 例）：真实 v11 库（含 3 条 operation、
4 条 item、1 次 UNDO、3 条 alias 引用、控制行与排除行）迁移后 operation/item/alias/control/exclusion 行
逐列不变、`schema.verify_schema(12)` 通过、`PRAGMA foreign_key_check` 为空、备份内 `schema_version=11`
且**没有** `entity_key`、v11 备份仍拒绝 `FACE_UNASSIGN`；注入损坏 0012 后整体回滚（版本仍 11、行不变、
无残留 `*_backup` 表）；缺件 `SCHEMA_INCONSISTENT`；过新库零写。

## 3. 脸级服务与模式/排除矩阵

`persons/face_correction.py` 是唯一的脸级用例实现，`persons/service.py` 只是门面：

- 逻辑控制：无控制行等价 `AUTO_ALLOWED, revision=0`；实际改变 bundle 的人工操作恰好 `current + 1`；
  `restore_auto` 写回 `AUTO_ALLOWED` 行**并从 live revision 向前推进**，而不是删行把版本打回 0。
- `unassign`：结束唯一有效关联（`MANUAL/CONFIRMED` 删除、`AUTO/CONFIRMED` 降级为
  `SUGGESTED/MANUAL_UNASSIGNED`），控制转 `MANUAL_UNASSIGNED`，并对原人物激活排除。
- `assign B`：结束原有效关联并对原人物激活排除；建立唯一 `MANUAL/CONFIRMED B`；解除 B 的排除（**只解除
  这一条**）；其他排除全部保留。同一 canonical 人物重复 assign 且未被排除时是 no-op（仍写 COMMITTED 操作）。
- `restore_auto`：移除 `MANUAL/CONFIRMED`，控制转 `AUTO_ALLOWED`，**全部排除保留、不采用任何建议**。
  已在 `AUTO_ALLOWED` 或无控制行时为 no-op，既有自动关联不被重复 restore 删除。
- `face_exclusions` 一律 upsert，`active=0` 保留行，从不删行；所有 person_id 在比较与写入前先 canonicalize
  （沿用 ADR-0006 的解析器，含环/悬挂拒绝）。
- `FACE_STATE_CONFLICT` 只用于权威 bundle 自相矛盾（同脸 >1 个 CONFIRMED、或 `MANUAL_UNASSIGNED` 却有有效
  关联），不把 ADR-0006 合法产生的“人工已指定但链接被合并降级”误判为冲突。

## 4. 幂等、批量与撤销

- `request_id` 仍是 `person_operations` 的全局幂等键；单项/批量 payload hash 用 UTF-8 canonical JSON，
  批量 items 先按 `face_id` 排序（顺序无关），保留每项 requested target。**门禁在一切 revision 检查之前**
  （用“后来 revision 已变化仍优先 replay”的用例证明）；同 key 异 payload 即
  `FACE_IDEMPOTENCY_CONFLICT`。
- 批量：1～500 项显式且去重的 `face_id`，先全批 shape/face/person/canonical target/revision/state 校验，
  再单事务提交；重复 face（即使 action 不同）与 501 项都是拒绝，事务中途失败整批回滚、operation 不落库。
- 撤销：扩展既有 `POST /api/v1/person-operations/{operation_id}/undo`。`persons/operations.py` 新增
  `operation_family()`（`UNDO` 行继承它所补偿操作的家族），因此脸级操作的撤销/幂等冲突回答 `FACE_*`、
  P3-03 操作回答 `PERSON_*`，而端点与 `request_id` 语义只有一个。撤销是新的 `UNDO` 操作：无冲突时按
  before 语义恢复且所有 revision 从 live 向前；任一 `FACE_CONTROL` item 的 after 快照不同即整批
  `FACE_OPERATION_NOT_UNDOABLE` + 只含字段名的 `conflicts`；不允许 undo an UNDO；快照指向的脸已消失记为
  具名 `face_missing` 冲突。
- `persons/operations.py` 另把权威→派生顺序抽成公共 `publish_derived()`，脸级操作用**同一个**函数，避免
  出现第二条追赶路径。

## 5. 自动路径门禁与合照范围

`face/index.py` 的保守关联每次提交都在**权威写事务内**重新读取人工事实：

- `_control_revision_gate()`：把本次 pass 观察到的 logical revision 与事务内当前值比较，变化的 face 记为
  `LATE_CONTROL_CONFLICT` 并**零写**；`reconcile()` 摘要新增该计数（诚实反映“拒绝过、没写”）。
  该入口以 `reconcile_asset(..., observed_controls=...)` 暴露，用例可注入过期观察值。
- `human_controlled_face_ids()` 仍在写事务内读取，`MANUAL_ASSIGNED`/`MANUAL_UNASSIGNED` 与活动排除命中
  一律不动。
- 新增 `_active_exclusion_persons()`/`_canonical_person_map()`：`AUTO_ALLOWED` 的脸仍然拒绝它被人工排除过的
  人物，且排除按 canonical 人物等价类生效（合并后仍挡住目标）。
- 建议始终是 `SUGGESTED`，没有任何路径把它提升为 `CONFIRMED`；`restore_auto` 后仍只读建议。
- 范围：所有写入都以请求里明确列出的 `face_id` 为单位；同图其他脸、其他资产、person 计数与队列在
  lifecycle 用例中逐项比对不变。

## 6. 权威→派生顺序与计数

changed 操作在一个 SQLite 事务内：operation 行 → 权威 face link/control/exclusion → `FACE_CONTROL` item
快照 → 受影响 `DISTINCT asset_id` → **恰好一次** `catalog_filter_revision`（cause 复用 `PERSON_CHANGED`）
→ 每资产 `search_projection_queue` → 已发布 face entry 的 `queue_candidate()`（`PENDING_REPAIR`）→ 回写
filter revision。事务内无文件 I/O、无模型、无 Lance，不写图片向量 `index_outbox`；no-op/replay 全部跳过。

`face_summary` 增加 ADR-0007 §9 的四个整数：`manual_assigned_faces`、`manual_unassigned_faces`、
`faces_with_active_exclusions`、`active_face_exclusions`。后者的 canonical 去重复用**同一个** redirect
解析器（而不是单跳 SQL join），因此 A→B→C 链与“同一 pair 的两种写法”都只计一次；未命名分组、建议、
单对相似度都不参与计数。

## 7. API、错误与隐私

新增 `GET/PUT /api/v1/faces/{face_id}/assignment`、`POST /api/v1/face-assignments/batch`，并扩展现有
undo 端点；OpenAPI 路径 35 → 37。PUT body 固定 `contract_version`（错版本即 `VALIDATION_ERROR`）、
`request_id`、`action`、`target_person_id`（仅 assign 必填）、`expected_revision >= 0`。

错误码与 HTTP：`FACE_NOT_FOUND` 404、`PERSON_NOT_FOUND` 404、`FACE_ASSIGNMENT_CONFLICT` 409、
`FACE_IDEMPOTENCY_CONFLICT` 409、`FACE_BATCH_TOO_LARGE` 422、`FACE_STATE_CONFLICT` 409、
`FACE_OPERATION_NOT_UNDOABLE` 409、`VALIDATION_ERROR` 422。

响应形状：`FACE_CONTROL` item 与 companion 的 `FaceOperationItem` 逐字段一致（`entity_type`/`face_id`/
`changed`/`mode`/`revision`/`assigned_person_id`/`exclusions`）；undo 端点返回 person 或 face 形状之一
（`response_model=PersonOperationResponse | FaceOperationResponse`），P3-03 响应体不变。session/Host/
Origin/CSRF 与统一脱敏沿用既有边界；api 用例对状态、操作与错误三类响应文本断言不含盘符路径，
也不含 `bbox`/`landmark`/`vector`/`latitude`/`confidence`/`similarity`/`payload_sha256`。

## 8. 实测命令与退出码

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --junitxml=test-results/p3-04b-full.xml` | exit 0；JUnit `tests=1114 failures=0 errors=0 skipped=1`，326.5 s |
| 本任务新增用例 | 50 例（见 §1） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，clean |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| 真实夹具 `tools/diagnostics/face_correction_smoke_fixture.py --device cpu` | exit 0，`status=ok`、`problems=[]`；见 §9 |
| C：`uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | exit 0，真实 onedir 重建成功 |
| C：`uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P3-04B-final --output D:\PhotoSearchSmoke\P3-04B-report.json` | exit 0，`status=ok`、`problems=[]`、`startup_seconds=2.41`、`coverage_capabilities_status=200`、`coverage_has_no_local_path=true` |

机械版本耦合更新（在原任务卡允许范围内）：`SUPPORTED_SCHEMA_VERSION` 11 → 12；
`tests/unit/test_catalog_migrations.py`（注释说明 0012 不新增表，集合不变）；
`tests/integration/test_p3_person_migration.py`（`SUPPORTED == 11` → `>= 11`，并把“已在目标版本”的幂等
用例重新钉在 0011，避免顺带执行 0012）；`tools/places/examples/mini-cities.{spec,manifest}.json` 的
`compatible_catalog_schema.maximum` 11 → 12（否则示例地名包在当前 schema 下被判不兼容，P2 dataset tool
两例会失败）。这是版本推进的机械后果，未改任何地名包语义或冻结契约。

测试环境说明：受限令牌下 pytest 的 basetemp（`Path.mkdir(mode=0o700)`）在本机会生成普通用户不可读的目录，
首次运行会大量 setup error。本任务按 P2-00/P3-04A 已记录的既有边界，在**正常 Windows 权限**下用新的
项目内 basetemp 目录重跑全部命令；未修改任何 ACL，也未删除历史测试目录。

## 9. 真实夹具 smoke（`%TEMP%`，4 资产 / 9 脸 / 8 个未命名分组）

`tools/diagnostics/face_correction_smoke_fixture.py --device cpu` 走真实检测与保守关联，再按用户场景
「自动分组 → 命名 → 单脸取消 → 指定另一人物 → 恢复自动 → 撤销 → 重启查询」逐步断言，输出
`status=ok`、`problems=[]`：

| 步骤 | 实际观察 |
|---|---|
| 检测/分组 | 4 资产 / 9 脸 / 8 个未命名分组；两次 rename 均为非重放 |
| 单脸取消 | `FACE_UNASSIGN`、`MANUAL_UNASSIGNED`、revision 1、有效关联为空、对原人物（person 8）激活排除 |
| 指定另一人物 | `FACE_ASSIGN`、`MANUAL_ASSIGNED`、revision 2、有效关联为目标人物 |
| 恢复自动 | `AUTO_ALLOWED`、排除仍为 `[8]`、有效关联为空（不采用建议） |
| 补偿撤销 | `UNDO`、`MANUAL_ASSIGNED`、revision 4（大于恢复时的 revision，始终向前） |
| 同图其他脸 | `siblings_unchanged=true` |
| 重新关联 | `processed=1`、`human_controlled=1`、`late_control_conflicts=0`；被排除的 person 8 未回归 |
| 重启查询 | 关闭并重开数据根后 mode/revision/归属/排除与撤销后完全一致（`assigned_person_id_matches=true`） |
| 计数 | `manual_assigned_faces=1`、`manual_unassigned_faces=0`、`faces_with_active_exclusions=1`、`active_face_exclusions=1` |
| 候选索引 | 该 face 的 index entry 为 `CONFIRMED`（投影追赶完成，向量未改） |

全程只写 `%TEMP%` 下的合成/公共领域夹具，不读真实图库、不发网络请求。

## 10. C 级打包核对

- `dist/PhotoSearch/_internal/photo_search/catalog/migrations/` 含 `0001`～`0012`（12 个文件），
  `0012_face_correction_operations.sql` 的 SHA-256
  `6E4E56F03AE93D20EF5BBC8A14037F1CA86E1B593B3C7E153D3A32D27EA1CF12` 与源码**逐字节一致**。
- 打包 smoke 的 data root（含中文与空格路径）实测 `schema_version=12`、`person_operation_items` 含
  `entity_key` 列、`person_operations` 的 CHECK 含 `FACE_UNASSIGN`。
- `coverage_faces_block` 里出现了本任务新增的四个只读计数（bundle 内的 `scheduler/projection.py` 是新的），
  且 `coverage_has_no_local_path=true`。

## 11. D 级具名故障注入

| 注入 | 结果 |
|---|---|
| 损坏 0012 | 整体回滚：版本仍 11、operation/item/alias 行不变、无残留备份表 |
| 缺件（DROP `person_operation_items`） | `SCHEMA_INCONSISTENT`，主库字节不变 |
| 过新库（schema 13） | `SCHEMA_TOO_NEW` 且库/API 两条路径零写 |
| 无控制行 revision 0 → 1 | 首次 unassign 成 revision 1；错误传 1 被拒且不补建控制行 |
| 过期 revision | `FACE_ASSIGNMENT_CONFLICT` 只回显 face_id/expected/current，库逐字节不变 |
| 幂等门顺序 | 后来 revision 已变化时同 key 同 payload 仍 replay；异 payload 冲突零写 |
| 晚到自动结果 | 注入过期 `observed_controls` 即 `LATE_CONTROL_CONFLICT`、链接与控制行逐字节不变 |
| 锁后重扫/重关联 | 重启 + 重新入队 + `reconcile()`：`human_controlled=1`、新分组 0、排除不变 |
| 排除命中 | `AUTO_ALLOWED` 的脸在重新关联后仍不回到被排除人物（含合并后的 canonical 等价） |
| 批量中途失败 | 第 2 项 revision 冲突即第 1 项、operation、filter revision、队列全部回滚 |
| 批量重复/超限 | 重复 face（含不同 action）422；501 项 `FACE_BATCH_TOO_LARGE` + `max_batch_size` |
| 撤销冲突 | 单项/批量任一 face 被后续编辑即整批 `FACE_OPERATION_NOT_UNDOABLE` + `conflicts` |
| undo an UNDO / 版本不符 / 已被撤销 | 三种都 `FACE_OPERATION_NOT_UNDOABLE`（脸级操作不回落 `PERSON_*`） |
| no-op 的撤销 | operation 标 UNDONE、`changed=false`、filter revision 与队列不变 |
| P3-03 交叉 | 合并后再撤销旧脸级操作：canonical 排除变化即整批拒绝、库不变 |
| 快照指向的脸消失 | 具名 `face_missing` 冲突，operation 保持 COMMITTED |
| API 边界 | 匿名 401、缺 CSRF 403、坏 Origin/Host 403；`FACE_*`/`PERSON_*`/`VALIDATION_ERROR` 状态码逐项 |

## 12. 未验证项（不阻塞本任务，但必须如实记录）

- 真实家庭照片库规模、跨进程并发写、真实 USB/SMB 断盘仍未验证（沿用 P2/P3 既有边界）。
- 混合 500 项的规模与延迟只在合成夹具验证形状与原子性，未做批量吞吐压测。
- `FACE_CONTROL` 全 exclusion 快照在极端大量人工否定下的体积未测量（P3-04A 已把这条列为待测）。
- 前端脸框/待手动归类/最近操作页仍属 P3-06 边界；本任务不加前端，真实浏览器 smoke 未在本任务重复执行。
- 检测重跑产生新 `face_id` 后的人工锚点映射仍属 P3-08；本任务不实现，也不把丢锁当临时方案。
- `late_control_conflicts` 只在 `reconcile_asset(observed_controls=...)` 注入下验证；真实双进程竞态未复现。

## 13. 交接记录

```text
任务 ID / 状态：P3-04B / PASS（B + D + C）
冻结合同：ADR-0007 + p3-face-correction-contracts.v1.json（本任务一字未改）
已完成行为：0012 扩闭合 CHECK（逐列保留 operation/item/undo/alias）；单脸 assign/unassign/restore_auto 与
  1..500 mixed batch；全局 request_id 幂等；FACE_CONTROL before/after 快照与向前补偿撤销；
  自动路径 logical revision 门禁 + canonical 排除；PERSON_CHANGED 一次 + 资产队列 + PENDING_REPAIR；
  3 条新 API 路径与扩展 undo、FACE_* 错误；face_summary 四个只读计数；schema_version 11→12
修改文件与接口/迁移变化：新增 src/photo_search/persons/face_correction.py、
  catalog/migrations/0012_face_correction_operations.sql、tests/p3_face_correction_fixtures.py、
  5 个新测试文件、tools/diagnostics/face_correction_smoke_fixture.py；加性修改 persons/**、face/index.py、
  catalog/{schema,errors}.py、scheduler/projection.py、api/{models,routes,errors}.py；
  未改 0001～0011、ADR、冻结 JSON、依赖（95 包）、PhotoSearch.spec、前端、places/geo/ingestion/workers
实际命令和结果：pytest 1114 例 0 失败 1 跳过（exit 0，JUnit 326.5 s）；ruff/diff-check/lock 全 clean；
  真实夹具 smoke status=ok problems=[]（4 资产/9 脸/8 分组，6 步操作全非重放，重启后一致，重新关联
  human_controlled=1 且被排除人物未回归）；真实 onedir 重建 + packaged_api_smoke status=ok problems=[]
  startup=2.41s，bundle 含 0001～0012 且 0012 与源码 SHA-256 一致，打包后 schema_version=12
报告/详细日志路径：docs/reports/P3-04B-face-correction-lock-and-undo.md；
  JUnit test-results/p3-04b-full.xml；打包 smoke D:\PhotoSearchSmoke\P3-04B-report.json
下一任务：P3-04C — P3-04 独立 C2 审查（含必要最小修复），推荐 C2 / GPT-5.6 Sol / high
任务卡：docs/tasks/P3-04C.md
```
