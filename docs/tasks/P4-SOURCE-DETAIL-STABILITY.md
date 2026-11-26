# P4-SOURCE-DETAIL-STABILITY — 新图库任务与统计明细卡顿及重复读取修复（工作包 A）

建卡日期：2026-11-25。状态：**PLANNED / WORK PACKAGE A**。原始审查基线：GitHub `master`，`22a47456738b95abbc867f60ec59fd13c14aa58c`；当前专项实施基线：`b334bd75a020108bf826e2790d2850290bf4210a`（2026-11-26）。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。并列工作包 B：[P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md)。

本卡只负责图库明细的前端请求生命周期、只读 Worker、空 scope 和 SQL 读取成本；不负责 SQLite 写入优先级或后台 writer 调度。与工作包 B 属于同一专项，但分别实施、分别验收，最后进行联合并发测试。

A1 前置门槛已由用户实测确认和随机浏览独立复核豁免收口。A1 与 B 的实施和验收分别执行。

## A1 · 图库明细读取与前台响应

本轮基线 `3d6ad4f`。用户确认随机浏览时间和孤儿资产清理的使用验收均通过，并豁免随机浏览时间的独立只读复核。A1 自身的独立审查和浏览器验收继续适用。

- **PERF-BOUNDED，链路与风险**。展开图库明细和登记后的列表刷新。重复请求、空范围聚合、两个读 Worker 同时被占用会放大排队时间。
- **做法与边界**。先在小型合成图库测一/两条明细、普通读和建库重叠，依证据确定明细排队。保持两个 Worker、现有 DTO 与统计口径，以一致的读快照跳过空范围统计。
- **验证与停止**。按现有[性能指南](../performance.md)用相同样本对照 Worker 排队、查询及耗时。运行受影响 B/D、A0 七场景、真实 HTTP 取消/关闭、内置浏览器和独立审查。小样本只描述局部结果。新增索引、迁移、统计语义或 A0 协议变化须先停。

**A1 IMPLEMENTED / VALIDATION PENDING**。图库明细按来源单飞并保留有效缓存，入口有界等待并在实际 Future 结束后释放容量。空范围复用只读快照和位置计数，读 SQL 13→8（无模型合成臂）。最终受影响 JUnit 400/400，含 A0 7/7 和真实 HTTP 断开/关闭回归；前端定向 3/3。已观察合成图库真实页面、窄窗口和排队，用户停止 Computer Use 后，页面失败/重试由自动测试覆盖，未现场复验。独立完整受影响面审查待办。 详见[A1 报告](../reports/P4-CATALOG-CONCURRENCY-A1.md)。

**独立审查接力。** 以 `3d6ad4f` 之后的 A1 变更、新增集成回归、A1 报告及本地 JUnit 为输入，在 Codex 中手动选择 DS-H，完整核对共享池/A0、取消/关闭、缓存代际及快照统计等价。用户已停止 Computer Use，失败后重试的浏览器尾项仍待人工验证。

## A0 · 查询池锁序（P0）

用户于 2026-11-26 单独授权 A0，基线为 `6f0bc84c6ce09948ee330809cc38fb7d77fabd0f`。本轮仅复现和修复查询池锁序，A 其余工作与 B 的启动门槛沿用父卡。先在合成子进程用真实锁与 Future 回调证明循环，再改产品代码。证据和命令收在[诊断报告](../reports/P4-CATALOG-CONCURRENCY-DIAGNOSTIC.md)。

- **PERF-BOUNDED，链路与风险**。查询池提交、完成、取消与 Memories 等待读空闲。已完成 Future 可同步回调，造成锁顺序反转。
- **做法与边界**。复用现有两个 Worker、队列状态和通知。先评估仅将回调注册移出临界区，每次提交保持原有回调和通知次数，沿用现有响应预算与[性能指南](../performance.md)的局部对照口径。
- **验证与停止**。用 Event 控制完成/等待交错并由子进程 watchdog 留栈。修复前须见真实互等。修复后检查四组阴性对照、请求清理和回忆唤醒、查询/投影/搜索入口、关闭与 lease，并做普通提交前后对照。失败时保留证据，范围限定至直接消费者和故障边界。

