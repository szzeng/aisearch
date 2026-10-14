## Current user-directed task: P4-UI2-PLACES-SIMPLIFY-A — IMPLEMENTED / BROWSER_PENDING (2026-10-14)

地点页默认仅浏览相册；主动进入整理工作区后可访问建议、地名目录/新建/编辑、高级维护及历史撤销；纠错操作已语义化且沿用原 CSRF/revision 写协议。定向 JUnit **15/0/0/0**、两份 JS `node --check`、`git diff --check` 通过。LUNA High 首轮两项 finding 已修复，repair-only 复核关闭，无新 finding（不是 DS-H）。按用户指示未运行浏览器验收，故不标整卡 PASS；未访问真实图库或执行性能/模型/GPU/打包检查。[任务卡](tasks/P4-UI2-PLACES-SIMPLIFY-A.md) / [报告](reports/P4-UI2-PLACES-SIMPLIFY-A.md)。

## Next task: P4-UI2-PLACES-SIMPLIFY-B — product browser acceptance

等用户发起后，按[任务卡](tasks/P4-UI2-PLACES-SIMPLIFY-B.md)在隔离合成数据和 Codex 内置浏览器执行两条路线，补地点浏览→整理→返回与浏览→详情→预览→返回的真实交互证据。当前明确 `BROWSER_PENDING`。

## Previous user-directed task: P4-PLACE-MAP-TIANDITU-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

天地图 `_w` 矢量/影像底图、图层切换、本机 Key 设置/API、详情小地图复用及无 Key/瓦片失败照片回退已实现。定向 JUnit **26/0/0/1 skipped**（Windows 符号链接权限不可用），Ruff、Node、compileall、diff-check 通过；Codex 内置浏览器 A/B 假 Key 和 2 张隔离合成 GPS 照片通过。Luna High repair-only 复核完成：runtime 恢复 finding 已修复并复看关闭，无剩余代码 finding。[任务卡](tasks/P4-PLACE-MAP-TIANDITU-A.md) / [报告](reports/P4-PLACE-MAP-TIANDITU-A.md)。真实天地图大陆网络与影像对齐 `PENDING / USER_MANUAL_CHECK`；Windows Key 文件继承 data-root ACL，用户拒绝 ACL 更改，故访问控制未验证且整卡不得 PASS。浏览器截图未能由 CUA 导出。未访问真实图库/Key，未跑全仓、压测、模型/GPU、打包/发布；按用户要求仅做本地提交，不推送。

## Unresolved prior acceptance: P4-PLACE-MAP-TIANDITU-A manual gates

用户在本机设置页录入自己的 Key 后，实测天地图标准与卫星图层的深圳等已知坐标覆盖和照片点对齐。Windows ACL 若需收紧，须重新取得明确授权；在此之前保持当前继承权限且记录 pending。
## Previous user-directed task: P4-UI2-SCROLL-LOAD-B — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

人物、合集目录、合集成员与现存普通照片网格已接独立游标自动续页；成员首屏失败提供重试；人物深页刷新最多恢复三页。定向 JUnit **24/0/0/0**、`node --check`、`git diff --check` 通过。[任务卡](P4-UI2-SCROLL-LOAD-B.md) / [报告](reports/P4-UI2-SCROLL-LOAD-B.md)。隔离合成浏览器实走人物两批及 Shift、95 个合集和 85 个成员分页；截图 pending。Luna High 关闭首屏重试 finding，仍有 P1 未解决：目录 DOM 随加载页增长无界，故 `PERF_10K` 与整卡验收 partial。未访问真实图库/模型/GPU、未跑全仓/打包/发布。

## Pending prior acceptance: P4-UI2-SCROLL-LOAD-B bounded DOM and remaining browser gates

先定义兼容已加载范围选择的目录回收/虚拟化策略并补行为证据；再按报告补未命名/隐藏目录、折叠/切换停载、续页失败及普通网格第二页浏览器路线，若可导出则保存截图。不得把当前 125/95/85 合成规模外推为 10K 性能通过。

