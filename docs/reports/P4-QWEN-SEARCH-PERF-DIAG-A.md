# P4-QWEN-SEARCH-PERF-DIAG-A — Qwen 搜索性能定位报告

日期：2026-10-20
状态：**静态诊断记录：IMPLEMENTED / VALIDATION PENDING；诊断能力实现：IMPLEMENTED / ACCEPTANCE PARTIAL**
本轮完成静态代码与只读运行环境核查；真实搜索计时被当前服务会话认证阻塞。原始脱敏材料见 `test-results/p4-qwen-search-perf-diag-a.json`。

## 结论摘要

- **目前不能把变慢比例归给模型或项目代码。** 当前 20 次热搜索和分段耗时都没有有效样本。
- 历史独立实验中 Qwen 文本编码 P50 为 **72.86 ms**，Chinese-CLIP 为 **12.1 ms 单次样本**；粗略相差约 **61 ms**，两者样本口径不同，只能说明额外模型编码大致是几十毫秒级参考，不能作为本轮对照或当前回归结论。历史独立模型加载约 **3156 ms**，不能计入正常热搜索。
- 静态生命周期结构**没有发现每次搜索重新加载模型**：`ApiContext.query_encoder()` 缓存 adapter，`WorkerQueryEncoder` 复用一个 client，`AiWorkerClient.start()` 首次调用后不重复创建子进程，`run_worker()` 在循环处理请求前仅调用一次 `load_adapter()`。服务重启、显式释放 encoder、候选模型资源切换等生命周期变化仍可能重新加载；本轮未测 PID 与实际加载次数。
- catalog 有 4727 条 assets，存在 Qwen EXIF v2、512D、READY generation；但同时存在 Chinese-CLIP 和 Qwen profile，运行时活动 profile/provider/device 尚未通过 API 确认。
- EXIF v2 的方向修复只走图片读入分支。`encode_text()` 只构造 query 文本与 instruction 并调用共享 Qwen forward；普通版与 EXIF v2 manifest 使用相同权重 revision、runtime、instruction、维度和 tokenizer。**EXIF v2 不会给文字搜索增加图片方向处理开销。**
- NVIDIA 单次快照：RTX 4060 Laptop GPU，利用率 0%，显存约 4306/8188 MiB，温度 50°C；GPU worker PID 属于 8000 端口服务进程树。空闲快照不能排除此前资源争用，也不能证明运行时精度/provider；任务/队列争用尚未查到实时状态。

## 实际运行绑定与认证边界

初次检查时发现两个 loopback Python 服务。8000 端口的数据根指纹对应 4727 条 assets；8117 端口对应空 catalog，二者数据根不同。仅对 8000 端口服务进行初始环境核查，没有向 8117 发搜索请求。8000 服务进程树包含一个使用 GPU 的子进程；catalog 中 Qwen generation 的 processor 为 `qwen3-vl-embedding-2b-exif-v2`，维度 512、状态 READY，runtime package 记录为 `torch@2.8.0+cu128`。这些是 catalog/进程证据，**不等于运行时 API 已确认活动 Profile、实际 CUDA device、FP16 或模型 health**。

尝试在内置浏览器建立会话时，启动器凭据交换返回 `SESSION_INVALID`。该凭据是单次使用；为遵守“不强制重启正在运行的服务”与认证边界，本轮未重启服务、未再尝试凭据、未调用认证 API 或搜索 API。浏览器 Network、API 总耗时、结果首显及缩略图显示均未测。

## 当前计时结果

