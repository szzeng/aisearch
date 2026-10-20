# P4-LIBRARY-RETENTION-C2-FINAL-FIX — 恢复与启动可靠性收口

**状态**：PASS（限定修复与隔离合成验收范围）
**基线**：`5dcb43f`；执行前已核对 `origin/master` 与本地 `master` 同步。
**范围**：仅修复耗尽尝试的崩溃恢复、同进程 runner 退出竞态，并完成隔离合成浏览器验收；不提交、不推送、不访问真实图库。

## 必读范围

- V3.1 §2.4、§3.2/3.4；`docs/testing.md`、`docs/performance.md`。
- 当前 C2-F 卡及其报告；实现限 `retention_runner.py`、`library_retention.py`、`api/routes.py`、`api/context.py` 和直接相关测试。

## 性能设计（PERF-BOUNDED）

- 链路与风险：单个持久清理 run 的批次恢复、结算及启动；新增执行线程、重试或重复统计会破坏有界串行语义。
- 做法与边界：复用现有每批最多 20 项与单 runner；仅归一化达到 3 次上限的 QUEUED/RUNNING 项，不增加查询服务或后台线程。
- 验证与停止：T1–T6、受影响 pytest/JUnit、Ruff、py_compile、Node 语法、diff-check及指定浏览器流程；不新增规模性能结论，真实图库与全仓测试不在范围。

## 验收

1. attempt_count 达到上限仍为 QUEUED/RUNNING 时恢复归一化为 FAILED，保留原因或记录明确中断原因；批次推进一次，统计不重复，其他资产继续处理。
2. 未耗尽次数的 RUNNING 项可恢复；ACTIVE barrier、DELETE_VECTOR 与 tombstone 不变量保持。
3. runner 正在退出时接收的新 run 自动接续；closed 明确拒绝；并发 start 不产生第二个 worker。API/UI 状态反映持久运行状态。
4. T1 崩溃后第三次 RUNNING 恢复；T2 后续资产不被阻塞；T3 上限不重复执行/累计；T4 退出竞态续接；T5 并发单 worker；T6 PARTIAL/再次点击/ACTIVE barrier 授权回归。
5. 隔离合成库浏览器完成取消、确认、跨批次统计、刷新/离页返回、失败后新 run；如确认框工具仍阻断，相关浏览器项 PENDING，整体 `IMPLEMENTED / ACCEPTANCE PARTIAL`。

## 验证命令

使用 Python 3.12.10、项目 `.venv` 和隔离 `PhotoSearchData`；仅运行受影响 pytest 并输出 JUnit XML、Ruff、py_compile、Node `--check`、`git diff --check`。不跑 GPU、真实图库、全仓及 30 万照片性能测试。

## 独立复核

按 §2.4 对 runner 状态机、数据恢复及全部改动面进行独立审查；沿用用户指定的 Luna H 子代理路线。高风险未决项须维持 partial。

## 结果

- 完成耗尽尝试恢复归一化和同进程 runner 串行接续；closed runner 的 API 启动请求明确返回 503，并保留可恢复的 QUEUED 状态。
- 受影响集成测试 **42 passed**（JUnit：`test-results/P4-LIBRARY-RETENTION-C2-FINAL-FIX-focused-r6.xml`）；Ruff、py_compile、Node `--check`、`git diff --check` 通过。
- Luna H 独立复核：无 blocker。自动启用与启动恢复时 `start=False` 的 warning 分支未单独断言，已检查实现逻辑；没有发现状态丢失路径。
- 隔离合成浏览器完成取消确认（取消后 0 run）、确认后跨批次 PARTIAL（65 checked / 64 completed / 1 failed）、刷新及离页返回状态保留、失败后再次点击创建新 run 并 COMPLETE（1 completed）。两个 run ID：`23133cc64ff8425faf263ca29879b133`、`6d77855ce25e4b859c19b3122ecea82c`。只使用项目内隔离合成库；未访问真实 PhotoSearchData。浏览器截图未能从当前工具导出。
- 父 C2 仍为 **IMPLEMENTED / ACCEPTANCE PARTIAL**：C2-E 媒体 unlink 与数据库记账之间的崩溃窗口仍可能使累计字节保守低估；本卡不处理该问题。
- 未提交、未推送。详见[执行报告](../reports/P4-LIBRARY-RETENTION-C2-FINAL-FIX.md)。
