# P4-UI-VIEWER-F 执行报告

日期：2026-10-14
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
任务卡：[P4-UI-VIEWER-F](../tasks/P4-UI-VIEWER-F.md)

## 完成内容

- 共享照片信息面板可折叠；统一预览提供可见关闭按钮、对话框标题关联、打开时焦点进入关闭按钮、关闭后焦点回到触发控件。
- 大图使用 `object-fit: contain` 并适配窄屏/竖图；预览失败时回退到缩略图并显示状态。
- 地图返回快照只有在 `member_version` 与当前 bbox 都匹配时才复用照片成员、游标和滚动锚点。时间排序快照还会通过服务端校验旧游标绑定的 catalog/time-rules revision；无续页游标时比较当前视口完整结果的资产 ID 与可信拍摄时间签名。
- 搜索返回前通过已有的只读候选时间分页接口验证会话与权威范围修订；时间浏览快照还比较当前与保存的 `taken_time_rules_version`。不重复运行 NLP/向量检索。验证失败或接口不能验证的快照不会恢复旧命中；保留查询条件并提示重新搜索。
- 五个页面统一更新查看器脚本和样式缓存版本。

## 验证

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_p4_place_map_review_ui.py --basetemp=.pytest-tmp/P4-UI-VIEWER-F --junitxml=test-results/P4-UI-VIEWER-F.xml
node --check src/photo_search/web/map.js
node --check src/photo_search/web/search.js
node --check src/photo_search/web/photo-info.js
.\.venv\Scripts\ruff.exe check tests/unit/test_photo_view_info_ui.py
git diff --check
```

- JUnit：[test-results/P4-UI-VIEWER-F.xml](../../test-results/P4-UI-VIEWER-F.xml)，**21 passed / 0 failed / 0 errors / 0 skipped**。
- 三个 JS 语法检查、Ruff、diff-check 均通过。
- Luna High 独立受影响面复核发现地图 bbox 和搜索快照修订校验两项 P2；两项已修复。复核还补出时间规则版本遗漏，已加入地图排序游标和搜索时间快照校验。最终复核确认原始问题均关闭，且本轮时间规则修复无新 actionable finding；未覆盖真实浏览器门，也不是 DS-H。

## 尚未验收

- `BROWSER_INTERACTION=PARTIALLY_VERIFIED`：本轮完成部分合成产品浏览器观察，逐项结果见下文；整卡门仍未关闭。任务卡要求的首页第30页往返、长竖图/窄窗、离线原图有缩略图、遮罩关闭和异步快切旧请求释放仍未覆盖。
- 最终 demo 使用本地 `file://` 打开时被浏览器策略阻止；未尝试绕开限制，最终 demo 的视觉对照仍待验。产品浏览器截图工件已在下方链接。
- 本轮未访问真实照片库；语义编码器不可用，搜索只使用空查询的图库权威筛选结果（`FILTER_ONLY`），不能视为语义搜索验收。未执行性能、全仓、GPU/模型、打包验证；相关门保持 PENDING，也不改变前置卡状态。

本卡实现已完成，但浏览器门未关闭，故不标整卡 PASS。按卡片停止边界转用户试用；任何真实图库现场结果由用户反馈另行记录，不自动扩展新任务。

## 2026-10-14 本机产品浏览器验收补记

沿用项目内隔离合成图库 `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData`，其中 10 张自动生成照片，测试人物、地点与 GPS 归属均为合成数据。产品服务只绑定 `127.0.0.1:18778`，由应用启动器生成一次性会话链接，在 Codex 应用内浏览器打开；未使用 `--open-browser`，未访问真实照片库，也未尝试绕过浏览器安全策略。会话凭证不记录在报告；服务已停止。

观察结果：

