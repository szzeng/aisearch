# P4-SEARCH-INTENT-UNIFIED-A 当前会话影响面审查

日期：2026-10-10。范围：本卡当前工作树的后端计划校验、个人搜索路由与三条执行路径、统一解析/Unicode span、既有地点与时间 helper、搜索前端状态，以及对应测试与任务契约。结论：原三项发现及复查新增的 P1 均已修复；用户确认当前会话主审通过。该结论是同会话工程主审，不是独立 DS-H。

## 原审查发现与修复

1. **[P1] 客户端计划可改写条件，且无版本计划首屏可执行 — 已修复。** 所有传入 `intent_plan` 都必须带当前版本。首屏由服务端按原句重建候选，校验剩余内容、人物/合集/地点/日期/来源是否来自候选；随后仍校验合集实例与修订、canonical 人物、来源 ID、地点目录身份。续页不重解析相对日期，计划过滤摘要仍由原搜索会话和游标核验。新增计划篡改、无版本计划拒绝及协调器候选校验用例。
2. **[P1] 计划的空人物/日期值覆盖手动条件 — 已修复。** 计划未设置同类筛选时保留请求中的手动人物、时间条件；两边都设置但不一致时返回 409，不静默改范围。人物匹配方式也参与冲突检查。新增 API 回归确认手动筛选未被空计划清除。
3. **[P2] NFC 来源名的匹配位置被误用于原文 — 已修复。** `_source_candidates()` 为 NFC 文本边界建立到原查询 Unicode 边界的映射；新增分解重音字符来源名回归，确认范围与剩余语义词准确。

修复回归还覆盖了无计划的手动地点取消路径：请求只取消地点时不再把原始地点语句重新带回语义查询。普通冻结 `POST /api/v1/search` 路由未改；所有路由差异限定在加性 `/search/place-aware`。

## 复查发现（2026-10-10）

4. **[P1] `unsupported` 查询仍可通过构造 `intent_plan` 执行候选硬筛选 — 已修复并验证。** `PersonalSearchCoordinator.validate()` 现在拒绝解析状态为 `unsupported` 的任何计划，并返回明确改写提示。新增 API 回归以真实人物候选构造同原句计划，断言返回 409 且 `search_request` 未被调用。
   - 证据：[unified_intent.py](../../src/photo_search/search/unified_intent.py#L240)、[unified_intent.py](../../src/photo_search/search/unified_intent.py#L291)、[personal_search.py](../../src/photo_search/search/personal_search.py#L68)、[routes.py](../../src/photo_search/api/routes.py#L2204)

## 验证

- 定向 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A-final3.xml`，**54 passed / 0 failed / 0 errors / 0 skipped**。包含统一解析与计划、既有自然语言/UI、合集/地点 API 与地点搜索流程及 unsupported 计划 API 回归。
- 受影响 Python 文件 Ruff 通过；`node --check src/photo_search/web/search.js` 通过；`git diff --check` 通过。
- 首查候选在计划验证和地点映射时复用；续页仍按当前目录身份复核。中间修复轮出现的断言/地点取消回归均已修正，并由最终 54 项定向套件覆盖通过；没有以中间失败作为通过证据。

最终复验命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_parse.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/unit/test_search_place_nl_ui.py `
  tests/integration/test_p4_search_collection.py `
  tests/integration/test_p4_search_nl_ux.py `
  tests/integration/test_p4_place_search_flow.py `
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-UNIFIED-A-final3 `
  --junitxml=test-results/P4-SEARCH-INTENT-UNIFIED-A-final3.xml
uv run --no-sync ruff check `
  src/photo_search/api/routes.py `
  src/photo_search/places/natural_search.py `
  src/photo_search/search/natural_intent.py `
  src/photo_search/search/intent_plan.py `
  src/photo_search/search/personal_search.py `
  src/photo_search/search/unified_intent.py `
  tests/unit/test_p4_search_intent_unified.py `
  tests/integration/test_p4_search_collection.py `
  tests/integration/test_p4_place_search_flow.py
node --check src/photo_search/web/search.js
git diff --check
```

## 审查边界

- 用户确认当前会话主审通过并要求完成任务卡；按此确认记录工程主审通过。没有切换模型，因此**不是独立 DS-H 审查结论**。
- 未访问真实图库或运行中的服务；未执行真实浏览器、全仓、GPU/真实模型、容量或打包验证。个人页面现场试用待用户确认。
- 未提交或推送。
