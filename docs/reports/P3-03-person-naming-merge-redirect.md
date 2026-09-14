# P3-03 — 人物名称、NFC `name_key`、同名合并、旧 ID 重定向

**状态：PASS（B + D + C）**，执行于 2026-09-15。任务卡 [docs/tasks/P3-03.md](../tasks/P3-03.md)，
冻结规则 [ADR-0006](../decisions/ADR-0006-person-naming-merge-operation-contract.md)。

执行模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**，按任务卡推荐档位执行；本次会话未发生模型切换，
也未声称发生过。客户端未暴露 token/费用明细。本任务未触发 C1/C2 升级：没有新的跨模块协议或数据语义
决策（C2 已冻结 ADR-0006），也没有遇到 ONNX/CUDA/DLL/权限类环境问题。

起始基线：HEAD `d4eae44`（P3-03 规则冻结提交），工作树干净。

## 1. 交付物与边界

新增：

| 文件 | 作用 |
|---|---|
| `src/photo_search/persons/contracts.py` | `nfc1:` 版本化 name_key、确定性 operation_id、canonical JSON/摘要、冻结错误码与界限 |
| `src/photo_search/persons/identity.py` | 唯一的 `merged_into` 解析器（含多跳、环、悬挂目标、步数上限） |
| `src/photo_search/persons/bundle.py` | `PERSON_BUNDLE` 前值/后值快照与语义比较（忽略时间戳） |
| `src/photo_search/persons/operations.py` | 唯一业务用例：RENAME / MERGE_BY_NAME / MERGE / BATCH_MERGE / UNDO、幂等、批量原子、权威→派生提交顺序 |
| `src/photo_search/persons/service.py` | 薄 facade（读 canonical 视图；写入口全部在 `Catalog.write()` 内） |
| `src/photo_search/catalog/migrations/0011_person_knowledge.sql` | 连续增量迁移，`schema_version 10 → 11` |
| `tools/diagnostics/person_smoke_fixture.py` | 真实夹具端到端 smoke（`%TEMP%`，从不接触真实照片库） |
| `tests/{p3_person_fixtures.py, unit/test_p3_person_contract.py, integration/test_p3_person_*.py}` | 74 个新用例 |

加性修改：`catalog/errors.py`（7 个 P3-03 错误码 + `MAX_PERSON_BATCH_SIZE = 500`）、
`catalog/schema.py`（`SUPPORTED_SCHEMA_VERSION = 11`）、`api/{models,routes,errors}.py`（4 条人物路径与
状态映射）、`scheduler/projection.py`（`face_summary` 新增 5 个只读计数）、`face/index.py`（代表与候选读取
排除人工未归类/活动排除），以及 8 处版本耦合的机械测试更新（见 §12）。

**未修改**：`0001`～`0010`、冻结 ADR 与 `docs/decisions/contracts/*.json`、ADR-0002/0003/0005 语义、
五字段 job 身份、lease/attempts/迟到提交、artifact/outbox 提交顺序、P2-03A/B 地点状态机与幂等、
P2-04 tokenizer 世代、P2-05 RRF/`hit_reason`、P2-06A/B 会话/profile/反馈、P2-07 上传限制、
P3-01 OCR 契约、P3-02 人脸契约（`face-document.v1` 形状、`face_id` 确定性、质量口径、校准阈值、
独立候选索引世代语义）、`geo/**`、`places/**`（仅 import 其 `revision` 复用器）、`ingestion/**`、
`workers/**`、前端资源、打包入口 `PhotoSearch.spec`、依赖（`uv.lock` 仍 95 包）。

## 2. 名称与版本化 `name_key`

`NAME_KEY_VERSION = 1`，前缀 `nfc1:`，规则与 ADR-0006 §2 逐字一致：

1. 输入必须是 `str`，否则 `PERSON_NAME_INVALID`；
2. 执行 Unicode **NFC**；
3. 使用 Unicode whitespace 分词，去首尾空白并把连续空白合为一个 ASCII 空格；
4. 结果为空 → `PERSON_NAME_INVALID` 且**零写入**；
5. `persons.name` 保存规范后的显示文本，`persons.name_key = "nfc1:" + 该文本`。

