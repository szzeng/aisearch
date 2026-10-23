# P4-WINDOWS-RELEASE-ONE-CLICK 执行报告

**当前状态：LOCAL PACKAGE PASS / GPU NOT TESTED**（2026-10-23）。修复提交 `64a9353` 上发行入口完成 Windows x64 冻结构建、原地 API smoke、ZIP64/CRC/SHA 和最终产物发布。GPU、第二台干净 Windows 与分享资格仍未验证；本地 ZIP 是个人候选包，未推送或公开发布。

基线复核：开始时本地 HEAD `542d8aa0c21ccd3cc98a8e8f3c1b56747990e012` 超前于 origin/master `f73becda4bafea73947d78549dfca65c28eaa938`；收尾时 origin/master 已前进到 `542d8aa0c21ccd3cc98a8e8f3c1b56747990e012`，与 HEAD 一致。工作树仍有其它未提交改动。

## 2026-10-23 发行文件发布失败修复与复验

用户提供的 PowerShell 错误为：`Write-Error: 找不到与参数名称 'NoClobber' 匹配的参数。` 上一轮日志证明冻结构建、API smoke 和 ZIP 创建/完整性校验已经成功，失败发生在最后发布文件时。`Move-Item` 不支持该参数；将三个文件的移交改为同卷 `[IO.File]::Move(source, destination)`，在目标已存在时抛错且不会覆盖。

复验提交 `64a935314da817650f0152b6b0cdc957133e733f`：再次运行 `tools/packaging/release_windows.ps1` 完整成功。PyInstaller exit 0（177.2 秒）；冻结 API smoke `status=ok`、`problems=[]`；ZIP 16,041 项、5,966,987,843 bytes，CRC/成员验证成功。SHA-256 `B69E9F42265F307AD16B274CFEFC9359E9E7B5BEADA7FB566523F19F9C741E35`，PowerShell `Get-FileHash` 与 `.sha256` 文件一致。定向 JUnit 11/11；发行和目录选择脚本 PowerShell AST、`git diff --check` 通过。

产物状态 `LOCAL_PACKAGE_PASS_GPU_NOT_TESTED`，报告保留 `suitable_for_sharing=false`。未做 GPU smoke、第二台干净 Windows 验证、公开上传或 license 审查。首轮失败 staging 原样保留，成功复跑 staging 由脚本清理。

## 实施

- 新增 `tools/packaging/release_windows.ps1`。从脚本位置定位仓库，要求 Windows x64、Python 3.12.10、uv、锁文件一致、`pyproject.toml`/`APP_VERSION`/`uv.lock` 版本相同，要求干净 Git 工作树，拒绝覆盖同版本产物，并在构建前估算磁盘空间。
- 每次构建使用唯一 staging `distpath`/`workpath` 调用现有 `build_onedir.py`，再检查必要 manifest、迁移、前端资源、文档和入口脚本。
- `packaged_api_smoke.py` 新增 `--in-place`，避免复制完整 onedir；合成图库、数据根及日志均在独立临时目录。
- `release_windows.py` 流式创建 ZIP64，拒绝 symlink、绝对/穿越/重复路径和图库/数据库/模型/私密配置，执行 `testzip()` 并计算 SHA-256；PowerShell 将 ZIP、`.sha256` 和 JSON 报告写到 `releases/`。
- 新增个人候选包说明；不自动提交、建 tag、上传或公开发布。默认报告 `GPU_NOT_TESTED`，不声称跨机认证。

## 验收结果

- R1：**PASS（本机个人候选包）**。修复提交 `64a9353` 上正式入口完成新构建、冻结 smoke 和 ZIP 产出，SHA 与 Windows `Get-FileHash` 一致；详见 2026-10-23 发行脚本续验记录。
- R2：**PASS（轻量失败保护）**。最终定向 JUnit `test-results/P4-WINDOWS-RELEASE-ONE-CLICK/focused.xml` 为 8/8；覆盖版本不一致、缺少 EXE/manifest、同名覆盖、空间不足、非零构建退出、ZIP 路径穿越/绝对路径/重复/私密权重和 CRC 损坏、SHA/ZIP64 成功路径及 GPU 状态筛选。实际从仓库外调用脚本时发现工作目录假设；修正为固定切到仓库根目录后，重跑 `uv lock --check --offline` 与版本检查成功，随后按设计因脏工作树退出 1。没有创建 `releases/` 或启动 PyInstaller。PowerShell AST 解析、Python `py_compile`、Ruff 与 `git diff --check` 通过。
- R3：**PENDING / GPU_NOT_TESTED**。本轮未提供模型目录，也未运行冻结 CUDA smoke。
- R4：版本来源与同名文件拒绝有 helper 测试覆盖；实际同版本拒绝依赖 release outputs 预检，PowerShell 端到端复用尚待 R1 条件满足。

