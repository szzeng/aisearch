# P4-PERSON-HIDE-UI-FIX-A — 批量管理多页选人不收起

日期：2026-10-09。状态：**PASS / 定向 DOM 回归通过**。计划基线：`3fda522b0a784264a1155b5ec60ae6c8b37b4655`；实际实施基线：`8a4bfc5299460e4e15fe4028c5aceb2e5d554bf2`。个人相册 **P4-PERSON-HIDE-A** 的前端现场反馈小修复。执行记录见[实现报告](../reports/P4-PERSON-HIDE-UI-FIX-A.md)。

## 1. 已证实的问题与目标

`src/photo_search/web/persons-albums.js` 有两条无须请求的全量重载：
- `togglePersonSelection(personId)` 在更新 `selectedPeople` 后执行 `renderPeopleManagement(); loadPeople(false)`；
- `boot()` 的 `batch-toggle` 点击事件在开启或退出批量模式后也执行 `loadPeople(false)`。

而 `loadPeople(false)` 会先 `$("cards").replaceChildren()`、清空 `listCursor`，再取首屏40人。因此用户已经通过「再看一些人物」展开80/120人后，点击一个人物就被迫重新展开。

此外，`batch-cancel` 只调用 `clearPeopleSelection()`，后者清空 Set 并更新顶部按钮，**未同步已经渲染的卡片选中边框、`aria-pressed` 与“已选择”标记**。修复时不能只删 `loadPeople(false)` 两处调用而忽略卡片同步。

**目标：** 批量选择、取消选择、开启/退出批量模式、取消选择时，已经加载的人物卡片、下一页游标及用户滚动位置保持不变；卡片视觉与 `selectedPeople` 一致。选中人物仍可跨页累积，至多500人。

## 2. 最小实现（只修改前端）

优先只改 `src/photo_search/web/persons-albums.js`；如确有必要仅少量调整 `persons.css` 和 `persons.html` 的静态资源版本以使浏览器读取新脚本。

1. 抽出一个轻量`syncPeopleCards()` / `updatePersonCardSelection()` 一类的小函数。给人物列表里的卡片关联稳定 `person_id`（例如 `data-person-id`），根据当前 `batchMode`、`selectedPeople` **就地**同步 `aria-pressed`、选中高亮与“已选择 / 点按选择”提示；仅遍历当前已加载的 DOM 卡片。勿将整张卡片替换为新节点，避免头像重复请求、丢失按钮焦点；非批量模式不保留误导性的已选状态。
2. `togglePersonSelection()` 只更改 Set、顶部“已选 N 人”和被点击卡片状态；**不调用 `loadPeople(false)`、不重置 `listCursor`、不修改 `listSeq` 或滚动位置**。取消某个人后 UI 立即消除高亮。
3. `batch-toggle` 开启/退出时仅同步现有卡片，退出时清空选择；`batch-cancel` 仍清空选择并退出批量模式，但必须同步清除所有**当前已加载卡片**的选中样式/提示。尽量不引起卡片高度跳动或页面定位突变。
4. `loadPeople(true)` 保持原有服务端 cursor 请求/追加行为；每张新卡片继续用同一选择状态投影。若「加载更多」请求尚未完成时切换模式或选择人物，最终追加的卡片必须按照**响应落地时的当前** `batchMode` / `selectedPeople` 呈现；不引入多余 API 调用或前端全量列表缓存。
5. **正常重载不改变：** 成功提交忽略/恢复后 `clearPeopleSelection() + loadPeople(false)` 仍要刷新；切换已忽略视图、人物/整理一下、修改排序/搜索条件、手动刷新仍可重新加载。提交前的确认取消/请求失败应保留当前已选集合和已展开列表，不能误清空。跨页 Set 保留逻辑、500 上限、原 CSRF/API、分页与后端数据库全部保持不变。

## 3. 个人范围最小验收

只要求 **1 个小型 JS/DOM 回归用例**（可复用现有 Node DOM 桩）和基础静态检查：

- 模拟至少 3 页 / 120 个已加载人物，在批量模式依次选中第85、20、110人，再取消第20人：已显示卡片数保持120；对应高亮、`aria-pressed` 和顶部计数正确；**没有额外 `/persons/browse` 请求**；`listCursor`、`listSeq`、滚动位置不变。
- 退出再进入批量模式、点击“取消选择”，原卡片状态及时清理；追加第4页后仍可多选；在「已忽略」恢复视图同样正常；操作失败/取消不丢选择，操作成功才重载首屏。
- 若测试 DOM 不支持真实滚动位置，至少断言不替换 `cards` 节点、不发重新加载请求、不改变已缓存 cursor；滚动由用户手动体验确认。

运行 `node --check src/photo_search/web/persons-albums.js`、该小型回归与 `git diff --check`。若涉及页面资源 URL，顺手核对缓存版本。**不做** pytest 全仓、GPU/模型、Schema/SQLite 迁移、性能专项或真实图库自动测试。

## 4. 边界、交付与推荐模型

范围：仅前端批量管理的 UI/DOM 同步与可能必要的资源版本更新，不修改 `persons/albums.py`、后端权限/隐藏表、人物关系、相册分页或人脸识别逻辑。勿为小 Bug 引入状态管理框架、虚拟列表、自动一次取全量人物或“加载全部人物”。

推荐 **GPT-6 Medium**，一轮实施 + 小型 DOM 验收即可；只核对相关事件处理和选择器逻辑，无需独立全仓审核。

交付最少：指出导致重载的两处调用、修复点、DOM 回归结果及用户手动确认事项。**PASS：** 用户已经展开多页时，可以连续勾选、取消、切换管理模式而不收起人物列表；真正提交忽略或恢复后仍能正常刷新。
