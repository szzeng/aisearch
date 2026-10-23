# P4-WINDOWS-PORTABLE-RELEASE-A — Windows 便携包与 Qwen 冻结运行验证

> **状态**：IMPLEMENTED / VALIDATION PENDING / 已按用户要求本地提交，未推送、未发布。V1 PASS、V2 LOCAL_ISOLATION_PASS、V3 PARTIAL、V4 PASS、V5 PARTIAL、V6 CLEAN_HOST_PENDING。全量回归 3,544 项中 21 项范围外失败、52 跳过；受影响 Provider/Worker/Manifest 68/68 通过（含新增冻结资源路径和导入顺序测试）。第一阶段交付个人试用便携 ZIP，不是 Setup/MSI、不是正式 Windows Release。
>
> **设计基线**：`szzeng/aisearch` `master @ 3d717bb（执行时实测 origin/master 一致）`（2026-10-23）；项目版本 0.6.0、Catalog Schema 43。执行时先读取最新 `master`、`git status` 和 `docs/development-status.md`；如已有同名任务卡/并行变更，先复核差异，禁止覆盖。
>
> **主执行建议**：C1 / GPT-5.6 Terra（Medium），对应 Windows、PyInstaller、CUDA/DLL 和冻结包诊断；用户手动选择模型。一次任务尽量在同一会话完成实现→测试→修复→复测。有界独立复核检查完整变更面；只有出现跨事务/数据身份/迁移/冻结 ADR 变更时，按 `AGENTS.md` 的高风险路由升级，不机械追加 Sol/DS-H 多轮审核。
>
> **本卡一句话目标**：把当前**真正使用的 Qwen 搜索**从 Windows Python 源码环境完整带进 **PyInstaller onedir**，让用户在兼容的 Windows x64 电脑**解压、双击、打开浏览器并使用既有模型与图库**，而非只看到一个能启动但无法搜图的 EXE。

## 性能设计与验收（PERF-BOUNDED）

- **链路与风险**：冻结 EXE 启动、Worker 首次加载与搜索；PyTorch/ONNX DLL 可能冲突，模型冷载入会增加内存/显存与启动耗时。
- **做法与边界**：沿用单活动 Provider、受控 Worker 和包外权重；本卡只记录真实 onedir/ZIP 体积、启动/首搜与短热搜及资源使用，不做 30 万容量或正式 P95 声明。
- **验证与停止**：按 V1–V6 矩阵和受影响冻结运行回归；无授权 Qwen 权重时保留 `QWEN_GPU_VALIDATION_BLOCKED`，V6 无独立主机则 `CLEAN_HOST_PENDING`；不越界下载权重/检查真实图库。

## 0. 本轮执行记录（2026-10-23，续验）

- V4 已按用户对具体 Qwen 模型目录的只读授权完成。固定 revision `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda` 的 5 个文件、4,266,571,889 bytes 哈希通过；不触碰相邻照片/catalog，不改模型。冻结 EXE 的 spawn Worker 在 RTX 4060 Laptop / PyTorchCUDA / FP16 / gpu_required 下实跑 512D、1024D 文字与合成图片，输出有限、非零、L2=1.0，禁 CPU fallback，峰值 VRAM 约 4.26 GB。
- 512D 与 1024D 分别在 `.tmp/.../v4-512`、`v4-1024` 独立隔离根扫描 3 张合成照片，真实冻结 CLI 建立 3/3 READY GPU 向量索引与 Lance rows 3/3；profile gate 选择通过。512D 应用 API 文字/图片搜索成功，退出重启后文字搜仍命中；1024D 文字搜索及重启后搜索成功，各返回 3 个合成候选。完整 JSON/JUnit 证据见唯一报告和 `test-results/P4-WINDOWS-PORTABLE-RELEASE-A-continued/`。
- V3：包内 Provider 与缺权重、错误 hash 拒绝、CUDA 不可用时 GPU-required 拒绝、占用端口及重复启动均通过；原无权重浏览器查询 `VALIDATION_ERROR` 详情未修复，仍 PARTIAL。
- 全回归第一次因两个 random-browse 测试导入拼写错误无法收集；修正为 `pytest` 与 `TestClient` 后完整运行 3,544 项 / 21 failed / 0 errors / 52 skipped。21 项均为范围外 UI/历史 companion 契约断言；Provider/Worker/Manifest 定向集 68/68 PASS。JUnit：`test-results/P4-WINDOWS-PORTABLE-RELEASE-A-continued/full-regression.xml`、`affected-regression.xml`。
- 原 `packaged_api_smoke.py` 成功证据沿用同一冻结包 SHA；本轮重跑复制完整 onedir 时 D: 空间为 0，WinError 112 失败。V6 无第二台干净 Windows 主机。无提交/推送/发布。
- 当前矩阵：V1 PASS；V2 LOCAL_ISOLATION_PASS；V3 PARTIAL；V4 PASS；V5 PARTIAL；V6 CLEAN_HOST_PENDING。

