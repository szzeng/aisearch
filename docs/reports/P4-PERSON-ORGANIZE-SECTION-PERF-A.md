# P4-PERSON-ORGANIZE-SECTION-PERF-A — 人物整理分块加载及最近照片排序

日期：2026-10-11。基线 HEAD：`54603ce`。结论：**SCOPED PASS**（任务卡限定的合成 API/UI 与定向验收）；工作区变更未提交。

## 实施

- 页面启动不再请求收起的合集列表。展开时加载并缓存页面；整理人物时不触发合集请求。批量加入合集时按需读取完整分页，再使用同一权威合集/实例接口提交；合集请求失败有本区块重试。
- 人物页与未命名人物页默认请求 `sort=photos_recent_desc`。新模式先按有效照片数降序，再按有效成员照片的 `assets.created_at` 最大值降序，最后按 `person_id` 升序；有效成员聚合仍复用 `MEMBER_PREDICATE` 和 `COUNT(DISTINCT f.asset_id)`。没有有效成员时最近时间为 0。
- 新排序用 `people-recent1` 前缀及 `[count, latest_asset_added_at, person_id]` 游标。`person_id` 和既有 `photos_desc` 查询及游标语义保留。合集人物同步使用三段排序和独立 `pcp-recent1` 前缀；旧合集人物游标返回现有刷新错误。
- 待整理卡片页与 summary 分开请求：卡片可先展示；summary 延迟时显示“统计中”，失败时显示“统计暂不可用”并允许单独重试，不清理已加载卡片。人物、卡片、summary、合集列表分别保护迟到响应并显示本区块错误/重试。
- 整理页仍异步读取活动补识别任务状态，避免隐藏正在运行的任务；来源列表和空闲预览计数延迟到用户使用补识别功能时读取。
- `inbox_group_page` 将最多一页代表脸投影合并为一次有界 `IN` 查询。未改 schema、索引、写协议、undo、模型或图库数据。

## SQL 与时序证据

使用 30 张合成待整理照片核对 `EXPLAIN QUERY PLAN`。人物新排序与合集人物计划使用现有 face/person 主键或索引查找；可见 `COUNT(DISTINCT)` 与全局排序临时 B-tree。待整理组聚合计划包含对候选人脸的扫描、排序/分组临时 B-tree；summary 计划包含多项关联过滤。本卡没有 30 万规模证据证明这些计划需要持久索引，因此未扩展该范围。

代表脸投影同形对照的一次本机样本：旧等价查询 **30 次 / 0.571 ms**，新批量查询 **1 次 / 0.097 ms**。这是 30 行合成 SQLite 投影的单次计时，不是端到端速度承诺或显著性结论。ASGI 合成时序样本中，`/health` 单独 1.382 ms；并发提交组页与 health 时分别为 32.825 ms 与 1.174 ms。此测试客户端样本没有观察到 health 延迟，但不足以证明真实 Uvicorn 运行时的事件循环调度；没有据此调整 SQLite 线程所有权。

## 五组验收

1. **三区块与选择器**：Node DOM 桩确认页面进入时无合集请求；45 个合成合集按两页加载后可选并提交；summary 延迟/失败时待整理卡片已显示且保留。补识别状态仍可见，未请求空闲预览或来源列表。
2. **排序与游标**：覆盖照片数优先、同数量按最新入库时间、同数量/时间按 ID、零有效照片、跨页以及新旧排序游标隔离。API 路由接受新排序，默认 `person_id` 与旧 `photos_desc` 用例仍通过。
3. **有效成员**：覆盖合照去重、人工移出后从排序成员集中排除、较新的 link `updated_at` 不置顶、合并/撤销后计数恢复，以及新合成入库照片被有效归属后置顶。
4. **分页与版本**：人物多页无重漏；过滤/revision 现有失效测试复用通过；合集人物新游标续页，旧前缀拒绝。
5. **写入与迟到响应**：批量加入合集仍可用；现有直接测试覆盖人脸确认、列表刷新/写入后游标刷新和旧请求不能覆盖新查询；合集成员变更及实例/revision 绑定测试通过。

## 验收结果与边界

- Python 3.12.10 定向 JUnit：`test-results/P4-PERSON-ORGANIZE-SECTION-PERF-A.xml`，**29 tests / 0 failures / 0 errors / 0 skipped**，退出码 0。
- `node --check src/photo_search/web/persons-albums.js`、6 个修改 Python 文件 Ruff、相同文件 `py_compile`、`git diff --check` 均通过。
- 未运行全仓回归、真实浏览器、真实 PhotoSearchData/图库、30 万规模或 GPU 测量；不声称真实图库提速或本机试用性能门槛通过。只使用测试生成的隔离合成图片与 SQLite 数据。
- 执行建议为 GPT-6 Luna / High；未尝试自动切换模型。
