# ADR-0008: 人物拆分、混合快照与补偿撤销契约

- 状态：Accepted（P3-05A C2 规则冻结）
- 日期：2026-09-20
- 适用实现：P3-05B
- 上游：ADR-0006、ADR-0007、主架构 §4.2 / §8.3.3～§8.3.5

## 1. 决策范围与阶段边界

本 ADR 冻结“从一个人物分组拆出若干明确 `face_id`，创建一个新的未命名人物”的唯一服务语义，包含
身份、成员、人工控制、排除、幂等、revision、操作快照、补偿撤销、API、错误和权威到派生的提交顺序。
机器可读伴随物是 `contracts/p3-person-split-contracts.v1.json`；两者冲突时以本 ADR 为准。

P3-05A 只冻结规则，不实现生产服务、API 或 migration，也不声称数据库已执行这些拒绝。P3-05B 才能按
本 ADR 新增 `0013`、人物拆分服务、API 与故障测试。前端选择器属于 P3-06；模型重新检测后的新旧
`face_id` 映射属于 P3-08。拆分不得复用、重写或重新生成检测 `face_id`，不得改变人脸检测、阈值、
embedding space、候选 generation 或图片向量。

## 2. 唯一拆分对象、路径与输入

唯一写路径为：

```text
POST /api/v1/persons/{source_person_id}/split
```

路径中的 `source_person_id` 是用户请求的 ID。服务在同一个 `BEGIN IMMEDIATE` 事务中用 ADR-0006 的唯一
resolver 解析到活动 canonical source；不存在为 `PERSON_NOT_FOUND`，悬挂或环为
`PERSON_REDIRECT_CYCLE`。旧 ID 可以提交，响应同时返回 requested/canonical source ID，不发 3xx。

请求 body 固定为：

```json
{
  "contract_version": "p3-person-split-contracts.v1",
  "request_id": "split-1",
  "source_expected_revision": 7,
  "faces": [
    {
      "face_id": "face-001",
      "expected_control_revision": 0,
      "expected_assignment_revision": 3
    }
  ]
}
```

`faces` 必须包含 1～500 个明确、互不重复的 `face_id`；服务端不得按 asset/person 扩张选择。空集合、重复
face 是 `VALIDATION_ERROR`，501 项是 `PERSON_BATCH_TOO_LARGE`，全部零写。每项必须同时提交 logical
control revision 与当前唯一 `CONFIRMED` link revision；两者分别防止人工控制和成员关系在用户确认后漂移。

## 3. 资格、剩余成员与新人物身份

shape 校验和 payload hash 完成后，`request_id` 幂等门必须早于 source/face/revision 检查。新请求随后在
任何写入前一次性验证：

1. canonical source revision 等于 `source_expected_revision`，否则 `PERSON_REVISION_CONFLICT`；
2. 每个 face 存在，否则 `FACE_NOT_FOUND`；
3. logical control revision 等于 `expected_control_revision`，否则 `FACE_ASSIGNMENT_CONFLICT`；
4. face 恰有一个 `CONFIRMED` link，link revision 等于 `expected_assignment_revision`，且 person
   canonicalize 后就是 source；
5. face 不是 `MANUAL_UNASSIGNED`，也不存在对 canonical source 的活动 exclusion；多重 effective link、
   排除与有效归属并存等不可描述状态同样拒绝；
6. 去掉所选 face 后，source 至少保留一个符合相同有效成员口径的未选 `CONFIRMED` face。

第 4～6 项失败统一为 `PERSON_SPLIT_CONFLICT`，安全 `details.reason` 固定为
`face_not_confirmed`、`face_owned_by_other_person`、`assignment_revision`、`face_manual_unassigned`、
`face_state_inconsistent` 或 `source_would_be_empty`。建议 face、无归属 face、来自其他人物的 face、选择
source 的全部有效成员都不是 no-op，也不得由实现猜测；它们按上述闭集拒绝。拆分没有语义 no-op：一个
非 replay 的成功请求必定创建人物并改变全部所选 face。

全部校验通过后，仍在同一事务内先写 operation 占位行（`target_person_id=NULL`），再创建一个新人物并把
其 ID 写回 operation。新人物固定为：`name/name_key=NULL`、aliases 为空、`merged_into=NULL`、活动、
revision 1；头像只从移动后的有效成员按 ADR-0006 的同 processor/space、质量优先规则选择。不得复制 source
的名字、别名或头像，从而不会因同名唯一索引立即把拆分抵消。ID 只在事务内生成；之后任一步失败时，人物、
operation 和所有关系一起回滚，ID 不得出现在成功响应或历史中。

