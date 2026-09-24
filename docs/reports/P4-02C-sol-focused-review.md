# P4-02C — C2 聚焦终审（2026-09-24）

**当前结论：`SOL SHORT RE-REVIEW PASS`（F4 修复后短审，见文末）。** 下文 `SOL FOCUSED REVIEW FAIL` 是修复前首次终审的历史结论。两次审查均只覆盖任务卡指定的高风险边界及直接回归；独立 DS-H 已完成全量工程主审，本报告未重走全量 diff 或全量测试。

**F4 修复状态（2026-09-24 追加）**：F4 已由 DS-H 会话按本报告「聚焦裁决与回退范围」一节修好——
审计的「当前工件」改按权威 **发布 revision**（`MIN(projection_revision)`，即 drain 判定投影胜负的同一值）
排序，`created_at` / `artifact_id` 仅作发布事件缺失时的确定性兜底；本报告的反例探针
`tmp/p402c-sol/tie_probe.py` 现在是 **1 / 0 / 0 / 0**（修复前 **1 / 1 failure**）。
细节与验收见 [实现报告 §12](P4-02C-recovery-and-audit-idempotency.md) 与
`docs/development-status.md` 的 Latest fix round 段。按本报告要求，修复后的短审只需覆盖 F4 与其直接回归。

## 输入与观察状态

- 基线提交：`665109e`；F1/F2/F3 修复仍为未提交工作树改动。聚焦输入为任务卡 `docs/tasks/P4-02C.md`、DS-H 主审报告的修复面复核 R1–R7、实现报告 §11、V3.1 §11、主架构 §7.2、ADR-0003 §7，以及直接实现与回归。
- DS-H 已记录 focused `11 / 0 / 0 / 0`、受影响面 `36 / 0 / 0 / 0`、全量 `1670 / 0 / 0 / 48`，以及隔离、恢复、失败关闭和变异证据；这些是前序证据，本轮没有冒充独立重跑。
- 本轮三条直接对照回归通过：`test_one_generation_with_two_artifacts_keeps_the_audit_bounded`、`test_a_reclaimed_newest_artifact_outranks_the_older_one`、`test_the_new_claim_predicate_keeps_the_p4_01c_discipline`。JUnit `test-results/p4-02c-sol-controls.xml`：**3 / 0 / 0 / 0**，命令 exit 0。

## F4 — 同毫秒发布时审计把旧内容排到更高 revision（阻断）

`IndexCoordinator.audit_and_schedule_repairs` 在 `src/photo_search/indexing/store.py:1026` 用 `(artifact.created_at, artifact_id)` 选三元组的“最新工件”，然后在 `:1041` 左右交给 `_schedule_existing` 分配新的 `projection_revision`。`created_at` 的单位是毫秒；同一毫秒内两个合法内容版本的 ID 字典序不代表发布顺序。实际发布协议已用 `index_outbox.projection_revision` 和 `index_entries.artifact_id` 表示内容投影的权威顺序。现有选择规则可与它相反。

最小反例（项目内合成夹具，`tmp/p402c-sol/tie_probe.py`）：

1. 同一 `(space_id, asset_id, generation_id)` 发布旧工件 `p4-02c-41`，事件 revision **0**；增加 `catalog_filter_revision` 后，在**相同 `created_at` 毫秒**发布新内容工件 `a-new-content`，事件 revision **1**。两次都经真实 `ArtifactStore.persist_result`，没有手工插入 artifact/outbox。
2. 在 drain 前调用生产 `audit_and_schedule_repairs`。它选字典序较大的旧工件，报告 `scheduled=['p4-02c-41']`，为旧工件新增 revision **2**。事件序列变成旧 0 → 新 1 → **旧 2**。
3. 生产 `drain_semantic_outbox` 确认三个事件后，Lance 行 `projection_revision=2`；与**旧向量**距离 `0.0`，与**新向量**距离 `1.883802890777588`。旧内容重新成为搜索向量，且 outbox 全确认；失败没有结构化信号。

命令（`UV_CACHE_DIR=.uv-cache`，正常 Windows 权限路径、项目内 `--basetemp`）及结果：

```powershell
uv run --no-sync python -m pytest -q -s -p no:cacheprovider `
  tmp/p402c-sol/tie_probe.py `
  --basetemp=.pytest-basetemp-p402c-sol/elevated-tie2 `
  --junitxml=test-results/p4-02c-sol-tie.xml
