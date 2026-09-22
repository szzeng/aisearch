# P3-08A 阶段 B 修复 — 对 Sol 四条失败边界的 DS-H 契约修正

- 状态：**CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING**（P3-08A 仍未 PASS；ADR-0010 仍为 Proposed）
- 日期：2026-09-22
- 实际模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**，由用户在 Codex 中手动选择；未发生自动切换
- 起点基线：`7dd392d`（阶段 A 工作树产物仍未提交；阶段 B 只改契约与交接元数据）
- 输入：[阶段 B Sol 终审报告](P3-08A-sol-contract-freeze.md)（`FAIL / DS-H CONTRACT CORRECTION REQUIRED`）
- 改动面（严格限于 Sol §7 允许集合）：`ADR-0010`、两份 companion、契约测试、报告、任务卡与状态

**是否升级 DeepSeek max：否。** 四条问题都在 ADR/companion/contract test 的有限范围内由最小反例定位
（V3.1 §2.2 六类条件逐条未命中），按 §2.3 交回 DS-H 做契约修正即可。

## 1. 修复总览

| Sol 发现 | 修复位置 | 修复后断言（可执行证据） | 修复前反例现状 |
|---|---|---|---|
| ①`1 → N` 可复制人工人物 authority | ADR §4.1 双向基数；companion 跨项规则；`test_one_decision_per_old_face_and_one_inheritor_per_new_face` | `request: at most one inheritor per old face` | 现在被拒（Sol §5 原为 `[]`） |
| ②保持没接到真实自动写门、队列生命周期未冻结 | ADR §3.4（两谓词分离 + 判定顺序 + 队列保留/重排队）、§5.3、§7、§9；矩阵 MAP-029～032 | 矩阵新增 5 条（含队列行保留与释放后重排队） | 契约级修复；实现与回归属 P3-08B |
| ③`MAP-001` 与稳定 `face_id` 冲突、未拒自映射 | ADR §2.1、§3.1、§7；矩阵 MAP-001 改为跨世代 + 新增 MAP-027/028 | `自映射不是映射`、`零新脸、零新映射行、零新 group` | `MAP-001` 不再声称未变化重跑会开 group |
| ④公开 schema 不是闭集 | ADR §8.1/§8.2；companion `MappingErrorDetails`/`allOf`/`Cursor`/`limit.maximum` | `$.details: unexpected bbox`；`group: UNIQUE_HIGH needs exactly one HIGH candidate`；`$.limit: maximum`；`page: next_cursor must decode to fm1:<old_face_id>` | 四类矛盾形状现在全部被拒（Sol §5 原为 `[]`） |

以下每节按 Sol 要求给出“修复前反例 / 修复后断言”。

## 2. 发现一：继承基数是双向的

**修复前反例（Sol §5 探针输出，逐字保留）**：

```text
old A = MANUAL_ASSIGNED/person P
confirm A -> [N1(inherits=true), N2(inherits=true)]
ADR §12 / MAP-007 期望：VALIDATION_ERROR，零写入
当前 contract_errors：[]（合法）
```

**修复。** ADR §4.1 改成一条双向基数规则：每个 group（旧脸）至多一个 `inherits_person=true`
（`multiple_targets_without_single_inheritor`），每个新脸跨 group 至多一个继承来源
（`multiple_inheritors_for_new_face`）；旧脸是 `MANUAL_ASSIGNED` 时允许零个继承者（用户明确不转移肯定
知识，目标按 §4.3 保守处理），但不允许两个及以上。companion 的 `DecisionRequest` 跨项规则同时实现两条
基数检查（JSON Schema 无法表达计数语义，因此由契约测试执行）。

**修复后断言（本机实测）**：

```text
1 per-group two inheritors: ['request: at most one inheritor per old face']
```

以及正向对照：`1 旧脸 → 2 新脸` 只标一个继承者 → 通过；两个都标 false → 通过；`2 旧脸 → 1 新脸` 只标
一个继承者 → 通过（`test_one_decision_per_old_face_and_one_inheritor_per_new_face`）。

## 3. 发现二：保持接到真实自动写门，队列生命周期显式冻结

**修复前反例（Sol §3 的只读定位，本机复核成立）**：

```text
face/processor.py::persist_face_document() 在读 human_controlled_face_ids() 之后删除旧检测行
face/index.py:791  _reconcile_asset() 的写事务内读的是 _manually_locked_face_ids()（mode-only）
face/index.py:937  关联遍末尾无条件 DELETE FROM face_association_queue WHERE asset_id = ? AND spec_revision = ?
```

