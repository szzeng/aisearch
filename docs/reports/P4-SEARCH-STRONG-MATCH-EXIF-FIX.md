# P4-SEARCH-STRONG-MATCH-EXIF-FIX 执行报告

**日期**：2026-10-20
**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL

## 修改

- `src/photo_search/search/service.py`：新增四个合法 `space_id` 前缀到既有 CLIP/Qwen 阈值常量的明确映射。冒号边界确保错误拼写与未知 provider 不匹配。`strong_match_policy()`、距离计数和向量检索路径未改变。
- `tests/unit/test_p4_embedding_provider.py`：普通版和 EXIF v2 身份资格/阈值测试；两个 EXIF v2 身份均通过真实 manifest 的 `base.identity_of()` 生成。覆盖未知/相似前缀、空身份、CLIP 1.3 与 1.300001、Qwen 1.5 与 1.500001、EXIF Qwen 5000 强匹配边界，并确认零候选仍为 available/count=0、summary 不影响候选顺序。
- 	ests/integration/test_p4_embedding_provider_flow.py：将已有 API 合成闭环扩展到四种 manifest，核对 capabilities 与搜索统计一致。
- 	ests/unit/test_api_frontend.py：断言可用性门控、0 值数值格式化与高度匹配文案。

## 验证

定向 pytest（`final-junit.xml`）：**39 passed，0 failed**。包括 embedding provider、5000 容量/计数、搜索统计不改变检索顺序、冻结分页摘要、四种真实 manifest 的 active provider capability/search API 合成路径，以及前端统计文案静态断言。

- Ruff：通过。
- Python 3.12 `py_compile`：通过。
- `node --check src/photo_search/web/search.js`：通过。
- `git diff --check`：通过。

JUnit 位于忽略目录 `test-results/p4-search-strong-match-exif-fix/`。

## 未验证及基线问题

没有加载 Chinese-CLIP/Qwen EXIF v2 真实模型，也没有访问真实 PhotoSearchData；不构成模型质量标定或真实环境行为证据。未进行真实浏览器交互。

按用户指定的更宽测试组合运行时，`tests/unit/test_api_frontend.py` 有 2 个既有 restore/layout 静态断言失败；`tests/unit/test_p4_search_nl_ui.py` 的 4 个 harness 用例因 stub `window.addEventListener` 缺失而失败。未改动这些非本次范围的断言或 harness。Node 语法和本次统计文案断言通过。