## 独立复核

Luna High 对本卡完整新增面做了有界只读复核；一次关于正式产物分步移动失败可能留下半套文件的意见，已通过仅回滚本次成功创建文件的逻辑关闭。随后复查了可选 Qwen smoke 与 stage 路径保护，无 blocker。审查指出本机并发进程若恰在目录 containment 检查后、递归清理前替换 staging，仍有狭窄竞态；每次 staging 使用唯一 UUID 路径，本轮未测试并发恶意替换。复核未运行大构建或代替 R1/R3 证据。

## 最终轻量复验

- JUnit：8 passed / 0 failed / 0 errors / 0 skipped。
- Ruff、Python `py_compile`、PowerShell AST parser 和 `git diff --check`：通过。
- 实际 PowerShell 预检从仓库外运行，`uv lock --check --offline` 与版本检查 exit 0，然后按预期对脏工作树拒绝（exit 1）；没有创建 `releases/`，也没有启动构建。
- 最后一次空间快照：D: 可用 13,651,537,920 bytes（约 12.7 GiB）。
- 后续续查：D: 可用 32.84 GiB；实际再次调用发布入口，`uv lock --check --offline` 通过后在干净工作树检查处按设计退出 1。未创建 `releases/`、未启动 PyInstaller；并行工作树改动保留。

## 2026-10-23 续验与本地提交

- 已本地提交任务实现；未推送。并行未提交的 persons、random browse 和 embedding provider 改动均留在主工作树。
- 首轮任务构建来自干净已提交源码 7682b289：PyInstaller exit 0，bundle 校验通过；冻结 smoke 以 3 项过时断言失败（全局搜索表单被误要求为普通 href=/search，OCR ID 仍断言 legacy ocr-text@1）。对照当前提交源码后，将断言更新为实际表单路由和 ocr-text-cuda-fp32@4。
- 使用更新后的 packaged_api_smoke.py 对同一已构建 bundle 进行原地复跑：exit 0，status=ok、problems=[]。这是 smoke 修正验证，不声称该包是在修正后的任务提交上新构建，也不代替 ZIP/SHA 验收。首次失败 JSON 与 PyInstaller warnings 保存在 ignored test-results/P4-WINDOWS-RELEASE-ONE-CLICK/r1-initial-smoke-failure.json 和 r1-pyinstaller-warnings.txt。
- 正式入口从 clean checkout 通过 uv lock --check --offline、版本和工作树检查；空间预检报告 D: available 23,403,753,472 bytes / 21.80 GiB，estimated 27,498,741,824 bytes / 25.61 GiB，exit 1，未启动第二次构建。没有 releases/ 输出 ZIP。
- 失败 staging 精确路径为 D:\SZ\project\aisearch\.tmp\P4-WINDOWS-RELEASE-ONE-CLICK\clean-source-7682b28\releases\.release-0d445441447549db82833d218fd64ab0，共 10.93 GiB，其中冻结 bundle 10.71 GiB。这是本任务生成、可重建的发布暂存；为释放重建空间尝试递归删除时，桌面执行策略返回 blocked by policy，所以未删除，也没有用其他方式绕过。清理该精确 staging 后，应从干净、已提交 checkout 重跑唯一发布命令完成新构建、冻结 smoke、ZIP64/CRC/SHA 和 Get-FileHash 对照。
- Luna High 对 smoke 断言调整及本卡发布流程续审：PASS，无 blocker。确认页面标记、OCR processor ID 与新 smoke 断言相符；其复核不覆盖未执行的 R1 新构建/ZIP 或 R3 CUDA。

## 剩余验收

R3 需在用户明确指定只读模型目录后运行可选 Qwen CUDA smoke；R4 的 PowerShell 同版本产物拒绝路径仍待端到端检查。当前产物 `releases/PhotoSearch-v0.6.0-win-x64.zip` 已完成本机打包验收。公开再分发仍需单独 license 审核；报告的 GPU 状态保持 `GPU_NOT_TESTED`。
