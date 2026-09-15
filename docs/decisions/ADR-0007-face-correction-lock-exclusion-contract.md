# ADR-0007: 单脸纠错、人工锁、排除与补偿撤销契约

- 状态：Accepted（P3-04A C2 规则冻结）
- 日期：2026-09-15
- 适用实现：P3-04B
- 上游：ADR-0006、主架构 §4.2 / §8.3.4 / §8.3.5

## 1. 决策范围与阶段边界

本 ADR 冻结以 `face_id` 为最小单位的取消归类、手动指定、恢复自动、批量、人工排除、幂等、
revision、补偿撤销、API、只读计数以及权威到派生的提交顺序。机器可读伴随物是
`contracts/p3-face-correction-contracts.v1.json`；两者冲突时以本 ADR 为准。

P3-04A 不实现生产行为，不修改 migration，也不声称数据库或 API 已经执行这些拒绝。P3-04B 才能按本
ADR 新增 `0012`、服务与 API。人物拆分、新旧检测 `face_id` 的可审查映射、前端脸框/待整理页面分别留给
P3-05、P3-08、P3-06。不得借本任务改变 P3-02 的检测产物、阈值、向量空间、候选索引世代，或
`face_person_links` 的“每脸至多一个 `CONFIRMED`”约束。

## 2. 操作词汇与同一操作权威

继续使用 ADR-0006 的 `person_operations` / `person_operation_items` 作为人物人工知识的唯一操作日志，
不建立第二套 face operation authority。`0012` 必须以复制校验、重建、回填、替换的方式扩展 0011 的闭合
`CHECK`：

- `person_operations.type` 增加 `FACE_ASSIGN`、`FACE_UNASSIGN`、`FACE_RESTORE_AUTO`、
  `BATCH_FACE_EDIT`；`UNDO` 继续是所有可撤销操作的共同补偿类型。
- 单脸端点的 type 由 action 唯一决定；批量端点即使只有一项也固定为 `BATCH_FACE_EDIT`。type 描述请求
  入口，不因 canonicalize、no-op 或批量实际变化数而漂移。
- `person_operation_items.entity_type` 增加 `FACE_CONTROL`。原 `PERSON_BUNDLE` 行继续使用正整数
  `entity_id`；`FACE_CONTROL` 行使用新增的非空 `entity_key = face_id` 且 `entity_id IS NULL`。两种形状
  互斥。P3-03 读写 `PERSON_BUNDLE` 的语义保持不变。
- 脸级操作的 `source_person_ids_json` 固定为 `[]`；单一 FACE_ASSIGN 可把 canonical target 写入
  `target_person_id`，取消、恢复自动及 mixed batch 写 NULL。真正的逐脸目标以 item 快照为准。

`FACE_CONTROL` 的 before/after 快照是 canonical JSON，至少包含：`face_id`；逻辑 control
（`mode/revision/last_operation_id/updated_at`）；当前唯一有效关联或 NULL
（`person_id/assignment_kind/decision/reason/revision/last_operation_id/created_at/updated_at`）；以及按
canonical `person_id` 排序的全部 exclusion 状态
（`person_id/active/operation_id/created_at`）。内部审计快照可保留恢复所需字段，但这些快照不得原样通过
API 返回，API 永不返回 confidence、bbox、landmark、向量或路径。

## 3. 逻辑控制 revision 与模式迁移

`expected_revision` 指向 `face_assignment_controls` 的**逻辑脸级 revision**，不是 `faces.face_revision`、
链接 revision 或 person revision。没有控制行等价于 `AUTO_ALLOWED, revision=0`。首次实际变更插入 revision
1；此后每个实际改变该脸权威 bundle 的人工操作恰好 `current + 1`。语义 no-op 仍写一条 COMMITTED
operation 供幂等重放，但不建/改控制行、不改链接/排除、不推进任何 revision 或队列。

| 当前逻辑状态 | action | 操作后状态 | 必要行为 |
|---|---|---|---|
| 任意 | `unassign` | `MANUAL_UNASSIGNED` | 结束唯一有效关联；若原来有 A，激活对 canonical A 的排除 |
| 任意 | `assign B` | `MANUAL_ASSIGNED` | 建立唯一 `MANUAL/CONFIRMED` B；若原来有 A≠B，激活 A 排除；解除 B 排除；其他排除保留 |
| `MANUAL_ASSIGNED` | `assign` 同一 canonical B | 不变 | 若 B 已是唯一有效人工关联且 B 未排除，则 no-op；否则修正 bundle 后 revision +1 |
| `MANUAL_UNASSIGNED` / `MANUAL_ASSIGNED` | `restore_auto` | `AUTO_ALLOWED` | 解除人工锁；移除当前 `MANUAL/CONFIRMED` 有效关联；全部排除保留；不采用建议 |
| `AUTO_ALLOWED` / 无控制行 | `restore_auto` | 不变 | no-op；既有自动有效关联不因重复 restore 被删除 |

