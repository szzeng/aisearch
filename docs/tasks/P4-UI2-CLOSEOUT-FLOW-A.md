# P4-UI2-CLOSEOUT-FLOW-A — UI 2.0 跨页面操作闭环收口

- **状态**：PLANNED（任务卡；尚未实施）
- **优先级**：P0，UI 2.0 第一张收口卡
- **基线**：`szzeng/aisearch` / `master`，编卡时 HEAD `d0251135`（2026-10-14）；执行前核对最新 HEAD，不覆盖后续修复
- **建议模型**：GPT-6 High / LUNA High；复核按 `AGENTS.md` 与 V3.1 §2.4 的实际风险分级，模型手动选择
- **验收方式**：个人相册轻验收；少量定向 JUnit、JS 检查和**真实产品浏览器 + 隔离合成图库**的操作闭环
- **性质**：跨页面修复/验证；**不等同 UI 2.0 全部验收通过**，不接管独立性能和视觉专项

## 1. 本卡唯一目标

**不重做 UI 2.0 架构，只消除日常操作中的“能进入、却回不来／不能继续用”的断点。**

1. **固定范围搜索**：从人物相册、人物合集、地点相册点「搜索此相册／合集／地点」→ 进入唯一全局搜索输入 → 范围 chip 可见 → 明确提交 → 得到正确的权威成员结果。
2. **预览和返回**：搜索结果→预览/详情→人物或地点→浏览器返回，或关闭/Esc 返回；正常未变化时尽可能恢复 `q`、范围、已验证结果/分页、滚动、原焦点；**不会因为校验方法不适用而误报版本过期**。
3. **真实过期保护**：成员、合集实例、活动地点包、时间规则、搜索会话或查询绑定确实变化时，不显示已过期照片、旧 cursor 或旧范围；明确提示如何重新查询或选择范围。
4. **已修复路径复验**：全部照片/来源、人物/合集、地点的时间轴卡片点击→正确详情→关闭返回；核对日期桶/锚点；抽验有 GPS 成员的地图往返和 bbox 变化。
5. 只修改**有复现证据**的阻断缺陷；余下性能、视觉和特殊图片适配仍维持各自任务的 PENDING。

**整卡收口判据**：主要 P0 路线在实际产品浏览器里走通；必要定向测试通过；无残余 P0 用户操作阻断。仅测试/API 通过不能代替浏览器通过；浏览器被阻止或场景无法成立时记 PARTIAL/PENDING。

## 2. 仓库事实和重点根因（先确认、再施工）

| 已有事实 | 本卡处理 |
| --- | --- |
| `38ebbae4`（P4-UI-TIMELINE-CLICK-PATH-FIX-A）修复了首页自定义时间轴卡片 `onOpen`、文件名/目录显示和 Esc 返回；定向 33 项通过、合成浏览器点开通过 | **回归，不重做**；人物/地点适配器独立抽验 |
| `f6692526` 修复时间轴 `insertBefore` DOMException；`dbc7efb1` 修复首页主体大留白 | 检查最新 HEAD 是否回归；不得新建第二套时间轴或借机重画 CSS |
| `P4-UI-VIEWER-F` 实测人物 `FILTER_ONLY` 结果跨页返回后仅恢复 chip/`q`，旧候选被清空 | P0 原因调查与定向修复 |
| `src/photo_search/web/search.js` `restoreSearchSnapshot()` 当前以 `/search/sessions/{id}/time?limit=1` 校验结果；`search_session_id === "filter-only"` 或以 `place-album:` 开头时直接 `SNAPSHOT_UNVERIFIABLE` | **已定位候选根因**：语义候选时间端点不能作为所有搜索形态的通用验证器；不得靠删除校验来“修复” |
| 同一文件 `saveSearchSnapshot()` 仅在有 `hits` 时写入快照 | 评估**空结果/错误状态**返回是否丢失 `q`/chip/提示；若可复现，仅补最小状态恢复，不要求恢复空结果的旧游标 |
| `VIEWER-F` 中无活动地名包的合成地点搜索遇 `CATALOG_FILTER_REVISION_STALE` | 这是**待查问题**而非已确诊产品 bug；在有效活动数据、未发生变化的前提下复现，并区分 fixture/身份过期/竞态 |
| `P4-UI-TIMELINE-SEARCH-MAP-A` 的人物/合集/地点 scoped 搜索 API、语义候选时间浏览、地图 viewport 时间排序均已实现并经定向检查 | 本卡只补**已启用**路径的真实用户操作验收，不添加新搜索模式 |
| `P4-UI-PLACES-E` 已证明有 GPS 成员的地图详情返回；bbox 变化后旧成员不得恢复的浏览器证据不足 | 小范围补验；不重写地图聚合 |

