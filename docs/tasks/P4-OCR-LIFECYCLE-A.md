# P4-OCR-LIFECYCLE-A — OCR 退役任务自动收尾与来源进度治本修复

日期：2026-10-11。状态：**SCOPED PASS / 定向验收与 LUNA H 复核通过**。
立卡基线：`szzeng/aisearch` master `f05464c9`。实施时核对了实际 HEAD/工作区并保留并行改动；完整结果见[执行报告](../reports/P4-OCR-LIFECYCLE-A.md)。
实施及定向验收在单会话完成；按用户指定直接由 **GPT-6 Luna / High（LUNA H）**独立复核，不做 DS-H。模型由用户手动切换。

## 1. 目标与实测证据

一次解决：OCR 从 `ocr-text@1` 升至 `ocr-text-cpu-fp32@2` / `ocr-text-cuda-fp32@2` 后，旧任务不再被领取，但仍使来源 OCR 长期显示「处理中」；以后升级 `@3` 等版本时不重复造清理脚本。

用户真实库只读检查（**诊断快照，非当前提交的验收结果**）：
- 旧 `@1`：`QUEUED=909`、`RUNNING=1`（租约检查为过期），合计 **910**；其中 **906** 已有与当前资产 SHA 对应的新版 OCR 当前结果。
- 另外 **4** 张（asset 2739、2748、2749、3606）无新版当前结果，但新版 CUDA 任务均已 `FAILED / DECODE_FAILED`；不应自动重新 OCR。
- OCR 模型权重 SHA/大小校验通过；当前有效 CUDA OCR 文档 3,182 条。
- 另有 `DECODE_FAILED=780`、`FORMAT_UNSUPPORTED=72` 的历史失败任务；本卡只负责正确统计/显示，不解决解码格式。

**成功标准**：退役队列不再制造假进度；旧任务自动安全收尾；真正有效任务与失败如实显示；历史 OCR/搜索结果不变。

## 2. 仓库直接依据与设计边界

先读 `AGENTS.md`、`docs/development-status.md`、本卡；核对 ADR-0018、`P4-02Y-L-IDENTITY-A` 以及主架构 OCR 模型边界 §8.2、§11.3.4，执行策略相关 §2.2/§2.4/§9.4。

- `src/photo_search/ocr/contract.py` 已定义 `OCR_LEGACY_PROCESSOR_ID` / `OCR_PROCESSOR_IDS`；`ocr/service.py::build_scheduler/run_queue` 只注册当前 CPU/GPU，旧 `@1` 不再被领取。
- `ocr/service.py::_LIVE_PROCESSOR_IDS` 仍含 legacy，用于保护有效执行租约；`_cancel_competing_pending` 只考虑当前 CPU/GPU 分支。
- `refresh/progress.py::ENRICHMENT_STAGES` 仍计入 `ocr-text@1`；`scheduler/projection.py::ocr_summary` 及 `ocr/service.py::pending_job_count` 按全体 OCR jobs 计算 pending，造成假 `IN_PROGRESS/LAGGING`。
- `refresh/progress.py::enrichment_counts` 把 `SUCCEEDED/FAILED/CANCELLED/BLOCKED` 都计入 `handled`；来源 UI `web/app.js` 仅显示阶段文字。
- OCR 当前结果在 `asset_ocr_text`，历史 artifact/regions/FTS 必须保留；GPU 优先、仅特定设备错误 CPU 兜底与五字段 job 身份仍按 ADR-0018 保持。

**范围**：OCR 本地最小生命周期声明与复用；不建立通用调度插件/新后台线程/GC，不修改人物、地点、向量任务，不增加 schema/依赖；不得扫描/改动真实 `PhotoSearchData`。

## 3. 实施要求

### A. 有效与退役版本统一来源

在 OCR 现有 contract/service 上集中声明：`active_processor_ids`（当前可执行 CPU/GPU）与明确的 `retired_processor_ids`（当前为 `ocr-text@1`）；历史 artifact reader 不因此退役。可直接复用/扩充现有常量，不为此新建框架。

调度、入队竞争分支、旧任务协调、来源进度、OCR summary 均从同一声明推导身份范围；不要在多个模块各自硬编码 `@1/@2`。未来升级 `@3` 时需显式登记退役版本并更新新处理器实现；未知或未声明 ID **不许**自动取消。

### B. 一套幂等、安全、自动的收尾

实现 OCR 局部 `reconcile_retired_ocr_jobs(...)`（名字可按代码习惯调整）：

1. 仅作用于明确退役的 `image_ocr` job；`QUEUED/RETRY_WAIT/BLOCKED` 根据现有状态矩阵合法取消；`RUNNING` 有效租约不能动，**明确过期**后才允许取消；租约空值/异常无法证明安全时保留、报告异常，不粗暴更新。
2. 使用 **SQLite 短事务＋原状态/lease 条件 CAS**；防止刚续约或被其他工作者领取的任务被取消。正常释放 token/worker/租约字段；保留可追溯退役原因，历史终态（SUCCEEDED/FAILED/CANCELLED）不改写。
3. 过期执行迟到提交仍必须被既有 `validate_submission` 的租约/processor 校验拒绝；不得放宽通用 `jobs.ALLOWED_TRANSITIONS`，不得改变 job 唯一键、OCR artifact 校验或当前结果发布协议。
4. 合理的**自动维护**入口：Catalog 就绪后有界执行；后续正常 OCR 入队/图库刷新可以补处理遗留批次。复用同一函数，限制每批行数、事务时间；不在页面只读查询里写库，不启动常驻线程。不中断启动；重复调用无副作用。
5. 可选提供简单 OCR CLI `reconcile --dry-run/--apply --json`；若接入负担过高，至少把自动处理数量/未处理租约/异常数放入已有只读 OCR status。**不必新增 UI 页面。**
6. 已有当前结果的 906 张不得重识别；无结果但新版已 `DECODE_FAILED` 的 4 张不得因为旧任务退役被重新排队。未来缺失且尚未尝试的资产继续通过**正常新版本入队流程**处理，不在修复步骤自动全库 force。

