# P4-UI2-VIEWER-UNIFY-A — UI 2.0 全场景照片查看器统一、缩放与返回闭环

- **状态**：PLANNED / 待实施（本卡仅为设计与执行指令，不代表已修改产品）
- **优先级**：P1；错图、无法关闭、丢失原筛选/权限/人工写入安全等实际阻断按 P0 处理
- **规划基线**：`szzeng/aisearch` / `master`，编卡 HEAD `da2111076547f314a0beb5b9598a4c0c45583d1f`（2026-10-14）；执行前核对最新 HEAD、工作树和并行卡片，不把基线写死为施工版本
- **推荐执行模型**：GPT-6 High（跨五页、历史导航、异步与人脸扩展）；按仓库 V3.1 `2.4 做完整受影响展示/状态面复核，触及会话/权限/成员/写操作协议才升级相应受影响面复核
- **验收**：个人使用的最小定向行为测试 + 必要的 Codex 内置产品浏览器验证；不跑全仓、模型/GPU、真实图库、20万/30万整库性能
- **原则**：**一个真实查看器容器 + 各页面有限 adapter + 兼容旧深链**。统一照片查看，不重写时间轴、搜索、人物整理和地图

## 1. 唯一目标与成功体验

用户在 UI 2.0 的“照片”普通网格/时间轴/独立来源、搜索结果、人物或人物合集、地点相册、地图照片列表，以及现有整张照片查看入口点击照片时，都看到**外观和核心操作一致的近全屏模态查看器**。弹窗里直接查看图片、缩放/平移、照片信息、文件位置和原图操作；关闭后继续浏览刚才的位置。

- 不再发生“缩略图弹窗、同卡的`查看详情`却跳转到另一套独立详情界面”。
- **保留完整业务能力**：现有路径/多位置/分辨率/时间来源/有效 GPS/人物入口/系统打开原图/文件夹定位；人物页人脸更正、原图细节识别、识别更新待确认等入口不得消失。
- 保留历史 `/#/asset/{id}` 深链和从搜索/地图/人物跳转后返回的合法语义；**不用简单删除 `view-detail` 或无条件调用 `history.back()` 充当统一方案**。
- 只改前端查看器和必要接线，不改变资产 ID、页面成员资格、搜索相关度与会话、时间规则、地图 bbox、权限或写操作。
- 依照现行 [UI 2.0 视觉规范](../plans/P4-UI-VISUAL-TOKENS.md) 和 [用户确认的最终演示](../mockups/ui2-interactive/demo.html) 的观感；**新交互按本卡合同优先**。演示是视觉参考，不应复制模拟数据/假路由。

## 2. 前置依赖、已核对事实与任务边界

**开工阅读顺序**：`AGENTS.md` → `docs/development-status.md` → 本卡 → 最新 [VIEWER-F 报告](../reports/P4-UI-VIEWER-F.md) / [UI2-CLOSEOUT-FLOW-A 报告](../reports/P4-UI2-CLOSEOUT-FLOW-A.md) → [视觉收口报告](../reports/P4-UI2-VISUAL-FINAL-A.md) → 直接相关源码和测试。按需阅读 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) `3.2/`4/`6.1 和旧 [P4-PHOTO-VIEW-A](P4-PHOTO-VIEW-A.md)；不要求重读全部历史文档。

| 实际实现 | 这张卡的职责 |
| --- | --- |
| `web/app.js` 使用 `showDetail/renderDetail` 与 `/#/asset/{id}`；`drawDetail` 已有 `+ / − / 适合窗口 / 实际大小`（约 0.1–8 倍） | 从独立详情中提取可复用缩放行为；普通点击改共享弹窗；旧链接仍兼容 |
| `web/photo-info.js` 有 `AIPhotoInfo.render()`（共享信息）与 `AIPhotoInfo.open()`（地点已用的简单弹窗） | **以现有共享层演进**为统一 viewer，集中生命周期、图片舞台、缩放、关闭、信息，避免再造第二套全局组件 |
| `web/search.js/search.html` 的 `#search-preview` 保留搜索 rank、候选分页、人物跳转 | 列表所有业务状态留在搜索页，只将 viewer 容器/通用行为迁移到共享层；“查看详情”与缩略图一致 |
| `web/persons-albums.js/persons.html` 的 `#pa-preview` 含上下张、人脸标记/更正/补识别/待确认入口 | 通过受限扩展槽/回调保留专属动作，不把写入状态机搬到 viewer |
| `web/places-albums.js` 已调用 `AIPhotoInfo.open`；`web/map.js` `openAsset` 跳转旧详情 | 地点先做共享 viewer 基线；地图直接弹窗且保持视野、成员与排序绑定 |
| `photo-timeline.js` 只通过 adapter `onOpen(item, card)` 通知父页面，虚拟页/游标属于 timeline | 不改共享时间轴分页/回收算法，只让各父页面打开 viewer 并保留既有锚点 |
| `media/contract.py` 规定 thumbnail 长边 320、preview 长边 1600 | 缩放对象仍是**加载的预览产物**；“预览 100%”不是原图 RAW/JPEG 像素 1:1 |
| `P4-UI2-SEARCH-SCOPE-UNIFY-A` 为已提交的**待实施**任务，`P4-UI2-BATCH-SELECT-UNIFY-A` 处理 Shift 多选 | **按顺序实施、不并行改重叠文件**；本卡不改变页头全库/当前范围搜索按钮或照片多选语义 |

