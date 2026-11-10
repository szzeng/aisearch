# P4-LOCK-CONTENTION-RECOVERY-A — SQLite 与模型资源锁冲突诊断及修复

日期：2026-11-10。状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / INDEPENDENT LUNA H REVIEW FINDINGS / NOT CLOSED**。未提交 Git。

## 结论与根因

### SQLite 写锁

P4-02U 归因报告已通过线程/进程探针确认，历史搜索争用的写锁持有者是后台 `IndexCoordinator.process_batch` 的 `claim`/`apply` 短事务序列：线程臂连续不可用最长 32.45 秒，占用率 98.96%。P4-02V 已修复搜索侧投影追赶的 100 ms 有界等待与 `LAGGING` 降级；本轮确认该机制只覆盖查询追赶，不覆盖扫描等直接写调用。

扫描链为 `ScanWorker.enumerate/_process_file` → `Catalog.write()` → `BEGIN IMMEDIATE`。目录遍历、文件稳定性等待、SHA-256 均在事务外，写事务持有时间有界；每个 Catalog 实例的 RLock 不能协调其他 Catalog 连接，跨连接/进程互斥依赖 SQLite。原始本机事故日志、时间线和连接身份未随任务提供，因此**不能断言某一次扫描故障的实际持有进程/线程**。隔离复现证明短暂外部写者在首个 busy timeout 后释放时可安全重试；持续占锁则在有界重试后明确失败，业务体未执行、没有部分提交。历史 P4-02U/V 对 drain 写者的归因仍成立，但不替代缺失的原始现场证据。

### 模型资源锁

人脸 `run_queue()` 持有 `BACKGROUND_MODEL_SLOT`；候选任务再取得 `ModelResourceGate.exclusive()`，协调前台语义查询。原代码分别只等 5 秒/15 秒，但均折叠成同一个 `RESOURCE_BUSY`，等待期间不能及时响应暂停/停止。两种锁都由候选独占保留，未取消；本轮没有原始现场状态文件可进一步追认某次 `RESOURCE_BUSY` 的来源。

## 改动

- [db.py](../../src/photo_search/catalog/db.py)：`Catalog.write()` 仅在 `BEGIN IMMEDIATE` 尚未成功、且 SQLite 错误码为 `SQLITE_BUSY` 时最多重试两次，间隔 50/100 ms；现有每次 5 秒 busy timeout 保持不变。SQL 业务体只执行一次。非 BUSY BEGIN 失败不重试；COMMIT 失败仍按原路径回滚并报错。失败详情增加阶段、总等待、尝试数、等待方 PID/TID/线程名和 SQLite 错误码/名称；SQLite 本身不能从等待连接查询外部持有者身份。
- [model_resource_gate.py](../../src/photo_search/runtime/model_resource_gate.py)：独占等待支持取消检查和 100 ms 轮询；区分前台查询占用、其他独占预留、暂停和停止，并在退出/取消/超时后可靠清除 writer-waiting 状态。
- [candidate_task.py](../../src/photo_search/models/embedding/candidate_task.py)：人脸槽位等待上限调为 60 秒，以避免 5 秒短等待造成不必要暂停；语义查询等待保持 15 秒。两者均响应暂停/停止，超时状态仍可恢复且原因分别保存为 `FACE_TASK_ACTIVE`、`SEMANTIC_QUERY_ACTIVE` 或 `SEMANTIC_RESOURCE_RESERVED`。encoder 在语义 gate 仍独占时关闭，之后才开放查询；人脸槽位由外层 `finally` 释放，包括模型加载/关闭异常路径。候选批次上限 100、generation 身份、profile 激活策略和索引确认逻辑未变。
- 新增两个定向测试文件；扩展候选管理暂停/恢复测试，维护 drain 的老拒绝重试测试现在单独隔离 drain 重试层。为使 `ruff check .` 通过，另对两个既有 UI 测试文件做了纯格式清理，没有行为改动。

## 验证

