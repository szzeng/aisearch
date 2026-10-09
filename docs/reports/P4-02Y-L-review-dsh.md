# P4-02Y-L — OCR 个人基础工程主审

日期：2026-10-09。审查基线：`14567c2`；OCR实现：`c1d0a34`；后续共享运行时改动：`9ebcbef`。按用户指示由当前 Codex 会话担任主审。最新结论：**ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED**。结论限于已审工程与合成范围，真实 weights/session/GPU 仍 PENDING。下面原主审与发现保留为历史，最新闭环复核见文末；本次未声称使用 DS-H。

## 覆盖范围

复核了 OCR 变更完整 diff 与直接调用链：ADR-0003 §1–6 和 companion schema；scheduler processor parser、资格矩阵、dispatch/result provider 校验；OCR spec、runtime evidence、CLI/device 参数、RapidOCR 三个实际 session 检查、CPU/GPU cache key、执行错误映射；artifact prepare/persist 与 OCR projection 事务；诊断 smoke/compare/build 输入与 JSON；相关单测、集成测试和已有 Sol/实现报告。后续 `9ebcbef` 的 ORT preload 与 CLIP/Qwen 导入隔离也已核对；本地 ORT 1.30.0 API doc/source 确认 `directory=""` 使用 NVIDIA site-packages 和默认路径。

模型身份三件套、`structured_model` 禁止向量语义、`non_model` CPU 约束、device qualification 和 session/provider 拒绝链与已冻结 ADR 一致；未发现隐式 GPU→CPU 成功回退。没有 schema 或 migration 变更。

## 高风险发现：CPU/GPU 结果共用逻辑身份，但结构化字节不同

实现用 `artifact_id_for(input_revision)` 产生 `ocr-document:ocr-text@1:<input_revision>`；artifact ID 没有设备维度。`build_payload` 会原样序列化每个 region 的浮点 `confidence`，`artifact_ref` 对完整 payload 计算 checksum。`ArtifactStore.persist_result` 遇到相同 artifact/logical key 时要求 checksum 相同，否则抛 `ARTIFACT_CONFLICT`。OCR 的 force re-recognition 仍复用相同 job/processor identity。

实现报告中的真实 dense CPU/GPU fixture 已有非零置信度差，最大 **0.0062746**；现有对照仍将其按 `0.03` tolerance 归为 equivalent。使用该历史 JSON 中的 dense documents、同一 input revision/location 重建 artifact 后，得到：

| 设备 | artifact ID | checksum |
|---|---|---|
| CPU | `ocr-document:ocr-text@1:aaaaaaaa…` | `47c91005c55c11554bb9c40c10ebec8cd59046b90f81ec43379f6284654bccc4` |
| GPU | `ocr-document:ocr-text@1:aaaaaaaa…` | `2b57ed5630a014ccb18ef1cfb28908a8d8c920086b7c114f670d937ce3ba3560` |

也就是同 ID、不同字节。若已有 CPU 结果后以相同输入 revision 执行 `--force --device gpu`（或反向切换），持久层会拒绝新 artifact，OCR current projection 不会完成更新。这里不只是浮点展示误差：confidence 已写入 structured artifact 和 `asset_ocr_regions`，低置信度汇总也由其计算。

这与 Sol 冻结“仅当 CPU/GPU 输出可复用时才复用 processor/artifact identity”的条件存在冲突。比较使用语义 tolerance 证明了识别结果接近，却没有证明当前字节级 artifact identity 可复用。该决定触及冻结身份/持久化语义，需先做一次 Sol high 聚焦裁决；不在本轮自行量化/丢弃 confidence 或更换 artifact identity。

## 本轮安全修正

- 诊断 smoke、CPU/GPU compare、refresh/build 曾对任意传入的 `--work-dir` 递归删除，refresh/build 还会向该路径写入并扫描。新增工作目录保护：仅接受项目 OCR 临时目录、pytest 临时目录或系统 temp 下的新/空目录；有内容的目录和非空父目录均拒绝且保留原数据。CLI 默认改用新的项目临时目录。
- OCR 端到端测试仍将默认 `auto` 锁成 CPU，并读取已删除的 `run.provider` / `run.precision` 字段。现改为核对 `auto` dispatch 与当前实际资格/provider 一致，并单独覆盖显式 CPU 请求；没有放宽产品行为。