## Current user-directed task: P4-UI2-PLACE-ALBUM-NAV-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

地点相册列表/详情分离，接入精确深链与 Back/Forward，保留轻量列表/时间轴恢复，并避免更正刷新重建导航。revision 过期时回查当前 scope 权威第一页并提示。定向 JUnit **34/0/0/0**；三份 JS `node --check` 和 `git diff --check` 通过。[任务卡](tasks/P4-UI2-PLACE-ALBUM-NAV-A.md) / [报告](reports/P4-UI2-PLACE-ALBUM-NAV-A.md)。Luna High 全面限域复核发现的快照 revision 问题已修复；两轮 repair-only 复核关闭 finding，无剩余项（不是 DS-H）。用户要求不做浏览器测试，`BROWSER_NOT_RUN_USER_REQUEST`，所以整卡保持 **ACCEPTANCE PARTIAL**，没有真实浏览器交互、截图或视觉 PASS。未访问真实图库；未跑全仓、`PERF_200K/CHECK_300K`、模型/GPU、打包或发布检查。

## Pending acceptance: P4-UI2-PLACE-ALBUM-NAV-A browser evidence

如后续继续关闭整卡，按任务卡使用隔离合成地点数据补列表→详情→返回、深链 Back/Forward 与预览关闭路径，并记录浏览器/截图/视觉状态。此轮按用户要求跳过该门禁；范围及当前代码证据见报告。

## Additional user-directed task: P4-SEARCH-PLACE-INTENT-CHIP-FIX-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

地点词“太阳”仅在明确拍摄地点语境形成地点筛选；相同地点身份只显示一个条件 chip；取消会使旧状态失效且不自动全库重搜。任务卡定向 JUnit **98/0/0/0**，Ruff、Node、Python 编译及 diff-check 通过。[任务卡](tasks/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A.md) / [报告](reports/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A.md)。隔离合成浏览器验证东京固定地点显示单 chip、取消后无自动请求；`太阳` 浏览器查询因 fixture 缺少 active search profile 返回 VALIDATION_ERROR，故 `BROWSER_SOLAR_QUERY=PENDING`。Luna High 独立复核发现的旧请求迟到断言缺口已补充，复看关闭，无新 finding。未触碰真实图库；未跑全仓、性能、模型/GPU、打包或发布检查。

## Previous user-directed task: P4-UI2-SCROLL-LOAD-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

搜索和普通照片列表已接入互斥自动续页与 500 卡 DOM 回收。定向 JUnit **38/0/0/0**，Node `--check`、`git diff --check` 通过；最终版本隔离合成浏览器观察搜索 20→40、普通列表 50→87 并到底。用户指定的 LUNA High 复核已完成，所有 findings 修复并复看关闭（不是 DS-H）。A03/A04/A08、session/revision stale、长滚动锚点与 20–30 页浏览器证据仍未全覆盖，故整卡 **ACCEPTANCE PARTIAL**。未使用模型/真实图库，`PERF_200K` 与 `CHECK_300K` pending。详见[任务卡](tasks/P4-UI2-SCROLL-LOAD-A.md) / [报告](reports/P4-UI2-SCROLL-LOAD-A.md)。

## Next necessary action: P4-UI2-SCROLL-LOAD-A remaining acceptance gates

按报告逐项补足 A01–A08 缺口，优先补查询代际/乱序与 stale 保护、候选时间/语义路径、20–30 页浏览器回收/返回，以及页大小/键盘边界；继续使用隔离合成库，不升级为 `PERF_200K/CHECK_300K` 通过。
## Previous executed task: P4-UI2-VIEWER-UNIFY-A — FUNCTIONAL SCOPED PASS / ACCEPTANCE PARTIAL (2026-10-14)

