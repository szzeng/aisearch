# P4-UI2-PHOTO-DETAIL-ENTRY-A 执行报告

日期：2026-10-15
结论：**FUNCTIONAL SCOPED PASS / BROWSER_PENDING / ACCEPTANCE PARTIAL**。用户要求不做浏览器验证。

## 实现

- 共享照片查看器增加「完整详情」按钮，读取 `setAsset()` 后当前资产 ID；服从 `beforeClose`，防止重复跳转。跳转前解除当前 viewer 的 `popstate` 监听并关闭弹窗，不再留下幽灵模态框或关闭时多退一次历史。
- 增加固定 `/#/asset/{id}/details` 路由，继续使用已有详情 renderer。详情入口捕获来源页面最小回跳意图，并在路由入口一次性消费；只接受允许的同源页面路径和对应资产。消费还要求当前文档导航标记或匹配的同源来源页，拒绝残留的同资产旧意图。旧 `/#/asset/{id}` 的 `history:false` viewer 会强制设置应用内安全 Back 前项。
- 「返回照片查看」恢复同资产兼容 viewer；有效来源意图可在查看器关闭后返回来源。首页保留已有 browse 锚点快照；搜索、人物、地点和地图接入各自的明确入口 adapter。人物写入期间继续由 `beforeClose` 拦截。
- 更新五个照片页面的共享 viewer 缓存版本，以及受影响的入口脚本缓存参数。

## 定向验证

| 范围 | 结果 |
| --- | --- |
| 共享 viewer、实时资产 ID、忙碌拦截、重复点击、弹窗清理 | synthetic DOM 通过 |
| 详情路由、无来源 Back 安全项、旧 `history:false` 路径、intent 一次性消费与 stale intent 拒绝 | 合成路由断言通过 |
| JUnit | `tests/unit/test_photo_view_info_ui.py`: **4 passed / 0 failed / 0 errors / 0 skipped**；XML：`test-results/P4-UI2-PHOTO-DETAIL-ENTRY-A.xml` |
| `node --check` | `photo-info.js`、`app.js`、`search.js`、`persons-albums.js`、`places-albums.js`、`map.js` 通过 |
| Ruff | `tests/unit/test_photo_view_info_ui.py` 通过 |
| `git diff --check` | 通过 |

Luna High repair-only 复核指出了直接深链 Back 前项缺口及启动 capabilities 可能清掉来源失效提示的时序问题；两项均已修复。最终 repair-only 复核未发现新的 actionable finding；复核只检查源码与合成测试，未运行浏览器。

## D01–D07 与边界

- D01：合成验证当前资产 ID 和 viewer 释放；真实页面链路未验证。
- D02：合成验证一次性返回意图和已有首页 browse 恢复测试；浏览器闭环未验证。
- D03：合成覆盖直达详情安全返回项和旧 `history:false` 意图；浏览器 Back/Forward 未验证。
- D04–D05：搜索、人物、合集、地点、地图入口均已接 adapter；各页面的查询/成员/revision/bbox 恢复只由既有模块负责，未做浏览器现场复核，保持待验。
- D06：合成覆盖忙碌拦截、重复触发、同源/路径校验、一次性与 stale intent，并验证存储拒绝会明确提示和安全退化，启动 capabilities 成功也不会清除此提示；API 错误的完整页面操作未做浏览器验证。
- D07：按钮语义与窄屏 CSS 已实现；键盘焦点、暗色和实际窄屏视觉未验证。

| 验收项 | 状态 |
| --- | --- |
| DETAIL_ROUTE | FUNCTIONAL SCOPED PASS |
| VIEWER_ENTRY | FUNCTIONAL SCOPED PASS |
| RETURN_AND_HISTORY | FUNCTIONAL SCOPED PASS（synthetic）；浏览器行为待验 |
| SCOPED_CONTEXT | ADAPTERS IMPLEMENTED / BROWSER_PENDING |
| BROWSER | BROWSER_NOT_RUN_USER_REQUEST |
| REGRESSION | 定向 JUnit 与语法检查通过；未运行全套 |
| REVIEW | Luna High repair-only 复核；非 DS-H |

未访问真实 `PhotoSearchData`，未运行全仓、性能、真实浏览器、GPU/模型、打包或发布验证。这些仍按各自任务卡保持未验。
