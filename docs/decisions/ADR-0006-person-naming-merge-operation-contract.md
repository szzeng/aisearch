# ADR-0006: 人物命名、合并、重定向与补偿撤销契约

- 状态：Accepted（P3-03 C2 规则冻结）
- 日期：2026-09-14
- 适用实现：P3-03
- 后续扩展：P3-04 单脸纠错；P3-05 拆分及通用人物操作扩展

## 1. 决策范围与阶段边界

本 ADR 冻结 P3-03 的显示名、`name_key`、同名/显式合并、旧 ID 重定向、P3-03 操作记录与撤销、
API 以及权威/派生投影顺序。它不改变 P3-02 的 `face_id`、检测产物、向量空间、阈值、候选索引世代或
`face_person_links` 的“每脸至多一个 `CONFIRMED`”语义。

P3-03 只实现 `RENAME`、`MERGE_BY_NAME`、`MERGE`、`BATCH_MERGE` 及这些操作的 `UNDO`。单脸取消、
手动指定、恢复自动、排除关系的产品写入口属于 P3-04；拆分及跨类型通用撤销扩展属于 P3-05。P3-03 的
迁移先建立 `face_assignment_controls`、`face_exclusions`，使 P3-02 已有人工优先读取路径立即生效，但
P3-03 不提供修改它们的公共 API。

## 2. 名称与版本化 `name_key`

固定 `NAME_KEY_VERSION = 1`、前缀 `nfc1:`。保存顺序为：输入必须是字符串；执行 Unicode NFC；使用
Unicode whitespace 分词，去首尾空白并把连续空白合为一个 ASCII 空格；结果为空返回
`PERSON_NAME_INVALID` 且零写入。`persons.name` 保存规范后的显示文本，`persons.name_key` 保存
`nfc1:` 加该文本。

英文大小写保留并参与比较；不做 casefold、NFKC、拼音、繁简、同义词、别名或模糊匹配。示例：
`" 爸爸\t " → name="爸爸", name_key="nfc1:爸爸"`；`"A  B" → "A B"`；`"Alice"` 与
`"alice"` 不同。`name IS NULL/name_key IS NULL` 才是未命名；界面占位文字“未命名人物 001”永不写库、
永不参与匹配。

沿用 0010 的部分唯一索引：仅 `merged_into IS NULL AND name_key IS NOT NULL` 的活动人物唯一。同名解析
只发生在用户提交命名时；后台关联、相似名字、相同别名和查询均不得触发合并。`person_aliases` 永不作为
自动合并键。

## 3. 0011 迁移形状

P3-03 只允许新增 `0011_person_knowledge.sql`，不得修改 0001～0010。`persons` 与
`face_person_links` 已能承载本任务，不加列、不改主键/唯一索引。0011 新增：

- `person_aliases(alias_id INTEGER PRIMARY KEY, person_id REFERENCES persons RESTRICT, alias,
  normalized_alias, last_operation_id, created_at)`；`UNIQUE(person_id, normalized_alias)`，但不同人物可有
  相同别名。`normalized_alias` 使用规范后的显示文本，不带 `nfc1:`。
- `face_assignment_controls(face_id PRIMARY KEY REFERENCES faces RESTRICT, mode CHECK IN
  ('AUTO_ALLOWED','MANUAL_ASSIGNED','MANUAL_UNASSIGNED'), revision >= 1, last_operation_id, updated_at)`。
- `face_exclusions(face_id REFERENCES faces RESTRICT, person_id REFERENCES persons RESTRICT,
  operation_id, active IN (0,1), created_at, PRIMARY KEY(face_id, person_id))`。
- `person_operations(operation_id TEXT PRIMARY KEY, request_id TEXT UNIQUE, payload_sha256,
  type CHECK IN ('RENAME','MERGE_BY_NAME','MERGE','BATCH_MERGE','UNDO'), source_person_ids_json
  CHECK json_valid, target_person_id, state CHECK IN ('COMMITTED','UNDONE'), operation_revision UNIQUE,
  undo_of_operation_id, undone_by, catalog_filter_revision, created_at, committed_at)`。
- `person_operation_items(operation_id REFERENCES person_operations RESTRICT, item_index,
  entity_type CHECK(entity_type='PERSON_BUNDLE'), entity_id, before_value_json, after_value_json,
  after_revision, PRIMARY KEY(operation_id,item_index))`；两个快照列必须为 NULL 或合法 JSON。

0011 按 0009 的已验收方式重建 `search_projection_queue`，原样保留现有五种 cause，再加
`PERSON_CHANGED`；不得重写或复制 0006/0009 的触发器。迁移前只读预检、备份、单事务推进、缺件/过新
拒写继续由 ADR-0002 与现有迁移器负责。

