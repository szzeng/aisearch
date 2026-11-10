# P4-LOCK-CONTENTION-RECOVERY-B 续修结果（2026-11-10）

## 结论

确认并修复 B 卡三项缺口：刷新扫描阶段曾被全周期 `BACKGROUND_MODEL_SLOT` 包围；短期 BEGIN 争用 exhausted 后缺少 runner 对同一持久化 scan job 的有界续跑；来源删除仅检查 scan_run 状态，未辨别 `RETRY_WAIT` 或已过期 lease。未修改 schema、候选独占协议、512D/1024D 模型身份、搜索协议、向量确认规则或 P4-02V drain 让步实现。

## 改动

- `api/refresher.py` 移除 `_run()` 全周期人脸槽位；`refresh/pipeline.py` 将槽位限制到语义模型加载、编码和 profile 确认，最长等待 60 秒并可取消。等待超时明确报告语义阶段延后并保留持久化 backlog。`face.service.run_queue()` 与候选任务原双锁和 `finally` 生命周期不变。
- `ingestion/scanner.py` 对枚举/内容处理和最终成功事务，仅在 `BEGIN IMMEDIATE` 尚未启动且 `SQLITE_BUSY` 时使用已有 `_requeue()`。不会重试事务体、SQL 或 COMMIT 错误，也不把数据库损坏当作争用。`api/runner.py` 检查 job 已到 `RETRY_WAIT/QUEUED` 且没有活动 owner 后，最多续跑 3 次，总等待上限 30 秒；长锁若连 retry transition 都无法提交，则保留有效租约，后续按现有 lease expiry 接管。
- `catalog/store.py` 在来源删除事务内核验关联 scan job：Queued/有效 lease 拒绝；Retry wait、终态和过期 lease 放行；job 缺失时保守拒绝。`ingestion/service.py` 传入事务核验时刻。只删除来源管理记录，不删除原图。
- 新增测试覆盖刷新扫描期间模型槽位可用、语义编码期间槽位被持有、encoder 加载异常释放、等待时取消且不泄漏槽位，过期 lease 下来源删除与活跃扫描拒绝，BEGIN busy 分类，以及持续外部写锁超过有限重试预算后的恢复与进度保留。

## 验证

| 验证 | 结果 |
|---|---:|
| 上一任务 140 项受影响集合 + B 主受影响集合及 RETRY_WAIT 删除增量 | **合并 217 项：194 passed / 23 skipped / 0 failed / 0 errors**（主 JUnit 216 项，另 1 项增量 JUnit） |
| SQLite 长锁恢复 | 通过；临时库连接 busy_timeout=50 ms，外部 writer 持锁 750 ms；同一 scan_run 最终成功，已有资产保留，新增 3 个位置正确绑定，pending=0，job attempts=1 |
| 刷新锁范围 | 通过；扫描停在闸门时可取 `BACKGROUND_MODEL_SLOT`；进入语义 `build_index` 后槽位不可取得 |
| 活跃、过期 lease 与 RETRY_WAIT 扫描删除 | 通过；活跃 queued 扫描仍返回 `SCAN_IN_PROGRESS`；过期 lease 和无 lease 的 RETRY_WAIT 可删除登记，合成原图仍存在 |
| P4-02V、A 的 SQLite/候选/512D 相关回归 | 通过，包含在主受影响集合中 |
| `uv run ruff check .`、compileall、`git diff --check` | 通过 |

JUnit：主受影响集 `test-results/p4-lock-contention-recovery-b/final-affected.xml`；增量用例 `test-results/p4-lock-contention-recovery-b/retry-wait-delete.xml`。完整 suite 历史的 24 项范围外失败没有在本续修中重跑；真实图库、GPU、人脸权重和冻结发行包验证未执行。Luna H 增量独立只读复核 **REVIEW PASS**：此前指出的 encoder load 异常/取消等待槽位释放测试缺口已关闭，未发现新增 blocker。应用源码需重启；冻结 Windows 发行版尚未重建，后续发行验证需用隔离合成目录覆盖刷新、512D 搜索和候选恢复。不提交、不推送。

---

# P4-LOCK-CONTENTION-RECOVERY-A — SQLite 与模型资源锁冲突诊断及修复

日期：2026-11-10。状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / INDEPENDENT LUNA H REVIEW PASS**。本轮补测及文档按用户授权提交并推送。

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
| 扫描与 drain 实际重叠 | 通过 | `test_scan_completes_while_maintenance_drain_holds_an_engine_write` 使用独立 Catalog 连接；drain 在 apply/Lance 写入事务内等待扫描进入 `Catalog.write()`，扫描尚未获得 `BEGIN IMMEDIATE` 时才释放 drain；随后验证扫描进度、outbox 确认及向量唯一性 |
| 候选暂停/恢复的持久化向量幂等 | 通过 | `test_candidate_pause_resume_preserves_confirmed_vectors` 以 101 张合成图跨越 100 项批次边界，真实运行候选管理器与 `semantic.build_index`；暂停时 100 条 SQLite 确认和 100 个 Lance 行，恢复同一任务后 101 条确认和 101 个唯一资产向量 |
| 补测后受影响集合 | **140 项：0 failed / 0 errors / 23 skipped** | `test-results/P4-LOCK-CONTENTION-RECOVERY-A-final-affected-rerun.xml` |
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
3. 初次独立 Luna High 复核指出扫描/drain 重叠和候选暂停/恢复持久化幂等两项 P2 测试证据缺口。已针对两项补测试并重跑受影响集合。Luna High 跟进只读复核结果 **REVIEW PASS**：
   - 扫描线程在独立连接启动后停在闸门；真实 drain 在 apply 事务内等待扫描进入 `Catalog.write()`。测试确认扫描未获写锁时 apply 仍被保持，随后放行并验证扫描完成、6 个事件确认和向量资产唯一。
   - 候选任务真实执行 `semantic.build_index`，101 张合成图在 100 项安全批次后暂停；持久化检查暂停时 100 条确认记录及 100 个 Lance 行，恢复同 task 后完成 101 条确认记录及 101 个唯一向量行。
   - Luna High 也确认新测试直接覆盖两个缺口，没有指出待修复项。此前对 BEGIN 有界重试、COMMIT/回滚、encoder/gate 释放与 P4-02V 不变的判断继续成立。
   新的受影响 JUnit 为 140 项，0 failed、0 errors、23 skipped；Ruff 与 diff-check 通过。完整 unit/integration 未因本轮纯测试补充而重跑；全套首轮的范围外 24 项失败仍需如实保留。此次追加修复与报告已按用户指示提交并推送。
4. 源码应用需重启后加载新实现。若当前有候选任务，应先允许它到批次安全边界或正常停止；重启恢复路径会把旧 owner 的 RUNNING 状态标记为 INTERRUPTED。随后可从候选任务页继续同一任务，已确认向量与 generation 不变。无活动后台任务时无需额外恢复操作。

本轮只使用隔离合成数据，未读取或修改真实图库、用户照片或模型；未下载模型。按用户授权提交并推送了测试和任务记录更新。