## 1. 交付边界：先做便携试用，不做完整安装系统

最终交付：`dist/PhotoSearch/`、版本化的 `PhotoSearch-Windows-x64-portable-<version>-<shortsha>.zip`、ZIP 的 SHA-256、简明 `README-PORTABLE.txt`，和一次可核对的**冻结包实际运行证据**。ZIP 包括软件、Python 解释器和必要第三方运行依赖；**不包括**用户原图、真实数据库、AI 模型权重、真实个人路径。

用户在新电脑的预期步骤：

1. 解压 ZIP 到任意普通可写/只读安装目录（安装目录自身不应成为数据目录）；
2. 将**当前程序支持的已准备、校验通过的模型文件**放入独立数据根，或复用完整合法的旧数据根；本卡不实现自动下载；
3. 双击明确的启动入口，自动打开有效的本地会话，无需 Python、uv、Git、PowerShell 或手工复制一次性 token；
4. 使用现有图库/搜索界面，正常退出后再次启动；移动整个程序目录仍可启动。

**明确排除**：安装向导/快捷方式/系统服务、自动下载模型、模型商店、自动更新/签名/SmartScreen、照片路径重新绑定和跨机数据迁移向导、Electron/WebView2、修改搜索算法、GTX 1660 兼容认证、30 万图库容量/正式 P95 认证、公开 GitHub Windows Release。这些若需做，后置独立任务，不在本卡暗中实现。

## 2. 当前实现事实与需先确认的风险

1. `tools/packaging/build_onedir.py` 使用 PyInstaller onedir，但显式排除 `torch` / `torchvision`；之前的 Chinese-CLIP 冻结包 PASS **不能证明 Qwen 路径可用**。
2. `pyproject.toml` 锁定 Python 3.12.10、Torch CUDA 12.8（`torch==2.8.0`、`torchvision==0.23.0`）、`transformers==4.57.0`；ONNX Runtime GPU 存在独立 CUDA 运行栈。PyTorch 与 ONNX 的 CUDA/cuDNN DLL **不能简单合并一个平铺目录**。执行前从实际锁文件/运行时记录准确版本，禁止猜测系统已满足。
3. `src/photo_search/workers/ai_worker.py::prepare_framework_stack()` 已处理 CLIP 的 `USE_TORCH=0` 路径；`workers/client.py` 使用 Windows `spawn`；`__main__.py` 已有 `multiprocessing.freeze_support()`、`--open-browser` 和一次性会话机制。应**复用**，优先通过外部便携启动入口连接现有参数，不为双击另造复杂桌面宿主。
4. `models/embedding/registry.py::shipped_manifest_dir()` 从 `__file__` 推算模板目录，`__main__.py::_default_manifest()` 对冻结运行使用 `sys._MEIPASS`。源码和 PyInstaller 的实际目录层次不同，**必须在冻结 EXE 内证明所有 Provider 模板来源正确**，不要假定 PyInstaller 自动收集了 Qwen 512D/1024D Manifest。
5. 当前 Qwen 512D / 1024D EXIF v2 两种配置**复用相同固定版本权重**，但有不同的 Processor / Space / Generation / Profile 身份；两种维度都必须能被发现及编码，严禁错误共用向量索引。当前默认 Provider 不得改变。
6. `src/photo_search/config.py` 把应用数据放在安装目录以外，默认 `%LOCALAPPDATA%\PhotoSearchData`；命令行可用 `--data-root` 指向**隔离**目录。模型/索引/数据库与 ZIP 分离，不自行复制真实数据。
7. 现有可复用的验证工具为 `tools/diagnostics/packaged_api_smoke.py` 及历史 `P4-02Y-T` 报告；只补缺少的**冻结 Qwen**检查，不重造第二套通用验收框架。

