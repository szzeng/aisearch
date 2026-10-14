# P4-UI2-VISUAL-FINAL-A 执行报告

日期：2026-10-14
结论：**IMPLEMENTED / ACCEPTANCE PARTIAL**。视觉代码层完成；定向 JUnit **9/0/0/0**、`git diff --check` 通过。2026-10-14 再次使用隔离合成图库、应用生成的一次性链接及 Codex 内置浏览器，产品首页、地点、地图、人物、搜索与管理路由均成功加载，API 页面请求无先前的 `401`。查看器可打开并用 Escape 返回；搜索查询因 fixture 没有激活搜索 profile 返回 `422 VALIDATION_ERROR`；人物/地点数据是明确的空状态。地图瓦片实际可见，平移后范围 1 张照片，打开 GPS 合成照片再返回保留范围。首页、搜索错误态、地图、viewer 四张截图已保存至 `docs/reports/artifacts/`。实测 CSS 视口 470×764、无横向溢出；系统 `prefers-color-scheme: dark` 为 true，暗色显示正常，但未切换系统主题。Codex 浏览器键盘缩放尝试没有改变视口指标，真实 200% Zoom 仍 pending。LUNA High 修复复核无剩余可执行 finding（不是 DS-H）；`PERF_200K/CHECK_300K` 按任务卡仍未执行。

## 差异清单与处理

唯一视觉基线为确认版 `docs/mockups/ui2-interactive/demo.html`。下表结合 demo 源码与同一 Codex 内置浏览器中实际可见的页面；合成 demo 内容只作视觉参照。

| # | 优先级 / 对照点 | 产品原状与位置 | 本次决定 / 结果 | 证据与待验 |
|---|---|---|---|---|
| 1 | P1 外壳宽度 | demo 侧栏约 13rem；`styles.css` 的 `.app-shell` 最大 15rem | 最大宽度收至 13rem，保持最小 12rem 和窄屏既有导航切换 | `STATIC_CHECKED`；改后桌面实看 pending |
| 2 | P1 照片网格密度 | demo 照片页 5 列；`photo-timeline.css` 的 13rem 卡片最小宽度在同尺寸下形成 4 列 | 降至 10rem，使可用宽度能排出接近 demo 的 5 列；640px 断点仍为两列 | 改前同浏览器可见 4 列、demo 5 列；改后桌面实看 pending |
| 3 | P1 人物/合集时间轴密度 | `persons.css` 独立时间轴原设 12rem，与公共时间轴密度不同 | 同步至 10rem；人物目录头像网格仍使用自身布局 | `STATIC_CHECKED`；浏览器路由成功，合成库人物数为 0，空状态正常显示 |
| 4 | P1 卡片语义 token | `photo-timeline.css` 使用未定义的 `--radius-control`、`--radius-card` 和重复颜色回退 | 控件统一用 `--radius`，卡片统一用 `--radius-lg` 与共享 `--line/--panel/--shadow-soft` | `STATIC_CHECKED`；亮暗色实际切换 pending |
| 5 | P1 查看器信息面板 | `photo-info.css` 有独立较紧内边距和与 F1 muted token 不同的回退色 | 内边距从 `.75rem` 微调至 `.85rem`；说明文字跟随共享 `--fg-muted` | 产品 viewer 在合成照片上打开并显示信息面板；Escape 返回首页通过；截图见 viewer 工件 |
| 6 | P1 地点面板层级 | `places.css .panel` 只有基础边框和小圆角，弱于 demo 面板层级 | 采用共享大圆角、间距和轻阴影，保留原 DOM、筛选和动作 | CSS 已对照；地点页窄视口结构可见，但有数据的目录/相册 pending |
| 7 | P1 地图高度 | `map.css .map-page-main` 固定为 `calc(100vh - 5rem)`，与 shell 的剩余网格行同时定高 | 改为跟随 shell 网格高度，窄屏原有 `height:auto` 仍生效 | 地图页/API 200；470px CSS 宽度下瓦片可见，平移后 1 张范围照片，详情返回仍为 1 张 |
| 8 | P1 缓存一致性 | 五个产品页使用不同的公共 CSS 旧版本号，地图、人物、地点、查看器样式亦各自分散 | 将实际受影响 CSS 的引用统一为 `p4-ui2-visual-final-a-1`，包括内部 `timeline-test.html`；JS 版本与 FLOW-A 的搜索脚本版本保留 | 五页 HTML 静态核对通过；`search.html` 中 FLOW-A 的 JS 缓存改动保留 |
| 9 | P1 全局搜索和导航 | demo 与产品都有唯一页头搜索、三项主导航及管理入口 | 保留产品现有结构、链接与权限门控，不为贴合 demo 增加第二个输入或删导航 | 6 个页面路由均成功加载，导航可见；页面 API 请求 200 |
| 10 | P1 搜索筛选与状态说明 | demo 模拟筛选更紧凑；产品搜索保留条件说明、高级筛选、授权错误提示等真实业务 UI | 保留必要说明和 FILTER_ONLY / 错误状态层级，不删真实条件以压缩版面 | `/search` 筛选与错误状态可见；查询返回 422，fixture 没有 active search profile，未得到有效结果 |
| 11 | P1 人物目录与整理 | demo 将人物目录、合集、整理做成演示入口；产品目录和批量/撤销动作有实际语义 | 保留产品自己的分区、操作和数据状态，不复制模拟头像或删除低频动作 | 人物/合集/整理结构可见；合成库返回 0 人，产品空状态正常显示 |
| 12 | P1 地点相册与地图列表 | demo 地点页使用假地名和模拟地图；产品需保留地点层级、修正动作及地图照片栏 | 保留真实 DOM/流程；不伪造地点、地图成员或瓦片状态 | 地点与地图 API 200；地点包未安装状态如实显示；地图瓦片可见，平移后范围有 1 张照片 |
| 13 | P1 管理页主次层级 | demo 将管理放在次级导航；产品来源、建库与维护操作由真实业务控制 | 保留入口与折叠维护区，不删除管理能力或模拟执行结果 | `/?view=sources` 显示合成 source 和按需管理入口，来源接口成功 |
| 14 | P1 首页留白与字体 | demo 使用较紧凑的内容起始位置；产品需要时间语义说明与图库/日期筛选 | 不压缩根字号、不移除准确时间说明；共享 `html` 根字号继续 100% | 改前首屏实际可见；F1 对焦点滚动修正保留；改后桌面布局 pending |
| 15 | P2 viewer 统一入口、装饰细节 | demo 是单一模拟预览；产品各查看入口保留原有 viewer 合同 | 有意保留不同入口和真实状态；本卡不引入新 viewer、缩放/平移或重写返回状态 | 产品 viewer 打开合成照片，Escape 返回通过；viewer 内部缩放 266% 不作为页面 Zoom 证据 |

