# ADR-0010: 模型重检测后的新旧 `face_id` 可审查映射与 P3 故障验收矩阵契约

- 状态：Accepted（2026-09-22，P3-08A：DS-H 契约草拟/两轮修正 + C2 / GPT-5.6 Sol / high 聚焦终审冻结）
- 日期：2026-09-22
- 适用实现：P3-08B
- 上游：ADR-0006、ADR-0007、ADR-0008、ADR-0009、主架构 §4.2 / §8.3.4 / §8.3.5 / §12.4、V3.1 §8 / §11

## 1. 决策范围与阶段边界

本 ADR 冻结“模型重新检测产生新 `face_id` 之后，旧人工锚点与新人脸之间的可审查映射”的唯一语义：
映射身份与锚点、候选生成、审核状态机、人工知识的转移矩阵、确认/拒绝/补偿撤销事务、重启与升级回退
持久状态、只读审核面与写入面、错误与隐私闭集，以及 P3 故障验收矩阵的权威形状。

机器可读伴随物是 `contracts/p3-face-redetection-contracts.v1.json`（映射协议）与
`contracts/p3-acceptance-matrix.v1.json`（P3 故障/纠错验收矩阵）；两者冲突时以本 ADR 为准。

P3-08A **只冻结规则**，不实现生产行为、不新增 migration、不改前端、不运行真实照片库，也不声称数据库或
API 已经执行本 ADR 的拒绝。P3-08B 才能按本 ADR 新增 `0014_face_redetection_mapping.sql`、服务、API 与
故障测试。不得借本任务改变 P3-02 的检测产物/阈值/`space_id`/候选 generation、ADR-0006 §8.3.3 的合并
重定向、ADR-0007 的脸级控制与排除、ADR-0008 的拆分、ADR-0009 的人物条件与 canonical 解析。

“未确认映射不得自动填回错误人物”是主架构 §8.3.4 的上位原则，本 ADR 把它落成 §3.4 的**临时保持
（provisional hold）**与 §4 的转移矩阵；P3-08A/B 都不得用自动相似度替代人工确认。

## 2. 映射身份与锚点

### 2.1 稳定键

- `old_face_id` / `new_face_id` 必须是 `faces.face_id` 的原值（64 位小写十六进制）；映射身份永远是
  **脸 ID 对**，不得使用 ordinal、`person_id`、cluster id、候选行号或列表位置冒充。
- 一条映射行的稳定键是 `mapping_id = sha256(UTF-8 canonical JSON)`，字段固定为
  `{"asset_id", "old_face_id", "new_face_id", "old_spec_revision", "new_spec_revision"}`（对象 key 升序、
  无多余空白、整数不带前导零）。同一对脸在任何一次重算中得到同一个 `mapping_id`。
- 唯一约束是 `UNIQUE(old_face_id, new_face_id)`；一个旧脸对同一新脸至多一条映射行，重复检测只能更新
  该行的匹配证据与 revision，不得插入第二行。
- **自映射不是映射**：`old_face_id == new_face_id` 的对永不生成，也不接受提交——同一次检测里的同一张脸
  没有被“重新识别成”别的脸。若提交这种对，答 `FACE_MAPPING_NOT_FOUND`，零写入。
- 审核单位是**旧脸的候选组**（下称 group），键就是 `old_face_id`。一个人脸资产上的一次决策请求可以
  覆盖多个 group（§5.2），因此“多旧脸合一一新脸”不需要跨请求协调。

### 2.2 资产与世代锚点

- `asset_id` 只来自 `faces.asset_id`；跨资产候选**永不生成**（§3.3），提交跨资产对答
  `FACE_MAPPING_NOT_FOUND`。
- `old_spec_revision` / `new_spec_revision` 取 `faces.spec_revision`。方向即“旧 = 本资产上本次重检测
  之外仍然存活的行，新 = 本次提交刚写入的行”，与升级/回退方向无关；回退只是把方向反过来。
- 几何锚点使用 `faces.bbox_json` 与 `faces.landmarks_json`，二者已是**方向修正后**的坐标系（0010 的
  `bbox` 定义即“orientation-normalised source coordinates”），因此匹配不需要重新读图、不依赖 EXIF。
- 检测版本锚点由 `spec_revision` + `detector_id` + `face_revision` 表达；映射行不复制 embedding，
  也不引用 `face_index_generations` / `face_candidates` / `face_embeddings` 的内容。

### 2.3 审查状态

映射行 `state ∈ {SUGGESTED, CONFIRMED, REJECTED, STALE}`，`revision >= 1`，`last_operation_id` 可空。
group `state ∈ {OPEN, CONFIRMED, REJECTED}`，`candidate_state ∈ {NO_CANDIDATE, UNIQUE_HIGH, UNIQUE_WEAK,
AMBIGUOUS}`，`revision >= 1`。旧脸与新脸的逻辑控制 revision 仍由 0012 的
`face_assignment_controls`（无行等价 `AUTO_ALLOWED, revision=0`）唯一决定，映射行不得复制它。

**映射行与 group 行都不是人物 authority。** 人物归属、控制与排除仍只写在 `face_person_links` /
`face_assignment_controls` / `face_exclusions`，操作日志仍只有 ADR-0006/0007 的
`person_operations` / `person_operation_items`（§5.1）。

## 3. 候选生成与审核队列

### 3.1 生成时机与范围

候选生成发生在一个资产的**实际写入人脸文档的提交事务内**（沿用 `persist_face_document()` 的调用位置），
并且只针对本次提交刚写入的**新行**与本次提交后**仍然存活的旧行**：

