# P1-01 — C2 Final Short Re-review / PASS

任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束  
复核角色：**C2 / GPT-5.6 Sol / high**  
复核基线：`f0b4f402e020e327748d9feef292447cd6301757`  
前次发现：`docs/reports/P1-01-c2-final-rereview.md`  
DS-H 最终返修：`docs/reports/P1-01-c2-final-remediation.md`  
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`

## 1. 最终结论

**C2 FINAL SHORT RE-REVIEW: PASS。P1-01 正式完成，P1-02 可以解除阻塞并开始执行。**

本轮短复核确认：前次 final re-review 的两个 blocker（F1 / F2）均已关闭，且没有发现需要修改 ADR-0002、数据身份、唯一约束、迁移/WAL、job 五字段逻辑键、缺失判定原子边界或原图只读规则的新问题。

C2 本轮不修改 `src/**`、`tests/**` 或 ADR-0002；本报告只记录最终门禁结论与交接状态。

## 2. F1 — exhaustion 持久化与 job / scan_run 终态联动：CLOSED

最终实现满足以下要求：

- `execute_scan()` 在领取事务内部捕获 `RETRY_EXHAUSTED`，让 job 的 `FAILED / attempts / lease clear` 与对应 `scan_run FAILED` 正常提交，事务提交后才向调用层重新抛出结构化错误，不再出现“先写 FAILED、后因外层异常回滚”的问题。
- `_requeue()` 检查 `jobs.finish(... RETRY_WAIT)` 的真实返回状态；达到 `max_attempts` 时，在同一业务事务内把对应 `scan_run` 同步为 `FAILED`。
- 通用 `claim_next()` 路径通过 `jobs.fail_exhausted_leases()` 同步耗尽 job 与其 `scan_run`，避免 `job=FAILED / scan_run=RUNNING` 分叉。
- 失败 scan identity 不再被重新领取；后续新扫描创建新的 `scan_run + job`，保持 ADR-0002 的终态身份语义。

生产路径回归测试覆盖：

- `test_expired_lease_exhaustion_fails_job_and_scan_on_the_production_path`
- `test_unstable_input_ceiling_fails_job_and_scan_together`
- `test_scheduler_fails_an_exhausted_scan_job_and_syncs_the_scan_run`

## 3. F2 — 同一 scan_id 的 execution coverage 污染：CLOSED

最终实现采用 P1-01 范围内的最小修复：每次新的 execution 成功 claim 后，清除该逻辑 `scan_id` 上一 execution 遗留的 `last_seen_scan_id` coverage marker；位置行、`size / mtime_ns`、`availability` 与已有 `asset_id` 等正向事实不删除、不重写身份。

因此：

- 恢复 execution 重新完整枚举时，`last_seen_scan_id = scan_id` 只代表**当前 execution**真正看到的位置；
- 上一 execution 已看到、但恢复前被删除或改名的文件，会在当前完整成功扫描的最终对账中正确进入 `MISSING`；
- 上一 execution 只留下 `asset_id IS NULL` 的未绑定位置，不再污染当前 execution 的成功 guard；若当前完整枚举没有看到它，最终可以正确转为 `MISSING`；
- 没有新增 schema 或改变 `(source_id, path_key)`、asset identity、scan identity。

生产路径回归测试覆盖：

- `test_resume_marks_positions_deleted_before_recovery_as_missing`
- `test_resume_ignores_unbound_leftovers_from_a_killed_execution`

## 4. 验证证据

DS-H 最终返修报告记录：

| Check | Result |
|---|---|
| `uv run pytest -o addopts="" -q tests/unit tests/integration` | exit 0, `122 passed, 1 skipped in 75.38s` |
| `uv run ruff check .` | exit 0, clean |
| `git diff --check` | exit 0, clean |
| F1 production-path regressions | 3 cases added and passing |
| F2 production-path regressions | 2 cases added and passing |

C2 本次最终短复核是对提交 `f0b4f402...` 的代码、测试与契约进行静态聚焦复核；**C2 没有在用户 Windows 开发机重新执行上述 122 个测试**。运行结果来源于已提交的 DS-H 验证记录。GitHub 当前也没有提供独立 CI status，因此该 provenance 保持显式记录。

## 5. 非阻断后续事项

以下两点不阻止 P1-01 PASS，但在 P1-02 / 后续规模验证中继续关注：

1. `reset_execution_coverage()` 当前会按来源/scan marker 做一次范围 UPDATE，并位于 execution claim 的写事务中。现有索引支持该查询，但在几十万位置规模下应测量事务时长与写锁占用；若成为性能问题，P1-02 可评估更细的 execution generation 方案，但不得改变冻结身份和 missing 语义。
2. 底层 `claim_job()` 的 exhaustion 分支仍依赖调用方在事务内正确处理“写终态后抛结构化错误”的边界。当前正式扫描入口和 `claim_next()` 路径均已安全覆盖；P1-02 若新增直接调用 `claim_job()` 的执行入口，应继续沿用该安全事务边界，或进一步收紧 API 以降低误用风险。

这些是工程优化/接口健壮性事项，不是 P1-01 正确性 blocker。

## 6. 门禁与交接

- P0：**PASS**。
- P1-01：**PASS**。
- ADR-0002：继续冻结；本轮无修订。
- P1-02：**READY / CURRENT TASK**。
- P1-02 推荐模型：**DS-H / DeepSeek V4.1 Flash / 高推理**。
- P1-02 任务卡：`docs/tasks/P1-02.md`。

P1-02 应在 P1-01 已冻结的数据身份、lease、恢复、missing 原子边界之上，把最小确定性扫描升级为流式、有界内存、稳定等待、hash 前后校验和生产级扫描闭环。