# P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP 执行报告

日期：2026-10-24。执行范围：首次模型准备入口、只读状态、OCR 离线复制、Face 固定 ZIP 安全导入、Qwen 既有下载器集成、便携 onedir 与首用页。

## 实施

- 增加 `Setup-AI-Models.cmd`，启动时可选全部或单组件（可用于单项重试），与 Start/Download/Configure 共用已保存的数据根；selector 以 `%APPDATA%\PhotoSearch\portable-operation.lock` 对这些入口做进程互斥。缺少数据根时沿用原交互选择流程。
- 新增 `PhotoSearch.exe models status|prepare|smoke-ocr`。Status 只检查文件存在/大小、安装标记和 Qwen manifests，不计算模型哈希、不创建 ONNX session、不探测 GPU。Prepare 按组件隔离错误；Qwen 复用现有固定下载器；OCR 从 RapidOCR 1.4.4 包资源复制到模型数据根并通过 pinned contract 校验后原子发布；Face 支持本地 ZIP 或固定官方 URL、单独许可确认、容量门禁、TLS/固定重定向主机、ZIP 路径/重复/展开大小保护、按 Face pinned contract 校验后发布。
- 首用页展示 Qwen/OCR/Face 缺失、待核验、安装时权重校验通过但运行环境未验证等状态，提示解压目录中的准备脚本；无自动下载、自动哈希、GPU probe 或建库。
- onedir 构建仅包含校验过哈希的 OCR 四个轻量资源，不包含 Qwen/Face 权重。

## 验证

- 当前任务定向 JUnit：**105 passed、4 skipped、0 failed（109 cases）**，覆盖首次准备/Face ZIP、Qwen downloader、OCR/Face contracts、OCR CLI、embedding status、便携 launcher 与四种 selector 锁冲突模式。最新报告：`test-results/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/junit-final-recovery-20261024-115544-581.xml`。这不是全仓回归。
- 下载链接使用 HTTP HEAD 检查，未读取任何响应实体：Qwen 固定 revision API 200（`huggingface.co`）；Face 固定 release URL 200（重定向主机 `release-assets.githubusercontent.com`，Content-Length 288,621,354）；实际读取响应体 0 bytes。材料：`test-results/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/link-check.json`。
- 修复 Face ZIP pinned 尺寸和本地归档复制长度边界后重新构建最终 onedir，bundle validator `valid=true`。冻结 CLI 在隔离中文/空格数据根报告初始三项缺失，准备 OCR 后报告 `weights_validated_at_install`；冻结 OCR CPU 合成图识别 `PHOTO SEARCH 2026`、1 个区域。最终 in-place packaged API smoke `status=ok`、`problems=[]`、启动 0.95 秒。最终 bundle 与证据：`.tmp/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/final-toctou-20261024/`。
- Codex 内置浏览器在隔离空根观察图库管理首用说明、Qwen/OCR/Face 状态、可见文案和窄面板布局。未运行全站浏览器回归。没有扫描/修改真实照片库或真实模型目录。
- Ruff、PowerShell parser、Python 编译和 `git diff --check` 均通过。最终构建日志与临时包在 `.tmp/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/`。

## 复现命令

```powershell
$python = "$PWD\.venv\Scripts\python.exe"
$runId = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$root = ".tmp/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/repro-$runId"
& $python -m pytest -q tests/unit/test_p4_first_run_models.py tests/unit/test_p4_qwen_download.py tests/unit/test_p3_ocr_contract.py tests/unit/test_p3_face_contract.py tests/integration/test_p3_ocr_cli.py tests/unit/test_embedding_management_status.py tests/unit/test_release_windows.py tests/integration/test_p4_first_run_selector_lock.py --junitxml="test-results/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/junit-$runId.xml" --basetemp=".pytest-tmp/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP-$runId"
& $python tools/packaging/build_onedir.py --distpath "$root/dist" --workpath "$root/work"
& $python tools/packaging/release_windows.py validate-bundle --bundle "$root/dist/PhotoSearch"
& $python tools/diagnostics/packaged_api_smoke.py --bundle "$root/dist/PhotoSearch" --work-dir "$root/api-smoke-work" --output "$root/evidence/packaged-api-smoke.json" --in-place
```

## 未执行与边界

- 没有下载 Qwen、Face ZIP 或任何模型权重。链接 200 只证明当前 HEAD 响应可达，不证明完整载荷可下载/可解压。
- 没有合法 Face 权重，因此真实 Face 推理为 `FACE_RUNTIME_PENDING`；未验证实际用户主机 GPU/干净电脑。
- 首轮最终冻结 smoke 曾因错误地从 `embedding.registry` 导入固定 Qwen provider ID 而失败；将固定 ID 移入首次准备模块后重新构建，最终 `models status` 与 API smoke 通过。失败及最终复验分别留在 `.tmp`/`test-results`。
- 用户于 2026-10-24 明确豁免本任务的 DS-H 复核门槛；指定的 Luna High 子代理复核已完成且未发现新的代码 blocker。真实 OS 强制终止发生在各发布阶段时的恢复仍未直接注入验证，故状态为 `IMPLEMENTED / VALIDATION PENDING（真实进程强停故障注入）`。

用户后续授权简单本地提交；不推送、不发布。


## Luna High 独立复核（2026-10-24）

- 首轮发现 Face pinned 文件可在 512 MiB 展开上限内超过预期尺寸，导致预检低估；现已在解压写入前核对 pinned 声明尺寸，并限制实际字节数和要求精确匹配。新增超大 pinned 文件拒绝测试。
- 后续发现本地 ZIP 在预检 `stat()` 后可能增长；现从打开的源句柄限量流复制，不能超过预检长度或归档上限，复制长度变化即失败并清理暂存。新增预检后增长模拟测试。
- 根级 pinned 文件条目不再触发 `IndexError`；缺少正确目录结构时通过安全校验拒绝。
- 锁竞争覆盖 Configure/Start/Download/Setup 四种 selector mode，并核对输出以排除 EXE 缺失造成的假通过。Face 重定向拒绝、发布校验失败清理、遗留暂存重试和既有目录不覆盖均有定向覆盖。
- Luna High 对最新改动面复核未发现新的代码 blocker。真实 OS 强制终止发生在每个发布阶段时的恢复未直接注入验证；当前测试覆盖异常清理和模拟残留 staging 后重试，不能称为进程中断全矩阵通过。
- 本次为用户指定的 Luna High 只读复核；用户已明确豁免 DS-H 门槛。真实进程强停故障注入仍待验证，因此不标 PASS。
