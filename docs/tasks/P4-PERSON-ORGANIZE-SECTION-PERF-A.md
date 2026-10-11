# P4-PERSON-ORGANIZE-SECTION-PERF-A — 人物整理分块加载及最近照片排序优化

日期：2026-10-11。状态：**READY / 仅立卡，尚未实施和测试**。

**执行建议**：GPT-6 Luna / High；个人相册、同一会话完成实现与定向简测。模型切换由用户手动操作。

## 1. 目标与实施边界

一次完成：
1. 「人物合集 / 未命名人物 / 待整理人脸」三区块独立加载，先完成的区块立即可用。
2. 移除无必要的首屏查询，仅针对有证据的慢 SQL 做局部优化。
3. 人物照片数相同时，按**最新有效照片的首次入库时间**排序；保持全局正确分页。

只读查询、页面交互与排序调整；**不改 schema、模型、识别结果、归属写协议、撤销、媒体缓存、向量库或真实 PhotoSearchData**。不做全局计数缓存、后台线程池或队列重构。

> 「最新照片」特指 `assets.created_at`（图片首次入库时间），**不是** EXIF 拍摄时间、`persons.updated_at`，也**不是**用户最近人工归类的时间。本次不新建人工归入事件时钟。旧图今天才被人工归给人物，不因该动作自动置顶。

## 2. 最新代码依据（实施时先核对 HEAD/工作区）

- `src/photo_search/web/persons.html`：顶层「人物 / 整理一下」共用同一页面；合集面板默认隐藏；整理模式显示未命名人物与待整理人脸区块。
- `src/photo_search/web/persons-albums.js::boot`：未展开合集也调用 `loadCollections(false)`；`setView(true)` 独立启动 `loadPeople(false)`、`loadInbox(false)`，并触发补识别控件统计。
- `loadInbox`：待整理卡片与 `/faces/inbox/summary` 的结果在同一个 `Promise.all` 中等待，然后绘制卡片；汇总慢/失败会拖住卡片。
- `persons/albums.py::people_page`：`photos_desc` 在完整候选集合聚合 `COUNT(DISTINCT f.asset_id)` 再全局排序，现用 `person_id ASC` 打破平局，照片数游标位置为 `[count,person_id]`。
- `persons/collections.py::people_page`：合集人物也按数量降序、ID 升序；使用独立合集游标前缀与实例/revision 绑定。
- `face_person_links.created_at` 可能因合并、撤销重建关联而改变；`face_person_links.updated_at` 也会因重复识别变化。为了反映「有新照片入库」，使用 `assets.created_at`，仅在**当前有效成员照片**范围内取 MAX。
- P4-PERSON-AVATAR-REUSE-A 已完成 A+B 定向验证、C 后置，真实浏览器缓存仍未验证。保留其 60 秒/变更失效、DOM 与 `<img>` 复用、分页/并发保护；不能覆盖尚未提交的代码。

## 3. 前端三区块独立加载（必做）

### A. 人物合集

- 默认收起，首次进入页面**不请求** `/persons/collections`；仅在展开时加载最多 40 个合集及计数。
- 再次展开在未变更时复用已加载列表，新增/改名/删除/成员变更/显式刷新时按原权威接口失效或更新。
- **批量加入合集的下拉选项必须按需可用**：不要把选择器错误绑定到已收起的合集面板；如现有数据不足，按原分页正确获取，不允许空选择器造成操作中断。
- 请求失败只提示本区块，提供简易重试，人物和待整理人脸不受影响。
- 保持现有分页、合集照片总数、成员身份、跨标签变化与删除/重建合集实例保护。

### B. 未命名人物

- 进入「整理一下」即加载当前筛选对应的第一页最多 40 人；请求完成后立即渲染，不等待待整理人脸卡片、汇总或补识别预览统计。
- 继续支持「只看未加入合集」、隐藏、质量筛选、批量操作、更多人物、滚动返回及头像复用。
- 独立的加载/空结果/错误状态；旧请求不可覆盖新筛选或写入结果。
- 不允许「先取前 40 人再排序」；后端仍负责全局数量优先排序。

### C. 待整理人脸

- `/faces/inbox/groups`（或非分组模式下的 `/faces/inbox`）和 `/faces/inbox/summary` **分开消费**；卡片返回后立刻展示，汇总稍后更新。
- 汇总进行中显示「统计中」；失败显示「统计暂不可用」，不可误报总数 0，更不得清空已成功的卡片。
- 每页最多 30 个候选组或单脸；未展开的组不读取全部子脸。已忽略人脸列表仍按展开时加载。
- 保留机器建议、低质量、人工移出、未匹配筛选，分组排序及组内身份纠错/确认/忽略/恢复、批量和撤销操作。
- 各区块有独立请求序号/失效规则；显式刷新和确实影响多个区块的写操作按需刷新，避免多次重复请求。不要因单一汇总失败让全部 `loadInbox` 抛错。

### D. 调度和实际阻塞

- 独立完成不等于后端一定并行：现有 `async` 路由可能直接调用同步 SQLite 查询。只用简单网络时序与定向合成数据判断是否出现同一事件循环阻塞。
- 优先减少隐藏合集、未用补识别统计等不必要的启动请求；前端必要时调整请求发起顺序，不引入新调度框架。
- 若确认需要线程/SQLite 连接所有权重构，**停止扩范围，报告问题另立卡**。

