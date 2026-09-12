# P1-01 — C2 Final Focused Re-review

任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束  
复核角色：**C2 / GPT-5.6 Sol / high**  
复核基线：`ce6f56c57fbf96203a0d81d1eb975fa27f469ac5`  
前次 C2 复核：`docs/reports/P1-01-c2-focused-review.md`  
DS-H 返修证据：`docs/reports/P1-01-c2-remediation.md`  
冻结契约：`docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`

## 1. 结论

**C2 FINAL RE-REVIEW: FAIL / FIX REQUIRED。P1-01 仍不得标记 PASS，P1-02 继续 BLOCKED。**

DS-H 在 `ce6f56c5` 中正确修复了上一轮的大部分问题：B1 写前提交资格、B3 `migrate=False` 旁路、R1 泛化 scan-run 自循环、R2 跨来源 `scan_id` 校验均已关闭；B4 的 stale hash 丢弃和 hash 后重新 stat 也已明显收紧。现有 D 级回归记录为 `117 passed, 1 skipped`，但 final re-review 仍发现两个会破坏恢复正确性的阻断问题。

本次 C2 不修改代码、不修改测试、不修改 ADR-0002。两个新发现都属于现有冻结契约下的实现问题，应由 DS-H 做小范围返修。

## 2. 已确认关闭的上一轮问题

| 项目 | Final re-review 结论 |
|---|---|
| B1 增量持久结果缺少 lease/revision/processor 写前资格校验 | **CLOSED**。`observe_location` 与 asset upsert + bind 均在各自短事务内重新校验提交资格；过期执行者不会在无资格状态下留下新的业务结果。 |
| B3 `migrate=False` 绕过 schema 只读预检 | **CLOSED**。业务 `Catalog.open()` / `CatalogService.open()` 已移除该旁路。 |
| R1 普通 `scan_runs RUNNING -> RUNNING` 自循环 | **CLOSED**。通用状态转移不再允许该自循环，lease takeover 只保持 scan_run 状态。 |
| R2 `last_seen_scan_id` 必须属于同一来源 | **CLOSED**。`observe_location()` / `reconcile_missing()` 已做同来源事务校验。 |
| B4 stale hash 可直接进入成功 | **PARTIALLY CLOSED**。hash 后会重新 stat，stale hash 会丢弃；持续不稳定会走 retry。但 retry exhaustion 与同 scan-id 恢复仍有下面两个阻断问题。 |
| B2 attempts/max_attempts | **PARTIALLY CLOSED**。普通 retry 计数和大部分单测已修正；真实业务事务中的 exhaustion 持久化仍有阻断问题。 |

## 3. Blocker F1 — exhaustion 的 FAILED 更新可能被外层事务回滚，且 job/scan_run 终态可能不同步

涉及：`src/photo_search/jobs/queue.py`、`src/photo_search/ingestion/scanner.py`。

### 3.1 过期 lease 接管到达 max_attempts 时，真实 `execute_scan()` 路径会回滚 FAILED 更新

`jobs._start_running(... takeover=True)` 在 `lost_attempts >= max_attempts` 时先执行：

- `jobs.state = FAILED`
- `attempts = lost_attempts`
- 清空 lease
- 写 `LEASE_EXPIRED`

随后立即抛出 `RETRY_EXHAUSTED`。

但生产路径 `execute_scan()` 是在 `with catalog.write()` 内直接调用 `jobs.claim_job()`。`Catalog.write()` 的规则是：事务块内异常向外传播时执行 `ROLLBACK`。因此 `_start_running()` 写入的 `FAILED` 会被同一个外层事务回滚，调用方最后只得到异常，数据库中的 job 仍可能保持原来的 `RUNNING + expired lease`。

现有 `test_expired_lease_takeover_respects_the_retry_ceiling` 没有真实复现这一调用边界：测试在 `with catalog.write()` **内部**捕获了 `RETRY_EXHAUSTED`，所以 context manager 看不到异常并正常 COMMIT。测试因此证明的是“内部 catch 时能提交”，不是“生产 `execute_scan()` 路径能提交”。

### 3.2 retry exhaustion 可能产生 `job=FAILED / scan_run=RUNNING`

`jobs.finish(... state=RETRY_WAIT)` 达到 `max_attempts` 时会把真实 target 自动改为 `FAILED`。

但 `_requeue()` 当前只调用 `jobs.finish(...)`，没有读取返回的真实 job state，也没有在同一事务中把对应 `scan_run` 同步到 `FAILED`。因此最后一次 `INPUT_UNSTABLE` 等 retryable failure 可能出现：

```text
jobs.state      = FAILED
scan_runs.state = RUNNING
```

这违反 ADR-0002 §3.3 冻结的状态联动规则：catalog_scan job 进入 `FAILED` 时，对应 scan_run 必须在同一业务提交路径进入 `FAILED`。

### 3.3 F1 返修要求

必须保证以下路径都在**不被异常回滚掉的事务边界内**原子落库：