> **开工前快速检查**：先列出「打包时必须收集的模块/动态库/数据文件」「现有冻结包是否漏 manifest」「spawn 子进程重入路径」「本地 Qwen 固定资源是否在授权范围」四项事实。没有证据的风险先测试，不预设必须大改运行时。

## 3. 最小实施路径（顺序和停止条件）

### A. 修通 Qwen onedir 的真实运行闭包

- 复用 `tools/packaging/build_onedir.py`；消除 Qwen 需要的组件被排除问题，补足 PyTorch/torchvision、Transformers、tokenizers、safetensors、必要动态 import、Python package metadata、NVIDIA DLL 和模型模板；尽量**精确收集**，不能靠盲目 `--collect-all` 全站依赖掩盖错误。PyInstaller spec 若为构建产物，遵循仓库现有管理方式。
- 同时保护 ONNX Runtime 的 CPU/CUDA 提供者、Pillow-HEIF/libheif、rawpy/CR2、OCR、人脸加载所需资源；**不重新设计**原有 AI Worker 协议或模型算法。实际证明两种 CUDA 运行栈在不同 Worker/使用场景下不会加载错误 DLL，也不改系统 PATH/CUDA 安装以绕过问题。
- 明确一个**冻结包内资源定位规则**：模型模板读取来自 `_internal`（或 PyInstaller 实际包内根），已安装权重/安装 Manifest 来自指定数据根；不能误读源码工作目录、`.venv` 或打包机缓存。保持本地安装 Manifest 优先级和现有 hash/版本拒绝。
- 静态检查 ZIP/onedir 包内 `web/`、**当前完整** SQLite migration 链、Qwen 512D/1024D 与现有 CLIP 模板、必要第三方 DLL/数据资源。不能用 `photo-search embedding list` 的源码运行结果替代冻结包观察。
- 不从网上下载权重，不把 Qwen 4GB+ 权重放入 ZIP；准备包时若需只读引用既有模型，按第 5 节授权执行。

### B. 双击启动，最小化新增产品代码

- 优先**新增 `Start-PhotoSearch.cmd`（或经证据证明更合适的极小入口）**，相对自身目录调用随包 `PhotoSearch.exe --open-browser`；保留原 `PhotoSearch.exe` CLI 行为，避免无缘无故改 CLI 默认和后台测试。双击后能自动打开**新的有效一次性会话**，不要求用户复制命令或 token。
- 便携入口须解释关闭方法：**关闭浏览器标签不等于停止本地服务**；提供可执行的正常退出方式或明确操作说明，确保 worker 和临时凭据/锁在正常关闭后不残留。禁止新增驻留服务或自动开机启动。
- 安装/解压目录、当前工作目录和数据根互相独立；即使解压到中文/空格路径、移动目录、更换盘符，也不依赖构建时固定路径。默认数据根沿用旧行为，测试使用显式隔离数据根。
- 端口占用、重复双击、缺模型、无 CUDA、权重 checksum 不符、GPU OOM、权限不足、Worker 崩溃应清晰拒绝并给出可诊断信息；无需发明复杂故障 UI，但不能弹出空白页面、悄然使用 CPU 或偷偷切换 Provider/修改图库。**优先复用**现有错误码、端口/数据根机制；若发现进程多开需要改跨进程锁/事务协议，记为明确阻塞，不在本卡临时设计新锁。
- 保持 `127.0.0.1`、一次性 session、CSRF、日志 token 脱敏；不将一次性链接或凭据写入 README/报告。

### C. 输出 ZIP 与用户说明

- 构建**实际** onedir 后，从实际产物打可移动 ZIP；记录构建 HEAD、Python/锁文件、架构、关键依赖版本、解压体积/ZIP 体积、主要占用来源、ZIP SHA-256。既有 PyInstaller Windows DLL 目录可以保留，不为减肥引入新的运行时分包系统。
- `README-PORTABLE.txt` 必须清楚写：系统/NVIDIA 驱动要求与**未验证范围**；解压和双击步骤；准备模型时如何使用当前项目支持的**固定 revision/hash**（不能随意下载上游最新模型）；数据默认路径及 `--data-root` 诊断选项；正常退出、查看错误；删除 ZIP 不会删除独立的数据根。不能暗示「只要一张显卡就保证能用」。
- 便携包可本地测试/私用。把第三方依赖与模型许可证信息和必要 notices 列清，**未确认权利前不公开再分发**受限组件/权重（尤其 InsightFace 官方模型），不因为包可运行便宣称可公开发布。

