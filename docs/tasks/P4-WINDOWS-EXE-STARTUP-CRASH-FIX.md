# P4-WINDOWS-EXE-STARTUP-CRASH-FIX — 冻结 EXE 启动崩溃

> 日期：2026-10-23。状态：**IMPLEMENTED / NEW BUNDLE PENDING**。用户已授权本地简单提交；不推送、不发布。执行规格来自用户提供的 `D:/download/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md`；本卡记录仓库内执行结果。

## 目标与边界

- 基线：HEAD `82e2b18a334145b228088eac886a943567ca00c9`，开工工作树干净；目标旧包 SHA-256 `33E6D278D389331409E91EA387DAC4A2C79F5123CF1FC01A7893180C5E3D9936`。
- 只处理 PyInstaller Windows DLL 解析顺序及打包 smoke 的启动失败诊断；不碰真实图库、数据库、模型权重、系统 PATH/驱动/ACL；不删旧构建。本轮仅本地提交，不推送。
- 不改变 CUDA 必需策略、Worker、模型身份、搜索/数据语义、安全认证或 smoke 的 HTTP/CSRF 验收。

## 性能设计与验收

- 链路与风险：冻结包启动及一次 onedir 构建；错误 CRT 会让 EXE 在 Python 输出前崩溃，完整构建/压缩有高磁盘峰值。
- 做法与边界：仅复用现有 onedir 做限时启动诊断；只在 PyInstaller 子进程 PATH 前置 Windows System32；不复制包、不触发新构建。
- 验证与停止：测量现有 EXE `--help` 时间并运行小型进程假体；新冻结 EXE 与 in-place HTTP smoke 要在干净提交基线上单次构建。本轮按最新授权仅本地提交；构建作为后续独立步骤，仍须核对干净工作树和空间。

## 诊断与实现

- 两次冻结探针（`--help`、`embedding list --help`）分别以 `-1073741819 / 0xC0000005` 退出；耗时约 5.9s / 5.4s，stdout/stderr 空。
- Application Error 1000 与 WER 1001：faulting module 为包内 `_internal\MSVCP140.dll` 14.32.31326.0，异常偏移 `0x13278`。COLLECT TOC 表明该文件及配套 `vcruntime140*.dll` 来自 `D:\Program Files\VanDyke Software\Clients`；其 SHA 与包内文件相同。System32 与 libheif runtime 的 MSVC DLL 为 14.51.36247.0。该第三方目录在构建 PATH 中先于 System32。
- 源码 CLI `--help` exit 0；分段导入 uvicorn、应用 API/CLI、ONNX Runtime、torch、torchvision、transformers、cv2、pyarrow 均 exit 0。
- `build_onedir.py` 现仅为 PyInstaller 子进程将 `%WINDIR%\System32` 放到继承 PATH 前，避免从更早的第三方目录选中旧 CRT。`packaged_api_smoke.py` 现在轮询 `Popen.poll()`，报告有符号与十六进制退出码、监听观察、限长并脱敏的 stdout/stderr；启动超时会进入 finally 清理本次 launcher。

## 验证与验收状态

- 定向 `tests/unit/test_packaged_startup_diagnostics.py`：最终 **7/7 passed**；覆盖 PATH 解析、早退、无 URL、成功 URL、超时和子进程清理、token 脱敏/输出上限及无效 bootstrap 分支脱敏。首轮 1 项脱敏测试失败，修复后重跑通过；各轮 JUnit 保存在 `test-results/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/`。
- Ruff、`py_compile`、`git diff --check` 通过。更新后的 smoke 对旧包快速报告实际 `-1073741819 (0xC0000005)`，未输出凭据。
- F1 崩溃定性：**PASS**（事件日志与构建来源支持错误 CRT 收集路径）。F2 最小修复：**IMPLEMENTED**，新包因未构建仍待因果复验。F3 失败可诊断：**PASS**。F4 新包 `--help` 与真实 in-place API smoke：**PENDING**；无新 ZIP/SHA。
- 有界独立打包代码复核：**PASS，无 blocker**；确认 resolver 路径证据与诊断边界，未运行新构建，不替代新包验收。完整冻结构建未运行；后续正式入口要求干净已提交工作树，当前仅获本地提交授权，未获推送/发布授权。

## 交付

- 执行报告：[P4-WINDOWS-EXE-STARTUP-CRASH-FIX](../reports/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md)
- 原始探针材料：忽略目录 `.tmp/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/` 与 `test-results/P4-WINDOWS-EXE-STARTUP-CRASH-FIX/`。