结论：只扩 `human_controlled_face_ids()` 挡不住自动关联；而且即使挡住，队列行仍被删除，`reject`/未选候选/
undo 之后新脸可能永久不再走正常自动路径。

**修复（契约级，P3-08B 按其实现）**：

1. ADR §3.4 明确**两个谓词必须分开**：`检测行保留集合`（只在 `persist_face_document()` 的删除门读）
   与 `自动关联保持集合`（只在 `_reconcile_asset()` 的 `BEGIN IMMEDIATE` 内、与
   `_manually_locked_face_ids()` 同一事务同一时点读）。保持**不得**让检测行变成不可删除的“人工锚点”。
2. ADR §3.4 冻结判定顺序：写/退休检测行 → 计算候选 group → **先判失效**（映射行 `STALE`）→ 再算保持集合；
   另加 §7 的崩溃/重启条款。
3. ADR §3.4 冻结 `face_association_queue` 生命周期：保持期内**保留/回写**队列行；
   释放（只有 `confirm` / `reject` 与 `confirm` 未选候选，见 §11 的第二轮修正）时保证仍为 `AUTO_ALLOWED`
   且无有效关联的新脸所在资产**已被（重新）
   排队**；决策事务本身不替用户选人物；幂等 replay/no-op 不改队列。
4. ADR §5.3 增加“保持与队列效应”表（confirm/reject/undo/失效四类结果），§9 把 `face/index.py` 列入
   P3-08B 修改面。
5. 矩阵新增并更新：`MAP-029`（保持挡住写门且队列行仍在）、`MAP-030`（释放后重新排队并被正常处理）、
   `MAP-031`（失效先于保持）、`MAP-032`（undo 回 `OPEN` 重新施加保持并重排队）、`MAP-013`
   （候选失效置 `STALE`）。这些是 A 级契约条目，**执行与回归属 P3-08B**，本轮不实现代码。

## 4. 发现三：身份边界（自映射、未变化重跑、同 spec）

**修复前反例（Sol §4 的探针 + 本机只读复核）**：`MAP-001` 用“同内容同 `spec_revision` 再检测出同一张脸”
却要求“新脸写入 + OPEN group”。而 `face_id_for()` 由 `(spec_revision, input_revision, ordinal, bbox)`
唯一决定，`persist_face_document()` 在 `assets.sha256 != result.input_revision` 时直接拒绝，
`_upsert_face()` 只 upsert，`_free_ordinals()` 只让新脸避开**存活且不在本次文档里**的 ordinal——
所以同一资产、同一内容、同一 spec 只对应一份合法文档，重跑必然同 ID：**不可能**产生第二张新脸。

**修复。**

1. ADR §2.1 增加“**自映射不是映射**”：`old_face_id == new_face_id` 永不生成，提交答
   `FACE_MAPPING_NOT_FOUND`，零写入。
2. ADR §3.1 明确 **v1 的唯一生成面是跨世代**（`old_spec_revision != new_spec_revision`，覆盖升级、回退
   与回退后写回的旧世代锚点）；**未变化重跑产生零新脸、零映射行、零新 group**；并冻结“v1 不定义同 spec
   的新旧映射”：同 spec 但 ordinal/bbox 不同的文档是版本纪律问题，按既有 `ARTIFACT_INVALID` /
   `STALE_INPUT_REVISION` 词表拒绝，不得因此建映射。要区分“旧脸被重新编号”与“模型换代”，只看
   `spec_revision`，不看 ordinal 或几何接近度。
3. ADR §7 的“重复检测”与“候选失效”条目按上面重写。
4. 矩阵：`MAP-001` 夹具改为“S1 人工锚点 × S2 新世代检测 → OPEN/UNIQUE_HIGH”，标题改为“跨世代重检测为
   人工锚点生成候选”；新增 `MAP-027`（未变化重跑零映射、已 `CONFIRMED` 不被改写）与 `MAP-028`
   （自映射被拒）。

## 5. 发现四：公开 schema 成为闭集

**修复前反例（Sol §5 探针输出，逐字保留）**：

```text
contradictory_group_errors=[]
private_error_details_errors=[]
unbounded_page_errors=[]
```

**修复。**

