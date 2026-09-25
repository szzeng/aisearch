# P4-02H — ADR-0012 / companion 整体主审（第二轮，含窗口归因更正）

**结论：`ENGINEERING REVIEW PASS`（7 项边界全部通过；本轮 **0 处必须修正**，
2 处非阻断表述建议 + 1 处变更控制提醒，全部留 Sol 终审裁决）。
维持 `A3 未实现/未启用`、`§12.2 未改`、`P4-02F 非性能 PASS`；本轮只读，未改任何冻结面。**
状态停在 `ENGINEERING REVIEW PASS (revised) / SOL FOCUSED REVIEW PENDING`。

被审基线 `2900a0a`（"P4-02H 短审修正"）；工作树干净、`src/**` 与 `0001`～`0016` 零 diff。

## 0. 被审文件指纹（SHA-256 前 16 位）

| 文件 | 前 16 位 | 与上一轮（`7ecd363`/`40f2bd4`）对比 |
|---|---|---|
| `docs/decisions/ADR-0012-a3-global-candidate-contract.md` | `65D2E29D71A6D073` | 已改（§3.3/§4/§6） |
| `docs/decisions/contracts/p4-a3-contracts.v1.json` | `06CDEF85699969D1` | 已改（删 `row_revision_mismatch`） |
| `tests/unit/test_p4_02g_a3_contracts.py` | `DADD2877184168FA` | 已改（矩阵文本 + 两种窗口形状断言） |
| `src/photo_search/search/retrieval.py` | `E337752B74FEA839` | **未改** |
| `src/photo_search/indexing/store.py` | `AF19998C5E9D9A0C` | **未改** |
| `src/photo_search/places/filter.py` | `43F528B96AAE9925` | **未改** |

## 1. 独立性声明（必须披露）

- 归属：ADR/companion/用例的改动来自 **短审修正会话**（`2900a0a`）；本轮主审在**另一会话**执行，
  且**自写探针**（`tmp/p402h-recheck/review_r2_probes.py`）重新计算三方一致性、并列/块界、
  `top_k` 两端与更正后的窗口形状，未复用作者的断言文本。
- 限制：**本报告与上一轮「独立复跑」出自同一会话**（同一 Codex 宿主）。因此「复跑 → 主审」
  不是两个互相独立的会话；本文不冒称第二家独立 DS-H，也不冒称任务卡指定的 **Sol high 聚焦终审**。
  由哪个模型执行需用户按 Codex 实际会话记录；**模型由用户在 Codex 中手动切换**。

## 2. 核对方法

- 读：ADR-0012 全文、companion 全文、契约用例全文；`search/retrieval.py`（`scope_condition`、
  `vector_channel`、`_CONFIRMED_BLOCK_SQL`）、`search/service.py`（`search_assets`/`search_request`/
  `_render_page`/`_authority_drift`）、`indexing/store.py`（`search_candidates`、`_record`、
  `process_next`/`process_batch`、`_mark_entry_pending_repair`、`_schedule_existing`）、
  `artifacts/store.py::_persist_embedding_event`、`places/filter.py`；`2900a0a` 的完整 diff。
- 跑（本会话自跑，命令与结果见 §5）：契约用例、B 级全量、三方一致性探针、并列/块界/`top_k` 探针、
  更正窗口探针；另复核了上一轮独立复跑的本机 JSON 证据。

## 3. 逐项判定（P4-02H 的 7 项边界）

