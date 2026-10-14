# P4-UI2-PERSON-BATCH-UX-FIX-A — 人物批量选择模式、Shift 连选与合集操作收口

- **状态**：READY / 待实施；本卡只规划修复，未实施业务代码
- **优先级**：P0（未识别人脸无法正常连选/忽略、勾选与真实状态不一致）；其余 P1（合集模式及操作精简、反选/退出回归）
- **编卡基线**：`szzeng/aisearch`，2026-10-14，`master` HEAD `2bd3285b653afb5251e4541651aa7f0546c15ed6`；施工必须重新查看最新 HEAD 与同文件改动
- **推荐模型**：GPT-6 High / LUNA High。重点是前端事件与有限状态机，不要求重写服务端；若修改人工归属、持久化合同或撤销协议，按仓库 V3.1 §2.4 升级受影响面审查
- **测试要求**：个人使用，**3～4 个定向场景 + 改动 JS 语法检查 + JUnit**；Codex 内置浏览器尽量用隔离合成数据实点 1～2 条关键路线。不要求全仓回归、外部浏览器、性能、模型/GPU 或真实照片库

## 1. 目的、现状证据及非目标

用户实际复测原任务 `P4-UI2-BATCH-SELECT-UNIFY-A`（报告 `SCOPED PASS / BROWSER_NOT_RUN_USER_REQUEST`）后发现：

1. **「整理一下 → 还没认出是谁」不能可靠用 Shift 连选人脸后批量忽略**。人物列表上方的全局 `pa-batch-toggle` 是唯一明显入口，`setView(true)` 又会清空 `batchMode`；`renderInboxFace()` 的 checkbox 只响应自身 click/change，普通卡片与候选比较卡片的点击行为不同；`selectInboxRange()` 只在同一 `rangeScope` 中连选，组间不可连选。
2. **合集 → 人物标签默认直接出现 checkbox 和「移出所选」**，而正常人物列表先点「批量管理」才进入选择，使用习惯不一致。当前 `collectionSelectedPeople` 与 `batchMode` 分离，缺乏独立的合集移出管理开关。
3. **合集标题处「合集整理」折叠菜单多余**。用户明确要求直接显示「改名」「删除合集」按钮，原有两个按钮的实际逻辑与 ID 保持不变。

代码检查还发现风险：`selectLoadedCollectionPeople(true)` 目前仅删除已选项、没有增加未选项；人脸 `renderInboxFace()` 在创建 DOM 时按 `batchMode` 一次性决定是否生成确认/修改按钮，之后只同步 checkbox；`clearInboxSelection()` 同时清空待整理与已忽略两个操作域；全局 Escape 跳过所有 INPUT（包括 checkbox）；`removeCollectionSelection()` 在请求等待阶段未设置显式防重入状态。checkbox Shift 路径使用 `preventDefault()` 和同步改 checked，可能出现浏览器复位/双切换，须复现并修正；**这些是源码风险/待验证缺陷，不能把所有风险都宣称为已浏览器复现**。

**任务目标**：三处使用一致的模式式操作——默认浏览，点击专属「批量管理/批量整理/管理合集人物」才显示选中框；已进入管理时普通点击单选、Shift 连续加选，选择与实际写入分离。

**不做**：不新增照片/搜索/地图的批量写入，不重新做地点 Shift（已完成），不重画 `P4-UI2-VISUAL-FINAL-A` 的 UI，不改变 face/person ID 语义、500 上限、自动人脸识别、数据库、图库文件与 API 协议。

## 2. 三个操作域的产品交互合同

| 操作域 | 默认模式 | 进入管理的按钮 | 管理中允许的操作 | 退出行为 |
| --- | --- | --- | --- | --- |
| 人物列表（人物、未命名、已忽略人物） | 点击人物卡进入相册 | 保留原「批量管理」 | 勾选、Shift 连选、现有忽略/合并/加入合集等 | 同按钮显示「退出批量管理」，或 Esc；清临时选择，不撤销已提交写入 |
| 「整理一下 → 还没认出是谁」 | 头像查看照片、候选确认/选择人物 | **新增该区块可见的「批量整理」**，而不是依赖另一个标签的批量按钮 | 勾选具体 `face_id`、Shift 连选、全选/反选已加载、清空、按原语义忽略所选/归属等 | 显示「完成整理」，或 Esc；正常确认/选择人物按钮恢复 |
| 合集 →「人物」标签 | 点击人物卡打开该人物相册，**不显示 checkbox 和移出工具栏** | **新增「管理合集人物」** | checkbox/整卡单击、Shift、全选/反选/清空已加载、移出所选 `person_id` | 显示「完成管理」，或 Esc；回到人物浏览、不重新加载/跳滚动 |

