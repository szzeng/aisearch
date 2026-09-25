# P4-02H — Sol 阻断项修复与压缩交接包

对应 [P4-02H 聚焦审核](P4-02H-focused-contract-review.md)（退回：F1/F2 + 两项终审前置缺失）。
被审基线 `77557db`；本轮**只改 ADR-0012、companion、契约用例与报告/状态**，产品代码零改动。
维持 `A3 未实现/未启用`、`§12.2 未改`、`P4-02F 非 PASS`。

## 1. F1 修复：`place_state` 独立于 `place_ids`

- **根因确认（读产品代码）**：`places/filter.py::place_condition` 在 `place_ids=()` 时，
  `assigned`/`unassigned`/`manual_unassigned`/`suggested` 仍返回非恒真谓词，只有 `any` 返回 `"1"`；
  而 `scope_condition` 只在谓词非 `"1"` 时才并入条件 —— 所以「列表为空」不等于「无筛选」。
- **契约修正**：ADR §2.2 改为**逐字段核对并由真实谓词是否恒真决定**，显式加入 `place_state == "any"`；
  companion `EligibilityRule` 新增必填 `place_state_is_any`；矩阵新增第 10 行
  `place_state_filter` ⇒ 不资格（`NOT_ELIGIBLE_EXTRA_SCOPE`）。
- **直接反例（真实代码，可执行）**：`test_real_place_state_makes_the_scope_predicate_non_tautological`
  调用真实 `scope_condition(SearchScope.build(place_state=…))`，断言只有 `any` 得到恒真 `"1"`，
  并断言 `place_state_is_any=False` 时即使证书成立也必须回退。

## 2. F2 修复：把 oracle 成员集与证书资格集分开

- **根因确认（读产品代码 + 真实调用）**：`_CONFIRMED_BLOCK_SQL` 只约束
  generation/space/`CONFIRMED`/`scope_condition`，不比较 `index_entries.confirmation_revision`
  与 Lance `projection_revision`；`availability=any` 时也不追加可见性条件。故「最新 revision 资格」
  与「现行 oracle」不是同一个集合 —— 原 ADR 把两者混称 `C`，导致自相矛盾。
- **契约修正**：§1/§3 拆成 `C_sql`（oracle 成员集，等于现行 SQL 的选择）与 `C_strict`
  （`C_sql` + 行级一致：每代每资产一行、space 匹配、行 `projection_revision` = 当前
  `confirmation_revision`）；§4 的 M1/M2/M3 **只在 `C_strict` 上评估**；§3.3 明确
  `C_strict ≠ C_sql`（含 `after_lance` 窗口）⇒ 强制回退，而回退页就是 oracle 页；
  并写入「本 ADR 不修改完整路径的 revision-blind 行为，收紧它须另行裁决立卡」。
  companion 的 `AuthoritySetRule` 相应改为 7 个冻结布尔（含 `mismatch_forces_fallback`），
  矩阵新增第 11 行 `row_revision_mismatch`。
- **直接反例（真实代码，可执行）**：
  `test_real_oracle_computes_c_sql_and_c_strict_for_both_window_shapes` 用**真实 `vector_channel`**
  ＋**真实 `scope_condition`**＋内存权威表＋替身索引，**显式计算 `C_sql`/`C_strict`** 两种窗口形状：
  ①防御性形状（`CONFIRMED` 条目的引擎行 revision 不一致）⇒ `C_sql={1,2}`、`C_strict={2}` ⇒ 回退；
  ②**真实 `after_lance` 形状**（条目已降为 `PENDING_REPAIR`、引擎已写新行）⇒ `C_sql == C_strict == {1,2}`
  且多余引擎行 `{3}` 使证书失败 ⇒ 同样回退。oracle 仍返回 `hits=[A(revision=2)]`，证明其 revision-blind。

### 2.1 短审修正（Sol 聚焦短审 F2：归因错误，已改）

原 §1/§2 把「`CONFIRMED` 条目 × 更新的引擎行」标为 `after_lance` 崩溃窗口，**与真实状态机不符**：
发布/重排程路径**先**把条目降为 `PENDING_REPAIR`，`process_next(failpoint="after_lance")` 抛出时条目
仍是 `PENDING_REPAIR`，该资产**既不在 `C_sql` 也不在 `C_strict`**（两集相等），回退由「引擎有行、
oracle 没有」触发。该结论由 [独立复跑](P4-02H-independent-recheck.md) §3 的 5 步真实交织
（每步 `C_sql == C_strict`）与仓库既有用例
`tests/integration/test_p2_search_topup.py`（断言 `entry_state == "PENDING_REPAIR"`）共同证实。
现 ADR §3.3/§6 已按此改写：第 1 行承载真实窗口，第 3 行降级为**防御性**状态
（越序写入/损坏/外部替换；现行 `IndexCoordinator` 不产生），并明确 `C_strict` 的逐行 revision 比较
在现行状态机下是**空转**、只作证书不许依赖的假设保留；§4 的 M2/M3 与 §6 第 9 行已把裸 `C`
改为 `C_strict`/`C_sql`，并加入发布前门禁 `C_strict == C_sql` 且「引擎可查询行集 == `C_strict`」。