1. **错误 details 闭集**：companion 新增 `MappingErrorDetails`（`additionalProperties: false`，只允许
   受控 ID、revision、枚举、短 `reason`、`max_decisions`/`max_targets` 与 `MappingConflict[]`）与
   `MappingConflict`；`MappingApiError` 用 `allOf if/then` 为每个错误码冻结必填键；ADR §8.2 增加逐码
   “允许键 / 必填键”表与 `conflicts` 形状（1..64 项、只含 `entity_type`/`entity_key`/`reason`）。
2. **候选状态闭集**：`MappingGroup` 用 `allOf if/then` 冻结 `candidate_state` 与候选数组、
   `candidate_count` 的一致性（`NO_CANDIDATE` 0 个、`UNIQUE_HIGH` 恰好 1 个 `HIGH`、`UNIQUE_WEAK`
   恰好 1 个 `WEAK`、`AMBIGUOUS` ≥2 个）；`MappingGroupSummary` 冻结 `candidate_state` ↔
   `candidate_count`。
3. **分页闭集**：`MappingPage.limit` 为 `1..200`（`maximum` 真正进入公开 schema）、`next_cursor` 使用
   新增 `Cursor`（base64url、无填充），契约测试额外解码校验固定为 `fm1:<old_face_id>`；ADR §8.1 明确
   越界 limit 答 `VALIDATION_ERROR`、外来/非法游标答既有 `INVALID_CURSOR`（因此 companion 错误闭集
   增加该既有码，不是新造码）。
4. 契约测试新增：`test_error_code_set_and_envelope_are_closed`（12 个码逐个必填键 + 缺键被拒）、
   `test_error_details_reject_private_keys`、`test_undoable_conflicts_are_bounded_and_typed`、
   `test_pagination_bounds_and_cursor_space_are_enforced`、`test_candidate_state_and_candidate_list_agree`
   （四态正反例）。

**修复后断言（本机实测）**：

```text
2 private details: ['$.details: unexpected bbox']
3 contradictory candidates: ['$.candidate_count: const', '$.candidates: too many',
   '$.candidates[0].match_state: const', 'group: UNIQUE_HIGH needs exactly one HIGH candidate']
4 page bounds: ['$.limit: maximum', 'page: next_cursor must decode to fm1:<old_face_id>']
```

## 6. 计数与文件清单更正

- Sol §5 指出的笔误已修：阶段 A 报告原先在一处写“59 条”，实际为 **56 条**（该报告 §9 已为 56）。
- 本修复轮把矩阵扩到 **65 条**（`existing 16 / must_rerun 11 / not_implemented 38`），契约测试从 33 例
  扩到 **43 例**；两者由契约测试持续核对，不接受“未跑即 PASS”。

## 7. 实测命令与结果

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-contract-fix.xml` | **43 tests / 0 failures / 0 errors / 0 skipped**（exit 0） |
| `test-results/p3-08a-unit.xml` | **802 tests / 0 failures / 0 errors / 0 skipped**（完整 `tests/unit`，exit 0；阶段 A 为 792，+10 为新增契约用例） |
| `ruff check tests/unit/test_p3_face_redetection_contracts.py` | clean（exit 0；首轮发现 import 顺序 I001，已修正后复跑通过） |
| `git diff --check` | clean（exit 0） |
| `uv lock --check` | exit 0，95 packages |
| 四条反例探针 | 全部被拒（§2、§5 输出） |

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p3_face_redetection_contracts.py `
  --basetemp=.pytest-tmp/P3-08A-fix `
  --junitxml=test-results/p3-08a-contract-fix.xml
uv run --no-sync ruff check tests/unit/test_p3_face_redetection_contracts.py
git diff --check
uv lock --check
```

未改 `src/photo_search/**`、`catalog/migrations/**`、既有 ADR/companion、依赖、`PhotoSearch.spec`、前端与既有
测试语义；`.pytest-tmp` 对受限令牌仍为 `WinError 5`（既有环境事实），完整单测经已授权的正常 Windows 权限
路由执行，未改任何 ACL，未触碰真实照片库，未下载模型。

## 8. 未验证项

- 保持谓词与队列保留规则的**真实实现**、`0014` 迁移、D 级故障全集与真实浏览器闭环：属 P3-08B。
- 30 万容量下候选生成成本、多进程并发：未测（P4-01/P4-02、P3-08B 范围）。
- 真实权重下的升级/回退与 C 级打包：属 C1/P3-08B。

## 9. 给 Sol 的短审交接包（只审四条失败边界 + 直接回归）