- 首页显示 10 张照片、可信拍摄日期 0、日期未知 0、日期待就绪 10。打开合成照片详情可见原文件位于项目 `.tmp` 数据根；此前保存的详情和人物预览截图继续作为本轮证据。
- 人物目录的“合成人物”相册显示 1 张待就绪照片。“搜索此相册”生成固定人物范围 chip；空查询返回 1 条权威图库筛选候选，明确标为 `FILTER_ONLY`，语义未就绪。结果预览显示 64×48 图片、信息面板、GPS 坐标、地图入口和人物入口。点击人物入口进入人物相册，浏览器返回时人物范围 chip 恢复，但旧结果被清空并提示“无法验证旧结果的范围或时间修订”，要求重新搜索；因此候选/查询状态的返回恢复**未通过**任务卡预期。
- 地点时间轴显示“合成验收地点”1 张有效照片及1张日期待就绪照片。“搜索此地点”显示固定地点范围 chip，但提交空查询时服务端返回 `CATALOG_FILTER_REVISION_STALE`（地点身份已变化，请重新选择），地点范围搜索结果路径未完成。该合成地点没有活动地名包，当前只能证明 UI 显示和安全拒绝状态，不能推断真实来源目录下的产品路径。
- 此前捕获的 GPS 地图截图显示合成库有 2 张带位置照片；从资产详情使用“返回地图相册”可回到地图并保留当前 1 张 viewport 成员。浏览器点按 Zoom Out 后未观察到成员数变化，故 bbox 改变后旧 viewport 成员失效规则仍**未验证**。
- 已观察 Escape 关闭人物照片预览并把焦点还给触发按钮、地点照片预览关闭后焦点返回；照片信息面板可折叠/展开。此前合成照片详情中显示未记录拍摄时间，无伪造“拍摄同一天”动作。没有离线原图状态样本，也没有长竖图、小窗口或遮罩关闭观察。

本轮及此前浏览器截图工件：

- 人物范围搜索：[person-scoped-search](artifacts/P4-UI-VIEWER-F-person-scoped-search.jpg)、[person-scoped-preview](artifacts/P4-UI-VIEWER-F-person-scoped-preview.jpg)
- 地点时间轴：[place-timeline-repeat](artifacts/P4-UI-VIEWER-F-place-timeline-repeat.jpg)
- 查看器、人物/地点预览、信息折叠和地图路径：[viewer-detail](artifacts/P4-UI-VIEWER-F-viewer-detail.jpg)、[person-preview](artifacts/P4-UI-VIEWER-F-person-preview.jpg)、[info-collapsed](artifacts/P4-UI-VIEWER-F-info-collapsed.jpg)、[map-scope](artifacts/P4-UI-VIEWER-F-map-scope.jpg)、[map-focused](artifacts/P4-UI-VIEWER-F-map-focused.jpg)、[map-return](artifacts/P4-UI-VIEWER-F-map-return.jpg)、[place-timeline](artifacts/P4-UI-VIEWER-F-place-timeline.jpg)、[place-preview](artifacts/P4-UI-VIEWER-F-place-preview.jpg)、[search-results](artifacts/P4-UI-VIEWER-F-search-results.jpg)、[search-preview](artifacts/P4-UI-VIEWER-F-search-preview.jpg)。

本补记只提高到 `BROWSER_INTERACTION=PARTIALLY_VERIFIED`，不标浏览器验收 PASS 或整卡 PASS。`PERF_200K`/其他规模性能、真实照片库体验、完整视觉基线对照和未覆盖交互仍待实际证据。

## 工作树边界

执行开始前已有 SEARCH-MAP、CORE 报告、人物生命周期测试等未提交改动；执行期间也观察到 SEARCH-MAP 阶段二工作树改动。它们均保留。本卡与 `map.js`、`search.js`、页面资源引用及共享 UI 测试有重叠，报告只归因于查看器实现及上述 bbox/搜索快照校验补丁，不归因并行的时间浏览、地图视口排序或 SEARCH-MAP API 改动。
