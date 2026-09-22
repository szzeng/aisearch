# P3-08A 阶段 B — 新旧 `face_id` 映射契约 Sol 聚焦终审

- 状态：**PASS / ADR-0010 ACCEPTED / P3-08A CLOSED**
- 日期：2026-09-22
- 实际模型：**C2 / GPT-5.6 Sol / high**，由用户在 Codex 中手动选择；未发生自动切换
- 审查基线：`7dd392d`；阶段 A 工作树产物未提交，实际改动面与阶段 A 报告一致
- 输入：阶段 A 报告 §9、ADR-0010、两份 companion、契约测试及任务卡点名的上位条款
- 范围：只审 authority 转移、事务/幂等/撤销、身份与升级恢复、API/错误/隐私闭集；未重复全仓 gap audit

## 1. 最终裁决

ADR-0010 **不能转 Accepted**，P3-08A **不能标 PASS**，P3-08B **不得生成**。阶段 A 的四条高风险边界中，
merge/undo 顺序缺陷已被正确纳入后续范围，但映射 authority、临时保持生命周期、稳定身份夹具与公开协议闭集
仍有四组普通且可界定的契约缺陷。它们会分别允许人工人物知识复制到多个新脸、让未确认候选绕过保持或在解除
保持后永久失去自动处理、要求实现一个与既有稳定 `face_id` 不相容的验收场景，以及让隐私字段进入错误
`details` 而仍通过契约测试。

这些缺陷不需要 DeepSeek max：问题均已由最小反例定位到 ADR/companion/contract test 的有限范围，没有命中
V3.1 §2.2 的跨模块未知根因、长状态机矛盾或多轮修复回归条件。按 V3.1 §2.3/§11 退回 DS-H 做契约修正；
修正后 Sol 只短审本报告列出的失败边界及直接回归面。

## 2. 发现一：`1 → N` 允许复制人工人物 authority

**结论：FAIL。** ADR-0010 §12 反例 3 与矩阵 `P3-08B-MAP-007` 都要求：一个人工归属旧脸映射到两个新脸时，
若两个 target 都给 `inherits_person=true`，必须 `VALIDATION_ERROR` 且零写入。可执行 companion 检查却只限制
“同一个新脸最多一个继承来源”，并在 `test_one_decision_per_old_face_and_one_inheritor_per_new_face()` 中明确把
“一个旧脸、两个不同新脸、两个 `inherits_person=true`”判为合法。

最小反例：

```text
old A = MANUAL_ASSIGNED/person P
confirm A -> [N1(inherits=true), N2(inherits=true)]

ADR §12 / MAP-007 期望：VALIDATION_ERROR，零写入
当前 contract_errors：[]（合法）
后果：P 被复制成 N1、N2 两条人工确认，违背 1→N 只允许一个继承者的保守转移规则
```

直接证据：ADR-0010 §4.1 只写了“每个新脸最多一个旧来源”，§12.3 又要求“每个旧 group 最多一个继承
target”；测试只实现了前者，并在 `tests/unit/test_p3_face_redetection_contracts.py:422`～`:425` 接受后者的
反例。DS-H 必须把两个方向同时冻结并测试：每个旧 group 至多一个 `inherits_person=true`，且每个新脸跨
group 至多一个继承来源。

## 3. 发现二：临时保持没有接到真实自动写门，解除后也没有恢复路径

**结论：FAIL。** ADR-0010 §3.4 与阶段 A 报告把 `human_controlled_face_ids()` 认定为自动路径的唯一入口，
并要求只扩展该函数即可挡住 OPEN group 的候选新脸。实际实现不是这样：

- `face/processor.py::persist_face_document()` 在删除旧检测行前调用 `human_controlled_face_ids()`；它是检测行
  保留门，不是自动人物关联的提交门；
- `face/index.py::_reconcile_asset()` 在人物关联写事务内调用的是另一个
  `_manually_locked_face_ids()`，且完全不调用 `human_controlled_face_ids()`；
