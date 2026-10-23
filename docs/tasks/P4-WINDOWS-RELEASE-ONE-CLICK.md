# P4-WINDOWS-RELEASE-ONE-CLICK — Windows 一键便携 ZIP 发布工具

> 状态：IMPLEMENTED / VALIDATION PARTIAL。修复提交 `64a9353` 上 R1 完整重跑通过，生成个人候选 ZIP；R2 轻量失败保护通过。R3 GPU 未测试，R4 同版本复用拒绝路径未端到端验证。初次失败 staging 保留；未推送、打 tag 或上传 Release。
> 设计基线：任务启动时本地 HEAD `542d8aa0c21ccd3cc98a8e8f3c1b56747990e012`、origin/master `f73becda4bafea73947d78549dfca65c28eaa938`；收尾复核时 origin/master 已推进至并与 HEAD 对齐 `542d8aa0c21ccd3cc98a8e8f3c1b56747990e012`。版本 `0.6.0`。
> 范围：新增 `tools/packaging/release_windows.ps1` 编排入口、`release_windows.py` 小型归档/校验 helper、`packaged_api_smoke.py --in-place`、轻量单元测试和 `docs/windows-release.md`。可选 `-QwenModelDir` 才读取用户明确指定的本地模型运行一次 512D 冻结 CUDA smoke。默认不访问模型。不重做 P4-WINDOWS-NEW-PC-READY，不改变构建依赖/冻结运行时。

## 性能设计与验收（PERF-RELEASE）

- 链路与风险：一次完整冻结构建与 ZIP 压缩会读写约 11.5 GB 级 onedir，并需要并存构建暂存和约 6.3 GB 级 ZIP；同时运行两份冻结包会显著抬高磁盘峰值。
- 做法与边界：只构建一次，任务专属暂存，烟雾测试从新包目录原地启动、不复制 11 GB 包；脚本按观测包体估算可用空间，拒绝覆盖现有版本产物。容量数字为历史观测，不是固定承诺。
- 验证与停止：轻量测试校验版本、缺文件、私密文件/危险 ZIP 路径、覆盖保护和 CRC/SHA；仅在干净已提交工作树及估算空间充足时构建。提交 `64a9353` 的完整 R1 已通过；不据此声明 GPU 或跨电脑认证。

## 2026-10-23 发行文件发布失败修复与复验

- 根因：Windows PowerShell 的 `Move-Item` 不支持 `-NoClobber`；脚本已完成构建、冻结 smoke 和 ZIP 校验，但在最终文件发布时报“找不到与参数名称 'NoClobber' 匹配的参数”。现改用同卷 `[IO.File]::Move`，目标已存在时拒绝覆盖。
- 修复提交：`64a9353`。发行入口完整重跑成功：构建 exit 0（177.2 秒），冻结 API smoke `status=ok` / `problems=[]`，ZIP 校验成功。JUnit 11/11、两份 PowerShell AST、`git diff --check` 通过。
- 产物：`releases/PhotoSearch-v0.6.0-win-x64.zip`，16,041 项，5,966,987,843 bytes；SHA-256 `B69E9F42265F307AD16B274CFEFC9359E9E7B5BEADA7FB566523F19F9C741E35`，与 `Get-FileHash` 和 `.sha256` 文件一致。状态 `LOCAL_PACKAGE_PASS_GPU_NOT_TESTED`；GPU smoke、第二台干净 Windows 与分享资格仍未验证。本地个人候选包，未推送、打 tag 或上传 Release。
- 初次失败 staging `releases/.release-ff164bcb5fd4435290a5a22fb0b37c6a` 保留作证据；成功运行生成的新 staging 已由脚本清理。

## 验收与复核

- R1 发布路径：**PASS（本机个人候选包）**。修复提交 `64a9353` 上新构建 exit 0、冻结 API smoke exit 0、ZIP CRC/SHA 验证及最终文件发布通过；16,041 项、5,966,987,843 bytes；SHA-256 `B69E9F42265F307AD16B274CFEFC9359E9E7B5BEADA7FB566523F19F9C741E35`，与 PowerShell `Get-FileHash` 一致。首轮 smoke 断言及低空间失败历史见报告；旧失败 staging 保留。
- R2 失败保护：定向 JUnit **8/8 passed**，覆盖版本不符、EXE/manifest 缺失、输出同名、空间不足、构建非零退出、危险/重复/私密 ZIP 条目、CRC 损坏拒绝、SHA/ZIP64 成功路径和 GPU CUDA 状态校验。PowerShell 预检从仓库外调用，uv lock/version 通过后按预期因脏工作树退出，没有创建发布目录或启动构建。
- R3 Qwen CUDA：**PENDING / GPU_NOT_TESTED**。本次生成新冻结包，但未使用模型目录运行 CUDA smoke；不下载权重。
- R4 复用性：R2 验证版本读源和同名拒绝的 helper；PowerShell 端到端复用仍需 R1 后用轻量检查确认。
- 验证命令：`$env:UV_CACHE_DIR='.uv-cache'; .\.venv\Scripts\python.exe -m pytest -q tests/unit/test_release_windows.py --junitxml=test-results/P4-WINDOWS-RELEASE-ONE-CLICK/focused.xml --basetemp=.pytest-tmp/P4-WINDOWS-RELEASE-ONE-CLICK`，`ruff check tools/packaging/release_windows.py tests/unit/test_release_windows.py tools/diagnostics/packaged_api_smoke.py`，Python `py_compile`，PowerShell parser，`git diff --check`。所有上述静态/轻量验证通过。
- 独立复核：有界 Windows 发布编排/打包复核，覆盖新增脚本、helper、原地 smoke、失败保护和报告边界；如发现冻结运行时或数据语义修改，再扩大复核面。
- Luna High 有界只读复核：PASS，无 blocker；半套发布文件回滚发现已修复并跟进确认。smoke 断言修正续审：PASS，无 blocker；确认当前 HTML 搜索表单及 OCR processor ID 与新断言相符。该复核不替代 R1/R3。

## 未验证边界

- 完整构建空间门槛按当前预检约 25.61 GiB；实测 D: 可用 21.80 GiB。精确可清理目标为 D:\SZ\project\aisearch\.tmp\P4-WINDOWS-RELEASE-ONE-CLICK\clean-source-7682b28\releases\.release-0d445441447549db82833d218fd64ab0（约 10.93 GiB，包含本任务可重建失败 staging；首次 smoke 失败 JSON 与 PyInstaller warnings 已另存到 test-results/P4-WINDOWS-RELEASE-ONE-CLICK/）。删除命令被自动策略拒绝，目标仍保留；未触碰真实照片/catalog/模型或其它文件。
- 本机产物、GPU smoke、跨电脑兼容、公开再分发许可审核均未验证；当前没有 `releases/` 产物。