## 验证与证据状态

- 定点 JUnit：**11 passed / 0 failed / 0 errors / 1 skipped**，文件 `test-results/p4-02y-l-review-20261009-final3.xml`。唯一 skip 是需要本机 pinned OCR weights 的显式 CPU 真实集成节点；权重未安装，按规则没有把它报成通过。
- 定向 Ruff、三个诊断 CLI `--help`、`git diff --check` 通过。
- 既有 required JUnit：138 项中 **119 passed / 19 skipped / 0 failed / 0 errors**；不能写成 138 全过。2026-09-30 历史 smoke、CPU/GPU compare 与 synthetic build 记录仍按原版本有效：auto GPU 3/3、CPU 3/3、6.199×、输出 tolerance 通过、build 3/3 CUDA jobs。它们不证明 `9ebcbef` 后真实 DLL/session 已重测，也不消除本报告发现的 identity conflict。
- 本轮没有加载模型或创建真实 OCR session、没有跑 GPU/CPU 真实对照、没有访问私人图库/服务、没有构建 exe、没有提交/推送。集成测试因 pinned weights 缺失而保留 PENDING。

## 下一步裁决

见[最小 Sol 裁决卡](../tasks/P4-02Y-L-artifact-identity-sol.md)。裁决需确定在保留当前 tolerance/人工置信度语义的前提下，CPU/GPU 结果能否共用现有 processor/artifact identity；并指定最小可实现的身份或规范化策略及需要的反例。裁决前保持此主审为 **PENDING**，不把任务标 PASS，也不交接模型切换审查卡。

## 2026-10-09：版本化身份与兜底新增面的独立聚焦复核

用户授权当前会话审核个人项目，DS-H 为建议模型，不另开重复轮次。本会话此前负责 ADR-0018 裁决，未编写本轮受审产品实现；本次独立读取实现 diff、直接调用链和证据。身份裁决已经完成，没有新的冻结语义争议。结论 **ENGINEERING REVIEW FINDINGS / FIX REQUIRED / SOL_NOT_REQUIRED**，不能标实现或 OCR 总 PASS。

### 范围与不变量

- 基线 HEAD `14567c2afd5755ff5e151f95576f6454905f6dcc`；index 空。受审为当前未提交 worktree 的 IDENTITY-A 实现。既有诊断、夹具、端到端测试与 `.tmp` 并行改动保留，不混入本次产品修改。
- 完整读取新增受影响面：OCR `contract/engine/processor/service/cli`，scheduler `engine/projection`，refresh `pipeline/progress`，unit `test_p3_ocr_contract`、integration `test_p3_ocr_lifecycle/test_p4_02y_l_identity`；追踪 jobs 五字段唯一键/状态迁移、资格/dispatch/result 校验、artifact checksum 与 OCR projection 事务。复用上述历史主审对未变共享面的结论。
- CPU/CUDA 独立 ProcessorSpec、device policy、config hash、job/result/payload/artifact 绑定及 legacy reader 的新增接入未见新的直接缺陷；原始 confidence 与严格字节冲突拒绝保持。两个 v2 profile 通过同一个 scheduler 事务 hook 写唯一 current、regions、text revision 和 FTS 待办。live lease 检查、失败/取消/过期提交保留旧 current，原有合成断言实际通过。结论限于静态和合成证据。
- GPU 自动兜底的资格探测、异常包装和旧 CPU 任务恢复存在以下可执行缺陷。原有新测试把 `record_runtime_evidence` 整体替换为健康合成证据，因而未覆盖真实资格探测入口；异常分类用裸 ORT 异常，未经过已安装 RapidOCR 包装。

### 发现与最小反例

**F1 / P1 — 旧 CPU 任务被取消后，自动兜底没有可执行分支。**