| # | 核对项 | 判定 | 依据 / 反例 |
|---:|---|---|---|
| 1 | 资格谓词完整性 | **通过** | §2.2 的六个字段与真实 `scope_condition` 一一对应：本轮自写探针在真实迁移库里把 8 个 `SearchScope` 字段做 240 组交叉（72 组结构化拒绝、168 组可评估），**只有全中性一处返回 `"1"`，与冻结判据零反例**。另核对：`assets` 存在性由 `index_entries` 的 FK 与「条目只降级不删除」保证；绑定 READY/space/dimension 由 §2.3 覆盖；无其它会改变成员集的权威条件。 |
| 2 | `C` 的定义与现状关系 | **通过（1 处观察，非缺陷）** | `C_sql` = `_CONFIRMED_BLOCK_SQL` 选择集（ID 入口）；`C_strict` 的增加条件是行级一致性；`index_entries` 主键 `(space, asset, generation)` 使「每代每资产一行」结构成立，`merge_insert("record_id")` 实测替换而非追加。「Lance 派生列不是凭证」成立：向量通道只读 `asset_id`+距离；`visible` 由写入时的 `assets.state` 派生，而 `assets.state` 的 CHECK **只允许 `ACTIVE`**（0001 起未放宽），故当前 `visible` 恒真、无漂移反例；会漂移的 `source_ids` 只被过滤查询使用，而过滤查询本来不资格。 |
| 3 | 证书与停止规则 | **通过（1 处非阻断表述建议）** | M1/M2/M3 已统一指向 `C_strict`（M2「与 `C_strict` 逐项等价」，M3「合格性按 `C_strict` 判定」），未放宽；7 类禁止机制由 schema 枚举拒绝（本轮核对枚举完整）；M3 最坏退化为完整扫描并落到回退，已明示。**建议**：§3.3 的「引擎对该代/空间的可查询行集」宜显式定义成「本次查询**可返回**的行集（M1 为物理表；M2 为下推后的集合）」，否则 M2 可能被误读为要求物理表本身等于 `C_strict`。 |
| 4 | 全序与并列 | **通过** | 本轮自跑真实 Lance 探针：20 行距离全并列（0.25）时 `top_k=1` 与 `block_size∈{5000,1}`（1 块 vs 20 块）返回**同一**最小 `asset_id` ⇒ 块约束是访问路径参数、不改变页；`top_k=1000` 返回 20 行且严格升序、`truncated=false`。代码侧 `search_candidates` 以 `limit=len(ids)` 每候选取一行，引擎无法在调用方全序之前截断（P4-01C B1）。companion `MinTopK/MaxTopK/OrderKey` 与 `MAX_TOP_K=1000`、`(distance, asset_id)` 一致。 |
| 5 | 五个点名反例 | **通过** | 未确认行 → 第 1 行（**已更正为真实 `after_lance` 窗口**）；`PENDING_REPAIR` → 第 2 行；旧/重复 revision → 第 3 行（**已降级为防御性**，由越序写入/损坏/外部替换触发，回退义务不变）；并列 → 第 5 行；并发发布窗 → 第 8 行。可执行锚点：第 1/2 行由仓库 `test_p2_search_topup.py` 与上一轮真实 Lance/调度器交织复现，并由本轮探针以真实引擎复核；第 3 行由契约用例显式计算 `C_sql={1,2}`/`C_strict={2}` 且保留真实 `vector_channel` revision-blind 断言；并列由 §4 与本轮探针。A3 侧的**实现性**仍属隔离实验卡。 |
| 6 | 回退义务与信封不变 | **通过** | §7 未改：证明不了完整 Top-K 即回退、不发布缺项页、不新增字段/错误码/状态词；companion 唯一改动是**删除**一个 `StateMatrixRow.case` 枚举成员，`FallbackReason`（10 值）与全部 `const` 未变；`A3Request` 无新增属性。矩阵每行仍断言 `published_page_equals_complete_path=true`。 |
| 7 | 边界记录 | **通过** | §10 如实记录：闸门 A 未达标（30 万档最优 7 377.9 ms）、闸门 B 尚未证明等价也未端到端达标、P4-02F 不授予、§12.2 不修改、A3 未授权；§9 四项实现前置与「本轮不实现、不启用 A3」一致。 |

## 4. 更正后的窗口归因复核（本轮重点）

### 4.1 更正内容与复核结论

`2900a0a` 把 `after_lance` 的真实形态写成「**引擎有行、oracle 没有**」（§6 第 1 行承载），
把「同一 `CONFIRMED` 条目的引擎行 revision 不一致」降级为**防御性**状态（§3.3 第 4 条 / §6 第 3 行），
并在 §3.3 加入发布前门禁 `C_strict == C_sql` 且「引擎可查询行集 == `C_strict`」。

**本轮复核：判定成立，且与状态机一致。** 独立证据：

1. 写入次序（符号级）：为同一 `(space, asset, generation)` 发布新产物的两条路径都**先**把条目降为
   `PENDING_REPAIR` 并提交 —— `artifacts/store.py::_persist_embedding_event`（约 :378 的
   `INSERT INTO index_entries … 'PENDING_REPAIR'`）与 `indexing/store.py::_schedule_existing`
   （调用 `_mark_entry_pending_repair`，约 :1187/:1233）；产品链路的引擎写入只有
   `IndexCoordinator` 两处（:596 单事件、:775 批量），且与把 `confirmation_revision` 置为
   **同一 revision** 的确认语句处于**同一写事务**（:606/:803）。