不做 casefold、NFKC、拼音、繁简、同义词、别名或模糊匹配。单元用例逐条固定：
`"  爸爸\t " → ("爸爸", "nfc1:爸爸")`、`"A  B" → "A B"`、`"A\t\nB" → "A B"`、
`"爸爸　爸爸" → "爸爸 爸爸"`（全角空格也是 whitespace）、`Alice` 与 `alice` 是两个不同的 key、
组合字符 `e◌́` 合成为 NFC 的 `é`、全角 `Ａ` **不**折叠成 `A`（NFKC 会，本任务不做）。

界面占位文字「未命名人物 001」在服务端**没有特例**：它从未被提交，因此也不参与匹配；测试固定这一
事实，防止后来有人为了"保护"占位文字而发明第二条命名规则。

`MAX_PERSON_NAME_CHARS = 200` 是本实现选择的**界限**（ADR-0006 未规定长度），与地点显示名同值，
用于让列保持可索引；它是界限而不是语义规则，已在契约模块注释中写明。

## 3. 迁移 0011 与表命名

连续增量 `0011_person_knowledge.sql`（`schema_version 10 → 11`，`0001`～`0010` 逐字节不变）：

| 表 | 职责 |
|---|---|
| `person_operations` | 幂等与历史权威：`request_id UNIQUE`、`payload_sha256`、`type ∈ {RENAME, MERGE_BY_NAME, MERGE, BATCH_MERGE, UNDO}`、`source_person_ids_json`、`target_person_id`、`state ∈ {COMMITTED, UNDONE}`、`operation_revision UNIQUE`、`undo_of_operation_id`、`undone_by`、`catalog_filter_revision` |
| `person_operation_items` | 每实体（`PERSON_BUNDLE`）的前值/后值快照与操作后 revision，`PK(operation_id, item_index)` |
| `person_aliases` | 命名人物的搜索别名；`UNIQUE(person_id, normalized_alias)`，**不同人物可同名别名**，别名永不参与合并 |
| `face_assignment_controls` | 每脸一个人工模式（`AUTO_ALLOWED/MANUAL_ASSIGNED/MANUAL_UNASSIGNED`）+ revision |
| `face_exclusions` | `PK(face_id, person_id)` 的人工否定；`active ∈ {0,1}` |

另按 0009 的**已验收范式**重建 `search_projection_queue`（原样保留五行 cause，新增第六个
`PERSON_CHANGED`），不重写、不复制 0006/0009 的触发器（触发器按名引用该表，替换后继续生效）。这是
0011 唯一替换的表，其余全部是新增对象。

数据库能表达的不变量全部下沉到 SQL 并由 `test_the_person_tables_enforce_the_p3_03_invariants` 逐条
验证：未知 `type` 被拒、`request_id` 重复被拒、`operation_revision` 重复被拒、`state='UNDONE'` 必须带
`undone_by`、操作不得补偿自身、别名在人物内唯一且空别名非法、指向不存在操作的外键被拒、
`MANUAL_MAYBE` 之类的模式被拒、排除行主键重复与 `active=2` 被拒、被人格层引用的人物不可删除。

**P3-04 兼容是硬约束**：两张人工表只包含架构必需列，没有任何"无默认值的额外 `NOT NULL` 列"，因此
P3-02 的人工优先用例只需把自建的两张临时表换成真实表即可（本任务已如此更新，见 §12）。

## 4. 唯一业务用例：改名 = 同名合并 = 显式合并

`rename_person()` 在同一个写事务里解析活动同名目标，并在目标是**别人**时调用与 `merge_persons()`
**同一个** `_apply_merge()`：

| 保存时的情形 | 结果 | 操作 `type` |
|---|---|---|
| 没有活动同名目标 | 原 `person_id` 改名（id 不变） | `RENAME` |
| 目标是自己且显示值相同 | 语义 no-op：写一条 `COMMITTED` 操作，不递增任何 revision、不写 outbox | `RENAME` |
| 目标是别人 | 合并到该目标（源保留 id，仅写 `merged_into`） | `MERGE_BY_NAME` |
| 显式合并 1 个源 | 调用同一合并函数 | `MERGE` |
| 显式合并 ≥2 个源 | 同一次原子操作 | `BATCH_MERGE` |

`type` 描述**请求形状**（1 个命名源 = `MERGE`，多个 = `BATCH_MERGE`），因此即使所有源都已解析到目标
（语义 no-op）类型也不会漂移。显式合并的**目标和每个源都先 canonicalize 再去重**，解析到目标或重复的
源按 no-op 丢弃；若全部丢弃则整次操作是语义 no-op。