位置：`src/photo_search/ocr/service.py:366–389`，`src/photo_search/jobs/queue.py:187`。先排 CPU，再选择 auto；enqueue 合法取消 CPU，GPU 随后发生 `OCR_GPU_EXECUTION_FAILED`。协调层看到同唯一键 CPU 记录仍只 `jobs.enqueue`，其 ON CONFLICT DO NOTHING 不恢复 CANCELLED。实际 GPU=FAILED / CPU=CANCELLED，没有 CPU 当前结果。独立反例 `test_fallback_reopens_cpu_cancelled_by_gpu_selection` 已确认。修复需使本次 handoff 能恢复必要旧 CPU 分支，同时保持一次交接、live lease、人工选择与重启幂等；不能每轮扫描历史 FAILED GPU 就无条件复活 CPU。

**F2 / P1 — RapidOCR 包装后的 CUDA 推理异常无法分类。**

位置：`src/photo_search/ocr/engine.py:420–454`。本机 RapidOCR 1.4.4 `utils/infer_engine.py:192–199` 在 session.run 抛错后执行 `raise ONNXRuntimeError(...) from e`。当前 `_is_onnx_cuda_failure` 只看外层类型 module/name，包装类来自 rapidocr_onnxruntime，直接返回 False。已用真实安装的 OrtInferSession 包装代码和实际 ORT RuntimeException（CUDA/CUBLAS）构造反例，无真实 session/model；实际抛 ONNXRuntimeError，未转换 `OCR_GPU_EXECUTION_FAILED`。对应 `test_real_rapidocr_wrapper_preserves_cuda_failure_classification`。需受控沿已知包装的结构化 cause 判断；未知异常、InvalidArgument 和仅带 CUDA 字符串的普通 RuntimeError 仍不得兜底。

**F3 / P2 — GPU session 初始化 provider 不符直接中断，未进入 auto CPU 兜底。**

位置：`src/photo_search/ocr/service.py:249–253`，`src/photo_search/ocr/engine.py:380–391`。PinnedOcrEngine 在初始化观察到 GPU session 实际为 CPU 时抛 RUNTIME_RESOLUTION_MISMATCH，但 probe catch 只接受 capability-unavailable / GPU-initialization-failed，run_queue 在调度前退出。反例 `test_gpu_initialization_provider_mismatch_allows_cpu_handoff` 已确认。这也是本机 ORT 初始化 fallback 后可能出现的路径（本轮只读安装代码，无真实故障验收）。按 ADR-0018 §3 区分已确认的 session 初始化错误与 result envelope 设备不符：前者拒绝 GPU 后可协调 auto 兜底，后者必须继续拒绝且不能兜底。不可全局放宽 RUNTIME_RESOLUTION_MISMATCH。

**F4 / P2 — GPU 首选路径被 CPU 探测失败挡住。**

位置：`src/photo_search/ocr/service.py:245`。无保护地先创建 CPU engine；CPU 能力故障会在 GPU 探测前退出，即使 GPU 本可正常执行。反例 `test_cpu_probe_failure_does_not_prevent_gpu_probe` 的实际调用序列仅 `[cpu]`。需独立记录两个 profile 的设备健康和资格，不能把 CPU 可用设成 GPU 首选/显式 GPU 任务的前置条件；权重或共同安装问题仍如实失败。

**F5 / P2 — limit 同时递减和与递增 steps 比较，提前结束。**

位置：`src/photo_search/ocr/service.py:426–432`。两张不同内容的合成图均可运行，limit=2 却只处理 1 项、留下 1 项 pending。`test_limit_two_executes_two_available_jobs` 已确认。只保留一种计数方式，并覆盖限定预算内 GPU 失败→CPU 的两次执行；budget 耗尽可以留待下次恢复，但不应提前消耗。

### 执行证据

核对既有 `test-results/p4-02y-l-identity-a.xml`：152 tests / 133 passed / 0 failures / 0 errors / 19 skipped，19 条原因全为缺 pinned PP-OCRv4 weights。实现报告与 XML 一致；旧 review 卡 151/132 和 14 新节点是过期文案，已修正为 152/133、15。未重跑整个 required 列表。

