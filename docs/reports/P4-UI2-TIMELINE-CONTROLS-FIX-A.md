# P4-UI2-TIMELINE-CONTROLS-FIX-A 实施报告

日期：2026-10-14

## 结果

时间轴模式默认状态下，列表专属排序、拍摄日期范围、刷新及日期说明不再显示或占位；列表模式仍由既有 `showBrowseMode()` 切换逻辑恢复这些控件，图库筛选与时间轴自身控件保持原有位置和逻辑。

根因是 `photo-timeline.css` 对 `#browse-list-filters` 设置 `display:flex`，覆盖了 HTML `hidden` 的默认隐藏样式。日期说明原先只隐藏内部 `span`，外层段落仍占布局空间。

## 修改

- `src/photo_search/web/photo-timeline.css`：对带 `[hidden]` 的列表筛选容器显式 `display:none`。
- `src/photo_search/web/index.html`：列表筛选容器和日期说明段落初始设为 `hidden`；将说明的既有 JS 控制 ID 移到段落；更新时间轴 CSS 缓存版本。
- `tests/unit/test_p4_ui_photos.py`：补充初始隐藏、CSS 优先级和缓存版本契约断言。

没有改动 JS、请求逻辑、过滤状态或 Timeline 数据语义。

## 验证

- 定向测试：`uv run --locked pytest -q tests/unit/test_p4_ui_photos.py --junitxml=docs/reports/artifacts/P4-UI2-TIMELINE-CONTROLS-FIX-A-junit.xml` — **8 passed, 0 failures/errors/skipped**。
- `git diff --check` — 通过。
- 浏览器交互与视口检查：**按用户要求跳过**，因此 `BROWSER_PENDING`；初次显示、列表切换/恢复、键盘焦点及桌面/窄屏表现尚无本轮产品浏览器证据。
- 未执行全仓回归、规模压测、真实图库、模型/GPU或打包验证。

整卡为实现及定向测试完成、浏览器验收待验；不标记整卡 PASS。
