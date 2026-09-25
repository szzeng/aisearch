# ADR-0012 — A3 无筛选全局候选的资格、证书与回退契约

- 状态：**Accepted（契约冻结）**
- 日期：2026-09-25
- 决策输入：[P4-02G A3 架构闸门评估](../reports/P4-02G-a3-gate-review.md)、
  [P4-02F 检索延迟阶段 0/1 实测](../reports/P4-02F-retrieval-latency.md)、
  [P4-01C 聚焦审查](../reports/P4-01C-sol-focused-review.md)
- 机器可读 companion：[`p4-a3-contracts.v1.json`](contracts/p4-a3-contracts.v1.json)
- 被冻结的既有契约（不得由本 ADR 改写）：ADR-0003 §8–§9、ADR-0005 §10、
  `POST /api/v1/search` 信封 / `query_hash` / 游标 / 全部错误码、RRF/BM25/距离公式

## 1. 术语

| 术语 | 含义 |
|---|---|
| **完整路径** | 现行 `search/retrieval.py::vector_channel` 的候选块精确路径：SQLite 权威资格决定成员，按 `asset_id` 分块调用 Lance，**未截断**（`truncated=false`）时逐块 top-k 的合并等于候选集全局 top-k |
| **`C_sql`（oracle 成员集）** | 该请求绑定的 `generation_id`/`space_id` 内，**恰好等于** `_CONFIRMED_BLOCK_SQL` 所选条目的 `asset_id` 集合（§3.1）。它定义现行完整路径（等价性 oracle）的成员 |
| **`C_strict`（证书资格集）** | `C_sql` 中那些在 Lance 侧也**行级一致**的资产：恰好一行（`record_id = generation:asset`）、space 匹配、且该行 `projection_revision` 等于该条目当前 `confirmation_revision`（§3.2）。证书（§4）只在 `C_strict` 上评估 |
| **A3** | 「无额外用户筛选时，用一次全局向量 Top-K 或等价的少候选访问取代整代候选块物化」的候选协议（P4-02G §3 的定义）。本 ADR 与 P4-02C 的同名审计幂等 A3 无关 |
| **证书** | §4 三种机制之一成立的**可验证**证明，且必须在**发布前**仍成立；没有证书就不得用 A3 发布页 |
| **回退** | 走完整路径（或按现行契约报告状态），不发布缺项页 |

## 2. 资格谓词 `A3_ELIGIBLE`（必须同时成立）

1. 请求带**完整向量绑定**：`(encoder|query_vector, space_id, generation_id, dimension)` 齐备，
   且存在语义通道（文本或图片查询）；纯筛选查询不合格。
2. **无额外权威筛选**（必须**逐字段**核对 `scope_condition`，不能只看列表是否为空）：
   `source_id is None`、`availability == "any"`、`place_ids` 为空、**`place_state == "any"`**、
   `person_ids` 为空、`capture is None` —— 即 `scope_condition` 恰好返回 `"1"`（恒真）。
   ⚠ `place_state` **独立于** `place_ids`：`places/filter.py::place_condition` 在 `place_ids=()` 时
   对 `assigned`/`unassigned`/`manual_unassigned`/`suggested` 仍返回非恒真谓词（Sol 聚焦审核 F1 的
   最小反例：`place_state=assigned` + 距离逆序时，全代 Top-1 会取到无地点资产的 B 而漏掉有
   `MANUAL_ASSIGNED` 的 A）。资格判据是**由真实谓词是否恒真**决定，而不是由列表是否为空决定。
3. 绑定的 generation 处于活动且 `state == "READY"`，其 `space_id`/维度与请求一致，
   不允许跨代、跨 space 或旧 generation。
4. 未命中上述任何一条 ⇒ **不资格**，按 §7 回退。

「无用户筛选」只表示没有额外限制，**不等于** Lance 全表都是可搜索行：资格集合始终是 §3 的 C。
带筛选（来源/地点/人物/拍摄时间）的请求**一律**走完整路径，本 ADR 不泛化到那些场景。

## 3. 两个集合：oracle 成员集 `C_sql` 与证书资格集 `C_strict`

### 3.1 `C_sql`（oracle 成员集，现行语义）