新增反例保存在忽略目录 `tmp/p4-02y-l/identity-focused-review-20261009/test_review_counterexamples.py`；修复时将有效回归移入 tracked 测试。最后一次实际执行：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = ((Get-Location).Path, (Join-Path (Get-Location) 'tests'), (Join-Path (Get-Location) 'tests/integration')) -join ';'
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-focused-review-20261009-final --junitxml=test-results/p4-02y-l-focused-review-20261009-final.xml tests/integration/test_p4_02y_l_identity.py tmp/p4-02y-l/identity-focused-review-20261009/test_review_counterexamples.py
```

exit **1**；JUnit **20 tests / 15 passed / 5 failures / 0 errors / 0 skipped**。15 个原新增合成节点通过；5 个独立期望行为反例失败，证明上述缺陷，不能把这一轮写成验证 PASS。较早四反例输出 `p4-02y-l-focused-review-20261009.xml` 为 19/15 passed/4 failures/0 errors/0 skips，由最终结果替代。没有模型下载/加载、真实 ORT session、真实 GPU 故障、私人图库、用户服务、全仓/性能/打包/提交/推送。

### 最小交接（发现时）

该段记录 finding 阶段的交接，后续修复状态见文末。下一步仅执行[五处定向修复卡](../tasks/P4-02Y-L-IDENTITY-A-fix.md)，修复后复核原发现、新 diff 与直接回归，无需另安排一整轮 DS-H 或重复 Sol。真实 weights/session/GPU 保留 PENDING，不自动启动模型切换任务。

## 2026-10-09 定向修复实施状态

五条 finding 已在当前实现会话修复，另覆盖旧 CPU `FAILED` job 的 auto handoff；实现与测试记录见[实现报告定向修复段](P4-02Y-L-IDENTITY-A.md#2026-10-09-定向修复记录)。修复轮 JUnit `test-results/p4-02y-l-identity-fix-final4.xml`：53/53 passed、0 failed/errors/skips；Ruff 和 `git diff --check` 通过。此记录不是独立复核结论：原五条 finding、新增状态转移、调用链和本轮直接回归仍待独立聚焦复核。真实 weights/session/GPU 保留 PENDING。

## 2026-10-09：五处修复后的独立聚焦复核

按用户“改好了，聚焦审核下”执行。HEAD 仍为 `14567c2`、index 空；读取当前修复卡、实现报告、`ocr/service.py` 全部修复路径、`ocr/engine.py` diff、新增 `jobs/queue.py` diff、直接 scheduler/ingestion 恢复调用及所有新增测试。未修改产品代码，不重开历史全受影响面、模型裁决、全仓或 GPU 实验。

### 原发现关闭

- F1：CPU CANCELLED/FAILED 旧行通过事务中 request marker 和 lease-free CAS 恢复，沿用唯一 job；实测关闭/重开 catalog 后只交接一次。后续显式选择会覆盖 marker，历史 auto failure 不再夺回选择。原恢复反例通过；新增通用状态表的问题另列 F6。
- F2：已知 RapidOCR ONNXRuntimeError 的结构化 cause 可被识别，包括实际安装包装器；未知异常、无 cause、普通 RuntimeError、ORT InvalidArgument 仍拒绝兜底。原包装异常反例通过。
- F3：GPU session 初始化 provider mismatch 转为专用初始化失败；auto 排 CPU，显式 GPU 仍严格。scheduler result identity 校验未修改，信封设备不符仍拒绝。原初始化反例通过；不可验证 provider 的新分支另列 F7。
- F4：CPU/GPU probe 分别记录；CPU 能力故障后继续 GPU probe，资格由各自实际 provider 健康决定；共同权重/安装错误直接拒绝。原 CPU probe 反例通过。
- F5：移除 remaining 递减，仅计实际执行次数；两现成任务或 GPU 故障＋CPU 兜底均可在 budget=2 执行；budget=1 后留 CPU QUEUED。原计数反例通过。

**F1–F5 在静态/合成边界内关闭，但本轮尚不能 PASS，存在以下两条新增实现缺陷。**

### F6 / P2 — OCR 恢复权限扩大到通用终态任务

位置：`src/photo_search/jobs/queue.py:53–56`，`:850–862`，以及新增 `requeue_terminal`。修复将全局 ALLOWED_TRANSITIONS 的 FAILED/CANCELLED 加入 QUEUED，`resume_blocked` 只检查此表，未额外要求 job.state=BLOCKED。因此非 OCR `geo_extract` 任务的 CANCELLED 或 FAILED 行都可经通用 resume 入口静默改为 QUEUED。两个独立反例实际都未抛应有的 INVALID_STATE_TRANSITION。这偏离 ADR-0002 §6.1 终态语义及 ADR-0018 不新增全局调度协议的范围。

当前 scanner/scheduler 调用方预先筛选 BLOCKED，未证明它们已有终态误恢复；发现是新增公共队列行为的可执行回归，不能夸大为现有数据已受损。最小修复：恢复普通全局终态矩阵；OCR 特殊恢复单独受限，核实 OCR task/profile、handoff marker、lease-free/CAS，不给其他任务通用终态复活权限。加入普通 resume 的正/反例与 OCR handoff 正例即可，无需重新设计协议或另做 Sol。

### F7 / P2 — 不可验证 GPU session provider 被误授权 CPU 兜底

位置：`src/photo_search/ocr/service.py:271–285`，`:396–411`。probe 吞入 `OCR_SESSION_PROVIDER_UNVERIFIED`，记 GPU unhealthy；资格解析统一把 GPU job 标 RUNTIME_CAPABILITY_UNAVAILABLE。协调层看到 snapshot.failure_code=OCR_SESSION_PROVIDER_UNVERIFIED 时不覆盖原能力错误，却继续按能力错误批准 fallback。独立反例在 CPU probe 正常、GPU provider 检查失败时，实际创建并执行 CPU 行，GPU 分支被取消。

这不是已确认 CUDA 初始化设备故障；按 ADR-0018 §2 的“仅确认发生在 session 初始化边界的设备故障”及原审核卡/provider 错误拒绝要求，不应被模糊能力错误洗成可兜底。最小修复：保留具体 probe 错误到 handoff 决策，明确拒绝 provider-unverified/未知/输入/安装类错误，真实缺资格/能力和已确认 GPU init/execute 故障继续允许 auto CPU；显式 GPU/result envelope 规则不放宽。增加不可验证 provider 的反例及真实缺 GPU 资格正例。

### 独立验证

已核对实现轮 `test-results/p4-02y-l-identity-fix-final4.xml` 53 tests / 0 failures / 0 errors / 0 skips。然后执行 tracked 定向集与五个旧独立反例，再补修复新增面的三个反例节点：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = ((Get-Location).Path, (Join-Path (Get-Location) 'tests'), (Join-Path (Get-Location) 'tests/integration')) -join ';'
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-focused-rereview-20261009 --junitxml=test-results/p4-02y-l-focused-rereview-20261009.xml tests/integration/test_p4_02y_l_identity.py tests/unit/test_p2_scheduler.py tmp/p4-02y-l/identity-focused-review-20261009/test_review_counterexamples.py tmp/p4-02y-l/identity-focused-rereview-20261009/test_rereview_boundaries.py
uv run --no-sync ruff check src/photo_search/ocr/engine.py src/photo_search/ocr/service.py src/photo_search/jobs/queue.py tests/integration/test_p4_02y_l_identity.py
git diff --check
```

