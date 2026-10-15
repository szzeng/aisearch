# P4-UI2-MAP-PHOTO-HOVER-A 实施报告

日期：2026-10-15

## 结果

地图照片卡片和 MapLibre 聚合叶子成员现在共用一个懒创建的定位 Marker。鼠标进入照片卡会按资产自身 GPS 更新位置；卡内移动不闪烁，离开后隐藏。Tab 键聚焦可显示同等提示，鼠标来源优先于键盘焦点；触屏交互、列表刷新/替换、地图移动、cell/cluster 切换、查看器打开及地图样式错误回退都会清理激活态。Marker 使用 `pointer-events: none`，不移动地图，也没有悬停网络请求。

聚合叶子继续使用 MapLibre feature 的 `[longitude, latitude]`；相同坐标保持重合。缺失或越界坐标不显示标记，`(0,0)` 和负经纬度有效。照片点击仍调用原 `openAsset`，地图点和聚合数字原处理逻辑未重构。

## 修改

- `src/photo_search/web/map.js`：唯一 Marker、列表级事件委托、GPS 校验及状态清理；地图错误回退分支也清除 hover。
- `src/photo_search/web/map.css`：红色中心点、白色描边和柔和外环；当前卡使用不改变尺寸的 outline。
- `src/photo_search/web/map.html`：仅更新 map.css/map.js 的缓存参数。
- `tests/unit/fixtures/p4_ui2_map_photo_hover_probe.js`、`tests/unit/test_p4_ui2_map_photo_hover.py`：执行真实前端函数与事件处理的确定性 probe。

工作区在本任务开始前已有其他未提交改动。本报告和提交仅包含本任务内容；`map.js` 中已有的查看完整详情回调、`map.html` 中已有的 photo-info 资源缓存更新均保留为未暂存改动。

## 验证

- `uv run --no-sync pytest -q -p no:cacheprovider tests/unit/test_p4_place_map_review_ui.py tests/unit/test_p4_ui2_map_photo_hover.py --junitxml=test-results/P4-UI2-MAP-PHOTO-HOVER-A.xml`：通过；JUnit `tests=10 failures=0 errors=0 skipped=0`。
- `node --check src/photo_search/web/map.js`、`node --check tests/unit/fixtures/p4_ui2_map_photo_hover_probe.js`：通过。
- `git diff --check`：通过。
- 行为 probe 覆盖精确 GPS、零/负/非法坐标、卡内移动、A→B→离开、键盘/鼠标优先级、触屏清理、cluster leaf 坐标与同坐标成员点击、查看器/地图错误清理、单 Marker/单次事件绑定、无 hover 请求及地图中心/缩放不变。既有 map review probe 的点击、成员打开和乱序响应回归仍通过。
- LUNA High 独立限域复核首轮发现两个清理缺口，均已修复；repair-only 复核关闭两项且无新 actionable finding（不是 DS-H）。
- 产品浏览器验证：`BROWSER_NOT_RUN_USER_REQUEST`。整卡为实现与定向自动验证完成、浏览器验收待验；不标记 PASS。未访问真实图库，未运行全仓、性能、模型/GPU、打包或发布验证。

实现会话实际模型/推理档未由运行环境提供可核验元数据；未声称切换模型。独立复核使用 LUNA High。
