# P4-05 — DS-H 完整受影响面复审（F1-R2「受控停机边界」修复，2026-09-28）

**性质**：只读**完整受影响面**复审（V3.1 §2.4）。本轮未改产品/测试；新增产物只有本报告 + 卡/状态行
（复核脚本留在 `tmp/p405-review/`，不入库）。
**为什么是完整受影响面而不是有界复审**：F1-R2 修复新增了 `runtime/data_root_lease.py`（跨进程 OS 锁）、
`app.py` 的应用生命周期钩子与 `backup.py` 的 SQLite checkpoint 语义——按 V3.1「修复扩展了影响面就扩大审查」
与 [修复报告](P4-05-sol-f1-r2-fix-round.md) 自身的门禁，本轮覆盖全部新面。
**基线**：`HEAD = f00955e` + 未提交工作树；产品文件 SHA-256（前 16）：`data_root_lease.py = 05e30d6f1482dacf`、`backup.py = 15a5fb81b2546625`、`app.py = c75da20621936123`（审查时点）。

## 1. 受影响面（逐文件）

| 文件 | 变更 | 判定 |
|---|---|---|
| `src/photo_search/runtime/data_root_lease.py`（新增） | `DataRootLease` / `acquire_data_root_lease`：`msvcrt.locking(LK_NBLCK, 1)`（POSIX 走 `flock`）非阻塞独占租约；`DataRootLeaseBusy`；陈旧文件不代表存活（OS 在进程退出时释放） | 正确（§3 L1–L8） |
| `src/photo_search/app.py`（+4） | lifespan 启动时获取租约、关闭时**先** `api.close()` 再释放租约 | 正确；F1R2-1 为信息面建议 |
| `src/photo_search/backup.py` | `create_backup` 在 `stopped=True` 之外必须取得同一租约（否则 `BACKUP_SERVICE_ACTIVE`）；取得后 `PRAGMA wal_checkpoint(TRUNCATE)`，`busy` 即拒绝；manifest `consistency` 增记 `managed_service_lease` 与 `snapshot`（generations / pending outbox / pending 投影）；成员集排除 `runtime/service.lease`、`catalog/catalog.db-wal`、`catalog/catalog.db-shm` | 正确（§3 L5–L7） |
| `tests/unit/test_p4_05_backup_restore.py`（+2 用例） | 租约被持有 → `BACKUP_SERVICE_ACTIVE`；真实 HTTP lifespan 运行 → 同码；manifest 形状断言 | 有效（定向 12→14） |
| 文档/卡/状态 | 修复记录 + 交接 | 与代码一致 |

**未改动**：schema/迁移（仍 16、0017 空闲）、`catalog/**`、`api/**` 信封与错误码、`indexing/**`、依赖/`uv.lock`、
ADR/companion、§12.2。

**直接调用链核对**：`__main__._serve → create_app() → lifespan → acquire_data_root_lease`；
`cli backup create → create_backup → 租约 + checkpoint + snapshot + 成员复制`；`restore_backup` **不取租约**
（目标必须为空，取租约反而会与"恢复到新根"冲突）。
另核对了其他会构造应用的调用点：`tests/conftest.py`、`tests/p2_search_api_fixtures.py`、若干集成测试
各自使用独立 `tmp_path` 数据根；`tests/integration/test_catalog_identity_lifecycle.py` 只调 `create_app().openapi()`
（不跑 lifespan）；`tools/diagnostics/packaged_api_smoke.py` 的两次 `Popen` 是**顺序**启动/重启 ⇒
没有"同一数据根并发两个应用"的既有用法被新租约破坏（全量测试亦通过，见 §2）。

## 2. 独立复跑（主审自己跑）

| 项 | 结果 |
|---|---|
| 定向（`tests/unit|integration/test_p4_05_backup_restore.py`） | **14 / 0 / 0**（`test-results/p4-05-f1r2-rereview-focus.xml`，10.14 s；修复轮 14/0/0，10.00 s） |
| B 级全量 | 修复轮 `p4-05-sol-f1-r2-full.xml` = **1801 / 0 / 0 / 48**（246.77 s）；**主审独立复跑** `test-results/p4-05-f1r2-affected-surface-full.xml` = **1801 / 0 / 0 / 48**（277.42 s） |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿（exit 0） |
| 上一轮 19 个失败边界矩阵（自建探针，B2 期望已按新 manifest 形状更新为"记录 mode/operator_confirmed/lease/snapshot"） | **19/19 符合预期，无回归**（B15/B18/B19 仍为结构化 `BACKUP_INVALID` / CLI 结构化 JSON） |
| Arm A（生产扫描 + 真实 Lance + 人工地点 → 备份 → 空目录恢复） | 全表 count+摘要 **0 差异**、`index_entries`/`artifacts`/`search_documents` 计数不变（未重扫/未重编码）、暖机后**页面逐字段相同**、schema 16 |
| 租约文件增长 | 同一根上 3 次获取（备份 → 应用启动 → 再备份）后仍为 **1 字节**（无增长） |

## 3. 租约与停止边界的 8 项独立边界（自建探针 `tmp/p405-review/lease_boundary_probe.py`）