| 指标 | 实测结果 | 说明 |
|---|---:|---|
| 冷启动 | 未测 | 需要重启/新实例；当前服务不重启 |
| 热搜索 P50 / P95 | 未测 | 0 个有效样本；认证失败前没有搜索请求 |
| Qwen 编码 P50 | 未测 | 历史参考 72.86 ms，不作当前结果 |
| SQL/FTS | 未测 | `catch_up` 与 `fts_channel` 均无独立计时 |
| LanceDB | 未测 | 运行时 `vector_scan.blocks/scanned_assets` 未读取 |
| 搜索解析与结果处理 | 未测 | UI 入口会经过 place-aware 意图解析与共享结果校验 |
| HTTP 请求 | 未测 | 没有认证搜索请求 |
| `top_k` | 未测 | 没有搜索响应 |
| 实际候选数量 | 未测 | 没有搜索响应；catalog 总资产数不能代替本次候选数 |
| 向量扫描 assets / blocks | 未测 | 没有搜索响应中的 `vector_scan` 元数据 |
| 查询模型 / 维度 / 相似度通道 | 运行时未确认 | catalog 有 Qwen EXIF v2 512D READY generation，但不能证明本次活动绑定 |
| 前端额外延迟 | 未测 | API/首显/缩略图 Network 证据缺失 |
| Worker 是否重载 | 静态路径未见逐搜索重载；20 次 PID/加载次数未测 | runtime health/worker PID 不可读 |
| GPU | 0% 利用率，4306/8188 MiB 显存，单次快照 | 不能判断历史竞争、FP16 或其他后台 job |

阶段名严格遵循用户任务卡；没有精确拆分证据的阶段不以总耗时减法估算。

## 搜索调用链与项目内可疑成本

默认 UI 文字搜索由 `src/photo_search/web/search.js` 的 `sendTextSearch()` 发到 `POST /api/v1/search/place-aware`。默认 `retrieval_mode=combined`；literal/text 控件会显式改成 `retrieval_mode=text`。place-aware 首次搜索可以运行 `parse_natural_intent()`，之后调用同一 `search_request()`/`search_assets()` 检索路径。因此仅压测 `/api/v1/search` 会漏掉默认 UI 的意图解析入口。

组合搜索链路按代码顺序包含：输入解析与 scope/profile 绑定；`encode_query()` 调用 Qwen Worker；`search_documents.catch_up()`（仅有匹配文本时，队列非空则可能在请求中写入）；FTS MATCH；Lance `vector_channel()` 对已确认且符合权威条件的候选分块精确扫描；RRF 融合；权威页统计/复核、FTS 列归因、pending projection 和响应整理。返回体包含 `top_k`、`vector_scan.blocks`、`vector_scan.scanned_assets` 和 `truncated`，可与阶段计时同时记录。当前约 4.7k assets 是否只构成单 block，要以有效搜索响应的实际扫描数量确认，不能由总 asset 数推断候选数。

## 最值得优先检查的 1～3 个候选项（测量顺序，不代表已证实的收益排序）

1. **先复查并测 FTS 投影追赶成本。** 初次只读快照记录 1941 条可行动的 `search_projection_queue`；这是旧时点的状态，最新续测没有重查该 catalog，当前积压数量未知。默认一次 `catch_up()` 最多跑 4 批、每批 200 条，最多处理 800 条，并在 FTS 查询前同步执行。下一次目标服务可用时，先记录当前队列数，再记录每次 queued/processed/written/remaining 和阶段耗时；若热请求明显在清理 backlog，再另开最小修复任务，不能直接删队列或改正确性。
2. **测真实候选上的 Lance 阶段。** 响应会给 `vector_scan.scanned_assets/blocks`；catalog 总数 4727 不等于本次已确认向量候选数。只有扫描阶段确实占时才评估检索优化，保留候选覆盖和排序语义。
3. **测 API 到可见结果的差值。** 记录 Network 搜索执行阶段完成、结果首显和首张缩略图时间；若 API 很快而 UI 显示慢，优先处理渲染/缩略图等待。

持久 SQLite 状态快照中 `jobs` 没有 `QUEUED/RUNNING` 状态，`scan_runs` 全为 `SUCCEEDED`，embedding candidate task 状态为 `COMPLETED`；这不覆盖进程内临时任务。GPU 单快照处于空闲。其余候选项目前没有收益数据。**不建议现在就创建性能修复任务**：首先应在可认证的现有服务会话里完成正式诊断，再决定是否需要代码修复。

## EXIF v2 与模型成本

