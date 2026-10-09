# P4-02Y-L-IDENTITY-A-FIX — OCR 兜底五处定向修复

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED**。最新独立闭环见[复核报告文末](../reports/P4-02Y-L-review-dsh.md)。独立定向 JUnit 60/60、0 failures/errors/skips，Ruff/diff-check 通过；真实 weights/session/GPU 仍 PENDING。以下修复要求和执行记录保留为历史。

## 输入与范围

AGENTS → development-status → 本卡 → [最新复核报告](../reports/P4-02Y-L-review-dsh.md)文末五条发现 → ADR-0018 §3。复用实现卡已读架构/策略和 required 证据，不重读全篇。基线 `14567c2`，保留 index/worktree 中并行改动。

只修 `ocr/service.py`、`ocr/engine.py`、F6 所需的 OCR 专用队列恢复边界及必要直接测试：

1. F1：恢复本次 auto GPU handoff 所需的旧 CPU CANCELLED/其他适用终态；合法状态迁移、live lease 保护、原子交接。必须证明重复 run/重启不重复执行，不因历史 GPU FAILED 抢回用户后续手动 CPU/GPU 选择；保持 strict checksum。
2. F2：识别安装的 RapidOCR ONNXRuntimeError 中结构化 ORT cause；仅明确 CUDA 故障可触发。未知异常、输入错误和仅含 CUDA 文本的普通异常不得兜底。
3. F3：把确认发生在 GPU session 初始化的 provider 不符归到初始化失败证据；auto 协调 CPU，显式 gpu 仍严格失败/阻塞。result/provider 信封不符不得触发兜底。
4. F4：CPU/GPU 资格探测独立；CPU 能力失败不挡 GPU，反向也成立；共同权重/安装问题如实拒绝。
5. F5：修正 queue limit 计数，预算 2 能执行两个现成任务，也能包含一次 GPU 故障与一次 CPU 兜底；预算耗尽后保留可恢复待办。

反例：`tmp/p4-02y-l/identity-focused-review-20261009/test_review_counterexamples.py`（ignored，需移入 tracked 测试并完善反向断言）。最终缺陷 JUnit：`test-results/p4-02y-l-focused-review-20261009-final.xml`，20 项中 15 pass / 5 failures / 0 errors / 0 skips。

## 最小验证与收口

运行 tracked identity/fallback 合成文件与上述新增回归，必要时加受影响 contract/scheduler 直接节点；项目 `.uv-cache`、新的 `.pytest-tmp`、JUnit XML。补旧 CPU 取消/失败后 handoff、真正关闭/重开 catalog 后恢复、显式 GPU 严格、已包装 CUDA/输入/未知异常、失败保持旧 current/FTS 及两任务预算。运行受改文件 Ruff 和 diff-check。未变化 required 证据复用，不默认全仓、GPU测速、真实图库或打包。

修复完成更新实现报告、review 报告与 development-status；复核只覆盖五条原发现、修复新 diff 和直接回归，由独立当前审核会话即可，不为 DS-H 名称再跑整轮。未完成真实 pinned weights/session/GPU 验证仍记录 PENDING，不标 OCR 总 PASS。不得加载/下载模型、访问真实图库/服务、改 ACL、提交或推送。

## 本轮执行记录

- 已修复五条发现；另补旧 CPU `FAILED` 终态恢复。手动 CPU/GPU 选择会更新持久 request marker，旧 auto GPU failure 不会夺回人工选择。
- 定向 JUnit：`test-results/p4-02y-l-identity-fix-final4.xml`，53 项 / 53 passed / 0 failed / 0 errors / 0 skipped；包含 `tests/integration/test_p4_02y_l_identity.py` 与直接 scheduler 节点。
- 受影响文件 Ruff 和 `git diff --check` 通过。尚需独立聚焦复核原五条发现、新 diff 与直接回归；pinned weights/session/GPU 仍 PENDING。

## 聚焦复核后的最小收口：仅 F6 / F7

独立复核原五条均关闭。JUnit `test-results/p4-02y-l-focused-rereview-20261009.xml` 为 61 tests / 58 passed / 3 failures / 0 errors / 0 skips，三个新反例证实两处缺陷；Ruff/diff-check 通过。原执行记录保留为历史。

1. F6：恢复普通全局 FAILED/CANCELLED 终态矩阵；通用 `resume_blocked` 拒绝非 BLOCKED 终态。OCR 旧 CPU 分支特殊恢复必须单独约束 task/profile、handoff marker、lease-free 与 CAS；不复活其他任务。补普通 BLOCKED 正例、非 OCR FAILED/CANCELLED 拒绝、OCR CANCELLED/FAILED handoff 正例。
2. F7：GPU provider 无法验证的 OCR_SESSION_PROVIDER_UNVERIFIED 不得经统一能力错误进入 fallback。保持具体 probe 错误与交接 allowlist；已确认 GPU init/provider mismatch 可自动兜底，真正缺 GPU 资格/能力仍可兜底，显式 GPU/result envelope 仍严格。

直接反例 `tmp/p4-02y-l/identity-focused-rereview-20261009/test_rereview_boundaries.py` 的有效节点已对应迁入 tracked identity 测试。只跑新增反例与直接 identity/scheduler 回归，新的项目临时目录、JUnit、受改文件 Ruff/diff-check。修复后仅审核 F6/F7 与新 diff；原 F1–F5 不重做裁决或大范围测试。没有新协议需求，SOL_NOT_REQUIRED，不再要求一整轮 DS-H。真实 weights/session/GPU 继续 PENDING，不自动启动其他任务。

### F6/F7 本轮执行记录

- 还原全局 `FAILED`/`CANCELLED` 终态不可转 `QUEUED`；`resume_blocked` 显式要求当前状态为 `BLOCKED`。OCR auto handoff 使用 service 内专用 CAS，限制 `task_type=image_ocr`、CPU profile、`requested_device=auto`、lease-free 与原终态匹配，不开放通用队列终态恢复。
- `OCR_SESSION_PROVIDER_UNVERIFIED` 在 fallback 决策处明确拒绝，并写回 GPU job 的 `last_error_code` 作为持久标记，后续能力快照变化也不会把它洗成普通能力缺失；scheduler 后续只有重新确认资格后才能恢复 GPU job。
- tracked identity/scheduler 节点、旧五反例和三个复核反例合并 JUnit：`test-results/p4-02y-l-f67-fix-1.xml`，65 项全部 passed、0 failures/errors/skips。受影响 Ruff 和 diff-check 通过。F6/F7 独立复核待执行；真实 weights/session/GPU 仍 PENDING。
