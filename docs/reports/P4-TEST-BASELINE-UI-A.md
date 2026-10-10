# P4-TEST-BASELINE-UI-A

日期：2026-10-11。状态：**SCOPED PASS / 静态与合成 DOM 定向检查通过**。

## 结果

- 静态 UI 断言对齐当前文案和路由：人物搜索选择器读取 `/persons/browse` 并按 `photos_desc` 排序；撤销说明文案已调整；备份预览检查保留 `snapshot_json_bytes` 汇总字段，不再把合法字节计数误判为私密快照内容。
- 人物页仅允许写入固定 key `aisearch.persons.changed` 的跨标签页刷新标记，并禁止将 CSRF/token 写入 localStorage；保留 cookie/header、同源凭据、无 `innerHTML`、无外部引用、路径脱敏等安全断言。`test_state_changing_requests_need_origin_and_csrf` 同时通过。
- 人物列表性能节点原来硬编码 4 条 SQL；当前受测路径还批量读取合集版本与本页合集标签，共 8 条固定查询。测试按现状核验 8 条、对 40 人整页仍逐人标量投影校验，并核对合集标签为空；没有调整生产 SQL 或计数。
- 合集交互用例的轻量 DOM 桩补齐浏览器原生 `append` 节点移动、`replaceChild` 和 `remove` 语义，并让分页响应由 cursor 决定。此前初始卡片为空是测试桩没有这些原生操作；现有真实 JS 测试执行通过。人物脚本 query 版本断言更新到当前合集计数版本。
- 未改 `src/`、CSS 或业务查询。没有在真实浏览器/图库做产品验收。

## 验证

- 最终定向 JUnit：`test-results/P4-TEST-BASELINE-UI-A.xml`，**9 passed / 0 failed / 0 errors / 0 skipped**。包含前端静态安全/API/词汇、搜索人物分页选择、人物列表性能投影、120 人合集 DOM 交互、CSRF、合集照片数 UI、人物分页/头像刷新 DOM。
- Ruff、修改 Python 文件 `py_compile`、`node --check src/photo_search/web/persons-albums.js`、`git diff --check` 通过。
- 未跑全浏览器矩阵、30 万人物/照片性能或全仓回归；没有访问真实 PhotoSearchData/照片库。

## 后续

继续总控阶段 4 CORE-A。历史全量报告剩余静态/DOM/性能节点没有逐一复跑，不由本阶段结果推断全量 UI 门禁状态。
