# P4-UI2-PLACES-SIMPLIFY-A — 地点页面去管理化与三级视图精简

> **状态**：READY / 待执行（本提交仅新增任务卡，不代表功能已经实现）
> **优先级**：P1，个人日常使用体验
> **建议执行模型**：GPT-6 High（用户在 Codex 手动选择），原生 HTML/CSS/JS 局部 UI 与状态治理
> **测试原则**：简单定向测试 + Codex 内置浏览器 2 条核心路线；不用真实照片库、全仓测试、模型/GPU 或 20万/30万数据夹具。
> **阅读顺序**：`AGENTS.md` → `docs/development-status.md` → 本卡 → `docs/tasks/P4-UI2-PLACE-ALBUM-NAV-A.md` → 直接相关实现/测试。动手前先核对最新 HEAD，避免覆盖其他 UI 修复。

## 一、问题与目标

现有 `/places` 已经通过 `P4-UI2-PLACE-ALBUM-NAV-A` 将地点列表和某个地点的照片详情分离，但列表下方仍是地名包、地点目录、新建/编辑、建议批量、操作历史/撤销等管理长页；列表顶部还有“地点工具”。`place_id`、`expected_revision`、`assign/unassign/restore_auto` 等内部术语也直接暴露。个人日常打开地点只想看照片，这些入口造成信息噪音。

**目标：默认只浏览地点相册；主动点击「整理地点」后才展示整理与维护工具。仅调整展示/导航/文案，不删除功能、不更改地点归属/候选/撤销协议。**

### 三种互斥的可见状态

| 状态 | 入口 / URL | 显示内容 |
| --- | --- | --- |
| **BROWSE 默认** | `/places` | 地点封面卡、城市/省份/区县/国家切换、筛选地名、唯一「整理地点」次级入口；不显示管理长页、技术说明和重复地图导航 |
| **DETAIL 具体地点** | `/places?album_kind=<kind>&album_key=<key>` | 返回地点列表、名称+上级/来源+实际照片数、共享时间轴和照片弹窗；保留「搜索此地点」 |
| **MANAGE 整理地点** | 建议 `/places?place_view=manage`；支持旧管理 hash | 返回地点相册、默认「整理识别建议」；「管理地名」「高级维护」「历史与撤销」分别按需打开 |

只显示一个主体。优先复用现有 `places.html`、面板 DOM ID 和 `places-albums.js` 的列表/详情导航；不新增前端框架、后端路由、数据库或全局设置页。不要只是把整个管理长页移到封面卡下面再折叠。

## 二、功能保留与重新归位

| 现有面板 / 操作 | 新位置 | 必须保持 |
| --- | --- | --- |
| `#panel-suggestions` 建议与批量整理 | MANAGE 默认「整理识别建议」 | 待确认候选、已加载 Shift、全选/反选/清空、分页、冻结快照、每批≤500、revision/冲突反馈 |
| `#panel-directory` 地点目录 | MANAGE →「管理地名」 | 原目录搜索、来源与父级身份、分页；技术字段藏进高级详情，但实际值不丢 |
| `#panel-create` / `#panel-edit` | MANAGE →「管理地名」 | 新建、改名、别名整组更新、expected_revision 与错误处理；不虚构合并/删除 |
| `#panel-datasets` 地名包状态 | MANAGE →「高级维护」 | 原查询、刷新、缺包/失败状态；未进入不预载明细 |
| `#album-backfill` 补齐照片地点 | MANAGE →「高级维护」 | 原按钮 ID、可继续处理、状态；必须主动点击才 POST |
| `#cn-update` / `#cn-pause` | MANAGE →「高级维护」 | 中国地点更新的可用性、暂停/继续、进度；绝不因打开页面自动更新 |
| `#panel-operations` 操作历史与撤销 | MANAGE →「历史与撤销」 | 旧日志、撤销资格、revision、冲突和失败回显 |
| `#panel-limits` 本页不支持的功能说明 | 移除可见开发说明 | 不新增服务端不支持的改父级、合并、物理删除入口 |
| `#place-intro`、`#place-page-nav` | 必要帮助放 MANAGE 内 | 日常浏览不显示内部接口/算法说明；旧管理 hash 仍可访问 |

