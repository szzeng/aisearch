# P4-02Y-W — 独立 DS-H 全受影响面工程主审

**状态：READY**

**执行模型：DS-H / DeepSeek V4.1 Flash / 高推理。** 本卡是 P4-02Y-W 的独立审查阶段；必须在新的 Codex 会话中执行，
不得由 P4-02Y-W 实现者自审。模型切换由用户手动完成。

## 目标

独立审查 5000 搜索结果 + 高度匹配计数的完整改动与验收证据，决定 `ENGINEERING REVIEW PASS` 或 `FAIL`。本审查不接管实现修复；
发现问题时给出可复现的最小反例、受影响面和修复建议，等待修复轮。

## 必读顺序与范围

1. `AGENTS.md` → `docs/development-status.md` → `docs/tasks/P4-02Y-W.md` → 实现报告 `docs/reports/P4-02Y-W-5000-results-strong-match.md`。
2. 检查 Git diff 与 JUnit `test-results/p4-02y-w-full.xml`、合同测试 XML `test-results/p4-02y-w-contract.xml`、排序等价 XML `test-results/p4-02y-w-order-equivalence.xml`、30 万 smoke JSON `test-results/p4-02y-w-smoke.json`。
3. 完整检查本卡所有改动文件、直接调用链和测试：
   - `src/photo_search/search/retrieval.py`
   - `src/photo_search/search/service.py`
   - `src/photo_search/search/sessions.py`
   - `src/photo_search/search/contracts.py`、`src/photo_search/api/models.py` 中的请求/响应接口（确认未误改顶层响应契约）
   - `src/photo_search/web/search.js`
   - 所有 P4-02Y-W 改动测试
   - `个人图片搜索系统_V3.0_开发主架构.md` §9.2、§9.5 与本任务/报告/状态文档
   - `docs/development-status-archive-2026-09-27.md` 追加的 2026-09-30 归档段：核实从 active status 移出的旧段完整保留，active status 只留 W/V 交接

按需读取 V3.1 §2.4、§2.2、§3.4；不重读全架构或无关实现。此为检索/API/Session 跨面功能，无适用的窄专项审查类别，故采用 DS-H 完整受影响面主审。

## 必审不变量

- `DEFAULT_TOP_K = MAX_TOP_K = MAX_SESSION_IDS = 5000`；产品默认固定 5000，UI 不暴露 top_k 设置。
- 强匹配唯一门限常量为 `STRONG_MATCH_MAX_DISTANCE = 1.5`；只使用 Vector Top5000 的 `distance` 统计。验证 1.49 / 1.5 / 1.500001 边界及第 5000 条决定的 `strong_match_more_possible`。
- 强匹配仅添加计数，不过滤、隐藏、重排或减少候选；SearchResult 顶层字段不变，API 不暴露阈值或百分比。
- vector `scan_truncated` 必须直接映射 `vector_scan.truncated`；不得再以 vector 候选数量/top_k 推断扫描不完整。真正扫描截断显示卡面指定警告；正常 5000 容量、5000 strong、或 RRF 结果超过 Session 容量均不弹异常警告。
- `len(entries) <= 5000` 不截断；`>5000` 保存前 5000 并标记截断；分页可达第 5000 个 ID。
- 性能证据实际使用 P4-02Y-V 的 30 万固定夹具，top_k=5000、20 blocks、300000 scanned、fallback=0、scan_truncated=false。

## 审查输出

写入 `docs/reports/P4-02Y-W-review-dsh.md`。逐项记录覆盖文件/链路、每项卡面条件结论、独立复核的命令与证据、遗漏风险、V3.1 §2.2 升级判断，以及结论：

- PASS：`ENGINEERING REVIEW PASS`，无 Sol high 必选项；更新 P4-02Y-W 与 `docs/development-status.md` 为 PASS 并停止，不创建下一搜索优化卡。
- FAIL：保留最小可复现反例和准确修复范围；状态为 `ENGINEERING REVIEW FAIL / FIX REQUIRED`，不扩大范围。

不得只引用实现报告；先核对报告与当前代码、JUnit、smoke artifact 是否一致。无需机械重跑已通过的昂贵全套；证据矛盾或缺失时只补跑相应范围。
