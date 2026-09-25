# P4-02G — A3 契约冻结与反例矩阵（不作实现授权）

**结论：`ADR-0012 契约冻结（Accepted）` + `companion 冻结` + `五个必验反例全部落在回退侧`；
本轮不实现、不启用 A3，不改 §12.2，不把 P4-02F 标为性能 PASS。**
决策输入是 [P4-02G A3 架构闸门评估](P4-02G-a3-gate-review.md)（用户裁决：进入 A3 契约与隔离实验）。

## 1. 本轮交付

| 交付 | 路径 |
|---|---|
| 契约冻结 | [ADR-0012](../decisions/ADR-0012-a3-global-candidate-contract.md)（Accepted） |
| 机器可读 companion | [p4-a3-contracts.v1.json](../decisions/contracts/p4-a3-contracts.v1.json) |
| 可执行冻结校验 | `tests/unit/test_p4_02g_a3_contracts.py`（9 用例，JUnit `test-results/p4-02g-contracts.xml`） |
| 决策输入 | [P4-02G 闸门评估](P4-02G-a3-gate-review.md) |

关系链：P4-02F 阶段 0/1 实测（闸门 A 未达标、闸门 B 未证等价）→ P4-02G 架构裁决（进入契约与隔离实验）
→ 本卡冻结 ADR-0012 + companion。**A3 的实现授权仍为空**。

## 2. 冻结的判据（摘要，正文见 ADR-0012）

1. **资格谓词 `A3_ELIGIBLE`**：完整向量绑定 + 语义查询 + 无额外权威筛选
   （`source_id` 空、`availability=any`、地点/人物/拍摄时间条件为空）+ 绑定代 `READY` 且 space/维度匹配。
   任一条不成立即不资格。
2. **C 的定义**：同一 SQLite 权威视图下该绑定代/空间内 `CONFIRMED`、当前有效投影 revision、
   与资产可见性权威事实相符的 `asset_id` 集合；Lance 的 `visible`/行数/索引存在性**不是**资格凭证。
3. **证书（三选一，且发布前仍成立）**：M1 等价快照（写者栅栏）、M2 下推谓词等价、M3 带停止证书的迭代扩大。
   固定 overfetch、固定重试次数、全局 Top-K 后过滤、计数/采样/索引存在性**都不构成证书**。
4. **全序与截断**：`(distance, asset_id)` 全局有序，截断不得先于全序；`top_k ∈ [1,1000]`。
5. **回退义务**：证明不了完整 Top-K ⇒ 回退完整候选块路径（或按现行契约报告状态），
   不发布缺项页、不改信封、不新增错误码。

## 3. 五个必验反例（用户点名的类别）与冻结决策

| 用户点名类别 | 契约行 | 冻结决策 |
|---|---|---|
| 未确认行（Lance 有行、SQLite 未确认） | `unconfirmed_row` | **回退**（`UNCONFIRMED_ROW_IN_ENGINE`） |
| 修复中（`PENDING_REPAIR`） | `pending_repair` | **回退**（`PENDING_REPAIR_ENTRY`） |
| 旧/重复 `projection_revision` | `stale_or_duplicate_revision` | **回退**（`STALE_OR_DUPLICATE_REVISION`） |
| 并列距离 | `tied_distance` | **回退**（`NO_CERTIFICATE_PROVABLE`）；全序由 `(distance, asset_id)` 决定 |
| 并发发布窗（查询→发布之间确认/撤销/revision 变化） | `concurrent_publication` | **回退/重搜**（`REVISION_OR_VISIBILITY_CHANGED_BEFORE_PUBLICATION`） |

连同矩阵其余行（`missing_engine_row`、隐藏/离线或来源变化、代/space 切换）共 9 行全部由测试逐行断言：
**8 行回退 + 1 行正对照（证书成立才允许 A3）**，且每行都要求发布页与完整路径逐项相同。

## 4. 验证与验收

- `tests/unit/test_p4_02g_a3_contracts.py` = **9 / 0 / 0 / 0**（JUnit `test-results/p4-02g-contracts.xml`）：
  companion 子集校验、矩阵与 companion `case` 枚举双向一致、7 类禁止机制被 schema 拒绝、
  `top_k` 0/1001 被拒、五个点名反例一律回退、无证书一律回退且页相等、资格谓词逐字段失败即回退、
  并列距离全序与「引擎内截断禁止」、ADR 边界条款（不实现/不放宽 §12.2/未达标/未证等价）在位。
- 本轮**零产品改动**：`src/**`、`0001`～`0016`、`SUPPORTED_SCHEMA_VERSION`、既有 ADR 与 companion、
  信封/错误码/`query_hash`/游标、RRF/BM25/距离、打包面、`pyproject.toml`、`uv.lock` 零 diff。

## 5. 未验证 / 仍缺的证据

- **等价性未证明**：本卡只冻结判据与反例矩阵，**没有**对 oracle（完整路径）做 `Recall@K=1.000` 的实测，
  也没有隔离实验的注入结果（未确认行/修复/旧 revision/并列/并发发布各例的真实前后计数与最小复现）。
- **端到端未达标**：A3 路径的真实 P50/P95（含真实文本编码、HTTP、回退率与回退时延）未测；
  P4-02F §14 的 ≈813 ms 只是分量相加，且 P4-02F 已披露跨运行漂移。
- 真实中文 CLIP 权重、GTX 1660 最低配置、冷 OS 缓存与长时 soak 仍 **BLOCKED**（与 P4-02F 相同）。

## 6. 路由与停止点

- 本卡是 **A 级契约冻结**，不标实现 PASS、不授予性能 PASS。
- 下一步：**独立 DS-H 完整受影响面工程审查**（审 ADR-0012/companion/契约用例与冻结边界是否与
  P4-02G §5.1/§5.3 一致，并逐条检查五个反例的决策与回退义务）→ 然后 **Sol high 聚焦终审**冻结协议
  与最小反例。终审通过前不得立实现卡。
- 隔离实验（在合成夹具上给出等价性与回退证据）是冻结通过后的**另一张卡**，见
  [P4-02H](../tasks/P4-02H.md) 的后续指针；实验本身也不得改 `src/**`。