## 4. 必须维持的产品不变量

- 原图只读、人工人物/地点/收藏/普通与智能合集记录不变；**不碰 schema/migration 内容、向量/模型身份、OCR/人脸算法、查询召回/排序/相关阈值**。仅允许最小冻结环境和文件定位兼容修复。
- Qwen 512D/1024D 共享**权重**，但 processor/space/generation/profile 各自独立；不复用、补零或改写其他维度的向量索引。原活动 Profile 及默认 Provider 不变；模型不可用要显式报告，不静默切成 Chinese-CLIP 或 CPU。
- 不因 EXE 解压/启动而自动建库、迁移或清理旧数据。隔离测试数据根允许创建合成 catalog/索引，严禁指向真实用户原图目录。
- 不下载大模型、不更改系统 DLL/驱动/全局 Python 或 ACL、不清理真实模型缓存和库数据；打包缓存与测试目录遵守仓库 `AGENTS.md`，不使用用户级依赖缓存。
- 必须使用真实冻结 EXE 作为「打包通过」的依据；源码 `uv run` 测试只负责保障回归，不是可携带证明。

## 5. 授权与真实 GPU 验证的边界

**本卡默认无权读取个人真实 `PhotoSearchData` 或真实图库**。可无授权使用仓库内合成 JPEG/HEIC、隔离库、静态模板和已有可复用的代码依赖。若要进行真正的 Qwen CUDA smoke，必须先得到用户**对指定模型权重路径的只读授权**；有授权后只读取固定模型文件，不列举/读取相邻真实图片、catalog、索引，不写权重目录。

- 有授权：使用外部已准备权重，将模型映射/复制到隔离测试数据根的方式应先核对权限与磁盘开销；**优先复用只读路径/受控资源注入**，不得擅自建立指向真实库的目录联接或偷偷修改清单。隔离库里建立少量合成照片的 512D/1024D 真实索引。
- 无授权/模型不可用：继续完成静态打包、无权重错误态及跨目录启动；标记 **`QWEN_GPU_VALIDATION_BLOCKED`**，而不是输出假的 Qwen PASS，也不要自行下载权重。
- 干净 Windows 主机缺席不是放弃本机验证的理由。独立标记 `CLEAN_HOST_PENDING`，不要把 PATH 临时删掉称为干净机。

## 6. 最小且足够的验收矩阵

下面 **V1–V5 是便携包本机技术验收**；**V6 是真正换电脑的外部验收**。逐项记录 `PASS / FAIL / BLOCKED / NOT_RUN` 与真实依据。条件允许应完成 V6；缺少设备可单独保持 PENDING，不把代码实施和换电脑认证混为一谈。

| 项 | 测试入口及通过标准 |
| --- | --- |
| **V1 冻结构建/资源** | 当前 HEAD + 锁定环境真实生成 onedir/ZIP；Web、当前迁移链、全部当前模型模板、native DLL/metadata 确在包中。包内不含权重、真实图库、真实数据库；ZIP hash/版本/体积可核对。 |
| **V2 双击与搬迁** | 复制**完整 onedir/ZIP**到不含源码的中文＋空格路径和不同盘符，用双击入口打开新会话；无需 Python/uv/Git；移走程序再复制到另一位置可正常启动、关闭、重启，未在安装目录写数据。无开发工具的模拟环境仅称 `LOCAL_ISOLATION_PASS`。 |
| **V3 冻结 Provider/故障状态** | 在 `PhotoSearch.exe` 中观察到正确 Qwen 512D/1024D EXIF v2 和 CLIP，实际模板路径来自**包内或隔离数据根**；缺权重/坏 hash/缺 CUDA/端口占用/重复双击均明确处理，无错误的语义可用声明、旧数据损坏或泄漏凭据。 |
| **V4 冻结 Qwen CUDA** | 在**授权的只读模型资源**下由冻结 EXE 的**真实 spawn Worker**加载 pinned Qwen FP16/CUDA，分别产生 512D/1024D 有限、非零、近似单位 L2 的**文字及图片**向量；隔离合成图库使用 512D 做“建小库→文字搜索→以图搜图→退出→重启搜索”，1024D 另用独立空间完成至少一次实际小库建索引与文字查询。设备/维度/Provider/Profile 对应真实报告，不接受 CPU/offload、stub 或直接源码运行替代。 |
| **V5 现有能力/安全/回归** | 冻结包完成原有 `packaged_api_smoke.py` 的适用检查；Chinese-CLIP 真运行有本轮模型资源才声称 PASS；无模型时检查诚实能力降级。OCR/人脸/HEIC/CR2 做受影响导入/解码或最小可用路径检查（需要权重时按授权决定），不把“可导入”冒称“识别效果通过”。检查 session/CSRF/loopback/worker 正常停止，以及合成数据库/原图不被错误写入。 |
| **V6 干净 Windows 电脑** | 在另一台**没有 Python、Git、uv、源代码、项目 `.venv`** 的 Windows x64 机器上，确认可解压、双击、访问页面；如目标含 Qwen GPU 功能，还需具备兼容 NVIDIA 驱动与模型并实测冻结 CUDA 搜索。无此设备时写 `CLEAN_HOST_PENDING`，**不能**标 `CROSS_PC_ACCEPTED`。VM 无 GPU 直通可证明基础启动，不可证明真实 Qwen CUDA。 |