验证按 V3.1 §3.2 的受影响 B 和对应 D 生命周期场景。改动界定在共享池回调注册处，连接与数据语义无变化，无全量回归触发。

**A0 SCOPED PASS / TERRA HIGH REVIEW PASS**。合成子进程复现 `_lock` 与 `_activity` 双向互等，修改为锁外注册 Future 完成回调。复现前 1 个目标失败、4 个阴性对照通过，修复后 7 个新增场景及最终受影响 JUnit 328/328 通过。取消/关闭、Memories 和数据根 lease 回归通过。 两个 Worker、既有取消和租约所有权保持。局部无 I/O 提交对照未见一致性额外开销。

**独立审查。** 用户指定 Terra High 替代 A0 默认 DS-H，执行完整受影响面独立审查。 审查通过，无 P0/P1/P2 发现。独立重跑 7/7，复核原受影响集 328/328 的 JUnit。A0 之外的工作保留原门槛。

## 1. 用户问题与目标

用户实际复现：

1. 新图库已经成功登记，尚未建库。
2. 点击展开该图库的「任务与统计明细（按需读取）」。
3. 页面及后台可能长时间没有可见进展；同时打开两个尚未建库图库时更容易发生。
4. 登记新来源后更容易复现。

本卡主目标是让新建/未建库图库的明细快速、可靠地返回；多个图库同时展开时，不因重复请求或读取池被占满而拖住页面和其他后台工作。先区分真正的 SQLite 锁等待、SQL 执行过久、Worker 排队和前端重复请求；在证据出现前不把现象定性为“数据库死锁”。

验收场景围绕“登记成功 → 展开一个/两个未建库图库 → 再登记一个图库 → 后台建库并行”完成，不扩展成全站性能重构。

## 2. 当前代码证据与待验证假设

以下是立卡时已核对的代码路径，不等于已经证明根因：

- `GET /api/v1/sources/{id}` 在 `api/routes.py::get_source` 中通过 `run_catalog_read()` 执行 `_source_detail_read()`；后者依次调用 `CatalogService.get_source()`、`catalog_counts()` 和 `refresh.progress.source_progress()`。
- `api/catalog_reads.py` 的 `CATALOG_WORKERS = 2`。两个耗时明细请求可以占满该 Catalog 的只读 Worker；是否因此影响具体其他请求，需在实际并发测试中验证。
- `refresh/progress.py::source_progress()` 即使当前来源没有可统计的 PRESENT 资产，也会继续计算语义 breakdown、资产分母以及 taken-time、GPS、place、face、OCR 阶段状态。对空 scope 的部分 SQL 可能是无意义工作；应先保持所有状态语义，再验证可否短路。
- `catalog/store.py::catalog_counts()` 在来源级统计中仍先执行全库 `SELECT COUNT(*) FROM assets`。它可能增加大图库明细成本，但不能在未确认 DTO/页面语义前直接删掉 `assets_total`。
- `web/app.js::loadSources()` 每次刷新来源列表都会把 `state.sourceDetails` 清空，却保留 `state.sourceDetailOpen`；`renderSources()` 会恢复原先展开的明细并重新加载。登记新来源后的摘要刷新因此可能重新请求此前展开的图库。
- `loadSourceDetail()` 没有按 source_id 的 in-flight 请求去重，也没有在折叠明细时取消请求。初始化展开路径与 toggle 事件是否会造成双请求，须用前端测试确认，不预设每次必然重复。
- 通用前端 `request(path, options)` 已支持 `AbortSignal`，可在不改变 API 的前提下实现安全取消；需确认取消确实释放服务端请求/SQLite Worker，而不只是让 UI 停止等待。
- 另一个次要候选：`POST /sources` 的 `register_source()` 最终返回完整 `get_source()`，会附带 counts；当前前端提交后不使用该响应内容，而是重新请求轻量 `/sources/summary`。先测其实际成本，只有确认属于同一链路的明显浪费时才做最小兼容调整。

## 3. 实施范围与优先顺序

### 3.1 先补最小诊断