**任务顺序**：若搜索栏统一、批量选择或视觉收口仍有未合入的同文件改动，先确认其最新提交/工作树边界，避免覆盖。旧 `VIEWER-F`、`FLOW-A` 的 PARTIAL/PENDING 记录继续有效；只对本卡实际改变或补验的闭环更新证据，不自动将旧卡标 PASS。

## 3. 产品交互合同：一个 viewer，多种受限入口

### 3.1 布局与信息层次

- 使用原生 `<dialog>` 模态语义或等效的**单一可访问模态实例**。桌面尽量接近可用视口，图片是视觉主体；顶部显示真实文件名、信息栏开关、关闭按钮；底部/舞台边缘显示照片切换及缩放工具，不能遮住主体。
- 图片默认 `contain`、**完全适窗不裁切**。右侧原共享 `photo-info-panel` 默认可见、可折叠；窄屏改为下方可收纳信息区，并保证图片舞台可视。
- 文件名来自当前资产真实可用位置（PRESENT 优先、稳定兜底），不出现 `capture` 等内部时间键作为标题；路径不能偷换成“唯一原图位置”。缺字段保持可解释空态。
- 继承 UI2 token、浅/暗主题与现有合理留白，避免在 `search.css` / `persons.css` / `map.css` 加不带根作用域的全局样式。
- 保留旧独立详情里的重要高级资料（如资产身份/内容指纹/入库时间/位置记录）为**可折叠的当前照片高级信息**，无需主舞台长期展示，不得因删除旧详情 DOM 永久失去这些只读内容。

### 3.2 缩放、平移与输入

| 用户操作 | 必须效果 |
| --- | --- |
| 打开或切换照片 | **适合窗口**；以当前图片解码后的 upright 像素计算；加载中禁用不适用的缩放控件 |
| `+` / `−` | 沿用约 1.25 倍增减，安全上下限约 10%–800%，比例显示准确，防止 NaN/0/无限变换 |
| `适合窗口` | 图片全幅可见；窗口/信息栏改变时若仍处 fit 模式自动重算，避免被切掉 |
| `预览 100%` | 每个预览像素对应约一个 CSS 像素；**不叫“原图实际 100%”**；以加载产物尺寸为准 |
| Ctrl + 鼠标滚轮 | 仅当指针在图片舞台且可缩放时，以指针附近为锚点缩放；阻止这一手势造成舞台滚动或误缩放浏览器，其余区域保持原滚动 |
| 双击图片 | 在适窗与预览 100% 两个状态间切换；按钮双击/人脸点击不受影响 |
| 放大后的拖动 | 鼠标指针拖动查看细节；仅可平移超出舞台的部分，边界夹紧；缩回适窗时位移清零 |
| 左/右方向键 | **仅**当前合法顺序内上一张/下一张；输入框、select、内容编辑、二级弹窗焦点下不能劫持 |
| Esc / 点击遮罩 / 点击关闭 | 按第 5 节关闭；遮罩判断真实目标，内部拖图、信息点击不关闭；编辑保存/确认中的子弹窗遵守原拦截 |
| 普通滚轮/触控窄屏 | 可正常滚动信息栏；不强制实现触控捏合，但按钮和触控拖动不能造成页面误操作 |