**验收结论分级**：

- `BUILD_ONLY`：V1 成功，冻结运行还未验证；不能叫可用便携包。
- `LOCAL_PORTABLE_ACCEPTED`：V1–V5 所有**适用的必测项**均真实 PASS，包括 V4；V6 尚未完成时明确加 `CLEAN_HOST_PENDING`。意味着本机隔离环境的便携包可用，**不保证换任意电脑成功**。
- `CROSS_PC_ACCEPTED`：在 `LOCAL_PORTABLE_ACCEPTED` 基础上 V6 真正通过，记录 Windows/驱动/GPU/模型身份和测试范围；**不等于 GTX 1660 最低配置认证或正式公开发行**。
- 任一 V1–V5 必需项因未授权/运行失败/条件缺失无法验证，结论只能是 `IMPLEMENTED / VALIDATION PENDING` 或 `BLOCKED`；不得偷偷缩小测试后改称 PASS。外部设备不可用按 V6 单独标 PENDING，不强迫无限等待或拖延已完成的文件交付。

### 最少验证命令与证据口径

首先复用 `tools/diagnostics/packaged_api_smoke.py`；允许**仅为 Qwen 冻结测试**新增 `tools/diagnostics/packaged_qwen_smoke.py`，明确它只启动 ZIP/onedir 的 `PhotoSearch.exe`，所有数据进入隔离临时根。不为一张卡再搭通用测试框架。受影响模型/worker、manifest、启动器、打包工具做定向 unit/integration。

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.tmp\uv-python"
uv sync --locked --python 3.12.10 --all-groups
uv run --no-sync python tools/packaging/build_onedir.py
uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch
uv run --no-sync pytest -q tests/unit tests/integration --junitxml=test-results/windows-portable-regression.xml --basetemp=.pytest-tmp/windows-portable
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- 打包/运行时/依赖改变按 `AGENTS.md` 的**完整受影响面**与全仓 regression 触发规则执行；如既有失败，保留初次退出码/JUnit、证明与本卡差异并复测受影响失败；不可删测试、增加无理由 skip 或宣称一次全绿。
- V4 的实际 frozen GPU 命令由实现者据原 CLI 与权重授权写入唯一报告。不可只跑 `--help`、`torch.cuda.is_available()` 或静态 `providers()` 就认定 CUDA 推理通过；需确认实际 FP16 模型执行和 Query/Index Worker。
- 只有启动入口/错误文案真正改变 UI 时，使用 Codex 内置浏览器对**隔离本机服务**观察一次有效会话、搜索首页与一个典型错误状态；不强制重新测所有 UI2.0 页面。现有 Web 页面视觉语义不在卡内重新设计。
- 测试写隔离仓库 `.tmp/`、`.pytest-tmp/`、`test-results/`，使用真实 JUnit XML/退出码，原始日志不提交。避免日志记录 session token/用户路径。要用别的 Windows 真机必须用户自行授权/安排，不能把模拟结果冒充实测。

## 7. 性能检查（PERF-BOUNDED，三行）