| # | 边界 | 期望 | 实测 |
|---|---|---|---|
| L1 | 另一受控进程（同机同进程内新句柄）持有租约 | `BACKUP_SERVICE_ACTIVE`、不发布任何东西 | ✅ 一致（目标不存在、无 staging 残留） |
| L2 | **真实 HTTP lifespan 运行中**（`create_app(ApiSettings.create(data_root=…))` + `TestClient`） | `BACKUP_SERVICE_ACTIVE` | ✅ 一致 |
| L3 | **只剩陈旧租约文件**（文件在、无 OS 锁） | 备份**仍然允许**（崩溃不应 brick 备份） | ✅ `OK` 且 bundle 生成 |
| L4 | 备份之后 | 服务可再次启动、第二次备份成功 | ✅ `[HTTP 200, OK]` |
| L5 | bundle 成员与 manifest | 排除 `runtime/service.lease`、`catalog.db-wal`、`catalog.db-shm`；记录 `consistency.{mode,operator_confirmed,managed_service_lease,snapshot}` | ✅ 一致 |
| L6 | 备份后源根的 WAL | 已 checkpoint（0 字节/不存在） | ✅ `0` |
| L7 | 恢复 | `ok`、恢复根**无租约文件**、metadata 保留、catalog 可打开 | ✅ 一致 |
| L8 | **另一进程**持有租约（真实"应用在别的进程 / 备份 CLI"形状） | `BACKUP_SERVICE_ACTIVE` | ✅ 一致（Windows 字节范围锁跨进程生效） |

## 4. 发现

### F1R2-1（低，启动失败信息面）
备份持租约期间启动应用会在 lifespan 抛 `DataRootLeaseBusy`（`RuntimeError: <root>\runtime\service.lease`），
uvicorn 打印异常并退出。语义正确（单实例所有权），但操作员拿不到"正在备份，请稍后"这类可读原因。
建议：lifespan/launcher 捕获后给出明确消息与退出码。非阻断。

### F1R2-2（中，**交 Sol 裁决口径**）
租约目前只接在 **HTTP 应用** 上；**第一方维护 CLI**（`indexing drain/build/reexecute`、`catalog …`）以及任何
直接使用 `Catalog` 的写者都不取租约。因此"备份期间它们仍可写" ⇒ 仍可产生非时间点快照：
主审 Arm B（在 `catalog.db` 复制之后用真实产品路径提交一次人工地点）显示 **bundle 仍发布、manifest 校验通过、
恢复根不含该提交**。修复报告已把这写成明确边界（"未接入 lease 的外部写者不受承诺支持"），
故本轮**不判为阻塞**；但请 Sol 裁决：当前"操作员必须同时停掉 drain/build 等维护命令"的口径是否足够，
或应把维护 CLI 也接上同一租约（这样"停机"就成为产品可判定性质）。

### F1R2-3（低，代码卫生）
`backup._consistency_snapshot` 用 `with sqlite3.connect(...)`：上下文管理器只提交、**不关闭**连接（靠 CPython
引用计数回收句柄）。建议改 `contextlib.closing`。非阻断（Windows-first、CPython 下无实际泄漏）。

### F1R2-4（低，副作用记录）
`acquire_data_root_lease` 会在源数据根创建 `runtime/service.lease`（1 字节）并在需要时创建 `runtime/`。
即：**备份会写入源数据根**（不是照片目录；该文件已从 bundle 排除，且应用启动本来也会创建它）。
与卡片"原图只读、只写数据根"一致，但建议在 `backup --help`/报告中写明，避免"备份只读"的误解。

### F1R2-5（沿用，未变；非阻断）
B16（成员符号链接）本会话无 Windows 符号链接权限，**未能实测**；`_members` 仍只查 `is_symlink()`，
Windows junction 未覆盖（首轮 F4）。CLI 对非 `CatalogError` 的 `OSError` 仍会打印 traceback（首轮 F6）；
`restore_backup` 对整包两次全量 SHA-256（首轮 F5）。

## 5. 结论与 Sol 输入

- **完整受影响面复审 = `ENGINEERING REVIEW PASS`（无阻塞缺陷）**：租约语义、跨进程行为、陈旧文件处理、
  checkpoint 与成员排除、恢复等价性、失败边界与全量回归均经主审独立复现；唯一边界（F1R2-2）是**已文档化**的
  不支持范围，交 Sol 裁决口径。
- 状态：**`ENGINEERING REVIEW PASS（完整受影响面）/ SOL FOCUSED REVIEW PENDING`** ⇒ 按 V3.1 任务表 P4-05 的
  Sol high 一致性聚焦终审为必选，**不得标 PASS、不得生成下一卡**。
- Sol 只需读：本报告 + [F1-R2 修复记录](P4-05-sol-f1-r2-fix-round.md) + `runtime/data_root_lease.py`、
  `app.py` 的 lifespan 段、`backup.py::create_backup/_consistency_snapshot/_members` 的关键 diff +
  §3 的 L1–L8 与上一轮 B15/B18/B19；**不需要**重跑全量或扩大到 remap/升级回退/打包/真实图库。
- 未验证/禁止扩大：真实图库演练、打包产物中的租约行为、GTX 1660、40 万档、来源 remap、升级/回退、HTTP 恢复入口。

## 6. 复现命令与哈希

```powershell
python tmp/p405-review/lease_boundary_probe.py          # L1–L8（8/8 符合预期）
python tmp/p405-review/lease_boundary_probe.py cross    # L8 跨进程单独复跑
python tmp/p405-review/failure_boundary_probe.py        # 上一轮 19 个失败边界（19/19）
python tmp/p405-review/review_probe.py                  # Arm A（无回归）/ Arm B（未接入租约写者的残余）
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_05_backup_restore.py `
  tests/integration/test_p4_05_backup_restore.py --junitxml=test-results/p4-05-f1r2-rereview-focus.xml   # 14/0/0
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --junitxml=test-results/p4-05-f1r2-affected-surface-full.xml                                          # 1801/0/0/48
uv run --no-sync ruff check . ; uv lock --check ; git diff --check                                       # exit 0
```

产品文件 SHA-256（前 16，审查时点）：`data_root_lease.py = 05e30d6f1482dacf`、`backup.py = 15a5fb81b2546625`、`app.py = c75da20621936123`。