**阅读顺序**：`AGENTS.md` → `docs/development-status.md` → 本卡 → [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) §1.3、§2、§3.2、§6.1 → [VIEWER-F 报告](../reports/P4-UI-VIEWER-F.md) / [SEARCH-MAP 报告](../reports/P4-UI-TIMELINE-SEARCH-MAP-A.md) / [点击修复报告](../reports/P4-UI-TIMELINE-CLICK-PATH-FIX-A.md) → 被修改的代码、对应测试。避免重复阅读无关历史。

## 3. 实施顺序与允许改动

### A. P0：范围搜索入口与搜索结果（先验现状）

检查 `web/persons-albums.js`、`web/places-albums.js` 的 `searchThisAlbum()`，以及 `web/search.js` 的 `consumeEntryScope()`、`restoreOrStartSearch()`、scoped chip/搜索与翻页逻辑。

- **人物**：以现有 canonical person 身份和有效成员为准，asset 去重；不以显示名或单张脸构造独立候选集合。
- **合集**：绑定 `collection_id`、`instance_token`、membership revision；不能拿合集中的某个人代替合集。
- **地点**：使用当前活动来源/有效 `place_kind/place_key`、人工覆盖/排除等权威 SQL 成员；同名地点不得在已选范围后重新猜测。
- 一次性 `sessionStorage` scope 只作**交接载体**，不是可信权限凭据；以服务端复核为准。交接消费一次，浏览器返回不应误清已有合法 chip。
- 点击快捷按钮只切到搜索页并聚焦，**空词不自动提交**；用户主动按“搜索”提交空内容，则走权威 `FILTER_ONLY` SQL 分页，零编码器调用；有内容词仍按原检索模型/TopK/排序行为。
- 固定 scope 与额外条件做 AND；不得默默继承原相册的年月过滤；取消 chip 使旧 search session/cursor 失效，**用户明确重新查询**后才能改为全局结果，不暗中扩大范围。
- 只对真正复现的无效按钮、范围丢失、翻页串范围等问题修改前端或必要的既有只读后端调用。

### B. P0：搜索快照与返回（本卡主要修复）

先分别复现并记录**未变更**的三种搜索快照：

1. `FILTER_ONLY`：人物或合集固定 scope + 明确空词提交；预览/跨人物跳转→返回。
2. **地点有效 scope** 的纯条件搜索：活动地名包/地点身份有效且无修订变化，搜索→预览/跨页→返回。
3. **有内容词/冻结语义候选**：若合成环境无编码器，使用已有可信测试接口/fixture 做协议级定向回归，浏览器标 `SEMANTIC_BROWSER_PENDING`，**不得把 FILTER_ONLY 冒充语义命中**。

**必须分开两层状态**：

- **UI 导航状态**：`q`、固定 chip、结构化条件、提示、来源页面、合法的日期/目录筛选、滚动/焦点。即使结果快照不再可信，也应尽力恢复这些非结果状态，并提供重新操作入口。
- **权威结果状态**：命中资产、总数、排序/原始语义 rank、候选快照、session/cursor。只有**对应查询形态的权威验证通过**，才允许复用；不能用 `sessionStorage` 的旧 JSON 充当验证。

修复策略按**最小安全方案**选择：
- 优先用现有 read-only 范围、有效 session/cursor 和 revision 机制验证；先盘点真实可用端点与失败码，再实现。
- `FILTER_ONLY` **不适用**语义候选时间端点时，允许按**原完整过滤条件**执行**一次**既有权威 SQL 查询/验证以恢复可见页，确保成员与排序最新；不可悄悄调编码器/FTS/向量、不可恢复旧的未验证深页 cursor 或任意旧结果。
- 语义搜索继续校验真实 session、query/profile/generation、固定 scope、catalog/time-rule 绑定与候选快照；只恢复**已验证且仍有效**的有限候选，不重复运行 NLP/向量来伪装“返回恢复”。若无现成验证路径，保留条件并提示显式重新搜索，**不得放行缓存命中**。
- 若需要新增公开 API 或改变 session/revision/cursor 契约，先给出影响面和独立审查要求；**不在本卡偷偷扩协议**。如确实不可在现有能力下安全实现，可保留明确 `BLOCKED_BY_CONTRACT`、拆后续小修卡，不能写整卡 PASS。
- 明确区分 `SEARCH_SESSION_EXPIRED`、真正 `CATALOG_FILTER_REVISION_STALE`、合集/地点失效、无法验证与请求失败。失效时清空不可信结果/cursor，保留尽可能可靠的条件/chip，给出「重新搜索」或「重新选择范围」；禁止自动无限重试、吞 409、静默扩大到全库。
- 重点检查 `requestGeneration`、异步回包竞态、`sessionStorage` 被禁用与返回过程中用户发起新查询；迟到的旧响应不得覆盖最新条件。
- **空结果**：至少能保留查询条件和空结果提示；如果确实需要保存状态，不能把“0 结果”提升为未经验证的旧资产缓存。