- 三个操作域状态必须**相互独立/明确按活动区块绑定**：至少区分人物列表批量、未识别人脸整理、合集成员管理。进入「整理一下」不应错误依赖被清掉的 `state.batchMode`；退出一个域不能误清另一个域的不可见选择。选择域、列表筛选或合集实例变化时，清除旧锚点及不再可信的选择，避免跨页错误提交。
- **勾选期间零业务写请求**。真正执行忽略、归属、合并、移出等按钮时才请求原接口及确认；已有人脸操作按具体 `face_id`，合集与人物目录按 `person_id`，不允许混同。保留合照其他脸不受影响。
- 统一清晰的视觉反馈：未进入管理时不留空复选框、不展示「已选 0 / 移出所选」工具条；管理中 checkbox、卡片边框与计数同步，复用现有 token/人物卡样式、深浅主题与焦点样式。避免交互控件嵌套。
- 键盘：正常焦点可用 Tab + Space 勾选，Esc 在 checkbox 聚焦时也能退出管理；但文本框输入中、已有 dialog/viewer 打开时不得抢先退出底层模式。写入中 / `pendingWrite` 时遵守原退出保护。
- 普通人物和合集人物的体验尽量一致：管理中点击人像/卡片空白也可选中；若需保留打开人物详情，在管理中用**清楚的独立入口**，不得让一次点击同时打开详情与切换选中。

## 3. 实现内容与建议次序

### A. P0 — 修好「还没认出是谁」人脸连选与忽略

相关：`src/photo_search/web/persons-albums.js` 的 `setView()`、`renderInboxFace()`、`syncInboxFaceCards()`、`selectInboxRange()`、`toggleInboxFace()`、`renderFaceManagement()`、`loadInbox()`/`loadHiddenFaces()`；`persons.html` 中 `#pa-inbox`。

1. 在「还没认出是谁」的标题或本区工具条放明确的「批量整理」/「完成整理」切换，进入后显示 checkbox 和既有忽略方式、忽略所选、归属按钮。未进入时保持单张查看、候选确认等现有功能；不让全局人物批量管理按钮控制本区。
2. 当前已加载且**展开后实际可见**的人脸按可视顺序连选，允许从一个已展开候选组跨到另一个已展开组、普通单项；只对真实 `face_id` 卡片作用，不将候选组 `<summary>`、参考头像或隐藏/未加载的人脸当作选择目标。折叠、筛选、排序、列表版本变化等导致锚点不可见/不可信时清锚点；无锚点 Shift 退化为单项。已选但暂时折叠的状态按既有安全合同处理，不跨到「已忽略人脸」。
3. 统一卡片与 checkbox 的点击事件：单击恰好切换一次，Shift 范围整体加选含端点，既有选集不因 Shift 清掉；取消其中一项不修改其它选择。**必须复核浏览器默认 checkbox click/change/`preventDefault()` 时序**，事件后实际 `checked`、`data-selected`、Set、计数一致，不能只验证模拟 DOM。
4. 切换整理模式时正确更新「确认是某人」「选其他人」「这是谁？」「查看整张照片」等控件的可见/可用性。不能只把 checkbox 设为 hidden 而导致原动作消失、残留或误写；参考候选头像绝不参与选择。
5. 待整理人脸与已忽略人脸保持各自独立的选集、计数、全选/反选/清空、提交按钮。两处「清空选择」只清对应域；旧批量忽略接口 `/faces/inbox/visibility` 和 `ignore_state`（暂时忽略/明确排除）不变。保留每次 500 张脸限制（超限整次拒绝），分页仅限已加载；加载下一页不能意外清选集。

### B. P0/P1 — 合集先进入人物管理，再允许多选移出

相关：`loadCollectionPeople()`、`updateCollectionSelection()`、`selectLoadedCollectionPeople()`、`removeCollectionSelection()`、`openCollection()`、`backToLibrary()`、照片/人物 tab 切换、`state.returnCollection`。