Pytest exit **1**；JUnit **61 tests / 58 passed / 3 failures / 0 errors / 0 skips**。53 个 tracked 节点与五个旧反例全部通过，三个新失败节点对应 F6 的 CANCELLED/FAILED 和 F7。Ruff、diff-check exit **0**。反例保存在 `tmp/p4-02y-l/identity-focused-rereview-20261009/test_rereview_boundaries.py`（ignored，后续迁入 tracked 测试）。没有加载模型、创建真实 ONNX session、访问真实图库/服务、提交或推送。

### 交接

只继续[原修复卡的 F6/F7 收口段](../tasks/P4-02Y-L-IDENTITY-A-fix.md)，原五条不重做；修复后仅复核这两条、新 diff 与直接回归。SOL_NOT_REQUIRED，无需另跑整轮 DS-H。真实 pinned weights/session/GPU 仍 PENDING，OCR 总 PASS 不变。

## 2026-10-09 F6/F7 修复状态

F6/F7 已由实现会话修复。全局 jobs 终态转移已恢复，`resume_blocked` 明确只接受 BLOCKED；OCR auto handoff 的终态恢复改为 service 内限定 asset task、CPU profile、auto marker、lease-free 和原状态的 CAS。GPU `OCR_SESSION_PROVIDER_UNVERIFIED` 错误被持久保留在 job，之后即使能力快照刷新也不会转成 CPU fallback 许可。