`C_sql` = `_CONFIRMED_BLOCK_SQL` 所选条目的 `asset_id` 集合，即同一 SQLite 权威视图下该绑定
generation/space 内满足 `index_entries.state = 'CONFIRMED'` **且** `scope_condition`（§2.2 成立时为
恒真）的行。它**只**比较 ID 入口，**不**比较 Lance 行的 `projection_revision`，也**不**在
`availability = "any"` 时追加可见性条件 —— 这正是现行完整路径的行为，因此 `C_sql` 就是
**等价性 oracle 的成员集**（§5.5）。

### 3.2 `C_strict`（证书资格集，A3 的更强前提）

`C_strict` = `C_sql` 中同时满足以下条件的资产：

1. 在 Lance 中恰好一行（`record_id = f"{generation_id}:{asset_id}"` 使「每代每资产一行」成为结构事实，
   不存在重复行）；
2. 该行 `space_id` 与绑定一致；
3. 该行 `projection_revision` **等于**该条目当前的 `confirmation_revision`。

Lance 侧的 `visible`、行数、计数或索引存在性**都不是**资格凭证；它们是派生数据。

### 3.3 两条集合的关系（F2 的裁决）

- A3 的**发布义务**是「与 oracle（`C_sql` 上的完整路径）逐项相同」；
- A3 的**证书义务**是在 `C_strict` 上可证；
- 因此 `C_strict ≠ C_sql`（例如 `after_lance` 崩溃窗口：Lance 已写未确认的更新行）时
  **强制回退**（§7），回退发布的正是 oracle 页，两者一致、不矛盾。
- **本 ADR 不修改**现行完整路径的 revision-blind 行为；若要收紧它，属完整路径自身的另行裁决与立卡。

## 4. 证书：允许 A3 的三条互斥机制

| 机制 | 内容 | 必须的证据 |
|---|---|---|
| **M1 等价快照** | 查询前存在可验证的写者栅栏/版本对，使「Lance 可查询行集及 revision」在**整个查询—发布窗口**内等于 `C_strict`（因而其 ID 投影等于 `C_sql`） | 栅栏标识 + 同一窗口内的 ID/revision 集合相等证明（计数与采样不构成证明） |
| **M2 下推等价** | 把 C 的资格约束**推入排序之前**（引擎过滤/成员快照），并证明该谓词与 C 逐项等价 | 谓词等价证明 + 反驳用例（不允许「引擎过滤后 SQLite 再删」两阶段替代） |
| **M3 带停止证书的迭代扩大** | 逐步扩大检索范围，直到已取得 K 个合格行，且**所有未检查行在 `(distance, asset_id)` 全序上都不可能排在第 K 行之前** | 停止证书（上界/下界对）+ 无证书即回退 |

固定倍数 overfetch、固定重试次数、全局 Top-K 后过滤、仅凭计数/采样/索引存在性**都不构成证书**。
M3 最坏情况下退化为完整候选扫描 —— 因此 A3 的收益**不是**必然保证。

## 5. 排序、并列与 `top_k`

1. 排序全序是 `(distance, asset_id)`，与完整路径完全一致。
2. **截断必须发生在全序之后**：不得让引擎先只返回 K 行再在 Python 排序（P4-01C 审查 B1）。
3. `top_k ∈ [1, 1000]`；契约要求对**每个** `top_k` 成立，不能只验证 `top_k=20`。
4. 融合、`pending_projection`、权威复读、会话/游标与错误语义保持原状。
5. **等价性 oracle 就是现行完整候选块路径的实现**（P4-02H 主审 F2）：包含它当前的
   revision-blind 窗口。任何**加强** revision 校验（例如要求引擎行的 `projection_revision`
   等于当前确认 revision）都是完整路径自身的后续工作，**不得**以 A3 为名改变基线语义；
   A3 只能被要求「不比完整路径更差」。

## 6. 状态矩阵（反例目录，冻结）

每一行都是「必须被决定」的输入状态；`A3` 列只允许在证书成立时为真，否则**回退**，
且两种情形**发布页都必须与完整路径逐项相同**（含并列距离的 `asset_id` 次序）。

