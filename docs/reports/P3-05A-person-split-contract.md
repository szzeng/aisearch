# P3-05A — 人物拆分、通用撤销与 revision 冲突契约冻结

- 状态：**PASS（A 级契约/文档闭环；零生产/schema/依赖变化）**
- 日期：2026-09-20
- 实际模型：C2（GPT-5.6 Sol / high），由用户在 Codex 中手动选择；未发生自动模型切换
- 起点 HEAD：`4e1dbaa`，开始时工作树干净
- 权威决策：[ADR-0008](../decisions/ADR-0008-person-split-operation-contract.md)
- 机器伴随物：[p3-person-split-contracts.v1.json](../decisions/contracts/p3-person-split-contracts.v1.json)
- 下一任务：[P3-05B](../tasks/P3-05B.md)

## 1. 交付与阶段边界

本任务只冻结 P3-05 的高风险语义，新增 ADR-0008、draft-2020-12 companion、22 个只读契约反例测试、
本报告与 P3-05B 执行卡，并更新状态。没有修改 `src/photo_search/**`、migration、schema_version、依赖、
旧 ADR/contract、打包配置、前端或诊断夹具；因此 PASS 只说明协议与机器伴随物自洽，不声称 split API、
数据库迁移、事务、重启或打包行为已经实现。

`schema_version` 保持 **12**，`0001`～`0012` 未改。P3-05B 才能实现 0013、服务/API 和 B + D + C 验收。
新旧检测 `face_id` 映射仍属于 P3-08；本任务明确禁止复用、改写或重新生成 face_id。

## 2. 核心冻结结论

### 2.1 拆分对象与身份

- 唯一路径：`POST /api/v1/persons/{source_person_id}/split`。
- 最小输入：requested source ID、source expected revision，以及 1..500 个明确且去重的 face；每脸同时带
  logical control revision 和当前 CONFIRMED assignment revision。
- source 按 ADR-0006 resolver canonicalize；旧 ID 可提交，响应同时返回 requested/canonical ID。
- 仅唯一 CONFIRMED、属于 canonical source、非 MANUAL_UNASSIGNED、无 source exclusion 的脸可拆；AUTO 与
  MANUAL confirmed 都可作为输入。
- empty/duplicate/501、suggestion、无归属、跨人物、消失脸、拆空 source 都有闭合拒绝；split 不存在业务
  no-op。
- 新人物在全量预检后于同一事务创建：未命名、无 alias、活动、revision 1；不复制 source 名字/别名，
  不会触发同名自动合并。失败时人物 ID 与 operation 一起回滚。

### 2.2 人工事实与人物属性

- 所选脸全部成为新人物的 `MANUAL_ASSIGNED + MANUAL/CONFIRMED`，logical revision 向前；激活对 source 的
  exclusion，其他 exclusions 原样保留。
- AUTO source link 可降级为 suggestion，MANUAL source link 可删除；检测、embedding 与 face_id 不动。
- source 保留 name/name_key/aliases，revision +1；头像未被移动则保留，被移动则只从剩余同 space 有效成员
  重选。新人物头像只从移动后的同 space 有效成员选，初始 revision 不因此二次增加。
- 同图未选 sibling、其他人物、图片与人数/照片按 asset_id 去重的口径不变。

### 2.3 为什么需要 `SPLIT_GROUP`

现有 `PERSON_BUNDLE` 包含全部 suggestion link，且假设 person 在 before/after 都存在；它无法同时表达：

1. 新人物在事务前不存在；
2. undo 不能删除稳定 ID，而要把 created ID redirect 回 source；
3. suggestion-only 变化不应阻止无歧义 undo；
4. 新 CONFIRMED member 必须阻止旧 undo。

因此冻结新 item type `SPLIT_GROUP`，而不是扭曲旧 `PERSON_BUNDLE`。它只记录 role、人物必要字段、aliases 与
有效 CONFIRMED member fingerprint。所选脸继续复用 ADR-0007 的完整 `FACE_CONTROL`。固定 item 顺序为：
source group、created group、按 face_id 排序的 face control。P3-05B 的 0013 只扩闭合 CHECK，不加事实表。

### 2.4 幂等与响应

- operation type 唯一新增 `SPLIT`；全局 request_id、operation_id 算法与 operation 表继续复用。
- digest 固定包含 contract version、`persons.split`、requested source/revision 和排序后的 face/control/link
  revision；不使用会漂移的 canonical source ID。
