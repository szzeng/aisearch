# P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP — Windows 便携版 AI 模型首次准备

> 日期：2026-10-24。状态：**IMPLEMENTED / VALIDATION PENDING（真实进程强停故障注入未执行）**。用户明确要求执行粘贴的草案；2026-10-24 用户豁免 DS-H 复核并授权简单本地提交，不推送、不发布。本轮不下载模型文件；允许验证固定下载 URL 的可达性/元数据，不取回权重内容。

## 目标与边界

- 在 Windows 便携版提供一个 `Setup-AI-Models.cmd` 入口，按需准备 Qwen、离线 OCR 和可选 Face 权重；复用 `Select-PhotoSearchDataRoot.ps1` 保存的数据根 `%APPDATA%\PhotoSearch\portable-data-root.txt`，不建立第二份根目录。
- OCR 资源来自锁定的 RapidOCR 1.4.4 冻结包并按 `ocr.weights` 校验；Face 只接受授权确认后的固定官方 buffalo_l ZIP 或用户提供的本地 ZIP，并按 `face.weights` 校验；Qwen 只复用既有 `embedding download-qwen`。
- 普通网页状态读取不下载、不哈希大权重、不加载模型、不运行 GPU 探针；区分缺失、待核验、权重通过、运行环境未验证/不可用。不自动建库。
- 不读写真实照片/数据库/用户模型目录，不下载 Qwen 或 Face 权重，不改 Schema、模型身份、推理配置、CUDA 策略或人工数据语义。Face 无合法真实权重时，推理验收记 `FACE_RUNTIME_PENDING`。
- 基线：HEAD `2133c0abe1d65d8873c65e2074e0758819d77fa5`，开工时工作树干净；D: 可用空间约 126 GiB。完成一次最终源码 onedir 构建；不生成 ZIP/发行包。

## 必读依据与调用面

- 主架构：V3.0 §11.1（用户选择的数据目录）。V3.1：§2.4（独立审查路由）、§3.2/§3.4（验收/Pass）、§4.2（性能门禁）。
- 直接实现：`Select-PhotoSearchDataRoot.ps1`、`Start-PhotoSearch.cmd`、`Download-Qwen.cmd`、`build_onedir.py`、`src/photo_search/__main__.py`、Qwen embedding CLI/downloader、OCR/Face contract/weights/engine、图库管理首次使用页。
- 测试与既有证据：`tests/unit/test_p4_qwen_download.py`、P3 OCR/Face weights 和 engine 测试、`docs/tasks/P4-WINDOWS-NEW-PC-READY.md` 与对应报告。

## 性能设计与验收

- 链路与风险：首次启动状态查询和单次模型准备；Qwen 约 4.3 GB、Face 权重总计约 191 MB，重复下载/校验或复制大文件会耗时并占用数据盘。
- 做法与边界：网页只查文件存在/大小及上次校验状态；只有用户显式准备时才哈希/复制。下载链接仅做 HEAD/小型元数据检查；测试使用 mock 与隔离合成文件，不下载模型。
- 验证与停止：受影响的 OCR/Face/Qwen 定向测试、HEAD 链接检查、真实冻结 OCR 合成单图推理、最终 onedir/bundle + API smoke；Face 真模型推理不作为本轮必需项，记 `FACE_RUNTIME_PENDING`。不跑真实图库、完整模型下载或容量压测。

## 不变量

- 同一数据根只由现有 selector 配置；缺失配置时沿用选择流程，拒绝/取消则零准备写入。Start、Qwen 下载与 Setup 之间有简单互斥，友好拒绝并允许用户重试。
- 成功权重必须满足固定 size/SHA；任何失败、取消、断网、空间不足、坏 ZIP、并发、进程中断都不得把部分文件标成就绪或破坏原有有效权重。
- 安全 ZIP 解压只接受 pinned 两个 Face ONNX 文件；拒绝穿越、重复成员、过大展开、损坏归档及哈希不符。TLS 校验保持开启；无任意镜像/可变版本。
- Qwen/Face/OCR 成败相互隔离；普通启动不触发联网、自动哈希、自动安装、GPU probe 或图库建库。