“结束有效关联”不得删除 face、检测事实或 embedding。被替换的 `AUTO/CONFIRMED` 可降为
`SUGGESTED/HUMAN_CONFLICT`，被取消的有效链接可降为 `SUGGESTED/MANUAL_UNASSIGNED`；已有非有效建议
保持机器事实。实现可以删除被替换的旧 `MANUAL/CONFIRMED` 行，恢复所需前值由操作快照保护。无论采用
降级还是删除，操作后都必须满足：至多一个 CONFIRMED；`MANUAL_UNASSIGNED` 没有 CONFIRMED；
`MANUAL_ASSIGNED` 恰有一个 `MANUAL/CONFIRMED`；一次操作不得把其他脸的任何行带入修改范围。

所有 person_id 在判断相等、激活/解除排除及建立归属前先按 ADR-0006 canonicalize。客户端提交的旧 ID
可以解析到活动目标，响应返回 canonical ID；重定向环/悬挂按既有 `PERSON_REDIRECT_CYCLE` /
`PERSON_NOT_FOUND` 拒绝。把脸明确指定回此前排除的 A 是合法更正，只解除 A 的排除，不清除其他排除。

## 4. 排除、建议与自动提交边界

`face_exclusions` 是持久人工否定。激活或解除沿用 `(face_id, person_id)` 一行 upsert，`active` 表示当前
语义；不得通过删行假装历史从未发生。合并后判断排除时按 canonical person 等价类生效，保留原行供审计。

自动路径每次提交必须先读取控制与活动排除，再在同一权威写事务里比较它领取/计算时观察到的 logical
control revision：

- `MANUAL_ASSIGNED`、`MANUAL_UNASSIGNED` 永远不得被自动链接替换；
- `AUTO_ALLOWED` 仍必须拒绝命中活动排除的人物；
- revision 已变化的晚到结果返回稳定冲突并零写权威关系；机器产物可以留存，不能变成有效人工事实；
- 自动建议可以显示，但始终是 `SUGGESTED`，不能绕过人工锁自动应用。

`human_controlled_face_ids()` 与 `blocked_face_links()` 的现有读取顺序继续成立：人工事务先写控制/排除
权威事实，自动路径随后读取时即被挡住。`未命名人物` 是没有 name 的 person group；`待手动归类` 是
`MANUAL_UNASSIGNED` 的 face 集合，两者不可共用状态或计数。合照只按请求里明确列出的 face_id 修改；
同图中未列出的妈妈、孩子或第二张同人物脸全部不变。

## 5. 单项、批量与幂等载荷

单项请求在路径中给一个 face_id。批量为 1～500 个明确且去重的 face item，每项带自己的 action、
`expected_revision` 和 action 所需 target。服务端先完成全批 shape、face、person、canonical target、
revision 与状态校验，再用一个 `BEGIN IMMEDIATE` 短事务全部提交；重复 face、501 项、任一不存在/冲突或
事务中途故障都整体零写入。“按照片全选”必须先在客户端冻结为 face_id 列表，服务端不得按 asset_id
扩张范围。

`request_id` 是整个 `person_operations` 表中的全局幂等键，1～64 个可打印 ASCII。payload hash 使用
UTF-8 canonical JSON（对象 key 排序、无多余空白）：单项含 endpoint family、请求 face_id、action、请求
target_person_id/NULL、expected_revision；批量 items 先按 face_id 排序，保留每项 requested target。
同 request_id + 同 hash 在任何 revision 检查之前返回原 operation/result，`replayed=true`；同 key +
不同 hash 返回 `FACE_IDEMPOTENCY_CONFLICT` 且零写入。去重前后任一超过 500 均为
`FACE_BATCH_TOO_LARGE`；500 内重复 face 是 `VALIDATION_ERROR`，不能靠去重静默接受。

## 6. API 与稳定错误

P3-04B 增加以下 `/api/v1` 路径，并沿用 loopback Host/Origin、HttpOnly session、double-submit CSRF 和
统一错误 envelope：