本轮内置浏览器未复现遮挡、无法滚动或不可读的视觉缺陷。首页、地点、地图、人物、搜索、管理和 viewer 路由均通过应用会话请求成功；搜索的 422 是合成 fixture 没有 active search profile 的配置/数据限制，未伪造命中结果，也未绕过认证。

## 后续修正：人物合集标题徽标

用户反馈窄徽标中的“人物合集”文字超出底色。`persons.css` 对 `#pa-album-avatar` 下徽标单独放宽最小高度并加入内边距、较紧凑行距和安全换行；`persons.html` 更新对应 CSS 缓存参数。源码与尺寸规则已静态检查。新启动的隔离服务通过应用生成的一次性链接在 Codex 内置浏览器完成认证；`/persons` API 正常但 fixture 中人物/合集为 0。点击“新建合集”时应用依赖的浏览器 prompt 未出现，无法构造徽标所需状态；因此此修复的内置浏览器目视复核为 `PENDING_FIXTURE / TOOL_LIMIT`，未宣称通过。搜索/相册行为未改。

## 内置浏览器验收

仅用项目 `.tmp/P4-UI2-CLOSEOUT-FLOW-A/map-browser-data` 隔离数据根、本机 `127.0.0.1:18831` 产品服务，以及应用启动时打印的一次性 session 链接；未访问 `PhotoSearchData` 用户图库、模型或 GPU。另将已确认 demo 通过项目只读静态服务 `127.0.0.1:18832` 打开，在同一 Codex 内置浏览器目视对照。没有使用外部浏览器、驱动、扩展或安全策略绕过。