实现会话 JUnit `test-results/p4-02y-l-f67-fix-1.xml`：65/65 passed、0 failures/errors/skips；受影响 Ruff 与 diff-check 通过。直接测试覆盖 F6/F7 反例并复用既有 58 个通过节点。此处不构成独立复核结论；下一步仅由独立复核者审查 F6/F7 与新 diff。真实 weights/session/GPU 仍 PENDING。

## 2026-10-09：F6/F7 独立聚焦复核闭环

结论：**ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED**。用户授权当前审核会话仅复核 F6/F7、修复新增 diff 与直接回归。本轮未修改产品代码；HEAD 仍 `14567c2`，index 空，并行 worktree 改动保留。

- **F6 关闭**：全局 FAILED/CANCELLED 终态矩阵已还原，通用 `resume_blocked` 显式限定 BLOCKED，非 OCR 两类终态都返回 INVALID_STATE_TRANSITION；普通 BLOCKED 恢复正例仍通过。新增 `_requeue_auto_fallback_cpu` 限于 asset/image_ocr/CPU profile、auto marker、原 CANCELLED/FAILED 状态和 lease-free CAS，由同一短事务中的 GPU handoff 调用。没有给其他任务普通终态恢复权限。旧 CPU handoff、重启幂等、live lease 与手动选择直接测试通过。
- **F7 关闭**：协调器遇到 OCR_SESSION_PROVIDER_UNVERIFIED 明确拒绝 fallback，且将具体错误持久写入 GPU job；再次读取不同能力快照时仍优先拒绝旧未验证错误。tracked 测试验证初次拒绝、具体码存储和快照刷新后继续拒绝；独立旧反例通过。真正缺资格/能力、确认的 GPU init/execute 故障继续 auto CPU，显式 GPU 仍严格。scheduler blocked recovery 仍须重新确认资格才恢复 GPU，result envelope 设备校验未放宽。
- 修复面为 `jobs/queue.py` 的 BLOCKED guard、`ocr/service.py` 的专用 CAS/具体错误持久拒绝和 tracked identity 新回归；读取对应实现与调用方。复用前轮 F1–F5 独立闭环及原共享面审查，没有再次做全 diff 或 Sol 裁决。未发现新增阻塞缺陷。

核对实现轮 `test-results/p4-02y-l-f67-fix-1.xml`：65 tests / 0 failures / 0 errors / 0 skips。独立实际执行当前 tracked identity/scheduler 与上一轮三个 F6/F7 反例：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = ((Get-Location).Path, (Join-Path (Get-Location) 'tests'), (Join-Path (Get-Location) 'tests/integration')) -join ';'
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-f67-focused-review-20261009 --junitxml=test-results/p4-02y-l-f67-focused-review-20261009.xml tests/integration/test_p4_02y_l_identity.py tests/unit/test_p2_scheduler.py tmp/p4-02y-l/identity-focused-rereview-20261009/test_rereview_boundaries.py
uv run --no-sync ruff check src/photo_search/jobs/queue.py src/photo_search/ocr/service.py tests/integration/test_p4_02y_l_identity.py
git diff --check
```

三个命令 exit **0**。独立 JUnit **60 tests / 60 passed / 0 failures / 0 errors / 0 skips**；包含 57 个 tracked 节点和三个旧独立反例。没有重复跑额外五个旧 F1–F5 反例（实现轮 65 节点证据已核对，本轮 tracked 直接回归也通过）。

本实现工程审核闭环，无需另跑整轮 DS-H 或重复 Sol。真实 pinned weights、ONNX sessions/provider、GPU 故障与现场效果仍 PENDING，工程结论不能代替 OCR 总/正式发行验收。未加载/下载模型、访问真实图库/用户服务、运行全仓/性能/打包、提交或推送。不自动启动模型切换或其他任务；后续真实资源验证由用户另行决定。