- reconcile 循环结束后无条件删除该资产的 `face_association_queue` 行。

因此仅按当前 ADR 修改 `human_controlled_face_ids()`，OPEN 候选仍可被 `_reconcile_asset()` 自动建立有效
关联，直接违反“未确认映射不得自动转移/填回”。若实现者额外修补真实写门，让它跳过 OPEN 候选，队列仍会被
删除；之后 `reject`、未选候选释放保持或 undo 回到 OPEN/再决策时，ADR 又没有冻结保留、延迟或重新排队
`face_association_queue` 的规则，这些新脸可能永久不再走正常自动路径。

还存在直接的失效顺序歧义：把 OPEN 候选加入 `human_controlled_face_ids()` 会让 `_retire_stale_faces()` 把它
视为不可删除的“受保护脸”，而 ADR §7 / `P3-08B-MAP-013` 又要求后续重检测删除消失的非人工候选并把映射置
`STALE`。契约必须明确先判定候选失效、再决定保留锚点的顺序，不能让同一 helper 同时表达“人工锚点”和
“临时禁止自动关联”。

DS-H 最小修正范围：冻结一个在自动关联写事务内复核的 OPEN-hold 谓词；冻结 queue 在 hold、confirm、reject、
undo、候选失效后的保留/重排队规则；区分“检测行保留”与“自动关联禁止”两种集合；增加至少三条直接反例：
OPEN + UNIQUE_HIGH 跑完整 association 仍零有效关联、reject/未选候选解除后可再次自动处理、消失候选能置
STALE 而不被临时保持永久保留。

## 4. 发现三：同内容同 spec 的 `MAP-001` 与稳定 `face_id` 冲突

**结论：FAIL。** `P3-08B-MAP-001` 的夹具是“同内容、同 `spec_revision` 再次检测出同一张脸”，却要求
“旧脸行保留 + 新脸写入 + OPEN/UNIQUE_HIGH 映射”。既有 `face_id_for()` 明确由相同 content revision、
spec revision、ordinal 和 bbox 确定；同一检测文档重跑得到相同 `face_id`，`persist_face_document()` 是
upsert，不会产生第二张新脸。现有 P3-02 回归也固定“rerun creates no second face/person/artifact”。

直接探针：

```text
same input_revision + ordinal + bbox + spec_revision
=> same_face_id=True, unique_ids=1
```

当前契约既没有禁止 `old_face_id == new_face_id` 的自映射，也没有说明“同 spec 但几何/ordinal 变化导致真的
产生不同 face_id”才进入映射。DS-H 必须把 unchanged rerun 冻结为零映射、零新 group；为同 spec 的真实
`face_id` 变化另建明确夹具，并在身份/候选生成规则中拒绝 self-mapping。

## 5. 发现四：API/error/details 与候选状态不是闭集

**结论：FAIL。** 阶段 B 必审项明确要求 API/error/details/隐私闭集足以直接生成 P3-08B，但 companion 仍接受
以下三种违反 ADR 的对象：

1. `MappingApiError.details` 仅声明为任意 object；加入 `bbox`、本地 `path`、`internal_snapshot` 后仍合法；
2. `candidate_state=UNIQUE_HIGH` 同时返回两个 `WEAK` 候选仍合法；现有跨项检查只处理
   `NO_CANDIDATE`、计数和重复；
3. `MappingPage.limit=999`、`next_cursor="not-fm1"` 仍合法，虽 ADR 冻结 limit 1..200 与独立 `fm1` 游标空间。

同一直接探针的输出为：

```text
contradictory_group_errors=[]
private_error_details_errors=[]
unbounded_page_errors=[]
```

DS-H 必须为每个错误码冻结安全 details 形状（或严格 oneOf）、补全 UNIQUE_HIGH/UNIQUE_WEAK/AMBIGUOUS 与候选
数组的交叉约束，并把 limit/cursor 上界真正接到公开 schema；相应负向 contract cases 必须在修复前失败、
修复后通过。阶段 A 报告 §10 还把实际 56 条矩阵写成 59 条，应同步纠正，但该计数笔误本身不是本轮 FAIL 根因。