```

JUnit **1 / 1 failure / 0 errors / 0 skips**，命令 exit 1；失败断言是“审计最后排程的工件必须是新内容”。首次受限令牌尝试遇到项目内 pytest 临时目录 `WinError 5`，走正常 Windows 权限路径后完成复现；没有更改 ACL。该失败属于实现语义，不是环境失败。精确同毫秒并列在 DS-H R7 中已标为未注入项，本轮注入证实它会破坏旧内容不回滚不变量，而非只多排程一次。

## 聚焦裁决与回退范围

| 边界 | 本轮裁决 |
|---|---|
| `RECLAIMED` 领取隔离、`PUBLISHED` 损坏失败关闭、逐字节等价重执行 | DS-H 已给直接注入证据；本轮未发现新矛盾，不扩大复测。 |
| 同三元组重复审计不增长 | 既有两工件回归本轮通过；同毫秒反例揭示“选哪个工件”的权威性缺口。 |
| 旧内容不回滚最新投影 | **FAIL：F4 已在真实发布、审计、drain 路径复现。** |
| P4-02C 总闭环 | **FAIL；不得标 PASS，不得宣称 P4-02B 前置已满足。** |

按 V3.1 §2.3/§11，普通实现修复交回 DS-H：只修审计按三元组选择修复目标的权威顺序，并补同毫秒、反向 artifact ID 的直接回归；同时核实 F3 的最新工件等待重执行时仍不会被旧工件越权覆盖，且真实缺失仍能排程。不能仅调整测试时间戳或 ID 排序来规避反例。修复后的 Sol 短审只覆盖 F4 与这些直接回归；不重走全量 diff。本轮未改生产代码、测试、工具或既有任务卡，只写终审报告和状态。

真实中文 CLIP 权重缺失、30 万档成本、ANN/API P95、GTX 1660 与冻结包 CLI 可达性继续按实现报告 §11.5 保留为未验证/BLOCKED；本轮没有把它们改写为通过。

## 修复后短审（2026-09-24）

**结论：`SOL SHORT RE-REVIEW PASS`。** 只复核 F4、F3 和真实缺失排程的直接边界。DS-H 修复将审计目标的权威顺序改为每个工件在 outbox 中的最小 `projection_revision`；审计生成的更高 revision 不会反过来把旧工件排为当前工件。原有 `CLAIMABLE_EVENT_PREDICATE`、`_is_superseded`、`_schedule_existing` 复用条件及显式 revision 路径未在 F4 轮改动。

- 独立重跑本报告原反例 `tmp/p402c-sol/tie_probe.py`：同毫秒旧工件 rev 0、新工件 rev 1；审计 `scheduled=[]`，未新增 rev 2；drain 确认 2 条事件，Lance revision **1**，与新向量距离 **0.0**、与旧向量距离 **1.883802890777588**。JUnit `test-results/p4-02c-sol-rereview-f4.xml` **1 / 0 / 0 / 0**，exit 0。
- 定向回归 `test_a_same_millisecond_content_change_keeps_the_newest_vector`、`test_a_reclaimed_newest_artifact_outranks_the_older_one`、`test_repeated_audits_do_not_grow_the_backlog_but_a_real_loss_still_schedules`、`test_one_generation_with_two_artifacts_keeps_the_audit_bounded`：JUnit `test-results/p4-02c-sol-rereview-controls.xml` **4 / 0 / 0 / 0**，exit 0。覆盖 F4、F3、重复审计有界与删除确认后的真实补排程。
- 核对 DS-H 修复轮留存的 JUnit：focused **12 / 0 / 0 / 0**、受影响面 **36 / 0 / 0 / 0**、全量 **1671 / 0 / 0 / 48 环境跳过**。本短审未重跑这些套件；`git diff --check` 本轮 exit 0。

据此，F4 不再阻断 P4-02C；结合已通过的 DS-H 主审，**P4-02C 可标 PASS**，P4-02B 的 A2/A3 硬前置可更新为「已收口」。这不构成 P4-02B 容量 PASS，也不改变真实模型、30 万档、ANN/API P95、GTX 1660 和冻结包 CLI 的未验证口径。若发布事件被手工删除，排序只能走 `job_id/created_at/artifact_id` 兜底，该非受支持状态未注入，保留为未验证项。