source 保留原 name/name_key/aliases/merged_into，revision 恰好 +1。若原头像是被移动 face，按剩余有效成员
在同一 space 重选；否则保留。source 没有可用代表时头像可为空。新人物 revision 不因初始头像再加一次。

## 4. 所选脸、人工控制、排除与同图边界

拆分是明确的人工作废/指定组合。对每个所选 face，按 ADR-0007 `assign B` 的同一矩阵执行，其中 B 是新人物：

- 原唯一有效 link 结束；AUTO link 可降为 `SUGGESTED/HUMAN_CONFLICT`，MANUAL link 可删除，检测与 embedding
  永不删除；
- 建立唯一 `MANUAL/CONFIRMED` 新人物 link；control 固定为 `MANUAL_ASSIGNED`，logical revision
  `current + 1`；link revision 必须高于被替换行和既有同 key 行；
- 激活对 canonical source 的 exclusion；对其他人物的全部 active/inactive exclusion 原样保留；新人物是
  本事务刚创建的 ID，不可能有旧 exclusion，不得清空其他 exclusion；
- 每脸仍至多一个 `CONFIRMED`。同图未选择的 siblings、其他人物、检测行、embedding 与图片本身全部不变。

输入允许原有效 link 为 `AUTO/CONFIRMED` 或 `MANUAL/CONFIRMED`；两者输出都成为对新人物的人工确认。
`SUGGESTED` 永远不被拆分提升，`MANUAL_UNASSIGNED` 不能借 split 绕过恢复/指定流程。

## 5. 操作权威、幂等与 0013 目标形状

继续使用全局唯一的 `person_operations.request_id`，不建立第二套 split 日志。operation type 新增唯一值
`SPLIT`；`source_person_ids_json` 保存升序后的**请求 source ID** 单元素数组，`target_person_id` 保存新人物
ID。`operation_id` 继续是 `sha256("person-operation\0" + request_id)`。

payload digest 是 UTF-8 canonical JSON，固定包含 contract version、endpoint family `persons.split`、
请求 source ID、source expected revision，以及按 `face_id` 排序的 face/revision 三元组。它使用请求 ID，
不用会随 redirect 漂移的 canonical ID。同 request_id + 同 digest 在任何 revision 或实体存在性检查之前
返回原 operation/result 且 `replayed=true`；同 key 异 digest 为 `PERSON_IDEMPOTENCY_CONFLICT`，不回显
payload/hash 并零写。被拒请求不占用 request_id。

现有 `PERSON_BUNDLE` 会复制所有 suggestion link，且不能表示“新人物在操作前不存在、撤销后保留稳定 ID”。
因此 `PERSON_BUNDLE + FACE_CONTROL` 不足够；0013 必须新增第三种 item type `SPLIT_GROUP`，但不加第二张
operation 表：

- `SPLIT_GROUP` 使用正整数 `entity_id`、`entity_key=NULL`；`FACE_CONTROL` 继续使用 `entity_key=face_id`、
  `entity_id=NULL`；形状互斥；
- split operation 的 item 顺序固定：source `SPLIT_GROUP` index 0，created `SPLIT_GROUP` index 1，随后
  `FACE_CONTROL` 按 `face_id` 升序；
- source snapshot 仅含 role、person 的 name/name_key/avatar/merged_into/revision、aliases，以及排序后的
  **有效 CONFIRMED member**（face_id、person_id、assignment_kind、link revision/last_operation_id）；
  suggestion 不进入 group conflict fingerprint；
- created before snapshot 固定为 `{role: CREATED, exists: false, person_id: new_id}`，after 使用同一最小 group
  形状且 `exists: true`；它表达事务前没有该 ID，而不是伪造一个空活动人物；
- 每个所选 face 复用 ADR-0007 的完整 `FACE_CONTROL` before/after（control、唯一有效 assignment、全部
  exclusion），不复制 bbox、landmark、向量、路径或相似度。

0013 只因 0012 的 operation type 和 item type 是闭合 CHECK 才安全重建 `person_operations` /
`person_operation_items`，逐列保留所有 P3-03/P3-04 operation、item、UNDO、alias 引用与 operation revision；
`0001`～`0012` 逐字节不变，不新增事实表，不重建 `search_projection_queue`。迁移形状与无损顺序必须由
P3-05B 的真实 v12→v13 夹具证明。本 ADR 不修改 migration。

