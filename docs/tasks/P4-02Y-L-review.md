# P4-02Y-L — OCR 版本化身份与兜底新增面独立工程复核

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED**。
最新闭环见[报告文末](../reports/P4-02Y-L-review-dsh.md)。F6/F7 独立复核通过；定向 JUnit 60/60 passed、0 failures/errors/skips，Ruff/diff-check 通过；实现轮 65/65 证据已核对。F1–F5 保持关闭，未见新增阻塞问题；不另跑整轮 DS-H/Sol。真实 weights/session/GPU 仍 PENDING，不标 OCR 总 PASS、不自动接续其他任务。以下原始受审范围保留为历史。

## 输入与结论边界

- [OCR 主卡](P4-02Y-L.md)、[实现卡](P4-02Y-L-IDENTITY-A.md)、[实现报告与验证证据](../reports/P4-02Y-L-IDENTITY-A.md)。
- 新冻结依据：[ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)；已完成的身份裁决：[Sol 报告](../reports/P4-02Y-L-artifact-identity-sol.md)。
- 既有 OCR 共享面主审：[P4-02Y-L-review-dsh.md](../reports/P4-02Y-L-review-dsh.md)。复用它覆盖的未变 scheduler/parser/session/provider/device 等语义；独立复核实现卡全部新 diff 与直接受影响调用链。
- 实现基线 `14567c2`，当前 worktree 还含并行 OCR 文档、诊断、夹具和 `.tmp` 改动。复核前先看实际 HEAD/index/worktree 和报告中列出的命令/版本，只把可归属本实现的新增 diff 作为目标。

结论限于新实现工程复核与合成证据。pinned OCR weights、真实 ONNX session/provider、真实 CUDA 故障和现场真实图库均为 PENDING；不得标 OCR 总 PASS 或正式发行 PASS。复核完成后仍不自动启动模型切换主审。

## 必须完整覆盖

- 实现报告所列全部新增/修改文件及直接调用链：两份 ProcessorSpec/config hash、enqueue/force、GPU 资格和错误分类、SQLite fallback handoff/restart、live lease/待执行竞争、双 profile scheduler dispatch、artifact/ref/payload checksum、OCR current/regions/FTS 原子事务、legacy `ocr-text@1` reader/pending/live、CLI/status 与 refresh 进度摘要。
- 与 ADR-0018、ADR-0003 和实现卡逐项对照。确认实际 provider/device 绑定 job、result、payload、artifact、current projection；显式 GPU 仍严格；同配置字节冲突仍零 current/FTS 写入；人工/其他 catalog 状态不受影响。
- 核对错误分类证据：只有 GPU 缺资格/能力及结构化识别的 GPU session 初始化/推理设备故障触发 auto CPU；权重、输入、lease、processor、provider/result envelope、checksum、低质量成功、未知异常不触发。查明本地 ONNX Runtime 异常类型判断是否足够具体；若证据不支持，提交最小反例并升级相关边界复核。
- 检查并发/恢复反例：同资产/input 两 profile 串行，live lease 不切换；BLOCKED GPU 被合法取消且故障原因保留，短事务中只排一个 CPU job；重启不会复活 GPU 覆盖 CPU；force 双向不 UPDATE 旧 processor identity；过期提交和失败保留旧 current。
- 核实 `test-results/p4-02y-l-identity-a.xml` 的 152/133 passed/0 failures/0 errors/19 skipped 与报告对应，19 skip 均为缺 pinned weights，15 个新合成用例零 skip；检查 Ruff 和 `git diff --check` 证据。不要把 skip 写成通过。已核对；补测最终 JUnit 20/15 passed/5 failures/0 errors/0 skips，5 个新反例确认实现缺陷。

## 复核动作与停止点

先独立完成完整受影响面审查并写报告。任务报告至少列出文件/调用链覆盖、每条高风险不变量、证据版本、发现、未决问题和结论。没有偏离冻结及高风险未决时可记录 `ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`；不得因当前审查模型不是 DS-H 而假称满足 DS-H。发现普通实现缺陷时给出最小反例并交回实现；修复后只复核原缺陷、新 diff 与直接回归。只有 V3.1 §2.2 的已证据条件才建议 DS-MAX；只有冻结/高风险语义仍未决才交 Sol 聚焦裁决。

不要重跑不受影响的全仓、30万、GPU测速、打包或 historical Sol。只有证据缺失/矛盾才补对应基础测试；如果补测，使用项目 `.uv-cache`、新 `.pytest-tmp` 目录和 JUnit XML，不以收集结果代替执行通过。不要下载/加载模型、访问真实图库/服务、改 ACL、提交或推送。

复核输出更新 `docs/reports/P4-02Y-L-review-dsh.md` 与 `docs/development-status.md`。身份实现复核结束后停止；主 OCR 任务和真实资源状态按证据保留，是否继续其它 OCR/模型切换工作由用户另行决定。

原审核启动文本（历史，已执行新增面审核；当前应进入定向修复）：

```text
按 AGENTS.md → docs/development-status.md → docs/tasks/P4-02Y-L-review.md 执行 OCR 版本化身份与兜底新增面的独立 DS-H 完整工程复核。先检查实际 HEAD/index/worktree 和实现报告，再逐项审查本实现新增 diff、直接调用链、ADR-0018 不变量及 test-results/p4-02y-l-identity-a.xml 证据。复用 P4-02Y-L-review-dsh.md 对未变共享面的结论，不重跑无关全仓/历史实验。重点审查 CUDA 故障分类、GPU→CPU 原子交接/重启、profile 并发与 force、唯一 current/FTS 原子性、legacy reader 和严格 checksum 拒绝。真实 weights/session/GPU 仍 PENDING；mock 不得代替真实 GPU 证据。写更新报告和 development-status；如有实现缺陷给最小反例后停止交回，不自动开始模型切换审查。不访问真实图库/用户服务，不加载模型，不提交或推送。
```

## 修复后的当前执行范围

原五条 finding 已修复。复核时验证：旧 CPU CANCELLED/FAILED 仅在对应 auto GPU 分支下恢复，live lease 不受影响，重复 handoff/catalog 重开不重复执行；手动 CPU/GPU 选择会压过历史 GPU FAILED；RapidOCR 包装 CUDA cause 的 allowlist 不接受输入、未知及普通 CUDA 字符串异常；GPU session-init provider mismatch 只对 auto 触发 fallback，显式 GPU 仍严格，result envelope mismatch 仍拒绝；CPU/GPU probe 独立且共享 weights/install 故障拒绝；queue limit 按执行次数且耗尽后保留 CPU pending；失败保持旧 current/FTS。

验证证据：`test-results/p4-02y-l-f67-fix-1.xml`（65/65 passed，0 failed/errors/skips），受影响 Ruff 与 diff-check 通过。核对当前 JUnit 与 F6/F7 diff；除非发现覆盖缺口，不重跑完整 identity required 列表。真实 weights/session/GPU 保留 PENDING，不标 OCR 总 PASS，不访问真实图库/用户服务、不提交或推送，不启动模型切换任务。
