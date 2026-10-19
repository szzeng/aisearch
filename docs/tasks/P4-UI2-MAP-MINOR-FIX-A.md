# P4-UI2-MAP-MINOR-FIX-A — 地图相册悬停标记与收藏按钮修复

- **状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；自动验证通过，浏览器验收 PENDING。
- **优先级/类型**：P2 / UI Bug 修复。
- **基线**：2026-10-19 当前工作树；只处理 `/map`。当前仓库未有本任务卡，激活依据为用户在本次任务中提供的完整需求与验收表。
- **关联实现/验证**：`src/photo_search/web/map.css`、`map.js`、`map.html`；`tests/unit/test_p4_ui2_map_photo_hover.py`；共享收藏样式仅供确认，不修改。

## 目标与边界

1. 单个复用 MapLibre Marker 的悬停红点保持坐标、中心、白描边和外环不变；恢复 Marker 的 `position:absolute`，以局部层级覆盖普通点与聚合数字，保留 `pointer-events:none`。
2. 地图卡只对专用照片操作按钮设置宽度、padding、底色、hover/focus 样式；共享 `.favorite-star` 仍由 `PhotoFavorites.button()` 生成，并维持缩略图右上角绝对定位。
3. 修改实际资源后同步 `/map` 查询缓存版本。保留点击地图标记、查看器、收藏、hover 事件与聚合计算。仅允许改本卡地图资源、定向测试、任务记录与进度记录；不改共享 favorites CSS/JS、API、数据语义、其他页面或地图架构。

## 性能与验证

- 链路与风险：右侧照片卡悬停/聚合刷新/卡片渲染；关注额外 Marker、监听、请求和卡片布局变化。
- 做法与边界：复用单 Marker，不新增 source/layer、HTTP、SQL、逐照片监听；保留共享收藏组件。
- 验证与停止：运行现有悬停测试及必要收藏样式断言、Node 语法、diff-check；用隔离合成图库在 Codex 内置浏览器检查 `/map`。如需地图架构或共享组件重构则停止并记录。

## 验收范围

自动：悬停 Marker 复用/坐标/状态/事件不回退；CSS 层级与绝对定位正确；照片操作按钮规则不再匹配收藏星标；JS 语法和 diff-check。按仓库要求保存 JUnit XML 至忽略目录并核对实际结果。

浏览器：悬停 GPS 照片并观察红点；在地图对应点/聚合数字重叠时检查层级及地图点仍可点击；观察收藏/取消收藏与缩略图、查看照片打开行为；看窄窗口/键盘焦点的基本可用性。环境或 fixture 限制项目标记 PENDING，不以静态断言替代浏览器验收。

## 交付

同一任务卡与 `docs/reports/P4-UI2-MAP-MINOR-FIX-A.md` 记录修改、命令/真实结果、浏览器路线及未验项；仅提交本卡相关文件，不推送。

## 实际结果（2026-10-19）

改动和自动验证已完成：定向悬停＋收藏 UI JUnit 8/8 通过，Node 语法和 diff-check 通过。浏览器视觉/交互项目按报告所述保持 PENDING；整卡不标 PASS。