1. 在合集「人物」tab 顶部设置「管理合集人物」（默认可见），**默认显示人物卡而不显示 checkbox/全选/反选/移出/清空**；点击进入后显示标准选中 UI，按钮改成「完成管理」。
2. 管理内的卡片、checkbox、Shift、快捷操作行为与普通人物列表一致。`collectionSelectedPeople` 独立于 `selectedPeople`；按集合 `collection_id + instance_token` 限定选择范围，不能把旧合集的 `person_id` 提交给新合集。正常浏览可以点击人物并原路返回，不能返回时自动重新进入移出管理。
3. 修复**真正反选**：当前已加载成员中，已选→未选，未选→已选；跨已加载页保留不在此次视图中的既有选择；一次性计算预计选中总数、超 500 则整次不变。清空不退出模式；一次首次 Shift（无锚点）相当于普通选中。
4. 「移出所选」调用已有合集成员 API，只移除关联，不删除人物、人脸、照片或人工归属。提交中禁用勾选/管理切换/再次移出，补防重入；异常、结果未知、`pendingWrite`、合集 revision/instance 更新按现行协议保护，必要时给用户刷新提示，不能静默换 token 重发写入。
5. 切换合集「照片/人物」、进入某人物相册再返回、刷新合集、删除合集、返回所有人物时一致退出/清理临时管理模式及锚点；不牺牲已实现的浏览、搜索范围、合集成员分页、返回位置与图库刷新能力。成功移出后采用已有刷新流程。

### C. P1 — 移除「合集整理」，直接展示改名和删除

相关：`src/photo_search/web/persons.html` 的 `#pa-collection-view-controls`、`#pa-collection-rename`、`#pa-collection-delete`，必要时局部 `persons.css`。

- **删除 `<details><summary>合集整理</summary>...</details>` 包装**，将原两个按钮直接放在合集页头（与「照片 / 人物」切换同一工具条，合理分组）：**「改名」与「删除合集」**，两种 tab 下始终可用且可见。
- **复用原按钮 ID 和原事件处理**，改名调用旧对话/保存流程；删除继续显示二次确认，仅删除合集及成员关系，不删人物、照片或人脸。管理选择中若点击改名/删除，应安全结束或保护移出管理状态；禁止 pending write 期间绕过限制。
- 删除为危险操作：保留清楚的危险含义、确认内容和焦点态；不把危险操作做成主行动按钮。窄屏工具条可换行，不覆盖合集标题，也不将两按钮藏进新菜单。
- **本项不改普通人物相册的「人物整理」菜单**，不扩大范围。

### D. P1 — 同源小缺陷，必须一起收尾

- 全局 Escape 当前把所有 INPUT 都排除；限定跳过文本输入/组合输入，**checkbox 焦点允许退出**，先尊重原生 dialog 与编辑器。
- 复核 `selectInboxRange`、合集、普通人物 checkbox 的 **首次 Shift 无锚点**、重复 click/change、取消勾选、整卡与复选框事件冒泡；同一次用户操作最多更新一次 Set，视觉 `checked` 不反跳。
- 同域全选、反选、清空只针对**已加载项目**；跨页选择保留；当列表内容/顺序、候选组展开状态、`quality_view`/`kind` 或合集实例变化时，重置无效锚点。按钮 disabled、计数与实际选择保持同步。
- 「已忽略人脸」如果与「还没认出是谁」共用 `renderInboxFace`，同步修复其 checkbox、模式与退出行为；仅恢复该区所选项时不得错误混入其他域。
- 不为了代码统一而抽出全站选择框架；若小型纯函数可减少误差可以局部复用。不要改 `photo-timeline.js` / `app.js` / `search.js` / `map.js` 或地点业务，除非实际证据证明受本修复影响。

## 4. 施工边界、冲突预防

优先修改：`src/photo_search/web/persons-albums.js`、`persons.html`、少量 `persons.css`、受影响的 1～2 个现有 UI 定向测试。`persons.js` 仅在确认逐脸相册也有同类首次 Shift / checkbox bug 时最小修补。**不修改 Python 生产 API、DB schema、人物模型、自动识别、人物合并及撤销合同**；如果新需求必须触及这些，请停止越界并注明后续拆卡。

