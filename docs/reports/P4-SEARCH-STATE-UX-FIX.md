# P4-SEARCH-STATE-UX-FIX 执行报告

- 日期：2026-10-20
- 状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
- 目的：修复搜索分页/排序/新搜索/返回时的已确认状态丢失及保存入口混放问题。

## 根因与修改

首次成功搜索才返回保存 token 和高度匹配元数据，后续分页没有这些稳定字段；旧实现却用每次响应覆盖 `lastBody` 并从当前响应显示保存按钮。时间浏览覆盖状态栏而返回相关度时没有基于确认状态重新生成统计。时间分页失败也可能丢失重试 cursor，时间快照分页入口误读相关度 `has_more`。保存对话框等待期间 token 还可能过期，原处理会继续发 POST，并在 finally 无条件重启按钮。

在 `search.js` 中增加与当前页/浏览模式分离的确认搜索元数据，集中重建结果操作与状态栏；新搜索、修改条件、服务端身份/revision 失效及快照验证按统一规则处理。时间分页保存本次请求 cursor 供连续重试；时间模式由本模式游标控制加载入口。所有异步保存动作在实际 POST 前核验原搜索对象与条件指纹。`search.html`、`search.css` 将智能合集主入口和“更多结果操作”移到独立结果操作区，并说明时间查看下仍保存原始规则。前端状态测试补入排序、分页、快照、token 失效、四种模型身份、高度匹配 0/正常/5000+/不可用语义和时间失败边界。

**P1 阻断修复复现与定位：** 隔离 harness 模拟 `/search/place-aware` 首次成功响应返回新 source token、Profile/Generation ID，而页面 capability 缓存未返回活动身份；原 `confirmedSearchCanSave()` 用严格相等，把身份未知当作不匹配，立即显示“保存凭据已失效”。同时前端另用本地 `confirmedAt` 估算 570 秒 TTL，但 API 响应并无 expiry 字段。后端路由仅在首次认证搜索响应加 token/mode；`ApiContext` 实际保存 expiry 与应用 Session hash，所有保存 API 在服务端解析 token 并校验 TTL/Session/Profile/Generation/revision。

修复移除前端本地时钟 TTL 推断和 capability 缓存作为 token 有效性权威；当前确认对象及条件指纹继续阻止跨查询串用。真正保存由 POST 服务端裁定。保存过期 token/失效应用 Session 后，清除操作资格、不重试并显示服务端错误/Session 提示。返回快照只确认搜索 Session、不确认 source token，因此保留结果展示并要求重新搜索后再保存。

## 验证

- 前端状态及静态 UI 定向测试：`tests/unit/test_search_text_mode_ui.py tests/unit/test_api_frontend.py`，最终 JUnit `test-results/p4-search-state-ux-final7.xml`：**100 passed, 0 failed**，pytest exit 0。
- 搜索保存 API 合约：token Session 绑定/TTL、过期 candidate session、Profile 切换冲突、动态智能规则保存 4 项均通过；JUnit `test-results/p4-search-state-api-final2.xml`。
- `node --check src/photo_search/web/search.js`：exit 0。
- `ruff check tests/unit/test_search_text_mode_ui.py tests/unit/test_api_frontend.py`：exit 0。
- `git diff --check`：exit 0。
- Luna High 独立只读复核：时间游标重试、时间快照分页入口、异步弹窗等待凭据过期、已绑定 Profile/Generation 身份丢失共 4 项 P2 均修复并跟进确认关闭。延迟弹窗、连续分页失败、快照游标和模型身份丢失用例加入最终覆盖。
- 中间 `r1`、`r2`、`r3` JUnit 保存了新增边界测试调试中的失败；`r4` 证明时间游标/快照测试通过。一次共享 pytest 临时根并行清理遇到 WinError 32，随后改用独立 `.pytest-tmp/p4-search-state-ux-final7` 并复跑通过。历史失败材料未删除，也不计入最终通过数。

### P1 阻断修复验证（2026-10-20）