### C. 进度口径治本（不只是隐藏旧任务）

- `pending` / `LAGGING`：只统计**当前处理器＋当前资产输入版本**真正可执行/等待的工作；退役旧任务另计诊断数。
- 按当前范围中的 **distinct asset** 统计成功、排队、失败、阻塞和未尝试；当前 OCR 文档即使由可读历史处理器产生，仍按有效当前版本结果识别，不因 job 被退役就丢失；同资产 CPU/GPU 不双算。
- 优先级按资产最终事实判定：有当前 OCR 成功结果（包括识别结果为空文本）算已识别；有活动新版分支在执行/等待算待执行；GPU 失败但 CPU 兜底在排队时不算最终失败；仅剩新版终态失败才算失败；`BLOCKED` 与未尝试要明确区分，`CANCELLED` 不等于成功。
- 保留现有 API/status 字段兼容性，必要时追加 `failed`、`blocked`、`retired_pending` 计数。历史 job 原始 `jobs` 分类仍可见，但不参与有效 pending 状态计算。
- 来源 UI `src/photo_search/web/app.js` 只做小改：例如「文字识别：已处理（81 张失败）」；只剩失败时不再显示「处理中」或误导点击「继续建库」。如还有未尝试的照片则显示「待处理」，真正等待中的新版任务保持「处理中」，受阻则如实提示。不要为了本卡重写全站阶段协议。

### D. 保护和停止条件

- 不删除/修改 `asset_ocr_text`、regions、FTS 投影、artifact、历史成功任务、原图、人物/地点人工数据。
- 保留 GPU-first/CPU-fallback 已验证的并发和权限规则；不允许旧 `RUNNING` 重新覆盖新版选择。
- 如需改变通用调度协议、schema 或 artifact/current OCR 身份，**停止扩范围并说明原因**；不要以绕过租约保护换取进度变绿。

## 4. 个人项目最小验收：四组

仅在合成 SQLite/catalog/资产上跑受影响的少量 `pytest -q`（生成 JUnit XML 至 `test-results/P4-OCR-LIFECYCLE-A.xml`），优先复用 `tests/integration/test_p4_02y_l_identity.py`、`tests/integration/test_p3_ocr_lifecycle.py` 和已有来源进度 UI 测试。

1. **安全收尾**：旧 `QUEUED` 取消；有效 lease `RUNNING` 不动；过期 lease 可安全取消、迟到提交拒绝；异常 lease 不误删。
2. **幂等与自动入口**：连续执行两次第二次零额外修改；模拟启动/刷新触发有界继续处理；未来模拟声明 `@2` 退役、`@3` 生效，无需另一套脚本。
3. **进度语义**：旧任务不使来源卡 `IN_PROGRESS`、OCR 总状态不误 `LAGGING`；同照片双设备去重；新版失败、兜底排队、BLOCKED、未尝试分别符合显示口径。
4. **数据保护**：历史成功文档（含空文本）、regions/FTS/current 不变；四个新版解码失败代表样例不重排；任务取消不改变 OCR 结果。

只需改动直接相关的 pytest/JUnit、受影响 Python 文件 Ruff、必要的 `node --check src/photo_search/web/app.js`、`git diff --check`。不跑全仓 2,000+ 回归、真实图库、真实 GPU、大规模 benchmark 或打包；不为绿灯弱化断言。租约/历史写安全的直接反例必须实际执行，不能仅 mock 通过。

## 5. 交付与状态

- 受影响范围优先：`ocr/{contract,service,cli}.py`、`refresh/progress.py`、`scheduler/projection.py`、必要启动入口、`web/app.js` 和少量直接测试；`jobs/queue.py` 仅必要时局部复用，不改全局状态矩阵。
- 简短报告 `docs/reports/P4-OCR-LIFECYCLE-A.md`：变更文件、自动收尾数量（合成/真实明确区分）、四组 JUnit、历史产物保护、未验证事项；同步 `docs/development-status.md`。
- 只在受影响定向测试真实通过后写 **SCOPED PASS**；若还没有实际运行则保持 READY/未验证。按 AGENTS/V3.1 对高风险租约/持久化语义执行必要的独立**受影响面**复核；用户要求的简单测试不等于跳过必需安全性反例，且无需全仓回归。
- 完成后提供用户自主验收步骤：OCR `status --json` 和来源 3/4 的实际进度；禁止实施会话擅自操作用户真实图库。

**交付记录（2026-10-11）：** 实施已完成，任务范围 JUnit `10/0/0/0`，Ruff、Python 编译、Node 语法及 `git diff --check` 通过；独立 LUNA H 复核 **PASS，finding=none**。定向报告及未验证边界见[执行报告](../reports/P4-OCR-LIFECYCLE-A.md)。真实图库、GPU/模型与浏览器运行仍待用户自主实测。

**核心验收：** 当前 910 条旧队列安全自动收尾，以后 OCR 版本升级时只维护版本声明和实际新实现，历史任务、进度与结果都能自然收敛。 