| 场景 | 结果 | 证据 |
|---|---:|---|
| 独立 Catalog 连接并发写入；外部写锁短暂释放后重试；长期占锁耗尽重试；事务体失败和 COMMIT 失败回滚 | 4/4 通过 | `test-results/p4-lock-contention-recovery-a/final-affected-20261110-192131-575.xml` |
| 人脸槽位/语义查询持锁、释放、暂停、停止、超时、模型加载失败释放；候选暂停后同 task/generation 恢复，确认数保持 1，关闭 encoder 时 gate 仍独占 | 通过 | 同上；合计受影响集 138 项，0 failures、0 errors、23 skipped，61.5 秒 |
| P4-02V 查询降级、P4-02A drain 重试/原子性、扫描 API 并发与来源登记、人脸服务/人物关联、候选 provider 流程、semantic build | 通过 | 同上；完整命令见下 |
| 完整 unit/integration | **3,664 项：3,586 passed、52 skipped、26 failed** | `test-results/p4-lock-contention-recovery-a/full-20261110-185551-064.xml`；其中 2 项直接受本改动影响的旧测试替身/断言已修正，并在上述最终受影响集通过。其余 24 项均在本次改动面之外，主要是前端静态/UI 与冻结 companion hash 断言，其中 1 项属于时间边界不稳定；完整套件未在这些测试修订后重跑，故不报告全量 PASS。 |
| `ruff check .`；`git diff --check` | 通过 | 均 exit 0 |

定向命令（Windows PowerShell，JUnit 已保留）：

```powershell
python -m pytest -q tests/unit/test_catalog_write_contention_recovery.py tests/unit/test_p4_candidate_resource_waits.py tests/unit/test_p4_02v_catch_up_contention.py tests/unit/test_embedding_management_coordination.py tests/unit/test_p4_embedding_management_fix.py tests/integration/test_p4_02a_maintenance_entry.py tests/integration/test_api_sources_assets.py tests/integration/test_source_registration_concurrency.py tests/integration/test_p3_face_lifecycle.py tests/integration/test_p3_face_cli.py tests/unit/test_p3_face_association.py tests/integration/test_p4_embedding_provider_flow.py tests/unit/test_p4_02o_semantic_build.py --junitxml=test-results/p4-lock-contention-recovery-a/final-affected-20261110-192131-575.xml --basetemp=.pytest-tmp/p4-lock-final-20261110-192131-575
python -m ruff check .
git diff --check
```

完整 unit/integration 的 26 项失败摘要及原始输出留在 JUnit；最终定向集通过。人脸真实权重未安装，22 个依赖 `buffalo_l` 实际推理/关联的测试因此跳过；另 1 个需要 Windows symlink 特权的测试跳过。没有下载模型或更改权限。没有运行真实 512D GPU 搜索；其身份、定义和查询协议未改。

## 未决项与运行建议

1. 缺少原始现场日志，无法将用户遇到的某一次 SQLite 错误或 `RESOURCE_BUSY` 与具体进程/线程/锁归属一一对应。新 SQLite 错误详情记录等待方和阶段，但无法反查外部 SQLite 写者。
2. 完整 unit/integration 尚未形成最终全绿结果；其余 24 个失败位于任务外 UI/冻结契约测试面，未在本轮修复；必须在后续独立审核中确认是否可作为基线问题接受。
3. 按用户要求，已由独立 Luna High 子代理完成全受影响面复核。结论 **REVIEW FINDINGS**，尚不足以关闭任务：
   - **P2 — 缺少扫描与 drain 同时运行的直接覆盖。** `test_api_sources_assets.py` 验证扫描期间的 API 来源写入；维护 drain 测试单独验证了 drain 争用，最终 JUnit 没有证明扫描与 drain 的重叠场景不会使扫描失败。
   - **P2 — 候选暂停/恢复测试未验证已持久化向量幂等。** 候选测试 stub 在两次调用都返回 `CONFIRMED: 1`，只核对同 task/generation 与状态；semantic build 的相关幂等测试不能替代暂停恢复跨路径的持久化向量断言。
   - 审查同时确认 BEGIN 重试只发生于事务开始前且有界、COMMIT/回滚路径保留、encoder 在 gate 释放前关闭、人脸槽位在外层 finally 释放，P4-02V 未被改动。
   用户本轮要求仅进行复核，因此本轮不追加实现或测试；两个 P2 证据缺口保持开放，任务状态不是 PASS。审查未报告必须升级 max 或 Sol 的问题。
4. 源码应用需重启后加载新实现。若当前有候选任务，应先允许它到批次安全边界或正常停止；重启恢复路径会把旧 owner 的 RUNNING 状态标记为 INTERRUPTED。随后可从候选任务页继续同一任务，已确认向量与 generation 不变。无活动后台任务时无需额外恢复操作。

本轮只使用隔离合成数据，未读取或修改真实图库、用户照片或模型；未下载模型；未提交 Git。
