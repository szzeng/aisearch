# P4-UI2-MAP-MINOR-FIX-A 实施报告

日期：2026-10-19

## 结果

地图相册悬停 Marker 恢复 `position:absolute`，设为局部 `z-index:2`，保留红色中心、白色描边、外环与 `pointer-events:none`。照片查看操作按钮改用 `.map-photo-action`，宽度、padding、背景和 hover/focus 样式限定到照片操作按钮；地图不再以 `.tile button` 覆盖共享 `.favorite-star`。收藏按钮仍由 `PhotoFavorites.button()` 生成。沿用既有单 Marker 和事件委托，不增地图 source/layer、请求、SQL 或逐照片监听。

## 修改文件

- `src/photo_search/web/map.css`：修复 Marker 定位与局部层叠；将卡片按钮样式限定为 `.map-photo-action` / `.map-photo-open`。
- `src/photo_search/web/map.js`：查看照片文字按钮增加专用 class。
- `src/photo_search/web/map.html`：同步 map.css/map.js 资源缓存版本。
- `tests/unit/test_p4_ui2_map_photo_hover.py`：增加 CSS、共享收藏调用及缓存版本断言。
- `docs/tasks/P4-UI2-MAP-MINOR-FIX-A.md`、`docs/development-status.md`：任务记录。

## 验证

- `uv run --offline pytest -q tests/unit/test_p4_ui2_map_photo_hover.py tests/unit/test_p4_favorites_ux_b_ui.py --junitxml=test-results/P4-UI2-MAP-MINOR-FIX-A.xml --basetemp=.pytest-tmp/P4-UI2-MAP-MINOR-FIX-A`：退出码 0；JUnit 8 tests、0 failures、0 errors、0 skipped。
- `node --check src/photo_search/web/map.js`：通过。
- `git diff --check`：通过。
- 现有行为 probe 覆盖 GPS 坐标、单 Marker 复用、键盘/鼠标状态、聚合叶子、查看器清理和无悬停请求；新增断言检查定位/层级/指针穿透、照片操作按钮样式限定、共享收藏组件调用与资源缓存版本。

## 浏览器与未验边界

浏览器交互、真实视觉层级、地图点点击、收藏/取消收藏和窄屏/键盘焦点路线 **PENDING**。已在既有隔离合成图库启动 loopback 服务，但当前 Codex 内置浏览器没有标签；仓库要求先用启动器新生成的一次性会话链接建立认证。当前 CUA 交互无法在不把凭证放进可见工具调用的情况下打开该链接，因此未绕过会话保护。未访问真实图库或真实 Key。自动测试不替代浏览器观察，所以本任务状态为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
