# P4-HOME-UX-A — 首页与照片预览调查

日期：2026-10-02。基线：`fe46753`，调查开始工作区干净。状态：**代码调查与任务卡完成；产品修复未实施，运行时耗时未测量**。

用户本轮要求：汇总之前的首页加载慢、图库来源不清楚、使用不方便，以及新增的照片详情预览太小问题，调查并生成一张执行卡。没有要求本轮立即实现。

## 1. 调查结论与证据

| 问题 | 当前代码证据 | 结论边界 |
|---|---|---|
| 首次首页慢 | `web/app.js::boot → handleHash → showView("sources")`，随后 `loadSources()` 发起 `GET /api/v1/sources`；请求返回前只有加载文案，返回后一次渲染所有来源 | 默认首页等待的是完整来源列表，并非自动加载所有照片 |
| 来源列表携带重统计 | `api/routes.py::list_sources` 逐个调用 `CatalogService.get_source` 与 `_with_progress`；前者走 `catalog/store.py::catalog_counts`，后者走 `refresh/progress.py::source_progress` | 每个来源重复计算计数、扫描、语义 backlog 和多阶段任务状态，是需要计时的主要候选瓶颈 |
| 统计有重复成本 | `catalog_counts` 即使指定来源仍先计算全库资产总数，随后执行多条来源计数查询；`source_progress` 又计算 scoped assets 和各阶段 facts/jobs | 调用次数随来源数增长；尚无 SQL trace/EXPLAIN/耗时证据，不能宣称已证明哪条 SQL 最慢 |
| 同步查询可能阻塞其他请求 | `list_sources` 是 async 路由，但上述查询在该调用链同步执行 | 需测同时间 capabilities/资产列表的等待；不能未经连接线程约束核对直接搬到线程池 |
| 来源不清楚 | `renderSources` 只用 `display_name` 作标题，主体堆叠来源编号、计数和分析状态；`renderSourceOptions` 仅显示名称 | 缺少易辨识来源说明和“查看此图库”的主入口，重名时不易区分 |
| 照片列表缺少来源名 | `buildTile` 显示相对路径、资产编号、加入时间和位置数；列表 `current_location` 有 `source_id/relative_path`，详情 locations 有 `source_display_name` | 可用轻量来源映射补充名称，无需每张照片额外请求详情；多位置不能被解释成只有一个来源 |
| 详情预览太小 | `drawDetail` 创建 `detail-figure > div.thumb-frame > img`；`styles.css::.thumb-frame img` 有 `max-height: 12rem`；`.detail-figure img` 仅设置 `max-width: min(100%, 48rem)`，没有解除高度限制 | CSS 规则直接解释详情仍按缩略图高度缩小；需实际浏览器确认计算样式与显示尺寸 |
| 搜索进入详情也受影响 | `search.js::buildHit` 的详情链接指向 `/#/asset/{id}` | 同一详情修复应覆盖首页浏览与普通搜索入口 |
| 地点相册另有预览 | `places-albums.js::showPreview` 使用 dialog；`places.css` 为 `max-height:80vh`、`max-width:86vw` | 不受上述 12rem 规则直接约束；需抽查入口一致性，不预设必须重写此弹窗 |

首页来源进度上下文中的 `served_binding → semantic_spec → pinned_embedding_spec` 读取 manifest/派生 spec；当前证据不支持“首页加载了整个 Qwen 模型”这一结论。缩略图浏览已是每页50张并按可见区域加载，不能把再加一次分页当作已解决首页来源读取慢。

## 2. 来源展示的既有边界

`tests/unit/test_api_frontend.py::test_frontend_never_renders_an_absolute_local_path` 明确要求只展示名称和来源相对路径，不回显 `source.root_path`；P1-05 实现报告也记载这个边界。API 有 root_path 字段不等于可以直接在页面回显。

此前聊天直接建议默认显示完整路径，没有核对这一条现有前端契约，现予纠正。执行卡要求先用来源显示名、可用来源说明和相对路径解决来源辨识；如仍需完整根目录查看，先给出最小展示契约和测试变更理由，不允许静默删掉路径守卫或把错误中的绝对路径开放。

## 3. 建议的唯一闭环

主瓶颈：默认首页把来源基本信息与完整进度统计绑在一个返回里。优先增加受会话保护的轻量来源读取，先使图库入口可用；详细统计只在用户展开某个图库或进入管理时加载。保持原完整来源接口及五态、search_ready 口径。

同一使用闭环内补来源名称、点击图库进入该来源照片、详情按视口适配、返回保留当前筛选和位置。暂不做完整首页重设计、推荐/最近相册、模型优化、向量算法、源目录重映射或发行打包。

执行卡：[P4-HOME-UX-A](../tasks/P4-HOME-UX-A.md)。先留存固定合成夹具的基线和原始计时，再做最小修复，报告前后同口径结果。

## 4. 本轮验证与未验证

- 已读取实际 Git、当前任务/实现报告、直接相关 UI/API/统计/媒体入口、主架构 §5.3/5.4、§10、§12.2 与执行策略相关条款。
- 未启动真实应用，未访问真实图库或真实数据根，未运行模型、扫描、pytest、浏览器或性能实验。
- 产品是否实际由上述统计拖慢、冷启动开销占比、离线设备影响、浏览器最终计算尺寸，均待执行卡实测。
- 本轮为纯说明文档，按 V3.1 §3.2 做文件链接检查与 `git diff --check`，不沿用此前通过的测试作为本次产品验证。
- 文档检查结果：任务卡/调查报告/status的Markdown文档链接均存在；`git diff --check`退出0；新增文档另查尾随空白与冲突标记。未提交或push。

## 5. 个人使用验收简化（2026-10-02）

用户明确要求个人项目测试尽量简单，任务卡已改为本任务范围的特例：2～3个来源、约50～100张合成照片，相关3个测试文件并输出JUnit，一次实际浏览器操作，前后各3次简单计时。删除B级全量、30万catalog/P95矩阵、强制性能JSON及独立审查/审查卡要求；保留首页不等待完整统计、来源辨识、横竖图适配/返回位置和必要的会话/回退检查。四项修复目标不变，其他任务审查和全局容量约定不变。

本轮只改任务卡、调查交接和status，未运行产品测试。工作区已有产品/测试及benchmark编辑，本轮未修改这些文件；不从它们存在推断实现或验证完成。不再要求扩展已有benchmark工具。