UI 2.0 首页、搜索、人物/合集、地图复用单一共享照片查看器；实现预览缩放/平移、受限页面扩展、上下张导航和安全返回，并保留旧 `/#/asset/{id}` 深链。定向 JUnit **21/0/0/0**；六份相关 JS `node --check` 与 `git diff --check` 通过。[任务卡](tasks/P4-UI2-VIEWER-UNIFY-A.md) / [报告](reports/P4-UI2-VIEWER-UNIFY-A.md)。LUNA High 独立复核的 3 个 finding 已修复并复看无剩余项（不是 DS-H）。内置浏览器用 10 张隔离合成图库观察首页缩放/Back、人物查看、FILTER_ONLY 搜索返回、GPS 地图返回及旧深链关闭；截图见报告。`FUNCTIONAL=SCOPED PASS`，但整卡 **ACCEPTANCE PARTIAL**：有效语义搜索、地图/地点全路由、写入实际提交、长图/失败资源/异步快切、真实 200%/主题及 `PERF_200K` 未完整验证。未访问真实图库或模型/GPU；按后续用户指令本地提交；未 push。

## Next necessary action: P4-UI2-VIEWER-UNIFY-A remaining acceptance gates

按报告补验地点相册独立返回、搜索连续翻页/有效语义结果、人物写操作及拦截、资源失败/长图/异步快切、多层返回与实际 200%/主题；`PERF_200K` 按需要由独立性能卡承担。不要用本轮的合成/窄视口证据替代这些门禁，也不改变前置卡状态。
## Current user-directed task: P4-UI2-SEARCH-SCOPE-UNIFY-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

统一页头范围搜索入口、关键词一次性交接和取消范围状态；人物固定范围取消会恢复原 `person_match`，地点回显条件取消时清理重复过滤。定向 JUnit **28/0/0/0**、Node `--check`、`py_compile` 与 `git diff --check` 通过；LUNA High 最终修复复核无剩余可执行 finding（不是 DS-H）。按用户追加要求完成一次本机 Codex 内置浏览器地点范围冒烟：隔离合成图库 4 张照片，地点 CTA 直接执行 FILTER_ONLY，取消后不自动全库重搜。仅这一条路线已浏览器验证；人物/合集/图库、非空词、取消竞态和预览返回等保持待验，截图未保存，整卡保持 ACCEPTANCE PARTIAL。[任务卡](tasks/P4-UI2-SEARCH-SCOPE-UNIFY-A.md) / [报告](reports/P4-UI2-SEARCH-SCOPE-UNIFY-A.md)。未访问真实图库、模型/GPU，未跑全仓、性能、真实 200% 缩放、打包或发布验证；无 commit。

## Next necessary action: P4-UI2-SEARCH-SCOPE-UNIFY-A remaining browser gates

如继续关闭整卡，按任务卡补齐人物 CTA/Enter、合集照片与成员 tab、图库来源 CTA、非空词、范围分页/预览返回、图片搜索屏蔽、失效 token 和必要的窄屏/主题路线；继续使用隔离合成数据，不能用本次地点 FILTER_ONLY 冒烟替代这些验收。

## Previous user-directed task: P4-UI2-BATCH-SELECT-UNIFY-A — SCOPED PASS / BROWSER NOT RUN BY REQUEST (2026-10-14)

地点建议按已加载顺序支持 `asset_id` Shift 连选、全选/反选/清空；人物合集成员改用原生 checkbox，维持独立人物详情按钮和按 `person_id` 原子移出。既有人物列表、人脸、待整理/已忽略候选组选择回归通过；选择不触发写 API。定向 JUnit **23/0/0/0**，两份 JS `node --check` 与 `git diff --check` 通过。[任务卡](tasks/P4-UI2-BATCH-SELECT-UNIFY-A.md) / [报告](reports/P4-UI2-BATCH-SELECT-UNIFY-A.md)。用户要求跳过浏览器，状态 `BROWSER_NOT_RUN_USER_REQUEST`；不声明浏览器/目视 PASS。未跑全仓、真实图库、模型/GPU、性能或打包检查；无后端协议改动。

## Previous user-directed task: P4-UI2-TIMELINE-CONTROLS-FIX-A — IMPLEMENTED / BROWSER PENDING (2026-10-14)