**像素语义**：当前生成预览最长边 1600px，`100%` 只能说明预览素材的像素比例。原图若是 6000×4000 或 CR2，不得放大 1600 预览后伪称原图细节；继续提供原有 `系统打开原图`。不新增完整原图 HTTP 流、后台 RAW 解码、分块高分辨率服务、媒体版本升级或新缓存目录。

**安全实现建议**：提炼 `app.js` 现有 `applyScale/fitPreview`，选择一种明确的尺寸/位移计算机制（避免 CSS width/height 与 transform 两套叠加），统一在图片加载成功、ResizeObserver/resize 和信息栏开闭时更新；缩放时保留稳定锚点，pan 只在溢出区间内有效。此为实现建议，最终以行为合同判定。

### 3.3 图片装载和错误

- 优先现有 `/assets/{id}/preview`，失败时回退 `thumbnail` 并提示“当前为缩略图”，两者失败显示清楚错误且允许关闭/切换；不得显示上一张残影。
- 仅在**打开当前照片**时读取必要的详情/人员/位置；列表首屏不得因为接入 viewer 产生逐卡详情、GPS、来源根路径或原图解码 N+1。
- 每次切图绑定唯一 request generation/asset ID；A→B→关闭后 A 的迟到响应不得复活图片、人物、地图或标题。
- 清理当前 `AIPhotoInfo.render` mount、MapLibre 实例、事件监听、图片引用/临时 URL、焦点陷阱/页面滚动锁。关闭前正在加载也可安全退出。避免重复 `showModal` / 多重 overlay。

### 3.4 共享信息与页面专属动作

统一复用 `AIPhotoInfo.render`：完整路径/复制、分辨率/格式/大小、拍摄 vs 文件时间、多个文件位置及可用性、有效 GPS/地图、canonical 人物头像、系统打开原图/定位文件、识别更新提示。

- 搜索结果原自建人物列表应与共享信息的“照片中的人物”去重，不允许出现两套矛盾头像/链接；如需要搜索 rank、命中原因、结果位置，作为搜索只读副信息展示。
- 人物相册保留当前照片人脸标记、`尝试原图细节识别`、`查看识别更新待确认` 等页面私有操作；原确认、撤销、写入 busy 与阻止关闭策略由人物模块控制。不能在共享 viewer 偷偷执行写 API。
- 原有照片详情的 `更正这张照片中的人脸`、`在地图相册中查看` 等跨页入口继续可用。跳人物/地图后返回按第 5 节恢复上下文，不能通过猜测名称或旧资源索引拼接范围。

## 4. 页面接入矩阵（按已实现行为迁移）

| 入口 | 点击与查看目标 | 序列/业务保护 |
| --- | --- | --- |
| 首页普通网格 | 照片按钮直接打开共享弹窗；旧 `查看详情` 不另开不同 UI | 当前 source、列表排序/筛选、已加载分页与滚动；没有真实相邻列表时不假装全库上下张 |
| 首页全部/来源时间轴 | 保留 `PhotoTimeline` 的 `onOpen`，只替换打开动作 | time_basis、group_by、年/月、unknown/pending 桶、revision、已回收页面锚点；不得调用所有资产 ID |
| 搜索结果普通与时间浏览 | 缩略图及 `查看详情` 统一进入共享弹窗 | 当前 q、固定 scope chip、原 rank/排序、session、cursor、结果恢复与无效快照处理；搜索请求/语义候选不得被 viewer 重新执行 |
| 人物相册（时间轴/旧列表） | 当前 asset 弹窗；可切当前人物有效照片 | 以 canonical person/scope 为准；仅沿现有有界 `photoIds` / cursor 继续，不因退出目录或人物合并串范围 |
| 人物合集 | 同一弹窗且不重复显示同一照片 | 绑定 collection_id / instance_token / revision；成员变化拒绝旧续页，保留原补救提示 |
| 地点相册 | 以已用 `AIPhotoInfo.open` 为基础增加统一工具区 | 只在地点合法 scope 内；地点更正和原时间轴不被弹窗事件吞掉 |
| 地图照片网格/有效单点 | 点击图片/查看照片直接在地图上弹出，而非先强制跳首页详情 | 保留 center/zoom/bbox/member_version/排序/聚合列表；缩放地图后的成员变化废弃旧序列 |
| 人脸整理及已有“查看整张照片” | 查看动作只打开对应 asset 整图 | face_id / person_id / asset_id 不能混为一谈；勾选、Shift、封面、更正和管理 Esc 不被查看器绑定覆盖 |