施工前按 `AGENTS.md` → `docs/development-status.md` → 本卡 → `P4-UI2-BATCH-SELECT-UNIFY-A` 的报告 → `P4-PERSON-SELECT-RANGE-A`、`P4-UI-DIALOG-ESC-A`、`P4-UI2-VISUAL-FINAL-A` 有关段落 → 最新相关 JS/HTML/CSS/测试。若同期 UI viewer、滚动加载、合集路由卡已改相同文件，以**最新代码为事实**合并，不覆盖 Viewer 统一、范围搜索、时间轴返回及 CSS cache-bust；同步更新 `persons.html` 实际改动 JS/CSS 的引用版本和对应测试断言。

## 5. 简单测试与判定

**只做小量、针对本卡的测试**，不回放原 23 项逐项矩阵。优先复用 `tests/unit/test_p4_ui2_batch_select_unify.py`、`test_person_loaded_pages_ui.py` 及相关人脸 DOM harness，新增最多必要的 1～2 个测试函数。建议 4 个代表用例：

| 场景 | 断言 |
| --- | --- |
| F1 未识别人脸批量忽略 | 进入「整理一下」→ 点击「批量整理」→ 展开两组 → 勾选第一张，Shift 点另一组的已显示人脸 → 范围全选且两端选中 → 取消其中一项 → 点击「忽略所选」；用 mock 请求核对仅选中 face_id、ignore_state，无实际图库写入。确认参考头像/组头不参与。 |
| F2 人脸退出和分区隔离 | 退出批量整理后「确认是某人/这是谁」等正常动作恢复；待整理与已忽略清空互不影响；checkbox 焦点 Esc 退出，dialog 打开时 Esc 先关闭 dialog；首次 Shift 无锚点退化成单选。 |
| F3 合集人物管理 | 默认无 checkbox/移出工具条且人物可打开 →「管理合集人物」进入 → 两点 Shift → 2/6 反选变 4/6 → 清空 → 完成管理，退出不跳位置；模拟移出请求中禁止重复提交和模式切换。 |
| F4 合集改名/删除直达 | 无「合集整理」summary，改名、删除直接可见；点击走原有 handler，删除仍有确认且只修改合集关系；窄屏布局不遮挡（静态 CSS/DOM 或可用浏览器检查）。 |

最低验证：改动 JS 跑 `node --check`；运行受影响 1～2 个定向 pytest 文件并按 `AGENTS.md` 导出 `test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml`；`git diff --check`。不强制全仓测试、Ruff/py_compile（未改生产 Python 时）、长滚动/30万图库、GPU/模型。

**浏览器建议只实看两条关键动作，避免重蹈「DOM PASS 但真实 Shift 失效」**：用 Codex 内置浏览器、`127.0.0.1` 产品服务与隔离合成数据，① 未识别人脸勾选 + Shift，② 合集先进入管理再勾选/退出；可以顺手看到直接的改名/删除按钮。按 `AGENTS.md` 合法打开应用生成的一次性 session 链接；仅当工具支持且有价值时保存 0～1 张真实截图。无法获取 fixture 或工具不能发送 Shift 时如实记 `BROWSER_PENDING` / `SHIFT_BROWSER_PENDING_TOOL_LIMIT`，**定向 DOM 通过可记代码层 `SCOPED PASS`，但不能声称真实页面问题已验证解决**。不为测试修浏览器环境，不访问 `PhotoSearchData` 真实图库。

## 6. 交付与停止条件

- `docs/reports/P4-UI2-PERSON-BATCH-UX-FIX-A.md` 说明三处修改、上述小测试的实际结果/JUnit 数、已检查的浏览器步骤或待验、前述小缺陷各自处理/复核情况；`docs/development-status.md` 只补简短链接与状态。
- **功能完成**：未识别人脸 Shift 可连选并正确提交忽略；合集默认浏览，点击「管理合集人物」才出现 checkbox 并可退出；合集页面直接显示「改名」「删除合集」且旧行为保留。反选、清空、Esc、checkbox 状态、忙时安全与三个域隔离有针对性证据。
- **代码层 `SCOPED PASS`** 不能替代浏览器 `BROWSER_PASS`；若关键 DOM 测试失败，标 `ACCEPTANCE PARTIAL` 并记录具体阻断。若合成浏览器没有人物/合集 fixture，仍需如实标未验收，不伪造图和通过结论。
- 做完这些即停止。不顺手改全站照片管理、后端协议、UI2 全站视觉或 20/30 万压力测试。