首次尝试的历史记录：产品首页经 session 链接进入成功，首页 API 正常。尝试复用 `.tmp/P4-UI-PLACES-E/browser/PhotoSearchData` fixture 时，应用因 `DataRootLeaseBusy` 拒绝启动；之后首次会话跨路由出现 401。该次没有释放租约、修改 ACL、重兑 token、注入 cookie 或更改安全设置。以下记录是本次用户要求的独立重试结果。

### 2026-10-14 重试结果

对先前未完成项重新使用项目合成数据根 `.tmp/P4-UI2-CLOSEOUT-FLOW-A/map-browser-data`，通过本机 `127.0.0.1:18831` 启动产品服务，并在 Codex 内置浏览器打开应用打印的新一次性 session URL。session exchange 为 303；首页时间轴、periods、缩略图及各页 API 请求成功，**之前跨路由出现的 401 本次没有复现**。catalog 展示 10 张日期待就绪照片，并引用 `.tmp/p4-photo-view-browser/geo-env/library/album/` 下由测试生成的合成 JPEG；未读取真实图库。

- `/`：10 张合成照片、时间轴控件、说明文字和缩略图可见。
- `/persons`：人物和合集操作结构完整，API 正常；catalog 返回 0 人并显示对应空态。
- `/places`：地点相册、地点目录、建议、批量和操作日志区域可见；API 正常。数据集状态为未安装，地点成员空态正常。
- `/map`：MapLibre 和瓦片实际显示。统计有 2 张带位置照片；初始视口为 0 张。平移后变为 1 张，打开合成照片 `GPS_10.jpg` 显示位置坐标，返回地图后范围仍为 1 张。
- `/search`：筛选控件与错误状态可见；提交“GPS 合成照片”后 HTTP 422 `VALIDATION_ERROR`，错误提示未激活 search profile 时请求需显式绑定 space/image generation/dimension。fixture 没有 active search profile，未能验收命中或 FILTER_ONLY 结果。
- `/?view=sources`：管理页显示合成 source 与按需管理入口，来源 API 正常。
- viewer：主页照片和地图 GPS 照片均可打开；主页 viewer 使用 Escape 返回、地图照片使用「返回地图相册」均通过。viewer 自身 266% 图片缩放不作为页面 zoom 证据。

CUA browser 读取到 CSS viewport **470×764**，`documentElement.scrollWidth=470`，实看页面没有横向溢出。`prefers-color-scheme: dark` 当前为 true，计算背景色 `rgb(24, 35, 32)`；CUA 无系统主题切换入口，未测 light 对比。对浏览器发送 Ctrl+0 和 4 次 Ctrl+plus 后，视口指标未变化、DPR=1.5、`visualViewport.scale=1`；真实 `ZOOM_200_NATIVE` 仍 pending。470px 实际窄视口不是 200% zoom。

内置浏览器截图已保存为真实产品 PNG：`docs/reports/artifacts/P4-UI2-VISUAL-FINAL-A-home.png`（`/`）、`...-search.png`（带合成查询词的搜索页；422 详情在视口下方，错误已在上文记录）、`...-map.png`（瓦片及 1 张范围照片）、`...-viewer.png`（合成照片详情）。截图文件尺寸为 470×740；页面 CSS viewport 读数为 470×764。截图通过 CUA screenshot API 捕获，并经项目内 loopback 接收器保存及 PNG 转换。短期接收器 `.tmp/P4-UI2-VISUAL-FINAL-A/screenshot_receiver.py` 与产品服务在本轮结束时停止。

第一次尝试的可见范围（历史）：

- `/`：隔离 10 张照片时间轴、日期待就绪分组与照片卡已观察；这是本轮唯一数据 API 正常的页面。
- `/search`：唯一页头查询、条件面板、高级筛选控件和 session 失效提示可见；FILTER_ONLY 结果、chips/空态和预览未观察。
- `/persons`：人物/合集/整理入口、筛选、批量管理层级可见；人物目录和相册数据未加载。
- `/places`：目录、相册、建议/批量整理、操作日志入口可见；地点成员和相册数据未加载。
- `/map`：地图容器、照片栏标题和排序控件可见；地图摘要因 session 失效，未验证瓦片和照片栏成员。
- `/?view=sources`：管理入口、维护折叠区和状态提示可见；来源数据未加载。
- viewer：由于离开首页后时间轴 API 401、照片卡消失，无法在本轮打开图片；外观、关闭按钮、Escape 和长图状态 pending。

