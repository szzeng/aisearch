# P4-05 — 恢复披露 R2 聚焦短复审

日期：2026-09-28。

**结论：`SOL SHORT REREVIEW PASS`，SOL-F1R2-1 关闭。上轮两个反例均已修复，未发现本次披露改动的新增阻断或直接回归。结合此前 DS-H 完整受影响面工程复审与已接受的 F1R2-2 口径，P4-05 记 `PASS`（仅个人试用切片的停机一致性备份、空目录恢复及迁移保护）。**

## 1. 范围与基线

仅复核 [披露短审报告](P4-05-sol-disclosure-short-review.md) 中的两个剩余反例及 [R2 修复记录](P4-05-sol-disclosure-r2-fix.md)：`backup.py::_restored_consistency`、`restore_backup` 返回值、CLI JSON 透传与新增直接断言。本轮不接管实现，不重跑全量、租约或恢复故障矩阵，不扩大到维护 CLI 接线、remap、升级回退、打包或真实图库。

基线 `HEAD=f00955e` 加未提交工作树。当前 SHA-256 前 16 位：

| 文件 | SHA-256 前 16 位 |
|---|---|
| `backup.py` | `af10a7ce22c2b2d9` |
| `tests/unit/test_p4_05_backup_restore.py` | `e27235656de22b03` |
| `__main__.py` | `1b9f0496394b136c` |
| `runtime/data_root_lease.py` | `05e30d6f1482dacf` |
| `app.py` | `c75da20621936123` |

后面三项与前次审查一致。本轮没有修改产品或测试源文件，落盘仅为本报告、五案例证据与任务卡/状态交接；旧失败报告及其反例产物保留。

## 2. 原失败边界逐项裁决

| 边界 | 当前行为与结论 |
|---|---|
| 空对象 `consistency={}` | 声明必须同时满足 `mode=stopped` 与 `operator_confirmed is True`；空对象输出 `status=unknown`，保留原字典用于审计，不再声称已确认。原问题 A 关闭 |
| 合法旧式 `{mode: stopped, operator_confirmed: true}` | 保留原始操作员声明，说明明确含 `no managed HTTP service lease was recorded`，不再附加未记录的租约事实。原问题 B 关闭 |
| 当前新式声明 | 只有实际记录 `managed_service_lease=acquired` 时，说明才披露该受控 HTTP lease 记录；声明原样携带，未丢弃 snapshot 或其它原文 |
| 缺失或非字典声明 | 继续输出 `unknown`，不自动补成已确认 |
| 验证范围 | 新旧有效声明均明确 `this does not prove every possible writer was stopped`；不将操作员声明或 HTTP 租约记录升级为全部写者静止证明 |
| CLI 输出 | `__main__.py` 仍直接输出 `restore_backup` 返回值；新增旧式声明 CLI 测试覆盖退出码 0、原声明保留及无租约记录说明；既有新式 CLI 测试保留 |

`unknown` 时保留原字典不等于认可该声明；明确状态与说明已经区分未知及已记录的操作员确认，符合此前裁决。

## 3. 验证与证据来源

| 项目 | 本轮核对结果 |
|---|---|
| 实现方定向 JUnit（本轮实读） | `test-results/p4-05-sol-disclosure-r2-focus.xml`：19 tests，19 passed，0 failures/errors/skipped，17.030 s；未冒充本轮复跑 |
| 本轮独立披露小探针 | `test-results/p4-05-sol-disclosure-r2-review-probe.xml`：5 tests，5 passed，0 failures/errors/skipped；命令退出码 0 |
| 独立探针案例 | 当前新式声明、缺失声明、非字典声明、空对象、旧式操作员声明，五项全部符合预期；旧式案例明确核对无 HTTP lease 记录说明及原声明保留 |
| 独立探针明细 | `tmp/p405-sol-disclosure-review/r2-probe.json`：五个输入、预期、实际输出及当前完整源文件 SHA-256；不覆盖上轮失败产物 |
| Git 检查 | `git diff --check` 退出码 0；本轮未改产品源文件 |

本轮探针命令为项目 `.venv/Scripts/python.exe -B -c <内存探针>`。通过 AST 仅提取当前 `_restored_consistency` 执行五个内存输入，不导入产品模块、打开 catalog、复制数据根或调用恢复流程；它验证真实 helper 的输出，不冒充端到端恢复测试。API/CLI 路径同时依据当前调用代码与实现方对应直接测试/JUnit核对。

此前 [DS-H 完整受影响面复审](P4-05-dsh-f1r2-affected-surface-review.md) 的租约、checkpoint、失败边界、Arm A 恢复等价性与 B 级证据沿用：全量 JUnit `test-results/p4-05-f1r2-affected-surface-full.xml` 为 1801 tests、1753 passed、48 skipped、0 failures/errors，277.417 s。这是既有主审版本的结果，不能表述为本轮披露 R2 的独立全量复跑。此次变更只涉及披露返回值与说明，原短审已明确允许用直接定向证据闭合，不要求重复该全量套件。

## 4. P4-05 闭环与保留范围

SOL-F1R2-1 关闭；F1R2-2 的非阻断裁决继续有效：`--stopped` 必须覆盖 HTTP、第一方 `drain/build/reexecute/catalog` 维护写者与直接写库/写派生物进程，并在整个复制期间保持停机。HTTP 租约与 checkpoint 不替代该操作员前提，不新增维护 CLI 全部接租约的要求。

旧 Arm B 仅证明晚到提交未收录，不作为“不是任何时刻快照”的证明。之前的聚焦失败结论已由本次修复复核关闭，旧报告保留为历史证据；以本报告和最新状态页作为当前交接。

此前非阻断建议及未验证项保留：B16 Windows 符号链接权限限制、junction 边界未覆盖、连接未显式 closing、部分非 CatalogError 的 OSError 仍走 traceback，以及双重摘要校验的成本。本轮不将这些限制追认为通过。

本卡 `PASS` 只表示已完成约定的个人试用备份恢复切片，不等于个人试用全部门槛、§12.2 性能、发行级 remap/备份、升级回退、打包或真实图库演练通过。本轮不生成下一卡、不启动下一实现、不提交或 push；后续工作由新的明确范围承接。
