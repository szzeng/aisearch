# P4-UI2-SHELL-STICKY-A — 实施报告

日期：2026-10-15（Asia/Shanghai）
基线：`b2a6d0e95835270dda7d83fc6053dab38cc817be`；初始工作树干净。

## 结果

五个公共页面已改为同一工作区结构。搜索页宽度与留白样式已同步适配 wrapper；五页修改过的公共/时间轴/地图 CSS 引用均已 cache-bust。普通页面继续由浏览器窗口滚动；左侧导航以 viewport 高度吸顶，短窗口下导航区可独立滚动；页头搜索吸顶且使用不透明背景。窄屏 680px 及以下保留原有紧凑导航并随正文滚动，页头搜索吸顶。时间轴组头读取页头实际高度后吸附在其下方。地图页面在工作区内继续使用固定视口网格，地图画布与照片列表的独立布局规则保留。

代码检查定位到旧外壳的结构风险：`body.app-shell` 设为固定 `100vh`，侧栏跨越各个 Grid 行，页头单独占一行且未定位；页头缺少可供 sticky 延伸的完整正文包含块。新 `.app-workspace` 让页头处于贯穿内容高度的包含块中，同时保留 window scroll。该根因归因来自结构检查；按用户要求没有运行浏览器，滚动现象是否已在产品中消除尚未现场确认。

## 改动

- `styles.css`：普通页面采用 `sidebar + workspace` 两列和内容高度行；侧栏/搜索页头吸顶；窄屏导航随文档滚动；地图工作区继续三行视口布局。
- `index.html`、`search.html`、`persons.html`、`places.html`、`map.html`：保留原有 ID/form，将通知、页头、页内导航和 `main` 收入 workspace；加载高度 helper，按实际引用更新 cache-bust。
- `shell-sticky.js`：用单个 `ResizeObserver` 更新页头高度变量；不监听滚动、不改变分页/恢复逻辑。
- `photo-timeline.css`：日期组头和定位边距使用共享页头高度变量。
- `map.css`：跟随 wrapper 更新空通知选择器。
- `tests/unit/test_p4_ui_shell.py`：补充五页 wrapper、公共滚动结构、窄屏、地图布局和 helper 的定向断言；将原本过期的地图 JS/CSS 版本断言同步到当前代码引用，并断言搜索页 wrapper 专属样式仍生效。

## 验证

- `uv run --no-sync pytest -q tests/unit/test_p4_ui_shell.py --basetemp=.pytest-tmp/P4-UI2-SHELL-STICKY-A --junitxml=test-results/P4-UI2-SHELL-STICKY-A.xml`：4 passed / 0 failed / 0 errors / 0 skipped；JUnit：`test-results/P4-UI2-SHELL-STICKY-A.xml`。
- `node --check src/photo_search/web/shell-sticky.js`：通过。
- `git diff --check`：通过。
- 独立复核：GPT-6 Luna / high 子代理首次指出搜索页选择器和 CSS cache-bust 两项 finding，修复后 repair-only 复看均已关闭，无新增 finding；这是限域复核，不是 DS-H。

## 未验证与验收边界

用户明确要求不做浏览器验证，记录 `BROWSER_NOT_RUN_USER_REQUEST`。实际长页滚动、续页和 viewer 返回、年月定位、范围搜索返回、地图 850px 切换、680px/720px/640px/短窗口、真实 200% 缩放、键盘焦点和通知/模态层级均未由产品浏览器验证；截图未生成。窄屏 CSS 视口不能代替原生 200% 缩放证据。未访问真实图库，未运行全仓、性能、模型/GPU、打包或发布验证。

因此：`IMPLEMENTED / FUNCTIONAL SCOPED PASS`（JUnit/静态范围），整体 `ACCEPTANCE PARTIAL / BROWSER_NOT_RUN_USER_REQUEST`。不标整卡 PASS。
