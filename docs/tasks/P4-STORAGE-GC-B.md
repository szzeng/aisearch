# P4-STORAGE-GC-B — LanceDB Vector GC 与 SQLite 离线维护

日期：2026-10-10；开工状态更新：2026-10-11。状态：**IMPLEMENTED / 任务卡定向验收通过**。GC-A 前置条件为相关定向测试通过，独立 GPT-6 Luna / High 复核按用户批准的路由例外接受；这不是 DS-H PASS。GC-A 全量 D 级失败仍作为历史问题单独保留，不作为本卡开工阻塞。立卡基线：`6210c4df3a80560b6acb22aae97356802af2ea15`。本卡实施核实最新 LanceDB 0.38.0 API；验收仅限四组定向测试、Ruff 和 CLI 冒烟，未跑全量回归。详情见[执行报告](../reports/P4-STORAGE-GC-B.md)。推荐实施模型：**GPT-6 High**。个人相册、四组定向测试，聚焦索引一致性与数据安全。

## 1. 目的

对**仍在正常服务的图像向量 generation**进行体检；按需维护标量索引、压缩 LanceDB fragment、清理可安全回收的旧**物理版本**。另提供可选停机 `VACUUM` 回收 SQLite 空闲页。明确区分磁盘释放与检索加速，两者都只记录实际观察，不设无依据的强制提升比例。

不改变检索算法、`SearchProfile` 指针、`index_generations/index_entries/index_outbox` 语义，不新建 ANN/FAISS，不变更 embedding 模型或维度，优先不新增 schema migration。

## 2. 现状与关键风险

- `indexing/store.py::LanceVectorIndex` 按 generation 分表，`upsert_batch` 使用 `merge_insert(record_id)`；写入与确认经 `IndexCoordinator` 协调。
- `ensure_scalar_index()` 已维护 `asset_id_idx`，当 `num_unindexed_rows > 0` 时会 `replace=True` 重建索引。历史报告指出覆盖不足可造成明显的候选块检索性能下降；不能把检索慢一概归咎“碎片”。
- `indexing/selection.py` 有覆盖/健康检查，但 `LanceVectorIndex.count()` 现经 `rows()` 全量 Python 物化；GC 体检**不得调用这种 30 万行/完整向量物化路径**，优先 `count_rows()`、索引元数据和有界的必要列读取。
- `search/profile.py` 原子绑定活跃 SearchProfile 与图像 generation；`indexing/maintenance.py` 的 replay/repair 依赖 `artifacts` 和 `index_outbox`。
- `face_embeddings`、`face_candidates` 的向量实际位于 SQLite，**不属于 LanceDB GC**。本卡只报告其逻辑 BLOB 大小；不删 face 向量/候选数据，也不清理人物操作历史。
- **LanceDB 的 table version 与 SQLite 的 index generation 是两种不同生命周期**，清理旧表物理版本不等于允许删除整个旧 generation。

## 3. 停机要求与入口

所有物理优化为**离线维护**：

1. 用户明确停止当前应用/服务，并指定 `--stopped --apply`。
2. 独占获取 `acquire_data_root_lease`，锁占用时拒绝；不强行关闭用户服务。
3. 确认 SQLite WAL、读写句柄、活跃写入/备份状态可安全维护；前置条件无法证明就报告 `skipped`。
4. 检查磁盘可用空间足以同时容纳压缩新文件与尚未删除的旧文件，并留合理余量；不足则拒绝，不尝试边删除边腾空间。
5. 仅访问受控 `vectors` 和 `catalog` 路径，不扫描/写入源照片文件夹。

CLI 在 A 卡 `storage inspect` 下增加 `storage optimize-vectors --stopped --apply`、`storage vacuum --stopped --apply`（可调整语法以符合现有 CLI）。**默认 dry-run**。HTTP/UI 仅报告状态与命令指引，不假装运行中的网页点击可以执行停机压缩。

## 4. 向量状态诊断 — 先验证再优化

对每个 generation 列出：

- `generation_id/space_id/processor_id/dimension`、`state/is_active`、是否被当前/候选 SearchProfile 引用；
- LanceDB 表是否存在、能否打开、`count_rows`、fragment 数、物理大小、版本与索引信息（仅在所锁版本支持时报告）；
- `asset_id_idx` 是否存在、`num_indexed_rows/num_unindexed_rows`（不支持时显示 `unknown`，不可假装 0）；
- SQLite `index_entries` CONFIRMED/STALE/PENDING_REPAIR 和本 generation 未确认 `index_outbox`；
- 历史版本**可否安全清理**与总大小；若 LanceDB 不提供可信可回收字节数，则显示 `unknown`，不把总文件大小算成可释放量。

若表缺失、维度不匹配、权威状态不稳定、需修复积压或候选世代正在构建：**跳过维护并给出明确原因**，不自动重算模型、不用压缩修复业务不一致。