普通与 EXIF v2 清单都指向 `Qwen/Qwen3-VL-Embedding-2B` revision `9f2f7e710d6d`，相同权重 SHA、PyTorch `2.8.0+cu128`、FP16 声明、512D MRL、query instruction、Qwen3VLProcessor/tokenizer 与 max length。EXIF v2 仅增加 `preprocessing.orientation=exif-transpose-v1`。Qwen adapter 的 `encode_text()` 不读取图片；方向处理位于 `_read_local_image()`/`encode_image()`。未为此重建索引或切换 Profile。

模型复用代码位置：`src/photo_search/api/context.py` 的 `query_encoder()` 缓存 encoder；`src/photo_search/workers/query_encoder.py` 的 `WorkerQueryEncoder` 持有 `AiWorkerClient`；`src/photo_search/workers/client.py` 用 `_started` 保护单次 spawn；`src/photo_search/workers/ai_worker.py` 在请求循环前加载 adapter，随后复用到 shutdown。`src/photo_search/runtime/model_resource_gate.py` 对 query encoder 与 candidate build 做资源互斥/拒绝门控；本轮没有实时 gate 或后台任务状态。

## 后续修复判断与限制

主要原因分类选择：**证据不足**。已有历史数据提示 Qwen 编码会比旧模型慢几十毫秒，但无法用不同样本口径解释当前体验；代码未显示每搜索重复加载模型；EXIF v2 不是文字编码额外成本。SQL/FTS、Lance、资源竞争、前端均未实测，不能归因。

如需完成原卡全部标准，需在服务以新进程启动、产生新一次性链接后，于同一内置浏览器建立会话；不得复用旧链接。然后记录活动 profile/generation/provider/dimension 和 worker health，执行规定的 2 次预热、20 次“海边的日落”及另外两个查询各 3 次，并以 `/search/place-aware` 为默认 UI 路径做 Network/首显检查。若不希望重启当前服务，应先安排自然停机窗口或安全隔离快照；本轮没有创建快照或修改真实数据。

## 复核命令与本次检查

- `nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader` — 一次 GPU 快照。
- catalog 通过当前 8000 服务的 `--data-root` 定位，以 SQLite URI `mode=ro` 打开；查询 `assets` 数量、`search_projection_queue` 可行动数量，以及 `jobs`/`scan_runs` 状态分组。没有读取照片内容或输出私有路径。
- 代码入口：`rg -n "def catch_up|DRAIN_BATCH_SIZE|CATCH_UP_BATCHES" src/photo_search/search/documents.py`；`rg -n "function sendTextSearch|post_place_aware_search|def search_request|def search_assets" src/photo_search/web/search.js src/photo_search/api/routes.py src/photo_search/search/service.py`。
- `git diff --check` — PASS；`.venv\\Scripts\\python.exe -c "import json,pathlib; json.loads(pathlib.Path('test-results/p4-qwen-search-perf-diag-a.json').read_text(encoding='utf-8'))"` — PASS。

本次没有新增诊断代码或生产代码。没有运行 pytest、全量测试、规模测试或维护任务。真实搜索/浏览器必测仍待执行。

## 续测检查（2026-10-20）

用户要求继续实测后，重新检查本机监听状态：8000、8117 均无监听进程；内置浏览器没有标签页或已登录会话。没有启动服务，因为当前运行时数据根绑定和新的一次性会话链接均不可用，按默认配置启动可能指向错误图库或与真实数据根发生冲突。未发搜索请求、未重启或触碰图库。故本轮仍无任何有效冷启动、热搜索、阶段或前端样本；先前历史 GPU/catalog 快照不代表当前状态。继续实测需要用户正常启动目标应用后，在同一内置浏览器使用新生成的一次性登录链接；无需把密钥发到对话中。


## 最新续测复核（2026-10-20 09:32，本地时间）

8000 端口无监听。8117 虽有 Python 服务，但其绑定 catalog 指纹为 `23BE562F5E`，只读确认 `assets=0`，与先前记录的空 catalog 相同；没有把它当作目标图库服务。内置浏览器仍无标签页/认证会话。只读查看数据根绑定及 catalog 计数，未发搜索请求、未启动服务、未读取照片内容。故本次仍没有新增性能样本；之前 1941 条 FTS projection queue 和 GPU 快照均只代表旧服务时点，不代表当前运行状态。JSON 的 `latest_recheck` 记录了本次复核。