幂等门在**任何人物/revision 检查之前**执行（ADR-0006 §7）：同 `request_id` + 相同摘要 → 返回原结果并
标记 `replayed=true`；摘要不同 → `PERSON_IDEMPOTENCY_CONFLICT` 且零写入。摘要按"客户端意图"规范化
canonical JSON，显式 `sources` 以**请求 person ID** 排序，不使用会随合并漂移的 canonical ID。
被拒绝的请求（校验/冲突）不占用 `request_id`，测试固定了这一点。

`operation_id = sha256("person-operation\0" + request_id)` 的十六进制值，因此重放是可证明的同一次调用。

## 5. 合并的数据行为

* **id 稳定**：源只写 `merged_into` 并递增自身 revision（源行、源显示名、源历史全部保留），目标只递增
  一次 revision；任何路径都不删除人物行、不形成重定向环（`PERSON_REDIRECT_CYCLE`）。
* **别名**：源的形式显示名与其别名一起移入目标别名集合，按 `(target_person_id, normalized_alias)` 去重；
  目标当前正式名永不成为自身别名；碰撞只在目标内去重，不跨人物拒绝。
* **链接去重**：先在内存按 `(face_id, assignment_kind)` 汇总再一次写回 —— `CONFIRMED` 优先于
  `SUGGESTED`，同 decision 取 revision 较新者；不同 `assignment_kind` 仍可共存，但每张脸始终只有一个
  `CONFIRMED`。来源属性不升级：`MANUAL` 仍是 `MANUAL`，一次批量合并不等于用户逐脸核实。
* **人工优先**：若该脸是 `MANUAL_UNASSIGNED`，迁入的 `CONFIRMED` 降为 `SUGGESTED` 且 reason 固定
  `MANUAL_UNASSIGNED`；若该脸对目标存在活动排除（排除里的 person ID 也 canonicalize），同样降级、
  reason 固定 `HUMAN_CONFLICT`，**排除行不删除**。降级脸计入 `pending_confirmation`，不成为代表、
  不会被自动应用。
* **代表/头像**：目标已有头像则保留；没有时从合并后的有效成员中按当前 face processor/space 内
  `quality_state='OK'`、`face_pixels DESC, face_id ASC` 选一个，**不跨 space**、不重算任何向量。
* **计数**：人物成员只数无活动排除、非 `MANUAL_UNASSIGNED` 的 `CONFIRMED` face；照片数对该结果
  `COUNT(DISTINCT asset_id)`，因此同图多脸只算一张照片（夹具实测 `face_count=3 / asset_count=2`）。

## 6. 权威提交与派生追赶

`changed` 操作在同一 `BEGIN IMMEDIATE` 事务里按 ADR-0006 §8 的顺序完成：
1. 操作行（先写，因为别名等行引用它）→ 人物/别名/链接/重定向权威行 → 操作项快照；
2. 变更前后所有受影响 face 与 `DISTINCT asset_id`；
3. `catalog_filter_revision` **只递增一次**，cause `PERSON_CHANGED`，并经既有 projection refresher 为
   已有图片向量预约 P2-03 outbox；
4. 每个受影响资产 upsert `search_projection_queue(..., 'PERSON_CHANGED', now_ms)`；
5. 每个受影响 face 调用既有 `queue_candidate()`：entry 先变 `PENDING_REPAIR`，由后台重放确认，不直接写
   `CONFIRMED`、不重算向量（仅对已在该世代发布过的脸排队，世代不存在时直接跳过）；
6. 写回操作行最终 `catalog_filter_revision`。

**no-op 不执行第 2～5 步**（测试断言 revision、`catalog_filter_revision_events`、`search_projection_queue`
与 face outbox 全部不变）。人物操作从不触碰图片索引命名空间：集成用例断言 `index_outbox`/`index_entries`
仍为 0 行。

## 7. 撤销：新的补偿操作

`UNDO` 是**新操作**，不倒退版本号：

* 先做 request_id 重放；再校验原操作存在（否则 `PERSON_NOT_FOUND`）、属于 P3-03 可撤销类型、
  `state='COMMITTED'`、`undone_by IS NULL` 且 `operation_revision` 匹配，否则
  `PERSON_OPERATION_NOT_UNDOABLE`；