P3-04 兼容是硬约束：现有 P3-02 人工优先测试只显式写架构必需列，不得给上述人工表增加无默认的额外
`NOT NULL` 列。

## 4. Canonical person ID

`resolve_canonical_person_id(connection, person_id)` 是所有人物读取、命名、合并、排除判定和代表查询的唯一
入口。它逐行跟随 `merged_into` 直到活动人物；不存在返回 `PERSON_NOT_FOUND`；访问到重复 ID 或步数超过
当前 `persons` 行数返回 `PERSON_REDIRECT_CYCLE`，不得修表或猜测目标。

旧 ID 查询返回 `requested_person_id`、`canonical_person_id` 与 `redirected`，不发 HTTP 3xx。显式合并的
目标和每个源先 canonicalize，再去重；源解析到目标时是 no-op。P3-07 的 `all` 人物过滤必须按 canonical
ID 去重。

## 5. 唯一业务用例、API 与并发

API 输入固定为：

- `PUT /api/v1/persons/{person_id}/name`：`request_id`、`name`、`expected_revision`。
- `POST /api/v1/persons/merge`：`request_id`、`target_person_id`、`target_expected_revision`、
  `sources[{person_id, expected_revision}]`；1～500 个源，重复源拒绝。
- `GET /api/v1/persons/{person_id}`：canonical 人物、别名、`face_count` 与按 `asset_id` 去重的
  `asset_count`。
- `POST /api/v1/person-operations/{operation_id}/undo`：`request_id`、
  `expected_operation_revision`。

所有写入口沿用既有 Host/Origin/session/double-submit CSRF 边界。响应和错误不得含路径、bbox、landmark、
向量、单对相似度或坐标。

命名和显式合并必须进入同一个内部 `merge_persons(...)` 用例。命名事务先解析 canonical ID 并检查 revision：
没有活动同名目标则原 ID 改名；目标是自己且显示值相同则 no-op；目标是别人则以 `MERGE_BY_NAME` 调用同一
合并函数。显式单/批量合并直接调用该函数，API 不得循环调用命名或单源合并。

使用现有 `Catalog.write()` 的 `BEGIN IMMEDIATE`。活动同名查询、revision 检查、操作落库、权威关系修改和
派生队列预约都在同一事务。另一个进程获得写锁后必须重新解析当前 canonical/同名目标，不能依赖事务外预读；
唯一索引竞争映射为同一业务结果或结构化冲突，不得成为 500。revision 不匹配返回
`PERSON_REVISION_CONFLICT` 并列出 person ID、期望值、当前值，整个批次零写入。

## 6. 合并数据规则

合并保留 canonical 目标 ID。每个源只设置 `merged_into=target` 并递增 revision，绝不删除；目标只递增
一次 revision。目标已有 `name` 与 `avatar_face_id` 默认保留。目标没有头像时，可从合并后的有效成员中按
当前 face processor/space 内 `quality_state='OK'`、`face_pixels DESC, face_id ASC` 选一个；不得跨
`space_id`，不得重算向量。

源正式旧名和源别名移到目标别名集合，按 `(target_person_id, normalized_alias)` 去重；目标当前正式名不得
同时作为自身别名。别名碰撞只在目标内去重，不跨人物拒绝。

链接先在内存中按 face 汇总，再一次写回，避免主键/部分唯一索引的中间冲突：

- `person_id` 改为 canonical 目标；`assignment_kind`、`confidence` 的来源含义不因批量合并升级。
- 相同 `(face_id,target,assignment_kind)` 冲突时，`CONFIRMED` 优先于 `SUGGESTED`；同 decision 取 revision
  较新的原行。不同 assignment_kind 可保留为不同建议，但仍只能有一个 `CONFIRMED`。
- 若该脸是 `MANUAL_UNASSIGNED`，迁入的 `CONFIRMED` 降为 `SUGGESTED`，reason 固定
  `MANUAL_UNASSIGNED`。
- 若该脸对目标存在活动排除（排除中的 person ID 也 canonicalize），迁入的 `CONFIRMED` 降为
  `SUGGESTED`，reason 固定 `HUMAN_CONFLICT`；人工来源仍保留 `assignment_kind='MANUAL'`，排除行不删除。
- 上述降级脸计入 `pending_confirmation`，不得成为代表或自动应用；不扩展 P3-02 的 decision 枚举。
- 人物成员只数无活动排除、非 `MANUAL_UNASSIGNED` 的 `CONFIRMED` face；照片数对此结果
  `COUNT(DISTINCT asset_id)`。

代表和候选读取必须解析 canonical person，并排除 `MANUAL_UNASSIGNED`/活动排除；不能只改人物表而让旧
候选继续代表被合并或被排除的人物。

## 7. 幂等、快照与补偿撤销

