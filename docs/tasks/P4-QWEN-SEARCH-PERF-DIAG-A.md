# P4-QWEN-SEARCH-PERF-DIAG-A — Qwen 搜索性能定位

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
范围来源：本轮按用户提供的多模型诊断能力任务实现；既有真实图库静态定位记录保留在报告中。`docs/development-status.md` 中 C2-F 的并行记录不由本卡替换。

## 目标与边界

在约 4000 张真实图库上定位 Qwen 文字搜图的冷启动、热搜索、阶段耗时、Worker/GPU、后台争用及 UI 可见延迟。只诊断，不更改生产搜索行为、Profile、模型、索引、排序或数据；不得启动与真实数据根冲突的服务。允许正常搜索，请求可能触发 `search_documents.catch_up` 写入，不能宣称严格零写入。

主要 UI 入口为 `/api/v1/search/place-aware`；直连 `/api/v1/search` 不代表默认 UI 搜索路径。冷启动不重启当前服务；只有能安全控制独立实例时才测。

## 性能设计与验收

- 链路与风险：文字搜索覆盖自然意图解析、Qwen 编码、FTS 投影追赶/检索、Lance 候选扫描、融合与权威复核、浏览器绘制及缩略图；潜在成本随图库候选数、待追赶投影和显示图片数变化。
- 做法与边界：固定相同图库、范围、参数与查询；“海边的日落”预热 2 次、正式 20 次，另两词各 3 次；记录搜索执行阶段（不含完整 HTTP 响应序列化）/内部阶段/结果 DOM/首张图片解码就绪与 GPU/Worker 快照。不为测速切 Profile、建库或增加后台任务。
- 验证与停止：本卡只记录观察样本，不作为 SLA/规模认证；完成 20 个有效热样本及必要分段后停止。若服务认证失效或需要重启/新建冲突实例，保留未测并要求安全恢复运行会话后续测。

## 本轮实现增补（2026-10-20）

- 统一支持 Chinese-CLIP / Chinese-CLIP EXIF v2 / Qwen3-VL-Embedding-2B / Qwen EXIF v2；诊断身份取自搜索实际绑定和 adapter spec，不依赖 UI 文案。空间、generation、processor 或维度不一致时将样本标成 `MODEL_BINDING_MISMATCH`。
- 服务端 `PHOTO_SEARCH_SEARCH_PERF=1` 与每次请求 `X-Search-Perf: 1` 双重启用；`/search?perf=1` 仍须检查服务端开关。普通请求不带计时 header，也不改变搜索结果 JSON。
- `Server-Timing` 覆盖可拆分的编码器解析、查询编码、意图解析、FTS 投影追赶/查询、向量检索、融合、权威复查、pending 补查和搜索执行阶段耗时（不含完整 HTTP 响应序列化）；未精确隔离的 `page_ms`、worker 内推理、后台等待留为未测。
- 首次搜索样本只在浏览器内存保留；请求 ID 关联 API 元数据，续页不记为新搜索；视口内图片按现有懒加载流程等待解码就绪、失败或 10 秒超时；不把 decode 完成称为已绘制到屏幕。导出仅包含匿名测试标签，不含原始查询、照片、文件名、绝对路径、cookie 或会话 token。
- PERF-BOUNDED：诊断多在显式开启时读取时间戳和已有响应字段，不做额外 SQL、模型调用或缩略图请求；固定每请求一个小型 metrics 对象，首屏图片只观察当前 viewport。
- 停止条件：定向身份/隐私/路由验证、默认关闭与开关 UI 检查完成即停止；四模型真实运行、4000 张图库统计、硬件/worker live health、真实搜索 Network 和长期 P95 需由用户正常运行应用后验收，不从空合成图库推断。

### 定向验证

- `tests/unit/test_search_perf_diagnostics.py`：11 passed，含四种 Provider 身份、安全字段过滤、绑定不一致、开关 API、opt-in `Server-Timing` 和普通请求无计时头。
- `tests/integration/test_search_text_mode.py -k text_paging_and_time_browse_need_no_profile`：2 passed；保留文字搜索既有分页行为。
- Ruff、Python 编译、Node 语法、`git diff --check` 均通过。隔离内置浏览器在空合成库确认 `/search?perf=1` 显示面板、普通 `/search` 隐藏面板；一次 literal 搜索得到搜索执行阶段 13.2 ms、视口图片解码就绪 39.0 ms，但因无活动向量模型而按预期标无效，不计入模型性能样本。
- 一组额外旧回归有 5 项失败，原始 JUnit 保留：地点数据 fixture 的 manifest schema 与当前支持版本不一致 4 项；另 1 项 UI Node harness 未提供既有 `window.addEventListener`。没有将它们计为通过或修改其验收语义。

