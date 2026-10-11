# P4-OCR-RUNTIME-CONFIRM-A — 固定 OCR 权重与 ONNX Runtime 原生冒烟

日期：2026-10-11。状态：**READY**。执行模型建议：**GPT-5.6 Terra / medium（C1）**，用于 Windows/ONNX 本机环境核验；由用户在 Codex 手动切换。

## 1. 目标与入口

确认当前项目 Python 环境中，冻结身份的 RapidOCR/ONNX Runtime 与本机现有固定权重是否可真实初始化并处理一张合成图。当前证据仍为 `RUNTIME_UNCONFIRMED`：P4-TEST-BASELINE-CORE-A 使用替身 Engine，不能证明 native session。

按 `AGENTS.md → docs/development-status.md → 本卡` 开始，核对实际 Git/工作区。读取：

- 主架构 §8.2（OCR 模型与运行时边界）及 §11.3.4（模型适配与运行时边界）；
- V3.1 §2.2、§2.4、§3.2、§9、§11；
- `docs/tasks/P4-TEST-BASELINE-CORE-A.md`、其报告、`docs/tasks/P4-02Y-L-IDENTITY-A.md`、其实现/DS-H 报告与 ADR-0018；
- `src/photo_search/ocr/{contract,weights,engine,processor,cli}.py`、`src/photo_search/runtime/device.py`、相应 OCR 单元/集成测试。

## 2. 边界

- 使用项目规定 Python 3.12.10 / `uv` 环境。先检查实际安装的 `rapidocr-onnxruntime`、`onnxruntime-gpu` 版本和已有 `PhotoSearchData` OCR 权重 manifest/文件校验结果；路径以当前权重解析实现为准，不假定安装或权重位置。
- 只用 `%TEMP%`/pytest 隔离目录生成的单张合成含字图片。通过产品真实 `OcrWeights` 与 `OcrEngine`/引擎缓存路径做最小初始化和一次识别；记录 CPU/CUDA provider 的实际 session 身份与明确拒绝/异常码。无 GPU 时只标 GPU `UNCONFIRMED`，不得伪造 CUDA 成功。
- 不扫描或读取真实照片库，不连接服务，不下载权重或大型文件，不安装 DLL/包，不设置强制 CPU/GPU 环境覆盖，不改 `uv.lock`、设备策略、OCR 生产代码、ADR、数据库或 migration。
- 若固定 wheel/权重缺失或校验失败，记录精确缺口并停止该分支；若 native smoke 证实产品实现缺陷，保留最小复现，另立修复卡，不在本环境验证卡中改生产代码。

## 3. 验收与证据

1. 独立子进程输出安装版本、可用 provider、固定 model/revision、manifest 身份及权重 hash/大小核验、session 实际 provider、识别结果摘要、完整 stderr 与退出码；输出不得包含本机私有路径或图片内容。
2. 对本机实际可用的设备路径分别运行。缺设备/缺权重/初始化失败必须保留为结构化结果，不以 pytest skip、mock 或自动换 provider 宣称通过。
3. 相关 native smoke 生成 `test-results/P4-OCR-RUNTIME-CONFIRM-A.xml`；按需仅跑该 probe 和直接 OCR contract/device 断言，不跑全仓、真实图库、整库建库或 GPU 压力测试。
4. Python 测试/探针文件 Ruff、`py_compile`、`git diff --check` 通过。报告 `docs/reports/P4-OCR-RUNTIME-CONFIRM-A.md` 记录命令、版本、原生 provider/权重证据、实际结果、设备边界及未验证项；同步更新 `docs/development-status.md`。
5. 报告结论按设备分别写 PASS / FAIL / `RUNTIME_UNCONFIRMED`；CPU 通过不推出 GPU 通过，也不推出 OCR 产品整体或 30 万个人试用性能通过。

## 4. 审查和停止点

本卡限环境核验与合成输入，不改变产品代码或冻结协议；先由 C1 收集完整 native 证据。若修改生产行为、runtime/device 契约、错误边界或持久状态，立即停止并另立实现卡，按 V3.1 §2.4 做完整受影响面 DS-H 审查；DS-MAX 仅按 §2.2 六项条件。Sol 不预设必选；只有新增高风险冻结或 DS-H 留下 ADR/数据安全矛盾时才追加聚焦终审。不得因本卡未做而扩大到全仓回归。
