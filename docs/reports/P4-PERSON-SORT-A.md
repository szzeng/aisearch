# P4-PERSON-SORT-A 实现与验证

日期：2026-10-09。基线：`b325b0e24e8a097fe7a9ba5bcdd819ac6289c631`。结论：**PASS — 人物列表全局排序与合成回归**。本卡是只读列表功能；按 A 级直接功能回归验收。没有修改任何真实图库。

## 实现

- `GET /api/v1/persons/browse` 增加可选 `sort=person_id|photos_desc`。省略参数时保留 person_id 升序，兼容人物选择器和旧客户端。
- `photos_desc` 在服务端根据全体筛选候选人物的有效成员计数排序：去重 `COUNT(DISTINCT asset_id) DESC`，同数按 `person_id ASC`。复用 `MEMBER_PREDICATE`；同张合照多张脸只计一张，建议脸、人工移出脸与排除关系不被计入。
- 新 keyset 游标包含 count 与 person_id，并绑定列表版本、kind、规范化名字查询、排序和 `catalog_filter_revision`。跨排序续页、成员状态变化后的旧游标都拒绝为 `INVALID_CURSOR`。
- 人物日常页新增“照片最多优先 / 默认顺序”选择器，默认照片最多优先。排序切换重载第一页；后续页沿用当前排序与服务端游标。页面脚本 URL 版本已更新以避免旧缓存继续提供旧脚本。
- 未改 schema、人物关系、撤销、照片相册顺序、模型或索引。

## 验证

- 定向 JUnit：`tests/unit/test_person_sort_ui.py`、`tests/integration/test_p4_person_albums.py`、`tests/integration/test_p3_persons_read_api.py`、`tests/integration/test_p4_person_album_perf_measure.py`：**18 passed / 0 failed / 0 errors / 0 skipped**。结果：`test-results/p4-person-sort-focused.xml`。
- 新 API 回归包含：2 张照片的人物排序平局由较小 person_id 稳定打破；合照内同人的第二张脸不增加照片数；人工移出的脸不增加数；零照片人物位于末尾；三页连续结果无遗漏；`kind` 与名字查询组合；未知 sort 返回 422；跨 sort 游标拒绝；改名导致筛选 revision 变化后旧游标拒绝；缺省 sort 仍是 person_id 顺序。
- Node 最小 DOM 实际执行页面脚本，验证排序选择值发送到 API，改变排序时不带旧游标，续页仍带当前 sort 和新游标。不是完整浏览器验收。
- Ruff（修改的 Python 文件）、Node `--check`、`git diff --check`：通过。
- 额外运行完整单元/集成套件：**2154 tests / 50 failures / 43 errors / 49 skipped**（2012 passed）。与上一张相册任务的完整 JUnit `test-results/p4-person-album-perf-full.xml` 对比，失败/错误项目集合与类型完全相同：新增 0、消失 0、类型变化 0；因此未扩大任务去修既有基线。执行中 ONNX Runtime 输出 Windows native exception `0xc0000139`，pytest 继续并完成。完整结果：`test-results/p4-person-sort-full.xml`。之后仅为同一 API 回归补充 `kind+q` 与无效 sort 断言，更新后的定向集再次17项全过；产品代码未改变。

## 范围与后续

此结论覆盖合成 API/最小 DOM 与既有客户端兼容；未做真实浏览器或真实图库验收，也未测量全人物规模上的排序延迟。未接触 `PhotoSearchData`，未运行模型/GPU/打包。该只读功能无 Sol 高风险终审事项，记录 `SOL_NOT_REQUIRED`。既有 `P4-PERSON-DETAIL-B` 工程审查与 Sol focused review 已在先前闭环通过，不由本卡重开；本功能完成后转日常使用与具体反馈。

## 合并后验证与本会话审查（2026-10-09）

- 修正默认 `person_id` 游标 binding：继续使用旧 `people1` 载荷，旧客户端生成的游标仍可续页。`photos_desc` 使用独立 `people-count1` 前缀；跨排序、跨 kind/query 和 revision 变化的游标均拒绝。回归直接构造旧格式游标并验证续页。
- 照片数排序使用候选人物 CTE 后再按共享 `MEMBER_PREDICATE` 全局聚合。SQL trace 回归证明：排序查询只做一次 `COUNT(DISTINCT f.asset_id)`，当前页摘要 SQL 不重复照片数聚合；同一计数同时作为排序键和响应 `asset_count`。
- 查询计划/索引核对与小样本测量：临时合成目录 243 人、483 张脸、40 人页，SQLite 3.49.1。计划使用 `idx_face_person_links_person`；DISTINCT 与排序有临时 B-tree。31 次热缓存页面投影的中位数：ID 顺序 **0.353 ms**，照片数排序 **1.238 ms**（约3.5倍）；仅为小样本回归信号，不外推真实图库。性能专项 fixture `test-results/p4-person-album-perf-measure.json` 的 API 对照也通过。
- 本会话直接审查覆盖 `albums.py`、浏览 API 参数、`MEMBER_PREDICATE`、`loadPeople`/`loadChoices` 调用、cursor 绑定及 focused tests。审查没有发现排序计数与返回数量口径不一致；测试确认旧游标和未排序选择器兼容。按用户要求不另开审查会话，故不称为独立 DS-H 审查。
- 版本边界：人工人物更改会 bump `catalog_filter_revision`，但 `face/index.py` 的自动 `_confirm` 与新 face 关联路径不会统一 bump。故游标能拒绝已知 revision 变化，却不提供后台自动归组期间的跨页快照保证；排序值并发变化时需刷新列表。本次不新增持久版本/快照机制。
- 完整单元/集成 JUnit：`test-results/p4-person-sort-review-full-final.xml`，**2154 total / 2012 passed / 50 failures / 43 errors / 49 skipped**。与 `test-results/p4-person-album-perf-full.xml` 的 2152 项基线相比，失败/错误名称及类型完全相同，无新增失败或错误；差额为排序新增的2个测试。执行中仍出现 Windows ONNX Runtime native exception `0xc0000139`，pytest 继续至结束。定向最终 JUnit `test-results/p4-person-sort-review-focused-final.xml`：18/0/0/0。Ruff（改动 Python 文件）、Node `--check`、`git diff --check` 均通过。
- 远端 READY 卡提出的 `id_asc|photo_count_desc` 和不加排序开关，与先前执行的本地卡 `person_id|photos_desc` 可选排序器不一致。保留本地已执行卡的行为，不把拉取时出现的文档方案当作产品交互变更授权。

没有访问 `PhotoSearchData` 或真实照片，没有跑 GPU/模型、打包或真实浏览器。本卡覆盖合成数据与本地回归，不代表30万张照片性能或后台更新期间分页快照一致性。
