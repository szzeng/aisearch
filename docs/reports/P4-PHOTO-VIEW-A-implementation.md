# P4-PHOTO-VIEW-A — 实现与定向验收

日期：2026-10-09。基线：`5d7bc139b2b73de5f30c77fbee9e6a1cc648f108`。状态：**IMPLEMENTED / 定向验证通过 / 合成浏览器子集已复测 / 独立 DS-H 展示面复核待做**。

## 实现

新增共享只读面板 `photo-info.js/css`，照片打开后才请求详情、对应来源根目录、GPS和有效人物。文件名、尺寸、格式、大小、可信拍摄时间/相机、所有登记位置和完整 Windows/UNC 路径集中展示；来源失败时保留相对路径并说明完整路径不可用，复制失败会选中路径供手动复制。完整路径只进入可见文本/剪贴板，不进入媒体请求、地图、URL或返回快照。

GPS 仅在 `/map/assets` 有效成员、`/geo` 为 VALID、revision 匹配、WGS84/ASSUMED_WGS84 且范围合法时标记；零坐标有效。地图脚本/瓦片失败保留坐标并显示降级信息。人物按有效 `assigned_person_id` 去重，经 canonical person 详情取名和头像；封面头像失败回退当前照片的人脸，人物按钮继续可点。共享组件对重复挂载、关闭和迟到响应取消旧状态并释放地图。

首页详情和搜索查看器保留原预览及顺序；人物查看器保留分页、封面、更正、补识别；地点相册仍单张查看；地图照片缩略图与原“查看照片”按钮均进原详情路由，地图视野/列表返回快照保持原实现。人物整理人脸缩略图现在以独立按钮查看所属整张照片，不包裹其选择、更正操作。所有新增/改动入口都是键盘可聚焦控件，现有 dialog 仍支持 Escape。

### 入口清单

| 入口 | 结果 |
|---|---|
| 首页照片网格与详情 | 网格缩略图改为按钮；详情加载共享信息侧栏，旧明细折叠到“更多信息与位置记录” |
| 搜索结果 | 既有按钮、上下张保留；新增共享信息侧栏，原人物头像区沿用；去人物相册前保存原列表、滚动和照片 asset_id |
| 人物相册照片 | 既有预览/上下张/整理按钮保留；添加共享信息侧栏 |
| 地点相册照片 | 缩略图改为按钮，打开共享单张查看器；返回焦点到原缩略图 |
| 地图照片网格 | 缩略图改为按钮，和“查看照片”共用详情路由，使用原地图返回状态 |
| 人脸整理卡 | 人脸图外包独立查看按钮，所属照片由 asset_id 打开；选择/纠正控件不受拦截 |

静态 shell 已加载带版本号的共享 JS/CSS；`api/web.py` 登记共享资源为需重新校验的静态资源。未改业务 API/schema/依赖。

## 验证证据

- `uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_p4_place_map_review_ui.py tests/unit/test_api_frontend.py --junitxml=test-results/p4-photo-view-a.xml --basetemp=.pytest-tmp/p4-photo-view-a`：退出码 0，JUnit **111 tests / 0 failures / 0 errors / 0 skipped**。
- `uv run --no-sync pytest -q tests/integration/test_api_sources_assets.py::test_asset_detail_and_unknown_ids tests/integration/test_api_sources_assets.py::test_sources_create_list_and_structured_rejections --junitxml=test-results/p4-photo-view-a-api.xml --basetemp=.pytest-tmp/p4-photo-view-a-api`：退出码 0，JUnit **2 / 0 / 0 / 0**。
- Ruff 检查新增及调整的相关 UI 测试：通过。
- Node `--check` 覆盖 `photo-info.js`、`app.js`、`search.js`、`persons-albums.js`、`persons.js`、`places-albums.js`、`map.js`：全部退出码 0。
- `git diff --check`：退出码 0。
- 新增 JS 实际执行测试覆盖双位置 Windows/UNC 路径、复制失败手动选择、文件时间标签、同人去重/canonical 跳转、`(0,0)` 标记、GPS 成员拒绝、A→B 迟到响应及关闭后迟到详情/地图释放；入口静态核对覆盖五个页面资源版本及按钮绑定。

## 待补与边界

### 复核意见修正（2026-10-09）

