# P4-02Y-W — 5000 搜索结果 + 高度匹配数量

状态：IMPLEMENTED / VALIDATION COMPLETE / INDEPENDENT DS-H FULL AFFECTED-SURFACE REVIEW PENDING
日期：2026-09-30

## 结果

- `DEFAULT_TOP_K`、`MAX_TOP_K`、`MAX_SESSION_IDS` 统一为 5000；SearchSessionStore 的现有 `len(entries) <= max_ids` 语义得到遵守，调用方也只在 `len(entries) > 5000` 时传入候选截断状态。
- `channels.vector` 加入 `strong_match_count`、`strong_match_more_possible`、`scan_truncated`。强匹配唯一内部阈值为 `STRONG_MATCH_MAX_DISTANCE = 1.5`，只计算 Vector Top-K 的 squared-L2 distance；RRF 输入和融合结果不受计数影响。SearchResult 顶层结构未变，也不向 API 暴露阈值或百分比。
- vector `truncated` 只随 `vector_scan.truncated`；前端只有 `scan_truncated=true` 才提示“向量扫描未完整完成，本次结果可能不完整。”正常 Session 容量不显示异常警告。语义搜索状态栏显示“约 N 张高度匹配”或“5000+ 张高度匹配”。
- 架构 §9.2、§9.5 同步为固定 5000 语义 Top-K/Session 上限，并说明纯筛选权威候选页仍是独立上限。
- `docs/development-status.md` 压缩至 11 行，只保留 W/V 最新交接；此前的历史交接原样转存到现有状态归档文件。

## 验收证据

- 定点契约测试：`uv run --no-sync pytest -q tests/unit/test_p4_02y_w_search_capacity.py --junitxml=test-results/p4-02y-w-contract.xml`，3 passed。API 排序等价对照 `tests/integration/test_p2_search_endpoint.py::test_strong_match_annotation_does_not_change_search_order_or_candidate_count` 独立通过，JUnit `test-results/p4-02y-w-order-equivalence.xml`。
- 完整 B 级：`uv run --no-sync pytest -q tests/unit tests/integration --junitxml=test-results/p4-02y-w-full.xml`，**1878 tests / 0 failures / 0 errors / 48 skipped**；JUnit XML 已生成。
- 30 万 smoke：复用 `tmp/p402f-阶段0/scale-300000-wave/data`，执行 `p4_02f_stage0.py --modes stages --samples 2 --top-k 5000`。status=ok；预热约 7595 ms；两次正式 Service 样本中位数 4216.28 ms；20 blocks、300000 scanned、fallback_calls=0、truncated=false；相同轮内页面与融合结果均一致。完整数据在 `test-results/p4-02y-w-smoke.json`。这是少量 warm smoke，不是新性能曲线。
- `uv run --no-sync ruff check`（改动 Python 文件）：通过；`node --check src/photo_search/web/search.js`：通过；`git diff --check`：通过。
- pytest 首轮在 `.pytest-tmp` 清理遇到沙箱 `WinError 5`，依仓库规则通过正常权限路线重跑；首轮完整测试还暴露一个旧上传表单用例仍将 top_k=1001 视作越界，已更新到 5001 后，最终全量复跑通过。未修改 ACL。

## 未决与范围

- 实现者未自审。依 V3.1 §2.4，本跨检索/API/Session 变更需另开会话做独立 DS-H 完整受影响面工程主审；当前不得标总任务 PASS。
- 不涉及 schema、migration、依赖、`uv.lock`、ANN/检索路径或真实照片库。未启动其他搜索算法优化。
- 下一步：`docs/tasks/P4-02Y-W-review.md`，建议 DS-H / DeepSeek V4.1 Flash / 高推理；模型由用户手动切换。