2. 真实引擎探针（本轮）：一代内 3 个引擎行，其中**最近的一行**（距离 0.05）对应条目为
   `PENDING_REPAIR` ⇒ `C_sql = {4,5}`，而真实 `vector_channel(top_k=3)` 返回 `[4,5]`
   （`scanned_assets=2`）——**多余引擎行对完整路径不可见**，正是「引擎有行、oracle 没有」。
3. 仓库自身用例：`tests/integration/test_p2_search_topup.py` 在 `after_lance` 注入后断言
   `entry_state == "PENDING_REPAIR"`（上一轮以同一函数体/断言复跑 2/2 通过）。
4. 防御性形态保留且可执行：契约用例以显式集合计算给出 `C_sql={1,2}`、`C_strict={2}`，
   并让真实 `vector_channel`（替身引擎）返回 rev2 的更近行，证明 oracle 在该越序状态下 revision-blind。

### 4.2 由此产生的两点重要口径（供 Sol 与实现卡）

1. **A3 资格请求下，现行「权威复读」是空转的**：`search_assets`/`_render_page` 用
   `_verified_ids` 把命中重新过一遍 `scope_condition`；而 A3 资格要求该谓词恰好是 `"1"`，
   于是这一步既不能剔除多余引擎行、也不能剔除越序行。**A3 的正确性完全落在证书与发布前门禁上**，
   ADR §5.4「权威复读保持原状」是对的，但不能被当作额外保险。建议 Sol 在冻结时点名确认这一点。
2. **门禁 vs 窗口**：§3.3 的「发布前必须通过门禁」应与 §4 M1 的「整个查询—发布窗口内等于
   `C_strict`」、§6 第 9 行「且在窗口内稳定」一起读 —— **单点采样不构成 §4 的证据**
   （§4 已写「计数与采样不构成证明」）。建议在实现卡中把门禁写成 M1 窗口证据的可执行形式，
   而不是一次独立的事后抽查。

## 5. 复跑证据（本会话，被审基线 `2900a0a`）

| 项 | 命令 / 产物 | 结果 |
|---|---|---|
| 契约 focused | `pytest tests/unit/test_p4_02g_a3_contracts.py` → `test-results/p4-02h-r2-contracts.xml` | **11 / 0 / 0 / 0** |
| B 级全量不回归 | `pytest tests/unit tests/integration` → `test-results/p4-02h-r2-full.xml` | **1709 / 0 / 0 / 48**（229.1 s；48 为既有环境跳过） |
| 三方一致性 + 并列/块界 + `top_k` 两端 + 更正窗口 | `tmp/p402h-recheck/review_r2_probes.py` → `test-results/p4-02h-r2-review-probes.json` | 见 §3 第 4 行、§4.1 |
| `ruff check .` / `git diff --check` / `uv lock --check` | — | 全部 exit 0 |
| `git status --short` | — | 空（`src/**`、迁移、信封、`pyproject.toml`、`uv.lock` 零 diff） |

三方一致性探针结论：ADR §6 恰为 **10 行**且行号连续；`companion.StateMatrixRow.case` 枚举与
契约用例 `STATE_MATRIX` 键集**完全相等**；`row_revision_mismatch` **三方均已不存在**；
第 3 行仍带「防御性」字样；7 类禁止机制枚举完整。

**环境事实**：本沙箱内 `Path.mkdir(mode=0o700)` 生成的目录无法枚举（`WinError 5`），而 pytest 的
`tmp_path`/`--basetemp` 正是这样建根 ⇒ 用 `tmp_path` 的用例在沙箱内必然 setup 报错。B 级全量因此
**按沙箱外正常权限路由**执行一次（未改任何 ACL）；focused 用例与全部探针仍在沙箱内完成。

## 6. 交接包 §4 三项高风险问题（更新到本轮基线）

### 6.1 ① `C_strict` 的跨库同窗口证明（M1）与 M2/M3 的构造成本

- **栅栏现状**：产品**没有**可用的跨库栅栏/版本对 —— `LanceVectorIndex._table`
  （`indexing/store.py:134`）总是 `open_table` 取最新版本，无 version pin；`store.py` 全文没有读取
  Lance version/timestamp；SQLite 读走独立快照。M1 的「栅栏标识 + 同窗口 ID/revision 集合相等证明」
  需要**新增机制**。
