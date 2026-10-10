# P4-STORAGE-GC-B 执行报告

日期：2026-10-11。状态：**IMPLEMENTED / 任务卡定向验收通过**。

## 范围与实现

- 新增停机 LanceDB generation 诊断和维护，以及可选 SQLite `VACUUM`。CLI 提供默认 dry-run 的 `storage optimize-vectors`、`storage vacuum`；只有同时提供 `--stopped --apply` 才会执行写入维护。
- 物理维护先获取 data-root lease 和 storage scan lock，再执行 WAL checkpoint。受管服务或备份持有 lease、scan lock 冲突、catalog/WAL 不可用、向量树路径不安全、磁盘余量不足、活跃代不健康、存在候选代或待修复/outbox 状态时，拒绝或跳过维护。
- 只维护当前活跃且被当前 SearchProfile 引用的图像 generation。检查 SQLite 代状态/引用/entries/outbox 和 Lance 表行数、维度、fragment、字节、版本、scalar index 覆盖。不调用全量 Python `count()`；样本和验证检索有界读取。
- 仅当 `asset_id_idx` 缺失/覆盖不完整时复用既有 `ensure_scalar_index()`；仅对多 fragment 的健康表调用 `compact_files()`。验证行数、样本摘要、固定检索结果和索引覆盖后，调用原生 `cleanup_old_versions(older_than=14 days, delete_unverified=False)`。不删除整个 generation、源照片、人物事实或 Lance 内部文件。
- SQLite `VACUUM` 为独立可选动作：默认 dry-run；仅在停机锁、checkpoint、integrity、空闲页阈值与磁盘空间检查通过后执行；比较 schema 定义、关键业务表计数和 active SearchProfile 指针。
- 为确保锁定的 LanceDB 0.38.0 能执行其拆分的 `compact_files()` 和 `cleanup_old_versions()` API，`pyproject.toml` 显式启用 `lancedb[pylance]` 并更新 `uv.lock`。当前 API 对这两个定向方法会发出弃用警告，提示未来迁移到 `Table.optimize()`；本卡要求分别控制 compaction 与版本清理，因此本次继续调用已核实参数的独立方法，没有调用合并多个副作用的 `Table.optimize()`。

## 定向验收结果

JUnit：`test-results/P4-STORAGE-GC-B-related.xml`，**4 passed / 0 failed / 0 skipped**。

- B1：合成 LanceDB 多批 `merge_insert` 生成 3 fragments；压缩后为 1 fragment，4 行、ID/向量、固定检索命中及顺序等价。版本清理 API 报告实际释放 **0 bytes**，符合所有版本均未超过 14 天保留期；没有把表总大小或 compaction 误报为释放量。
- B2：构造 scalar index 后追加未索引行；维护复用 `ensure_scalar_index()` 返回 `refreshed`，覆盖恢复为 3/3 indexed、0 unindexed。
- B3：候选 generation 阻止活跃代维护；active data-root lease 和 scan lock 均拒绝执行；compaction 抛出合成中断后表仍可读，重试成功；RETIRED generation 的行数和版本不变。验证清理参数为 14 天、`delete_unverified=False`。
- B4：合成 SQLite 删除大表记录后，`integrity_check` 前后均为 `ok`；schema/关键表计数/Profile 指针不变；示例 VACUUM 从 9,850,880 freelist bytes 降到 0，catalog 文件实际缩小 **9,850,880 bytes**。该数值仅描述隔离合成 fixture。

其他检查：

- `uv run --no-sync ruff check src/photo_search/storage_maintenance.py src/photo_search/__main__.py tests/integration/test_p4_storage_gc_b.py`：通过。
- `uv lock --check`：通过。
- `python -m py_compile` 检查两个新增/修改的 Python 模块：通过。
- `python -m photo_search storage optimize-vectors --help` 和 `storage vacuum --help`：通过。
- `git diff --check`：通过。

仅运行以上任务卡相关 pytest 与 CLI/lint 检查；没有跑全量 unit/integration、30 万行数据、GPU/模型或真实 PhotoSearchData/图库。GC-A 历史全量失败维持原样并独立跟踪；GC-A 的路由例外仍不是 DS-H PASS，本报告不重复审核 GC-A。

## 未验证项与交接

- 未在用户真实 data root 执行维护，实际 freed bytes 由运行时原生 cleanup 结果给出；未超过保留期的版本可能报告 0 bytes。
- 没有覆盖真实服务进程、真实备份进程、系统断电或硬件故障；synthetic 锁持有和异常后重试检查通过。
- LanceDB 0.38.0 对指定的两个独立方法发出弃用警告。后续如升级 LanceDB，应重新确认参数和分离的阶段语义后再迁移 API。
- GC-B 已交付并可本地提交；没有启动后续全量基线修复任务，也未推送。
