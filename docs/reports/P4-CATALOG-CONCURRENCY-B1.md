# P4-CATALOG-CONCURRENCY-B1 执行报告

2026-11-27 · **SCOPED PASS / TERRA HIGH REVIEW PASS** · 实施基线 `ec6a8d1`。 [B1 任务卡](../tasks/P4-CATALOG-CONCURRENCY-B1.md)

12 个用户触发的 PersonService 写路由使用同一专用线程，包含人物改名、合并、拆分、撤销、可见性，单脸和批量纠错、建人、regroup、映射决策及手动清理操作快照。Worker 用 `ApiContext._service_factory` 自行打开 CatalogService，并在同一线程关闭。一个写操作执行时最多等候四个，满队列返回可重试的 `503 PERSON_WRITE_BUSY`。业务状态检查仍在既有操作中执行，涵盖映射错误信封、revision、幂等、撤销和派生队列原子性。

应用退出时先停止接收人物写入，异步等待最多 5 秒。超时后由既有 `ApiContext` 延迟清理接管，继续持有数据根租约。事件循环仍存活时，线程结束后由原线程完成清理并释放租约。事件循环已退出时，人物 Worker 作为非守护线程继续排空写入并关闭连接，数据根租约由操作系统在进程退出时释放。写入或连接关闭永久卡住时，服务 lifespan 可返回，进程会继续保有租约。

## 证据

| 场景 | 结果 |
| --- | --- |
| 独立合成库，第二条 SQLite 连接持有 `BEGIN IMMEDIATE` | 仅替换回 `ec6a8d1` 的同步路由时，健康响应晚于 6 秒保护释放，定向对照失败，退出码 1。新路由在持锁期间返回健康和人物详情，各定向等待上限 2 秒。释放锁后改名提交，相同 `request_id` 重放仅保留一个操作。 |
| 队列、连接归属与生命周期 | 1 个执行、4 个等候，溢出请求 503 且零写入。已接受的等待方取消后仍按顺序执行。连接打开、使用、关闭重试均在专用线程。宽限内和超时延迟清理均验证租约释放。独立子进程在主线程及其事件循环返回后完成写入，进程退出后重新取得租约。旧无期限等待实现的定向对照在宽限后超时，退出码 1。 |
| 人物 API 和业务回归 | 190 通过，含 4 个 B1 用例以及合并撤销、revision 拒绝、人工纠错、映射与 regroup。退出码 0。 |
| Worker、健康和读侧回归 | 35 通过，含 A0 锁序 7 场景、A1 的入口容量、读取快照和关闭三项回归。退出码 0。 |

独立 Terra High 首轮审核覆盖全部写路由和白名单、API 契约、事务、队列、取消及应用关闭，发现一项 P2。应用无期限等候人物 Worker 会阻止进入延迟清理，且只给等待加超时会让守护线程随进程退出而丢失已接受写入。修复后独立复审 **REVIEW PASS**，P2 已关闭，无新增 P0/P1/P2。首轮复跑 17 项、复审复跑 34 项，退出码均为 0。Catalog 锁序、原子事务和依赖沿用原实现。Ruff、compileall 和 `git diff --check` 均通过。

证据来自 Windows Python 3.12.10 的隔离合成图库及真实 ASGI 路由。实际后台建库持续争用、真实图库和发行性能未验证。B2 仍以实测持续写锁饥饿为启动条件。

## 复现

使用 `.venv` 和仓内隔离环境变量。原始 JUnit 及真实退出码保留于忽略目录 `test-results/P4-CATALOG-CONCURRENCY-B1/`。 [日常验证设置](../testing.md)

```powershell
$personTests = @(
  "tests/integration/test_p4_person_write_worker.py",
  "tests/integration/test_p3_person_api.py",
  "tests/integration/test_p3_person_merge.py",
  "tests/integration/test_p3_person_operations.py",
  "tests/integration/test_p3_person_undo.py",
  "tests/integration/test_p3_person_undo_ordering.py",
  "tests/integration/test_p3_person_split_api.py",
  "tests/integration/test_p3_person_split_lifecycle.py",
  "tests/integration/test_p3_person_split_undo.py",
  "tests/integration/test_p3_face_correction_api.py",
  "tests/integration/test_p3_face_correction_lifecycle.py",
  "tests/integration/test_p3_face_correction_undo.py",
  "tests/integration/test_p3_face_redetection_decisions.py",
  "tests/integration/test_p3_face_redetection_restart.py",
  "tests/integration/test_p4_person_regroup.py",
  "tests/integration/test_p4_person_ignore_state.py",
  "tests/integration/test_p4_person_name_case.py",
  "tests/integration/test_p4_person_inbox_create.py",
  "tests/integration/test_p4_person_oplog_gc.py",
  "tests/unit/test_p3_face_redetection_api_privacy.py",
  "tests/unit/test_p3_face_redetection_decisions.py"
)

$lifecycleTests = @(
  "tests/unit/test_health.py",
  "tests/unit/test_p4_build_stability_a2_lifecycle.py",
  "tests/unit/test_catalog_worker_lock_order.py",
  "tests/integration/test_p4_perf_catalog_worker_lifecycle.py",
  "tests/integration/test_p4_source_detail_stability.py::test_detail_admission_leaves_other_reads_capacity_and_reclaims_queue",
  "tests/integration/test_p4_source_detail_stability.py::test_detail_snapshot_keeps_counts_and_progress_together_while_scan_commits",
  "tests/integration/test_p4_source_detail_stability.py::test_close_rejects_queued_detail_and_drains_connection_before_lease_release",
  "tests/integration/test_api_sources_assets.py::test_source_summary_is_rootless_lightweight_and_session_protected"
)

$testRun = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$evidence = "test-results/P4-CATALOG-CONCURRENCY-B1"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
& .\.venv\Scripts\python.exe -m pytest -q @personTests --junitxml="$evidence/person-$testRun.xml" --basetemp=".pytest-tmp/p4-b1-person-$testRun"
& .\.venv\Scripts\python.exe -m pytest -q @lifecycleTests --junitxml="$evidence/lifecycle-$testRun.xml" --basetemp=".pytest-tmp/p4-b1-lifecycle-$testRun"
```