- **跨世代是 v1 的唯一生成面**：`old_spec_revision != new_spec_revision`。模型升级、回退以及回退后再次
  写入的旧世代锚点都落在这里；`_retire_stale_faces()` 的作用域是当前 `spec_revision`，因此其他世代的
  行天然存活并成为旧侧；
- **同内容同 `spec_revision` 的重跑是幂等的**：`face_id_for()` 由 `(spec_revision, input_revision,
  ordinal, bbox)` 唯一决定（`face/processor.py`），同一资产的内容哈希固定（`persist_face_document()`
  在 `assets.sha256 != result.input_revision` 时直接拒绝），同一文档重跑得到同一批 `face_id`，
  `_upsert_face()` 只做 upsert。因此**未变化的重跑产生零新脸、零映射行、零新 group**，既有
  `CONFIRMED`/`REJECTED` group 也不被改写；
- **v1 不定义同 spec 的新旧映射**：同一 `spec_revision` + 同一 `input_revision` 只对应一份合法文档，
  若出现同 spec 但 ordinal/bbox 不同的文档，那是版本纪律问题（`spec_revision` 未随检测/预处理变更一起
  升级），不是可映射的“新脸”；实现必须按既有 `ARTIFACT_INVALID` / `STALE_INPUT_REVISION` 词表拒绝，
  **不得**因此建立同 spec 映射行或 group。要区分“旧脸被重新编号”与“模型升级换了世代”，只能看
  `spec_revision`，不能看 ordinal 或几何接近度；
- `_retire_stale_faces()` 对人工控制行的保护是**检测行保留**的防御性守卫（§3.4），不参与候选生成判定；
- 无人工控制的旧行**不进入审核队列**（§3.3 末段）：没有可转移的人工知识，自动路径会为新脸重新推导归属。

### 3.2 匹配证据（v1 冻结常量）

匹配只用方向修正后的几何，不调用模型、不读原图：

- `iou` = 两个 bbox 的交并比（无交集为 0）；
- `landmark_ratio = sqrt(mean_{i=1..5} ||p_i^old - p_i^new||^2) / diag(old bbox)`，
  `diag` 为旧 bbox 对角线长度（bbox 正面积由 0010 保证，故分母恒 > 0）；
- 候选**合格**条件：`iou >= 0.30` 且 `landmark_ratio <= 0.25`；
- 候选**高强度**条件：`iou >= 0.50` 且 `landmark_ratio <= 0.12`；
- 旧脸或新脸任一 `quality_state = 'LOW'` 时，最高只能是 `WEAK`（永远不产生 `HIGH`）。

这五个数字是 v1 冻结常量。改变其中任何一个都必须发布新的 contract version，不得在实现里“微调”。

### 3.3 候选状态与歧义

对每个进入范围的旧脸，取其合格候选集合（跨资产候选不生成、不参与计数）：

| 合格候选数 | `candidate_state` | 含义 |
|---:|---|---|
| 0 | `NO_CANDIDATE` | 旧人工锚点仍存活但本次检测没有匹配脸；“脸消失”也要人工确认 |
| 1 且 `HIGH` | `UNIQUE_HIGH` | 最可能的同脸；**仍需人工确认** |
| 1 且 `WEAK` | `UNIQUE_WEAK` | 唯一但证据弱；必须人工确认 |
| ≥2 | `AMBIGUOUS` | 拆脸/合脸/同框近邻等歧义；必须逐候选人工选择或拒绝 |

`NO_CANDIDATE` 与 `AMBIGUOUS` 都不给出默认选中项：服务端**永不自动确认**，也不给前端“预选”。0 候选时
唯一可提交的决策是 `reject`（§5.3）。

### 3.4 临时保持（provisional hold）

当旧脸携带人工知识（`mode ∈ {MANUAL_ASSIGNED, MANUAL_UNASSIGNED}` 或存在活动 exclusion）时，其候选
新脸在本组 `state = OPEN` 期间**不得被自动路径建立或替换有效关联**——这直接落地主架构 §8.3.4
“尚未确认映射的受影响人脸不得先自动填回错误人物”。

**两个谓词必须分开，不能共用一个 helper。**

| 谓词 | 语义 | 读它的位置 | 用途 |
|---|---|---|---|
| 检测行保留集合（detection-row retention） | 该资产上带人工控制/活动排除的**已存行** | `persist_face_document()` 的删除前置（既有 `human_controlled_face_ids()`） | 只决定重检测时哪些旧行不被删除，**不表达**“禁止自动归类” |
| 自动关联保持集合（association hold） | 该资产上属于 `OPEN` group 候选的**新脸**（且该 group 的旧脸携带人工知识） | **自动关联写事务内部**（`face/index.py::_reconcile_asset()` 在 `BEGIN IMMEDIATE` 内读取控制/排除的那一处，与 `_manually_locked_face_ids()` 同一事务、同一时点） | 只决定自动关联这一遍是否可写这张脸 |

把保持塞进 `human_controlled_face_ids()` 是**错的**：那个函数只在删除旧检测行时被读，不是自动关联的
提交门；真正的写门是 `_reconcile_asset()` 事务内的 `_manually_locked_face_ids()`（mode-only）加活动
排除。因此 P3-08B 必须在写门内增加“OPEN group 候选”这一来源，并且**不得**让保持影响检测行保留。

这里**不新增第四种 control mode**，也不写 `MANUAL_ASSIGNED` / `MANUAL_UNASSIGNED` 控制行（否则会污染
ADR-0007 §9 的“待手动归类”口径）。

