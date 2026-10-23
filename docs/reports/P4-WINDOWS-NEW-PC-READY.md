# P4-WINDOWS-NEW-PC-READY — 续验报告

**状态：IN PROGRESS / ACCEPTANCE PARTIAL**（2026-10-23 续验）。A 的 Qwen 冻结 CUDA 前置已通过。本轮补入图库管理首次使用引导和一页中文说明；源码验证通过，冻结包尚未重建，下载/OCR/干净主机验收仍未闭合。未提交/推送。

## 已核对

- **N1 PARTIAL**：冻结包在中文路径/隔离空根启动，Catalog 初始化、一次性会话、CSRF、首页、stats、capabilities、embedding status 成功；0 assets、模型未安装。内置浏览器观察首页与图库管理。原冻结包仍没有本轮源码新加的首次引导。JUnit 两次均 1/1：`.tmp/P4-WINDOWS-NEW-PC-READY-N1/N1-empty-root.junit.xml`、`N1-empty-root-rerun.junit.xml`。本轮重跑从中文 hardlink 包路径启动，实际退出清理正常；内置浏览器再次观察首页及图库管理，模型未安装状态正确，但首次引导确实缺失。
- **N2 SCOPED PASS（当前机模拟）**：临时设置 `CUDA_VISIBLE_DEVICES=-1` 后，冻结 GPU-required Qwen Worker 明确报 `EMBEDDING_DEVICE_UNAVAILABLE` 并退出，无 CPU fallback；不替代无 GPU 主机验收。
- **N4 SCOPED PASS**：冻结 `embedding prepare-local` 对授权只读权重完成五文件、4,266,571,889 bytes pinned hash 校验；512D 与 1024D 使用相同 revision/权重，各自身份和 Space 独立。
- **N5 PARTIAL**：冻结包在两个隔离合成根中各扫描 3 JPEG，候选构建确认 3/3 GPU embedding、Lance 3/3，profile gate 完整；512D 文字/图片搜索、重启文字搜索成功；1024D 文字搜索、重启搜索成功。未做首次使用 UI 操作和缩略图浏览验收。数据位置 `.tmp/P4-WINDOWS-PORTABLE-RELEASE-A/v4-512`、`v4-1024`。

## 未完成项

- **N3 PENDING**：还没有官方固定 revision 下载器/首次准备保护 UI，也没有本地 HTTP 小文件的白名单、取消、中断、坏 hash、磁盘不足模拟。现有命令仅支持从本地目录校验导入。
- **N6 PENDING**：OCR 没有用合成文字照片做实际识别；人脸权重按卡内许可边界保持可选。
- **N7 SOURCE_IMPLEMENTED / VALIDATION_PARTIAL**：新增首次使用四步引导、单页中文说明，并调整构建脚本以把说明复制到新包。源码服务空根在内置浏览器确认引导和模型未安装状态；未做窄屏视觉/关键交互检查，冻结包也尚未包含该 UI。
- **N8 CLEAN_HOST_PENDING**：没有独立干净 Windows x64/NVIDIA 主机。
- 本轮检查时 D: 可用空间约 **6.98 GB**。此前 packaged smoke 复制完整 onedir 因 WinError 112 失败；当前空间仍小于约 11.5 GB onedir 产物，不能重建冻结包。源码实现和小型 JUnit 尚有空间，但完整验收仍需冻结重建。保留所有现有临时数据/证据，未擅自清理。

**结论**：N1、N5 部分通过；N2、N4 为本机范围通过；N3、N6、N8 未完成；N7 源码已实现但验收部分待完成。要继续实现并按冻结包验收，重建冻结 onedir 仍需先释放额外空间以容纳约 11.5 GB 产物及构建暂存。未访问真实照片/catalog、未下载模型、未写授权模型权重。


## 本轮追加（2026-10-23）