- 在隔离合成 catalog 中分别测：单个未建库明细、两个未建库明细并发、登记第三个来源后刷新、明细与建库重叠。
- 区分 HTTP 总耗时、Catalog Worker 排队/连接打开耗时和 SQL 执行耗时。优先复用 `catalog_reads.py` 已有的 perf metrics / SQL trace 能力及测试替身；不新增常驻日志系统，不记录原图路径、照片内容或用户隐私。
- 对实际可疑的 `semantic_breakdown`、`enrichment_counts`、`ocr_enrichment_counts` 和 source-specific counts SQL 检查查询计划。尤其核对 `j.subject_id` 的 CAST 是否影响 `idx_jobs_subject` 的使用；只有执行计划和小型对照支持时才改写，不能仅凭 SQL 外观判断。

### 3.2 前端请求生命周期（优先修复）

- 为每个 source_id 建立单飞请求状态：同一图库在同一时刻最多一个明细请求；toggle、首次渲染和刷新重建不得重复提交。
- `loadSources()` 刷新后保留仍存在来源的有效明细缓存；只清理已删除来源。新登记来源没有缓存时正常按需加载。
- 对建库/同步完成、明确需要重新读取的来源，只失效对应 source_id 的缓存；不能为了更新一个图库而清空全部图库明细。保留现有统计数据在真正需要更新时失效的规则。
- 如果来源卡片重建时某个请求仍在运行，应让新容器接续同一请求结果，不能让结果只写入已脱离 DOM 的旧容器，也不能因此再发一份相同请求。
- 多图库明细请求采用有界并发，优先保证至少留有前台读取余量；默认先评估“同时最多一个明细查询”，不盲目提高 Worker 数量。第二个明细应显示明确的等待/读取状态，不能表现成无响应。
- 用户折叠明细时，可取消已经不再需要的请求；必须处理取消与重开、列表重建、共享 in-flight 请求之间的竞态。确认 AbortSignal 能中断服务端 SQL 并释放 Worker；若不能，不能把前端超时/隐藏加载状态冒充取消成功。
- 失败保留独立错误和重试入口；一个来源失败不能清空其他来源结果。避免无依据的无限超时或后台仍运行的“假取消”。

### 3.3 未建库/空 scope 快速路径

- 在 `source_progress()` 中先取得权威 scan 计数、built marker 和当前 PRESENT 绑定资产数。
- 当 scope 没有当前可统计资产时，评估跳过语义与五个增强阶段的逐项任务统计，直接构造与现有函数完全等价的零计数结果。
- 严格保留：`UNBUILT` 与 `NO_IMAGES` 的区别；语义通道不可用时的 `UNAVAILABLE`；每个增强阶段的 `NOT_STARTED` / `UNAVAILABLE`；scan 的 files/present/missing；`built` 和 `search_ready` 含义。
- 不能把“还没扫描”“扫描过但内容未绑定”“建库已分类但无图片”“模型/地名数据不可用”混成同一个状态。
- `catalog_counts()` 的全库资产总数、来源位置计数和扫描/任务计数保持现有字段与准确性；若它是实际瓶颈，再在不改变 DTO 语义的前提下做最小优化。

### 3.4 SQL 与 Worker 保护

- 对有非空 scope 的查询，依据真实 EXPLAIN 和数据形状验证索引路径；若改写 CAST 关联，验证 asset subject_id 的规范字符串身份、历史任务、状态分类、去重与统计结果完全等价。
- 不增加 schema、表或索引迁移；若证据确实表明必须新增索引，先停止并说明原因，另行取得用户确认。
- 不修改 `CATALOG_WORKERS` 数量，不引入全局互斥锁，不把读请求改成写请求，不跨线程复用 Catalog/SQLite connection。只有证明现有有界请求仍无法满足需求，才讨论 Worker 调度变化。
- 只读明细不能启动扫描、建库、模型加载、原图探测或修改任何持久数据。

## 4. 不变量与禁止事项

- 来源身份、asset_id、file_locations 多位置关系、jobs 身份/revision/state、语义 generation、人工人物/地点状态及所有既有统计口径不变。
- 不因快速返回而伪造“完成”“可搜索”或把缺失统计显示成 0；未加载与真实零值必须可区分。
- 只读请求保持只读；取消时不得留下 SQLite 连接、线程或 Worker 占用。
- 不扫描、读取、修改、移动或删除用户真实照片库；仅使用 `tmp_path` / 项目忽略目录中的合成数据。
- 不下载或加载模型，不修改依赖、锁文件、打包配置或数据迁移。
- 不以增加 Worker、延长 SQLite timeout、隐藏错误、取消加载提示或降低统计准确性掩盖问题。