## 验证与交付

- A：选择数据根和 Setup UI/网页文案覆盖（仅短状态、引导脚本，不提供网页下载向导）。
- B：CLI 状态/准备、锁竞争、文件发布/恢复、OCR/Face 安全校验；现有 Qwen downloader 消费者及失败边界。
- C：单次新 onedir、bundle 检查、真实冻结 OCR 合成图片推理、隔离空根 API smoke、中文/空格路径。打包资源变化只做必要冻结验收；不声明第二台主机通过。
- 网络：固定 Qwen revision endpoint、官方 Face ZIP URL 只做 HEAD/重定向元数据检查，记录状态码/最终域名/长度/检查时间，不获取响应实体。
- 独立复核：涉及模型权重发布、锁与数据根状态；按 V3.1 §2.4 完整覆盖修改面及状态/恢复不变量。实际路由/模型以可用环境如实记录，不声称自动切换。
- 证据放 `test-results/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/` 与 `.tmp/`；更新本卡、唯一报告、`docs/development-status.md`。不提交、不推送、不开始后续任务。

## 本轮结果与 Luna High 复核（2026-10-24）

- Face 本地 ZIP 在预检后使用单个打开句柄限量流复制，复制量不得超过预检长度及归档上限；解压写入前按 pinned 精确尺寸拒绝，实际读取长度也受限且必须匹配。根级 pinned 名称安全拒绝，不再触发 `IndexError`。
- 新增覆盖：空间不足时不创建数据根、超大 pinned 成员在发布前拒绝、预检后归档增长、pin 校验失败清理与重试、遗留 staging 清理、既有未验证目录不覆盖、HTTP/恶意主机重定向拒绝；真实 Windows selector 锁冲突覆盖 Configure/Start/Download/Setup 四种模式。
- 最新任务定向 JUnit **105 passed / 4 skipped / 0 failed（109 cases）**：`test-results/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/junit-final-recovery-20261024-115544-581.xml`。Ruff、Python 编译、`uv lock --check`、`git diff --check` 均通过；这不是全仓回归。
- 最新源码重新构建 onedir，bundle validator `valid=true`；隔离中文/空格数据根上的冻结 CLI 状态/离线 OCR 安装/复查通过，冻结 OCR CPU 合成图识别 `PHOTO SEARCH 2026`、1 个区域；隔离 packaged API smoke `status=ok`、`problems=[]`、启动 0.95 秒。产物与证据位于 `.tmp/P4-WINDOWS-AI-MODEL-FIRST-RUN-SETUP/final-toctou-20261024/`。
- 首用页此前已在 Codex 内置浏览器的隔离空根及窄面板观察；本轮没有 UI 改动，因此复用该观察。固定 Qwen revision API 和 Face 官方 URL 的 HEAD 返回 200，最终主机分别为 `huggingface.co` 与 `release-assets.githubusercontent.com`，响应体读取均为 0 bytes；没有下载模型权重或访问真实图库。
- Luna High 对最新完整受影响面做只读复核，确认磁盘尺寸/TOCTOU、根级 ZIP 条目和四种锁模式问题已关闭，未发现新的代码 blocker。异常清理及模拟残留 staging 后重试已覆盖；真实 OS 强制终止发生在每个发布阶段时的恢复仍未直接注入验证。用户已豁免 DS-H 复核要求。
- Face 真实推理仍为 `FACE_RUNTIME_PENDING`。用户已明确豁免 DS-H 复核门槛，Luna High 子代理复核未发现新代码 blocker。真实 OS 强制终止过程的阶段性恢复仍未直接注入验证，因此保持 **IMPLEMENTED / VALIDATION PENDING（真实进程强停故障注入）**。用户授权本地提交，不推送或发布。