第一次尝试的窄屏输出仅有渲染图尺寸。重试中 CSS viewport 已读取为 470×764；导航换行且主要控件可达，无横向溢出。系统 dark preference 当前为 true，暗色正常渲染；未切换系统主题做对照。一次真实浏览器缩放快捷键尝试未改变指标。

## 指标状态

| 指标 | 状态与证据 |
|---|---|
| `VISUAL_CODE` | **PASS**：token/作用域/资源版本静态核对与定向 UI JUnit 通过 |
| `BUILTIN_BROWSER_CORE` | **PARTIAL**：主要页面与 API 可访问，地图和 viewer 可交互；search query 因无 active profile 返回 422，人物/地点无对应数据 |
| `BUILTIN_BROWSER_NARROW` | **PASS（470×764 CSS viewport）**：多页实看且 `scrollWidth=470`，未见横向溢出；不代表 200% zoom |
| `BUILTIN_BROWSER_DARK` | **PARTIAL**：系统 preference 实测 dark=true 且产品暗色渲染；未切换至 light 做对照 |
| `ZOOM_200_NATIVE` | **PENDING_TOOL_LIMIT**：键盘放大尝试未改变 viewport/DPR，真实页面 Zoom 200% 未证实 |
| `ZOOM_LAYOUT_PROXY` | **PARTIAL**：窄面板观察与媒体查询静态检查；不能替代完整 CSS viewport 压力验收或真实 Zoom |
| `SCREENSHOT_FILE` | **PASS**：4 张 CUA 内置浏览器实拍 PNG 已保存在 `docs/reports/artifacts/P4-UI2-VISUAL-FINAL-A-*.png` |
| `MAP_TILES` | **PASS（瓦片可见）**：实际地图瓦片渲染，平移后 1 张范围照片；瓦片不可用回退未测 |
| `FUNCTIONAL_REGRESSION` | **PARTIAL**：定向 JUnit 9 passed；viewer open/Escape、地图照片往返通过；search query 422 无有效 profile 结果 |
| `PERF_200K / CHECK_300K` | **PENDING / 未执行**：本卡不测，既有任务状态不变 |

## 验证

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_ui_shell.py `
  tests/unit/test_p4_ui_search_entry.py `
  tests/unit/test_photo_view_info_ui.py `
  --basetemp=.pytest-tmp/P4-UI2-VISUAL-FINAL-A `
  --junitxml=test-results/P4-UI2-VISUAL-FINAL-A.xml
git diff --check
```

- JUnit：`test-results/P4-UI2-VISUAL-FINAL-A.xml`，**9 passed / 0 failed / 0 errors / 0 skipped**。
- 首次测试运行发现 map 脚本版本断言落后于当前 SEARCH-MAP 页面，以及共享 viewer CSS 断言仍期望旧版本；将断言对齐真实版本后完整重跑通过。FLOW-A 的 search.js 改动、测试和报告均保留。
- 本卡只修改 CSS/HTML/静态 UI 断言，未修改 JavaScript 或 Python，故未运行 Node、Ruff、py_compile。
- 仅运行任务卡限定测试，没有执行全仓、模型/GPU、性能、真实图库或打包验证。

## 复核与交接

初次 LUNA High 复核指出 `.photo-timeline__controls` 和控件仍残留 `--radius-card` / `--radius-control` 回退 token（P2）。已将面板圆角统一为 `--radius-lg`、控件统一为 `--radius`，移除 restore 控件的硬编码圆角回退，并在 `test_p4_ui_shell.py` 增加断言。修复后同一 LUNA High 对面板、控件、卡片、restore 控件及断言复核，无剩余可执行 finding（不是 DS-H）。改动仍限展示层，没有修改页面 ID、JS/API、搜索/地点协议或查看器状态机；`search.html` 原有 FLOW-A 脚本版本改动保留。

本轮不提交。没有读取、扫描或更改真实照片库。核心浏览器页面与 viewer 已实际观察，四张截图已保存；search profile 的有效结果、人物/地点成员数据、真实 200% Zoom 和主题切换对照仍 pending。整卡保持 ACCEPTANCE PARTIAL；不要以视觉代码、空数据或 demo 观感替代这些门禁。