## 独立复核

Luna 子代理对本报告、脱敏 JSON 和相关代码做了有界独立复核。静态 Worker 复用与 EXIF v2 判断有源码支持；复核指出服务状态和 FTS 队列数量需要限定到初次快照，现已修正文案。未发现静态结论层面的 blocker；真实图库搜索、阶段计时和前端 Network 证据仍未完成，因此任务继续保持 `IMPLEMENTED / VALIDATION PENDING`。

## 最新续测（2026-10-20 09:47:29 +0800）

8000 无监听。8117 有 Python 服务，但只读检查其绑定 catalog 指纹为 `23BE562F5E`、资产数为 0，不是目标图库。内置浏览器没有标签页/认证会话。未发搜索请求、未启动服务、未读取照片内容；热搜索、冷启动、阶段耗时、当前 GPU/Worker 与前端 Network 继续 PENDING。原始脱敏状态见 JSON `latest_recheck`。

Luna High 本轮只读复核：续测 JSON 与报告/任务卡相符；Worker 复用和 EXIF v2 静态结论有源码支持；未发现 blocker。该复核不补足真实运行时搜索、GPU 或浏览器证据，状态仍为 `IMPLEMENTED / VALIDATION PENDING`。


## 2026-10-20 诊断能力实现跟进

当前状态：**诊断代码 IMPLEMENTED / ACCEPTANCE PARTIAL**。本轮把此前的只读定位延伸为可显式启用的 UI/API 诊断能力；此前静态观察、认证阻塞记录与原始结果均保留，不把空合成库的数据当作真实图库结论。

- 支持 Chinese-CLIP、Chinese-CLIP EXIF v2、Qwen3-VL-Embedding-2B、Qwen EXIF v2。运行时绑定身份用于核验活动 profile、generation、processor 与维度；不一致样本不纳入模型性能结果。
- 默认关闭。需同时设置 `PHOTO_SEARCH_SEARCH_PERF=1` 并进入 `/search?perf=1`；搜索请求仅在明确 opt-in 时增加诊断响应头。产品搜索 JSON 不变。
- 计时包括可独立观察的请求/解析、编码器解析、意图解析、查询编码、FTS 投影追赶与 FTS、向量阶段、融合、权威复查、pending 补查，以及搜索执行阶段（不含完整 HTTP 响应序列化）、结果 DOM 和当前视口缩略图解码就绪等待。不能精确拆分的页面耗时、GPU 设备健康与后台等待不推算，导出为未测/未知。
- 每个样本有请求关联 ID。导出聚合与样本计时、绑定身份及匿名标签；不导出原始查询、照片内容、文件路径、Cookie 或 session token。样本数不足时提示低样本量；该导出不是 SLA 或容量认证。

定向验证：性能诊断 pytest 11/11、既有文字分页 pytest 2/2；Ruff、Python 编译、Node 语法和 diff-check 通过。隔离内置浏览器在空合成库验证诊断页门控和一次文字搜索计时；该次因无活动模型被正确标为绑定不匹配，未计入有效性能样本。额外的两个旧回归模块共 5 项失败：4 项 fixture manifest 版本与当前支持版本不兼容，1 项既有 UI Node harness 缺少 `window.addEventListener`；保留原始 JUnit，不声称这些失败通过。

仍待用户在正常服务会话中，用四种模型分别执行真实图库搜索，核对活动身份、实际 Worker/GPU 状态、阶段分布和可见结果耗时，并生成足量热样本；冷启动只能在可安全控制的独立实例中测量。本轮没有访问真实照片库，也没有宣称模型耗时或性能瓶颈已得到实测结论。任务卡：[P4-QWEN-SEARCH-PERF-DIAG-A](../tasks/P4-QWEN-SEARCH-PERF-DIAG-A.md)。