- N7 源码补齐：图库管理在尚无来源或默认 Qwen 尚无活动发布 profile 时显示“运行环境→模型→照片目录→建库”四步引导，明确 Qwen 需要 NVIDIA GPU/驱动、不会 CPU 回退、模型状态不代表 SHA/GPU 验证，并说明当前版本没有官方自动下载入口。新增 `tools/packaging/NEW-PC-QUICK-START.txt` 一页中文说明，并将其加入 `build_onedir.py` 的随包复制列表，覆盖解压、启动、模型导入命令、手动登记照片目录、建库和重启。
- 源码服务使用隔离空根 `.tmp/P4-WINDOWS-NEW-PC-READY-source-ui/data`，一次性会话后在 Codex 内置浏览器查看 `/` 和 `/?view=sources`；实际看到空图库、Qwen 未安装和完整四步引导，非 AI 浏览入口仍可用。仅有可访问性树观察；未取得窄屏视觉证据，不能标 N7 UI PASS。服务已停止。
- `node --check src/photo_search/web/app.js` 与 `git diff --check` 通过；`tests/unit/test_p4_embedding_provider.py` **35/35**，JUnit `test-results/P4-WINDOWS-NEW-PC-READY/provider-focused.xml`。
- `huggingface_hub` 存在于 `uv.lock`，但当前没有已实现的固定下载/取消/发布链路或 N3 本地 HTTP 安全测试；未尝试公网下载。OCR 权重未在合成根安装，本轮未取用外部 OCR 权重或执行 N6 推理。N8 无设备。
- 本轮 D: 可用约 6.98 GB。现有 `dist/PhotoSearch` 约 11.5 GB；C 级 smoke 脚本会先复制完整 bundle，因此此空间不足以安全重跑现有包 smoke，也不能重建含 UI 改动的冻结包。A 的 V3/V5 原结论未改变。未访问真实照片/catalog，未修改模型权重。


**本轮最新磁盘状态**：A packaged smoke 复制和合成测试完成后，D: 约余 1.99 GB；复制包与 fixture 均保留，没有删除证据。

## 2026-10-23 主流程续验（用户选择“先做主流程”）

本轮按用户选定范围，只补新电脑的核心闭环，不扩到 N3 全部下载故障矩阵、N6 OCR 或 N8 第二台机器。使用新隔离根 `.tmp/P4-WINDOWS-NEW-PC-READY-main-flow/`，从 Codex 内置浏览器实际操作当前源码服务；照片来自项目已有的三张合成 JPEG，Qwen 固定权重目录只读。真实照片、catalog 和模型文件均未读取/改动。

- 空根首次使用页可见，未安装/未激活状态正确；从“添加图库来源”登记合成目录成功，图库来源数为 1。
- 在 UI 点“建库/补齐”后，状态显示刷新完成、语义索引 **3/3**、待处理 0；照片浏览页显示 red/green/blue 三张合成照片。
- 通过首页进入综合文字搜索 `red photo`，Qwen 512D EXIF v2 实际返回 **3/3** 候选结果。
- 停止并重启同一隔离数据根后，首页仍显示 3 张，图库来源仅 1 个；再次搜索返回 **3/3**。这验证源码 UI 主流程的持久化，不冒充冻结 ZIP 中的 UI 证据。
- 冻结 EXE 上先前已有的 N5 证据仍覆盖 GPU 建库、文字/图片搜索及重启搜索；两类证据组合支持本机核心流程，但当前工作树含并行 UI 改动，冻结包尚未重建，因此完整 frozen-EXE UI 闭环仍待验。内置浏览器未能通过受支持操作设置图片文件选择器，本轮 UI 只验证文字搜索；图片搜索继续引用 A 的冻结 EXE/API 证据。
- 本轮未跑 N3 下载器模拟（当前版本没有官方固定下载器）、N6 OCR 实际识别、窄屏视觉专项/浏览器图片上传、全仓回归或 N8 干净主机；这些保持 PENDING，不阻止本轮缩减范围的核心流程结论。N3 下载/准备实现和将本轮 UI 打进冻结包仍是交付缺口，整体仍 **IN PROGRESS / ACCEPTANCE PARTIAL**。
- 隔离运行日志保留于该 `.tmp` 目录。服务与本轮 Qwen Worker 已停止，数据和合成 fixture 保留。当前 D: 可用空间约 14.2 GB；空间读数仅为本轮快照，不代表可以完整重建并额外复制/压缩冻结包。

## 2026-10-23 首次使用页视觉续验

本轮在 Codex 内置浏览器的深色主题实看图库管理首次使用卡，发现引导文字使用浅色前景、卡片却退回浅色背景，造成明显低对比。按 UI 2.0 语义 token 将卡片底色改为 `--tint`，版本化 CSS URL 后重新加载实看；当前 1265px 截图中标题、四步说明与次要说明均清晰可读。`git diff --check` 通过。浏览器窗口没有提供设置窄视口的可用控件，本轮只能记录窄屏视觉仍 PENDING；本次截图也未导出为项目文件。