```text
GET  /api/v1/faces/{face_id}/assignment
PUT  /api/v1/faces/{face_id}/assignment
POST /api/v1/face-assignments/batch
POST /api/v1/person-operations/{operation_id}/undo   # 扩展既有端点
```

PUT body：`contract_version`、`request_id`、`action ∈ {assign,unassign,restore_auto}`、
`target_person_id`（仅 assign 必须且仅 assign 允许）、`expected_revision >= 0`。batch body 除 request_id
外携带同形 item；每项 face_id 放 body。undo 沿用 `request_id + expected_operation_revision`，可撤销
P3-03 或 P3-04 operation，但不同 entity_type 使用各自 conflict 检查。

成功响应固定包含 operation id/type/revision/status、undo_of、changed、filter revision、replayed 与逐脸
`FACE_CONTROL` item（face_id、changed、mode、revision、assigned_person_id、activated/deactivated
exclusion person IDs）。GET 只返回该脸的 mode、logical revision、canonical effective person（可空）、
active exclusion person IDs 与 suggestions_available 布尔值；不返回单对分数或几何信息。

| code | HTTP | 语义 / 安全 details |
|---|---:|---|
| `FACE_NOT_FOUND` | 404 | face 或脸级 operation 不存在；只返回安全 ID |
| `PERSON_NOT_FOUND` | 404 | assign target 不存在或重定向悬挂 |
| `FACE_ASSIGNMENT_CONFLICT` | 409 | expected/current logical control revision 不同；可返回 face_id、expected/current |
| `FACE_IDEMPOTENCY_CONFLICT` | 409 | request_id 已绑定不同 payload；不回显 payload/hash |
| `FACE_BATCH_TOO_LARGE` | 422 | 去重前后任一项目数超过 500；可返回 max_batch_size |
| `FACE_STATE_CONFLICT` | 409 | 权威 bundle 违反模式/唯一有效关联组合；只返回字段名原因 |
| `FACE_OPERATION_NOT_UNDOABLE` | 409 | operation 状态或 after snapshot 已被后续人工编辑改变；返回安全 conflicts |
| `PERSON_REDIRECT_CYCLE` | 409 | target canonicalize 遇环；沿用 ADR-0006 |
| `VALIDATION_ERROR` | 422 | action/target 组合、重复 face、ID/request_id shape 非法 |

响应、错误、日志和诊断不得含本地路径、bbox、landmark、向量、confidence/单对相似度、幂等 payload 或
内部快照。未知内部错误仍经既有统一脱敏边界变成 `INTERNAL_ERROR`。

## 7. 补偿撤销

`UNDO` 是新操作而不是回滚计数器。先做新 request_id 的幂等门，再校验原 operation 存在、类型属于
P3-03 或 P3-04 可撤销集合、`state=COMMITTED`、未被撤销且 operation revision 匹配。首版不允许 undo an
UNDO。

对每个 `FACE_CONTROL` item，把 live canonical bundle 与原 `after_value_json` 比较。任何 control mode/
revision、有效关联、排除集合或相关 operation 标记不同，都说明该脸有后续人工变化；整批返回
`FACE_OPERATION_NOT_UNDOABLE`、零写入。`details.conflicts` 只含
`entity_type=FACE_CONTROL`、`face_id`、`reason`（逗号分隔的字段名）。无冲突时恢复 before 的**语义**，
但新 control revision 必须从 live revision +1；恢复的链接 revision 也取
`max(before_revision, live_revision)+1`，不得倒退。原 operation 标为 UNDONE 并指向新的 UNDO；UNDO 自己
保存 before/after 快照并按本 ADR §8 产生新的派生追赶。

若原 P3-03 合并已把一个 `MANUAL_UNASSIGNED` 的脸降级，而之后 P3-04 又编辑该脸，则合并撤销看到其
`PERSON_BUNDLE` after snapshot 不同，或脸级撤销看到 FACE_CONTROL after 不同，必须整体拒绝较旧操作；
不得为了“撤销成功”覆盖后来的人工作废/指定。

## 8. 权威到派生的提交顺序

实际 changed 操作在一个 SQLite 事务内按固定顺序提交：