## 5. 按需执行顺序

**B1. 索引覆盖核对**：先判断当前服务 generation 的 `asset_id_idx` 是否不完整。需要时复用 `ensure_scalar_index()` 做明确授权的受控修复，再核对结果，不编写第二套索引重建器。索引元数据不可判定时记录并跳过该修复，不能无条件重建。

**B2. 碎片压缩**：仅对确有碎片/物理整理需求且健康的表调用当前锁定版本支持的 `compact_files()`。不得直接删除 Lance 内部 `_versions`、`_indices` 或其他文件；禁止不核对 API 就调用全局优化组合。压缩可能制造新版本并暂时增加空间。

**B3. 旧表物理版本回收**：压缩结果可读及索引覆盖通过后，用 LanceDB 原生 `cleanup_old_versions()`。首版默认保留至少 **14 天**，`delete_unverified=False`；参数名和兼容行为以实际锁定版本校验为准。不使用零保留期/激进选项，不手动删内部文件。旧版本处于保护期时，诚实报告本次可能**不释放磁盘**。

**B4. 结果验证**：维护前后核对当前 generation、表实际行数、关键列摘要、标量索引覆盖与固定检索 `(asset_id, distance, order)`。发现行数或结果不等价时不可标 PASS，保留现场证据；不得通过篡改 `index_entries` 硬置 CONFIRMED 来消除错误。

全流程每个阶段单独报告 `done/skipped/failed`、原因、耗时、文件/版本变化、实际回收字节。压缩和清理不保证跨 SQLite/Lance 原子事务，必须在阶段边界可安全重试；中断后优先确认当前表可用再恢复后续维护。

## 6. 严格排除“逻辑向量删除”

当前 LanceDB 表里存在来源已删除的 asset 向量时，本卡**只统计，不调用 `delete(asset_id)`**。现有 `audit_and_schedule_repairs()` 可能根据 Artifact 和 outbox 将旧向量重新发布；SQLite `0003` 虽允许 `DELETE_VECTOR` 事件类型，不能由此推定当前生产者/消费者、排序、重放和一致性契约完整。

如将来实际统计证明无来源的资产和当前表逻辑向量占用值得治理，另立独立卡设计：权威失效状态、DELETE_VECTOR 生成与消费、revision 排序、恢复审计、人工信息保留、故障重放和索引一致性。**不得偷并入 B 卡。**

同理：不删除 SQLite `assets/faces/face_embeddings/face_candidates`，不主动淘汰 RETIRED generation 表，不因 `is_active=0` 就认为表能物理删除；候选/历史引用需要独立资格合同。

## 7. SQLite VACUUM（可选，独立动作）

- 先读 `PRAGMA page_count/page_size/freelist_count`，显示内部可复用页估计。空闲比例或可回收量很小时建议直接跳过；不强制为每次 GC 做 VACUUM。
- 在**停机独占**、安全 WAL checkpoint、`integrity_check` 通过、可用磁盘空间充足时才执行。保守核对 VACUUM 所需临时空间，不因为空间不足冒险运行。
- 不修改 schema/journal_mode、不删除业务历史、不删除人脸/地点/OCR/人物数据。VACUUM 是**物理整理**，不是业务 GC。
- 维护后重新检查 DB 可打开、Schema 未变、关键表与 SearchProfile 指针不变、数据完整性正常。

## 8. 最小验收（四组）

- **B1 向量压缩**：合成真实 LanceDB 表多批 `merge_insert` 和历史版本；压缩前后当前行数、ID/向量、距离、全序一致；记录 fragment/文件字节变化。
- **B2 索引覆盖**：构造未索引新行，检查覆盖→复用标量索引维护→覆盖恢复；如果依赖版本无指标须报告 `unknown` 并安全跳过。
- **B3 版本/互斥/中断**：旧版受 14 天保护，活跃/候选/退休世代保护，服务持锁拒绝，压缩中断后重启可读且重试幂等；禁止 `delete_unverified=True`。
- **B4 SQLite + 检索**：隔离 SQLite VACUUM 前后 integrity_check、表计数、人物/合集/地点/OCR/历史摘要及当前 SearchProfile 不变；对固定少量向量比较检索结果与次数/耗时，只作观察不设强制提升百分比。

仅跑相关 pytest、Ruff 和最小 CLI 检查；不访问真实 PhotoSearchData、不开真实 AI 权重/GPU、不跑 30 万全库和全仓矩阵。

## 9. 交付与 PASS

提交代码、定向测试、简短的阶段/实际释放量报告与状态更新。PASS 必须保证**当前检索结果不变、索引覆盖不回退、没有意外模型重算或修复积压、数据根互斥正确、中断可恢复、原图/人物/人工事实零变更**。如只完成压缩、历史版本仍在保留期内，必须如实报告零或未知物理收益，不得宣称“已回收全部空间”。