## 6. 成功响应、replay 与隐私闭集

成功、replay 和 split-family undo 都返回 `p3-person-split-contracts.v1` 的同一 envelope：operation id/type/
revision/status、undo_of、requested/canonical source ID、new person ID、changed、catalog filter revision、
replayed 与 items。`SPLIT` 和其 `UNDO` 的 `changed` 恒为 true；items 含恰好两个 group item 和 1～500 个
face item。group item 只返回 entity ID、role、revision、merged_into；face item 沿用安全的 mode/revision/
assigned person/exclusion changes，不返回内部快照。

响应、错误、日志与普通诊断不得含本地路径、name/name_key/alias 文本、bbox、landmark、向量、confidence、
单对相似度、原始幂等 payload/hash 或内部 before/after snapshot。API 沿用 session、Host/Origin 与
double-submit CSRF；伴随物的所有公共 object 都是 `additionalProperties: false`。

## 7. 补偿撤销与跨类型冲突

继续使用唯一端点：

```text
POST /api/v1/person-operations/{operation_id}/undo
```

请求仍为 `request_id + expected_operation_revision`。先执行全局 request_id 幂等门；再要求原 operation
存在、type=`SPLIT`、`state=COMMITTED`、未撤销且 operation revision 匹配。`UNDO` 是新 operation，继承
split family envelope；不允许 undo an UNDO。失败全部零写。

撤销前必须比较 source/created 的 live `SPLIT_GROUP` 与原 after snapshot，以及每个所选 face 的 live
`FACE_CONTROL` 与原 after snapshot。以下任何一项使整批 `PERSON_OPERATION_NOT_UNDOABLE`：

| 后续事实 | 结论 / 原因 |
|---|---|
| source 或 created 被 rename、改 alias、改头像、revision 前进 | `group_changed` |
| source 或 created 被 merge、redirect 链改变、人物行消失 | `group_redirected` / `person_missing` |
| 任一 selected face 被 assign/unassign/restore_auto，或 assignment/exclusion revision 改变 | `face_control_changed` |
| 任一 selected face 消失 | `face_missing` |
| 第二次 split 改变任一组或 selected face | 相应 group/face 冲突 |
| 自动关联给 source/created 新增或移除一个 CONFIRMED member | `effective_members_changed` |
| 仅新增/更新 SUGGESTED link，且未改变 selected FACE_CONTROL/effective member | 不冲突，保留原状 |
| 不相关人物或不属于两组的 face 变化 | 不冲突 |

redirect resolver 遇环仍返回 `PERSON_REDIRECT_CYCLE`，坏/过新 schema 沿用 `SCHEMA_INCONSISTENT` /
`SCHEMA_TOO_NEW` 的启动/写拒绝；不得把损坏状态模糊恢复到猜测人物。

无冲突时，撤销按以下补偿语义执行，而不是删除历史或倒退 revision：

1. 每个 selected face 恢复原 FACE_CONTROL 的 mode、有效 assignment 与 exclusion 语义；control/link revision
   从 live 与记录值的最大值继续 +1，所有 exclusion 行保留、只切 active 状态；
2. source 恢复 split 前的头像和有效成员，person revision 从 live +1；name/aliases 因冲突门保证未变；
3. created 不删除：清空其初始头像，保持 name/name_key NULL、aliases 空，把 `merged_into` 指向 source，
   revision 从 live +1。其稳定 ID 此后按 ADR-0006 解析回 source；
4. 原 SPLIT 标记 UNDONE 并指向新 UNDO；UNDO 保存自己执行前后的两个 `SPLIT_GROUP` 和全部
   `FACE_CONTROL` item，item 顺序不变。

这保证撤销不会回滚后续人工整理：任何相关后续整理都先被冲突门拒绝；未发生后续整理时才做无歧义补偿。

## 8. 权威到派生的提交顺序

一个 changed split/undo 在同一 SQLite 事务内按固定顺序：

1. 幂等门与全量预检；写 operation 占位；split 创建新人物并补 target；
2. 写 source/created 人物事实、所选 face link/control/exclusion 与全部 operation items；
3. 收集所选 face 与其 `DISTINCT asset_id`，全 operation 的 `catalog_filter_revision` **恰好 +1**，cause
   复用 `PERSON_CHANGED`；