- **证书核对的量级**（上一轮探针，真实 Lance 表 20 000 行）：读全代每行
  `(record_id, asset_id, space_id, projection_revision)` **36.4–37.6 ms**（≈一次整代 pass），
  同夹具真实候选块路径 **226.9–253.9 ms**（4 块）⇒ **约 6.5×**。
- **对 §12.2 的含义**：P4-02F 记录 30 万档单次 pass 347 ms、A3 形状的无过滤全局查询约 0.37 s。
  若每查询都要一次整代行集/revision 核对，分量已接近 **0.7 s**（未含文本编码与 HTTP）⇒
  **逐行 M1 核对很可能吃掉预算**；更可行的是**代级栅栏**（例如「该代无未确认 outbox 事件」这类
  一次 SQLite 查询）加上 §4.1 的写入次序不变式，把核对变成结构性判断。
  ⚠ 本轮 §3.3 新增的门禁（「引擎可查询行集 == `C_strict`」）**字面实现就是一次整代枚举**，
  这正是需要 Sol 明确「门禁如何由 M1 栅栏满足」而不是裸实现的原因。
- **M2/M3**：仓库内**没有任何**下推等价或停止证书的实现与基准，构造成本**未评估** ⇒ 不得按
  「已有依据」对待。

### 6.2 ② 回退率

- **可测的静态面**：240 组 `scope_condition` 交叉中只有 1 组合格（168 组可评估里 1 组，≈0.6%）；
  其余输入按 §2.2 一律走完整路径。这是**资格面**，不是负载回退率。
- **不可测的部分**：A3 未实现 ⇒「无筛选查询中因状态/窗口回退」的比例没有载体。按 §4.1，
  真实会命中回退的是「引擎有额外未确认行」（§6 第 1/2 行），其频率由**维护/发布与查询的并发密度**
  决定，不是 revision 比较决定 ⇒ 必须由隔离实验卡按真实混合负载报回退率与回退时延（ADR §7.3）。
- **结论**：**未验证、实现前不可测**；不得写成「回退率低」。

### 6.3 ③ `place_state` 之外的同类字段

**已答（本轮复核）**：8 个字段中只有 `place_state` 存在「入口为空但真实谓词非恒真」的形态。
`person_ids` 为空时 `person_condition` 纯短路返回 `"1"`；`place_ids` 为空时的恒真性由 `place_state`
决定；`person_match="all"` 配空 `person_ids` 是显式拒绝（`INVALID_PERSON_FILTER`）；
`include_descendants` 在 `place_ids` 为空时不改变结果集，且不是资格字段。
另记两处保守方向观察：`CaptureRange()`（非 `None`、未设键）谓词为 `"1"` 但按 `capture is None`
判为不资格（过度拒绝、协议侧不可达）；`capture` 已设而规则版本不匹配时谓词为 `"0"`（不可满足）。

## 7. DS-MAX 判定（V3.1 §2.2，如实记录）

逐条核对后**不升 DS-MAX**：

| 条件 | 判定 |
|---:|---|
| 1 跨模块异常且无法证根因 | 未命中（两处归因均已用真实调用链与探针定位） |
| 2 transaction/revision/undo 状态互扰且高推理后仍矛盾 | 未命中（`C_sql`/`C_strict` 关系已收敛） |
| 3 测试通过但代码与 ADR 对不上且无法判断 | **接近但未命中**：短审轮确实出现「ADR 例子与行为不符」，但**能判断**（ADR 文本错、实现与断言正确），且已在 `2900a0a` 更正；本轮复核未再发现对不上的条款 |
| 4 一次修复引发第二/第三处相关回归 | 未命中（`2900a0a` 未改 `src/**`，无行为回归；改动面经三方一致性探针核对） |
| 5 大 diff 多状态机耦合需长推演 | 未命中（本轮 diff 为文档 + 用例文本，产品 diff 为零） |
| 6 Sol 提出问题需大范围反查 | 未命中（Sol 尚未执行） |

若 Sol 在终审时认为需要大范围反查，则按 #6 升级，并在报告中写明编号、证据与待回答问题。

## 8. 未验证项、环境事实与停止点

**未验证（不得当作已通过）**

- A3 与 oracle 的 `Recall@K=1.000` 等价性；五类注入在 A3 实现下的真实前后计数；A3 端到端
  P50/P95（含真实文本编码、HTTP、回退率与回退时延）；真实中文 CLIP 权重、GTX 1660、冷缓存与长时 soak。