* 逐项比较 live bundle 与 `after_value_json`；任一不符（含"合并后新增成员导致快照不等"）→ 整次撤销
  拒绝、**零写入**，`details.conflicts` 只列 `entity_type/entity_id/reason`（字段名，不含姓名/坐标/相似度）；
* 无冲突时按 `before_value_json` 恢复人物行/别名集合/链接集合，**所有被恢复的 revision 都从当前值向前
  递增**（人物 `current+1`；链接 `max(before_revision, current_revision) + 1`；仅语义确实变化的行才写）；
* 原操作置 `state='UNDONE'` 并写 `undone_by`；UNDO 自身也保存 before/after 快照，但首版**不允许 undo
  an UNDO**（类型不在可撤销集合内，测试固定）。

实测证据：改名撤销后 `name/name_key` 回到 NULL 而 revision 由 2 → 3；合并撤销后源 `merged_into` 归空、
源链接以 revision 3（原 2）恢复、目标别名清空、目标头像回到 NULL；目标在合并后又被并入第三个分组时，
撤销返回 `PERSON_OPERATION_NOT_UNDOABLE` 且 `conflicts=[{entity_type: PERSON_BUNDLE, entity_id: <target>,
reason: "revision,links"}]`、数据库逐字节不变。撤销在**新进程**中依然可用（持久化验证）。

## 8. API、只读状态与隐私

`/api/v1` 新增 4 条路径（沿用既有 Host/Origin/session/double-submit CSRF 边界）：

| 路径 | 说明 |
|---|---|
| `GET /api/v1/persons/{person_id}` | canonical 人物 + 别名 + `face_count` + 按 `asset_id` 去重的 `asset_count`；旧 id 以 `requested_person_id/canonical_person_id/redirected` 应答，**不发 3xx** |
| `PUT /api/v1/persons/{person_id}/name` | `request_id` + `name` + `expected_revision` → RENAME / MERGE_BY_NAME |
| `POST /api/v1/persons/merge` | `request_id` + `target_person_id` + `target_expected_revision` + `sources[1..500]` → MERGE / BATCH_MERGE |
| `POST /api/v1/person-operations/{operation_id}/undo` | `request_id` + `expected_operation_revision` |

冻结错误码与 HTTP 映射实测：`PERSON_NOT_FOUND` 404；`PERSON_NAME_INVALID`、`PERSON_BATCH_TOO_LARGE` 422；
`PERSON_REVISION_CONFLICT`、`PERSON_IDEMPOTENCY_CONFLICT`、`PERSON_REDIRECT_CYCLE`、
`PERSON_OPERATION_NOT_UNDOABLE` 409。

`configure` 细节一处：`PersonMergeRequest.sources` **不在传输层设 `max_length`**，1..500 由服务层判定，
否则 501 个源会先被 pydantic 变成 `VALIDATION_ERROR`，冻结码 `PERSON_BATCH_TOO_LARGE` 在 HTTP 上不可达。
这与 P2-03A 的 `PlaceBatchRequest` 完全同构。

`face_summary` 新增只读计数：`named_persons`、`unnamed_persons`、`aliases`、`pending_confirmation`
（reason ∈ {`HUMAN_CONFLICT`,`MANUAL_UNASSIGNED`} 的 distinct face）、`recent_operations`
（0～20）。**不输出**名称、别名文本、person id、bbox、关键点、向量或单对相似度——集成用例对
`/capabilities` 的原始响应文本做了"名称与 `nfc1:` 均不出现"的断言。

## 9. 阈值/规则之外的实现选择（非 ADR 语义）

1. 显示名上限 200 字符（§2）。
2. `type` 按请求形状而非去重后源数决定（§4）。
3. 源的 `expected_revision` 以**canonical 对象**解析后的 revision 比较：客户端从 `GET /persons/{id}` 读到
   的就是该值，因此合并后的旧 id 也能用同一条读取规则提交（`tools/diagnostics/person_smoke_fixture.py`
   正是这样消费 API 的）。
4. 纯移动的链接保留原 revision，只有 decision 被降级的行才 +1；`last_operation_id` 只写给真正变化的行。
5. `person_aliases.last_operation_id` 外键指向 `person_operations`，因此操作行在权威行之前写入（§6 顺序）。

## 10. 实测命令与退出码

