# P4-TEST-BASELINE-LITE-B 执行报告

日期：2026-10-11。状态：**SCOPED PASS**。基线 `3c4e29e`，`master` 工作区干净。修改限于一个测试文件和本阶段文档；未改产品代码、正式契约或迁移。

## 两项旧断言

1. `test_the_frozen_contracts_migrations_and_schema_version_are_untouched`：重现时当前 P2 companion 为 SHA-256 `4270997c…0325fa`，旧预期 `ec7de5f0…e4a57`。历史 `2222450459c5a6f64f2a50de0f7c9f17d20f4c75` 中的原始 companion SHA 为 `ec7de5f0…e4a57`；经批准的 `structured_model` 加性扩展在 `c1d0a34` 引入，批准依据为 [Sol 契约冻结报告](P4-02Y-L-sol-contract-freeze.md)。测试现在通过固定 Git revision 核验历史字节，通过固定 SHA 核验当前 companion，并检查 `structured_model`、必需 model identity/weights 及非 embedding 输出约束。未动态生成 expected，也未编辑 `docs/decisions/contracts/`。
2. `test_the_person_list_page_still_counts_the_same_members`：`person_page(kind="all")` 只包含已命名人物或有有效成员的人物；`kind="unnamed"` 包含未命名人物。按实现中的 `MEMBER_PREDICATE`，C 只有 suggested 和 manual-unassigned 关系、有效成员数为 0，因此默认 all 不含 C；unnamed 视图列出 C 并计数 0。A/B/D 仍逐一与成员谓词计算值比较。未修改人物 API。

## 验证

- 修复前：两个 nodeid 单独运行，JUnit `P4-TEST-BASELINE-LITE-B-repro.xml`，**0 passed / 2 failures**，pytest 退出码 1。
- 修复后：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q --basetemp=.pytest-tmp/P4-TEST-BASELINE-LITE-B --junitxml=test-results/P4-TEST-BASELINE-LITE-B.xml tests/integration/test_p3_person_search_filter.py
```

  退出码 **0**；JUnit `test-results/P4-TEST-BASELINE-LITE-B.xml`：**43 tests / 43 passed / 0 failures / 0 errors / 0 skipped**。
- `ruff check tests/integration/test_p3_person_search_filter.py`、`python -m py_compile tests/integration/test_p3_person_search_filter.py`、`git diff --check` 均退出码 **0**。

未运行全仓回归；不据此更新历史全量失败计数或全仓门禁。