**判定顺序（同一写事务内，不得颠倒）**：①写/退休检测行（只用保留集合）→ ②按新行 × 存活旧行计算候选
group → ③先判定候选失效（新行已不存在 → 映射行 `STALE`、group 重算）→ ④再用**存活 group** 计算自动关联
保持集合。因此“消失的候选”只会变 `STALE`，绝不会因为保持而被永久保留或永久挡住自动关联。

**`face_association_queue` 生命周期（P3-08B 必须实现）**：现在的实现会在每遍关联结束时无条件
`DELETE ... WHERE asset_id = ? AND spec_revision = ?`。本契约要求改成：

- 一遍关联跑完时，若该资产仍有属于保持集合的脸（或有关键决定因保持/晚到控制冲突被跳过），**保留/回写**
  该队列行（`face_revision` 取当前值），使保持在解除后还能再跑一遍；
- **释放集合只有 `confirm` / `reject`**（以及 `confirm` 未选择的候选）：这些情况下释放后仍为
  `AUTO_ALLOWED` 且没有有效关联的新脸所在资产**已被（重新）排队**，由随后的正常关联遍处理；决策事务
  本身**不**顺手建立自动关联；
- **`undo` 回到 `OPEN` 不是释放**：它恢复 `OPEN` 保持——该 group 的候选新脸重新被关联遍跳过——同时
  保留/重新排队该资产的队列行，等待下一次决策；`undo` 绝不允许紧随其后的关联遍给保持脸写有效关联；
- 确认并继承到人工知识的脸仍按人工锁处理（自动路径本来就跳过）；`reject` 释放后允许被正常自动处理，
  但同一个 `reject` **不**在事务内替它选人物；
- 候选失效（③）先于保持（④），失效的脸不进入保持集合；
- 幂等 replay 与语义 no-op 不改动队列状态；重启后队列行仍在（队列是权威表，不是进程内状态）。

旧脸自身的人工事实（控制行、有效关联、排除）在整条链路中**不改变**：它是审计锚点，继续可查询
（主架构 §8.3.4）。

## 4. 人工知识转移矩阵

先立总则：**任何歧义不得自动转移人工知识。** 只有经人工确认的映射才按下面的矩阵转移；`AMBIGUOUS`、
`NO_CANDIDATE`、未确认的 `SUGGESTED` 行一律不转移任何人工锁、人工归属或排除。

确认决策同时给出该资产上一个或多个旧脸的 `targets`（§5.2）。对任一新脸 `N`，其目标状态由**同一请求
内**全部指向它的旧脸共同决定：

1. **继承基数是双向受限的**：
   - 每个 group（旧脸）**至多一个** `targets` 标 `inherits_person = true`，否则 `VALIDATION_ERROR`
     （`multiple_targets_without_single_inheritor`）。一个人工归属不能同时复制到 N1、N2 两条人工确认；
     旧脸是 `MANUAL_ASSIGNED` 时允许**零个**继承者（用户明确选择不把肯定知识转给任何新脸，目标按第 3 条
     保守处理），但不允许两个或更多。
   - 每个新脸 `N` **至多一个**来源标 `inherits_person = true`（跨 group 计数），否则 `VALIDATION_ERROR`
     （`multiple_inheritors_for_new_face`）。N→1 的合脸场景里，多个旧 group 可以指向同一新脸，但只有
     一个能提供肯定知识。
2. 若某个旧脸是 `MANUAL_ASSIGNED` 到 canonical `P` 且有旧脸的 target 标记 `inherits_person = true`：
   `N` 的 control 变成 `MANUAL_ASSIGNED`，并建立指向 `P` 的唯一 `MANUAL/CONFIRMED` 关联；`P` 的
   exclusion 被解除，其他人物 exclusion 保持不变（沿用 ADR-0007 §3 的 assign 语义）。
3. 若 `N` 没有继承者，但它至少有一个来源旧脸处于 `MANUAL_ASSIGNED` / `MANUAL_UNASSIGNED`：`N` 的
   control 变成 `MANUAL_UNASSIGNED`、不建立有效关联。这是保守的否定知识：宁可留待人工整理，也不自动
   归入任何人物。
4. 全部来源旧脸的活动 exclusion 并集复制到 `N`（否定知识冗余复制是安全的，且满足“不能仅因 `face_id`
   变化就丢掉人工锁或排除”）。
5. 若 `N` 继承了人物 `P`，同时又从别的来源旧脸继承到 `P` 的活动 exclusion：整请求
   `FACE_MAPPING_CONFLICT` / `human_affirm_deny_conflict`，零写入。人工肯定与否定不得靠覆盖解决
   （ADR-0006 §8.3.3）。
6. 若 `N` 在本次事务开始前**已经**带有人工控制（`MANUAL_*` 控制行或活动 exclusion）：整请求
   `FACE_MAPPING_TARGET_CONTROLLED`，零写入。新脸已被人工编辑时，映射不得覆盖后来的决定；用户必须先
   处理那张脸。
7. `AUTO_ALLOWED` 且无 exclusion 的旧脸：没有人工知识可转移。它的确认只改变映射状态（`changed=false`
   对 control 事实），新脸的自动归属仍由自动路径推导。

被确认映射覆盖不到的新脸（本资产上既不继承也无来源旧脸）**一律保持自动路径留下的状态**，不静默继承
任何人物。

## 5. 事务、幂等与补偿撤销

### 5.1 唯一操作权威

映射决策写进既有 `person_operations` / `person_operation_items`：