### 用户启用与实际测试

```powershell
$env:PHOTO_SEARCH_SEARCH_PERF = "1"
# 在同一 PowerShell 会话中按日常方式启动应用；已运行实例需重启才能读取新环境变量。
& .\.venv\Scripts\python.exe -m photo_search
```

登录后打开 `http://127.0.0.1:8000/search?perf=1`。首个样本只标为 first_observed；仅在元数据显示该请求实际启动 Worker 时标为 worker_cold。之后两次为 warmup；首次观测不等同冷启动，再执行正式查询；标签只填 `sunset-01` 这类不含搜索内容的标识。按「导出性能报告」分别保存当前模型 JSON。切换到另一模型时仍只使用现有模型管理流程；按需重启并用新的一次性登录链接重新进入诊断页，页面会自动读取新的活动身份。报告的 GPU、真实运行设备、图库在线/离线数、后台任务等待及无法拆分的 page 阶段会明确留空/标注未测；实际四模型比较仍待用户运行验收。

## 当前结论

静态代码确认 encoder 在 `ApiContext` 内缓存，`AiWorkerClient` 管理单个子进程，`run_worker` 在消息循环前加载 adapter；正常热搜索没有每次重载模型的代码路径。当前 worker PID 连续性和模型实际加载次数仍未实测。

8000 端口对应的数据目录有 4727 条 assets；catalog 有 Qwen EXIF v2、512D、READY generation。Catalog 同时保存 Chinese-CLIP 和 Qwen profile，单凭只读 catalog 和 `is_active` 不能确认运行时活动 profile。内置浏览器尝试使用启动器凭据时得到 `SESSION_INVALID`；不重启当前服务、不绕过认证。真实 API、阶段及浏览器测量待续。

EXIF v2 manifest 与普通版使用相同 Qwen 权重、runtime、instruction、512D 输出和文本编码处理；EXIF 方向修正只在图片编码路径，因此静态上不会增加文字 query 的方向处理开销。

## 必需验证与交付

- 真实运行时：活动 profile/generation/provider/dimension、worker PID、真实 health/device/precision、活动后台任务。
- 热/冷测速：20 个热样本与两组偏差查询；冷启动仅在不干扰当前实例时执行。
- 分段计时：仅报告真实计时；不可拆分的阶段标为 `not_measured`，禁止以总耗时差值推算。
- 前端：一个真实 UI 查询的 Network 搜索执行阶段完成、结果首显、首批缩略图显示。
- 本次允许：诊断脚本基础检查（如新增）、新增代码 Ruff、`git diff --check`、必要定向检查；不跑全量 pytest/大规模测试。

详细证据见 [`P4-QWEN-SEARCH-PERF-DIAG-A 报告`](../reports/P4-QWEN-SEARCH-PERF-DIAG-A.md)。原始脱敏结果：`test-results/p4-qwen-search-perf-diag-a.json`。

2026-10-20 续测：再次检查时 8000/8117 均无监听服务，内置浏览器无标签页/认证会话。没有基于猜测数据根启动服务；真实搜图计时仍待目标应用由用户正常启动后续测。此检查没有发搜索请求或触碰图库。详见报告续测段及原始 JSON `continuation_check`。


2026-10-20 最新续测复核（本地 09:32）：8000 无监听；8117 绑定旧记录中的空 catalog（指纹 `23BE562F5E`、0 assets），不是目标图库。内置浏览器无标签页/认证会话；未发搜索请求或启动服务。实机热搜索、分段计时、worker/GPU 活动状态和前端 Network 仍 PENDING。

独立复核：Luna 子代理确认静态 Worker 复用和 EXIF v2 结论有源码支持；按复核意见澄清了服务/队列快照时间。实机测量仍 PENDING。

2026-10-20 09:47 续测：8000 无监听；8117 绑定空 catalog（指纹 `23BE562F5E`、0 assets），不是目标图库。未向其发请求、未启动服务或访问照片。实机测量仍待目标应用正常运行并有有效登录会话。

Luna High 本轮只读复核无 blocker；确认续测快照与文档一致、静态结论有源码支持。真实运行时测量仍 PENDING。