修复时间轴下列表专属控件被 `display:flex` 覆盖 `hidden` 的问题，并将日期说明整体隐藏以消除空白；列表切回仍沿用现有模式逻辑。定向 JUnit **8/0/0/0**、`git diff --check` 通过。[任务卡](tasks/P4-UI2-TIMELINE-CONTROLS-FIX-A.md) / [报告](reports/P4-UI2-TIMELINE-CONTROLS-FIX-A.md)。按用户要求跳过浏览器验证，故首次显示无闪现、列表往返恢复及桌面/窄屏交互仍 `BROWSER_PENDING`，整卡不标 PASS。未访问真实图库、模型/GPU；未做全仓或性能验证。
## Current user-directed task: P4-UI2-VISUAL-FINAL-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

视觉代码统一已完成：公共侧栏/照片网格密度、共享卡片 token、查看器信息面板、地点面板和地图高度规则小幅调整；受影响 CSS 页面同步 cache-bust。定向 JUnit **9/0/0/0**、diff-check 通过。[任务卡](tasks/P4-UI2-VISUAL-FINAL-A.md) / [报告](reports/P4-UI2-VISUAL-FINAL-A.md)。本次按用户要求重试了本机浏览器验收：一次性会话在首页、地点、地图、人物、搜索、管理各页均有效，相关页面 API 未复现 401。首页 10 张合成照片、地图瓦片与 pan 后 1 张范围照片、合成 viewer 及 Escape/地图详情返回均观察通过。搜索查询因 fixture 无 active search profile 返回 422；人物 0 人、地点包未安装为有效空状态。内置浏览器真实截图 4 张已存 `reports/artifacts/P4-UI2-VISUAL-FINAL-A-*.png`。CSS viewport 470×764 且未见横向溢出；当前 system dark=true，但未切换 light；Ctrl+plus 未改变 viewport，真实 Zoom 200% 仍 pending。LUNA High 修复复核无剩余 finding（不是 DS-H）；`PERF_200K/CHECK_300K` 未按本卡运行，真实图库/模型/GPU未访问；整卡 ACCEPTANCE PARTIAL，无 commit。

人物合集相册标题徽标文字溢出已按用户截图局部修正，并更新 `persons.css` 缓存版本。新会话在 Codex 内置浏览器通过认证，但隔离图库没有人物/合集，创建合集所需 prompt 未弹出；该徽标目视复核仍 pending，详见报告。

## Next necessary action: P4-UI2-VISUAL-FINAL-A remaining visual gates

LUNA High 修复复核、核心页面浏览器走查及截图保存已完成。若以后需要进一步关闭整卡，仍需可用 search profile 下的搜索结果、地点成员 fixture、真实 Zoom 200% 和主题切换对照；按本卡边界继续将 `PERF_200K/CHECK_300K` 保持继承状态，不以窄视口代替 Zoom。

## Previous user-directed task: P4-UI2-CLOSEOUT-FLOW-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

FILTER_ONLY/有效地点 scope 返回时按原条件重新请求权威首屏；零结果也保存导航快照，错误时保留条件并清理不可信结果。定向 JUnit **26/0/0/0**，Node、Ruff、Python 编译、diff-check 通过；LUNA High 无可执行 finding（不是 DS-H）。本轮按 P4-UI-PLACES-E 本机流程，用应用生成的一次性会话链接在 Codex 内置浏览器复验东京 4 张地点 scope、明确全局重搜、合集成员变化后拒绝旧快照、首页 Escape 与第二页返回、GPS bbox 变化/详情往返。浏览器截图在 `reports/artifacts/`，具体观察和 F01–F10 子项见[报告](reports/P4-UI2-CLOSEOUT-FLOW-A.md)。F04–F09 仍有子路线待验，特别是 F07 race、session 不可验证返回、人物 scope 取消、地点包/时间规则变化和日期轴跨桶；整卡保持 ACCEPTANCE PARTIAL。`SEMANTIC_BROWSER=PENDING`、`PERF_200K=PENDING`；视觉、真实图库、模型/GPU、打包和全仓测试未执行。本轮临时服务已关闭，18821–18825 无监听；无 commit。