- 同 key 同 digest 在实体/revision 检查前 replay；异 digest 为 `PERSON_IDEMPOTENCY_CONFLICT`，被拒请求不
  占 key。
- split、replay、split-family UNDO 使用同一个 `p3-person-split-contracts.v1` closed envelope，含 source/new
  ID、两个 group item 与 1..500 个 face item；内部 snapshot 和个人媒体事实不出 API。

## 3. 补偿撤销与冲突矩阵

撤销仍走共享 `/api/v1/person-operations/{operation_id}/undo`，新 `UNDO` 向前推进版本，不允许 undo an
UNDO。先做全局幂等门，再校验 SPLIT 状态/revision 和全部 after snapshot；任一冲突整批零写。

| split 后发生的事实 | 冻结结论 |
|---|---|
| source/created rename、alias、avatar、revision 变化 | `group_changed`，拒绝 |
| source/created merge、redirect 或消失 | `group_redirected` / `person_missing`，拒绝 |
| selected face assign/unassign/restore_auto 或 control/link/exclusion 变化 | `face_control_changed`，拒绝 |
| selected face 消失 | `face_missing`，拒绝 |
| 第二次 split 改变相关 group/face | 对应 group/face 冲突，拒绝 |
| source/created 新增/移除 CONFIRMED member | `effective_members_changed`，拒绝 |
| 只有 SUGGESTED 变化 | 不冲突，保留 suggestion |
| 不相关人物/face 变化 | 不冲突 |

无冲突时，selected faces 恢复 split 前 control/effective assignment/exclusion 语义，control/link revision 从
live 与记录最大值继续前进；source 恢复成员/头像并 +1；created 不删除，而是清空初始头像并 redirect 到
source、revision +1。该方案保留稳定 ID，又不会覆盖后续人工整理。

## 4. ADR-0006 / ADR-0007 兼容矩阵

| 既有冻结面 | P3-05A 结论 |
|---|---|
| ADR-0006 canonical resolver / 旧 ID | 原样复用；split source 与 undo 后 created ID 都走同一 resolver |
| ADR-0006 全局 request_id / operation_id | 原样复用；SPLIT 与既有 family 共享唯一命名空间 |
| ADR-0006 PERSON_BUNDLE | 不改旧形状/读写；新增 SPLIT_GROUP 避免改变既有 undo 比较 |
| ADR-0006 rename/merge/undo envelope | 原样保留；共享 undo dispatcher 增加 split family，不改变 person family |
| ADR-0007 FACE_CONTROL | 原样复用 selected face 的完整 before/after 与向前恢复语义 |
| ADR-0007 assign/exclusion 矩阵 | split 对新人物执行同一 assign 规则，并激活 source exclusion |
| ADR-0007 face family envelope/error | 一字未改；split 使用独立 companion 与 person-family错误 |
| operation item 表 | 0013 仅扩闭合 CHECK；P3-03/P3-04 历史行逐列保留 |

现有三个 P3-03/P3-04 contract 测试文件 64/64 通过，证明旧 companion、operation type、control mode、错误
闭集与实现常量没有因新文档发生漂移。

## 5. API、错误、隐私与读取

companion 对 request、共享 undo request、success/replay/undo operation 与 error 全部使用
`additionalProperties: false`。唯一新增业务错误 `PERSON_SPLIT_CONFLICT` 映射 409；source/control stale
分别沿用 `PERSON_REVISION_CONFLICT` / `FACE_ASSIGNMENT_CONFLICT`，link/资格/剩余成员冲突由固定 reason
闭集区分。empty/duplicate 为 `VALIDATION_ERROR`，501 为 `PERSON_BATCH_TOO_LARGE`。

响应/错误/日志不得含路径、姓名/name_key/alias、bbox、landmark、向量、confidence、单对相似度、原始
payload/hash 或 before/after snapshot。现有 session、Host/Origin、double-submit CSRF 边界保持。

人物列表/详情继续使用当前权威口径：split 后增加一个 unnamed active person；undo 后 created ID 解析回
source。现有 face_summary 字段足以自然反映 named/unnamed/manual/exclusion/recent operation，不新增计数。

## 6. 权威到派生顺序

split/undo 在同一 SQLite 事务写 operation、人物、脸级权威事实和 items；仅对所选 face 的 assets：