### C. P0：时间轴、预览与原位置返回回归

- 首页全部照片/来源、人物相册/合集、地点相册各点击一张，确认进入**正确 asset** 且只触发一次；地点内部预览按钮的冒泡不得触发第二次打开。
- 关闭按钮/Esc/已有遮罩关闭和浏览器返回按当前页面实际支持的方式恢复；焦点优先回到仍存在的触发按钮，否则回到可见安全焦点位。
- 检查拍摄时间/入库时间、年/月定位、未知/待就绪、scope 切换：**不串范围、不伪造拍摄日期、不复用过期游标**；详情返回仍能定位当前 asset 或其合法邻近锚点。
- **至少一次两页以上**的滚动→预览→返回，覆盖跨页同日组头与回收页后的合法重定位。若已有长滚动 fixture 可复用，顺带观察更深页；不为本卡单独构建大图库或重复完整 30 页性能验收。
- 核对最新首页留白修复 `dbc7efb1` 与文件名/目录显示 `38ebbae4` 未回归；不要做整体 CSS 风格重构。

### D. P1：地图与轻量特殊交互

- **仅用有 GPS 成员的现成隔离夹具**：进入地图→预览→返回；在 bbox/排序/成员版本没有变化时恢复当前成员和位置。
- **实际改变 bbox**（记录前后范围，而不是仅点击 Zoom Out 就假定已改变）或隔离夹具的成员 revision；旧 viewport 不能混入新范围，可信拍摄时间模式仍绑定 `member_version`、bbox、catalog/time-rule 修订。
- 抽查一次预览快速切换、资源失败缩略图回退、Esc/焦点恢复。长竖图、200%/暗色视觉矩阵、离线原图特殊解码未修改的部分留给视觉/后续实测，不把未验内容标 PASS。

## 4. 禁止扩大范围

1. **不做** 20 万/30 万性能优化、SQL 索引/schema/migration、预聚合与元数据大夹具；交 `P4-UI-TIMELINE-PERFORMANCE-A`（独立性能专项，尚未建卡时只作为拟定名称），本卡的功能回归不能替代性能验收。
2. **不做** UI 2.0 全站重设计、视觉 token 全改、精确 200%/暗色视觉验收；交 `P4-UI2-VISUAL-FINAL-A`。仅允许修复阻断点击/显示的局部 CSS。
3. **不改** 真实 `PhotoSearchData`、用户原图、人物/地点归属与撤销数据、扫描/建库、模型/GPU、RAW 解码、鉴权/CSRF、原搜索排序/TopK 以及现有修改事务。严禁为了验收给假 session、关闭版本保护或绕过浏览器认证。
4. 不引入第二套 viewer、时间轴、全局状态总线、全量成员 ID 列表、无限快照缓存或逐图额外请求。
5. 若出现身份、权限、session、revision、cursor 的**公共契约变更**，停止超范围开发并记录升级建议/风险及对应独立完整受影响面复核；正常的最小前端接线/现有 SQL 只读复用仍可按本卡完成。
6. 不允许自动修改或扫描真实图库；真实数据试用由用户另行执行，本卡只用隔离合成数据。

## 5. 最小验收矩阵（有明确判定，不反复测试）

| 编号 | 场景 | 期望 |
| --- | --- | --- |
| F01 | 人物→「搜索此相册」→主动提交空词→预览→跳人物→返回 | 单一输入、固定人物 chip、FILTER_ONLY 成员准确；未变更时已验证结果/位置可恢复，零编码器 |
| F02 | 合集同路线，翻至第二页 | 绑定 collection id/instance/revision；资产去重；无冒充单个人物/跨页串 scope |
| F03 | **有效活动地点**→「搜索此地点」→提交→预览→返回 | 当前权威地点成员；无真实变化不误报 revision stale；旧无活动包样本单独标 `FIXTURE_INVALID` |
| F04 | 三种 scope 分别取消 chip，再用户明确重搜 | 新请求才扩至全库，旧 session/cursor 不继续，返回仍不串旧 scope |
| F05 | 结果不变、结果为空及 session 不可验证三种返回 | UI 条件/提示尽量保留；有效结果只在匹配的权威校验后重显；失效结果不重显 |
| F06 | 合集成员/地点活动数据/时间规则发生真实变化再返回（**仅隔离夹具**） | 拒绝旧结果和游标，清楚提示重新查询/选范围；无死循环 |
| F07 | 旧请求迟到、用户已切另一个搜索范围 | 旧响应不覆盖新结果/chip；没有永久 loading |
| F08 | 首页、来源、人物、合集、地点各点一张→关闭/Esc | 正确照片仅打开一次；恢复原页面/合法锚点/焦点；`capture` 字符串不再冒充文件标题 |
| F09 | 时间轴两页以上、年/月、未知/待就绪桶、详情返回 | 不错乱范围、时间状态和组头；合法页可继续翻页 |
| F10 | GPS 地图成员→详情→返回；确认 bbox 真正变化后再次返回 | 不变时可恢复；变更后旧成员被作废；地图有成员而非空态代测 |

