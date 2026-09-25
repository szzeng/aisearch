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

## 3. 验收

契约用例 **11 / 0 / 0 / 0**（JUnit `test-results/p4-02h-fix-contracts.xml`；
原 9 例 + F1/F2 两条真实代码反例）；`ruff check .`、`git diff --check` exit 0；
B 级全量见 `test-results/p4-02h-fix-full.xml`。`src/**`、migration、schema、既有 ADR/companion、
信封/错误码、`pyproject.toml`、`uv.lock` 零 diff。

## 4. 压缩交接包（Sol 终审输入，按 P4-02H 必须交付第 4 项）

| 项 | 内容 |
|---|---|
| 审查基线 | `77557db`（本次修复后为本次提交；`src/**` 全程零改动） |
| 改动文件与结论 | ADR-0012（§1/§2/§3/§4/§5/§6）、`p4-a3-contracts.v1.json`（`EligibilityRule`/`AuthoritySetRule`/`StateMatrixRow.case`）、`tests/unit/test_p4_02g_a3_contracts.py`（+2 真实代码反例）、本报告、状态条目。结论：F1/F2 均已按「收紧判据、不改基线语义」修复 |
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
