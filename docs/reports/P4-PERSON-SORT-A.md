# P4-PERSON-SORT-A 实现与验证

日期：2026-10-09。基线：`b325b0e24e8a097fe7a9ba5bcdd819ac6289c631`。结论：**PASS — 人物列表全局排序与合成回归**。本卡是只读列表功能；按 A 级直接功能回归验收。没有修改任何真实图库。

## 实现

- `GET /api/v1/persons/browse` 增加可选 `sort=person_id|photos_desc`。省略参数时保留 person_id 升序，兼容人物选择器和旧客户端。
- `photos_desc` 在服务端根据全体筛选候选人物的有效成员计数排序：去重 `COUNT(DISTINCT asset_id) DESC`，同数按 `person_id ASC`。复用 `MEMBER_PREDICATE`；同张合照多张脸只计一张，建议脸、人工移出脸与排除关系不被计入。
- 新 keyset 游标包含 count 与 person_id，并绑定列表版本、kind、规范化名字查询、排序和 `catalog_filter_revision`。跨排序续页、成员状态变化后的旧游标都拒绝为 `INVALID_CURSOR`。
- 人物日常页新增“照片最多优先 / 默认顺序”选择器，默认照片最多优先。排序切换重载第一页；后续页沿用当前排序与服务端游标。页面脚本 URL 版本已更新以避免旧缓存继续提供旧脚本。
- 未改 schema、人物关系、撤销、照片相册顺序、模型或索引。

## 验证

- 定向 JUnit：`tests/unit/test_person_sort_ui.py`、`tests/integration/test_p4_person_albums.py`、`tests/integration/test_p3_persons_read_api.py`：**17 passed / 0 failed / 0 errors / 0 skipped**。结果：`test-results/p4-person-sort-focused.xml`。
- 新 API 回归包含：2 张照片的人物排序平局由较小 person_id 稳定打破；合照内同人的第二张脸不增加照片数；人工移出的脸不增加数；零照片人物位于末尾；三页连续结果无遗漏；`kind` 与名字查询组合；未知 sort 返回 422；跨 sort 游标拒绝；改名导致筛选 revision 变化后旧游标拒绝；缺省 sort 仍是 person_id 顺序。
- Node 最小 DOM 实际执行页面脚本，验证排序选择值发送到 API，改变排序时不带旧游标，续页仍带当前 sort 和新游标。不是完整浏览器验收。
- Ruff（修改的 Python 文件）、Node `--check`、`git diff --check`：通过。
- 额外运行完整单元/集成套件：**2154 tests / 50 failures / 43 errors / 49 skipped**（2012 passed）。与上一张相册任务的完整 JUnit `test-results/p4-person-album-perf-full.xml` 对比，失败/错误项目集合与类型完全相同：新增 0、消失 0、类型变化 0；因此未扩大任务去修既有基线。执行中 ONNX Runtime 输出 Windows native exception `0xc0000139`，pytest 继续并完成。完整结果：`test-results/p4-person-sort-full.xml`。之后仅为同一 API 回归补充 `kind+q` 与无效 sort 断言，更新后的定向集再次17项全过；产品代码未改变。

## 范围与后续

此结论覆盖合成 API/最小 DOM 与既有客户端兼容；未做真实浏览器或真实图库验收，也未测量全人物规模上的排序延迟。未接触 `PhotoSearchData`，未运行模型/GPU/打包。该只读功能无 Sol 高风险终审事项，记录 `SOL_NOT_REQUIRED`。既有 `P4-PERSON-DETAIL-B` 工程审查与 Sol focused review 已在先前闭环通过，不由本卡重开；本功能完成后转日常使用与具体反馈。
