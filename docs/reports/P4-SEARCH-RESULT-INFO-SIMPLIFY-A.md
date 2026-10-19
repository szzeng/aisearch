# P4-SEARCH-RESULT-INFO-SIMPLIFY-A 执行报告

**日期**：2026-10-19
**状态**：PASS（本卡定向验证范围）

搜索结果卡片不再输出“语义就绪”、通道名次或独立“查看详情”按钮；保留总体排名、命中原因、匹配位置、缩略图预览、预览中的详情入口和分页。`semantic_ready`、`matched_by` 等结果字段及请求/搜索逻辑未改。同步更新 `docs/diagnostics/search-smoke-checklist.md` 的旧展示预期。

## 验证

- `node --check src/photo_search/web/search.js`：通过，exit 0。
- `uv run --no-sync python -m pytest -q tests/unit/test_search_text_mode_ui.py tests/unit/test_api_frontend.py --junitxml=test-results/P4-SEARCH-RESULT-INFO-SIMPLIFY-A/frontend.xml --basetemp=.pytest-tmp/P4-SEARCH-RESULT-INFO-SIMPLIFY-A`：99 passed，exit 0。
- `uv run --no-sync python -m pytest -q tests/unit/test_p4_favorites_ux_b_ui.py --junitxml=test-results/P4-SEARCH-RESULT-INFO-SIMPLIFY-A/favorites.xml --basetemp=.pytest-tmp/P4-SEARCH-RESULT-INFO-SIMPLIFY-A/favorites`：4 passed，exit 0。
- 语义结果断言验证“第 33 名”和匹配位置仍显示，技术字段不显示；现有文字搜索用例继续覆盖分页、照片预览及详情返回。收藏共享 UI 用例通过。
- 续改删除详情按钮后，Node 语法检查通过；`test_search_text_mode_ui.py` 与 `test_api_frontend.py` 合计 99 passed，exit 0。另有一次中间运行因静态契约仍要求被删除按钮的 click handler 而 1 项失败；将契约改为检查缩略图预览 handler 后，最终重跑通过。

浏览器截图、真实图库、模型、GPU、全量回归及规模性能均按任务范围未执行。展示改动不增加性能工作，也不建立延迟或容量结论。原始 JUnit 留在忽略目录 `test-results/P4-SEARCH-RESULT-INFO-SIMPLIFY-A/`。

## 实际变更

- `src/photo_search/web/search.js`
- `tests/unit/test_search_text_mode_ui.py`
- `tests/unit/test_api_frontend.py`
- `docs/diagnostics/search-smoke-checklist.md`
- `docs/tasks/P4-SEARCH-RESULT-INFO-SIMPLIFY-A.md`

本地独立 commit 记录在 Git 历史中，不推送。
