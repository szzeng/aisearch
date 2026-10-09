# P4-PHOTO-VIEW-A — 全页面照片查看、基本信息、路径、地图与人物

> **2026-10-09个人待审范围更新（当前优先）：** 按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)与[本组唯一入口](P4-PERSONAL-REVIEW-SEARCH-A.md)收口。下文保留历史实现要求；冲突的重复全量/30万/模型/打包/固定多轮审查及浏览器组合要求，以本次个人基础范围为准。先复用可核对证据，缺口才补最小测试；首次审查覆盖完整未审新增面，身份、人工状态、撤销、迁移/恢复保护仍必留。此更新未执行审查或新增PASS；已有现场未验事项照实保留。

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过 / 本机合成浏览器小样本验证通过 / GPT-6 独立展示面复核通过（按用户指示替代 DS-H）/ 整卡验证仍待补**。基线：5d7bc139b2b73de5f30c77fbee9e6a1cc648f108。
推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**，用户手动选择；同会话完成实现、调试、简单验证和修复。
依据：[规划与现状](../reports/P4-PHOTO-VIEW-A-planning.md)。本卡接续本次用户新增查看需求；P4-SEARCH-PLACE-NL-A仍READY，其他待验/待审状态保留，不自动实施多个任务。
实现与证据：[P4-PHOTO-VIEW-A 实现报告](../reports/P4-PHOTO-VIEW-A-implementation.md)。原定向JUnit为**111/0/0/0**；复核修复后重跑**113/0/0/0**，只读 API JUnit **2/0/0/0**、Ruff/Node/diff-check通过。复核记录：[展示面复核](../reports/P4-PHOTO-VIEW-A-review.md)。修复后合成浏览器已复测首页Back、人物页往返、搜索预览及返回、合并别名人物、空页has_more、地点相册照片入口和离线瓦片降级；在线GPS标记正确。两旧人物别名由只读 API 归一为同一canonical人物，前端二次去重也由定向交互测试覆盖。小样本浏览器验证通过；独立 DS-H 展示面复核待做，整卡不标PASS。

## 1. 唯一闭环

任何页面已有的照片缩略图，点击即可查看大图。查看时可直接看到文件名、完整文件路径和基本信息；有有效GPS就显示在线地图框和拍摄位置标记；已有有效人物归属就显示头像，点击进入对应人物相册。体验参考Google相册：照片为主体，信息集中在侧栏，返回后继续原来的浏览。

一张卡内完成“共享照片信息 → 各页面接入 → 小样本验收”，不拆后端/UI/测试子卡，不自动延伸成图库重扫、性能或发行任务。

## 2. 开工读取与范围

顺序：AGENTS.md → docs/development-status.md → 本卡 → 规划报告 → 实际Git状态。已有status修改、自然语言地点规划文件、.tmp及tmp清理记录均需保留；未授权提交或推送。

仅读V3.0 §4.1（内容身份与多个文件位置）、§4.5.2/4.5.4（GPS事实及受保护详情）、§10.1～10.3；V3.1 §2.1/2.2/2.4、§3.2/3.4、§4.1、§11/12；ADR-0011的拍摄/文件时间展示条款。地图沿用P4-PLACE-MAP-A §2～4的用户个人使用例外与有效GPS规则；人物仅读ADR-0014相关只读头像/人工归属条款。

直接代码/接口：

- web/app.js、index.html、styles.css中的照片浏览、详情、文件位置、预览缩放与返回。
- web/search.js/html/css中的搜索查看器、人物入口、分页及返回快照。
- web/persons-albums.js、persons.html/css、persons.js中的相册查看、封面、更正与整理缩略图。
- web/places-albums.js、places.js、places.html/css中的地点相册查看、已有照片缩略图与整理；不要求给纯文字列表新增缩略图。
- web/map.js/html/css、map-config.js中的照片网格、同坐标成员列表、地图聚焦及返回。
- api/models.py、routes.py、queries.py中的直接读模型：GET /assets/{id}、/sources/{id}、/assets/{id}/geo、/assets/{id}/faces、/persons/{id}、/map/assets；api/web.py的静态资源清单（若新增资源需要登记）。
- tests/unit/test_api_frontend.py、test_search_person_album_link_ui.py、test_person_preview_navigation_ui.py、test_p4_place_map_review_ui.py；tests/integration/test_api_sources_assets.py的详情/来源只读场景。

允许修改上述前端、必要的静态资源登记、直接相关测试、本卡/报告/status；允许新增小型web/photo-info.js与photo-info.css来共享展示逻辑。优先不改业务API，不增加依赖/schema/迁移。

## 3. 工作包一：共享照片信息面板

实施前在实现报告写短设计：共享面板如何装载到现有详情/搜索/人物/地点查看器，如何绑定asset_id和请求序号，如何清理地图，以及路径/返回状态如何处理。共享的是照片信息和查看行为，不强制重写所有页面成一套大型路由框架。