## 6. 已执行证据

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-sol-review.xml` | **33 tests / 0 failures / 0 errors / 0 skipped** |
| 同输入 `face_id_for()` 直接探针 | `same_face_id=True`，唯一 ID 数为 1 |
| companion 直接负向探针 | 四类反例均返回空错误列表，证明现有 33 例未覆盖本报告缺陷 |
| Git 基线/范围 | HEAD `7dd392d`；未改 `src/photo_search/**`、migration、依赖、前端或既有测试语义 |

契约测试全绿只证明当前文档与当前断言一致；发现一中断言本身选择了与 ADR §12 相反的分支，发现四则缺少
负向断言，因此不能据此冻结。

## 7. 退回 DS-H 的最小修改与停止点

只允许修正：

- `docs/decisions/ADR-0010-face-redetection-mapping-and-p3-acceptance-contract.md`；
- `docs/decisions/contracts/p3-face-redetection-contracts.v1.json`；
- `docs/decisions/contracts/p3-acceptance-matrix.v1.json`；
- `tests/unit/test_p3_face_redetection_contracts.py`；
- 阶段 A/修复报告、`docs/tasks/P3-08A.md` 与 `docs/development-status.md` 的交接元数据。

不得实现 `src/photo_search/**`、`0014`、前端或 P3-07B merge/undo 修复，不得把 ADR 标为 Accepted，不得生成
P3-08B。DS-H 修复后需重跑任务卡 A 级命令并给出每条失败边界的“修复前反例 / 修复后断言”。下一轮 Sol 只
短审：双向继承基数、hold/queue/stale 生命周期、unchanged/self-mapping 身份边界、公开 schema 闭集及其
直接 contract tests；通过后才能完成 P3-08A。

## 8. DS-H 修复后的 Sol 短审补轮

**结论：仍为 FAIL。** 第一轮四条边界中，双向继承基数与 unchanged/self-mapping 身份边界已经闭合；
`candidate_state`、分页上限与 `fm1` 游标也已形成可执行约束。但 hold/queue 的 `undo → OPEN` 语义仍在 ADR
内部自相矛盾，error `details` 仍只是“所有安全键的并集”，并没有实现 ADR §8.2 声称的逐错误码闭集。
因此 ADR-0010 仍为 Proposed，P3-08A 不得标 PASS，P3-08B 不得生成。

### 8.1 阻塞一：`undo → OPEN` 同时被定义为释放与重新施加保持

ADR §3.4 的释放条款把“撤销回 `OPEN`”列为**释放保持**，要求释放后的 `AUTO_ALLOWED` 新脸重新排队并由
正常关联遍处理；同一 ADR §5.3、§12.20 以及矩阵 `P3-08B-MAP-032` 却要求撤销回 `OPEN` 后**重新施加保持**，
并禁止新脸被自动归类。两种结果不能同时成立：若 P3-08B 按 §3.4 的“释放”实现，紧随其后的关联遍可写入
有效人物关联，直接违反 MAP-032；若按 MAP-032 实现，§3.4 的释放清单就是错误的实现指令。

最小修正：把 §3.4 的释放集合限定为 `confirm` / `reject`（以及 confirm 未选择的候选），把
`undo → OPEN` 单独定义为“恢复 OPEN 保持 + 保留/重新排队，但关联遍继续跳过保持脸”。同步修正 DS-H 修复
报告中“确认、拒绝、undo 释放”的同类表述，并增加一条静态契约断言，避免以后再次把 undo 放进释放集合。

### 8.2 阻塞二：错误 `details` 没有逐码闭合

companion 的 `MappingErrorDetails` 在一个公共对象里允许全部安全键，`MappingApiError.allOf` 只按 code 增加
必填键，没有按 code 禁止其他安全键，也没有为 `FACE_NOT_FOUND` 冻结“old/new 至少一个”的必填关系。
因此以下三个违反 ADR §8.2 表格的对象仍被 `contract_errors(..., "MappingApiError")` 判为合法：

```text
FACE_MAPPING_NOT_FOUND details={old_face_id, person_id} → []
FACE_NOT_FOUND         details={}                      → []
INVALID_CURSOR         details={old_face_id}           → []
```

这里没有泄露 bbox/path，但仍破坏公开错误协议的**逐码**闭集：客户端无法依赖每个 code 的固定形状，且实现可把
其他错误上下文串入当前错误。现有 `test_error_code_set_and_envelope_are_closed()` 只验证必填键与未知 code，
`test_error_details_reject_private_keys()` 只验证全局禁止的私有键，所以 43 例全绿未覆盖这个反例。

最小修正：把 `MappingApiError` 改成按 code 分支的严格 `oneOf`（或等价的逐分支 details schema），每个分支
同时冻结允许键与必填键；为 `FACE_NOT_FOUND` 表达 `old_face_id` / `new_face_id` 至少一个，为
`INVALID_CURSOR` 表达空对象。新增跨码安全键负向用例和 `FACE_NOT_FOUND {}` 负向用例。

## 9. 短审补轮证据与停止点

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-sol-rereview.xml` | **43 tests / 0 failures / 0 errors / 0 skipped** |
| 逐码 `details` 三个直接探针 | 全部返回 `[]`，复现逐码闭集缺口 |
| ADR / matrix 交叉核验 | §3.4 的 undo 释放与 §5.3 / MAP-032 的 undo 保持直接冲突 |
| `ruff check tests/unit/test_p3_face_redetection_contracts.py` | clean |
| `git diff --check` | clean |
| `uv lock --check` | exit 0，95 packages |

本补轮不升级 DeepSeek max：两条问题都由有限文档/companion 反例精确定位，没有新的跨模块未知根因。下一轮
DS-H 只修上述两点及直接测试/交接元数据；不得修改生产代码、migration、依赖、前端，不得把 ADR 转
Accepted，不得生成 P3-08B。修复后 Sol 只复核这两个反例。

## 10. 第二轮修复后的最终短审：PASS

**最终裁决：PASS。** 本轮严格只复核 §8 的两个残留阻塞及正向对照，没有重复全仓审查。第二轮 DS-H 修正
已消除两处矛盾：

1. ADR §3.4 的释放集合现在只含 `confirm` / `reject`（含 confirm 未选候选）；`undo → OPEN` 被单独冻结为
   恢复保持并保留/重新排队，紧随其后的关联遍继续跳过保持脸。该语义与 §5.3、§12、MAP-032 一致。
2. `MappingApiError` 已改为 12 个严格 `oneOf` 分支，每个 code 独立关闭允许键和必填键；
   `FACE_NOT_FOUND` 用 `anyOf` 要求 old/new 至少一个，`INVALID_CURSOR` 只接受空对象。

独立直接探针结果：

```text
FACE_MAPPING_NOT_FOUND + person_id → ['$: oneOf matched 0']
FACE_NOT_FOUND {}                  → ['$: oneOf matched 0']
INVALID_CURSOR + old_face_id       → ['$: oneOf matched 0']
FACE_NOT_FOUND {old_face_id}       → []
INVALID_CURSOR {}                  → []
```

最终 A 级验证：

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-sol-final-rereview.xml` | **58 tests / 0 failures / 0 errors / 0 skipped** |
| `ruff check tests/unit/test_p3_face_redetection_contracts.py` | clean |
| `git diff --check` | clean |
| `uv lock --check` | exit 0，95 packages |

ADR-0010 已转 **Accepted**，契约测试的状态断言同步更新并复跑通过。P3-08A 至此关闭；未改生产代码、migration、
依赖、前端或真实照片库。下一任务为 `P3-08B`：按冻结件实施 mapping、修复 merge/undo 顺序缺陷、完成 65 条
矩阵对应的 D 级故障验收与记录的真实浏览器闭环；推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**，模型切换
仍由用户在 Codex 中手动完成。
