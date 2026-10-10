# P4-SEARCH-INTENT-UNIFIED-A 实施报告

日期：2026-10-10。状态：**工程任务完成 / 定向验证通过 / 用户确认当前会话主审通过（非独立 DS-H）/ 个人现场试用待确认**。

当前会话审查记录：[P4-SEARCH-INTENT-UNIFIED-A-review-current-session.md](P4-SEARCH-INTENT-UNIFIED-A-review-current-session.md)。审查指出的原三项及复查新增 P1 均已修复；54 项最终定向用例通过。用户确认当前会话主审通过；没有执行独立 DS-H，不将本结论描述为独立 DS-H。

## 实施范围

- 新增 `search/unified_intent.py`，让合集、人物、地点、拍摄时间和已登记来源候选同时读取完整原句，再集中处理重叠、同名选择、不支持关系和剩余内容。未知视觉/OCR文字原样保留；年份包含 1900–2099，OCR 文字语境不自动变成时间过滤。
- 新增有界、版本化的计划形状和 `PersonalSearchCoordinator`。客户端计划绑定原句；服务端复核合集实例/修订、人物身份、来源 ID 和地点来源/活动包身份。相对时间与地点范围在首查计划中冻结，续页复用已确认计划。
- `/api/v1/search/place-aware` 使用统一计划，并由协调器选择原有三条执行路径：完整 SQLite 纯条件页、既有地点相册成员页、范围内 FTS/Vector/RRF。没有重写 `SearchScope`、地点成员 SQL、合集谓词或语义检索。
- 搜索页继续用现有候选区域显示范围选择；合集、人物、地点、时间和来源以可取消 chip 显示。地点/来源取消只清除该条件。明确否定、多个地点和多个时间范围返回不可执行提示。纯地点可接受“人物/合集在地点”的表达；同名 OSM/GeoNames 仍沿用现有 OSM 优先及父链判断。
- 未修改冻结的 `POST /api/v1/search`、图片上传搜索、schema/migration、模型、索引、真实图库或运行中的服务；未实施 `P4-UI-SEARCH-C`。

## 验证

实施轮定向 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A.xml`，**50 passed / 0 failed / 0 errors / 0 skipped**。首轮审查修复后定向 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A-final.xml`，**53 passed / 0 failed / 0 errors / 0 skipped**；关闭 unsupported 计划绕过回归后最终 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A-final3.xml`，**54 passed / 0 failed / 0 errors / 0 skipped**。

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
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-UNIFIED-A `
  --junitxml=test-results/P4-SEARCH-INTENT-UNIFIED-A.xml
```

受影响 Python 文件 `ruff check` 通过；`node --check src/photo_search/web/search.js` 通过；`git diff --check` 通过。地点流程单独执行时曾因缺少 `PYTHONPATH=src;tests;.` 收集失败，补齐环境后包含在上述最终套件并通过。

## 未验证与交接

- 当前会话全受影响面工程审查及复查问题均已关闭。用户确认当前会话主审通过；没有切换模型，不声称独立 DS-H。个人现场试用仍待确认。
- 真实图库、运行中的个人服务、真实浏览器、GPU/真实模型、全仓测试、30 万规模和发行包均未访问/执行；少量个人页面试用待用户正常刷新后确认。
- 没有观察或承诺候选准备性能提升。本卡涉及的 SQL/身份/范围正确性由隔离 SQLite 与合成目录测试覆盖，不代表真实数据质量或现场验收。