- expired lease takeover 达到 retry ceiling → job `FAILED`，并按 catalog_scan 语义同步 scan_run `FAILED`；
- retryable failure 达到 retry ceiling → job `FAILED` + scan_run `FAILED`；
- 上述 FAILED 状态提交后，调用层可以再抛结构化错误，但不能依靠“先写再在同一个会回滚的事务里抛异常”。

实现方式由 DS-H 决定，但不得放松 ADR 的 attempts、lease、终态联动语义。

## 4. Blocker F2 — 同一 scan_id 的多次 execution 共用 `last_seen_scan_id`，会污染最终 missing 判定

涉及：`src/photo_search/catalog/store.py`、`src/photo_search/ingestion/scanner.py`。

ADR-0002 明确允许进程中断/lease 过期后继续复用同一个逻辑 `scan_run`，即恢复执行仍使用同一个 `scan_id`。当前 `observe_location()` 把看到的位置写成：

```text
last_seen_scan_id = 当前 scan_id
```

而最终 `reconcile_missing()` 只把：

```text
last_seen_scan_id != 当前 scan_id
```

的位置标为 `MISSING`。

这样无法区分“本 scan_run 的旧 execution 看到过”与“当前这次完整 recovery execution 真的看到”。

### 4.1 可复现错误场景

1. scan_id=1 开始第一次 execution；
2. 第一次 execution 扫描到 `a.jpg`，持久化 `last_seen_scan_id=1`；
3. 进程被杀 / execution 失联；
4. lease 过期；
5. 在恢复前，用户删除或改名 `a.jpg`；
6. 新 worker 接管同一个 scan_id=1，并重新完整枚举；当前 execution 没看到 `a.jpg`；
7. 最终 `reconcile_missing(source_id, scan_id=1)` 发现旧行的 `last_seen_scan_id` 仍然等于 1，因此不会把它标为 MISSING；
8. scan 仍可能进入 `SUCCEEDED`。

这违反 ADR-0002 §5.2/§5.3：missing 结论必须来自**这次完整成功覆盖**，不能被上一 execution 的部分正向观察污染。

### 4.2 未绑定位置的恢复死角

还有更严格的变体：旧 execution 已经 `observe_location`，但在 asset binding 前死亡，因此该位置满足：

```text
last_seen_scan_id = 当前 scan_id
asset_id = NULL
```

如果恢复前文件已经消失，新的完整 execution 根本不会再处理该位置。最终 success 事务的 `count_unbound_locations(source_id, scan_id)` 会看到这个旧 pending 行并抛 `INPUT_UNSTABLE`。

该异常发生在最终 `catalog.write()` 内部，而且当前逻辑没有把它转成 `_requeue()`，因此可能留下 job/scan_run 继续 `RUNNING`，形成无法靠普通恢复正确收敛的状态。

### 4.3 F2 返修要求

P1-01 必须能区分同一逻辑 scan_run 下的不同 execution coverage。具体数据结构由 DS-H 决定，但必须满足：

- 恢复执行重新完整枚举时，最终 missing 判定只依据**当前完整 execution**看到的位置；
- 上一 execution 的部分 `last_seen` 不能阻止本次把已经消失的位置标 MISSING；
- 上一 execution 留下的 unbound/pending 位置不能让当前完整重扫永久卡在最终 success guard；
- 不得为此修改资产身份、`(source_id,path_key)` 唯一性、五字段 job identity、missing 原子边界或原图只读规则；
- 如果需要新增 execution generation / observation generation 概念，必须保持它是机器恢复元数据，不改变稳定业务身份。

## 5. 必须新增的回归测试

DS-H 下一轮至少新增以下 3 个生产路径测试：

1. **真实 `execute_scan()` + expired lease + max_attempts**：不要在 `Catalog.write()` 内部人为 catch exhaustion；验证数据库最终持久状态是 job `FAILED`、scan_run `FAILED`、lease 清空，并且后续不能再次 claim 同一失败任务。
2. **连续 `INPUT_UNSTABLE` 直到 max_attempts**：最终 job 与 scan_run 必须在同一业务路径进入 `FAILED`，不得出现 job FAILED / scan RUNNING。
3. **同一 scan_id 中断恢复的 missing 正确性**：第一次 execution 已观察到某文件后中断；恢复前删除或改名该文件；lease 到期后用同一 scan_id 重扫并成功，旧位置必须按当前完整 execution 的事实正确进入 `MISSING`。建议同时覆盖“旧 execution 只留下 unbound location”的变体。

这些测试之外，应继续跑完整：

```text
uv run pytest -o addopts="" -q tests/unit tests/integration
uv run ruff check .
git diff --check
```

## 6. 当前门禁结论

- 上一轮 B1 / B3 / R1 / R2：**关闭**。
- B2 / B4：核心方向已修，但 final re-review 暴露 F1 / F2 两个恢复语义 blocker。
- ADR-0002：**本次不需要修改**。
- P1-01：**NOT PASS**。
- P1-02：**BLOCKED BY P1-01**。

下一步推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**，只处理 F1/F2 和对应测试。返修与 D 级复验后，再切回 **C2 / GPT-5.6 Sol / high** 做最终短复核。