## Next necessary action: P4-UI2-CLOSEOUT-FLOW-A remains acceptance partial

如继续，先完成 F07 浏览器竞态与剩余 scope/session/stale/时间轴路线，再按实际证据判断 P0 关闭；无需重跑已通过定向 JUnit，也不启动性能或视觉专项。
# Development status

## Additional user-directed task: P4-UI-TIMELINE-CLICK-PATH-FIX-A — TASK-CARD MINIMUM ACCEPTANCE PASS (2026-10-14)

时间轴首页自定义卡片现沿用既有详情路由；当前页 API 元数据提供真实文件名/目录并按 PRESENT 优先、location_id 稳定次序选择实例，支持缺路径回退、两行省略与完整路径 tooltip。详情关闭按钮及 Escape 返回已通过本机 6 张合成照片浏览器验证。定向 JUnit **33/0/0/0**，Node、Python 编译及 diff-check 通过。[任务卡](tasks/P4-UI-TIMELINE-CLICK-PATH-FIX-A.md) / [报告](reports/P4-UI-TIMELINE-CLICK-PATH-FIX-A.md)。有限复查未专项浏览人物/地点产品点击或暗色主题；未做性能测试。未访问真实相册、模型、GPU；提交 `38ebbae`。VIEWER-F 后续状态见当前任务记录。

## Current user-directed task: P4-UI-VIEWER-F — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

共享照片信息面板、查看器关闭/焦点/缩略图回退、地图 bbox/time-rules 快照边界与搜索会话范围/时间修订验证已实现。定向 JUnit **21/0/0/0**；相关 Node、Ruff、diff-check 通过。[任务卡](tasks/P4-UI-VIEWER-F.md) / [报告](reports/P4-UI-VIEWER-F.md)。用户指定的 Luna High 受影响面复核及修复复核无剩余实现 finding；不是 DS-H。`BROWSER_INTERACTION=PARTIALLY_VERIFIED`：本机合成浏览器已检查查看器、人物范围搜索和地图/地点部分路径并保存截图；浏览器返回未恢复旧搜索候选，地点搜索返回 `CATALOG_FILTER_REVISION_STALE`，首页30页、bbox成员失效及长竖图/窄屏/离线原图仍待验。语义编码器不可用，搜索证据仅为 FILTER_ONLY。真实图库、性能、全仓和打包未做，整卡保持 partial。

## Next necessary action: P4-UI-VIEWER-F remains acceptance partial

报告已记录本机产品浏览器部分观察与截图；整卡还缺首页30页往返、有效搜索结果返回恢复、地点范围搜索、bbox成员失效、长竖图/窄屏/离线原图、遮罩关闭与快切资源释放。按实际证据继续保持 ACCEPTANCE PARTIAL；真实图库现场体验由用户日常试用反馈另行记录。

## Existing SEARCH-MAP acceptance status (unchanged)

P4-UI-TIMELINE-SEARCH-MAP-A — IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE (2026-10-14)

阶段一人物/合集/地点固定范围搜索的 API 合同、服务端 scope/revision/keyset 与时间条件已实现。定向 JUnit **72/0/0/0**；Ruff、Python 编译、`search.js`/`persons-albums.js`/`places-albums.js` Node 语法及 staged/unstaged diff-check 通过。Luna High 子代理指出两处 pending/time-choice 计划问题，已修复并复核无剩余 actionable findings；不是 DS-H。[任务卡](tasks/P4-UI-TIMELINE-SEARCH-MAP-A.md) / [报告](reports/P4-UI-TIMELINE-SEARCH-MAP-A.md)。人物/合集/地点 API 行为有定向覆盖，端到端浏览器门仍 pending；Computer Use 因当前 Chrome URL 无法足够确认而停止，未形成浏览器证据。`PERF_200K=PENDING`、`CHECK_300K=PENDING`；阶段二语义候选时间浏览与 map viewport 时间浏览已实现并通过定向 API/UI 回归（82/0/0/0）；LUNA High 阶段二复核的三项 finding 已修复并确认关闭（不是 DS-H）。浏览器交互、`PERF_200K`、`CHECK_300K` 仍 pending，不宣称验收通过。前置 A/C/CORE/B/D/E 的验收状态均未改变；本轮无 commit、无真实图库/模型/GPU访问。

