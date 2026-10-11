# P4-OCR-LIFECYCLE-A — OCR 退役任务收尾与进度修复

日期：2026-10-11。当前状态：**SCOPED PASS / LUNA H REVIEW PASS**。

## 实施摘要

- 在 `ocr/contract.py` 集中声明当前可执行与明确退役的 processor ID；调度进度只使用当前集合，清理只针对已声明退役集合，未知 ID 不处理。
- 新增 OCR 局部 `reconcile_retired_ocr_jobs()`：每批最多 100 条，使用 SQLite 短事务和状态、processor、五字段身份及 lease 字段 CAS。仅取消合法状态；有效运行租约保留，过期且结构完整的运行租约才收尾；缺失/异常租约保留并报告。退役原因写入 job 错误字段，释放 token、worker 和租约字段。旧终态不改写。
- Catalog 就绪、OCR 入队和 OCR 运行入口都会调用同一有界收尾函数；启动清理失败只记日志，不阻止 Catalog 启动。CLI 增加 `reconcile --dry-run/--apply --limit --json`。
- OCR summary 的有效 pending/failed/blocked 按当前 processor、当前资产 SHA 和 distinct asset 计算；旧任务保留在原始 `jobs` 分类和退役诊断字段中，不再制造 `LAGGING`。来源进度优先识别当前文档（包括空文本和 legacy 当前文档），再按活动任务、BLOCKED、FAILED、未处理分类，CPU/GPU 不重复计数。来源卡显示失败/受阻/等待数量；只有有可运行余量时才提供继续建库。
- 未改 schema、全局 job 状态矩阵、OCR artifact/current 写入协议或旧结果；未扫描/修改真实 `PhotoSearchData`，未触碰真实 OCR 队列。

## 定向验证

JUnit：[`test-results/P4-OCR-LIFECYCLE-A.xml`](../../test-results/P4-OCR-LIFECYCLE-A.xml)，**10 passed / 0 failed / 0 errors / 0 skipped**。

- 安全收尾：QUEUED/BLOCKED 与明确过期 lease 取消；未过期 lease 与异常 lease 保留；过期提交经现有 `validate_submission` 拒绝；异常 lease 不允许竞争配置入队。
- 幂等/自动入口：重复执行零新增修改；批次上限与后续批次；Catalog 启动、正常 OCR 入队自动触发；模拟 `@2` 退役、`@3` 活跃和未知 processor 不处理。
- 进度语义：legacy 当前空结果计为成功；双配置同资产去重；GPU 失败而 CPU 排队计为等待；BLOCKED、FAILED、未尝试分离；legacy pending 不进入有效 OCR pending。
- 数据保护/UI：现有 current 文档、regions、artifact、投影队列及终态行保持不变；来源卡失败时不显示“处理中”或提供继续建库，存在未尝试且可用时显示待处理并允许继续。
- 受影响 Python 文件 Ruff、Python 编译、`node --check src/photo_search/web/app.js` 和 `git diff --check` 均通过。

额外尝试收集既有 `tests/integration/test_p4_02y_l_identity.py` 时，pytest 在未修改且 HEAD 已含的第 824 行字面量 `\n` 处报 `SyntaxError`，该文件未能收集；未改动该无关基线。卡片要求的本次租约迟到提交与历史结果保护反例由上面的新 synthetic pytest 实际执行。

## 复核与未验证项

- 独立复核：按用户明确指定由 **GPT-6 Luna / High（LUNA H）**完成；结论 **LUNA H REVIEW PASS，finding=none**，无阻塞问题或需修复项。不做 DS-H。
- 复核检查了七个 OCR 实现文件及直接入口，确认退役任务范围、lease 安全条件与 CAS、自动入口复用、当前版本/资产 SHA 去重、来源状态与 UI 行为均符合任务卡；复核者检查了本报告 JUnit 证据与 `git diff --check`，未重跑测试。
- 额外收集 `tests/integration/test_p4_02y_l_identity.py` 仍因 HEAD 中未修改的第 824 行字面量 `\n` 报 `SyntaxError`；此非本次定向 JUnit，也未归因于本任务。卡片要求的租约迟交拒绝和历史结果保护反例由本任务测试实际执行。
- 本报告不证明真实图库中 910 条诊断旧任务已清理，也不证明真实 CUDA/GPU、OCR 权重、原生服务重启或浏览器实测；本会话未访问这些资源。
- 全仓回归、真实图库、GPU/模型、性能及打包均未运行，不代表全仓或 OCR 生产运行 PASS。

## 用户自主实测

在自己的受控环境中可执行 `photo-search-ocr status --json`（或 `python -m photo_search.ocr.cli status --json`）核对 `pending_jobs`、`retired_pending_jobs`、`retired_live_leases` 与 `retired_lease_anomalies`；来源卡核对实际失败/受阻/待处理数量。实施会话没有替用户操作真实图库。
