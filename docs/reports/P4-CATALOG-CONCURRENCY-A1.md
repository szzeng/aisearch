# P4-CATALOG-CONCURRENCY-A1 图库明细读取与前台响应

2026-11-27 · 基线 `3d6ad4f328b9772fa77b2bc2e65e197cbfbd4eb9` · **IMPLEMENTED / VALIDATION PENDING**

用户确认随机浏览时间及孤儿资产清理的使用验收通过，并豁免随机浏览时间的独立只读复核。该反馈未提供浏览器版本、截图或分步骤结果。A0 已提交并推送，原锁序回归保留。A1 独立审查继续适用。

## 实施

- **请求与缓存。** 同一 source_id 的有效请求单飞。消费者以明细容器和渲染代际登记，重绘复用在途请求，只有最后一位消费者离开才 abort。旧结果不写入缓存或新容器。来源摘要刷新保留有效缓存。状态或最近成功扫描变化、单独扫描结束和删除按来源失效。建库与同步的终态失效见下方 P2 续修记录。切走或关闭页面取消不再使用的明细，返回页面可重新读取。前端依次发送明细，显示排队、读取、空结果、失败和重试。
- **查库容量。** 受控基线中两条明细同时占满两个 Worker，照片列表和人物浏览卡在池队列。后端把单一来源明细限制为 1 条占池、最多 2 条在池外 FIFO 等待，覆盖验收中的三图库并发。第四条返回 `503 SOURCE_DETAIL_BUSY` 和 `retryable=true`。其它查询仍共用原来两个 Worker。浏览器分页面或直接 API 访问同样受此实例上限约束。
- **断开与关闭。** 来源明细路由监听 ASGI disconnect 并取消等待工作。排队取消即时移除票据。活动 SQL 沿既有 `_Request.cancel`、progress handler 与 SQLite interrupt 路径收尾。实际 Future 完成且连接已关闭后才释放明细 slot。关闭拒绝队外票据，原查询池继续在释放数据根 lease 前清理活动连接。A0 回调注册及锁序保持。
- **统计。** 以既有 PRESENT 绑定资产计数判断空范围，复用同一快照内的来源位置计数。空范围不再执行语义和增强聚合。保留 built marker、UNBUILT/NO_IMAGES/UNAVAILABLE、OCR runtime availability、历史位置和全库资产计数。单明细同样使用线程自有只读 WAL 快照。扫描在另一连接提交时已验证两次读取各自一致。
- **登记。** `POST /sources` 返回原有 `SourceDetail` counts，随后摘要读取只取身份字段，两条调用的实际工作不同。该合成样本下登记与摘要均为毫秒量级，未调整 DTO。原有全库 `assets_total` 保留准确值。

## 基线和性能证据

复用 `ApiEnv` 和真实 API 入口，60 张合成照片，两个未扫描范围各 10 张文件，一个已扫描范围 40 张。实际并发对照用 Event 控制 Worker 挂点，两个版本同时展开两条明细，先观察窗口约 150 ms，再放行。另加合成语义绑定核对语义 SQL 计划。用既有 SQL trace 计数，连接代理计执行及取数时间，单独记录 Worker 等待、打开、构造、关闭、读事务与 HTTP 阶段。主要 JSON 位于 `test-results/p4-catalog-concurrency-a1/`。

| 同形状对照 | 基线 | A1 |
| --- | --- | --- |
| 未扫描明细，真实无模型配置读取 SQL | 各 13 条 | 各 8 条 |
| 未扫描明细，有合成语义绑定读取 SQL | 各 14 条 | 各 8 条 |
| 已扫描明细，有合成语义绑定读取 SQL | 14 条 | 13 条 |
| 照片列表 / 人物浏览在明细被挂起时完成 | 未完成 | 均完成 |
| 对应 Worker 队列等待，照片 / 人物 | 169.13 / 168.36 ms | 0.50 / 6.06 ms |
| 两条受控慢明细 HTTP 总耗时 | 175.57 / 177.04 ms | 172.00 / 174.57 ms |
| 登记来源 / 来源摘要 HTTP | 4.35 / 2.38 ms | 4.46 / 1.13 ms |

无模型无额外挂起臂的单条空范围 Worker 执行与排队观察约 3.2–3.8 ms → 3.3–3.9 ms。A1 空范围只读快照约 2.45–2.69 ms。有绑定受控臂含单次冷启动抖动，单明细无稳定加速结论。真实 TCP 断开后观察到 SQLite 中断，Worker Future 完成且连接关闭，定向实例间隔约 0.49 ms，随后同一来源读取 200。结果只描述本机夹具和受控交错。