1. `catalog_filter_revision` 恰好 +1，cause `PERSON_CHANGED`；
2. `search_projection_queue` upsert；
3. 已发布 face candidate 进入 `PENDING_REPAIR`；
4. 需要时失效 source/created 人物计数/头像 cache；
5. 写回 operation filter revision并提交。

不做文件 I/O/模型/Lance，禁止图片重编码、图片向量 `index_outbox`、face embedding/space/generation 变化。
replay 零派生副作用；split 没有 no-op。

## 7. 契约反例覆盖

`tests/unit/test_p3_person_split_contract.py` 共 22 例，实际执行 companion 的 schema/ref/oneOf/allOf/type/
required/closed-object/bound/pattern 子集，并额外验证 JSON Schema 不能表达的规则：

- face ID 去重，1..500，control 允许 0 而 assignment revision 从 1 开始；
- operation 固定两个 group role/ID，face item 唯一且排序；
- SPLIT 的所有 face 必为 MANUAL_ASSIGNED 且指向同一个 new person；
- UNDO 使用同一 envelope 并允许 created group redirect；
- 私密字段不能进入 group/face item；
- error code/reason 闭集；
- ADR 中的唯一 API、幂等门顺序、资格、SPLIT_GROUP、冲突矩阵、revision-forward、派生顺序、零实现声明；
- P3-04 companion operation vocabulary 保持逐项不变。

测试直接读取 ADR 与 companion 并断言冻结值，不是只检查文件存在，也没有复制 production 常量自证。

## 8. 实测命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit/test_p3_person_split_contract.py --basetemp=.pytest-tmp/P3-05A-contract --junitxml=test-results/p3-05a-contract.xml` | exit 0；22 passed；初次 JUnit 0.089 s |
| companion error details 闭集补强后用新 basetemp 复跑同一测试文件 | exit 0；22 passed；最终 JUnit `tests=22 failures=0 errors=0 skipped=0`，0.092 s |
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit/test_p3_person_contract.py tests/unit/test_p3_face_correction_contracts.py tests/unit/test_p3_face_correction_contract.py --basetemp=.pytest-tmp/P3-05A-regression --junitxml=test-results/p3-05a-existing-contracts.xml` | exit 0；64 passed；JUnit `tests=64 failures=0 errors=0 skipped=0`，0.120 s |
| `uv run --no-sync ruff check .` | exit 0；`All checks passed!` |
| `git diff --check` | exit 0；clean |
| `uv run --no-sync uv lock --check` | exit 0；依赖锁未变化 |

uv cache 使用项目内 `.uv-cache`，pytest 临时目录使用项目内 `.pytest-tmp`。本次未遇 ACL 问题，未修改 ACL。

## 9. 未实现与未验证

- 0013、split service/API、真实 SQLite transaction/rollback/restart、实际 operation replay/undo 尚未实现；
- v12→v13 无损迁移、损坏 migration、缺件/过新库、跨进程竞争尚未执行；
- 500 face 批量延迟、超大人物 snapshot 体积、真实家庭图库、真实多人合照未测；
- 人物页、脸框选择、最近操作与浏览器交互属于 P3-06；
- 模型重检测产生新 face_id 后的锚点映射属于 P3-08；
- 真实 onedir 与 bundle migration/hash 尚未验证，留给改生产包的 P3-05B。

以上不阻塞 P3-05A 的文档/契约 PASS，但 P3-05B 未实际完成前不得声称人物拆分可用。

## 10. 交接

```text
任务 ID / 状态：P3-05A / PASS（A，契约与文档；零生产/schema/依赖变化）
冻结产物：ADR-0008 + p3-person-split-contracts.v1.json + 22 个契约用例
核心结论：唯一 POST /persons/{source}/split；source/control/link 三 revision；新人物未命名；
  selected face MANUAL_ASSIGNED + source exclusion；SPLIT_GROUP + FACE_CONTROL；全局 request_id；
  created ID 撤销后 redirect；相关后续编辑整批冲突；PERSON_CHANGED 一次 + PENDING_REPAIR；
  不写图片向量，不改 face_id/embedding/generation
兼容证据：既有 P3-03/P3-04 contract tests 64/64 通过
下一任务：P3-05B / DS-H（DeepSeek V4.1 Flash / 高推理）实现 B + D + C
任务卡：docs/tasks/P3-05B.md
```

**下一任务建议使用 DS-H（DeepSeek V4.1 Flash / 高推理），请在 Codex 中手动切换模型/推理档后继续。**