BROWSE 只保留一个「整理地点」入口；不把低频按钮放回相册标题栏。侧栏已有地图相册入口，地点列表中不另重复。可保留轻量能力探测，但不能在默认浏览时预载日志、候选大列表和地名包明细。

MANAGE 内只激活当前工具面板。其他区块必须真正 `display:none`、不可 Tab、不可因 IntersectionObserver 或旧锚点而在后台预加载。保留现有表单 DOM ID、事件注册、CSRF/revision、错误反馈和撤销能力。

## 三、照片「更正地点」降噪

`places-albums.js::renderAsset()` 当前在每张照片下追加一整行「更正地点」`details`。改成照片卡角落可访问的「更多操作」按钮；默认不占单独整行，点击后才显示纠错工具，键盘/触屏可操作。

在原有 `correct()` / `write()` / 地点选择器上最小改动，不移植到全局 `photo-info.js`。纠错操作改成理解成本较低的显式选择：

1. **改到其他地点**：搜索并点选真实地点；同名展示父级和来源；不可输入任意文本直接入库。
2. **排除此照片的地点归属**：底层 `unassign`，明确提示会阻止自动重新归入，不能误写成仅“取消人工指定”。
3. **恢复自动识别**：底层 `restore_auto`，撤去人工排除/覆盖，让自动归属规则重新生效。

不再要求用户知道输入 `*` 或留空的隐式命令。全部写操作仍由用户显式点击，维持现有身份验证、CSRF、expected_revision、错误/冲突信息。成功后按当前权威成员刷新，不跳错相册或清空时间轴导航；失败保留当前视图供重试。若完整三动作重构明显扩大前端写入风险，先完成“更多操作”收纳和语义清晰化，未完成部分在报告标 PENDING，不虚报 PASS。

## 四、导航、历史与异步保护（P0）

1. **单一状态控制**：结合 `places-albums.js` 现有 `MANAGEMENT_IDS`、`switchView()`、`setManagementVisible()`，统一管理 BROWSE/DETAIL/MANAGE。避免 `places.js` 与 `places-albums.js` 分别控制最终可见模式；保留现有地点列表状态及 `__placeAlbumList` 轻量快照。
2. **地点详情合同不变**：`album_kind + album_key` 为唯一身份，`cn:` key 不透明保留；直接链接/刷新/Back/Forward、搜索地点范围、查看器返回、时间轴日期桶/滚动恢复沿用 `P4-UI2-PLACE-ALBUM-NAV-A`。无效地点只显示明确错误，不回退全库。
3. **旧管理入口兼容**：`/places#panel-directory`、`#panel-suggestions`、`#panel-operations`、`#panel-datasets`、`#panel-create`、`#panel-edit` 都进入 MANAGE 并打开正确面板；`?place_view=manage#panel-...` 也可用。未知 hash 不自动打开高级管理。
4. **冲突 URL 优先级**：管理 hash 与详情参数同时存在时沿用现有 hash 优先，清理详情参数并进入 MANAGE；只有 `place_view=manage` 与合法详情参数冲突且无管理 hash 时优先 DETAIL，并规范化多余参数。主动点击整理地点时必须清除详情参数，不混用旧照片/管理状态。
5. **往返恢复**：BROWSE→MANAGE→返回保留城市种类、地名筛选、封面卡、滚动/焦点。DETAIL 退出仍回列表；MANAGE→DETAIL/返回不遗留建议/选择的可见 UI 或「搜索此地点」条件；浏览器 Back/Forward 无死循环 pushState。
6. **惰性与竞态**：`places.js::setupDeferredPanels()` 和 `boot()` 只能在 MANAGE 对当前可见区块触发对应管理数据读取；原轻量 capability 探测可保留，不触发任何隐式 POST。异步请求迟到不得切换当前视图、串地点身份或显示隐藏面板。管理进入/退出不应每次重新拉全量地点列表；读取失败有重试，不显示为“空图库”。
7. **显示与焦点**：检查 `places.css` 中 `[hidden]` 与 `display:flex/grid` 的优先级，避免隐藏内容占高度或被 Tab 命中。窄屏、浅/暗色沿用 UI 2.0 token。不因本卡重做共享时间轴、搜索、地图或照片查看器。