`EXPLAIN QUERY PLAN` 显示来源位置聚合走来源索引，PRESENT 绑定范围走 `idx_file_locations_present_source_asset`。语义及普通增强 `CAST(j.subject_id AS INTEGER)` 的计划先用 `idx_jobs_processor_state` 再按 asset rowid / revision 关联。OCR 相关子查询走 jobs 五字段唯一索引，保留 legacy 文档及 revision 的实际口径。全库 count 使用覆盖索引 `idx_assets_created_id`。本轮用空范围分支消除这些不必要的聚合，同一非空形状的相关 SQL 计划与口径延续基线，样本不足以证实改写 CAST 或删减全局 count 的收益。

## 浏览器证据

内置浏览器打开真实 `127.0.0.1` 应用的启动器一次性会话，确认受保护 API 可用。合成数据根预置两个未扫描来源，每个 12 张文件，第三个来源通过真实表单登记。观察到：

- 两张空明细展开并显示「暂无可统计的照片」。登记 C 后 A、B 明细保留，服务端访问序列中登记前后 A、B 各仅一次详情 GET。折叠重开 A 使用现有明细。展开 C 只请求该来源。
- 从 C 的卡片启动建库，最终显示 12 个扫描文件与 12 项资产。语义通道缺失如实显示不可用。照片列表显示合成缩略图和加入时间，人物浏览 200。正常流程控制台错误列表为空。
- 400 像素视口下，页面可用宽度与滚动宽度同为 385 像素，三个展开区域各约 294 像素。观察卡片换行及控制大小，无页面级横向溢出。
- 第二次启动在合成服务端用受控等待与一次故障。浏览器确实显示 A「正在读取」、B「明细正在排队」。用户随后要求停止 Computer Use，实际 503 已由服务端记录，失败提示及点击重试只有前端交互测试覆盖，未在浏览器继续观察。无截图文件导出。两次合成服务均已停止。

## 回归与剩余验收

- 最终受影响 B/D 共 35 文件、400 passed / 0 failed / 0 skipped（138.735 s，退出 0，`affected-final.xml`）。覆盖 A0 七场景、池和 API 关闭、来源统计与建库、Memories、读/投影/搜索以及同一池的直接消费者。Ruff、compileall、Node 语法与 diff-check 通过。无全量回归触发。
- 新增 A1 后端 15/15，通过的场景包含 empty scope 组合、两 Worker 容量/队满/释放、真实本地 TCP disconnect、关闭时活动与排队明细、WAL 读快照跨扫描提交，以及语义、Face、OCR 的刷新阶段入点重叠。语义使用确定性替身，Face/OCR 覆盖真实刷新编排进入阶段并仍可读明细，未加载模型。
- 前端定向 3/3，覆盖请求单飞、旧响应、立刻重开、共享消费者、登记和删除、可重试失败及路径刷新。广义前端文件基线 88 passed / 13 failed，当前 91 passed / 11 failed。剩余 11 个失败在同一基线已复现，属于既有字样或 CSS 断言。两个路径测试修正了测试 DOM 缺少 `getElementById` 的替身，原并发及失效断言保留。未修改其他页面来迎合旧字样。
- 原始预试验、失败断言和各轮退出位于忽略的 `test-results/p4-catalog-concurrency-a1/`。早期探针发现空 OCR 分支缺 `total` 已补齐。关闭回归发现活动 SQL 中断走普通 500，现仅在该明细 admission 已关闭且错误为 SQLITE_INTERRUPT 时映射既有 `CATALOG_UNREADABLE`。
### 首轮独立 Terra High 审查

审核提交 `bfdba39f7c524a4454b33cceff25d027a830986b`。用户指定 Terra High 执行 V3.1 §2.4 完整受影响面独立审查。结论 **REVIEW FAIL，1 项 P2**。A1 保持 `VALIDATION PENDING`。

**P2 · 建库后共享资产所在的其他来源明细缓存仍有效。** 预先展开 B 的明细，A 和 B 均有绑定同一 asset_id 的 PRESENT 位置。仅对 A 建库或同步并改变该资产的语义或增强阶段时，`src/photo_search/web/app.js:1809` 仅失效本轮直接执行的来源。`reconcileSourceDetails`（1136–1146）以来源状态及最近成功扫描识别更改，B 的这些字段可能不变。B 会继续显示旧统计，操作入口也可能沿用旧状态。统计查询本身按共享 asset_id 读取全库任务和索引事实，数据库数据正确。