1. 先写 operation 行，再写当前 face link/control/exclusion 权威事实与 `FACE_CONTROL` item 快照；
2. 收集操作前后受影响的 face_id 与 `DISTINCT asset_id`；
3. 全操作只推进一次 `catalog_filter_revision`，复用现有 cause `PERSON_CHANGED`；
4. 每个受影响资产 upsert `search_projection_queue(..., PERSON_CHANGED, now_ms)`；
5. 对已发布进当前 face generation 的每个受影响 face 调用既有 `queue_candidate()`，先变
   `PENDING_REPAIR`，由 replay 确认；没有世代/entry 时安全跳过；
6. 写回 operation 的最终 filter revision 并提交。

事务内不做文件 I/O、模型推理、Lance 写入或投影重建。任一步失败整体回滚。no-op 与幂等 replay 不执行
第 2～5 步。脸级纠错改变的是人物权威投影，`PERSON_CHANGED` 已准确表达，因此 `0012` **不得**再重建
`search_projection_queue` 或新增虚假的 cause，也不得写图片向量的 `index_outbox`。

## 9. 只读状态与计数口径

`/capabilities` 的 `face_summary` 加性增加四个整数，全部从 SQLite 当前权威事实计算：

- `manual_assigned_faces`：control mode 为 MANUAL_ASSIGNED 且有唯一有效 MANUAL link 的 distinct face；
- `manual_unassigned_faces`：control mode 为 MANUAL_UNASSIGNED 的 distinct face；
- `faces_with_active_exclusions`：至少一个活动 exclusion 的 distinct face；
- `active_face_exclusions`：活动 `(face_id, canonical person)` 关系数，canonical 等价项按 pair 去重。

不把未命名 group 数当待手动归类数；不把建议当归属；不因同一 face 多条排除重复计 face。仅返回计数，
不返回姓名、face_id、路径、坐标或相似度。坏/过新 schema 继续沿用既有只读诊断边界。

## 10. 0012 迁移结论

P3-04B **需要且只允许**新增 `0012_face_correction_operations.sql`。原因不是增加控制事实列：0011 已具备
足够的 `face_assignment_controls` / `face_exclusions` 形状，且不得给它们增加无默认值 NOT NULL 列；
原因是 0011 把 operation type 和 item entity type/ID 形状写成闭合 CHECK，无法合法保存本 ADR 的脸级
操作。0012 仅重建这两张操作表（含必要索引/外键）、原样回填既有行并扩展闭合枚举/互斥字段。

迁移必须遵守 ADR-0002 §7 / 主架构 §11.2：只读预检、备份、单事务推进、缺件/损坏整体回滚、旧程序见
schema 12 拒写、过新库零写；0001～0011 逐字节不变。外键依赖要求实施卡先设计安全重建顺序并用含历史
operation/item/undo/alias 引用的 v11 夹具证明逐列保留。

## 11. 必须固定的反例

1. 从爸爸 A 取消后，重启/重扫/重新关联均不能自动归回 A 或别人；A exclusion 活跃。
2. assign B 后晚到 AUTO 结果比较旧 revision，零写入，B 的 MANUAL/CONFIRMED 不变。
3. 明确 assign 回曾排除的 A：只解除 A 排除，其他人物排除仍活跃。
4. restore_auto 保留全部排除、移除人工有效归属且不采用任何 suggestion。
5. 同一 face 在一个 batch 出现两次，即使 action/revision 不同也 422 且零写入。
6. batch 第二项冲突或事务中途异常时，第一项、operation、filter revision、队列均回滚。
7. 原操作后该脸被人工再次编辑，撤销整体 `FACE_OPERATION_NOT_UNDOABLE`；revision 不倒退。
8. 对 UNDO 再撤销被拒，且没有新操作行。
9. P3-03 合并遇 MANUAL_UNASSIGNED 仍按 ADR-0006 降级；其后脸级编辑使旧快照撤销冲突，而不是覆盖。
10. 合照中请求只列一个 face_id；同图其他脸和人物计数保持不变，照片仅在仍有有效脸时留在人物相册。
11. 无控制行以 expected_revision=0 首次 unassign 成 revision 1；错误地传 1 被拒且不补建行。
12. 同 request_id 同 payload 在后来 revision 已变化后仍优先 replay；同 key 不同 payload 冲突。
13. MANUAL_ASSIGNED/MANUAL_UNASSIGNED 下建议可以读，但任何自动路径都不得把它提升为 CONFIRMED。
14. changed 操作 filter revision 只 +1、资产队列 cause 为 PERSON_CHANGED、face entry 先 PENDING_REPAIR；
    no-op/replay 全部不变。