`request_id` 沿用本地写 API 的 1～64 个可打印 ASCII。服务对“客户端意图”做规范化 canonical JSON 后
SHA-256；显式 sources 以请求 person ID 排序，但不使用会随合并变化的 canonical ID 计算摘要。
`operation_id = sha256("person-operation\0" + request_id)` 的十六进制值。

事务第一步按 request_id 查已有操作：摘要相同返回原结果并标记 `replayed=true`；摘要不同返回
`PERSON_IDEMPOTENCY_CONFLICT`，且必须早于人物/revision 检查，使重放不受后来状态影响。新 request 的
语义 no-op 仍写一条 `COMMITTED` 操作，但不递增人物/filter revision、不写 outbox。

每个受影响源与目标各写一个 `PERSON_BUNDLE` 项。两个快照是排序 canonical JSON，包含 person 的
`name/name_key/avatar_face_id/merged_into/revision`、别名集合及 `face_person_links` 集合；不复制向量、
bbox、位置或整库。链接快照包含 person/face、assignment_kind、decision、confidence、reason、revision、
last_operation_id；比较忽略 created/updated 时间，但不得忽略这些语义与 revision。

撤销自身是新 `UNDO` 操作。先做 request_id 重放；再校验原操作存在、是 P3-03 可撤销类型、
`state=COMMITTED`、`undone_by IS NULL` 且 operation_revision 符合；最后逐项比较 live bundle 与 after
快照。任一不符返回 `PERSON_OPERATION_NOT_UNDOABLE`，details 只列 `entity_type/entity_id/reason`，全事务
零写入。

无冲突时恢复 before 的语义值，但所有恢复的 person/link revision 都从当前值递增，绝不倒退，且
`last_operation_id` 指向 UNDO。原操作改为 `UNDONE` 并写 `undone_by`。UNDO 也保存 before/after bundle，
但首版不允许 undo an UNDO。合并后新增成员导致快照不等，必须拒绝整次撤销，不能猜测归属。

## 8. 权威提交与派生追赶

changed 操作在同一 SQLite 事务中按以下顺序完成：

1. 写 person/alias/link/redirect 与操作项权威状态。
2. 收集变更前后所有受影响 face 和 `DISTINCT asset_id`。
3. `catalog_filter_revision` 只递增一次，cause=`PERSON_CHANGED`；通过现有 projection refresher 为已有图片
   向量预约 P2-03 outbox。
4. 对每个受影响资产 upsert `search_projection_queue(...,'PERSON_CHANGED',now_ms)`。
5. 对每个受影响 face 调用现有 `queue_candidate()`：entry 先变 `PENDING_REPAIR`，outbox 后台重放确认；
   不直接写 CONFIRMED，不重算向量。
6. 写操作的最终 `catalog_filter_revision`，提交事务。

权威人物查询立即读 SQLite；派生索引滞后不得覆盖或漏掉权威人工结果。no-op 不执行第 2～5 步。

## 9. 只读状态与错误码

`face_summary` 加计数：`named_persons`、`unnamed_persons`、`aliases`、`pending_confirmation`（reason 为
`HUMAN_CONFLICT`/`MANUAL_UNASSIGNED` 的 distinct face）、`recent_operations`（最近 20 条 operation 的数量，
0～20）。不得输出名称或 alias 文本。

冻结错误码及 HTTP 映射：`PERSON_NOT_FOUND` → 404；`PERSON_NAME_INVALID`、
`PERSON_BATCH_TOO_LARGE` → 422；`PERSON_REVISION_CONFLICT`、`PERSON_IDEMPOTENCY_CONFLICT`、
`PERSON_REDIRECT_CYCLE`、`PERSON_OPERATION_NOT_UNDOABLE` → 409。未知数据库异常仍由现有结构化 500 与
脱敏器处理。

## 10. DS-H 实现边界与验收

DS-H 可新增 `persons/**`、0011、人物 API models/routes、人物错误码与 face summary；只在 canonical/排除/
代表读取和 `queue_candidate()` 复用所必需时加性修改 `face/index.py` 或 `face/service.py`。不得修改
ADR-0002/0003/0005、本 ADR、冻结 JSON、0001～0010、P3-02 产物/face_id/阈值/space、places/geo/OCR/
ingestion/前端、worker、打包入口或依赖。

至少覆盖任务卡已有 D 场景，并增加：NFC/空白/英文大小写；别名不触发合并；旧 ID 多跳与环；同图多脸按
asset 去重；目标排除/`MANUAL_UNASSIGNED` 降级待确认；链接来源不升级；合并后新增成员导致 undo 整体拒绝；
no-op 不推进任何 revision/outbox。因新增可导入模块和 migration package-data，最终验收必须执行
B + D + C；C 使用全新 D 盘工作目录，并复核 bundle 的 0001～0011 与源码 0011 SHA-256 一致。
