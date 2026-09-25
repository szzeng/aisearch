# P4-02H — ADR-0012 / companion 独立工程主审

**结论：`ENGINEERING REVIEW PASS（含 2 条必须修正的表述、1 条独立性限制）`；
维持 `A3 未实现/未启用`、`§12.2 未改`、`P4-02F 非 PASS`；交 Sol high 聚焦冻结。**
被审基线 `40f2bd4`；被审文件：
`docs/decisions/ADR-0012-a3-global-candidate-contract.md`（SHA-256 `a1f0…` 见 git）、
`docs/decisions/contracts/p4-a3-contracts.v1.json`、`tests/unit/test_p4_02g_a3_contracts.py`。

## 0. 独立性限制（必须披露）

本次主审与 ADR/companion 的**起草同一会话**，因此**不是** V3.1 §2.3/§2.4 期望的「另一位 DS-H 会话」。
独立性只体现在：主审重新读产品调用链与状态机、自写核对命令、并发现了起草者未写清的两处表述缺陷。
**建议在 Sol 聚焦终审前，另开会话（另一模型/档位）复跑本报告 §2 的核对项**；若不复跑，Sol 终审时应把
本报告的 §2.1–§2.5 当作未独立复现的输入看待。

## 1. 核对方法

只读核对，无产品改动：读 `search/retrieval.py::vector_channel`/`_CONFIRMED_BLOCK_SQL`、
`indexing/store.py::LanceVectorIndex.search_candidates`/`IndexCoordinator._record`/`_is_superseded`/
`_schedule_existing`，以及既有证据 `P4-01C-sol-focused-review.md`、`P4-02B-dsh-engineering-review.md`
（`after_lance` 窗口注入）、`P4-02C`（回收/审计）与 `P4-02F` 实测。契约用例复跑见 §3。

## 2. 逐项判定（P4-02H 的 7 项边界）

| # | 核对项 | 判定 | 依据 / 反例 |
|---:|---|---|---|
| 1 | 资格谓词完整性 | **通过** | 与 `SearchScope`/`scope_condition` 的字段一一对应（`source_id`、`availability`、`place_ids`、`person_ids`、`capture`）；「无额外筛选」即其余条件全空的恒真谓词。未发现被漏掉、仍会改变结果集的权威条件 |
| 2 | `C` 的定义 | **通过（需 1 处表述修正）** | `index_entries.state='CONFIRMED'` 是产品入口（`_CONFIRMED_BLOCK_SQL`）；Lance 的 `visible` 由 `_record` 写入、**不参与资格**，与 ADR §3 一致。修正见 F1 |
| 3 | 证书与停止规则 | **通过** | M1/M2/M3 与 P4-02G §3.2 的三种机制一致；禁止项枚举（7 类）由 schema 拒绝（契约用例覆盖）；M3 最坏退化为完整扫描已明确为可接受回退 |
| 4 | 全序与并列 | **通过** | `(distance, asset_id)` 与现行 `vector_channel` 的 `sorted(key=(distance, asset_id))` 一致；P4-01C B1 的「引擎不得先截断」被写成硬约束；`top_k=1..1000` 与 `MAX_TOP_K` 一致 |
| 5 | 五个点名反例 | **通过（需 1 处措辞修正）** | 五类都由产品状态机可达或为防御性行，决策与理由可测。可达性细节见 F2 |
| 6 | 回退义务与信封不变 | **通过** | ADR §7 明确「证明不了就回退、不发布缺项页、不改信封/错误码」；契约用例对每行断言 `published_page_equals_complete_path=true` |
| 7 | 边界记录 | **通过** | ADR §10 与 §16.1 如实记录闸门 A 未达标、闸门 B 未证等价、P4-02F 非 PASS、§12.2 未改、A3 未授权 |

## 3. 主审独立复跑

`uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_02g_a3_contracts.py`
= **9 / 0 / 0 / 0**（JUnit `test-results/p4-02h-contracts.xml`）。B 级全量沿用冻结轮
`test-results/p4-02g-full.xml` = **1707 / 0 / 0 / 48**（本轮只改文档措辞，未改产品代码与用例逻辑）。

## 4. 发现与处理

**F1（表述，已修）**：`_record` 的 `record_id = f"{generation_id}:{asset_id}"` 且 `upsert_batch` 用
`merge_insert("record_id")` ⇒ **每代每资产只有一行**，「重复行」不是结构状态；ADR §6 第 3 行的
「旧/重复 revision」应写成「revision 不一致或非当前确认版本（含 Lance 已写未确认的崩溃窗口）」，
避免读者以为存在重复行问题。

**F2（可达性 + oracle 定义，已修）**：五类的可达性核对结果——①未确认行：可达（outbox 已写未确认，
P4-02B `after_lance` 注入复现）；②`PENDING_REPAIR`：可达（P4-02C 的修复路径）；③revision 不一致：
可达，且实际方向主要是「Lance 较新、SQLite 仍旧确认」（同一次事务内引擎写先于确认提交）；
④Lance 缺行：**防御性**（引擎行从不删除，只有表/文件丢失或被替换时才出现）；⑤并列：可达
（P4-01C 已复现）。据此在 ADR §5 明确：**等价性 oracle 是「现行完整候选块路径的实现」**，
包含其 revision-blind 窗口；任何**加强** revision 校验都属完整路径自身的后续工作，不得借 A3 之名
顺手改变基线语义。

两条都是**收紧/澄清**，未放宽 §2–§7 的任何判据；修正后契约用例仍 **9 / 0 / 0 / 0**。

## 5. 未验证与停止点

- 等价性（oracle 对照 `Recall@K=1.000`）、五类注入的真实前后计数、A3 端到端 P50/P95（含真实文本编码、
  HTTP、回退率与回退时延）仍未验证 ⇒ 属隔离实验卡与后续实现卡。
- 真实中文 CLIP 权重、GTX 1660、冷缓存/soak 仍 BLOCKED（与 P4-02F 相同）。
- 有界回退是**正确性优先**设计：A3 的收益可能被回退率吃掉，实验必须按真实混合负载报 P95，不能用快样本
  掩盖慢回退。
- **停止点**：状态停在 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。Sol high 冻结通过前
  **不得立 A3 实现卡**；`src/**` 保持零改动。