```text
基线 / 改动：仍以 7dd392d 为基线；本轮只改 ADR-0010、p3-face-redetection-contracts.v1.json、
            p3-acceptance-matrix.v1.json（56 → 65 条）、tests/unit/test_p3_face_redetection_contracts.py
            （33 → 43 例），以及报告/任务卡/状态元数据。未改 src/photo_search/**、migration、依赖、前端。

① 双向继承基数：ADR §4.1（multiple_targets_without_single_inheritor + multiple_inheritors_for_new_face）
   → 测试 test_one_decision_per_old_face_and_one_inheritor_per_new_face；探针输出见本报告 §2。
② 保持/队列生命周期：ADR §3.4（两谓词分离、判定顺序、队列保留与重排队）、§5.3、§7、§9
   → 矩阵 MAP-029/030/031/032（+MAP-013）；事实依据 face/index.py:791 与 :937 的只读定位。
③ 身份边界：ADR §2.1（自映射不是映射）、§3.1（跨世代唯一生成面、未变化重跑零映射、v1 不定义同 spec
   映射）、§7 → 矩阵 MAP-001 改写 + MAP-027/028。
④ 公开闭集：ADR §8.1/§8.2、companion MappingErrorDetails/MappingConflict/Cursor/limit.maximum
   → 测试 test_error_code_set_and_envelope_are_closed、test_error_details_reject_private_keys、
   test_undoable_conflicts_are_bounded_and_typed、test_pagination_bounds_and_cursor_space_are_enforced、
   test_candidate_state_and_candidate_list_agree。

实测：契约 43/0/0/0；完整 tests/unit 802/0/0/0；Ruff / git diff --check / uv lock --check 全 clean。
禁止扩大：不得要求本轮实现 src/**、0014、前端或 P3-07B merge/undo 修复；不得把 ADR 标 Accepted；
          不得生成 P3-08B；不得改写 ADR-0006～0009 已冻结语义。
通过后：ADR-0010 转 Accepted、写 P3-08A-sol-contract-freeze.md 的补轮结论、P3-08A 标 PASS、生成 P3-08B；
        不通过：只输出最小反例与受影响边界，交回 DS-H。
```

## 10. 阶段交接

```text
任务 ID / 状态：P3-08A / 阶段 B 修复完成 —— CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING
ADR 状态：Proposed（未转 Accepted）
冻结件：ADR-0010、p3-face-redetection-contracts.v1.json、p3-acceptance-matrix.v1.json（65 条）、
        tests/unit/test_p3_face_redetection_contracts.py（43 例）
实测：test-results/p3-08a-contract-fix.xml = 43 / 0 / 0 / 0
      test-results/p3-08a-unit.xml        = 802 / 0 / 0 / 0
      Ruff / git diff --check / uv lock --check（95 packages）全 clean
max：未升级（四条问题均可由最小反例界定）
下一步：独立新会话 C2 / GPT-5.6 Sol / high 只短审四条失败边界及直接回归（输入为本报告 §9）
模型操作：请在 Codex 中手动切换模型/推理档后继续（不得自动切换或声称已切换）
```

> 注：§10 记录的是**第一轮**交接（契约 43 例）。Sol 短审补轮发现的两个残留阻塞与第二轮修正见 §11
> （契约 58 例、完整 `tests/unit` 817 例）。

## 11. 第二轮修复：Sol 短审补轮的两个阻塞

输入：[阶段 B 报告 §8～§9](P3-08A-sol-contract-freeze.md)（`SOL SHORT RE-REVIEW FAIL /
SECOND DS-H CONTRACT CORRECTION REQUIRED`）。第一轮的四条边界中，双向继承基数、
unchanged/self-mapping 身份、`candidate_state`/分页/游标均已判为闭合；本轮只修两个残留阻塞。

### 11.1 阻塞一：`undo → OPEN` 曾同时被写成“释放”和“重新施加保持”

**修复前反例（Sol §8.1）**：ADR §3.4 的队列条款把“撤销回 `OPEN`”列进释放集合（释放后允许被正常自动
处理），而 §5.3、§12.20 与矩阵 `MAP-032` 又要求撤销回 `OPEN` 后**重新施加保持**并禁止自动归类——两种
实现指令互斥。

**修复（ADR §3.4）**：释放集合显式收窄为
“**释放集合只有 `confirm` / `reject`**（以及 `confirm` 未选择的候选）”，并新增独立条款
“**`undo` 回到 `OPEN` 不是释放**”：`undo` 恢复 `OPEN` 保持（该 group 的候选新脸重新被关联遍跳过），
同时只保留/重新排队队列行等待下一次决策，绝不允许紧随其后的关联遍给保持脸写有效关联。本报告 §3 第 3 点
的同类表述同步更正。