## 五、实施边界

- **优先涉及**：`src/photo_search/web/places.html`、`places.css`、`places-albums.js`、`places.js`；必要时小幅补定向测试，按项目惯例更新 JS/CSS 静态缓存版本。
- **禁止修改**：Python API、SQLite schema、地名算法、GPS 解析、地点候选资格、人工归属优先级、批量 revision/undo、全局搜索与地图成员、全站日期时间轴策略。
- 不访问真实 `PhotoSearchData`、不扫描或修改真实照片、不启动模型/GPU/OCR、不运行 20万/30万性能大夹具；无用户明示授权，不对真实图库做任何更新。
- 如纯前端改动不足以保住旧写入/撤销协议，停止扩大范围并记录后续任务，不能悄悄演变成后端重构。
- 「日期待就绪 · 0」等共享时间轴入口是否按 0 隐藏，另案处理，不顺手改所有相册。

## 六、简单验收（小型定向测试）

| 检查 | 核心结果 |
| --- | --- |
| S01 / BROWSE | 打开 `/places`：只出现地点卡、类型与地名筛选、整理地点入口；不自动拉取日志/大批建议；点击封面成员/照片数准确 |
| S02 / MANAGE | 点整理地点→建议→高级维护/历史→返回；旧批量选择、撤销入口都可找到，未点更新不产生 POST；恢复原浏览筛选/位置 |
| S03 / 旧链接 | `/places#panel-directory`、`#panel-suggestions`、`?place_view=manage#panel-operations` 仍正确定位，Back/Forward 可用 |
| S04 / DETAIL | 点击城市→照片弹窗→关闭→返回；直接地点深链；原 `kind/key`、照片、搜索此地点、时间轴与历史恢复正确 |
| S05 / 更正 | 更多操作默认不展开；合成数据上尝试改到其他地点、排除/恢复自动及错误回显，验证归属语义和 CSRF/revision |
| S06 / 竞态与隐藏 | 管理请求迟到并切换详情，不能覆盖当前状态；窄屏、Tab 不进入隐藏区；CSS 隐藏区不占高度 |

执行 `node --check`（修改过的 JS）、`git diff --check`，复用并少量补充 `tests/unit/test_p4_ui2_place_album_nav_a.py`、`tests/unit/test_p4_place_unified_ui.py` 与批量整理定向用例。pytest 使用项目内 `.pytest-tmp` 和 `--junitxml=test-results/P4-UI2-PLACES-SIMPLIFY-A.xml`，报告实际通过/失败数，不以 collect-only 为 PASS。

**浏览器只走两条核心路线**：①地点浏览→整理→返回；②地点浏览→详情→照片弹窗→返回，可顺带观察更正入口。使用 AGENTS.md 规定的本机 `127.0.0.1`、Codex 内置浏览器、隔离合成数据、一次性会话链接；可导出时存 1–2 张真实截图。浏览器工具不支持的检查记 PENDING，不反复尝试或用模拟页冒充验收。发现真实回归再扩大相应定向测试，无须全仓回归/大型性能测试。

## 七、交付和停止边界

- 新增 `docs/reports/P4-UI2-PLACES-SIMPLIFY-A.md`，记录实际变更、定向 JUnit、浏览器观察/截图、未完成项和真实照片数据未访问声明；必要时简短更新 `docs/development-status.md`。
- 完成标准：默认地点页不再是管理长页；MANAGE 仍可进入所有旧工具；旧 hash/详情深链/Back 不损坏；更正、批量和撤销无协议回归。跳过浏览器时明确标 `IMPLEMENTED / BROWSER_PENDING`，不能声称视觉验收 PASS。
- 限域检查覆盖全部受影响前端状态与事件；若触及实际写入身份、权限、持久化或撤销语义，依 V3.1 扩大为独立高风险审查。
- **只实施本卡**，不捎带其他日期、地图底图、滚动性能或视觉收尾任务；完成即停止。

**验收一句话**：打开地点直接看相册；只有主动选择「整理地点」，才会看到识别建议、地名管理、维护和撤销。功能保留，日常浏览不暴露数据库后台。