## 5. 性能设计与验收（PERF-BOUNDED）

- **链路与风险**：登记后来源列表重绘、展开明细、两个 source scope 并发及后台建库重叠；风险来自重复明细请求、空 scope 仍执行多组 SQL、全库资产计数及两个只读 Worker 被占满。
- **做法与边界**：先消除重复请求和空 scope 的无效查询，再依据实际 SQL 计划优化关联；明细请求有界，不改 Worker 总数、schema 或建库预算。工作量分别观察全库资产数 N、当前来源可读绑定资产数 K 与同时展开来源数 S；不把局部小样本外推为 30 万性能承诺。
- **验证与停止**：2～3 个合成来源、约 50～100 张合成照片；重点覆盖两个未建库来源并发、登记第三个来源后刷新、明细与建库并行。用定向测试和一次内置浏览器闭环；无重复请求、无长时间无响应、建库持续推进且统计等价后停止，不新增 30 万容量验收。

## 6. 验证计划

按 V3.1 §3.2 采用完整受影响 B（前端请求生命周期、API、progress 查询及直接调用者），并执行必要的 Node/ruff/diff-check。只有实际修改共享 Worker 取消/关闭生命周期、schema/索引或其他无法界定的公共基础设施时，才扩大到对应 Worker 故障测试或完整回归；不因本卡标题自动跑全仓。

优先复用并按实际 diff 补充以下测试：

- `tests/unit/test_api_frontend.py`：摘要刷新后缓存保留/定向失效、同一 source_id 请求去重、展开/折叠/重开、旧响应不能覆盖新容器或新状态。
- `tests/integration/test_api_sources_assets.py`：新登记来源明细、来源统计 DTO 和只读边界。
- `tests/integration/test_p4_02y_i_source_build.py`、`tests/integration/test_p4_balanced_metadata_reads.py`：UNBUILT/NO_IMAGES、各增强阶段状态和统计等价。
- `tests/integration/test_p4_perf_catalog_reads.py`、`tests/integration/test_p4_perf_catalog_worker_lifecycle.py`：仅在本次改动触及相应 Worker/SQL 行为时运行或补测；先确认文件与当前实现仍适用。

浏览器验收使用项目内置浏览器、127.0.0.1 和独立合成 data root：

1. 登记两个含合成文件、但未扫描/未建库的来源；分别展开明细，随后同时展开，确认两张卡都有明确状态且最终都能完成。
2. 两张明细保持展开时再登记第三个来源；确认旧来源不重复发明细请求、不闪回加载状态，新来源正常出现。
3. 对其中一个来源启动建库；确认明细读取不会让其他页面/API 无响应，建库状态持续变化。
4. 折叠再展开一张卡，确认取消/重开不会遗留 Worker，也不会显示旧请求结果。

浏览器只验证上述关键闭环，不做全站截图或多分辨率矩阵。所有 pytest 使用独立 JUnit XML 和 basetemp，记录真实退出码；不要求全量、30 万、GPU、真实模型或发行测试。

## 7. 审查与收口

- 若改动仅限前端请求去重/缓存失效与只读 progress 空 scope 快速路径，按 §2.4 评估是否需要独立审查，报告完整列出受影响文件和调用链；不得把自检称为独立审查。
- 若改动共享 Worker 取消/关闭、全局调度优先级、事务/租约、schema/索引或数据身份语义，停止扩展并按 §2.4 / §11 要求执行完整受影响面 DS-H；需要改变冻结协议时先征求用户决定。
- 最终报告：`docs/reports/P4-SOURCE-DETAIL-STABILITY.md`。完成后更新 `docs/development-status.md` 与本卡状态；当前任务未结束前，本卡保持 NEXT TASK。
- 任一必需 API/前端/浏览器验收未完成，保持 `IMPLEMENTED / VALIDATION PENDING` 或 `BLOCKED`；不能把没有复现出真正死锁说成“死锁已修复”。只报告已验证的卡顿来源和局部使用结论。
