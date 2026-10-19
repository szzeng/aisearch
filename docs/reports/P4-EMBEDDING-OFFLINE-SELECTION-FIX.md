# P4-EMBEDDING-OFFLINE-SELECTION-FIX 执行报告

**结果：IMPLEMENTED / ACCEPTANCE PARTIAL**。离线资产共享门禁、API/CLI 选择、前端风险提示及合成回归已实现。Luna High 首轮指出的两项问题已修复并经跟进确认关闭；浏览器中显式点击「检查」和确认安排流程仍为 PENDING，未据此将本卡标为 PASS。基线：`df3246c`。

## 改动

- `src/photo_search/indexing/selection.py`：无 PRESENT 位置的 ACTIVE 资产继续通过 `offline_assets` 诊断，并在选择准备与事务提交间比较计数；它不再单独拒绝完整在线覆盖。仍标记 PRESENT 但实际不可读的资产通过 `out_of_scope.UNREADABLE` fail-closed。在线缺口、空 generation、pending/outbox、repair/stale、artifact、Lance 和事务目录摘要门禁保留。
- `src/photo_search/api/routes.py`：检查读取完整 immutable profile，核对其 space/processor 与候选 provider generation，并调用 `resolve_profile_binding` 复核实际 generation 绑定。错配时 `switch_eligible=false`；安排接口保留 `PROFILE_GENERATION_MISMATCH`，不会写入 pending transition。
- `src/photo_search/web/app.js`：覆盖标签明确为“在线可索引图片覆盖”；通过结果说明离线数量不计入分母、切换后的语义检索影响及恢复原图后需增量建库。安排对话框重复此影响提示。
- `tests/integration/test_p4_embedding_provider_flow.py`：原先“一个 MISSING 就拒绝”的旧期望改为：离线资产保留且切换成功；在线新增缺向量仍拒绝；双来源、双 MISSING 位置与零位置按唯一 ACTIVE asset 计数；PRESENT 但不可读拒绝；事务准备后恢复在线会使旧摘要失效；其它候选门禁仍拒绝；API 检查/排队与合成 stopped CLI 激活路径带离线计数通过。新增 API 错配拒绝用例及完整 CLI 恢复重扫后 `COVERAGE_MISSING` 拒绝用例，并断言不创建 pending transition / 不移动 active profile。
- `tests/unit/test_p4_embedding_check_ux.py`：覆盖通过状态的离线风险说明、安排确认警告、取消确认不发请求及确认后仅排队请求。

## 验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
$env:PYTHONUTF8 = '1'
uv run --python 3.12.10 -- pytest -q tests/integration/test_p4_embedding_provider_flow.py tests/unit/test_p4_embedding_check_ux.py tests/unit/test_embedding_management_status.py tests/unit/test_p4_embedding_management_fix.py --junitxml=test-results/p4-embedding-offline-selection-fix-final.xml --basetemp=.pytest-tmp/p4-embedding-offline-selection-fix-final
uv run --python 3.12.10 -- ruff check src/photo_search/indexing/selection.py src/photo_search/api/routes.py tests/integration/test_p4_embedding_provider_flow.py tests/unit/test_p4_embedding_check_ux.py
py -3.12 -m py_compile src/photo_search/indexing/selection.py src/photo_search/api/routes.py tests/integration/test_p4_embedding_provider_flow.py tests/unit/test_p4_embedding_check_ux.py
node --check src/photo_search/web/app.js
git diff --check
```

- 最终受影响 JUnit：`test-results/p4-embedding-offline-selection-fix-final-r2.xml`，**30 tests, 29 passed, 0 failures, 0 errors, 1 skipped**。唯一 skip 是既有 symlink 用例因 Windows `WinError 1314` 无法创建合成链接。针对新增复核点的 focused JUnit r3 为 **2/2 passed**。
- Ruff、Python 编译、Node 语法与 diff-check：通过。
- 独立工程复核：Luna High 首轮发现两项 P2：API profile 与候选身份未完整绑定；缺少恢复后停机 CLI 拒绝路径。修复后针对性跟进确认两项均关闭，无新增 finding。
- 内置浏览器：隔离管理页已通过单次本机会话打开；status API 返回 200，页面显示当前 CLIP 与 Qwen 候选。由于当前 CUA 本轮只返回页面快照，没有可用的已文档化页面点击操作，浏览器中的 check→风险提示→安排确认流程标记 **PENDING**。相关真实 UI DOM 行为由单测覆盖，后端 check/switch 队列由 API 联测覆盖，CLI 实际激活由合成 stopped 选择测试覆盖。

## 合成数据与未验范围

浏览器夹具保留在 `.tmp/P4-EMBEDDING-OFFLINE-SELECTION-FIX/browser-synthetic`：3 张合成 JPEG，其中 1 个 catalog 位置标记 MISSING；Qwen generation 覆盖 2 张在线图片。数据目录内有 5 个 0 字节模型占位文件，仅满足本地 provider 可见性，没有加载或下载模型。夹具无真实照片来源、无 GPU 运行；未执行实际停机切换。

未访问 `D:\SZ\project\PhotoSearchData`，未验证真实 Qwen/CUDA、真实索引质量或 30 万图容量。在线覆盖和切换流程证据均来自合成夹具。浏览器点击流程仍 pending，因此任务总体保留 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