- **链路与风险**：PyTorch/ONNX 两栈可能导致包体积暴增、CUDA DLL 冲突、重复加载模型、首次搜索 RAM/VRAM 占用较大，尤其 Qwen FP16 GPU OOM。
- **做法与边界**：保持既有单活动 Provider、单 Worker / 受控排队和原 batch=1，不同时常驻 CLIP 与 Qwen、不复制 512D/1024D 权重；从包外加载模型，不新建网络服务和全图库后台处理。
- **验证与停止**：记录 ZIP/解压大小、首次启动/首次成功搜索及短量热搜索、GPU/进程数量和 RAM/VRAM；有条件与**相同设备的源码运行**短对照，出现 OOM/异常翻倍再定向修复。不声称正式 P95、最低显卡或 30 万容量。

## 8. 允许修改、报告和最终停止点

**允许的最小路径**：`tools/packaging/**`、与其直接相关的构建 spec 生成方式、`tools/diagnostics/packaged_api_smoke.py` 和必要的新增 Qwen frozen smoke、`src/photo_search/models/embedding/registry.py`（资源定位）、`src/photo_search/__main__.py`（只有确实需要时）、`src/photo_search/workers/**` 与 `src/photo_search/runtime/**`（只限实际冻结运行缺陷）、相关 tests 和便携说明。`pyproject.toml` / `uv.lock` 仅在实证缺依赖时修改并重新锁定。新增任何其他生产代码文件必须先在报告说明直接必要性，不得顺手改变业务逻辑。

**禁止修改**：已发布迁移 SQL、schema 版本、搜索排序/阈值、Provider ID/Processor/Space/Generation/Profile、自动建库/人脸/OCR 判定、人工整理表、图库路径语义、原图和真实模型权重。禁系统级 Python/CUDA/PATH/ACL 修改。

**报告仅一份**：`docs/reports/P4-WINDOWS-PORTABLE-RELEASE-A.md`。包含当前 HEAD/工作树、打包实际改动和精确命令、依赖/DLL 处理、包大小/SHA、V1–V6 的状态、GPU 设备及模型维度、未验边界、最小运行步骤和独立复核结论。更新 `docs/development-status.md` 的精简状态和本卡；原始 JUnit、日志、ZIP 不进 Git。

**有界独立复核**：复核者审完整直接变更、PyInstaller/Worker/Manifest/DLL 运行边界、V1–V6 真实证据及是否触发更深安全/事务审查。若只是环境/打包改动且不改 ADR/schema/API/数据库语义，可记 `SOL_NOT_REQUIRED`；遇到真实高风险契约变化按仓库策略另行交接，不擅自强推。

**完成后立即停止**：汇报实际是 `BUILD_ONLY`、`LOCAL_PORTABLE_ACCEPTED`、`CROSS_PC_ACCEPTED` 还是 PENDING/BLOCKED；列出文件路径、关键验收状态与未验条件。仅提议下一阶段 `P4-WINDOWS-INSTALLER-A`（安装器及模型资源准备）而**不创建/执行下一卡**，不发布 GitHub Release。

**提交策略**：用户已于 2026-10-23 明确要求提交当前这组未提交文件；仅做本地任务范围提交，不推送、不发布、不开始下个阶段。不得访问个人真实图库；若 GPU 验证必须读取外部 Qwen 权重，先取得对具体目录的只读授权。

## 9. 复制给 Codex 的启动提示词

> 基于最新 master 执行 `docs/tasks/P4-WINDOWS-PORTABLE-RELEASE-A.md`，先读 `AGENTS.md → development-status.md → 本卡` 和直接打包/运行实现。只做 **Windows x64 PyInstaller onedir 便携 ZIP**，重点修通 Qwen3-VL-Embedding-2B 512D/1024D EXIF v2 的 PyTorch/CUDA 冻结运行、Provider Manifest 定位、ONNX/PyTorch DLL 隔离、spawn Worker 与双击启动。优先复用现有打包入口、单次会话和 packaged smoke，所有测试使用隔离合成库；需要读取真实 Qwen 权重先请求对指定模型目录的只读授权，不访问真实照片/catalog、不下载大模型。V1–V5 必须按冻结 EXE 实证，V6 有设备再跨机验证；不能用源码通过、仅删除 PATH、只有 API 200 或 `cuda.is_available()` 冒充跨机/Qwen 通过。修复完毕做相关回归与有界独立复核，报告诚实分级。只交付便携 ZIP、使用说明和一份报告；不做安装器/下载器/数据迁移；未经我许可不要 commit/push，不发布、不启动下一任务。