- `person_operations.type` 增加 `FACE_MAPPING_CONFIRM`、`FACE_MAPPING_REJECT`；`UNDO` 仍是所有可撤销
  操作的共同补偿类型（ADR-0006 §7）。`source_person_ids_json` 固定 `[]`，`target_person_id` 写 NULL。
- `person_operation_items.entity_type` 增加 `FACE_MAPPING_GROUP`，`entity_key = old_face_id`、
  `entity_id IS NULL`，与既有 `PERSON_BUNDLE`（正整数 `entity_id`）和 `FACE_CONTROL`
  （`entity_key = face_id`）互斥。一次决策**每个受影响旧脸恰好一项**，`item_index` 继续严格连续。
- group 项的快照是 canonical JSON，至少含：group 的 `state`/`revision`/`candidate_state`；该旧脸全部
  候选的 `mapping_id`/`new_face_id`/`state`/`revision`/`inherits_person`；本次写入的新脸人工事实摘要。
  快照**不含** bbox、landmark、IoU、向量、路径，也不得原样通过 API 返回（ADR-0007 §2 同口径）。
- 同一决策实际改动了新脸 control/exclusion 时，同事务再写对应 `FACE_CONTROL` 项（复用 ADR-0007 §2 的
  快照形状），使一次补偿撤销同时恢复映射状态与人工事实。

不建立第二套人物/映射操作日志，不新增第二套幂等键，不把映射状态写进 `persons` 表。

### 5.2 请求、批量与幂等

```text
POST /api/v1/face-mappings/decisions
```

body 固定为 `contract_version`、`request_id`、`asset_id`、`decisions[]`：
`decisions` 为 1..200 项，每项含 `old_face_id`、`expected_review_revision`、
`decision ∈ {confirm, reject}`、`targets[]`（`confirm` 为 1..16 项且每项含 `new_face_id` 与
`inherits_person`；`reject` 必须为空数组）。同一请求内 `old_face_id` 去重、`asset_id` 与每个 group 的
`asset_id` 一致、`targets` 内 `new_face_id` 去重、每个 group 的 `targets` 必须是该组**当前**候选集合的
子集，否则 `VALIDATION_ERROR`（重复或未知目标）或 `FACE_MAPPING_STALE`（候选已过期）。

`request_id` 沿用全局幂等键语义（1..64 可打印 ASCII，UTF-8 canonical JSON payload hash，含 endpoint
family、`asset_id` 与**排序后的** decisions）：同 key 同 hash 在任何 revision 检查之前返回原
operation 且 `replayed=true`；同 key 不同 hash 返回 `FACE_MAPPING_IDEMPOTENCY_CONFLICT` 且零写入。

