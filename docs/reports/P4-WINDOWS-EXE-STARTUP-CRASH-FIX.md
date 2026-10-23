# P4-WINDOWS-EXE-STARTUP-CRASH-FIX 执行报告

日期：2026-10-23 · 状态：**LOCAL PACKAGE PASS / GPU NOT TESTED**

在 HEAD `82e2b18a334145b228088eac886a943567ca00c9` 的干净工作树上，复用现有冻结 EXE（SHA-256 `33E6D278D389331409E91EA387DAC4A2C79F5123CF1FC01A7893180C5E3D9936`）。`--help` 与 `embedding list --help` 分别约 5.9s / 5.4s 后以 `-1073741819 (0xC0000005)` 退出，无 stdout/stderr。Application Error 1000 与 WER 1001 指向包内 `_internal\MSVCP140.dll` 14.32.31326.0、偏移 `0x13278`。PyInstaller `COLLECT-00.toc` 证明该 DLL 和 `vcruntime140*.dll` 从 `D:\Program Files\VanDyke Software\Clients` 收集；包内 DLL SHA 与来源相同。该目录在 PATH 中早于 System32；System32 与 libheif 自带运行时为 14.51.36247.0。源码 CLI `--help` 和分段 Python 导入均成功。

修复让构建脚本只在 PyInstaller 子进程 PATH 前置 Windows System32，避免第三方目录的旧 VC runtime 被捡入包。Smoke 启动等待现检查子进程早退，显示有符号与十六进制退出码、是否观察到监听以及限长脱敏输出，并在超时/失败时清理本次进程。旧包复跑在约 10 秒内准确诊断相同崩溃：无监听，stdout/stderr 空；没有 180 秒空等。

验证：启动诊断定向 JUnit **7/7**；`tests/unit/test_release_windows.py` **9/9**（JUnit）。Ruff、`py_compile`、`git diff --check` 通过。首次测试发现裸 `token=` 脱敏缺口；独立复核指出无效 URL 分支也需脱敏，均已修复并保留首次失败 XML。有限范围独立复核对启动诊断/resolver 及归档过滤器窄修复均为 **PASS，无 blocker**；评审指出的嵌套目录权重拒绝覆盖已补入并复跑 9/9。

按授权本地提交 `e7becd6` 后，正式 `release_windows.ps1` 单次构建完成：`validate-bundle` 为 `valid: true`，真实 in-place API smoke 为 `status: ok`、`problems: []`。新包 `PhotoSearch.exe --help` 退出码 **0**，stdout 2295 字节、stderr 0；因果启动修复确认。原脚本在 ZIP 阶段将合法 `_internal/tokenizers/models/__init__.pyi` 误判为用户模型目录。归档器窄修后以定向测试验证：只拒绝 bundle 顶层用户 `models/`，扩展名、私密配置和 PhotoSearchData 检查继续生效。没有第二次构建；对已验证的同一 bundle 直接调用归档 helper，ZIP 全量 CRC/成员校验通过，共 **16039** 项、**5,966,983,954** 字节。

本地候选包：[PhotoSearch-v0.6.0-win-x64.zip](../../releases/PhotoSearch-v0.6.0-win-x64.zip)；SHA-256 `B31AB115D02698CE5493C36C3C8D05D9144CCCC02C74DA80EBECC399C7D5E366`，见 [.sha256](../../releases/PhotoSearch-v0.6.0-win-x64.zip.sha256) 和[机器报告](../../releases/PhotoSearch-v0.6.0-win-x64-report.json)。应用二进制基于 `e7becd6`；归档 helper 修复为 `a221f79`。构建耗时未记录，因为原入口在归档阶段报错退出。Qwen GPU 推理和第二台干净 Windows 机器未验证，故报告 `suitable_for_sharing=false`；不推送、不发布。未读取 dump、未访问图库/模型、未改系统设置；失败构建 stage 与诊断文件保留。

复现与详细验收见[任务卡](../tasks/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md)。