### 2.2 第三轮聚焦审核（Sol r3）：门禁改为**按机制**

Sol r3 指出上面那句统一门禁与 M3 冲突：M3 正是要在「引擎有多余（未确认）行」时用停止证书工作，
而「全物理行集 == `C_strict`」一旦写成**所有机制**的公共门禁，M3 在其目标场景里必然失败。
最小反例（Sol 原文）：同代同 space、SQLite 仅 B `CONFIRMED`、引擎另有更近的未确认 A、`top_k=1`
—— M3 依停止证书可发布 B（与 oracle 逐项相同），统一门禁却会强制回退。已改：

| 机制 | 引擎侧门禁（ADR §3.3 新表） |
|---|---|
| M1 等价快照 | **全物理行集 == `C_strict`**（不允许多余行） |
| M2 下推等价 | **已证明等价的谓词过滤后行集 == `C_strict`**（全物理集可有多余行） |
| M3 停止证书 | **不要求行集相等**：允许多余行，但必须在**引擎行流**上给出可判定的停止证书（已取得 K 个合格行 + 未检查行的距离下界劣于第 K 行），并在**发布前复核第 K 行合格性** |

三机制**共同**的门禁只剩：公共门禁 `C_strict == C_sql` + 发布前窗口复核。
ADR §4 的机制表新增「引擎侧门禁」列，§6 第 1 行改为「M1/M2 不可用、**M3 可用**（有证书时）」，
并新增第 11 行 `extra_engine_row_with_m3_certificate`（A 更近但未确认、B 已确认、`top_k=1`
⇒ 允许 M3，页 = `{B}` = oracle；同状态下 M1/M2 必须回退）。companion 新增
`EngineSideGate` 枚举并把 `engine_side_gate` 列为 `CertificateRule` 必填；
契约用例新增 `test_gate_is_per_mechanism_and_m3_covers_the_extra_engine_row_state`
（含「未检查行下界更近 ⇒ 证书不成立、必须继续扩大或回退」的负例）。

### 2.3 第四轮聚焦审核（Sol r4）：M3 停止规则措辞与机器契约配对

1. **M3 停止规则自相矛盾（已修）**：§3.3/§4 原文要求「所有未检查**或已被排除**的行都不可能排在第 K 行
   之前」，而 §6 第 11 行的正例恰恰存在**更近但已排除**的 A(0.10) ⇒ 按原文该证书必然失败。
   现改为：停止证书只需覆盖**未检查的潜在合格行**；**已检查并经同一权威视图判为不合格的行可以更近**，
   但其**失格判据与发布前有效性必须可证**（本例 A 的判据 = 不在 `C_sql`）。§6 第 11 行同步写明
   「未检查行下界更近，或 A 的失格判据不可证/发布前失效 ⇒ 继续扩大或回退」。
2. **机制×门禁错配（已修）**：companion 原先把 `mechanism`/`engine_side_gate` 作为两个独立枚举，
   `{mechanism: M1, engine_side_gate: M3 的门禁}` 会被零错误接受。现改为
   `CertificateRule = oneOf {M1Certificate, M2Certificate, M3Certificate}`，每个变体用 `const`
   把机制与门禁**一对一绑定**，交叉组合一律拒绝；参考模型 `decide` 同样校验配对，错配即回退。
3. 另按 r4 把 §6 第 1 行措辞改为「**M1 不可用；M2 只在证明排序前下推等价时可用；M3 只在停止证书
   成立时可用**」，与机制表一致。新增
   `test_companion_binds_each_mechanism_to_exactly_one_engine_side_gate`（3 组正配 + 全部交叉错配），
   并把 M3 用例改为显式写明「更近的 A 是已检查、判据可证的失格行」。

## 3. 验收

契约用例 **12 / 0 / 0 / 0**（JUnit `test-results/p4-02h-r3-contracts.xml`；
原 9 例 + F1/F2 两条真实代码反例 + Sol r3 的按机制门禁用例）；`ruff check .`、`git diff --check` exit 0；
B 级全量 **1710 / 0 / 0 / 48**（`test-results/p4-02h-r3-full.xml`）。
`src/**`、migration、schema、既有 ADR/companion、
信封/错误码、`pyproject.toml`、`uv.lock` 零 diff。