## Next necessary action: P4-UI-TIMELINE-SEARCH-MAP-A browser and scale acceptance gates remain PENDING

获取人物/合集/地点 CTA 到共享搜索、翻页、预览与返回的真实浏览器证据，并在有代表性的复用规模数据上测新增过滤查询及续页。现存 CORE/E 30 万 catalog 缺少可信时间元数据与人物/合集/地点成员，不能替代有效成员性能验证；没有新建规模夹具。

## Predecessor status (unchanged): P4-UI-PLACES-E — IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE (2026-10-14)

地点相册接入共享时间轴并增加可复用地点成员 SQL scope；保留原地点整理和地图查询行为。定向 JUnit **32/0/0/0**，Ruff、Python 编译、places/timeline/map 相关四个 JS 语法检查及 diff-check 通过。用户指定的 LUNA High 受影响面复核完成、修复已回看，无剩余阻断 finding；不是 DS-H。[任务卡](tasks/P4-UI-PLACES-E.md) / [报告](reports/P4-UI-PLACES-E.md)。2026-10-14 已在真实产品浏览器中用隔离 Places-E 4 照片样本和既有 GPS 合成样本验证地点时间轴、详情返回、地图 2 张范围照片及详情返回后范围恢复；请求日志为 200/304。`BROWSER_INTERACTION=VERIFIED`；本轮续验因 CUA 拦截一次性 session 链接未进入认证态，也未产生截图；既有交互证据保留。可落盘截图工件、`PERF_200K` 与有代表性的 `CHECK_300K` 仍 pending，不能标整卡 PASS。

用户要求复用原 30 万夹具、不建新夹具。既有 30 万 catalog 没有地点表、成员或 EXIF metadata；空地点 scope 查询仅为诊断，不代表有效成员性能。SEARCH-MAP 仅因用户 2026-10-14 明确指示而启动；A/C/CORE/B/D/E 的既有未验收状态按各自报告保留。

## Next necessary action: P4-UI-TIMELINE-SEARCH-MAP-A acceptance gates — browser and scale evidence remain PENDING

执行本卡剩余门禁：获得可核验的浏览器 CTA→搜索→分页/预览/返回证据，并复用有效规模 fixture 验证时间、人物、合集、地点过滤及续页。当前无可复用的有效 20 万规模 fixture，旧 30 万 catalog 缺少时间元数据和成员数据，故保持 PERF_200K 与 CHECK_300K pending。浏览器工具因当前 URL 无法安全判定而停止；未保存或声称浏览器截图通过。

## Previous user-directed task: P4-UI-PEOPLE-D — IMPLEMENTED / BROWSER INTERACTION VERIFIED / PERF_200K PENDING (2026-10-14)

人物与合集照片页已接入共享时间轴，保留旧局部搜图与人物整理操作；合集实例/revision 不匹配时停止显示结果并提供刷新后的当前范围重载，查看器续页沿用有界照片 ID。定向 JUnit **31/0/0/0**；Ruff、Node、diff-check 通过。[任务卡](tasks/P4-UI-PEOPLE-D.md) / [报告](reports/P4-UI-PEOPLE-D.md)。用户指定的 LUNA High 受影响面复核无剩余实现 findings，不代表 DS-H。2026-10-14 以隔离合成库和本机产品浏览器验证跨页人物选择/取消、忽略恢复、单脸对话框取消、人物/合集时间轴和详情返回；截图保存在 `reports/artifacts/P4-UI-PEOPLE-D-browser.jpg`。`PERF_200K` 仍 pending；用户要求先执行 E 不变更更早任务的验收状态。

