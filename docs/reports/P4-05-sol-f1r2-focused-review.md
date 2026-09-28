# P4-05 — F1-R2 一致性聚焦终审

日期：2026-09-28。

**结论：F1R2-2 接受为非阻断边界；本轮指定租约与结构化错误边界未发现新增阻断。P4-05 整体仍为 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW FAIL`，仅因上轮裁决要求的恢复输出披露尚未闭合，不得标任务 PASS、不得生成下一卡。**

## 1. 范围与基线

本轮按用户要求只读聚焦审查，不接管实现，不重跑全量，不扩大到 remap、升级回退、打包或真实图库。输入为：

- [DS-H F1-R2 完整受影响面复审](P4-05-dsh-f1r2-affected-surface-review.md)。
- [F1-R2 修复记录](P4-05-sol-f1-r2-fix-round.md)。
- `runtime/data_root_lease.py`、`app.py` lifespan、`backup.py::create_backup/_consistency_snapshot/_members` 的关键变更。
- L1–L8 与 B15/B18/B19；仅核对已有产物及 JUnit，不重新执行探针或测试。
- 上轮未决的恢复输出披露项；核对 `backup.py::restore_backup` 返回值及 CLI 输出路径。

基线为 `HEAD=f00955e` 加未提交工作树。当前产品文件 SHA-256 前 16 位与 DS-H 报告一致：

| 文件 | SHA-256 前 16 位 |
|---|---|
| `src/photo_search/runtime/data_root_lease.py` | `05e30d6f1482dacf` |
| `src/photo_search/backup.py` | `15a5fb81b2546625` |
| `src/photo_search/app.py` | `c75da20621936123` |

本次落盘只新增本报告并更新任务卡/状态交接，不修改产品、测试、schema、依赖或既有主审报告。

## 2. F1R2-2 裁决：接受停掉全部写者的个人试用口径

本卡承诺的是全部写者已停止之后的备份；不要求在本卡内继续把所有维护 CLI 接入租约。第一方维护 CLI 必须明确点名，不能统称为“外部写者”：操作员的 `--stopped` 确认涵盖 HTTP 服务、`indexing drain/build/reexecute`、写入数据根的 catalog 维护命令，以及任何直接写库/写派生物的进程。该前提必须持续整个复制过程。

当前租约能排斥持有同一租约的 HTTP 服务，并覆盖备份持有期间的新 HTTP lifespan 启动；陈旧文件本身不代表存活，OS 锁的获取/释放与跨进程排斥已有主审证据。此额外守卫不替代对未接入租约写者的操作员确认。

`wal_checkpoint(TRUNCATE)` 成功只反映该次 checkpoint 的状态；generation/pending 摘要记录停机边界。二者都不是持续阻止维护 CLI 写入的机制，也不能证明所有写者已经停止。`managed_service_lease=acquired` 只能按上述受控服务范围解释，不能升级为全局静止证明。

因此 **F1R2-2 非阻断**，不以维护 CLI 全部接租约作为本卡 PASS 的新增前置。如果维护 CLI 与备份并发，停机前提不成立，该运行不在本卡承诺范围内。

## 3. 尚未闭合：恢复输出丢失一致性声明（SOL-F1R2-1，P2）

上轮聚焦终审已要求“恢复输出保留操作员声明、程序验证范围的口径”。当前 `backup.py::restore_backup`（审查时点第 313 行）只返回 `status/data_root/schema_version/restored_members`，没有 `consistency`；CLI 直接输出该返回值，也没有补充声明。

这不是本轮新增租约机制的缺陷，而是此前披露裁决的未完成项。manifest 虽然保存了确认和受控服务租约事实，恢复命令输出仍不能区分声明已存在、声明缺失及验证范围。本轮不能将该项默认为关闭。

**最小回修范围：**

1. 恢复输出携带备份 manifest 的一致性声明，并明确它是操作员确认及受控 HTTP 租约范围的记录，不代表程序验证了全部写者停机。
2. 对缺少声明的旧包，输出明确的“未知/未确认”；不得自动补成已经确认或已经验证。无需因此增加在线备份能力或改变 schema。
3. 补直接断言：新包恢复输出保留声明，缺少声明的包不被输出为已确认。修复后仅复核此返回值、CLI 出口和对应定向证据；无需重复租约全量主审。

## 4. 证据与实际验证层级

| 项目 | 本轮核对结果与限制 |
|---|---|
| L1–L7 | 读取 DS-H 的独立结果记录及探针断言；没有冒充本轮重新执行。现存租约 JSON 已被 L8 单独复跑覆盖，不能用它证明 L1–L7 的原始结果仍全部留存 |
| L8 | `tmp/p405-review/lease/lease-boundary-report.json` 现存 1 条，另一进程持租约时 `BACKUP_SERVICE_ACTIVE`，`failed=0` |
| B15/B18/B19 | `tmp/p405-review/boundary/failure-boundary-report.json` 三条均符合预期：create/restore 的损坏 catalog 为 `BACKUP_INVALID`，CLI 为退出码 1、JSON、无 traceback |
| 定向 JUnit | `test-results/p4-05-f1r2-rereview-focus.xml`：14 tests，14 passed，0 failures/errors/skipped，10.143 s |
| 全量 JUnit | `test-results/p4-05-f1r2-affected-surface-full.xml`：1801 tests，1753 passed，48 skipped，0 failures/errors，277.417 s |
| 产品版本 | 三个产品文件的当前哈希与主审报告一致 |
| 本轮执行 | 只执行文件读取、哈希及 Git 状态/diff 核对；没有执行 pytest、租约探针、失败边界探针、容量或打包验证 |

主审记录的 Arm A 恢复等价性与 ruff/lock 检查沿用其报告，不记为本轮独立复跑。报告落盘后已回读本报告与两处交接，产品哈希不变，`git diff --check` 退出码 0；没有因此运行产品测试。B16 的 Windows 符号链接权限限制及 junction 未覆盖等既有保留项不被本轮追认通过。

## 5. 报告口径纠正与交接

Arm B 在 catalog 已复制后提交一个晚到的人工地点，并观察恢复根不含该提交。这个观察只证明晚到提交未收录，以及未接入租约的写者可以执行；恢复根可能仍对应提交前状态。它不能单独证明“不是任何时刻的快照”，也未证明 SQLite 与 Lance 在该反例中发生了跨成员不一致。旧报告中的该断言不作为本轮裁决依据。

下一步仍在 P4-05 内：完成 SOL-F1R2-1 的最小恢复披露修复及直接定向验证，再做该边界的聚焦短审。F1R2-2 已裁决，不重开维护 CLI 租约实现，不生成下一任务卡。模型切换如需进行，由用户手动选择。

本报告不授予个人试用全部门槛、发行备份、性能、打包或真实图库演练 PASS。