全部必需命令都在**沙箱外（提权）**执行：受限令牌无法创建 pytest basetemp（`.pytest-tmp` 的 DACL 拒绝
访问），这是本机既有环境事实，P2-02/P3-01/P3-02 同样如此；uv 缓存始终使用项目内 `.uv-cache`。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | 0 | JUnit `tests=1038 failures=0 errors=0 skipped=1`，324.6 s |
| 本任务新增用例 | 0 | 74 例（contract 31、merge 9、operations 12、undo 10、api 5、migration 6、lifecycle 1） |
| `uv run --no-sync ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | clean |
| `uv run --no-sync uv lock --check` | 0 | `Resolved 95 packages`（依赖未变） |
| `uv run --no-sync python tools/diagnostics/person_smoke_fixture.py --device cpu` | 0 | `status=ok`、`problems=[]`；见 §11 |
| C：`uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | 0 | 真实 onedir 重建成功 |
| C：`uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P3-03-final --output D:\PhotoSearchSmoke\P3-03-report.json` | 0 | `status=ok`、`problems=[]`、`startup_seconds=1.2`、`coverage_capabilities_status=200`、`coverage_has_no_local_path=true` |

## 11. 真实夹具 smoke（`%TEMP%`，4 资产 / 9 脸 / 8 个未命名分组）

`tools/diagnostics/person_smoke_fixture.py --device cpu` 用**产品路径**跑完整闭环：
注册 → 扫描 → 真实检测/关联 → 命名 → 同名合并 → 撤销 → 人工未归类 → 显式合并 → 撤销 → 只读状态 →
重新索引追赶。记录结果：

| 观察 | 值 |
|---|---|
| 分组 | `assets=4`、`faces=9`、`unnamed_persons=8` |
| 操作 | `rename=RENAME`、`same_name_merge=MERGE_BY_NAME`、`same_name_undo=UNDO`、`explicit_merge=MERGE`、`undo=UNDO`，`replayed` 全为 `false` |
| 人工优先 | 人工未归类脸在合并中降为 `["SUGGESTED","MANUAL_UNASSIGNED"]`；撤销后回到 `CONFIRMED`/`AUTO`，人工模式行仍为 `MANUAL_UNASSIGNED` |
| 只读状态 | `named_persons=1`、`unnamed_persons=7`、`aliases=0`、`pending_confirmation=0`、`recent_operations=5` |
| 候选索引 | 撤销后 `repaired_after_undo=3`，最终 `entries={CONFIRMED: 9}`（无残留 `PENDING_REPAIR`） |

C 级打包审计：`dist/PhotoSearch/_internal/photo_search/catalog/migrations/` 含 `0001`～`0011`（11 个文件），
`0011_person_knowledge.sql` 的 SHA-256 与源码**逐字节一致**
（`0CD3DC42D838D7C6AE160335240E366E884353C0F42590A2E31781553A064325`）；
冻结包内 `/api/v1/capabilities.faces` 返回真值并带上 P3-03 的五个新计数
（`named_persons/unnamed_persons/aliases/pending_confirmation/recent_operations`，均为 0），证明
`photo_search.persons` 真的在包内；打包 smoke 的 data root 实测 `schema_version=11` 且含
`persons/person_aliases/person_operations/person_operation_items` 四张新表。

## 12. D 级故障注入（具名场景）