- M2/M3 的构造成本；§3.3 门禁由 M1 栅栏满足的可执行形式；§6.2 的真实回退率。
- 「引擎行 revision == 确认 revision」在**全部**可达交织下成立：本轮与上一轮共覆盖
  产物发布/重排程/单事件/批量/审计修复等 6 类步骤，属**实测 + 推理**，不是机器检查的不变式证明。
  未覆盖：generation `RETIRED`/复用、引擎表被外部删除或替换、多 space 同代、手工改库、`STALE` 复兴的更多交织。

**环境事实**：见 §5 的 `WinError 5` / `mkdir(mode=0o700)`；未修改 ACL；B 级全量按沙箱外权限路由执行一次。

**变更控制提醒**：companion 在 `rules_version: 1` 未变的情况下删除了一个枚举成员。当前仓库内
**没有任何产品代码读取该 companion**（仅契约用例读取，已核对），故无消费者影响；但按 ADR §11，
冻结面修改须走独立审查 + Sol 聚焦终审 —— 本轮主审即该项独立审查，**Sol 终审时请显式确认此次枚举删除**。

**停止点**：状态停在 `ENGINEERING REVIEW PASS (revised) / SOL FOCUSED REVIEW PENDING`。
Sol high 冻结通过前**不生成隔离实验卡、不立 A3 实现卡**；`src/**` 保持零改动；
本轮**不存在**需要放宽 §2–§7 以继续的情形。

## 9. 交 Sol high 的压缩交接包（V3.1 §2.3）

| 项 | 内容 |
|---|---|
| 审查基线 | `2900a0a`（工作树干净；`src/**`、迁移、信封、错误码、`pyproject.toml`、`uv.lock` 零 diff） |
| 改动文件与结论 | 本轮主审只新增本报告与状态条目。被审改动面：ADR-0012（§3.3/§4 M2,M3/§6 第 1,3,9 行 + 删第 11 行）、companion（删 `row_revision_mismatch`）、契约用例（矩阵文本 + 两种窗口形状断言）。结论：**7 项边界全通过，0 处必须修正** |
| 待 Sol 裁决 | ①§3.3「引擎可查询行集」定义（物理表 vs 本次查询可返回集）与门禁如何由 M1 栅栏满足（§6.1）；②A3 资格请求下权威复读空转，正确性只在证书（§4.2）；③companion 枚举删除的确认（§8）；④`C_strict` 去留与是否需要「写入次序不变式 + 代级栅栏」的正式条款 |
| ADR 条款与预期/实际差异 | 预期「`after_lance` 会产生 `C_strict ≠ C_sql`」→ 实际该窗口 `C_sql == C_strict`，失败来自多余引擎行（已在 `2900a0a` 更正）；预期「Lance 派生列不是凭证」有反例 → 实际该条**当前无可执行反例**（`assets.state` 只允许 `ACTIVE`，`visible` 恒真），属防御性条款 |
| 符号级定位 | `indexing/store.py:134`（无 version pin）、`:596`/`:775` 引擎写入、`:606`/`:803` 同事务确认、`:404/1187/1233` 条目降级；`artifacts/store.py:378`；`search/retrieval.py::vector_channel`/`_CONFIRMED_BLOCK_SQL`；`search/service.py::_verified_ids`/`_render_page`/`_authority_drift` |
| 最小反例 | 更正窗口：引擎行 {4,5,6}、6 为最近（0.05）且条目 `PENDING_REPAIR` ⇒ `C_sql={4,5}`、真实 `vector_channel` 返回 `[4,5]`；防御性窗口：`C_sql={1,2}`、`C_strict={2}`，真实 `vector_channel` 返回 rev2 的更近行 |
| 受影响测试与真实命令 | 契约 **11/0/0/0**（`test-results/p4-02h-r2-contracts.xml`）；B 级全量 **1709/0/0/48**（`test-results/p4-02h-r2-full.xml`）；探针 `test-results/p4-02h-r2-review-probes.json`；`ruff`/`git diff --check`/`uv lock --check` 全 exit 0 |
| 未验证项 | §8 全部条目（等价性 Recall@K、五类注入计数、A3 P50/P95 与回退率/回退时延、真实模型与最低配置、冷缓存/soak、M2/M3 构造成本、不变式的机器检查） |
| 禁止 Sol 扩大范围 | 不得实现/启用 A3、不得改 §12.2、不得改 `src/**`、信封/错误码/`query_hash`/游标、RRF/BM25/距离、既有 ADR 条款；不得以固定 overfetch/后过滤/计数或采样/索引存在性充当证书；不得接真实照片库 |
