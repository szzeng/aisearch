# P4-05 — DS-H 失败边界复审（Sol F1 修复轮之后，2026-09-28）

**性质**：只读短复审 + 失败边界专项。本轮未改产品/测试；新增产物只有本报告 + 卡/状态行（复核脚本留在 `tmp/p405-review/`，不入库）。
**范围**（按 V3.1「修复轮只复核原失败边界、新增 diff 与直接回归」）：修复轮 [P4-05-sol-f1-fix-round.md](P4-05-sol-f1-fix-round.md)
引入的 `stopped` 确认边界、manifest `consistency` 字段、CLI `--stopped`，以及**全部失败/拒绝边界的重新实测**。
**基线**：`HEAD = f00955e`，工作树未提交；审查时点产品文件 SHA-256（前 16）：`backup.py = 3d92bb773ad28db5`、`__main__.py = 362598457f77fb84`。

## 1. 结论

- **F1（Sol 裁定项）已按「显式确认 + 可审计」修复并验证**：没有 `stopped=True` / `--stopped` 时 `create_backup` 结构化拒绝
  （`BACKUP_STOPPED_CONFIRMATION_REQUIRED`），**不创建 staging、不创建目标**；成功备份的 manifest 记录
  `"consistency": {"mode": "stopped", "operator_confirmed": true}`。原先“静默运行中备份”的路径已消失。
- **F2 已落测**：集成测试现在逐行比较 `places` / `asset_place_assignments` / `catalog_filter_revision_events`（人工知识 + 操作历史）。
- **R1（新发现，阻塞，需最小修复）**：**损坏/非 SQLite 的 `catalog.db` 会逃逸为原始 `sqlite3.DatabaseError`**，不是结构化拒绝；
  对应 CLI 会打印 traceback、不输出 JSON（详见 §3）。
- **裁决：DS-H 失败边界复审 = `REVIEW FAIL`（仅因 R1）**。R1 是一处 2 行的最小修复；修完只需有界复跑
  （§3 的 B15/B18/B19 + 定向 9/0），随后 Sol 只复审 F1 修复边界与 R1 是否影响其裁决输入。
- 产物清理不变量在全部 19 个边界上都成立（没有留下可误用的 bundle/target/staging），源根在拒绝路径上零写入。

## 2. 独立复跑（本主审自己跑）

| 项 | 本主审结果 | 修复轮报告 |
|---|---|---|
| 定向（`tests/unit|integration/test_p4_05_backup_restore.py`） | **9 / 0 / 0**（`test-results/p4-05-rereview-focus.xml`，7.09 s） | 9/0/0（7.46 s） |
| B 级全量 | **1796 / 0 / 0 / 48，248.71 s**（`p4-05-rereview-full.xml`） | 1796/0/0/48，244.23 s |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿（exit 0） | 一致 |
| 主审 Arm A（生产扫描 + 真实 Lance + 人工地点 → 备份 → 空目录恢复） | **全表 0 差异**、派生计数不变（未重扫/未重编码）、暖机后**页面逐字段相同**、schema 16 | 修复轮未做该项（沿用上一轮主审探针，本轮在修复后重跑） |

## 3. 失败边界矩阵（主审自建探针：`tmp/p405-review/failure_boundary_probe.py`，19 个边界）

