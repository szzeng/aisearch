# P4-05 — 独立 DS-H 完整受影响面工程主审（新会话，2026-09-28）

**性质**：只读独立审查。本轮未改任何产品/测试文件、未改实现报告；新增产物只有本报告 + 任务卡 [P4-05](../tasks/P4-05.md)
状态行与 `docs/development-status.md` 的交接行（并把主审自己的复核脚本留在 `tmp/p405-review/`，不入库）。
**审查方式**：V3.1 §2.4「完整受影响面工程主审」；P4-05 在 V3.1 任务表中**Sol high 一致性聚焦终审为必选**。
**结论**：**产品面 `ENGINEERING REVIEW PASS`**；**1 项发现（F1，停机前提的强制/披露）需 Sol 裁决**，1 项验收覆盖缺口（F2）已由主审独立补证并建议落测，另 3 条非阻断建议（F3–F5）与 1 条 CLI 表面观察（F6）。
状态：**`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`** —— 按 V3.1 §2.3/launcher §4，本阶段停止，**不标 PASS、不生成下一卡**。
基线 `HEAD = f00955e`（master，工作树含 P4-05 未提交改动）。

## 1. 受影响面（逐项）

| 文件 | 变更 | 判定 |
|---|---|---|
| `src/photo_search/backup.py`（新增，230 行） | `create_backup` / `restore_backup`：固定成员根 + 逐文件（路径/大小/SHA-256）清单 + staging + 原子发布；恢复前双重校验 + schema 精确匹配；symlink 拒绝 | 正确；见 §2/§3/§4 |
| `src/photo_search/__main__.py`（+26） | 新增 `backup create|restore` 子命令（`--data-root/--bundle/--json`），`CatalogError` → 结构化 JSON + exit 1 | 接线正确；F6 为表面观察 |
| `tests/unit/test_p4_05_backup_restore.py`（新增，8 用例中的 6） | 清单/摘要、空目录恢复保 metadata、备份与恢复**失败注入**、manifest schema 篡改、真实 schema 15/17 拒绝、CLI JSON | 有效（非空壳） |
| `tests/integration/test_p4_05_backup_restore.py`（新增，1 用例） | 生产扫描 + FTS + Lance 的恢复前后 `hits` 与 `channels` 逐项相同 | 有效但覆盖窄（见 F2） |
| `docs/reports/P4-05-backup-restore-implementation.md`、卡状态行、`development-status.md` | 实现报告与交接 | 与代码一致；薄（2.9 KB），关键未测项已如实列出 |

**未改动**：`catalog/**`（含 `errors.py`、schema、迁移，仍 16、0017 空闲）、`api/**`、搜索信封/游标、
`indexing/**`、依赖/`uv.lock`、ADR/companion、§12.2（`git diff --stat` 已核）。

## 2. 独立验证（本主审自己跑，不复述实现者）

| 项 | 本主审结果 | 与实现者对照 |
|---|---|---|
| 定向（新增单测 + 新集成） | **8 / 0 / 0**（`test-results/p4-05-review-focus.xml`，7.66 s） | 8/0/0（7.20 s） |
| B 级全量 | **1795 / 0 / 0 / 48，249.31 s**（`test-results/p4-05-review-full.xml`） | 1795/0/0/48，246.31 s |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿（exit 0） | 一致 |
| **主审自建探针（`tmp/p405-review/review_probe.py`）** | 见下 §3 | 实现者未做该项 |

## 3. 主审自建探针的独立证据

**Arm A（正确性/等价/不重扫）** —— 生产扫描两图 + 真实 Lance 写入 + 人工地点（`create_place` + `assign_place`）
→ 停机备份 → 空目录恢复：

- **全表逐表 count + 内容摘要比对：0 张表不同**（覆盖 `places`、`asset_place_assignments`、
  `catalog_filter_revision_events` 等人工知识与操作历史，共 16 个成员文件）。