**谨慎迁移上下张**：搜索及人物已有的上一张/下一张必须迁移且保留跨已加载页和合法续页。其他页面仅使用现成有序已加载资产或服务端既有 cursor；没可靠相邻项时隐藏/禁用，不为了“统一”发明新全库排序或把当前地点/人物扩成全库。分页发生 revision 变化，清理旧导航并提示刷新，绝不使用缓存旧资产继续翻页。

**事件边界**：不可用 `document.querySelectorAll("img")` 全局劫持；时间轴 card / 子 button / 复选框 / link 不得双触发。选择/整理模式保持它自己的点击语义；只在明确“查看照片”控件打开 viewer。

## 5. 浏览器历史、关闭和来源状态（主要风险）

先画出真实 `hashchange`、`popstate`、`sessionStorage`、`window.location.href` 和旧 `detail-back` 的事件顺序，再以**最小可回退适配**实现；不能仅因新模态而抹掉现有 FLOW-A 的修订校验和 FILTER_ONLY 合法回查。

必须明确区分：

1. **普通从当前页打开**：关闭按钮、Esc、遮罩关闭后停留在当前页面、当前 scroll / scope / 焦点。若为实现“浏览器 Back 关闭弹窗”推入历史标记，标记只属该次 viewer，不重复压栈；关闭后历史也要清理，不留下按 Back 才空白的步骤。
2. **直接加载旧 `/#/asset/123` 深链**：应能用同一 viewer 显示指定 asset（含刷新/复制链接）；没有可确认的本应用返回目标时关闭安全落到照片主页，**不盲目 `history.back()` 离开站点**。保留原有搜索或地图明确来源时回原页面。
3. **搜索→照片→人物→浏览器 Back**：合法情况下恢复搜索词、scope、经过验证的结果/第一页与滚动；FILTER_ONLY 按 FLOW-A 现有权威请求恢复，不把旧 session JSON 当权威；真实失效时保留条件并提示重搜。
4. **地图→照片→关闭/返回**：尽量直接保留页面上的地图，不重新创建 map；旧链接路径仍使用原 `map-return` 有效校验。bbox 或 member_version 实际变化时丢弃旧成员，不因查看器拷贝旧 viewport。
5. **时间轴→照片→关闭**：当前 DOM 未回收则聚焦原卡；已回收则复用现有 asset/anchor 恢复能力并聚焦有效替代；不得为保证返回而缓存30页缩略图 DOM。
6. **编辑/二级确认弹窗**：仅最上层 Escape/Back/遮罩事件可响应，写操作忙时保持现有保护；查看器退出不误触发人物“退出批量管理”。

建议定义小范围 viewer lifecycle（closed/open/transitioning、assetId、origin/entryKey、currentGeneration、historyOwned），仅供受影响页面 adapter 使用。禁止引入全站路由状态总线、通用导航框架或持久化全相册资产 ID。

**状态边界**：viewer 只持有当前照片与来源适配器；scope/version、结果 session/cursor、地图 bbox、时间轴日期、人工选中项仍由原页面/服务端权威管理。任何失效/失败不得静默“恢复成全库”，也不得把真实错误隐藏在统一 toast 里。

## 6. 允许修改、禁止扩展与冲突管理