| # | 边界 | 期望 | 实测 | 判定 |
|---|---|---|---|---|
| B1 | `create_backup(stopped=False)` | `BACKUP_STOPPED_CONFIRMATION_REQUIRED`；无 bundle、无 staging | 一致 | ✅ |
| B2 | `create_backup(stopped=True)` | ok + manifest 记录停机确认 | `status=ok`、`consistency={"mode":"stopped","operator_confirmed":true}` | ✅ |
| B3 | 目标目录已存在 | `BACKUP_TARGET_EXISTS` | 一致 | ✅ |
| B4 | 恢复包成员字节被篡改 | `BACKUP_INVALID`；目标不存在、无 staging | 一致 | ✅ |
| B5 | manifest 成员路径含 `..` | `BACKUP_INVALID` | 一致 | ✅ |
| B6 | manifest 成员路径为绝对路径 | `BACKUP_INVALID` | 一致 | ✅ |
| B7 | manifest 成员路径重复 | `BACKUP_INVALID` | 一致 | ✅ |
| B8 | 恢复到非空目录 | `RESTORE_TARGET_NOT_EMPTY`；目标内容不变 | 一致（摘要不变） | ✅ |
| B9 | 恢复到**已存在的空目录** | ok | `status=ok` | ✅ |
| B10 | 备份中途复制失败（注入） | 无 bundle、无 staging | 一致 | ✅ |
| B11 | 恢复中途复制失败（注入） | 无 target、无 staging | 一致 | ✅ |
| B12 | manifest schema=17 | `BACKUP_SCHEMA_MISMATCH`；无 target | 一致 | ✅ |
| B13 | 拒绝路径是否写源根 | 源根摘要不变 | 不变 | ✅ |
| B14 | 根下没有 catalog | `BACKUP_INVALID` | 一致 | ✅ |
| **B15** | **catalog 文件不是 SQLite（create）** | `BACKUP_INVALID` | **`RAW:sqlite3.DatabaseError`** | ❌ **R1** |
| **B18** | **bundle 内 catalog 损坏但哈希自洽（restore）** | `BACKUP_INVALID`（且不留 target/staging） | **`RAW:sqlite3.DatabaseError`**（target/staging 已正确清理） | ❌ **R1** |
| **B19** | **CLI 对损坏 catalog 执行 create** | exit 1 + 结构化 JSON | **exit 1，但无 JSON、stderr 打 traceback** | ❌ **R1** |
| B16 | 成员内符号链接 | `BACKUP_INVALID`（若可建） | **本会话无 Windows 符号链接权限 ⇒ 未测（记为限制，不是通过）** | ⚠️ 限制 |
| B17 | CLI `create` 不带 `--stopped` | exit 1 + `BACKUP_STOPPED_CONFIRMATION_REQUIRED` | 一致 | ✅ |

**R1 根因与最小修复**：`backup.py::_schema` 只捕 `CatalogError`，而 `inspect_database` 对非 SQLite 文件抛的是
`sqlite3.DatabaseError`（`catalog/migrate.py:62 → catalog/schema.py:147`）；`__main__.py` 也只捕 `CatalogError`。
- 修复：`_schema` 增加 `except sqlite3.Error`（或 `except (CatalogError, sqlite3.Error)` 分支）→ 包成
  `BACKUP_INVALID`（`cause=type(error).__name__`）；必要时 `__main__` 对非 `CatalogError` 也输出结构化错误而不是 traceback。
- 复验：B15/B18/B19 + 定向 9/0（其余 16 个边界不应变化）。

## 4. 其它确认与观察

- **R2（已确认）**：F1 修好了“**静默**”那一半——现在运行中备份必须由操作员显式确认；`stopped=True` 时包内仍可能不是时间点快照，
  这是**已文档化的残余**（修复报告写明“这不是在线一致性承诺，也不假称能从 WAL/文件锁推断所有运行者”）。
  本主审用 Arm B（显式确认 + 备份中途真实提交）复现该残余：bundle 校验通过、恢复成功、恢复根不含那次提交——**与文档一致，不再是静默失败**。
- **R3（已确认）**：F2 的人工知识行级断言已进集成测试；主审全表摘要复核仍 0 差异。
- **R4（低，修复轮纪律）**：修复轮在 `__main__.py` 夹带了 3 处与本修复无关的纯换行重排
  （`_encoder_wiring`、`_default_manifest`、`_demo_search`）。逐行核对**语义未变**，但修复轮 diff 应最小化——建议还原或单独提交，
  便于 Sol 只读修复边界。
- **R5（限制）**：B16（成员符号链接）在本会话无法实测；`_members` 仍只查 `is_symlink()`，Windows **junction** 未被覆盖
  （上一轮 F4，仍开放、非阻断）。

## 5. 交接给 Sol 的边界

Sol 聚焦终审的输入不变：F1 的“显式确认 + manifest 记录 + 运行中不受支持”已可审计；**R1 修复后**请只读
`backup.py::_schema` 的异常边界、`__main__.py` 的错误出口，以及本报告 §3 的 B15/B18/B19 三行，外加修复轮的定向 JUnit。
本主审不建议把范围扩大到 remap/升级回退/打包/真实图库。

## 6. 复现命令

```powershell
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_05_backup_restore.py `
  tests/integration/test_p4_05_backup_restore.py --junitxml=test-results/p4-05-rereview-focus.xml   # 9/0/0
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --junitxml=test-results/p4-05-rereview-full.xml                                                  # 1796/0/0/48
python tmp/p405-review/failure_boundary_probe.py        # 19 边界矩阵，产物 tmp/p405-review/failure-boundary-report.json
python tmp/p405-review/review_probe.py                  # Arm A/B（正确性 + 显式确认下的残余）
uv run --no-sync ruff check . ; uv lock --check ; git diff --check                                 # exit 0
```
