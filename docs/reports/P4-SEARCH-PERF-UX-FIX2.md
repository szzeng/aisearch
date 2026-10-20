# P4-SEARCH-PERF-UX-FIX2 修复报告

**日期：** 2026-10-20
**基线：** `41eec95`
**状态：** IMPLEMENTED / ACCEPTANCE PARTIAL
**范围：** 搜索性能诊断身份读取、标准测试条件/样本隔离、阶段统计。按用户要求本地提交，不推送。

## 根因

1. 搜索页只调用轻量 `/capabilities/page`，其中没有活动 Profile/Generation；诊断却从该响应读取身份。完整 `/capabilities` 会解析 query encoder，不能作为身份读取方案。
2. 自动测试将用户控制条件与服务端解析出来的 `intentPlan`、地点状态、范围状态混在同一快照中，首次请求更新内部解析结果后可能误判为用户改条件。
3. 后端耗时存放在 `sample.server`，百分位统计读取顶层字段，导致查询编码与向量检索时间显示未测。

## 修改与设计

- 新增 `GET /api/v1/search-perf/identity`。该端点只读查询活动 Profile 指针、被指向的 Profile 和该 Profile 明确绑定的 Generation，返回提供方、处理器、Space、维度、状态和绑定一致性。没有按 `is_active` 另选 Generation。
- 前端在打开诊断后显示“正在确认当前模型…”，异步读取身份；失败显示原因与重试入口。点击开始时再核对一次身份。请求序号阻止关闭诊断后的迟到响应覆盖状态；不调用 query encoder 或完整 capabilities。
- 自动测试冻结用户输入/手工筛选/用户范围；服务端解析语义单独固定，后续发生变化则停止。身份变化和需人工选择的范围继续阻断测试。每轮只计本轮新增且 Profile、Generation、Provider、Processor、Space、Dimension 均匹配的有效 hot 样本；摘要、导出指标和完成计数共用当前轮样本切片。
- 编码和向量百分位读取 `sample.server`，只接受有限数值；缺失指标显示“未测”及有效 `n`。面板与 JSON 导出复用统计函数，保留 JSON v1 结构。后端搜索耗时、浏览器请求响应、首图解码就绪、视口就绪保持分项。

## API 与模型加载边界

**新增 API：** 是，`GET /api/v1/search-perf/identity`。沿用路由的现有会话认证。
**身份读取是否额外加载 AI 模型：** 不会。实现只用 SQLite readonly 查询；定向 API 测试将 `context.query_encoder` 替换为立即失败的桩，接口仍成功返回四种模型身份/绑定结果及无活动 Profile 原因。此为代码路径与隔离测试证据，未对实际 Worker 做启动/资源观测。

## 定向验证

- `tests/unit/test_search_perf_diagnostics.py` 与 `tests/unit/test_search_text_mode_ui.py`：**21 passed，0 failed/errors/skipped**。覆盖只读身份接口（Qwen、Chinese-CLIP 与各自 EXIF v2）、Profile 精确 Generation 绑定、无活动模型、统计取值/缺失值、条件快照、迟到身份响应及本轮 hot 样本隔离。JUnit：`test-results/P4-SEARCH-PERF-UX-FIX2-focused.xml`。
- `tests/unit/test_api_frontend.py -k search_page_validates_authority`：**1 passed**。JUnit：`test-results/P4-SEARCH-PERF-UX-FIX2-frontend.xml`。
- Ruff、`py_compile`、`node --check src/photo_search/web/search.js`、`git diff --check`：通过。补充轮次隔离断言时，首次将两样本 nearest-rank P50 错误期望为最大值，导致该次测试退出 1；修正断言期望为第一个排序值后，最终组合 JUnit 全部通过。
- Luna H 独立复核发现同身份的旧轮样本会混入摘要/导出 P50/P95，已增加当前轮样本边界并以 Node 回归断言覆盖；跟进复核确认 finding 已关闭、未发现新增问题。Luna H 未运行测试或浏览器。

## 尚待用户验收

没有在本次隔离环境中加载真实 Qwen/Chinese-CLIP Worker，也没有运行真实图库上的 1+2+20 轮测速。因此真实请求是否可完成、真实模型阶段计时、首图与视口图片就绪统计仍待用户在 PhotoSearch 中验收。未访问真实照片库，未重建图库，未运行全量 pytest 或大规模性能认证。