| 场景 | 注入方式 | 观察结果 |
|---|---|---|
| 同 request_id 幂等重放 | 同一 `request_id` + 同一意图（含尾随空白） | 返回原 `operation_id`/`operation_revision`，`replayed=true`，操作行数不变 |
| 同 request_id 载荷冲突 | 同 `request_id` + 不同名字 | `PERSON_IDEMPOTENCY_CONFLICT`，零写入 |
| 幂等门早于 revision 检查 | 重放一个"已被后续改名推进 revision"的旧请求 | 仍返回原操作（不因后来状态变成冲突） |
| 过期 revision | `expected_revision = 当前+3` | `PERSON_REVISION_CONFLICT`（含 person id/期望/当前），零写入 |
| 批量合并中途失败 | 第二个源 revision 过期 | 整批回滚：无操作行、无重定向、filter revision 不变 |
| 重定向环 | 手工把两条 `merged_into` 连成环 | 读与写都返回 `PERSON_REDIRECT_CYCLE`，零写入 |
| 把分组改成已有名字 | 两个活动分组同名保存 | 自动 `MERGE_BY_NAME`，活动同名人物只剩一个，源行与文件位置保留 |
| `MANUAL_UNASSIGNED` 脸 | 合并前写入人工模式 | 迁入的 `CONFIRMED` 降为 `SUGGESTED/MANUAL_UNASSIGNED`，人物计数排除该脸，人工行不变 |
| 活动排除 | 排除行指向目标，以及排除行指向**源**（合并后 canonicalize） | 两种情况都降为 `SUGGESTED/HUMAN_CONFLICT`，排除行保留 |
| 撤销目标被后续编辑 | 合并后再把第三个分组并入目标 | `PERSON_OPERATION_NOT_UNDOABLE`（列出实体与字段原因），整次零写入 |
| undo an UNDO | 对 UNDO 操作再撤销 | `PERSON_OPERATION_NOT_UNDOABLE` |
| 未知操作 | 撤销不存在的 operation_id | `PERSON_NOT_FOUND` |
| no-op | 保存相同名字 | 写 `COMMITTED` 操作但不递增人物/filter revision、不写 outbox/队列 |
| 投影与索引 | 合并/改名后 | `catalog_filter_revision` 只 +1（cause `PERSON_CHANGED`）、受影响资产入队、受影响脸 entry 变 `PENDING_REPAIR` 并由协调器回到 `CONFIRMED`；图片索引零变化 |

**版本耦合的机械更新（8 处，非生产代码）**：`tests/unit/test_catalog_migrations.py`（表集合 +5）、
`tests/integration/test_p2_scheduler_recovery.py`（全表集合 +5）、
`tests/integration/test_p3_face_migration.py`（`SUPPORTED == 10` → `>= 10`）、
`tests/integration/test_p3_face_lifecycle.py`（人工优先用例不再自建两张表，改为写 0011 的真表，列不变）、
以及 5 个迁移测试文件的 `migrations_upto()` 助手改为直接 glob 真实迁移目录（原先依赖
`schema.migration_files()`，当一个用例把 `SUPPORTED_SCHEMA_VERSION` 钉到旧版本时会在"把新版脚本拷进
旧目录"这一步自相矛盾）。另外 `tools/places/examples/mini-cities.{spec,manifest}.json` 的
`compatible_catalog_schema.maximum` 由 10 机械更新为 11，并用项目自带
`tools/places/manage_dataset.py build` 重新生成 manifest 核对（与手工修改逐字节一致）。

## 13. 未验证项（不阻塞本任务，但必须如实记录）

* **真实家庭照片库规模**：夹具只有 4 张资产、9 张脸、8 个分组；批量合并 500 源、单人物数千脸的
  `IN (...)` 分块与投影追赶**未在真实规模上压测**。
* **多人同名真实场景**：只用脚本构造的同名分组验证；真实家庭里"同名不同人"的呈现与用户改名顺序未测。
* **跨进程并发**：写事务是单进程 `BEGIN IMMEDIATE`；另一进程同时改名同名、或并发第二次合并的竞争
  未真实注入（唯一索引竞争被映射为结构化冲突，但没有双进程复现）。会话/操作日志的跨进程可见性未测。
* **前端交互**：本任务未接触前端；「保存后将合并到爸爸」的提示、待确认列表与撤销入口仍是 P3-06 的范围。
* **真实人名规范化边界**：emoji、组合字符序列（ZWJ 家族 emoji）、双向文本、超长组合序列只做了 NFC/
  空白/大小写/全角与分解重音用例，未做完整 Unicode 规范化一致性测试；也不做拼音/繁简（按 ADR 明确不做）。
* **别名写入入口**：`person_aliases` 只能由合并（源形式名/别名迁移）产生；P3-03 没有独立的别名编辑 API。
* **头像显示**：`avatar_face_id` 只按质量与 space 选择，未验证 UI 渲染与小图缓存失效。
* **`pending_confirmation` 的消费方**：计数与降级语义已实现，但"待确认"列表页（P3-06）与单脸纠错写入口
  （P3-04）尚未实现，因此该分支只由本任务的 D 级注入覆盖。
* **回收/补偿的历史规模**：`person_operation_items` 的 before/after 快照只在小夹具验证；超大人物
  （数千链接）的快照体积未测量。

## 14. 交接记录（策略 §8.4）

