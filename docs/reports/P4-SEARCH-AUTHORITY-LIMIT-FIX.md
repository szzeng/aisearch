# P4-SEARCH-AUTHORITY-LIMIT-FIX 执行报告

日期：2026-10-23。结果：**IMPLEMENTED / 定向验证通过**。

## 修改

- `retrieval.AUTHORITY_PAGE_LIMIT` 现在引用 `sessions.MAX_SESSION_IDS`，纯筛选的权威候选列表和搜索会话共享 5000 项上限，不再分别维护互相矛盾的常量。
- 搜索会话的 `candidates_truncated` 纳入权威查询真实截断状态。超过 5000 项时，响应保留完整匹配总数、已发布数和截断标志；不宣称剩余项目可通过该会话浏览。
- P3 person/search companion 的 `MaxAuthorityPageAssets` 更新为 5000，并同步实现契约断言。
- 集成测试在隔离 SQLite 图库中创建合成照片：4319 项逐页走完并验证完整递增 ID、唯一性、首末页、总数和无截断；5005 项验证只发布 5000 项并正确报告截断。未创建照片文件或接触真实图库。

## 验证

- 定向 pytest：
  `uv run --no-sync pytest -q tests/integration/test_p2_search_endpoint.py tests/integration/test_p2_search_image.py tests/integration/test_search_text_mode.py tests/unit/test_p2_search_sessions.py tests/unit/test_p3_person_search_contracts.py --basetemp=.pytest-tmp/p4-search-authority-limit-final --junitxml=test-results/p4-search-authority-limit-final.xml --tb=short`
  结果：**114 passed，0 failed，0 errors，0 skipped**（JUnit XML）。覆盖普通文字、语义、以图搜图、会话游标、契约与合成 4319/5005 项分页。
- `uv run --no-sync ruff check src/photo_search/search/retrieval.py src/photo_search/search/service.py tests/integration/test_p2_search_endpoint.py tests/unit/test_p3_person_search_contracts.py`：通过。
- `python -m py_compile` 对上述两个实现文件和两个测试文件：通过。
- `git diff --check`：通过。
- JavaScript 未修改，无需 Node 语法检查。

## 未验证/限制

- 5000 项是现有会话容量边界；超过该数量仍返回明确截断，不支持无限制浏览。
- 未运行全仓测试、规模性能测试或真实图库验证；本任务不要求这些范围。
- 任务卡所指的 V3.0/V3.1 架构文档未出现在仓库 `docs` 文件中，无法直接编辑；现有 P3 companion 已更新，任务卡与本报告说明了适用边界。
- 未推送或发布。
