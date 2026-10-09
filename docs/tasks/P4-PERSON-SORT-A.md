# P4-PERSON-SORT-A — 按有效照片数排序人物

日期：2026-10-09。状态：**本地 A 级范围已实现；远端 READY 卡的额外要求待核验**。基线：`b325b0e24e8a097fe7a9ba5bcdd819ac6289c631`。本卡由用户明确启动；上一张 P4-PERSON-ALBUM-PERF-A 已 PASS，但明确排除本功能。实现与结果见[报告](../reports/P4-PERSON-SORT-A.md)。

## 目标

人物页提供全局“照片最多优先”排序，让常用人物更容易找到。排序必须由服务端在完整候选人物集合上完成，再进行有界分页；不能只重排当前已加载的一页。

## 范围与不变量

- 在 `GET /api/v1/persons/browse` 增加可选 `sort=person_id|photos_desc`；缺省 `person_id` 保持既有调用和顺序兼容。`photos_desc` 按 `asset_count DESC, person_id ASC` 稳定排序。
- 排序计数必须复用当前权威 `MEMBER_PREDICATE` 与去重 `COUNT(DISTINCT asset_id)` 语义；同图多脸只算一张照片。仍只列 canonical 人物，并保留 `all/named/unnamed`、名字查询、ACTIVE/空未命名过滤、封面与响应字段。
- `photos_desc` 使用 count + person_id keyset continuation，游标绑定 kind、规范化 q、sort 和 `catalog_filter_revision`。损坏游标、跨排序/查询续页或 revision 变化按现有浏览列表错误路径拒绝；不得静默换序或重复/漏项。
- 人物日常浏览增加排序选择器；切换排序/视图/查询从第一页重新加载，清空旧卡片与游标。每次请求仍由 API 返回服务端排序后的全局页面。人物选择器及其他旧客户端省略 sort 时维持旧顺序。
- 只读查询和显示改动；不改 schema、迁移、人物身份、人工关系/锁/排除/撤销、写协议、照片相册顺序、模型、索引或真实数据。

## 允许修改

`src/photo_search/persons/albums.py`、`src/photo_search/api/routes.py`、`src/photo_search/web/persons-albums.js`、`src/photo_search/web/persons.html`、直接相关的 `tests/integration/test_p4_person_albums.py` 与必要 UI 测试、此任务卡、报告和 `docs/development-status.md`。若实现证明还需其他文件，先限定到直接调用面并说明。

## 架构与策略

已按缺卡规则对齐当前架构与策略；直接条款为 V3.0 §8.3/§8.3.1、§10.1/§10.2，V3.1 §2、§3.2/§3.4、§4、§9及§2.4。全局排序源自 `docs/plans/P4-PERSON-UX.md`：不能只对客户端当前页排序。V3.1 推荐 DS-L / DeepSeek V4.1 Flash / 低推理用于普通 UX 功能与小型多文件修改；模型切换由用户手动完成。

## 验收（A 级：低风险、只读人物列表功能，JUnit XML）

- 新增 API 回归：照片数降序、同数按 person_id 升序、同图多脸去重、有效成员/排除/手动移除过滤、查询与 kind 组合、跨页无漏项/重复、游标跨排序拒绝、成员 revision 变化拒绝。
- UI 定向验证：排序选择器提交 sort；切换后回第一页并清理游标；续页沿用当前 sort；名字选择器省略 sort 时仍可工作。
- 定向 pytest：`tests/unit/test_person_sort_ui.py`、`tests/integration/test_p4_person_albums.py`、`tests/integration/test_p3_persons_read_api.py`，写入JUnit XML；Ruff（修改的 Python 文件）、Node `--check`（修改的 JS）、`git diff --check`。
- 可附加运行完整套件确认仓库基线是否变化；全量基线问题不扩展为本卡修复范围。
- 测试使用项目内合成 fixture；不接触 `PhotoSearchData`。保留既有全仓基线失败，不新增 skip/xfail、不改测试 oracle 绕过失败。

## 停止点与下一步

完成人物全局排序、直接回归和个人使用页面可选排序器后停止；不启动性能专项或扩大到真实图库。该只读小功能不需 Sol 终审，报告记录 `SOL_NOT_REQUIRED` 及理由。既有 P4-PERSON-DETAIL-B 工程审查及 Sol focused review 已通过；此卡完成后转日常使用与具体反馈，不自动续开性能优化任务。

## 合并远端 READY 卡后的对齐记录（2026-10-09）

`origin/master` 同名 READY 卡在本地实现完成后到达。该卡包含更严格且部分不同的验收条件，不能用本卡原 PASS 记录代替：

- 远端计划的枚举值是 `id_asc|photo_count_desc`，实现使用 `person_id|photos_desc`；API 缺省仍为旧 `person_id` 顺序。远端要求人物主列表固定照片数降序且不增加排序开关；实现提供选择器并默认照片数优先，切回默认顺序也可用。
- 排序 SQL 已求全局有效照片数，但随后 `_album_summaries` 会为当前页再次聚合计数；远端要求复用分页查询计数以避免本页重复计算。该项尚未调整。
- 未记录旧/新 SQL 的 `EXPLAIN QUERY PLAN` 对比或合成耗时；不声称性能验收通过。
- 本地报告未包含针对排序游标/API/调用面的独立 DS-H 全面审查；该审查尚待完成。

因此，原实现报告的定向功能回归结论仍成立，但在确认适用卡片范围、完成重复计数处理及远端卡要求的查询计划/独立审查前，不应把远端 READY 卡的全部验收标为完成。
