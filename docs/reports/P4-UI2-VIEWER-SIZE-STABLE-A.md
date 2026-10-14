# P4-UI2-VIEWER-SIZE-STABLE-A 实施与验证报告

日期：2026-10-14
结论：**FUNCTIONAL SCOPED PASS / ACCEPTANCE PARTIAL**。完成共享照片查看器的视口驱动固定外框和内部约束布局；用户明确要求不做浏览器测试，因此不声称几何或交互通过。

## 问题与根因

任务卡给出的源码基线只有 `max-height`，dialog 高度仍由内容决定；dialog 本身可滚动，figure/stage 含多个依赖内容的最小高度，信息面板也只设最大高度。由此图片尺寸/信息内容可能经 grid 的 intrinsic sizing 影响外框。**这属于源码推断**：按用户要求未启动产品浏览器，未记录修复前 rect，也未实测原问题变化的坐标/宽高。

## 修改

- `src/photo_search/web/photo-info.css`：外框使用 `width:min(94vw,1500px,calc(100vw - 16px))`、`height:min(92dvh,calc(100dvh - 16px))`、`box-sizing:border-box`、关闭内容溢出；仅 `dialog[open]` 使用 grid，关闭态仍由原生 dialog 隐藏。
- dialog 内部改为标题/内容/可选扩展区三行；中部 grid、figure、stage 使用 `minmax(0,1fr)` 与 `min-width/min-height:0`，图片信息栏和扩展内容各自在内部滚动。空扩展区不占空间。窄屏保持固定视口高度并改成单列布局，移除累加的舞台最小高度。
- 五个入口的 `photo-info.css` URL 更新为 `p4-ui2-viewer-size-stable-a-1`。JS 缩放、路由、历史与人物写操作均未修改。
- `tests/unit/test_photo_view_info_ui.py`：同步 CSS cache-bust 断言；并将人物 albums 脚本断言从已过期的 `person-batch-ux-fix-a-5` 更新到页面当前实际引用的 `scroll-load-b-3`。

## 验证

命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-UI2-VIEWER-SIZE-STABLE-A --junitxml=test-results/P4-UI2-VIEWER-SIZE-STABLE-A.xml
git diff --check
```

结果：JUnit **4 tests / 0 failures / 0 errors / 0 skipped**，XML：`test-results/P4-UI2-VIEWER-SIZE-STABLE-A.xml`；`git diff --check` 通过。首跑仅因上述旧脚本版本断言失败；更新断言后重跑全绿。

## S01–S06 状态

| ID | 状态 | 证据 |
|---|---|---|
| S01 | `PENDING_USER_REQUEST` | 未运行真实浏览器；无缩放前后 dialog/stage rect 数值。 |
| S02 | `PENDING_USER_REQUEST` | 未在产品中切换横/竖/长图。 |
| S03 | `PENDING_USER_REQUEST` | 未在产品中展开长路径、GPS、人物或扩展区。 |
| S04 | `PENDING_USER_REQUEST` | 未调整产品浏览器窗口或验证 zoom 保留。 |
| S05 | `PENDING_USER_REQUEST` | 未在产品浏览器实测 470×764 / 720×450。 |
| S06 | `PENDING_USER_REQUEST` | 未在产品中操作 Esc、关闭、搜索、人物或地图路径。 |

未创建截图；没有真实几何测量数据。未访问真实图库、模型/GPU，未跑全仓、性能、打包或发布验证。

## 独立状态

- `LAYOUT_CODE=IMPLEMENTED`：仅有源码约束与定向回归；产品布局结果尚未测量。
- `GEOMETRY_BROWSER=PENDING_USER_REQUEST`
- `ZOOM_INTERACTION=PENDING_USER_REQUEST`
- `NARROW_VIEWPORT=PENDING_USER_REQUEST`
- `REGRESSION=SCOPED_JUNIT_PASS (4/0/0/0)`

整卡保持 **ACCEPTANCE PARTIAL**。若之后继续验收，先按任务卡在隔离合成数据中测量 S01，再按需要测 S05 和 S06；不得把当前代码/JUnit 证据当作像素几何证明。