- 修正完整路径拼接对盘符根目录`D:\`的处理；拒绝绝对/盘符相对路径与`.`/`..`段，避免将越界或非相对位置显示为来源完整路径。
- 人物详情解析完成后按canonical person ID再次去重；同图中不同assigned person ID已合并到同一canonical人物时只显示一个入口。
- 扩展合成执行测试，新增盘符根目录、无效relative_path以及两个原始人物ID合并为一个canonical人物的反例；共享资源查询版本更新为`p4-photo-view-a-2`。
- 独立复核卡补充固定基线`5d7bc139b2b73de5f30c77fbee9e6a1cc648f108`和并行改动边界。上述修正不代表独立DS-H复核或真实浏览器闭环已完成。
- 修正后重跑任务定向JUnit **111/0/0/0** 和只读API JUnit **2/0/0/0**；单独照片查看器测试JUnit **2/0/0/0**。Ruff、7个受影响JavaScript文件的Node syntax及`git diff --check`均通过；详细JUnit文件为`test-results/p4-photo-view-a.xml`、`p4-photo-view-a-api.xml`和`p4-photo-view-a-review-fixes.xml`。

### 浏览器复核发现修正（2026-10-09）

针对展示面复核报告的三项发现：

- 首页照片详情写入一次性浏览返回快照，浏览器 Back 返回原网格；人物页往返重新载入详情时恢复来源、排序、时间筛选、已加载页数和滚动位置。显式导航会清理旧快照。
- 搜索照片预览在请求人物详情后按canonical person ID二次去重。
- 共享面板与搜索预览在人脸页为空且`has_more=true`时说明“前200张尚未发现、仍有后续人脸”，不会断言整张照片没有已归属人物。

新增/扩展合成用例后，定向集合重跑为JUnit **113/0/0/0**（`test-results/p4-photo-view-a-review-fixes-r2.xml`）；Ruff、7个受影响JavaScript文件Node syntax、`git diff --check`通过。此轮未重跑真实浏览器；复核报告中已有的浏览器交互证据是在这些修复之前取得，修复后的浏览器状态恢复、搜索人物合并和空页提示仍待实机回归与独立DS-H复审。

任务卡要求的一轮真实浏览器合成闭环未完成。本轮只用合成数据尝试：本地合成服务绑定 `127.0.0.1:8765` 返回 Windows `EACCES`；Codex in-app browser 拒绝 `file:`，只允许 HTTP/HTTPS。没有改 ACL，也没有启动连接 PhotoSearchData 的服务。受此限制，页面间实际点击、浏览器 Back/页内返回、在线 OSM 标定和离线瓦片降级尚无浏览器证据，故不能标本机小样本浏览器 PASS；可在允许本机回环端口的环境补验。

### 修复后浏览器复测（2026-10-09）

使用既有 `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData` 合成catalog，新进程仅绑定系统分配的 `127.0.0.1:55579` 临时回环端口；无 PhotoSearchData、真实照片或ACL访问。浏览器通过本地 session 登录，应用状态显示 schema v21、10 张合成照片。

- 首页：限定 `library · #1` 后打开资产 #1，点击浏览器 Back，回到原照片网格，来源筛选仍选中 `library · #1`，10张列表保留。
- 人物往返：首页资产 #1 →“查看合成人物的照片”→浏览器 Back 回 `/#/asset/1`→“返回浏览”；返回网格后来源筛选仍为 `library · #1`，列表及数量仍为10。
- 搜索：无文本、来源筛选 `library` 得到权威 FILTER_ONLY 结果10张；从结果缩略图打开照片预览，路径/位置/人物信息可见；前进到第2张后关闭，预览消失、搜索页/来源筛选/10张结果保持。
- GPS详情显示 `(30.25, 120.16666666666667)`；浏览器控制台本次无 error/warn。搜索复测实际加载 `search.js?v=p4-photo-view-a-2`、`photo-info.js?v=p4-photo-view-a-3`、`search.css?v=p4-photo-view-a-1`、`photo-info.css?v=p4-photo-view-a-3`。截图：`.tmp/p4-photo-view-browser/search-preview.jpg`。
- 当前合成数据只有一个canonical人物入口，无法复现两个原始人物ID合并去重；也没有空人脸首页超过200且 `has_more=true` 的资产。这两项由定向JS测试覆盖，本轮真实浏览器未触发。地点相册入口与离线瓦片降级仍因无活动地名包/离线瓦片条件而待验证；不可标整卡PASS。
未运行全量 B、性能/30万夹具、模型、真实图库、打包或用户服务。当前工作区已有 P4-SEARCH-NL-UX-A 的未提交代码/测试/任务状态和 `.tmp` 内容均保留；未提交、未推送。

静态回归中原 `toFixed` 禁止断言与已存在的搜索字节数格式化相冲突；调整为保留 `.score`/`reranker_score` 禁止断言，并注明字节格式可舍入，避免测试误报分数展示。

## 后续

按 V3.1 §2.4 生成有界 DS-H 展示面独立复核卡：核对完整 UI/API 映射 diff、入口、只读路径读取、GPS/人物/竞态及本报告证据。该复核仍须把真实浏览器闭环标为待补，不得据单测将整卡判 PASS；不触及高风险协议时不需要 Sol/max。

## 浏览器补验（2026-10-09）

针对复核后的缺口继续使用 `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData` 合成数据根：插入201张合成脸（API首200张均无人脸归属、后续仍有一张已归属）、两张分别链接到 canonical/merged alias 的脸，以及一张人工合成地点成员。用户实际 PhotoSearchData 和真实照片均未连接。

- 搜索页显示10张 `FILTER_ONLY` 合成照片；合并别名的人物在预览中只显示一个 canonical 人物按钮。点击人物进入相册，浏览器 Back 后搜索筛选/结果和原预览恢复。
- 搜索预览与首页照片详情共享面板对空首200张都显示剩余人脸提示。
- 合成地点相册打开照片进入共享信息查看器，关闭后焦点恢复到原缩略图；当前没有安装地名包，验证的是人工合成地点相册成员。
- 用测试服务把地图瓦片 URL 指向不可达的本机端口；GPS详情仍显示坐标和地图不可用提示，控制台 error/warn 为空。在线 OSM 瓦片和 marker 已在先前合成浏览器走查确认。

截图见 `.tmp/p4-photo-view-browser/search-preview.jpg`（has_more 提示）和 `.tmp/p4-photo-view-browser/offline-map.jpg`（瓦片失败降级）。剩余独立 DS-H 审查仍待做；验证不包含真实图库、真实照片、正式数据包、完整发行或性能验收。


## 独立复核路由确认（2026-10-09）

用户明确接受 GPT-6 有界独立复核替代 DS-H；复核记录见 [展示面复核报告](P4-PHOTO-VIEW-A-review.md)。这只关闭独立复核路由，不将未覆盖的真实浏览器验收组合标为通过；任务整体仍为 **IMPLEMENTED / VALIDATION PENDING**。
