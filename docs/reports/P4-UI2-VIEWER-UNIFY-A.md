# P4-UI2-VIEWER-UNIFY-A 实施与验证报告

日期：2026-10-14
结论：**FUNCTIONAL SCOPED PASS / ACCEPTANCE PARTIAL**。UI 2.0 首页、搜索、人物/合集和地图共用 `AIPhotoInfo.open()` 查看器；为各页面保留受限扩展、导航和安全关闭回调。统一舞台支持预览适窗、约 10%–800% 缩放、预览 100%、双击、Ctrl+滚轮和边界夹紧平移。保留旧 `/#/asset/{id}` 入口并为无已知返回上下文的深链提供安全回首页路径。仅改前端，不改 API、搜索会话或数据语义。

## 实现面

- `photo-info.js/css`：将共享信息面板扩展为单一可访问模态查看器，集中管理 preview/thumbnail 回退、图片舞台、缩放和平移、序列导航、焦点返回、Esc/遮罩/Back 关闭、旧地图/面板资源释放。
- `app.js`：首页照片点击直接打开共享查看器；旧深链继续兼容。深链关闭时使用已知搜索/地图返回上下文，未知来路回首页；不以无条件 `history.back()` 离开应用。
- `search.js`：缩略图和“查看详情”进入同一查看器，搜索 rank/人物扩展和结果导航留在搜索页；关闭可将焦点还给触发项。
- `persons-albums.js`：保留脸部操作扩展、人物/合集照片导航和封面/更正/原图细节/待确认入口；通过 `beforeClose` 阻止写入或待确认期间关闭。
- `map.js`：GPS 照片在地图页面内打开；地图 center、bbox 和列表上下文不被详情路由替换。地点页复用已有 `AIPhotoInfo.open()`。
- 相关 HTML cache-bust 已更新；未删除旧详情 DOM 的兼容回退路径。

## 复核与修复

用户指定 LUNA High 路由后，独立受影响面审查提出 3 项 P2 finding：封面更新成功时 `writing` 状态仍阻止关闭；序列位置覆盖“上一张”按钮文字；搜索入口没有传共享查看器的返回焦点元素。已分别修复：关闭前清理写入态但保留 pending-write 拦截；位置文本落在单独状态节点且增加回归断言；缩略图/详情触发器传入 `returnFocus`，快照恢复时查找对应结果入口。LUNA High 复看确认无剩余可执行 finding。**这不是 DS-H 复核或通过声明。**

## 自动验证

JUnit XML：`test-results/P4-UI2-VIEWER-UNIFY-A.xml`，**21 tests / 0 failures / 0 errors / 0 skipped**。

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_p4_place_map_review_ui.py --basetemp=.pytest-tmp/P4-UI2-VIEWER-UNIFY-A --junitxml=test-results/P4-UI2-VIEWER-UNIFY-A.xml
node --check src/photo_search/web/photo-info.js
node --check src/photo_search/web/app.js
node --check src/photo_search/web/search.js
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/map.js
git diff --check
```

合成 DOM/JS 回归覆盖缩放按钮步进、适窗/100% 双击切换、Ctrl+滚轮、拖动边界夹紧、键盘导航输入框保护、位置节点独立、关闭焦点返回及地图释放。测试仅按本任务卡范围执行；没有运行全仓、Ruff、真实图库、模型/GPU或打包检查。

## 产品浏览器观察

在 Codex 内置浏览器和 `127.0.0.1` 本机服务，使用项目 `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData` 合成图库（10 张照片、人物和 GPS）。启动器的一次性 session 在同一浏览器内验证；token 未写入报告。没有访问 `PhotoSearchData` 用户真实图库。

| 流程 | 观察 |
|---|---|
| 首页时间轴 | 打开 `合成照片_08.jpg`；共享模态、信息面板和缩放控件可见；预览放大后倍率变化；浏览器 Back 关闭并恢复首页触发卡片焦点。 |
| 搜索结果 | 从人物相册进入固定人物范围的 FILTER_ONLY 搜索，预览结果后 Back 关闭；scope chip 和结果保留。该 fixture 没有 active semantic profile，因此这只证明 FILTER_ONLY 路径。 |
| 人物相册 | `合成人物` 照片使用同一模态；人物脸部标记/更正、封面选择、原图细节和待确认入口仍可见。本轮未触发任何写入。 |
| GPS 地图 | 从共享信息 GPS 链接进入地图，在地图列表打开照片后关闭；返回原地图 URL，当前成员和列表上下文仍在。样本只含一个当前成员。 |
| 旧深链 | `/#/asset/10` 打开同一共享查看器；关闭后安全回到 `/`，不再次打开深链。 |
| 截图 | [真实内置浏览器查看器截图](artifacts/P4-UI2-VIEWER-UNIFY-A-viewer.jpg)，当前合成照片和查看器可见。截图为窄 viewport 捕获，不作为原生 200% 缩放证据。 |

## 状态与遗留门禁

| 指标 | 状态 |
|---|---|
| `FUNCTIONAL` | **SCOPED PASS**：统一查看器接线及任务卡定向 JUnit 通过。 |
| `ZOOM_PAN` | **PARTIAL**：自动交互覆盖缩放/平移边界，浏览器观察到缩放控件响应；实机长图、全景、触控拖动与真实 200% 未测。 |
| `NAVIGATION_RETURN` | **PARTIAL**：首页、搜索 scope、人物、地图和旧深链核心返回已观察；搜索箭头连续翻页、多页/过期候选和多层二级对话框路线未全验。 |
| `PERSON_ACTION_SAFETY` | **PARTIAL**：扩展入口可见，任务卡单测通过；未在浏览器实际提交封面/人脸写操作。LUNA finding 修复由代码复核并加位置行为断言。 |
| `BUILTIN_BROWSER` | **PARTIAL**：四条核心入口和旧深链有本机合成数据证据；地点相册独立返回、资源失败/离线、快切迟到响应以及有效语义搜索未覆盖。 |
| `LEGACY_LINK` | **PASS（直接未知来路关闭路径）**：深链刷新/打开后关闭回首页。带搜索/地图已保存上下文的旧链接返回组合未全验。 |
| `PERF_NOT_RETESTED` | **PENDING**：本卡未执行 PERF_200K/CHECK_300K；未进行整库性能工作。 |
| `REVIEW` | **LUNA HIGH COMPLETE**：三项 finding 已修复并复看关闭；不称为 DS-H。 |

本卡不追认 VIEWER-F、FLOW-A、VISUAL-FINAL-A 或 SEARCH-SCOPE 的待验项。真实 200%/暗色主题对照、长图/失败资源、逐入口全导航、人物写入实操、`PERF_200K` 和更广回归仍待后续相应任务验证。因此整卡保持 **ACCEPTANCE PARTIAL**。本地提交由用户后续明确授权执行；未 push。
