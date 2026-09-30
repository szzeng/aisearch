# 整批改动检查与上游 PR

2026-10-01，用户要求检查本轮全部改动、完善说明并向原作者提交 PR。目标仓库 `szzeng/aisearch`，基线 `2d5fd94`，范围含搜索/模型、macOS、Vue 界面、离线地点边界及工具/文档；不局限于上一张地点卡。

本卡完成实现者整合检查、修复、B/D 回归、前端及 wheel 构建，整理可供上游独立审查的 PR。仓库要求的独立 DS-H 工程审查及 Sol 聚焦终审尚未执行，不伪称正式验收；不自动切换模型。本轮仅使用隔离夹具和已有公开 benchmark 证据，不重跑模型比较、不扫描私人图库。

阅读：主架构 §3～11、§15；策略 §2.4、§3、§5；ADR-0001/0003/0014；search-repair、macos-benchmark、frontend-modernization、place-filtering、place-geography 任务记录及直接受影响实现/测试。

检查重点：模型文件/空间身份与设备资格、worker 并发和恢复、SQLite 权威过滤与迁移、人工地点锁与撤销、API 会话/CSRF/路径安全、前端写操作及异步状态、构建资源、安装/升级文档、提交中无模型/图库/凭据/本机产物。结果见下；PR 链接在提交后记录。


## 改动和原因

| 范围 | 改动 | 目的及兼容性 |
|---|---|---|
| 检索和建库 | FG-CLIP 2 Base 768D、RapidOCR/PP-OCRv6；固定文件散列与模型空间；有界搜索线程、worker 交换锁/恢复、SQL 分页、Lance 句柄和 Top-K | 提供当前图文/OCR 接口，避免事件循环阻塞、串回复和全库目标装入内存；旧向量必须重建，人工知识保留 |
| macOS | MPS 图文、CPU OCR/人脸，平台依赖和数据目录 | Apple Silicon 源码可运行；GPU 不可用明确报错，Windows 保留 CUDA 路径但新组合未做硬件验收 |
| 界面 | Vue 3 / TypeScript / Vite / Naive UI；照片、人物、地点、图库及详情 | 复用组件管理加载、分页、冲突与撤销，删除被替代手写静态页面；继续同源认证/CSRF，不增加运行时 Node 服务 |
| 地点 | Shapely STRtree、行政层级、跨区景区、离线包及来源提示；0017/0018 | 杭州包含下级行政区照片，西湖景区独立叠加；人工取消/纠正优先，包更新不覆盖人工知识 |

安装、Node 构建、下载体积、备份/升级和回退见根 README；地理数据语义见 ADR-0014。ADR-0013 中历史预留的 UpgradeRun 0017 已明确失效，冻结参考 JSON 不改写；未来实现须重新编号并复核。其他历史任务报告保留当时证据，当前状态以本卡和 development-status 为准。

## 整合检查修复

- 恢复 OCR 目标选择导出，保留新 SQL 有界选择；修复 CLI/刷新入口的导入错误。
- 地点停用的 AFTER 触发器不能从已排除停用地点的有效视图反查旧关系。新增 0018 从存储关系排队，修复停用/恢复，以及已用过 schema 17 的残留投影；补测 17→18 自动/人工关系和外键。0017 不再原地修改。
- 拒绝畸形 GeoJSON 和同一 namespace 变换图层；详情的自动归属同时核对资产当前 revision。
- macOS 测试使用原生路径与符号链接，保留遍历/链接拒绝的断言；OCR 使用本机中文字体，按分词保留数字间空格。并发写入测试在实际执行线程创建 SQLite 连接，继续验证冲突/重试。
- 内存诊断用 psutil 读取当前 RSS，修正 macOS 峰值 RSS 单位；中断注入在确认首批后握手再杀子进程，避免机器太快导致根本未发生中断，保留独立的 Lance 已写/SQLite 未确认故障窗口。
- 旧 schema 16 专项测试仍模拟其版本，不把历史冻结 DDL 误当当前版本。P2 契约 JSON 与基线逐字节一致，仅修正四处早已不匹配的测试散列常量（实际 `4270997cac64a3c19c80f1d118b5e3d16022fdccb2c0a454c21d43d7730325fa`）。

## 验证与证据

本机 Apple M4 / 16GB，Python 3.12.10；离线执行，仅使用隔离合成夹具及已授权的公开 benchmark 库。首次整体回归暴露上述问题；不以收集成功或无终端输出判定通过，最终以 JUnit 和进程退出码为准。

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv run --locked --no-sync --offline pytest -q tests/unit tests/integration --basetemp=.pytest-tmp/pr-final --junitxml=test-results/pr-final.xml
npm --prefix frontend run build
npm --prefix frontend test
.venv/bin/ruff check src tools tests
git diff --check
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv lock --check --offline
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv build --wheel --offline --out-dir .pytest-tmp/pr-wheel
```

前端 15 项通过（2.07 秒，`test-results/frontend.xml`）；TypeScript 和生产构建通过；Ruff、差异检查、锁文件检查和 wheel 构建通过。逐字节核对 wheel 中 8 个 UI 文件、18 条迁移、模型 manifest 和 OCR 配置，未包含旧 app.js、数据库或模型权重。修复针对性回归 41 项（12.73 秒），地理迁移补测 9 项（0.60 秒），JUnit 分别为 `pr-final-fixes.xml`、`pr-migration18.xml`。

整体回归执行 **1,857 项：1,850 通过、6 跳过、1 项旧 OCR 断言失败，166.34 秒**（`test-results/pr-final.xml`）。失败来自旧测试读取不存在的 run.precision/provider，并假定旧 CPU_PREFERRED；改为核对实际 job 的 precision、runtime_provider、resolved_device 与已资格验证的设备后，相关 **13 项全部通过**（`test-results/pr-ocr-final.xml`），未再修改生产代码。最终覆盖中无未解决失败；未把第一次失败报告改写成全绿，也未重复完整运行。6 项跳过分别为 Windows 盘符 1 项、缺失旧 P0-03 模型夹具 4 项、需显式 CUDA 硬件开关 1 项；它们不算通过。JUnit/构建输出留在本机忽略目录，以上命令可复现；不将模型、图库、凭据或机器日志提交到 Git。

正常入口重启后，原公开 2,000 图目录经自动备份升级至 schema 18，外键检查无错误；浏览器地点页、离线包来源/年份提示正常。此前真实搜索、以图搜图、人物命名/撤销、地点归类/撤销和合成 GPS 层级浏览证据见相关任务记录，本轮未重复模型 benchmark。

未验：Windows NVIDIA/GTX 1660 实机与冻结包（含 GEOS/CUDA 依赖）、macOS 安装包、十万/三十万真实图库、所有人物复杂操作的浏览器路径、独立 DS-H 工程审查及 Sol 高风险聚焦终审。旧容量报告不能代表当前模型和地理版本；历史中国边界和一个西湖景区包不代表最新全国数据。以上均在 PR 中披露，提交为草稿供上游审阅，不标正式 PASS。