```text
任务 ID / 状态：P3-03 / PASS（B + D + C）
实际模型 / reasoning：DS-H（DeepSeek V4.1 Flash / 高推理）按任务卡推荐执行；未发生也未声明模型切换
基线提交号：d4eae44（P3-03 规则冻结提交），工作树干净
已完成行为：nfc1: 版本化 name_key 与规范化边界；迁移 0011（person_operations/person_operation_items/
  person_aliases/face_assignment_controls/face_exclusions + search_projection_queue 第六 cause）；
  merged_into canonical 解析（多跳/环/悬挂/步数上限）；改名=同名合并=显式合并同一业务用例；批量原子；
  request_id 幂等（重放/冲突）；操作前值与补偿撤销（revision 只向前）；人工未归类/活动排除降级为待确认并保留
  人工行；别名迁移与人物内去重；代表/头像同 space 重选；PERSON_CHANGED 一次 filter revision + 队列 + 候选
  PENDING_REPAIR 追赶；4 条 /api/v1 路径与冻结错误码；face_summary 五个只读计数
修改文件与接口/迁移变化：新增 src/photo_search/persons/**（5 文件）、catalog/migrations/0011_person_knowledge.sql、
  7 个测试文件 + 共享夹具 p3_person_fixtures.py、tools/diagnostics/person_smoke_fixture.py；加性改
  catalog/errors.py、catalog/schema.py、scheduler/projection.py、face/index.py、api/{models,routes,errors}.py；
  schema_version 10→11；未改 0001～0010、冻结 ADR/JSON、geo/places/ingestion/workers/前端/打包入口/依赖（95 包）
实际命令和结果：pytest 1038 例 0 失败 1 跳过（exit 0，JUnit 324.6s）；本任务新增 74 例；ruff/diff-check/lock
  全 clean；真实夹具 smoke status=ok problems=[]（4 资产/9 脸/8 分组，5 步操作全部非重放，人工未归类降级后
  撤销恢复，索引 9 CONFIRMED）；真实 onedir 重建 + packaged_api_smoke status=ok problems=[] startup=1.2s，
  bundle 含 0001～0011 且 0011 与源码 SHA-256 一致，打包后 schema_version=11
报告/详细日志路径：docs/reports/P3-03-person-naming-merge-redirect.md；JUnit test-results/junit.xml；
  打包 smoke D:\PhotoSearchSmoke\P3-03-report.json
未验证项和具体阻塞：真实家庭照片库规模；多人同名真实场景；跨进程并发；前端交互（P3-06）；emoji/组合字符/
  双向文本；独立别名写入入口；头像渲染；待确认列表页；超大人物的快照体积。无阻塞
失败修复轮次：实现期 3 处自查修正（操作行必须先于引用它的别名行写入、批量合并的 type 按请求形状而非去重后
  源数决定、跨空间头像选择的测试夹具 spec_revision 必须用真实世代）
下一任务及必须读取的文件：P3-04A（单脸纠错、合照与人工锁定的 C2 规则冻结），任务卡 docs/tasks/P3-04A.md，
  推荐 C2 / GPT-5.6 Sol / high 先冻结规则，再由 P3-04B 交 DS-H 实现 + C2 审查；需读 AGENTS.md、
  docs/development-status.md、本卡、ADR-0006 全文、主架构 §4.2/§8.3.4/§8.3.5/§11.2/§12.4、
  策略 §3/§8/§11/§12
独立审查者与结果：本任务按任务卡要求为 DS-H 实现，后续仍需 C2 独立审查；本次自证为 B + D + C
```

## 15. 下一任务

**P3-04A — 单脸纠错、合照与人工锁定：C2 规则冻结**，任务卡
[docs/tasks/P3-04A.md](../tasks/P3-04A.md)。ADR-0006 §1 已把"单脸取消、手动指定、恢复自动、排除关系的
产品写入口"留给 P3-04，但 `person_operations.type`、`person_operation_items.entity_type`、API 形状与
撤销语义都还没有冻结，因此下一步是先冻结规则（A 级：契约测试 + ruff + diff-check），再由 **P3-04B**
交 **DS-H** 实现并通过 **B + D**（新增可导入模块或 migration package-data 时加 C）。

**下一任务建议使用 C2（GPT-5.6 Sol / high），请在 Codex 中手动切换模型/推理档后继续。**