- 复现用例先通过，证明当前旧 UI 确会因 capability active identity 缺失把带新 token 的搜索判为不可保存：`test-results/p4-search-state-blocker-repro.xml`。
- 修复后前端状态/UI JUnit `test-results/p4-search-state-p1-fix-final2.xml`：**100 passed, 0 failed**。覆盖首次 token 保存、翻页后原 token 保存、时间浏览仍保存原规则 token、token 过期和 401 Session 失效、快照不信任缓存 token，以及条件/当前 Session 切换时禁止串用。
- API 合约 JUnit `test-results/p4-search-state-p1-api-final.xml`：**6 passed**（首次 token、token/session 绑定、过期 candidate session、模型切换拒绝、智能规则操作）。扩展的实际 API 用例先发首次查询，再取下一页及时间页，确认 continuation 不重新签发 token，仍可用首次 token 保存：`test-results/p4-search-state-p1-paging-api.xml` **1 passed**。
- `node --check`、定向 Ruff、`git diff --check` 通过。未改后端代码、保存协议、数据库、模型或检索算法。

### P1 阻断续修及复核跟进

- 定向复核指出筛选快照刷新成功后仍需解除旧快照的保存重确认门禁；现在只有刷新请求成功并由服务端返回新 token 后才清除门禁，并增加筛选快照恢复用例。`PHOTO_SMART_DEPENDENCY_CHANGED` 现按搜索规则失效处理，清除凭据并要求重新确认。
- Luna High 后续发现时间浏览时保存被拒、Session 失效后，返回相关度会恢复旧 relevance cursor 并露出无效“加载更多”。失效路径现清除此嵌套 cursor；回归验证返回相关度后入口保持隐藏。
- 最终前端 JUnit：`test-results/p4-search-state-p1-fix-final4.xml`，**100 passed, 0 failures, 0 errors, 0 skipped**。保存 API 合约：`test-results/p4-search-state-p1-api-final2.xml`，**7 passed**。`node --check`、Ruff 与 `git diff --check` 均通过。
- Luna High 跟进确认筛选快照重确认、智能依赖失效、时间模式 Session 失效游标三项已关闭，未发现遗留保存安全 blocker。没有修改后端实现或保存协议。

复现命令：

```powershell
uv run --python 3.12.10 --no-sync pytest -q tests/unit/test_search_text_mode_ui.py tests/unit/test_api_frontend.py --junitxml=test-results/p4-search-state-p1-fix-final4.xml --basetemp=.pytest-tmp/p4-search-state-p1-fix-final4
uv run --python 3.12.10 --no-sync pytest -q tests/integration/test_p4_photo_collections.py::test_filter_search_snapshot_and_photo_collection_search_scope tests/integration/test_p4_photo_collections.py::test_ranked_search_save_preserves_text_candidate_order tests/integration/test_p4_photo_collections.py::test_combined_search_save_rejects_changed_active_profile tests/integration/test_p4_photo_collections.py::test_search_source_token_rejects_wrong_mode_and_tampering tests/integration/test_p4_photo_collections.py::test_search_source_token_is_session_bound_and_expires_at_deadline tests/integration/test_p4_photo_collections.py::test_ranked_search_save_rejects_an_expired_candidate_session tests/integration/test_p4_photo_smart_collections.py::test_filter_search_creates_dynamic_smart_rule_and_exclusion_is_recoverable --junitxml=test-results/p4-search-state-p1-api-final2.xml --basetemp=.pytest-tmp/p4-search-state-p1-api-final2
```

## 未验证边界

- **真实浏览器检查 PENDING**：当前 CUA 环境没有可用浏览器标签或已打开应用页，未把模拟 API/Node harness 结果描述为浏览器验收。需要在隔离合成数据的登录应用中检查桌面和窄窗口，确认首个结果操作、排序切换、加载下一页和返回快照。
- 未做真实图库、真实模型、GPU、30 万规模测试；本卡不承诺这些范围。
- 按用户后续明确授权本地提交，不推送。工作树中同时存在本卡之外的 `api/routes.py`、`scheduler/projection.py`、`search/perf_diagnostics.py`、`tests/unit/test_search_perf_diagnostics.py` 修改及 `.pytest-tmp-c2-retry-fix*` 临时目录；这些未归属本卡的改动和临时目录均保留。`search.js`、`search.html` 中与性能诊断相关的既有差异也未回退。
