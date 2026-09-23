# P4-02A C2 聚焦终审报告

**结论：`SOL FOCUSED REVIEW PASS`（P4-02A 本卡范围）。** 审查基线为 `95c7104`，起始工作树干净。本轮只审生产入口真实性、并发与旧 revision 顺序、`busy_timeout`、主审 A1 修复及直接回归；没有复做 DS-H 全量工程主审，也没有修改生产代码。

## 依据与受审边界

- 任务卡：[P4-02A](../tasks/P4-02A.md) 的不变量 1～6、交付事实与审查路由；主架构 §7.2/§7.3 的 outbox 提交、派生索引及恢复顺序；V3.1 §2.3/§11 的 Sol 聚焦终审规则。ADR-0003 把正式 artifact/outbox/Lance 确认顺序留给 P2-03，本卡没有改写 ADR 或 contract。
- 主审输入：[DS-H 主审报告](P4-02A-dsh-engineering-review.md) 的 A1/A2/A3、[实现报告 §12](P4-02A-maintenance-entry.md) 的 A1 修复与验收、`95c7104` 的关键 diff、`test-results/p4-02a-fix-maintenance.json` 与 JUnit。主审原结论为 `ENGINEERING REVIEW FAIL`；A1 修复和复核发生在其后，不能把原结论直接写成主审 PASS。
- 本轮实查 `drain_semantic_outbox`、CLI 调用链、`IndexCoordinator` 的领取/应用/确认与 `applied_events`、相关最小用例和机器证据；未复扫普通实现、全仓 diff、30 万性能或真实照片。

## 聚焦裁决

| 边界 | 结论与直接证据 |
|---|---|
| 生产入口 | `python -m photo_search.indexing.cli drain` 经 `Catalog.open` 调用 `drain_semantic_outbox → IndexCoordinator.process_all`，真实工件/outbox/Lance 夹具从 pending 300 到 confirmed 300；`applied=confirmed_delta=300`，再调用为 0。入口是**操作员显式运行的 CLI**，没有自动调度或冻结包内入口承诺。|
| 并发、旧 revision | 领取先提交；应用事务的 `BEGIN IMMEDIATE` 涵盖权威复读、superseded 检查、Lance 写和 SQLite 确认。两维护者证据合计 `applied=25`、outbox `{CONFIRMED:25}`、Lance 无逻辑重复；第二维护者等待 422.199 ms 后跳过，证明**正确性，不证明并行吞吐**。前台 30 次修订写及 10 次发布均提交，收尾后 `{CONFIRMED:310}`；目标资产的 Lance/entry/最大事件 revision 一致。P4-01C 的“旧写者不得最后落盘”定向回归本轮通过。|
| `busy_timeout` | 独立写锁持有 9 s 时，首个 claim 在配置 5 s 后等待 5480.777 ms，返回 `CATALOG_UNREADABLE` / `database is locked`；一次有界重试后 24 条全确认、无重复。`retry_limit=0` 的拒绝仍抛结构化错误，后续可 drain。前台侧本轮证据为 **0 次真实拒绝**，只证明全部已提交写入未静默丢失；不宣称实测了前台拒绝。|
| A1：部分进展后拒绝 | 修复后的 `applied_events` 在已提交的批次后计数，重试使用 `limit - applied_events`；本轮定向回归验证先确认 2 条、第 2 批 apply 被拒、重试后 `applied=confirmed_delta=5` 且不超过 limit。主审修复前反例为 3 对 5。批路径计数在 SQLite 提交后增加；“别人已确认而跳过”不计本调用。|
| 崩溃/恢复 | 读取既有最终机器证据：`after_lance` 时 `{IN_FLIGHT:3,PENDING:21}`、Lance 203 行；重放后 24 条确认、224 行且无逻辑重复；中断重启、不中断、crash 重放的派生状态 SHA-256 均为 `2ba50ddd…`。本轮没有重复执行全部 D 级注入。|

## 本轮验证与证据限制

- 用正常 Windows 权限路径运行 7 个定向 pytest 用例，JUnit `test-results/p4-02a-sol-focused.xml`：**7 / 0 failures / 0 errors / 0 skips**，进程退出 0。覆盖 CLI、真实/模拟 busy 拒绝、A1、两个维护者、前台写及旧 revision 交错。首次受限令牌无法创建项目内 `.pytest-tmp/p402a-sol-focused`，产生 7 个 setup 环境错误；同一 JUnit 随后由成功重跑覆盖。未改 ACL。
- 核对修复轮 JUnit：`p4-02a-fix-focused.xml` **36 / 0 / 0 / 0**，`p4-02a-fix-full.xml` **1659 / 0 / 0 / 48**；这是 DS-H 修复轮证据，**本轮未重跑全量**。`git diff --check` 本轮退出 0。受审 `src`、测试及工具无改动。
- 本卡不宣称 1 万/10 万/30 万正式容量、真实模型吞吐、ANN/召回目标、API P95、GTX 1660、冻结包 CLI 可达性或 30 万档 `--repair` 成本通过。

## 未决项与停止点

主审 **A2/A3 未因本次终审而关闭**：不可读工件会让最早优先的整批 drain 持续失败，`ArtifactStore.republish_reclaimed` 仍只有测试调用点；反复 `--repair` 会将卡住夹具的 pending 事件从 3 增至 5、7。它们位于 P2-03 既有状态机/恢复面，超出 P4-02A 允许修改范围，本轮没有接管修复。应另立小卡处理恢复入口与审计幂等，再做 P4-02B 的正式容量 PASS 裁决；不能以本卡聚焦通过把 A2/A3 当作已验证。

**收口口径：P4-02A 可标 `PASS`，P4-02B 仍未启动。** 本报告只关闭 P4-02A 聚焦门禁；后续容量与异常恢复另按相应任务卡验收。模型选择和会话切换由用户手动进行。