整批先完成 shape/存在性/**全部** `expected_review_revision`/目标合法性校验，再在一个
`BEGIN IMMEDIATE` 短事务内一次提交；任一项失败或事务中途故障整体零写入。

### 5.3 决策语义

| 决策 | 前置 | 结果 |
|---|---|---|
| `confirm` | 该 group `state = OPEN`、`candidate_state != NO_CANDIDATE`、`targets` 为当前候选子集 | 命中候选 `state = CONFIRMED`，未命中的当前候选 `state = REJECTED`；group `state = CONFIRMED`；按 §4 写入人工事实 |
| `reject` | 该 group `state = OPEN` | 全部当前候选 `state = REJECTED`；group `state = REJECTED`；**不**改变任何 control/exclusion/关联事实 |

`NO_CANDIDATE` 只接受 `reject`（acknowledge），`confirm` 答 `VALIDATION_ERROR`
（`confirm_requires_targets`）。两个决策都允许把已 `CONFIRMED`/`REJECTED` 的 group 再次提交吗？**不允许**：
重复提交同一 group 是 `FACE_MAPPING_CONFLICT` / `review_already_decided`，除非通过补偿撤销回到 `OPEN`。

**保持与队列效应（同一事务）**：决策提交后，保持集合与 `face_association_queue` 必须按下列规则落到权威态，
不得留到“下一次碰巧重启”才修：

| 决策结果 | 保持 | 队列 |
|---|---|---|
| `confirm`（有继承者或继承 exclusion） | 该新脸转入人工锁，自动路径本就跳过 | 人工事实变化 → 一次 `PERSON_CHANGED` 投影 + 受影响 face entry `PENDING_REPAIR` |
| `confirm`（无人工事实变化，例如来源旧脸是 `AUTO_ALLOWED`） | 解除该 group 的保持 | 释放后仍 `AUTO_ALLOWED` 且无有效关联的脸所在资产必须已在队列（保留或重新排队） |
| `reject` | 解除该 group 的保持；不写任何 control/exclusion | 资产被保留/重新排队；**不推进** `catalog_filter_revision` |
| `undo` 回到 `OPEN` | 重新施加保持 | 资产保留/重新排队，等待下一次决策 |
| 候选失效置 `STALE` | 失效脸不进入保持集合 | 队列状态不受映射失效影响（它仍由该资产自身的关联需要决定） |

“重新排队”指 upsert/保留 `face_association_queue(asset_id, spec_revision, face_revision, queued_at)`，
**不是**在决策事务里替用户选人物。

### 5.4 revision 与补偿撤销

- `expected_review_revision` 必须等于 group 的实时 `revision`；不等答 `FACE_MAPPING_STALE` 并返回
  `expected_review_revision` / `current_review_revision`（安全 ID 与整数，无内部快照）。
- 实际改变 group 状态的决策把 group `revision` 与全部受影响映射行 `revision` 各 `+1`，**不倒退**；
  幂等 replay 与语义 no-op 都不推进 revision。
- `POST /api/v1/person-operations/{operation_id}/undo` 继续是唯一撤销入口；映射操作由
  `operation_family()` 归类为 `mapping` 家族，交新的 `persons/mapping.undo_face_mapping_operation()`
  处理。撤销是**新补偿操作**：先过新 `request_id` 幂等门，再校验原 operation 属于
  `{FACE_MAPPING_CONFIRM, FACE_MAPPING_REJECT}`、`state=COMMITTED`、`undone_by IS NULL`、
  `operation_revision` 匹配；首版不允许 undo an `UNDO`（ADR-0007 §7）。
- 撤销前把每个 `FACE_MAPPING_GROUP` 项与 `FACE_CONTROL` 项的 live 状态与记录的 `after` 快照比较；任一
  group/candidate 状态或人工事实不同，整批 `FACE_MAPPING_OPERATION_NOT_UNDOABLE`，`details.conflicts`
  只含 `entity_type`/`entity_key`/`reason`（逗号分隔字段名），零写入。
- 无冲突时恢复 `before` 的**语义**（group 回到 `OPEN`、候选回到之前状态、人工事实回到决策前），但
  恢复后的 group/映射行/控制与关联 revision 取 `max(before, live) + 1`，永不倒退；原 operation 标
  `UNDONE` 并指向新 `UNDO`。

## 6. 权威到派生的提交顺序

一次 changed 的映射决策在一个 SQLite 事务内按 ADR-0007 §8 的同一顺序提交：

1. 先写 operation 行，再写 group/映射行状态与 `FACE_MAPPING_GROUP` 快照；
2. 需要转移人工知识时，在同一事务写新脸的 `face_assignment_controls` /
   `face_person_links` / `face_exclusions` 与对应 `FACE_CONTROL` 快照；
3. 收集操作前后受影响的 `face_id` 与 `DISTINCT asset_id`；
4. 仅当人工事实实际变化时，全操作推进一次 `catalog_filter_revision`，复用 cause `PERSON_CHANGED`；
   纯 `reject` 或纯审核（无人工事实变化）**不推进**该 revision、不写投影队列；
5. 人工事实变化时按 ADR-0007 §8 对受影响资产 upsert `search_projection_queue(..., PERSON_CHANGED)`，
   并把已发布的脸索引项置 `PENDING_REPAIR`，由 replay 确认；
6. 写回 operation 的最终 filter revision 并提交。

事务内不做文件 I/O、模型推理、LanceDB 写入或投影重建。**不写图片向量的 `index_outbox`**，不新增 queue
cause，不重建 `face_index_generations` 的 `space_id`。

## 7. 重启、重检测、升级回退与恢复

- **重启**：group/映射行、操作快照与人工事实全部在 SQLite 并与操作同事务提交，重启后 `OPEN` 仍是
  `OPEN`、`CONFIRMED` 仍是 `CONFIRMED`；临时保持由持久 group 状态推导，不依赖进程内状态。
- **重复检测（同内容、同 `spec_revision`）**：`face_id_for()` 与 `_upsert_face()` 使重跑幂等——同一批
  `face_id` 被 upsert，**零新脸、零新映射行、零新 group**；已 `CONFIRMED`/`REJECTED` 的 group 不被
  改写（匹配证据可刷新），不得把人工决定清回 `OPEN`，也不得为同一对脸插入第二条映射行。
- **检测失败 / 取消 / 晚到 worker**：没有新脸就没有新候选；旧世代 worker 的产物按 0010 的
  `processor_id`/`spec_revision`/`space_id` 校验被拒（`ARTIFACT_INVALID`），不产生候选，也不改变既有
  group。晚到结果不得把已 `CONFIRMED` 的映射改回自动。
- **模型升级**：新 `spec_revision` 的行与旧世代行共存，为带人工知识的旧脸建立新 group；旧世代锚点与其
  group 继续可查。
- **升级失败 / 回退**：`FACE_SPEC_REVISION` 回到旧常量时，方向反转并为“当时的存活行 × 本次写入行”重新
  建候选；`CONFIRMED` 映射及其人工事实不回滚，revision 不回退，旧审计链保留。
- **候选失效**：后续重检测删除了某个候选新脸（非人工控制的新脸会被 `_retire_stale_faces()` 删除）时，
  对应映射行置 `STALE`、`+1` revision，group 重新计算 `candidate_state`；对该 group 的决策若不是基于
  实时候选集合，答 `FACE_MAPPING_STALE`。失效判定排在保持判定**之前**（§3.4），被删除的候选不得因为
  保持而被当作不可删除的人工锚点。
- **崩溃/重启后的保持与队列**：保持集合由持久 group 状态推导，队列行是权威表的一行；崩溃后重跑关联遍
  必须重新算出同样的保持集合，并在保持期内保留队列行。
- **备份恢复**：映射行、group 行、操作日志与人工锁/排除/重定向同在权威库，恢复后状态自洽；恢复不得
  把 `CONFIRMED` 降级，也不得把 revision 重置为 1。

## 8. API、错误与隐私

### 8.1 只读审核面

```text
GET /api/v1/face-mappings?state=open|all&limit=1..200&cursor=...
GET /api/v1/face-mappings/{old_face_id}
```

列表按 `old_face_id` 的 keyset 分页（独立游标空间 `fm1`），只返回 group 摘要：`old_face_id`、
`asset_id`、`old_spec_revision`、`candidate_state`、`state`、`revision`、旧脸的既有控制投影
（`mode`/`control_revision`/`assigned_person_id`）、候选数与 `thumbnail_url`
（复用既有 `/api/v1/faces/{face_id}/thumbnail` 模板）。单条查询额外返回候选数组，每项只含
`new_face_id`、`new_spec_revision`、`match_state ∈ {HIGH, WEAK}`、`quality_state`、`state`、`revision`、
`thumbnail_url`。

响应、错误与日志**不得**包含：bbox、landmark、IoU 或任何几何数值、embedding、confidence、单对相似度、
绝对/相对本地路径、原始 EXIF/GPS、幂等 payload/hash、内部快照、`spec_revision` 之外的模型权重细节。

**公开边界本身就是契约的一部分**：`limit` 只接受 1..200（缺省 50），越界答 `VALIDATION_ERROR`；
`state` 只接受 `open` / `all`；`next_cursor` 与请求 `cursor` 采用与既有列表相同的**不透明**编码
（base64url、无填充，解码后固定为 `fm1:<old_face_id>`），前缀不是 `fm1`、值不是 64 位小写十六进制或不
可解码，一律答既有的 `INVALID_CURSOR`（与 `/persons`、`/faces` 共用，不新造码）。服务端不得把超界 limit
静默夹紧成 200，也不得把外来游标解释成本列表的位置。

### 8.2 冻结错误闭集

| code | HTTP | 语义 / 安全 details |
|---|---:|---|
| `FACE_MAPPING_NOT_FOUND` | 404 | 跨资产对、未知 `mapping_id`/`old_face_id`；只返回安全 ID |
| `FACE_NOT_FOUND` | 404 | 请求中的 `old_face_id`/`new_face_id` 在权威库不存在 |
| `PERSON_NOT_FOUND` | 404 | canonical 解析悬挂（继承人物的重定向目标缺失） |
| `PERSON_REDIRECT_CYCLE` | 409 | canonical 解析遇环；沿用 ADR-0006 |
| `FACE_MAPPING_CONFLICT` | 409 | group 已决策、肯定/否定冲突、目标组合非法；只返回 `reason` 与安全 ID |
| `FACE_MAPPING_STALE` | 409 | `expected_review_revision` 不等，或候选集合已变化；可返回两个整数 revision |
| `FACE_MAPPING_TARGET_CONTROLLED` | 409 | 目标新脸在事务开始前已有人工控制；只返回安全 ID |
| `FACE_MAPPING_IDEMPOTENCY_CONFLICT` | 409 | 同 `request_id` 不同 payload；不回显 payload/hash |
| `FACE_MAPPING_BATCH_TOO_LARGE` | 422 | decisions 超过 200 或某组 targets 超过 16；可返回上界 |
| `FACE_MAPPING_OPERATION_NOT_UNDOABLE` | 409 | 后续人工编辑使 `after` 快照失效；返回安全 `conflicts` |
| `INVALID_CURSOR` | 422 | 游标不可解码、前缀不是 `fm1` 或值形状非法（沿用既有列表码） |
| `VALIDATION_ERROR` | 422 | shape、去重、`reject` 带 targets、`confirm` 缺 targets、ID 形状非法 |

未知内部错误仍经既有统一脱敏边界变成 `INTERNAL_ERROR`。

**`details` 是闭集，不是自由对象。** 每个错误码只允许下表列出的键（全部为受控 ID、整数 revision 与
枚举/短 reason），并且必须至少包含“必填”一列；出现任何其他键（含 `bbox`、`landmark`、`iou`、`path`、
`internal_snapshot`、幂等 payload/hash）即视为契约违规，实现必须拒绝而不是透传。

逐码闭集在 companion 里用**严格 `oneOf`** 实现：每个错误码一个分支，分支同时冻结 `code`、允许键与必填键，
因此跨码串键（例如在 `FACE_MAPPING_NOT_FOUND` 的 `details` 里带上 `person_id`）会落到零个匹配分支而被拒；
`FACE_NOT_FOUND` 用 `anyOf` 表达“`old_face_id` / `new_face_id` 至少一个”；`INVALID_CURSOR` 的 `details`
是**空对象**（连安全 ID 也不允许）。禁止用“所有安全键的并集 + 逐码追加必填键”的弱形状代替。

| code | `details` 允许键 | 必填键 |
|---|---|---|
| `FACE_MAPPING_NOT_FOUND` | `old_face_id`、`new_face_id`、`asset_id` | `old_face_id` |
| `FACE_NOT_FOUND` | `old_face_id`、`new_face_id`、`asset_id` | 请求涉及的那一个 |
| `PERSON_NOT_FOUND` | `person_id`、`old_face_id` | `person_id` |
| `PERSON_REDIRECT_CYCLE` | `person_id` | `person_id` |
| `FACE_MAPPING_CONFLICT` | `old_face_id`、`new_face_id`、`reason` | `reason` |
| `FACE_MAPPING_STALE` | `old_face_id`、`expected_review_revision`、`current_review_revision` | 三个全部 |
| `FACE_MAPPING_TARGET_CONTROLLED` | `old_face_id`、`new_face_id` | `new_face_id` |
| `FACE_MAPPING_IDEMPOTENCY_CONFLICT` | `old_face_id`、`asset_id` | `asset_id` |
| `FACE_MAPPING_BATCH_TOO_LARGE` | `max_decisions`、`max_targets` | `max_decisions` |
| `FACE_MAPPING_OPERATION_NOT_UNDOABLE` | `operation_id`、`expected_operation_revision`、`current_operation_revision`、`status`、`conflicts` | `operation_id` 与 `conflicts` |
| `INVALID_CURSOR` | （空对象） | 无 |
| `VALIDATION_ERROR` | `reason`、`old_face_id`、`new_face_id`、`asset_id` | `reason` |

`details.conflicts` 是 1..64 项的数组，每项只含 `entity_type`（固定 `FACE_MAPPING_GROUP`）、
`entity_key`（`old_face_id`）与 `reason`（逗号分隔的字段名），不得含快照内容。

### 8.3 诚实自描述

`/capabilities` 的 `face_summary` 加性增加只读计数：`open_face_mappings`、
`ambiguous_face_mappings`、`stale_face_mappings`、`confirmed_face_mappings`（全部为整数，不返回姓名、
face_id、路径或坐标）。入口只在 `schema_version >= 14` 且 `schema_status == "ok"` 时显示；旧 schema、
空队列或无候选时返回诚实的空页，不伪造“已支持”，也不把建议当已确认。

## 9. P3-08B 最小实现面（建议，不在本卡实现）

- `catalog/migrations/0014_face_redetection_mapping.sql`：新增 `face_redetection_mappings`、
  `face_redetection_groups` 两表与其索引/外键，并重建 `person_operations` / `person_operation_items`
  的闭合 `CHECK`（新增 `FACE_MAPPING_CONFIRM`/`FACE_MAPPING_REJECT` 与 `FACE_MAPPING_GROUP`），沿用
  ADR-0007 §10 / ADR-0002 §7 的备份、单事务、兼容边界与 `0001`～`0013` 逐字节不变要求；
  `schema_version 13 → 14`。
- `face/processor.py`：在 `persist_face_document()` 同事务内生成/重算候选、先置 `STALE` 再算保持；
  `human_controlled_face_ids()` 保持“检测行保留”语义，**不**承载自动关联保持。
- `face/index.py`：在 `_reconcile_asset()` 的自动关联写事务内加入 §3.4 的保持谓词（与
  `_manually_locked_face_ids()`、活动排除同一事务同一时点读取），并把 pass 末尾的无条件
  `DELETE FROM face_association_queue` 改成 §3.4 的保留/重新排队规则。
- `persons/mapping.py`（新）：`decide_face_mappings()`、`undo_face_mapping_operation()` 与
  `operation_family()` 的 `mapping` 分支；`persons/operations.py` 只做家族路由与 §11 的顺序修复。
- `persons/read.py`：`mapping_page()` / `mapping_group()` 只读投影；`api/models.py`、`api/routes.py`、
  `api/errors.py`、`api/queries.py` 增加冻结路径、DTO 与计数。
- `src/photo_search/web/**`：`/persons` 页加“重检测待确认”视图（旧脸/新脸裁剪并排、逐候选选择、拒绝、
  撤销），无新依赖、无 CDN。

## 10. P3-07B §6 缺陷的冻结不变量（P3-08B 必修）

现象（P3-07B 报告 §6，独立复现）：`merge(target=高 id, sources=[低 id])` 提交成功，但
`undo` 触发 `UNIQUE constraint failed: face_person_links.face_id`（部分唯一索引
`ux_face_person_links_effective`）；目标 id 更小时恰好不会触发。

根因（只读定位）：`persons/operations.py::undo_person_operation()` 先按 `item_index` 顺序（即
`sorted(before)` 的 person id 升序）逐人调用 `_restore_link_rows()`，该函数对每个 person 内部做
“先删后插”。源人物的 `CONFIRMED` 行被插回时，目标人物仍持有同一批 `face_id` 的有效行。

冻结不变量（P3-08B 必须实现并回归）：

1. 一次撤销内的链接恢复必须**与人 id 顺序无关**：先对操作影响集合内的全部 `CONFIRMED` 行做一次删除，
   再按 before 快照写回；任何语句边界上每张脸至多一条有效行。
2. “影响集合”严格由该 operation 的 `before/after` 快照决定；不得删除其他脸或未被本操作写过的行。
3. 恢复 revision 继续 `max(before, live) + 1`，永不倒退；`MANUAL_UNASSIGNED` 的脸在撤销后仍无
   `CONFIRMED` 行。
4. 回归必须覆盖：目标 id 大于源 id（原反例）、反向 id 顺序、≥2 个源人物、源/目标带控制或排除、
   以及合并后被再次人工编辑 → 整批 `PERSON_OPERATION_NOT_UNDOABLE` 而非覆盖。
5. 该修复不得改变 ADR-0006/0007/0008 已冻结的对外错误码、响应形状或 `catalog_filter_revision` 语义。

## 11. P3 故障验收矩阵的权威形状

`contracts/p3-acceptance-matrix.v1.json` 是 P3-08B 的验收清单权威件。每一项必须给出：稳定 `id`、
`clause`（上位条款）、`group`、`fixture`、`injection`、`expected_authority`、
`allowed_side_effects`、`forbidden_side_effects`、`evidence`（`existing` / `must_rerun` /
`not_implemented`）与 `p3_08b_step`（测试路径或浏览器步骤）。矩阵必须覆盖：

- 主架构 §12.4 第 8、14～22 项（人物与投影）与第 29～33 项（地点交叉）；
- 映射的生成、确认、拒绝、撤销、重启、重扫、升级失败、回退与晚到 worker；
- 人物 rename/merge/split/face correction/undo 与地点 assign/unassign/restore_auto/batch/undo 的交错
  顺序，以及“各自只改自己的 authority、共同递增/校验 `catalog_filter_revision`、旧搜索会话必须明确
  stale”；
- 同图 sibling、`MANUAL_UNASSIGNED`、活动 exclusion、肯定/否定冲突、旧 canonical id、投影滞后补查；
- §10 的 merge/undo 顺序缺陷族；
- 真实浏览器闭环计划（`/persons` → `/places` → `/search` 的纠错→筛选→撤销/冲突→重搜，窄宽视口、
  键盘可达、无路径/坐标/外链泄露、访问日志边界）。自动化工具未落地前只能称“记录的真实浏览器 smoke”。

## 12. 必须固定的反例

1. 只有一个 `UNIQUE_HIGH` 候选时仍**不自动确认**：group 保持 `OPEN`，新脸在保持期内不得被自动归类。
2. 旧脸 `MANUAL_ASSIGNED` 到 A，确认到新脸 N：N 继承 `MANUAL_ASSIGNED`/A 与旧脸全部排除；旧脸事实
   不变、仍可查；`catalog_filter_revision` 恰好 +1。
3. `1 → N` 多目标确认且请求给了两个 `inherits_person=true`：`VALIDATION_ERROR`，零写入。
4. `1 → N` 多目标确认只指定一个继承者：其余目标为 `MANUAL_UNASSIGNED`（若来源旧脸是 MANUAL）且复制
   exclusion；不产生第二个 `CONFIRMED`。
5. `2 旧脸 → 1 新脸`（合脸）：同一请求两个决策指向同一 `new_face_id`，只有一个 `inherits_person=true`；
   exclusion 取并集；`catalog_filter_revision` 只 +1。
6. `NO_CANDIDATE` 上提交 `confirm` → `VALIDATION_ERROR`；提交 `reject` → group `REJECTED`，旧脸人工
   事实不变，`catalog_filter_revision` 不变。
7. 确认时目标新脸已在事务开始前带人工控制 → `FACE_MAPPING_TARGET_CONTROLLED`，零写入、不覆盖。
8. 继承人物 P 与指向 P 的活动 exclusion 冲突 → `FACE_MAPPING_CONFLICT`
   （`human_affirm_deny_conflict`），零写入。
9. `expected_review_revision` 过期或候选集合已变化（新脸被后续检测删除）→ `FACE_MAPPING_STALE`
   （或对应行 `STALE`），零写入。
10. 同 `request_id` 同 payload 在任何 revision 检查前 replay；同 key 不同 payload
    `FACE_MAPPING_IDEMPOTENCY_CONFLICT` 且零写入。
11. 决策后被人工再次编辑该新脸，撤销整体 `FACE_MAPPING_OPERATION_NOT_UNDOABLE`；revision 不回退。
12. 重启、重扫、同内容重复检测后：`CONFIRMED`/`REJECTED` 不被重算清回 `OPEN`，同一对脸得到同一
    `mapping_id`。
13. 模型升级失败或回退后：旧世代锚点、人工锁与排除保留；旧会话按既有 stale 口径失效，不出现混版回答。
14. 隐私负向扫描：审核响应/错误/日志没有 bbox、landmark、IoU、向量、confidence、路径、坐标与内部快照。
15. `merge(target=高 id, source=低 id)` 撤销成功且每张脸始终至多一条 `CONFIRMED`；反向顺序、多源、
    带控制/排除同样成立（§10）。
16. 一个旧脸映射到两个新脸且两个 target 都 `inherits_person=true` → `VALIDATION_ERROR`
    （`multiple_targets_without_single_inheritor`）且零写入；只标一个继承者时另一个目标按 §4.3 保守处理
    并复制 exclusion（§4.1）。
17. 同内容同 `spec_revision` 重跑：零新脸、零映射行、零新 group；已 `CONFIRMED` 的 group 不被改写
    （§3.1、§7）。
18. 自映射（`old_face_id == new_face_id`）永不生成；提交这种对答 `FACE_MAPPING_NOT_FOUND`（§2.1）。
19. `OPEN` + `UNIQUE_HIGH` 跑完整自动关联遍：新脸零有效关联、零人工控制行，且该资产
    `face_association_queue` 行仍然存在（§3.4）。
20. `reject` 或“未选候选”释放保持后，仍为 `AUTO_ALLOWED` 且无有效关联的新脸能被随后的正常关联遍处理；
    决策事务内不得替它预选人物（§3.4、§5.3）。
21. 失效先于保持：候选新脸被后续重检测删除时映射行 `STALE`，该脸不会因为保持被当成不可删除的人工锚点
    而保留（§3.4、§7）。
22. 公开闭集负向：`details` 出现 `bbox`/`path`/`internal_snapshot`、`candidate_state=UNIQUE_HIGH` 同时给
    两个 `WEAK` 候选、`limit=999`、`next_cursor` 前缀不是 `fm1`，全部必须被拒绝（§8.1、§8.2）。

## 13. 后果与未验证项

- 本 ADR 不新增第四个 control mode，也不建立第二套 authority；代价是自动关联遍多一次“OPEN group 候选”
  读取，以及队列行在保持期内不被删除，收益是人工知识在 `face_id` 变化后不再静默丢失、且保持解除后仍有
  正常的自动处理机会。
- 30 万容量下的候选生成成本、审核队列长度、`0014` 迁移在真实数据上的耗时、多进程并发、C 级打包与真实
  GPU 升级/回退路径**均未验证**，属 P3-08B/C 与 C1 范围。
- 真实浏览器闭环在自动化工具落地前只是“记录的真实浏览器 smoke”，不得写成 Playwright/Selenium PASS。
- 低质量或几何极端的脸可能长期停在 `AMBIGUOUS`/`NO_CANDIDATE`；这是**有意的**人工确认成本，而不是
  自动代填。