| # | 反例类别 | 状态 | 决策 |
|---:|---|---|---|
| 1 | 未确认行 | Lance 有行、SQLite 未确认（outbox 未 `CONFIRMED`） | 无 M1/M2/M3 ⇒ **回退**（不得发布缺项页） |
| 2 | 修复中 | `index_entries = PENDING_REPAIR` | ⇒ **回退** |
| 3 | revision 不一致 | Lance 行 `projection_revision` 不是当前有效版本（**含 Lance 已写未确认的崩溃窗口**；`record_id = generation:asset` 保证每代每资产只有一行，不存在重复行） | ⇒ **回退** |
| 4 | Lance 缺行 | SQLite `CONFIRMED` 但 Lance 无对应行/旧行 | ⇒ **回退**（事后核验补不回缺失项） |
| 5 | 并列距离 | 第 K 与第 K+1 距离相同 | 全序 `(distance, asset_id)` 决定；引擎内截断一律禁止 |
| 6 | 隐藏/离线/来源变化 | 权威可见性或来源在查询窗口内变化 | 以 SQLite 权威为准 ⇒ 变化即 **回退/重搜** |
| 7 | 代/space 切换 | 绑定 generation/space 变化或不再 `READY` | ⇒ **回退** |
| 8 | 并发发布窗 | 查询与发布之间发生确认/修复/撤销/revision 变化 | 发布前按现行 revision/资格门禁复核；不能重建完整结果则 **回退/重搜** |
| 9 | 正对照（证书成立） | 全部可能行与 C 逐项一致且窗口内稳定 | 允许 A3；页必须与完整路径逐项相同 |
| 10 | 空 `place_ids` 但 `place_state != any`（Sol F1） | 无地点 ID 条件，`place_state ∈ {assigned, unassigned, manual_unassigned, suggested}` | **不资格**（`NOT_ELIGIBLE_EXTRA_SCOPE`）；`scope_condition` 非恒真 ⇒ 走完整路径 |
| 11 | 行 revision 与确认不一致（Sol F2） | Lance 行 `projection_revision=2`、条目 `confirmation_revision=1`（`after_lance` 窗口） | `C_strict ≠ C_sql` ⇒ **回退**；oracle 页照现行行为发布 |

## 7. 回退义务

1. **证明不了完整 Top-K 就必须回退**：任一前提缺失、证书不成立、窗口已变或无法重建，
   一律走完整路径（或在不可能重建时按现行契约报告状态），**不得发布缺项页**。
2. 回退不改变信封：不得新增字段、错误码或状态词；回退页与完整路径页逐项相同。
3. 回退率与回退时延是**必测指标**（P4-02G §5.2）：A3 的快样本不得掩盖慢回退。

## 8. 禁止范围（本次冻结下）

- 不得改 `src/**` 检索语义、信封/错误码/`query_hash`/游标、RRF/BM25/距离、人物/地点/拍摄时间
  权威谓词、P1～P3 人工事实、`0001`～`0016` 迁移内容与既有 ADR 条款。
- 不得引入 ANN/第二个向量后端/云服务；不得接入真实照片库。
- **不得放宽 §12.2**（30 万资产暖机普通文字搜索 API P95 ≤1 s，含文本编码）；
  不得用固定 overfetch、后过滤、修改 oracle 或改测试来掩盖缺项。

## 9. 授权与实现前置

1. **本轮不实现、不启用 A3**；产品继续使用 SQLite 权威候选块路径。
2. 实现前置（缺一不可）：① 本 ADR/companion 经**独立 DS-H 完整受影响面工程审查**；
   ② **Sol high 聚焦终审**冻结协议与最小反例；③ 隔离实验卡给出等价性（Recall@K=1.000 对 oracle）
   与回退证据；④ 实现卡另立，并按冻结结果再审。
3. 隔离实验只允许：合成夹具、`tmp/` 探针与 `test-results/` 证据；不得改产品代码。

## 10. 状态记录

| 事项 | 本轮记录 |
|---|---|
| 闸门 A（无契约变更） | **未达标**：30 万档已测最优 7 377.9 ms（P4-02F §13） |
| 闸门 B（A3） | **尚未证明等价，也未端到端达标**：分量下界约 813 ms 未含真实文本编码/HTTP（P4-02F §14） |
| P4-02F 性能 PASS | **不授予** |
| §12.2 | **不修改** |
| A3 实现/启用 | **未授权** |

## 11. 变更控制

本 ADR 或 companion 的任何修改都必须走新的 ADR 或正式补遗，并经 §9 的独立审查与 Sol 聚焦终审；
不得由实现方在其任务卡内自行放宽。