**直接涉及**：`src/photo_search/web/photo-info.js`、`photo-info.css`、`app.js`、`index.html`、`search.js/html/css`、`persons-albums.js/html/css`、`places-albums.js/html/css`、`map.js/html/css`；只在确有必要时增加**一个轻量 viewer 文件**并按现有 `api/web.py` 静态资源白名单/打包路径核对；`photo-timeline.js` 原则不改，只复用 `onOpen`。

若近期 `P4-UI2-SEARCH-SCOPE-UNIFY-A` 已修改 `search.js/persons-albums.js/places-albums.js`，按最新交接 API 合并当前入口，**不回退其顶部搜索框旁“搜索此…”直接执行**行为。若 `P4-UI2-BATCH-SELECT-UNIFY-A` 改了勾选和 Shift，原样保留；查看器只接明确预览动作。可复用 `UI2-VISUAL-FINAL-A` 的样式、缓存版本与内置浏览器验证环境；只更新受影响资源引用。

**禁止**：

- 改照片数据库/schema/migration、assets/session/权限/CSRF 合同、扫描/建库、原图/media processor ID、模型/向量、搜索召回/排序、时间轴 SQL 或地图权威成员。
- 一次加载人物/地点/全库所有 `asset_id`；在列表首屏逐图请求详情/GPS/根目录；预览时强制重新解析原始 RAW/CR2。
- 删除旧详情兼容链接、丢弃人物专属操作、擅自打开本机文件、写入用户真实 `PhotoSearchData`。
- 重绘 UI2 全站主题、重新处理搜索页头或为普通纯浏览页新增批量选择功能。
- 为解决验收限制使用外部浏览器驱动、绕过产品认证、记录一次性会话 token。

若确需更改公共 session/revision/cursor、人工写语义或新增高分辨率原图 API，停止扩范围，记录问题和独立任务建议；不能把它们偷偷合进本前端卡。

## 7. 最少但有判定力的测试

**小型合成样本**：复用项目已有 10–40 张隔离照片/人员/地点/GPS fixture（及小型多页元数据夹具），不使用真实个人图库；新增场景优先补最少数据。测试基本操作而不是重复全量 UI2 验收。关键用例：

| ID | 流程 | 完成判定 |
| --- | --- | --- |
| V01 | 首页普通网格、来源、时间轴各点击一张→关闭/Esc | 统一弹窗、asset 正确、各只打开一次；日期/来源/滚动/焦点恢复 |
| V02 | 搜索结果点击图及`查看详情`→切下一张→关闭 | 同一个容器，正确 rank/成员、scope chip/q/结果和合法候选分页不丢 |
| V03 | 人物/合集打开→切图→进入人脸操作→退出 | 人脸标记、补识别与复核入口保留；写保护、合照单脸语义与合集去重未退化 |
| V04 | 地点相册→预览→关闭；地图单点/网格→预览→关闭 | 地点范围无串联；地图 center/zoom/bbox/member/version/排序按原机制保留 |
| V05 | 适窗、放大/缩小、预览100%、双击、Ctrl+滚轮、拖动、窗口改变 | 无裁切/无错比例/无越界 pan；预览100%不声称原图1:1 |
| V06 | Esc、关闭、遮罩、按浏览器 Back、二级对话框/文本输入焦点 | 最上层事件正确、无两次关闭/空白历史/离站、焦点返回 |
| V07 | A→B 快切；A 慢速详情/GPS/人脸响应随后到达；关窗时仍有请求 | 不串图、不复活旧地图或信息、不遗留监听/滚动锁 |
| V08 | 预览/缩略图失败、原图离线、长竖图、全景和窄屏 | 正确回退与信息，长图完整适窗，缺资源也能退出 |
| V09 | 合集 revision / search session / 地图 bbox 变化后尝试续页或返回 | 丢弃旧候选/游标/地图成员；保留合法条件并给明确纠正入口 |
| V10 | 旧 `/#/asset/{id}` 直接访问、刷新、搜索/地图旧链接、Back | 同 viewer，合法返回；未知来路关闭安全，不依赖外域历史 |
| V11 | 人物勾选/Shift、照片封面/更正、时间轴子按钮 | 查看操作不改变选中项，不产生双触发或意外写 API |