- 恢复后计数与恢复前逐项一致：`index_entries=2`、`artifacts=2`、`search_documents=2`、`places=1`、
  `asset_place_assignments=1`、`catalog_filter_revision_events=1` ⇒ **未重扫、未重编码、未重新派生**。
- 恢复根 schema = 16，`restore` 返回 `status=ok`；同一查询在恢复前后返回**逐字段相同**的页
  （`hits`（含 `hit_reason`）、`channels`、`search_projection`、`binding`、`vector_scan` 等全部相等）。
  - 口径说明（透明记录）：首次比对曾出现 `search_projection.processed_assets: 2 vs 0`——那是**首次请求自己追赶了 2 条待投影**
    造成的探针假象（该字段语义就是“本次请求追赶了多少”），把投影先暖机后再比对即完全相等（`page_identical: True`）。

**Arm B（停机前提的最小反例）** —— 用 `create_backup(copy_hook=...)` 在 `catalog/catalog.db` **已复制之后**
让一个真实写者提交（`create_place`，走产品目录路径）：

| 观测 | 结果 |
|---|---|
| 备份开始时 live 根 `places` | 1 |
| 备份结束后 live 根 `places`（写者已提交） | **2** |
| 备份包 manifest 校验 | **通过**（每个成员与自身摘要一致） |
| `restore_backup` 返回 | `status=ok` |
| 恢复根 `places` | **1**（不含那次提交） |

即：**备份包通过全部校验、恢复成功，但它不是某一时刻的快照**。文件级 SHA-256 只能证明“每个成员与自身一致”，
无法发现跨成员的时间错位（catalog 与 vectors/artifacts/thumbnails 来自不同瞬间）。

## 4. 发现

### F1（交 Sol 裁决；主审已给最小反例）
**「停机」是操作员假设，未被检测也未被记录。** 实现报告已如实声明“以操作员保证停机为前提”，但：
- `create_backup` 不检查任何存活信号（没有 lease/锁/`-wal`/`-shm` 判定，也不要求显式确认）；
- manifest 的 `excluded` 只写 `source_photos` 与 `models`，不记录“是否在停机状态下采集”；
- §3 Arm B 证明：**运行中备份可以静默通过校验并恢复成功**，只是结果不是时间点快照。
产品本身能容忍其中一部分窗口（ADR-0012 状态矩阵、`PENDING_REPAIR`、搜索侧 `LAGGING`），
因此最可能的后果是**恢复后需要一次修复/再派生**，而不是静默损坏；但**备份包与恢复输出都不会告诉操作员发生过这件事**。
建议（择一，交 Sol 裁定哪条是本卡 PASS 的必要条件）：
1. **廉价守卫**：`catalog/catalog.db-wal`（或 `-shm`）存在且非空时，`create_backup` 拒绝并要求显式 `--force`/`--stopped`；
   同时在 manifest 记录存活证据（`wal_present`、`shm_present`、是否显式确认）。
2. **显式声明 + 文档**：强制 `--stopped`（或等价确认）并写进 manifest；文档明确“运行中备份可能需要在恢复后跑一次修复/再派生”。

### F2（验收覆盖缺口；主审已独立补证，建议落测）
卡面 §4.2 要求“人工知识（人物/地点/排除/hidden/操作历史）行摘要一致”。**提交的测试没有做这条行级断言**：
单测只查 `metadata` 一行 + 几个文件字节，集成测试只比 `hits`/`channels`。
本主审用 §3 Arm A 的**全表 count+摘要**补证（0 差异、含 `places`/`assignments`/`filter_revision_events`）。
建议把该断言（至少人工知识三类 + 全表摘要）补进 `tests/unit|integration/test_p4_05_backup_restore.py`，防止以后回归。
**非阻断**（逻辑上 `catalog.db` 整体复制必然保留全部 SQL 行，主审已实证）。