**P0 必过**：F01–F09 中适用的核心流程，尤其 F01/F03/F05/F08；F10 属轻量 P1。浏览器不具备合法认证/未建立有效地点测试数据时相应项只能 `BROWSER_PENDING` 或 `FIXTURE_INVALID`，不得把测试桩当浏览器。若任一 P0 仍失败，整体结论 `ACCEPTANCE_PARTIAL`。

## 6. 测试预算（个人项目，必要即可）

- 复用 `P4-UI-VIEWER-F`、`P4-UI-PLACES-E`、`P4-UI-PEOPLE-D` 的现有隔离样本；不满足有效活动地点时，**只补必要的有效地名包/合成成员配置**，约 10–40 张合成图片即可；如需第二页，用少量合成元数据，不生成 20 万图片。
- **优先**增加 4–8 个围绕本次问题的回归断言：`FILTER_ONLY` 未变返回、真 stale 返回、合集/地点 identity、迟到响应、点击一次、日期锚点。只有实际修改的路径才补测试；不为了凑数跑所有 UI 测试。
- 优先复用 `tests/unit/test_search_person_album_link_ui.py`、`tests/unit/test_photo_view_info_ui.py`、`tests/unit/test_p4_ui_photos.py`、`tests/unit/test_p4_ui_people_d.py` 和 `tests/integration/test_p4_place_search_flow.py`、`test_p4_search_collection.py`、`test_p4_ui_timeline_integration.py` 的受影响用例；可用独立 `tests/unit/test_p4_ui2_closeout_flow.py` 汇总新回归。
- **真实产品浏览器**：一次合法本机会话 + 隔离合成数据，至少 F01、F02、F03、F08、F10（有条件时）对应的用户操作；查看状态、URL、浏览器 console 及服务请求，保留少量可复核截图。浏览器工具若遇会话/URL 安全限制，记录 `BROWSER_PENDING` 并停止，不得绕过。
- 命令依真实改动缩小范围；以下仅为起点（PowerShell，新增测试存在才加入）：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q `
  tests/unit/test_search_person_album_link_ui.py `
  tests/unit/test_photo_view_info_ui.py `
  tests/integration/test_p4_place_search_flow.py `
  --basetemp=.pytest-tmp/P4-UI2-CLOSEOUT-FLOW-A `
  --junitxml=test-results/P4-UI2-CLOSEOUT-FLOW-A.xml
node --check src/photo_search/web/search.js
node --check src/photo_search/web/photo-timeline.js
git diff --check
```

- 对其他**修改过**的 JS 也做 `node --check`；对修改过的 Python 源码/测试执行 Ruff 和 `py_compile`。JUnit 必须落盘，记录实际退出码/结果。
- **不要求**全仓 pytest、真实模型/GPU/OCR、打包、20 万/30 万压测、全站视觉截图矩阵。若触及身份/搜索会话/公共 revision 安全合同，升级到 V3.1 §2.4 要求的独立工程复核，不因个人项目省略必要的安全校验。

## 7. 交付与停止

1. 实施前从最新 HEAD 逐一确认尚存缺陷；原则上优先改 `src/photo_search/web/search.js`，其他 `persons-albums.js` / `places-albums.js` / `photo-timeline.js` / `app.js` / `map.js` / `photo-info.js` 及后端只在复现且必要时最小修改。
2. 生成 `docs/reports/P4-UI2-CLOSEOUT-FLOW-A.md`，按 **复现→原因→修复→F01–F10 结果→未验证** 记载。定向 JUnit 保存 `test-results/P4-UI2-CLOSEOUT-FLOW-A.xml`，必要截图保存 `docs/reports/artifacts/`。
3. 更新 `docs/development-status.md`，但只补充本卡有证据的关闭范围；原 A/C/CORE/B/D/E/SEARCH-MAP/F 的性能和其他未验收门**不得批量改 PASS**。
4. 结论分列 `FUNCTIONAL`、`BROWSER_INTERACTION`、`REVIEW` 和原因码。P0 用户闭环通过且无未修 P0→`FLOW_SCOPED_PASS`；否则 `ACCEPTANCE_PARTIAL`。原 `PERF_200K` / `CHECK_300K` / 视觉门保持各自待验状态。
5. 本卡完成即**停止，不自动执行性能/视觉收口任务、不启动模型建库、不发布版本**。性能收口和最终视觉统一另行安排。