## 4. 压缩交接包（Sol 终审输入，按 P4-02H 必须交付第 4 项）

| 项 | 内容 |
|---|---|
| 审查基线 | `6cd526e`（本轮 r4 修正后为本次提交；`src/**` 全程零改动）。**r2 的整体主审被审基线是 `2900a0a`；`6cd526e` 的按机制门禁 diff 尚无点名该提交的独立主审报告 ⇒ 需补一份，可只覆盖 r3/r4 修订面** |
| 改动文件与结论 | ADR-0012（§1/§2/§3/§4/§5/§6，含 r3 按机制门禁表与 r4 的 M3 措辞）、`p4-a3-contracts.v1.json`（`EligibilityRule`/`AuthoritySetRule`/`StateMatrixRow.case`/`EngineSideGate`/`CertificateRule` 三变体一对一绑定）、`tests/unit/test_p4_02g_a3_contracts.py`（+2 真实代码反例 + 按机制门禁 + 配对用例）、本报告、状态条目。结论：F1/F2 与 r3/r4 阻断均已按「收紧判据、不改基线语义」修复 |
| 待裁决高风险问题 | ①`C_strict` 的「行 revision = 确认 revision」需要一次**跨库同窗口证明**（M1 栅栏）才可用；M2/M3 的等价性尚未有构造成本评估。②回退率未知：ADR 的强制回退可能吃掉全部收益，必须由隔离实验按真实混合负载报 P95。③`place_state` 之外是否还有同类「列表为空但谓词非恒真」的字段（本卡已逐字段核对，建议独立复跑） |
| ADR 预期/实际差异 | 预期「无额外筛选 = 所有列表为空」；实际「谓词是否恒真」才是判据（F1）。预期「C = 权威资格集」；实际现行路径只按 ID 入口（F2）。两处差异已写入 ADR §2.2/§3 |
| 符号级定位 | `src/photo_search/places/filter.py::place_condition`（`state` 分支）、`src/photo_search/search/retrieval.py::scope_condition`（谓词 `"1"` 短路）、`_CONFIRMED_BLOCK_SQL`、`src/photo_search/indexing/store.py::_record`（`record_id = generation:asset`）与 `IndexCoordinator` 的 `after_lance` 故障点 |
| 最小反例 | F1：无任何列表条件 + `place_state=assigned` + 距离逆序（B=0.1 无地点、A=0.2 有 `MANUAL_ASSIGNED`），`top_k=1` ⇒ 全代 Top-1 漏 A。F2（已按短审修正）：**真实窗口** = 条目 `PENDING_REPAIR` + 引擎已写新行 ⇒ `C_sql == C_strict`，证书因多余引擎行失败 ⇒ 回退；**防御性窗口** = 同一 `CONFIRMED` 条目的引擎行 revision 不一致 ⇒ `C_strict ≠ C_sql` ⇒ 回退，oracle 在该形状下仍返回 A(rev2)。两者都在 `test_p4_02g_a3_contracts.py` 内可执行（含显式 `C_sql`/`C_strict` 计算） |
| 未验证项 | A3 与 oracle 的 `Recall@K=1.000` 等价性、五类注入的真实前后计数、A3 端到端 P50/P95（含真实文本编码/HTTP/回退率与回退时延）、真实中文 CLIP 权重、GTX 1660、冷缓存与长时 soak |
| 禁止扩大范围 | 不得实现/启用 A3、不得改 §12.2、不得改 `src/**`、信封/错误码/`query_hash`/游标、RRF/BM25/距离、既有 ADR 条款；不得以固定 overfetch/后过滤/计数或采样/索引存在性充当证书；不得接真实照片库 |

## 5. 独立性限制（仍未满足，如实记录）

本次修复与 ADR 起草、P4-02H 主审**都在同一会话**完成，因此 §2 的核对与本节修复**不构成**
V3.1 §2.3/§2.4 期望的「另一位 DS-H 会话」独立复现。建议在下一次聚焦审核（短审）时**另开会话、另一模型**
复跑：真实 `scope_condition` 的全部字段恒真性核对、`vector_channel` 的 revision 窗口、以及本报告 §4 的
「待裁决高风险问题」三项。若不复跑，Sol 终审应把上述内容当作起草方自证看待。

## 6. 停止点

状态停在 `ENGINEERING REVIEW PASS (revised) / SOL FOCUSED REVIEW PENDING`。Sol 冻结通过前
**不生成 A3 实现卡**，隔离实验卡也须等冻结通过；`src/**` 保持零改动。