### F3（非阻断，健壮性）
`MEMBER_ROOTS` 是硬编码元组；数据根里既不在成员表、也不在 `excluded` 声明内的顶层项会被**静默**排除。
本主审核对当前 `config.py`：`catalog`/`vectors`/`artifacts`/`thumbnails`/`previews`/`cache`（含 `cache/media`）/
`runtime`/`places`（含 `places/datasets`）**全部被覆盖**，`backups/` 正确地不入备份，`models/` 按设计排除并已声明 ⇒ 今天无缺项。
建议：成员表从 `config` 常量派生，并把「根下未纳入备份的顶层项」写进 manifest（如 `skipped_top_level`），避免未来新增目录静默丢失。

### F4（低，Windows 边界）
`_members` 只检查 `Path.is_symlink()`；Windows **junction** 在 `is_symlink()` 下为 `False`，会被 `rglob` 跟随进备份。
建议同时检查 `Path.is_junction()`（Python 3.12 可用）或改用不跟随的遍历。

### F5（低，成本与未测边界）
`restore_backup` 对整包做**两次**全量 SHA-256（bundle 与 staging）；缩略图 GB 级时成本可观而报告未给数字。
另：备份包内 `catalog.db` 若带未 checkpoint 的 `-wal`（非正常停机）时 `_schema()` 能否读取未测——属边界，建议由 Sol 之后或后续测量卡明确。

### F6（低，CLI 表面）
只有 `CatalogError` 被转成结构化 JSON；其它 `OSError`（磁盘满、权限）会打印 traceback（exit 非 0）。
`--json` 输出形状也没有在报告里给出样例。建议统一错误出口或在报告记录形状。

## 5. 压缩交接包（供 Sol high 一致性聚焦终审）

- **基线/改动文件**：`f00955e` + 未提交：`src/photo_search/backup.py`（新增）、`src/photo_search/__main__.py`（+26）、
  `tests/unit/test_p4_05_backup_restore.py`、`tests/integration/test_p4_05_backup_restore.py`、
  `docs/reports/P4-05-backup-restore-implementation.md`、`docs/tasks/P4-05.md`、`docs/development-status.md`。
  产品文件 SHA-256（前 16）：`backup.py = e80ce7ef5fe8286b`、`__main__.py = 8054c5ba27ff3137`（审查时点）。
- **结论表**：定向 8/0/0（主审 7.66 s）、B 级 1795/0/0/48（主审 249.31 s）、ruff/lock/diff-check 全绿；
  主审 Arm A = 全表 0 差异 + 计数不变 + 恢复前后同页；Arm B = 运行中备份可静默通过校验（反例）。
- **必审/未决高风险项**：**F1**（停机前提的强制或披露口径，二选一由 Sol 定；主审最小反例见 §3 Arm B）。
- **ADR 预期与实际差异**：无 ADR/companion 文本改动；`catalog/errors.py` 未改（`BACKUP_*` 是 `backup.py` 内传给
  `CatalogError` 的**新命令局部字符串码**，未进入既有错误码目录，也未改搜索信封/错误码语义）——请 Sol 确认这个界定成立。
- **关键 diff 定位**：`backup.py:1-230`（`_members`/`_verify`/`_schema`/`create_backup`/`restore_backup`）、
  `__main__.py:210-232`（子命令）与 `270-288`（分派与错误出口）。
- **最小反例与命令**：`tmp/p405-review/review_probe.py`（Arm A/B），结果 `tmp/p405-review/review-probe.json`；
  pytest 命令与 JUnit 见 §2。
- **未验证项（Sol 不得扩大范围）**：真实图库演练、模型权重是否随备份携带、来源 remap（P4-05A/B）、升级/回退、
  多机/打包、服务进程暂停/句柄关闭协调、HTTP 恢复入口、40 万档、冷缓存/soak、ACL；§12.2 与发行级备份验收均未授予。

## 6. 未验证 / 不授权

- 未测真实中文 CLIP 文本塔、真实图库、GTX 1660、打包/离线发行、40 万档；未触碰参考夹具与真实照片目录。
- 本卡的“一致性”= 停机状态下的一致性；**运行中备份的一致性未被支持**（F1）。
- 本报告不授予 §12.2、发行级备份或打包 PASS；**不生成下一张卡**（Sol 必选、阶段未完成）。