- 大图适合窗口，信息栏默认显示基本信息；窄屏可折叠成下方面板。已有缩放与原图入口保留。内部哈希、civil排序键和产物状态不占基本信息区，可保留在高级详情。
- 基本信息：文件名、完整路径（可复制）、分辨率（宽 × 高，像素）、格式、大小、已有拍摄时间/相机信息。文件时间明确标注，不冒充拍摄时间；缺字段不编造，不重新解码整库以补齐展示。
- 路径方案：GET /assets/{id}的locations提供source_id和relative_path；查看照片后按所需来源调用既有GET /sources/{id}读取root_path，组合为Windows完整显示路径。支持盘符根目录（如`D:\`）、普通盘符目录、UNC/中文/长路径与分隔符；拒绝盘符相对路径、以斜杠开头及包含`.`/`..`段的relative_path，不误显示相对路径为完整路径。
- 同内容有多个位置时，优先显示PRESENT位置，并可展开全部位置，标注来源与缺失状态；不随意称第一位置为唯一原图。来源详情失败仍显示相对路径及“完整路径暂不可用”，复制不能声称得到完整路径。
- 本次用户明确要求查看时显示完整路径，取代旧P4-SEARCH-PREVIEW-A不展示locations的UI限制；仅按已打开照片读取根目录，首页/列表仍用rootless摘要。路径仅作本地展示和复制；图片访问仍用asset_id，不把显示路径送入媒体API、地图、URL、日志或返回快照。
- 复制成功/失败有简单反馈，失败时允许手动选取；不把完整路径作为可执行命令或任意文件打开接口。
- GPS直接复用/geo的normalized_wgs84及地图成员有效性：VALID、当前input_revision、WGS84/ASSUMED_WGS84和有效文件位置；有限值、纬经度范围、(0,0)、[longitude,latitude]顺序正确。人工地点名或城市中心不能当拍摄坐标。
- 复用本地MapLibre 4.7.1与map-config.js的OSM/WGS84配置，有有效GPS即加载地图和标记，提供“在地图相册中查看”。沿用既有用户在线瓦片例外，不新增确认弹窗/隐私开关；不接在线逆地理或新地图服务。
- 无GPS隐藏地图，GPS读取失败区别于确实无GPS；瓦片/脚本/WebGL失败显示“地图暂不可用”和已知坐标，大图和其他信息仍可查看。
- 人物复用/faces已有有效归属及/persons/{id} canonical结果，按canonical person_id去重；测试覆盖多个不同assigned_person_id合并为同一canonical人物。优先人物封面头像，再用当前照片对应脸，最后默认头像。无归属不猜身份，未命名有效人物用现有名称；接口有has_more时如实提示展示范围，不宣称全量人物。
- 点击人物头像进入/persons?person_id={id}；返回可恢复来源列表和原照片。保存asset_id、来源标识和既有列表恢复状态即可，不复制完整路径/GPS/凭证到存储。
- 打开/切换/关闭的图片、元数据、人物和GPS响应均校验当前asset_id/请求序号；切换清空旧面板，关闭后迟到响应不得复活或串图。关闭或切换时remove地图实例并释放监听；共享脚本加载去重。

## 4. 工作包二：所有现有入口接入

实施报告先记录入口清单及结果，覆盖以下范围：

| 页面/现有入口 | 接入要求 |
|---|---|
| 首页/普通照片浏览与详情 | 照片缩略图可点击；详情接共享信息、完整路径和人物；现有预览缩放保留 |
| 搜索结果 | 保留现有大图/上下张/续页，补完整路径、地图及共享人物信息 |
| 人物相册照片 | 保留现有上下张、相册脸标记、封面/更正/原图补识别操作，补信息、路径、地图及其他已归属人物头像 |
| 地点相册照片 | 现有仅大图弹窗接共享信息；关闭回当前相册，不丢分页/更正状态 |
| 地图照片网格/同坐标成员 | 图片本身和“查看照片”都能打开；不局限于文字按钮；保留地图视野、成员列表、滚动与版本检查 |
| 其他已有照片/人脸整理缩略图 | 有asset_id的人脸可查看所属整张照片，同时保留选择/更正操作；人物头像卡片依然进入人物相册，地点封面卡片依然进入地点相册 |

不要把所有img全局绑定点击，也不截获选择框/编辑/封面按钮。照片入口使用可聚焦按钮/链接，支持键盘Enter，弹窗支持关闭/Esc并恢复焦点。

已有上下张的查看器继续沿当前结果顺序；跨页复用原session/cursor，不把局部相册扩成整库。地点等此前只有单张预览的入口允许先保持单张查看，不为本卡引入新分页协议。浏览器Back、弹窗关闭、详情页内返回分别核对，搜索词/筛选/已加载分页/滚动和地图视野按原来源恢复。

静态资源版本同步更新；新增共享资源按现有发行清单登记并做轻量读取验证，不启动exe构建。检查原前端静态测试中“仅展开来源才读根目录”“地点页不读GPS/根目录”等断言：本卡允许已打开照片详情按需读取，但不能全局删除断言或放宽普通列表启动边界。

## 5. 工作包三：简单验证

**用户本次明确授权个人使用、简单测试：本卡采用A类只读展示的定向验收范围，覆盖多页但不触碰业务写入/身份/坐标/分页契约。按用户指令不运行B全量、30万夹具、模型、真实图库扫描、性能或打包。** 如实现确需高风险协议变动，停止扩大，记录并另立任务，不靠本卡测试例外覆盖。

约8～12张项目内合成照片覆盖：有GPS+两名人物、无GPS/无人、同一人物多脸、未命名人物、头像缺失、两处相同内容路径、中文/UNC/长路径、离线文件、合法零坐标与非法/过期GPS。

新增tests/unit/test_photo_view_info_ui.py：实际执行产品JS与最小DOM/HTTP/MapLibre桩，测试面板、复制、人物跳转及迟到响应/关闭清理；不只断言字符串存在。复用已有小测试，避免对简单样式添加镜像测试。

必验：

1. 基本信息、完整路径和多个位置正确；根目录按打开照片的source_id按需读取；来源失败可解释，列表不批量读根目录。
2. 有GPS显示正确坐标/标记；零值合法，无GPS隐藏；无效/过期不标定；地图离线只影响地图。
3. 合照人物去重、正确封面/回退与canonical跳转；无归属不猜身份；头像失败仍可点击有效人物。
4. A→B快速切换/关闭、迟到元数据/地图/人物响应不串图；地图对象释放；列表原分页/人物整理操作不被点击查看破坏。
5. 每类入口至少点击一张合成照片；上下张首尾/简单跨页、关闭/Esc、浏览器Back、页内返回、人物头像跳转后返回分别完成一次，确认来源状态及原照片可恢复。

参考定向命令（新增UI测试落地后执行）：

~~~powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_p4_place_map_review_ui.py tests/unit/test_api_frontend.py --junitxml=test-results/p4-photo-view-a.xml --basetemp=.pytest-tmp/p4-photo-view-a
uv run --no-sync pytest -q tests/integration/test_api_sources_assets.py::test_asset_detail_and_unknown_ids tests/integration/test_api_sources_assets.py::test_sources_create_list_and_structured_rejections --junitxml=test-results/p4-photo-view-a-api.xml --basetemp=.pytest-tmp/p4-photo-view-a-api
uv run --no-sync ruff check tests/unit/test_photo_view_info_ui.py
node --check src/photo_search/web/photo-info.js
node --check src/photo_search/web/app.js
node --check src/photo_search/web/search.js
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/map.js
git diff --check
~~~

实际修改persons.js/places.js/map-config.js等脚本也纳入Node；修改的Python文件纳入Ruff，后端只读实现若变更再补直接受影响API用例。不虚构未建立的新文件已通过；Python使用3.12.10。WinError5记录环境失败，走正常权限路线复验，不改ACL。

一轮隔离合成浏览器走查覆盖入口表、信息/路径复制、地图实际标定、人物跳转与返回；记录页面资源版本、实际动作/结果，JS桩不替代真实浏览器。若网络不通，记录地图离线降级通过、在线瓦片定位待验，不能把整卡标PASS。禁止启动默认连接PhotoSearchData的服务。

## 6. 收口与复核

输出docs/reports/P4-PHOTO-VIEW-A-implementation.md：短设计、入口清单、全部改动文件、定向命令/退出码/JUnit计数、浏览器证据和未验证项。更新本卡/status；必需项未完成写IMPLEMENTED / VALIDATION PENDING；完成后只标“本机个人照片查看与小样本验证通过”，不声称真实图库、性能或正式发行验收。

展示面有界复核按V3.1 §2.4核对完整展示diff、全部入口、只读API映射、路径按需读取、GPS/人物/错误与竞态、所需证据；不重复底层识别或历史大型测试。本轮由GPT-6完成，用户明确接受该独立复核替代DS-H；不得将其表述为DeepSeek审查。若出现新增写入/权限或高风险语义变化，扩大为完整受影响面审查；§2.2有实际证据才建议max。默认SOL_NOT_REQUIRED：本卡不改身份、事务、坐标基准、人工知识或权限；有高风险ADR未决项时才生成Sol high聚焦卡。

个人试用闭环后停止，不默认生成性能/打包/全量验收卡。实现阶段不会自动启动其他Agent；下一模型始终由用户手动选择。



### 独立复核状态更正（2026-10-09）

用户明确接受本会话 GPT-6 有界独立复核替代 DS-H。复核报告已覆盖代码/API、修复项、复测证据及合成浏览器入口；不声称为 DeepSeek DS-H。由于主卡规定的多位置/路径等部分验收组合仍未在真实浏览器完整覆盖，整体仍为 **IMPLEMENTED / VALIDATION PENDING**。

## 分辨率补充（2026-10-09）

共享照片信息面板明确显示原图分辨率。详情已有宽高时直接显示；缺失时仅对当前打开照片的受保护可用文件读取图像头，不完整解码、不写回catalog，按EXIF方向显示upright像素尺寸；来源不可读或格式不受支持时保留为空。五个页面同步加载新版共享脚本。