后续回归应预缓存 B 并验证 A 的处理改变共享资产后，B 能重新读取并显示最新统计及入口。还要保留无关登记时的缓存复用和旧响应保护。刷新返回的 `source_ids` 只覆盖直接执行范围，现有 `catalog_filter_revision` 也无法覆盖明细全部阶段，不可直接用这两个字段判定其它缓存仍然有效。

其它审核面包括 A0 锁序、Future 回收、排队取消与关闭、线程自有连接和数据根 lease、真实 HTTP 断开、WAL 快照、空范围统计与 DTO。未发现其它 P0/P1/P2 或未解决的 ADR、统计持久化和 lease 安全问题。

独立定向重跑的 `terra-review.xml` 显示 25 passed / 0 failed / 0 skipped，涵盖 A1 后端 15、前端 3、A0 7。命令退出码未单独记录。审核者复核了原 400 项 JUnit 和基线前端失败节点，本轮没有全量重跑。浏览器中的失败与重试仍未现场观察。未使用 Computer Use、模型或真实图库。

### P2 续修与独立复核（2026-11-27）

以 `bfdba39` 为基线。对真正共享资产的合成图库，只建库 A 后，B 的状态及最近成功扫描编号保持原值，语义明细从 `UNBUILT` 变为 `SEARCHABLE`，两张照片都已确认。前端用相同摘要和更新后的明细构造回归。原版在三种终态均未重新读取 B，3 failed，退出码 1（`p2/before.xml`）。

刷新终态按 `refresh_id` 去重，并清理已跟踪来源的结果、错误和请求。共享资产不受直接执行来源 ID 的限制。可见图库立即用现有摘要重绘，展开项按原单飞队列重新读取。摘要到达后，未变更的卡片复用在途读取，摘要字段变化时沿用定向失效。关闭的卡片下次展开才读取，登记仍保留有效缓存。前端资源版本更新为 `p4-catalog-concurrency-a1-2`。

首轮续修时，Terra High 发现摘要请求失败或旧响应因版本被丢弃时，旧明细仍留在页面。定向回归在仅清缓存的版本上复现两个故障，3 passed / 2 failed，退出码 1（`p2/summary-baseline.xml`）。立即重绘后，明细在摘要返回前就开始更新。最终定向 9 passed，退出码 0（`p2/target-final.xml`），包含五种终态与摘要组合、既有三个前端回归和共享资产 API 回归。测试同时验证放弃旧请求、迟到结果无效、重复终态去重，以及未展开项不请求。

本次受影响组合 161 passed / 11 failed / 0 skipped，退出码 1（`p2/final.xml`）。图库建库与来源 API、明细生命周期、A0 锁序共 65/65。前端 96/107，其 11 个失败节点与基线 `ui-final.xml` 完全相同。Ruff、Python compileall、Node 语法及 diff-check 均通过。原 400 项证据对应续修前的 A1，不能计为本轮整套重跑。

最终 Terra High 独立复核 **REVIEW PASS**。两项 P2 已关闭，无新增可行动问题。独立定向 9 passed / 0 skipped，退出码 0（`p2/terra-final.xml` 与同名 `.exit`）。上述原始材料位于 `test-results/p4-catalog-concurrency-a1/`。本轮验证未使用 Computer Use，未触碰真实图库或模型。A1 的真实浏览器失败与重试观察仍待完成，保持 `VALIDATION PENDING`。

### 复跑入口

使用 Python 3.12.10、项目内 uv cache 和各自 basetemp。模型资源保持空且依赖离线。

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location) '.uv-cache')
$evidence = 'test-results/p4-catalog-concurrency-a1'
$nodes = @(Get-Content -LiteralPath "$evidence/affected-files.txt")
uv run --offline --no-sync python -m pytest -q @nodes --basetemp=.pytest-tmp/a1-review --junitxml="$evidence/review.xml" -o junit_family=xunit1
uv run --offline --no-sync python -m pytest -q tests/unit/test_api_frontend.py -k 'source_detail_requests or source_paths_refresh' --basetemp=.pytest-tmp/a1-ui-review --junitxml="$evidence/ui-review.xml"
uv run --offline --no-sync python -m pytest -q tests/unit/test_api_frontend.py tests/integration/test_p4_02y_i_source_build.py -k 'scoped_refresh_invalidates or building_only_the_later_source or source_detail_requests_keep_current or source_paths_refresh' --basetemp=.pytest-tmp/a1-p2-review --junitxml="$evidence/p2/review.xml"
uv run --offline --no-sync ruff check .
.venv\Scripts\python.exe -m compileall -q src/photo_search tests
node --check src/photo_search/web/app.js
git diff --check
```