## 4. 新人物排序：新增模式、保留旧模式（必做）

现有 `GET /api/v1/persons/browse` 的 `sort=person_id|photos_desc` **不改变既有语义/游标**。

增加独立的 `sort=photos_recent_desc`（命名若需微调应前后端统一）；人物页、未命名人物页默认请求新模式：

1. `asset_count DESC`：按当前有效成员的不同照片数量降序。
2. `latest_asset_added_at DESC`：数量相同，当前有效成员照片中 `MAX(a.created_at)` 较新者优先。
3. `person_id ASC`：数量和时间相同稳定打破平局。

在 `persons/albums.py::people_page` 已有的 `effective_counts` 中，按 `MEMBER_PREDICATE` 对**相同成员集合**同时投影：

```sql
COUNT(DISTINCT f.asset_id) AS asset_count,
COALESCE(MAX(a.created_at), 0) AS latest_asset_added_at
```

`a` 通过 `faces.asset_id = assets.asset_id` 关联；**不得额外加入与旧照片数量口径不同的过滤条件**。没有有效成员时最近入库时间按 0 排序。不能为 MAX 再执行逐人物全库查询。

- 新排序使用独立游标版本/前缀，游标位置为 `[asset_count,latest_asset_added_at,person_id]`，续页谓词必须与降序/升序的三重排序严格一致；检查同数量/时间平局跨页、改筛选和 revision 失效。
- 保留 `sort=photos_desc` 原来的 `[count,person_id]` 游标及老调用；原 `sort=person_id` 的默认 ID 游标不变，不让人物选择弹窗意外改为新排序。
- `src/photo_search/api/routes.py` 只扩展新模式的入参白名单。
- `persons/collections.py::people_page` 的合集人物采用**同样的新平局规则**，独立升级合集人物游标前缀，保留合集 `collection_id/instance_token/revision` 和现有 `filter_revision` 绑定；旧合集游标拒绝并提示刷新，不静默错误续页。
- **待整理人脸不改**：仍为 `photo_count DESC, stable_key ASC`；它们并非统一的「人物最新入库照片」模型。
- 当前后台自动确认可能不推进 `catalog_filter_revision`：不可宣称并发建库期间所有跨页结果具有快照一致性。变化后刷新重新取第一页，保留原有冲突/失效处理。

## 5. SQL 局部优化（先测后改）

核对：
- `persons/albums.py::people_page` 的候选总数、有效成员照片去重和排序；
- `inbox_group_page` 的候选聚合、代表人脸投影；
- `inbox_summary` 的汇总；
- `persons/collections.py::people_page` 的合集内人物排序。

用现有合成测试与 `EXPLAIN QUERY PLAN` 检查明显重复扫描、JOIN 放大或每卡额外 SQL。优先复用现有聚合，并可把当前最多 30 张代表人脸的逐卡投影改为有界批量查询；**仅确认确实有收益且语义等价时改**。避免为性能改动新增持久数据、迁移、索引或全局缓存。记录前后查询时间即可，不要求提升百分比。

## 6. 个人项目最小验收：五组

只新增必要的测试桩/断言，优先复用 `tests/unit/test_person_loaded_pages_ui.py`、`tests/unit/test_person_count_ui.py`、`tests/integration/test_p4_person_albums.py` 及已存在的合集分页测试。

1. **三区块**：默认无合集请求；展开加载；未命名人物和待整理卡片分别能先显示，汇总延迟/失败不会阻断卡片；批量选合集仍可用。
2. **平局排序**：照片数不同、照片数相同而 `assets.created_at` 不同、数量和时间均同三种情况；含 0 有效照片。
3. **有效成员**：合照去重、人工排除/移出、合并/撤销后当前集合的数量与最新入库时间一致；旧人物改名或重复更新 link 的 `updated_at` 不导致无端置顶；新入库且归入有效照片能置顶。
4. **游标**：两页以上无重漏；新旧 `browse` 排序游标互不混用；合集人物升级后的游标可继续、旧版本拒绝；筛选/revision 变化走现有失效路径。
5. **写操作和并发**：人工确认、合集成员变化、刷新、旧请求迟到时不串区块、不覆盖已完成数据。

只跑改动直接相关的 pytest 并生成 JUnit XML，再跑 `node --check src/photo_search/web/persons-albums.js`、修改的 Python 文件 Ruff 和 `git diff --check`。不跑全仓、真实图库、GPU、万人压力、打包或多轮专项审核。

## 7. 交付和停止边界

允许文件：`src/photo_search/web/persons-albums.js`、`src/photo_search/web/persons.html`、必要样式、`src/photo_search/persons/albums.py`、`src/photo_search/persons/collections.py`、`src/photo_search/api/routes.py`、直接测试、报告与状态文档。

交付报告 `docs/reports/P4-PERSON-ORGANIZE-SECTION-PERF-A.md`：列明实际加载时序、减少的请求、SQL 前后简要证据、新排序/游标结果、五组定向 JUnit、未验证的真实浏览器/图库性能；实施结束同步 `docs/development-status.md`。

验收重点：**三区块独立响应、合集不预加载、待整理卡片不等待汇总、人物数量优先且最新入库照片打破平局、全局分页准确、原有人工操作/头像复用不退化**。

若发现必须新增持久时钟/索引/迁移或改动 SQLite 线程模型、身份写协议，停止扩大本卡范围，报告证据另立卡。不得声称未测的真实图库提速。
