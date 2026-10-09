# P4-SEARCH-NL-UX-A — 实现与定向验收

日期：2026-10-09。状态：**IMPLEMENTED / DIRECTED VALIDATION PASS / 本机浏览器交互待补**。

## 实现范围

- 新增 `search/natural_intent.py` 确定性解析年/月/日期/相对季节与 canonical 人物，支持 all/any、别名重名消歧和安全的内容词保留；时间边界左闭右开，相对日期计划在首次解析后随续页冻结。
- 在既有 `POST /api/v1/search/place-aware` 中复用地点识别与 SearchScope。纯条件使用 `asset_id` keyset SQL 分页和完整 distinct COUNT，单页只读 `page_size+1` 行，不调用 encoder、不创建 SearchSession；语义搜索继续原有 Top5000 路径。冻结的 `POST /api/v1/search` 及其旧纯条件1000条行为未改。
- 搜索页突出自然语言框，将传统筛选折叠；日期/人物条件显示为可取消标签。纯条件页面一次只保留当前页，翻页不让 DOM 或 sessionStorage 随总结果数增长；返回恢复保留 intent plan。

## 验收证据

定向 pytest 命令（退出码 0）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q tests/unit/test_p4_search_nl_parse.py tests/unit/test_p4_search_nl_ui.py tests/integration/test_p4_search_nl_ux.py tests/integration/test_p4_place_search_flow.py tests/integration/test_p2_search_endpoint.py -k "not strong_match_annotation and not place_edit_between_pages" --basetemp=.pytest-tmp/p4-search-nl-accept --junitxml=test-results/p4-search-nl-ux.xml
```

JUnit：**27 tests / 0 failures / 0 errors / 0 skipped**。包含 5005 条纯筛选结果完整分页、编码器/会话未调用、人物 all/any、日期/消歧规则、旧搜索入口回归以及 51 页 × 100 条的前端有界窗口模拟；现有 P4 地点搜索流也纳入。两项旧 P2 测试按命令过滤：其一依赖已移除的 `search.service.STRONG_MATCH_MAX_DISTANCE` 符号，其二使用无来源证明的旧手工地点分配夹具。它们未被本次修改覆盖或修复。

其他验证：定向 Python Ruff **通过**；`node --check src/photo_search/web/search.js` **通过**；`git diff --check` **通过**。

本机浏览器尝试使用隔离合成数据服务。搜索页壳可打开，但浏览器对 `/api/v1/session` 会话交换导航返回 `ERR_BLOCKED_BY_CLIENT`，因此认证后的输入、结果翻页、查看器与返回恢复浏览器闭环未完成。未访问真实照片、PhotoSearchData、模型或 GPU；没有运行全量 pytest、性能任务或打包。

## 边界与后续

解析失败或含糊的人名/地点不会自动猜选；当前自然语言时间只按既有 EXIF 拍摄时间过滤，未知地点和否定表达不强行转成硬条件。SQL keyset 分页不承诺多个 HTTP 请求间的快照一致性，后台新增资产可能使后续计数变化。语义文本结果仍受既有 Top5000 上限约束。旧 `POST /search` 的纯条件上限未变。

核心实现和合成定向验证已完成，任务卡保留浏览器验收待补状态，不将未执行的浏览器交互标为通过。下一项已排定任务仍为 P4-PHOTO-VIEW-A。
