# P4-SEARCH-INTENT-UNIFIED-A-FIX2 修复报告

日期：2026-10-10。结果：**修复完成 / 定向验证通过**。基线提交：`b1ffe9b fix(search): disambiguate unified intent choices`。

## 原因与修复

用户复测后仍看到 GeoNames 的“香港”与“香港（父级）”两个选项。按钮文案暗示这是一条同名父子记录路径：上一轮修复能区分选项，但解析器的来源优先规则无法在两个 GeoNames 候选间选择。

地点候选查询可选提供内部 `_parent_key`。对同一搜索跨度，只有当同名城市的直接父 key 指向另一条同名国家记录，并且来源、活动数据包一致时，解析器才移除较窄子项、采用国家范围。无法验证父身份的同名地点及城市/城市同名父子继续歧义。解析器在返回结果前剥除私有字段，手动地点选择 API 默认不请求该身份数据。

没有读取用户活动目录；具体香港两行是否构成上述父子关系仍需用户现场重启/刷新复测确认。

## 验证

- 定向 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A-FIX2-r4.xml`，**41 passed / 0 failed / 0 errors / 0 skipped**。
- Ruff 与 `git diff --check` 通过。
- 覆盖：合成 GeoNames 父 ID 投影、同名直接父子默认较大范围、无身份关联的同名地点仍歧义、统一搜索计划选中父级范围。
- 未运行全仓测试、真实浏览器或真实图库/活动目录验证。

验证命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = (Get-Location).Path + ';' + (Join-Path (Get-Location) 'tests')
uv run --no-sync pytest -q tests/unit/test_search_place_nl_ui.py tests/unit/test_p4_search_intent_unified.py tests/integration/test_p4_place_unified.py --basetemp=.pytest-tmp/P4-SEARCH-INTENT-UNIFIED-A-FIX2-r4 --junitxml=test-results/P4-SEARCH-INTENT-UNIFIED-A-FIX2-r4.xml
uv run --no-sync ruff check src/photo_search/places/choices.py src/photo_search/places/natural_search.py src/photo_search/search/personal_search.py src/photo_search/api/routes.py tests/unit/test_search_place_nl_ui.py tests/unit/test_p4_search_intent_unified.py tests/integration/test_p4_place_unified.py
git diff --check
```