**最小自动验证**：

- 优先更新 `tests/unit/test_photo_view_info_ui.py`，必要时新增 `tests/unit/test_p4_ui2_viewer_unify.py`；复用直接受影响的搜索/人物/地点/地图现有测试，不要求一口气重跑全部 UI2 用例。
- 核心交互至少以现有合成 DOM + JS 事件/状态 harness 覆盖 V05/V06/V07、旧深链行为与序列失效；**仅用字符串断言某按钮存在不够**。测试不能支持的纯产品浏览器路径保留待验。
- 参考执行（按实际修改挑选受影响文件并记录准确结果）：

~~~powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_p4_place_map_review_ui.py --basetemp=.pytest-tmp/P4-UI2-VIEWER-UNIFY-A --junitxml=test-results/P4-UI2-VIEWER-UNIFY-A.xml
node --check src/photo_search/web/photo-info.js
node --check src/photo_search/web/app.js
node --check src/photo_search/web/search.js
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/map.js
git diff --check
~~~

新增 viewer JS、单测文件及实际改过的 JS 分别补 syntax check；如修改 Python 测试再运行对应 Ruff/编译。JUnit 必须读取 XML 核对真实通过数与失败数；不以退出日志缺失断言成功。

**产品浏览器**：遵循 `AGENTS.md` 已固化的本机流程：Codex 内置浏览器 + `127.0.0.1` + 项目内隔离合成数据 + 启动器一次性会话链接。至少走**首页时间轴查看/缩放/关闭、搜索结果切图/返回、人物专属查看、地点或 GPS 地图查看**四条短路线；小窗口/长竖图有样本则补一次。截图若支持导出，存到 `docs/reports/artifacts/`；不要为无法使用的缩放、主题切换或合成夹具反复折腾浏览器，分开记 `BROWSER_PENDING` / `FIXTURE_MISSING` / `TOOL_LIMIT`，不要造截图或把 DOM 桩记成产品验收。

**性能边界**：不重建20万/30万数据、不跑完整性能专项；用合成浏览器/Network/轻量断言核对“打开弹窗不让基础列表重取第一页”“未切图不加载整个相册”“关闭释放当前图片/地图”“列表没新增逐卡详情请求”。新增加了查询或媒体服务协议则不得沿用本卡轻验收，需另立专项。

## 8. 交付、验收状态与停工边界

- 新代码和必要的静态引用版本、定向自动测试与 `test-results/P4-UI2-VIEWER-UNIFY-A.xml`。
- 执行报告 `docs/reports/P4-UI2-VIEWER-UNIFY-A.md`：短设计、入口适配勾稽、实际文件 diff、测试命令与 JUnit 计数、浏览器真实动作/截图、深链/Back 结果、遗留问题；更新 `docs/development-status.md` 一段简洁摘要。
- 分列 `FUNCTIONAL`、`ZOOM_PAN`、`NAVIGATION_RETURN`、`PERSON_ACTION_SAFETY`、`BUILTIN_BROWSER`、`LEGACY_LINK`、`PERF_NOT_RETESTED`、`REVIEW`。**没有证据的门保持 PENDING**，不追认 VIEWER-F/FLOW-A/VISUAL-FINAL-A/SEARCH-SCOPE 原未通过项。
- **SCOPED PASS**：所有实际改变的入口共用 viewer，缩放/拖动与返回行为在定向测试中通过，人物写入接线未被破坏；适用的关键产品浏览器路径有证据时，单独记浏览器通过。浏览器工具能力不足时允许代码层 `FUNCTIONAL SCOPED PASS`，整体仍按实际必要门记录 `ACCEPTANCE PARTIAL`；**真正发生的错图/错误 scope/丢操作不允许借 TOOL_LIMIT 掩盖**。
- 完成后**停止本卡**；不自动继续其他任务、不扫描用户照片、不修改既有性能/视觉卡结论。最终提交仅限本卡实现/证据，不夹带未授权的其它工作树修改。