**修复后断言**：`test_undo_is_never_part_of_the_release_set`（新增）——释放条款切片内不得出现 `undo` 或
“撤销”，且 ADR 必须同时包含两条冻结措辞与“该 group 的候选新脸重新被关联遍跳过”。

### 11.2 阻塞二：错误 `details` 只有“并集 + 必填”，没有逐码闭集

**修复前反例（Sol §8.2，本机复现的同一形状）**：

```text
FACE_MAPPING_NOT_FOUND details={old_face_id, person_id} → []
FACE_NOT_FOUND         details={}                       → []
INVALID_CURSOR         details={old_face_id}            → []
```

**修复（companion）**：删除 `MappingErrorDetails`（“所有安全键的并集”）与只追加必填键的 `allOf`，把
`MappingApiError` 改成 **12 个逐码分支的严格 `oneOf`**：每个分支固定 `code` 并各自声明**允许键 + 必填键 +
`additionalProperties: false`**；`FACE_NOT_FOUND` 用 `anyOf` 表达 `old_face_id` / `new_face_id` 至少一个；
`INVALID_CURSOR` 的 `details` 是空对象（连安全 ID 都不允许）。“跨码串键”因此落到零个匹配分支。ADR §8.2
同步写明禁止用“并集 + 追加必填键”的弱形状代替。

**修复后断言（本机实测）**：

```text
A FACE_MAPPING_NOT_FOUND +person_id: ['$: oneOf matched 0']
B FACE_NOT_FOUND {}:                 ['$: oneOf matched 0']
C INVALID_CURSOR +old_face_id:       ['$: oneOf matched 0']
D positive FACE_NOT_FOUND old:       []
E positive INVALID_CURSOR {}:        []
```

契约测试新增/扩展：`test_error_code_set_is_the_closed_one_of_branches`、
`test_error_details_are_closed_per_code`（逐码：合法样本通过、注入**其它码的安全键**被拒、逐个抽掉必填键被
拒）、`test_face_not_found_needs_at_least_one_of_the_two_ids`、`test_invalid_cursor_details_must_be_empty`，
以及更新后的 `test_adr_freezes_the_public_closed_sets`。

### 11.3 第二轮实测

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-contract-fix2.xml` | **58 tests / 0 failures / 0 errors / 0 skipped**（exit 0；第一轮 43 → +15） |
| `test-results/p3-08a-unit.xml` | **817 tests / 0 failures / 0 errors / 0 skipped**（完整 `tests/unit`，exit 0） |
| `ruff check tests/unit/test_p3_face_redetection_contracts.py` | clean（exit 0） |
| `git diff --check` | clean（exit 0） |
| `uv lock --check` | exit 0，95 packages |
| Sol 的三个反例探针 | 全部被拒（§11.2 输出），正向对照仍通过 |

矩阵仍为 65 条（本轮未增删场景，只改 ADR/companion 与契约测试）。未改 `src/photo_search/**`、migration、
依赖、`PhotoSearch.spec`、前端与既有测试语义；未改 ACL，未触碰真实照片库。

### 11.4 第二轮交接

```text
状态：CORRECTIONS APPLIED（第二轮）/ SOL SHORT RE-REVIEW PENDING
起点：仍以 7dd392d 为基线，工作树包含阶段 A 与两轮修复的全部未提交产物
本轮改动：ADR-0010（§3.4 释放/undo、§8.2 逐码 oneOf）、
          p3-face-redetection-contracts.v1.json（MappingApiError 严格 oneOf + anyOf）、
          tests/unit/test_p3_face_redetection_contracts.py（43 → 58 例）、本报告 / 任务卡 / 状态元数据
实测：契约 58 / 0 / 0 / 0（p3-08a-contract-fix2.xml）；完整 tests/unit 817 / 0 / 0 / 0；
      Ruff / git diff --check / uv lock --check（95 packages）全 clean
max：未升级（两点均有最小反例，未命中 V3.1 §2.2）
下一步：C2 / GPT-5.6 Sol / high 只复核 §11.1 与 §11.2 两个反例（含正向对照）
禁止：不得要求本轮实现 src/**、0014、前端或 merge/undo 修复；不得把 ADR 转 Accepted；不得生成 P3-08B
```
