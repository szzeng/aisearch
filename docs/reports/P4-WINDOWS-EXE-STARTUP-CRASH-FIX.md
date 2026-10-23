# P4-WINDOWS-EXE-STARTUP-CRASH-FIX 执行报告

日期：2026-10-23 · 状态：**IMPLEMENTED / NEW BUNDLE PENDING**

在 HEAD `82e2b18a334145b228088eac886a943567ca00c9` 的干净工作树上，复用现有冻结 EXE（SHA-256 `33E6D278D389331409E91EA387DAC4A2C79F5123CF1FC01A7893180C5E3D9936`）。`--help` 与 `embedding list --help` 分别约 5.9s / 5.4s 后以 `-1073741819 (0xC0000005)` 退出，无 stdout/stderr。Application Error 1000 与 WER 1001 指向包内 `_internal\MSVCP140.dll` 14.32.31326.0、偏移 `0x13278`。PyInstaller `COLLECT-00.toc` 证明该 DLL 和 `vcruntime140*.dll` 从 `D:\Program Files\VanDyke Software\Clients` 收集；包内 DLL SHA 与来源相同。该目录在 PATH 中早于 System32；System32 与 libheif 自带运行时为 14.51.36247.0。源码 CLI `--help` 和分段 Python 导入均成功。

修复让构建脚本只在 PyInstaller 子进程 PATH 前置 Windows System32，避免第三方目录的旧 VC runtime 被捡入包。Smoke 启动等待现检查子进程早退，显示有符号与十六进制退出码、是否观察到监听以及限长脱敏输出，并在超时/失败时清理本次进程。旧包复跑在约 10 秒内准确诊断相同崩溃：无监听，stdout/stderr 空；没有 180 秒空等。

验证：新增定向 JUnit 最终 **7/7**；Ruff、`py_compile`、`git diff --check` 通过。首次测试发现裸 `token=` 脱敏缺口；独立复核又指出无效 URL 分支也需脱敏，均已修复并保留首次失败 XML。有限范围独立打包代码复核 **PASS，无 blocker**；该复核不替代新包验证。

**未验证：**未对修复后的新冻结包构建/实测，故 CRT 归因对应的因果修复仍需新包验证；F4 与 ZIP/SHA 保持 PENDING。正式 `release_windows.ps1` 要求干净已提交工作树；用户随后授权本地简单提交，本轮仅做提交，不继续启动构建。未读取 dump、未访问图库/模型、未改系统设置。后续可在干净提交基线上重新检查空间后运行正式入口。

复现与详细验收见[任务卡](../tasks/P4-WINDOWS-EXE-STARTUP-CRASH-FIX.md)。