4. 每个受影响资产 upsert `search_projection_queue(..., PERSON_CHANGED, now_ms)`；
5. 对当前 face generation 已发布的所选 face 调用 `queue_candidate()`，先置 `PENDING_REPAIR` 后重放；
6. 失效 source/created 的人物计数/头像缓存（若实现存在该 cache），写回 operation filter revision并提交。

提交后 SQLite 权威查询立即显示新人物/成员或撤销后的 redirect；派生滞后不得漏召回或显示旧成员。
replay 零写、零 revision、零队列副作用；split 没有业务 no-op。事务内不得做模型、文件 I/O 或 Lance 写入。
拆分与撤销不得重新编码图片、写图片向量 `index_outbox`、修改 face embedding 或切换 generation。

## 9. 读取口径、计数与错误映射

拆分后人物列表出现一个未命名活动人物；source 保持原显示名。成员/照片数继续使用 ADR-0006 的有效
`CONFIRMED` + control/exclusion 口径，照片按 asset_id 去重。撤销后 created ID 作为旧 ID canonicalize 到
source。`face_summary` 现有 named/unnamed/manual/exclusion/recent-operation 计数自然反映事实，不新增计数，
不改变其字段或隐私边界。

冻结 API 错误闭集：

| code | HTTP | 语义 |
|---|---:|---|
| `PERSON_NOT_FOUND` | 404 | source/operation/person 不存在 |
| `FACE_NOT_FOUND` | 404 | 请求 face 不存在 |
| `PERSON_REVISION_CONFLICT` | 409 | source revision 过期 |
| `FACE_ASSIGNMENT_CONFLICT` | 409 | logical control revision 过期 |
| `PERSON_SPLIT_CONFLICT` | 409 | 成员/link revision/资格/剩余成员冲突 |
| `PERSON_IDEMPOTENCY_CONFLICT` | 409 | request_id 已绑定不同 payload |
| `PERSON_REDIRECT_CYCLE` | 409 | source/关联人物 canonical 链损坏 |
| `PERSON_OPERATION_NOT_UNDOABLE` | 409 | split 状态或 after snapshot 已变化 |
| `SCHEMA_INCONSISTENT` / `SCHEMA_TOO_NEW` | 409 | catalog 不能安全执行写入 |
| `PERSON_BATCH_TOO_LARGE` | 422 | face 项超过 500 |
| `VALIDATION_ERROR` | 422 | contract/version/ID/request shape、空/重复 face 非法 |

冲突 details 只允许安全 ID、expected/current revision、固定 reason 和
`entity_type/entity_id/face_id`；不得回显姓名、快照或个人媒体事实。

## 10. P3-05B 实现边界与必测反例

P3-05B 可新增 `0013_person_split_operations.sql` 与人物 split 模块，并加性修改 persons service/contracts/
operations、API models/routes/errors、schema 版本、测试和专用 `%TEMP%` 诊断夹具。不得修改本 ADR、companion、
`0001`～`0012`、依赖、前端、检测/向量/阈值、模型权重、places/geo/OCR/ingestion/worker 或图片搜索协议。

至少以 B + D 验证：

1. 同 request_id 同 payload 在 source/face revision 变化后 replay；异 payload 零写冲突；
2. source/control/assignment revision 任一 stale 时 operation/person/face/filter/queue 全零写；
3. 空、重复、501、跨人物、suggestion、无归属、MANUAL_UNASSIGNED、消失 face 与拆空 source 的闭合错误；
4. 新人物未命名、无 alias、未复制名字；source 保留 sibling/名字，移动 face 全为人工确认；
5. 原 control 与 canonical exclusions 在 split/undo 往返后语义不丢、revision 只向前；
6. 后续 rename、merge、face edit、第二次 split、新 effective member 分别阻止旧 undo；suggestion-only 与
   不相关变化不阻止；
7. undo an UNDO、重复 undo、人物/face 消失、redirect 环与损坏 0013 均零写拒绝；
8. operation/item、两组、全部 face、filter revision、搜索队列、face repair 同事务全成或全回滚；
9. split/undo 不写图片 `index_outbox`，不改 face embedding/space/generation；
10. API session/CSRF/Origin/Host、closed response/error schema、OpenAPI 唯一路径和隐私扫描通过；
11. v12→v13 含 P3-03/P3-04 operation/item/UNDO/alias 引用逐列保留，0012 以前逐字节不变；
12. 重启后新人物、人工锁、排除、operation 与 undo 可复现，且旧 created ID 在 undo 后解析到 source。

新增生产模块和 migration package-data 时，P3-05B 按任务卡决定是否补 C；随后必须生成独立 C2 审查卡。
