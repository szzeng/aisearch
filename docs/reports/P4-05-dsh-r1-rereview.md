# P4-05 — DS-H R1 修复后的有界失败边界复审（2026-09-28）

**性质**：只读有界复审（按 [R1 复审](P4-05-dsh-failure-boundary-rereview.md) §3 指定的复验范围：B15/B18/B19 + 定向；另加无回归检查）。
本轮未改产品/测试；新增产物只有本报告 + 卡/状态行（复核脚本留在 `tmp/p405-review/`，不入库）。
**基线**：`HEAD = f00955e` + 未提交工作树；复审时点 SHA-256（前 16）：`backup.py = fab0ac344a40545d`、
`__main__.py = 1b9f0496394b136c`、`tests/unit/test_p4_05_backup_restore.py = 59f402e91cfb1ad5`。

## 1. 被复审的修复（最小面）

- `backup.py`：新增 `import sqlite3`；`_schema()` 在既有 `CatalogError` 分支之前增加
  `except sqlite3.Error: raise _fail("BACKUP_INVALID", "backup catalog failed verification", cause=type(error).__name__)`。
  其余语义（清单、staging、原子发布、schema 精确匹配、`stopped` 确认）**未改**。
- `__main__.py`：上一轮的无关格式化漂移（R4）**已还原**——当前该文件 diff 只含 `backup create|restore` 接线与 `backup import`。
- `tests/unit/test_p4_05_backup_restore.py`：新增 3 个边界用例（非 SQLite catalog 的 create 拒绝、同情形 restore 拒绝、
  CLI 结构化 JSON），定向总数 9 → **12**。

## 2. 本主审的独立复跑结果

| 项 | 结果 |
|---|---|
| **有界复跑 B15/B18/B19**（`tmp/p405-review/failure_boundary_probe.py B15 B18 B19`） | **3/3 符合预期，0 不符**：B15 `BACKUP_INVALID`、B18 `BACKUP_INVALID`、B19 `exit=1 + json + clean-stderr`；产物 `tmp/p405-review/boundary/failure-boundary-report.json`（cases 恰好 3 条） |
| 全 19 边界矩阵（同轮全跑一次） | **19/19 符合预期**（含上一轮已通过的 16 项与 B16 的“无 Windows 符号链接权限⇒不可测”限制项） |
| 定向测试 | **12 / 0 / 0**（`test-results/p4-05-bounded-rerun-focus.xml`，10.41 s；修复轮 12/0/0，9.77 s） |
| B 级全量 | **1799 / 0 / 0 / 48，255.63 s**（`test-results/p4-05-r2-full.xml`；修复轮 1799/0/0/48，250.82 s） |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿（exit 0） |
| 正确性无回归（主审 Arm A：生产扫描 + 真实 Lance + 人工地点 → 备份 → 空目录恢复） | **全表 count+摘要 0 差异**、`index_entries`/`artifacts`/`search_documents` 计数不变（未重扫/未重编码）、暖机后**页面逐字段相同**、schema 16 |

## 3. 结论

- **R1 关闭**：损坏/非 SQLite 的 `catalog.db` 在 create 与 restore 两条路径上都变成结构化 `BACKUP_INVALID`，
  CLI 也变成 `exit 1 + 结构化 JSON + 干净 stderr`；上一轮的 16 个已通过边界**无回归**，成功路径与恢复等价性**无回归**。
- **DS-H 失败边界复审（本轮）= `PASS`（失败边界面）**；R4（修复轮夹带无关格式化）已随本修复还原。
- 状态仍为 **`ENGINEERING REVIEW PASS（产品面/失败边界面）/ SOL FOCUSED REVIEW PENDING`**：按 V3.1 任务表 P4-05 的
  Sol high 一致性聚焦终审为必选 ⇒ **不得标 PASS、不得生成下一卡**。

## 4. 保留项（不阻断本轮，但需如实记录）

1. **B16 未实测**：本会话无 Windows 符号链接权限，成员符号链接边界无法实测；`_members` 仍只查 `is_symlink()`，
   Windows **junction** 未被覆盖（首轮 F4，仍开放）。
2. `sqlite3.Error` 现在统一映射为 `BACKUP_INVALID`（文案 "failed verification"）：对 BUSY/IO 类错误而言**安全但语义偏粗**，
   若以后要区分“被占用”与“损坏”，可另立更细的错误码（非本卡要求）。
3. `restore_backup` 对整包做两次全量 SHA-256（首轮 F5 成本观察，未变）；CLI 对非 `CatalogError` 的 `OSError`
   仍会打印 traceback（首轮 F6，未变，非阻断）。

## 5. 交给 Sol 的输入（不扩大范围）

Sol high 只需读：本报告 + [失败边界复审](P4-05-dsh-failure-boundary-rereview.md) §3 的三行 B15/B18/B19 +
[F1 修复轮](P4-05-sol-f1-fix-round.md) + `backup.py::_schema` 的异常边界与 `__main__.py` 的错误出口；
不需要重跑全量或扩展到 remap/升级回退/打包/真实图库。

## 6. 复现命令

```powershell
python tmp/p405-review/failure_boundary_probe.py B15 B18 B19     # 3/3 符合预期（有界复跑）
python tmp/p405-review/failure_boundary_probe.py                 # 19/19（全矩阵，含 B16 限制项）
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_05_backup_restore.py `
  tests/integration/test_p4_05_backup_restore.py --junitxml=test-results/p4-05-bounded-rerun-focus.xml   # 12/0/0
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --junitxml=test-results/p4-05-r2-full.xml                                                             # 1799/0/0/48
python tmp/p405-review/review_probe.py                            # Arm A 无回归（全表 0 差异、页面逐字段相同）
uv run --no-sync ruff check . ; uv lock --check ; git diff --check                                      # exit 0
```