## Previous executed task: P4-UI-PHOTOS-B — FUNCTIONAL SCOPED PASS / LUNA HIGH REVIEW COMPLETE / BROWSER INTERACTION PARTIAL / PERF_200K PENDING (2026-10-14)

照片页默认统一时间轴、保留列表/图库管理；定向 JUnit **28/0/0/0**，关联 timeline integration **1/0/0/0**；Ruff、Node、diff-check 通过。[任务卡](tasks/P4-UI-PHOTOS-B.md) / [报告](reports/P4-UI-PHOTOS-B.md)。2026-10-14 本机浏览器观察来源切换、日期桶空/待就绪、管理校验与 31 页合成滚动；详情可通过路由打开并返回锚点，但照片卡片点击未进入详情，截图未落盘，首图时延未测，故 BROWSER 门仍 pending；`PERF_200K` 仍 pending。

## Previous completed task: P4-PERSON-IGNORED-CANDIDATE-A — SCOPED PASS (2026-10-13)

[Task card](tasks/P4-PERSON-IGNORED-CANDIDATE-A.md), [report](reports/P4-PERSON-IGNORED-CANDIDATE-A.md). 忽略人脸显示已有候选头像并可确认/手动选择；候选确认后刷新忽略视图，不改识别、相似度或后端归属协议。定向 JUnit **2/0/0/0**；Node、Ruff、diff-check 通过。验证仅覆盖任务卡 API/合成 DOM 范围。

## Previous completed task: P4-PERSON-IGNORE-STATE-A — SCOPED PASS / LUNA HIGH REVIEW COMPLETE (NOT DS-H) (2026-10-13)

[Task card](tasks/P4-PERSON-IGNORE-STATE-A.md), [report](reports/P4-PERSON-IGNORE-STATE-A.md), [LUNA High review](reports/P4-PERSON-IGNORE-STATE-REVIEW-A.md). Legacy hidden rows migrate to TEMPORARY; EXCLUDED filters suggestion projections and automatic face candidates without deleting person/face data. Focused JUnit is **34/0/0/0**; Ruff, `node --check`, `py_compile`, and diff-check pass. LUNA High's stale-inbox finding was fixed and re-reviewed; not DS-H.

## Previous completed task: P4-UI-TIMELINE-REVISION-A — IMPLEMENTED / LUNA HIGH REVIEW COMPLETE (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-REVISION-A.md), [report](reports/P4-UI-TIMELINE-REVISION-A.md). Reused `catalog_filter_revision` for scanned new assets, source removal, collection membership/deletion, and changed person visibility; person operations and capture-time/place mutations retain existing hooks. User chose the existing per-file scanner transaction as the revision unit: each newly visible asset commits with one bump in that transaction; no-op/repeated content does not bump. Task-card JUnit is **14/0/0/0**; Ruff, `py_compile`, and diff-check pass. Regression covers single-location detach, duplicate-location detach no-op, first binding to a new source, and same-source duplicate content without an unnecessary bump. **LUNA High final repair review complete with no remaining findings**. No schema/API/cursor protocol change; existing uncommitted `photo-timeline.js` edits were preserved.

## Predecessor status: P4-UI-TIMELINE-CORE-A — PARTIAL / VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-TIMELINE-CORE-A.md), [report](reports/P4-UI-TIMELINE-CORE-A.md)。Timeline API/核心组件已实现；REVISION-A 已修复共享 catalog revision 信号。CORE-A 的浏览器证据、`PERF_200K` 和独立工程审查仍 pending。Integration-A 只接入首页，不代表 CORE-A 关闭。CORE-A 阻断关闭前不要开始 B.

## Predecessor status: P4-UI-SHELL-A-F1 — IMPLEMENTED / VISUAL VALIDATION PENDING (2026-10-13)

[Task card](tasks/P4-UI-SHELL-A-F1.md), [report](reports/P4-UI-SHELL-A-F1.md), [visual tokens](plans/P4-UI-VISUAL-TOKENS.md). Shared theme/navigation and search page visual scope are implemented. Focused JUnit is **37/0/0/0**, Ruff and diff-check pass. Remaining visual gates: real-browser 200% zoom and a saved search-hit screenshot; LUNA High review is not DS-H.

