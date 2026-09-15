# P3-04A — 单脸纠错、合照与人工锁定：C2 规则冻结

- 状态：PASS（A 级契约/文档闭环；不含生产实现）
- 日期：2026-09-15
- 实际模型：C2（GPT-5.6 Sol / high），由用户在 Codex 中手动选择；未发生自动模型切换
- 起点 HEAD：`ac3f422004ce8ef22199eed6a4c813dc403d4183`（开始时 `master...origin/master`，工作树干净）
- 权威决策：[ADR-0007](../decisions/ADR-0007-face-correction-lock-exclusion-contract.md)
- 机器伴随物：[p3-face-correction-contracts.v1.json](../decisions/contracts/p3-face-correction-contracts.v1.json)

## 1. 交付与阶段边界

本任务只冻结 P3-04 的可实现规则，新增 ADR、JSON Schema 伴随物和契约反例测试。没有修改
`src/photo_search/**`、migration、schema_version、依赖、既有 ADR/JSON、打包或前端；因此这里的 PASS
只说明冻结文档和伴随物自洽，不声称数据库/API/自动提交已执行拒绝。生产实现进入 P3-04B。

## 2. 核心决策

- 继续使用 `person_operations` / `person_operation_items` 作为唯一人物知识操作权威；未来 0012 扩展闭合
  CHECK，而不是建立第二套 face operation 日志。
- 新 type：`FACE_ASSIGN`、`FACE_UNASSIGN`、`FACE_RESTORE_AUTO`、`BATCH_FACE_EDIT`；补偿继续使用
  `UNDO`。item 新增 `FACE_CONTROL`，以 `entity_key=face_id` 与 PERSON_BUNDLE 的 integer entity_id 分流。
- `expected_revision` 是 logical face-control revision；无控制行等价 AUTO_ALLOWED/revision 0；实际变化
  current+1，no-op/replay 不推进。
- 单脸 assign/unassign/restore_auto 的有效关联、模式与排除矩阵逐项冻结；assign 回旧目标只解除目标排除，
  restore_auto 保留全部排除且不采用 suggestion。
- 批量 1～500 个明确且去重 face_id，允许 mixed action，先全量校验再单事务提交；服务端不得按照片扩张。
- 自动提交每次读取 control/exclusion 并比较观察到的 logical revision；MANUAL 两态、活动排除与晚到
  revision 都不能被自动覆盖，建议只能保持 SUGGESTED。
- FACE_CONTROL before/after canonical bundle 支持无冲突补偿撤销；后续脸级人工变化使整个旧操作不可撤销；
  revision 始终向前，不允许 undo an UNDO。
- changed 操作复用 `PERSON_CHANGED`，同一事务只推进一次 CatalogFilterRevision、排资产投影队列并把
  已发布 face entry 置 PENDING_REPAIR；不新增 queue cause，不触碰图片向量 namespace。

## 3. API 与安全边界

冻结 GET/PUT `/api/v1/faces/{face_id}/assignment`、POST `/api/v1/face-assignments/batch`，并扩展既有
POST `/api/v1/person-operations/{operation_id}/undo`。稳定 FACE_* 错误区分 not found、revision、
idempotency、batch limit、state 与 undo conflict；person target 继续复用 PERSON_NOT_FOUND 与
PERSON_REDIRECT_CYCLE。

响应/错误/日志/普通诊断不返回路径、bbox、landmark、向量、confidence/单对相似度、幂等 payload 或内部
快照。`face_summary` 只增加 manual_assigned_faces、manual_unassigned_faces、
faces_with_active_exclusions、active_face_exclusions 四个 SQLite 权威计数。

## 4. 迁移结论

P3-04B 需要且只允许 `0012_face_correction_operations.sql`。0011 的控制/排除表字段已经足够，不加任何
无默认值 NOT NULL 列；必须迁移只因为 0011 的 operation type 与 item entity shape 是闭合 CHECK。
0012 安全重建两张 operation 表、原样回填历史/undo/引用并扩展互斥 entity_id/entity_key。0011 以前文件
保持逐字节不变；`search_projection_queue` 不重建，因为 `PERSON_CHANGED` 已准确覆盖。

## 5. 反例覆盖

ADR §11 固定 14 组实现期反例：取消后自动回归、指定后晚到覆盖、重新指定旧排除、restore 清排除、重复
face、批量中途失败、后续编辑后的 undo、undo an UNDO、P3-03 合并交叉冲突、合照范围、revision 0、
幂等门顺序、锁下建议、changed/no-op/replay 的派生差异。

契约测试实际覆盖 request action/target 条件、revision 0、request_id 边界、500 上限与重复 face、三种
mode/assignment 组合（含 AUTO_ALLOWED 可保留既有自动归属）、排除唯一和 canonical 顺序、operation item 唯一、隐私字段关闭、错误码闭合及 ADR
关键语义防漂移。

## 6. 实测命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q ... tests/unit/test_p3_face_correction_contracts.py` | 26 passed，exit 0；JUnit `test-results/p3-04a-contract.xml` |
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；最终 JUnit `tests=1064 failures=0 errors=0 skipped=1`，302.892 s；`test-results/p3-04a-junit.xml` |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，clean |
| `uv run --no-sync uv lock --check` | exit 0，95 packages，lock 未变化 |

专项首轮有 2 个文档 phrase 断言失败：目标语义跨 Markdown 行，测试错误地要求整句连续；收窄为稳定短语
后通过。没有因该失败改变冻结语义。最终自审又发现 companion 曾把 AUTO_ALLOWED 误约束为无有效归属，
而 ADR 明确允许既有 AUTO/CONFIRMED；修正 schema 并新增正例后，专项为 26/26。

完整套件在受限令牌下首次运行时，项目 `.pytest-tmp/P3-04A-full-20260915` 的 mkdir 全面触发
`PermissionError [WinError 5]`，产生大量 setup error，不能作为代码失败或通过证据。按任务卡已知边界，
获准使用正常 Windows 权限后改用新的项目内 basetemp
`.pytest-tmp/P3-04A-full-escalated-20260915` 重跑得到 1063/0/0/1；伴随物自洽修正后又以新目录
`.pytest-tmp/P3-04A-full-final-20260915` 完整重跑，得到上表最终 1064/0/0/1。未修改 ACL。

## 7. 未实现与未验证

- P3-04B 尚未新增 0012、服务/API、自动晚到 revision gate、真实操作/撤销与派生追赶；本任务没有运行
  数据库/API/打包行为验收。
- 混合 500 face 的规模、跨进程并发、真实家庭图库、前端脸框/待手动归类/最近操作均未测；分别留给
  P3-04B、P3-06/P3-08。
- 模型重检测产生新 face_id 后的人工锚点映射仍属 P3-08；ADR-0007 不以丢失旧锁作为临时方案。
- FACE_CONTROL 全 exclusion 快照在极端大量人工否定下的体积未压测，P3-04B 报告必须测量或明确上界。

## 8. 交接

```text
任务 ID / 状态：P3-04A / PASS（A，契约与文档；零生产/schema/依赖变化）
冻结产物：ADR-0007 + p3-face-correction-contracts.v1.json + 26 个契约用例
关键结论：共享 operation authority；0012 扩闭合 CHECK；FACE_CONTROL 快照；logical revision 0；
  mixed batch 500；排除保留；向前补偿 undo；PERSON_CHANGED + PENDING_REPAIR
下一任务：P3-04B / DS-H（DeepSeek V4.1 Flash / 高推理）实现，随后 C2 独立审查
任务卡：docs/tasks/P3-04B.md
```
