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