## Related UI status

- [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md): IMPLEMENTED / VALIDATION PENDING; 200% feedback moved the compact-nav breakpoint to 680px and the synthetic in-app browser confirmed visible navigation on a narrow viewport; exact 200%, dark mode and persistent screenshots remain pending. [Report](reports/P4-UI-SHELL-A.md).
- [P4-UI-SEARCH-C](tasks/P4-UI-SEARCH-C.md): implementation and focused engineering validation passed; full visual checks remain pending.
- [P4-UI-INTERACTIVE-DEMO](reports/P4-UI-INTERACTIVE-DEMO.md): user-confirmed visual reference only; synthetic, not product acceptance.

## Additional requested task: P4-MEDIA-CR2-A

[Task card](tasks/P4-MEDIA-CR2-A.md), [report](reports/P4-MEDIA-CR2-A.md): implementation partial / validation pending. The user authorized `D:\SZ\天空\100CANON`; five read-only CR2 samples decode through the shared RAW frame at 1.101–1.183 s each, with unchanged SHA/size/mtime. Embedded JPEG and RAW geometry did not establish reliable crop mapping. Focused regression and static checks pass; real face/OCR inference, active-model text search, valid-GPS/portrait coverage, actual continuation idempotence, frozen-package execution, and old face/OCR terminal-job recovery remain pending. Do not claim SCOPED PASS.

## Additional user-directed task: P4-UI2-PERSON-BATCH-UX-FIX-A — IMPLEMENTED / SCOPED PASS / ACCEPTANCE PARTIAL (2026-10-14)

人脸待整理/已忽略选择分区、跨展开组 Shift、合集先浏览后管理、真实反选和直达改名/删除均已按任务卡实施。返工修复了真实 DOM 中 `HTMLCollection.forEach` 异常及 checkbox 取消激活回滚；定向 JUnit **17/0/0/0**，Node `--check` 与 `git diff --check` 通过。LUNA High repair-only 回看关闭两项 finding。内置浏览器已验证待整理区独立入口、首项至第三项 Shift 连选、取消中间项后 checkbox/计数同步；当前合成库无合集成员，浏览器不支持创建合集所需的 `prompt()`，合集成员浏览器验收仍 `COLLECTION_BROWSER_FIXTURE_MISSING`，整卡保持 **ACCEPTANCE PARTIAL**。详见[任务卡](tasks/P4-UI2-PERSON-BATCH-UX-FIX-A.md) / [报告](reports/P4-UI2-PERSON-BATCH-UX-FIX-A.md)。未访问真实图库；未跑全仓、性能、模型/GPU、打包或发布检查。
## Previous user-directed task: P4-UI2-VIEWER-SIZE-STABLE-A — IMPLEMENTED / ACCEPTANCE PARTIAL (2026-10-14)

共享照片查看器外框改为显式视口公式 `min(94vw,1500px,calc(100vw - 16px)) × min(92dvh,calc(100dvh - 16px))`，内部 grid/stage/panel/扩展区增加尺寸约束与内部滚动；同步五页 CSS cache-bust。定向 JUnit **4/0/0/0**、`git diff --check` 通过，报告见[任务报告](reports/P4-UI2-VIEWER-SIZE-STABLE-A.md)。按用户要求未运行浏览器，修复前/后真实 rect、S01–S06 及截图均 pending，整卡 **ACCEPTANCE PARTIAL**。未访问真实图库/模型/GPU，未跑全仓、性能、打包或发布验证。

## Next necessary action: P4-UI2-VIEWER-SIZE-STABLE-A browser geometry gates

若继续关闭整卡，使用隔离合成图库和 Codex 内置浏览器测 S01 实际 dialog/stage 几何及缩放不变量，再按需要测 S05/S06；本轮没有真实浏览器或像素几何证据。
