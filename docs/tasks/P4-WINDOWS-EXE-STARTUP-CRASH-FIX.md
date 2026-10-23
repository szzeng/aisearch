# P4-WINDOWS-EXE-STARTUP-CRASH-FIX — 冻结 EXE 启动崩溃

> 日期：2026-10-23。状态：**LOCAL PACKAGE PASS / GPU NOT TESTED**。用户已授权本地简单提交；不推送、不发布。执行规格来自用户提供的 `D:/download/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md`；本卡记录仓库内执行结果。

## 目标与边界

- 基线：HEAD `82e2b18a334145b228088eac886a943567ca00c9`，开工工作树干净；目标旧包 SHA-256 `33E6D278D389331409E91EA387DAC4A2C79F5123CF1FC01A7893180C5E3D9936`。
- 只处理 PyInstaller Windows DLL 解析顺序及打包 smoke 的启动失败诊断；不碰真实图库、数据库、模型权重、系统 PATH/驱动/ACL；不删旧构建。本轮仅本地提交，不推送。
- 不改变 CUDA 必需策略、Worker、模型身份、搜索/数据语义、安全认证或 smoke 的 HTTP/CSRF 验收。

## 性能设计与验收

- 链路与风险：冻结包启动及一次 onedir 构建；错误 CRT 会让 EXE 在 Python 输出前崩溃，完整构建/压缩有高磁盘峰值。
- 做法与边界：只在 PyInstaller 子进程 PATH 前置 Windows System32；限时诊断后，在干净提交基线上完成一次新构建及 in-place smoke。归档误报修复后，仅复用 smoke 通过的同一 onedir 续跑归档，不重复构建。
- 验证与停止：测量现有 EXE `--help` 时间并运行小型进程假体；新包 `--help`、真实 in-place HTTP smoke、ZIP 完整性和 SHA 全部有实际结果。GPU inference 与第二台干净 Windows 机器留作未验证项。

## 诊断与实现

- 两次冻结探针（`--help`、`embedding list --help`）分别以 `-1073741819 / 0xC0000005` 退出；耗时约 5.9s / 5.4s，stdout/stderr 空。
- Application Error 1000 与 WER 1001：faulting module 为包内 `_internal\MSVCP140.dll` 14.32.31326.0，异常偏移 `0x13278`。COLLECT TOC 表明该文件及配套 `vcruntime140*.dll` 来自 `D:\Program Files\VanDyke Software\Clients`；其 SHA 与包内文件相同。System32 与 libheif runtime 的 MSVC DLL 为 14.51.36247.0。该第三方目录在构建 PATH 中先于 System32。
- 源码 CLI `--help` exit 0；分段导入 uvicorn、应用 API/CLI、ONNX Runtime、torch、torchvision、transformers、cv2、pyarrow 均 exit 0。
- `build_onedir.py` 现仅为 PyInstaller 子进程将 `%WINDIR%\System32` 放到继承 PATH 前，避免从更早的第三方目录选中旧 CRT。`packaged_api_smoke.py` 现在轮询 `Popen.poll()`，报告有符号与十六进制退出码、监听观察、限长并脱敏的 stdout/stderr；启动超时会进入 finally 清理本次 launcher。

## 验证与验收状态

- 定向 `tests/unit/test_packaged_startup_diagnostics.py` **7/7**；`tests/unit/test_release_windows.py` **9/9**；JUnit 在 `test-results/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/`。Ruff、`py_compile`、`git diff --check` 通过。旧包诊断准确报告 `-1073741819 (0xC0000005)`。
- 单次正式构建（应用源提交 `e7becd6`）成功；`validate-bundle` 为 `valid: true`，in-place API smoke `status: ok`、`problems: []`。新包 `--help` exit 0，stdout 2295 字节、stderr 0。原脚本归档时将合法 `_internal/tokenizers/models/__init__.pyi` 误报；修复后仅 bundle 顶层用户 `models/` 目录被拒绝，权重扩展名/私密配置/PhotoSearchData 检查仍生效。对同一 bundle 续跑 archive helper，全量 CRC/成员验证通过：16039 项、5966983954 字节、SHA-256 `B31AB115D02698CE5493C36C3C8D05D9144CCCC02C74DA80EBECC399C7D5E366`。没有第二次构建。
- F1 崩溃定性：**PASS**（事件日志与收集路径证据支持旧 CRT 根因）。F2 最小修复：**PASS**（新 EXE 启动成功）。F3 失败可诊断：**PASS**。F4 新包 CLI 与真实 in-place API smoke：**PASS**。ZIP/SHA：**PASS**；整体 **LOCAL PACKAGE PASS / GPU NOT TESTED**。
- 有界独立复核对启动诊断/resolver 与归档过滤器窄修复均 **PASS，无 blocker**。评审建议独立覆盖嵌套 `models/*.safetensors`，已补断言并复跑 `tests/unit/test_release_windows.py` **9/9**。Qwen GPU 推理与第二台干净 Windows 机器未验证，机器报告 `suitable_for_sharing=false`。候选 ZIP、SHA、JSON 报告见执行报告；保留原构建 staging 与诊断材料。不推送、不发布。

## 交付

- 执行报告：[P4-WINDOWS-EXE-STARTUP-CRASH-FIX](../reports/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md)
- 原始探针材料：忽略目录 `.tmp/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/` 与 `test-results/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/`。
