# P4-SEARCH-PERF-UX-FIX — 执行报告

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
范围：诊断界面、逐请求计时开关、样本统计与导出；未修改搜索算法、阈值、排序或索引。

## 结果

搜索页现在提供当前标签页内的诊断开关和标准测试按钮。诊断默认关闭；UI 显式启用时仅为同源认证搜索请求增加 `X-Search-Perf: 1`，服务端不再要求 `PHOTO_SEARCH_SEARCH_PERF=1`。测试固定查询和筛选，串行尝试 1 次首次观察、2 次预热、20 次热搜索；在查询条件改变、模型绑定错配、人工选择歧义、用户停止、离页或等待超时时停止。

修正了 `first_page_hits` 的计数位置、capabilities Profile/Generation 字段、空视口误判、缺失计时 `Number(null)`、热样本混用、计时口径与缩略图迟到回调污染。能确认的 Worker 冷启动才可标记；当前实现不从 encoder 获取之后的状态推断冷启动，标为 `not_measured`。诊断导出不包含原始搜索词、路径、Cookie 或 Token。

标准测试结束只有本轮产生 20 个有效热样本才报告完成；重复运行时的完成计数从本轮起始样本索引计算，旧轮成功样本不会补足本轮失败样本。未达标时显示有效数量，保留失败样本且不重试。

| 场景 | 数据与设备 | 结果/预算结论 | 未验边界 |
|---|---|---|---|
| 定向搜索诊断与文字搜索 UI | 隔离 pytest fixtures，Python 3.12.10 / Windows | 最终 JUnit 13 passed；Ruff、py_compile、Node `--check`、diff-check 通过 | 未执行真实模型/图片 23 轮 |
| 相关静态前端断言 | `tests/unit/test_api_frontend.py` | 初始 7 passed、1 failed；将函数边界断言更新为支持现有可选参数后，最终相关选集 8 passed | 初始 JUnit 保留；变更仅修正脆弱签名匹配，不降低行为检查 |
| UI 入口 | 内置浏览器 + 项目内隔离空 catalog | 默认关闭；开关立即显示面板；无活动 Profile/Generation 时标准测试明确拒绝启动 | 空 catalog 无法验证模型身份四组合的真实查询、图像加载和标准测试完成路径 |

最终定向 JUnit：`test-results/P4-SEARCH-PERF-UX-FIX-final.xml`。相关前端初次与修正断言后的 JUnit 分别为 `test-results/P4-SEARCH-PERF-UX-FIX-frontend-final.xml` 和 `test-results/P4-SEARCH-PERF-UX-FIX-frontend-final-rerun.xml`。首次 text UI harness 运行因 mock 缺少现有 browser `window.addEventListener` 失败；补齐 mock 后最终 JUnit 全过。隔离浏览器服务已停止。

## 性能设计与停止点

PERF-BOUNDED：每轮复用一次现有搜索请求和当前视口懒加载；不增加常驻任务、额外模型探测、额外数据库扫描或视口外缩略图请求。测试只有用户点击才开始，最多 23 个串行请求。没有真实模型/图库数据，不得将合成空库响应描述为搜索性能结论。

## 复核与后续验收

Luna High 独立复核指出并已关闭两项 P2：有失败样本时不得无条件显示完成；跨轮运行须将有效样本计数限制在本轮。复核确认修改已关闭发现且未发现新问题，未运行浏览器。任务要求中的真实模型一键 23 轮、缩略图成功/失败/超时/空视口组合、重复运行真实浏览器验证、模型切换中止及真实图库最终测量目前 **PENDING**；因此整体保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。真实图库测量由用户在本人机器按任务卡执行。本任务未运行全量 pytest、30 万图库测试或访问 `PhotoSearchData`。
