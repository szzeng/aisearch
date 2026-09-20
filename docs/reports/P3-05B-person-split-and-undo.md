# P3-05B — 人物拆分与通用撤销实现（DS-H）

- 状态：**PASS（B + D + C）**
- 日期：2026-09-20
- 实际模型：DS-H（DeepSeek V4.1 Flash / 高推理），由用户在 Codex 中手动选择；未发生自动模型切换
- 起点 HEAD：`7ba4c12`（P3-05A 文档冻结），开始时工作树干净
- 权威决策：[ADR-0008](../decisions/ADR-0008-person-split-operation-contract.md) 与
  [companion](../decisions/contracts/p3-person-split-contracts.v1.json)（本任务**一字未改**）
- 下一任务：[P3-05C](../tasks/P3-05C.md)

## 1. 交付物与边界

新增 `catalog/migrations/0013_person_split_operations.sql`、`persons/split.py`、四个测试文件与共享夹具、
一个 `%TEMP%` 真实 CPU 夹具；加性修改 `persons/{contracts,operations,service}.py`、
`catalog/{errors,schema}.py`、`api/{models,routes,errors}.py`；机械修改迁移集合/schema 断言与地名示例
兼容上限。**`schema_version` 12 → 13**，migration 连续 `0001`～`0013`。

未改：ADR-0001～0008 与既有/新增 companion、`0001`～`0012` 逐字节、`pyproject.toml`、`uv.lock`、
`PhotoSearch.spec`、前端资源、检测/识别权重与阈值、`face_id` 算法、embedding/space/generation、
places/geo/OCR/ingestion/worker/search 排序协议。未实现：人物页与脸框选择器（P3-06）、模型重检测后的
新旧 `face_id` 映射（P3-08）。

## 2. 迁移 0013：第二次扩宽闭合 CHECK，仍不新增事实表

0013 与 0012 同构：只重建 `person_operations`（type 加 `SPLIT`）与 `person_operation_items`（entity_type 加
`SPLIT_GROUP`，并把形状约束改成「只有 `FACE_CONTROL` 带 `entity_key`」，即
`CHECK((entity_type <> 'FACE_CONTROL') = (entity_id IS NOT NULL))`）。别名引用先停放再逐列还原，
`PRAGMA defer_foreign_keys` 只用于同一条 `INSERT ... SELECT` 内的行序；`person_aliases`、
`face_assignment_controls`、`face_exclusions`、`search_projection_queue`（含 `PERSON_CHANGED` 第六 cause）
与 `persons`/`faces`/`face_person_links` 等表一字未改，未新增 queue cause。

真实 v12→v13 夹具（`tests/integration/test_p3_person_split_migration.py`）在含
`RENAME/UNDO/MERGE_BY_NAME/BATCH_FACE_EDIT`、`PERSON_BUNDLE`+`FACE_CONTROL` item、原始/撤销互引用、
3 条 alias `last_operation_id`、1 条 control、1 条 exclusion 的库上证明：

| 检查 | 结果 |
|---|---|
| 连续性 | `migration_files()` = `0001`～`0013`；`0013_person_split_operations.sql` 存在 |
| 历史冻结 | `0001`～`0012` 逐字节未改（本任务文件内比对 12 个哈希互不相同） |
| 无损 | 备份内 `schema_version=12` 且拒绝 `SPLIT`；迁移后 `person_operations`/`person_aliases`/`face_assignment_controls`/`face_exclusions` 逐列不变，`person_operation_items` 八列（含 `entity_key`）逐列不变，`PRAGMA foreign_key_check` 为空，alias 引用仍为 `(1,'op-3'),(2,NULL),(3,'op-1')` |
| 新形状 | `SPLIT`/`SPLIT_GROUP`/`FACE_ASSIGN`/`FACE_CONTROL` 都在 CHECK 文本里；`SPLIT_GROUP` 用整数 id 且不得带 text key；`FACE_CONTROL` 不得带整数 id；`PERSON_BUNDLE.after_revision >= 1` 仍被强制；`MOVE_FACE` 之类未知类型仍被拒 |
| 失败语义 | 损坏 0013 整体回滚（版本仍 12、无残留 `*_backup` 表、行逐列不变）；缺 `person_operation_items` → `SCHEMA_INCONSISTENT`；过新库 `SCHEMA_TOO_NEW` 且文件字节不变 |
| 幂等 | 同一目标重复迁移 `applied=()`、`previous_version=13` |

## 3. 拆分事务与不变量（ADR-0008 §2～§5）

唯一路径 `POST /api/v1/persons/{source_person_id}/split`，`persons/split.py` 在服务层一个
`BEGIN IMMEDIATE` 内按固定顺序执行：

1. `contract_version`/`request_id`/1..500 去重 face/`source_expected_revision >= 1` 的 shape 检查；
2. 请求 digest（contract version + `persons.split` + **请求** source ID + 期望 revision + 按 face 排序的
   三元组）→ 全局 request_id 幂等门；
3. canonicalize source、source revision、每脸 control revision、唯一 `CONFIRMED` link 及其 revision、
   归属、`MANUAL_UNASSIGNED`、对 canonical source 的活动 exclusion，以及「去掉所选 face 后 source 仍至少
   剩一个有效成员」；
4. 写 operation 占位（`target_person_id=NULL`）→ 创建 name/name_key NULL、无 alias、活动、revision 1 的
   新人物并回填 ID；
5. 每个所选 face 走 ADR-0007 `assign` 的**同一份**实现（删除 `MANUAL` link / 把 `AUTO` link 降级为
   `HUMAN_CONFLICT` 建议、写 `MANUAL_ASSIGNED` control、对 canonical source 激活 exclusion、建立
   `MANUAL/CONFIRMED` link），新增的规则只有严格向前的 link revision：新 link 号严格高于该脸当前
   所有 link（例：seed link `3` → 降级建议 `4` → 新人工 link `5`）；
6. source 头像在「被移动脸即原头像」时按同 processor/space + 质量优先重选，否则保留；新人物头像从移动后
   的有效成员同规则选择，**不因此二次增加 revision**；source revision 恰好 +1；
7. items 固定为 source `SPLIT_GROUP`(index 0)、created `SPLIT_GROUP`(index 1)、按 `face_id` 升序的
   `FACE_CONTROL`；source snapshot 只含 role/person 字段/aliases/**有效 CONFIRMED member**（建议不入
   fingerprint）；created before 固定 `{role: CREATED, exists: false, person_id: new_id}`。

拆分没有业务 no-op：非 replay 的成功请求必定创建人物并改变全部所选 face。成功与 replay 返回同一个
`new_person_id`、operation id/revision 与记录时 filter revision；replay 只读取已存 item JSON，**不读
live 状态**。

`PERSON_SPLIT_CONFLICT` 的 `details.reason` 固定在 ADR §3 的六值闭集内：
`face_not_confirmed` / `face_owned_by_other_person` / `assignment_revision` / `face_manual_unassigned` /
`face_state_inconsistent` / `source_would_be_empty`。

## 4. 幂等、revision 与 envelope

| 场景 | 结果 |
|---|---|
| 同 request_id + 同 payload | 在一切 revision/实体检查**之前** replay；operation/person/link/control/exclusion/item/filter/queue/candidate 全零变化（逐行快照比对） |
| 同 request_id + 异 payload | `PERSON_IDEMPOTENCY_CONFLICT`（409）且零写，不回显 payload/hash |
| 被拒请求 | 不占用 request_id（先验证后写 operation 行；注入失败后同 key 可成功） |
| source / control / assignment revision stale | 三个独立用例，分别 `PERSON_REVISION_CONFLICT` / `FACE_ASSIGNMENT_CONFLICT` / `PERSON_SPLIT_CONFLICT(assignment_revision)`，`details` 只含安全 ID 与 expected/current revision |
| 幂等门早于「later revision」与「entity missing」 | 拆分后先改名（source revision 前进）再删除那把脸，仍以原 envelope replay，不报 `FACE_NOT_FOUND` |
| 未知 operation | `PERSON_NOT_FOUND`（沿用 P3-03 语义，404） |

## 5. 补偿撤销与冲突矩阵（ADR-0008 §7）

共享端点 `POST /api/v1/person-operations/{operation_id}/undo` 现在分派 person / face / **split** 三族；
P3-03 与 P3-04 的响应、错误码与 replay 逐字段不变（见 §8 回归与完整套件）。

split family 的门禁顺序：全局幂等门 → 原操作必须是 `COMMITTED`/未撤销/`type=SPLIT`/revision 匹配（否则
`PERSON_OPERATION_NOT_UNDOABLE`，且禁止 undo an UNDO）→ item family/顺序/数量严格校验（缺失或混入同样
拒绝且零写）→ 比较 source/created 的 live `SPLIT_GROUP` 与每个所选 face 的 live `FACE_CONTROL`。
redirect 链损坏时保留 `PERSON_REDIRECT_CYCLE`（409），人物行消失记为具名 `person_missing`。

| 后续事实 | 实测结论 |
|---|---|
| source 改名 / created 改名 | `group_changed`，整批拒绝、零写 |
| source 或 created 被 merge | `group_redirected` |
| 第二次拆分改变 source 组 | `group_changed` |
| 自动关联给 source 增加 `CONFIRMED` 成员（不改 person revision） | `effective_members_changed` |
| 所选 face assign / unassign / control 或 link/exclusion 改变 | `face_control_changed` |
| 所选 face 消失 | `face_missing` |
| 只有 `SUGGESTED` 变化、或不相关人物的改名 | **不冲突**，撤销照常完成，建议原样保留 |
| redirect 环 | `PERSON_REDIRECT_CYCLE`，零写 |

无冲突时按补偿语义执行（不删历史、不倒退 revision）：selected face 恢复原 control/assignment/exclusion
（control 取 live 与记录最大值 +1，link 严格高于 live 同行与记录值）；source 恢复 split 前头像与有效成员并
+1；created **不删除**，清空初始头像、`merged_into` 指向 source、revision live+1；原 `SPLIT` 标 `UNDONE`
并指向新 `UNDO`，新 `UNDO` 保存同样的两个 `SPLIT_GROUP` 与全部 `FACE_CONTROL`。

## 6. 权威→派生顺序、计数与隐私

changed split/undo 在同一 SQLite 事务内：收集所选 face 与其 `DISTINCT asset_id` → `catalog_filter_revision`
**恰好 +1**（cause 复用 `PERSON_CHANGED`）→ 每资产 `search_projection_queue` upsert → 对当前 generation
已发布的所选 face `queue_candidate()` 置 `PENDING_REPAIR` → 写回 operation filter revision。
**不调用**图片 projection refresh（`refresh=None`），不写 `index_outbox`，不改 embedding/space/generation
（测试逐项比对 `face_embeddings`/`face_index_generations`/`index_outbox` 行）。

读取口径沿用现有权威：拆分后多一个未命名活动人物，source 保留原名；人数/照片数仍按有效 `CONFIRMED` 成员
与 `asset_id` 去重；撤销后 created ID 经 ADR-0006 resolver 解析回 source。`face_summary` 字段未改、未新增。

## 7. API、错误与 OpenAPI

新增 `PersonSplitRequest`/`PersonSplitFaceSelection`/`PersonSplitOperationResponse`/`SplitGroupItemResponse`，
全部 `extra="forbid"`；`faces` 为必填数组（与 companion 一致），1..500/去重/空集合由服务端给出冻结错误码。
共享 undo 的响应联合新增 split 分支，P3-03/P3-04 的 OpenAPI shape 未变。

错误闭集与 HTTP：`PERSON_NOT_FOUND` 404、`FACE_NOT_FOUND` 404、`PERSON_REVISION_CONFLICT` /
`FACE_ASSIGNMENT_CONFLICT` / `PERSON_SPLIT_CONFLICT` / `PERSON_IDEMPOTENCY_CONFLICT` /
`PERSON_REDIRECT_CYCLE` / `PERSON_OPERATION_NOT_UNDOABLE` 409、`PERSON_BATCH_TOO_LARGE` /
`VALIDATION_ERROR` 422。session / Host / Origin / double-submit CSRF 由既有中间件覆盖（401/403 实测）。

隐私：新路径与 envelope 的响应/错误文本经 FORBIDDEN_FRAGMENTS 扫描，不含本机路径、`name`/`name_key`、
alias、bbox、landmark、向量、confidence、单对相似度与 `payload_sha256`；OpenAPI 路径文本同样扫描。

## 8. 实测命令与结果

环境事实：本仓库 `.pytest-tmp` 对当前受限令牌不可读（连 `Get-Acl` 都返回 “Attempted to perform an
unauthorized operation”），且 pytest 会以 `mode=0o700` 创建 basetemp/`tmp_path`，在受限令牌下产生
`WinError 5`。按任务卡要求**未修改任何 ACL**，全部 pytest 改由已授权的正常 Windows 权限路由执行，basename
仍使用任务卡指定的 `.pytest-tmp/P3-05B-*`。

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-05B-full2 --junitxml=test-results/p3-05b-full.xml` | exit 0；JUnit `tests=1182 failures=0 errors=0 skipped=1`，374.5 s |
| `uv run --no-sync python -m pytest -q … tests/unit/test_p3_person_split_unit.py tests/integration/test_p3_person_split_{lifecycle,undo,api,migration}.py --basetemp=.pytest-tmp/P3-05B-focused --junitxml=test-results/p3-05b-focused.xml` | exit 0；`tests=44 failures=0 errors=0 skipped=0`，2.72 s |
| P3-03/P3-04/P2-03B/P3-05 回归批（21 个文件，`--junitxml=test-results/p3-05b-regression2.xml`） | exit 0；`tests=231 failures=0`，22.9 s |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（`Resolved 95 packages`，依赖未变） |
| 本任务新增用例 | 44 例（unit 7、lifecycle 10、undo 15、api 4、migration 8）；另有 `SUPPORTED_SCHEMA_VERSION`、两处版本钉扎与 `MOVE_FACE` 类型替换、地名示例兼容上限 12 → 13 等机械修改 |

第一次完整套件运行（`--junitxml=test-results/p3-05b-full.xml` 覆写前）出现 1 例**与本任务无关**的失败：
`tests/unit/test_p2_search_rerank.py::test_scored_rows_are_deterministic_candidate_scores_only` 断言两次
连续调用的完整 envelope 相等，其中含实测 `elapsed_ms`（机器连续满载时得到 `15` vs `0`）。该断言与
P2-06B 精排代码本次均未改动、也不在允许修改范围内，因此未改动它们；随后完整重跑同一命令 exit 0。

## 9. 真实 CPU 夹具（`%TEMP%`，公共领域/合成图）

`tools/diagnostics/person_split_smoke_fixture.py --device cpu` exit 0、`status=ok`、`problems=[]`：
真实检测 InsightFace 自带合照 → 真实保守关联 → 真实 `merge` 把两个检测分组并成一个 → 真实 `rename`
命名 → 真实 `split` 拆出一把脸 → **关闭并重新打开 catalog（进程重启语义）** 复核 → 真实 `undo` →
再次重启复核。关键证据：`created_unnamed=true`、`created_members` 只有所选 face、
`source_members` 正好少一把、`target_mode=MANUAL_ASSIGNED`、`target_exclusions=[source]`、
`filter_delta=1`、`queue_cause=PERSON_CHANGED`、`image_outbox_delta=0`、`siblings_unchanged=true`；
撤销后 `created_redirects_to_source=true`、`created_avatar_cleared=true`、
`source_members_restored=true`、`target_mode=AUTO_ALLOWED`、`target_exclusions=[]`、
原操作 `state=UNDONE`（重启后仍成立）。摘要只输出 ID/模式/revision/计数，不含姓名、路径、坐标或向量。

## 10. C 级打包核对

| 检查 | 结果 |
|---|---|
| `uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | exit 0，真实 onedir 重建（`Build complete!`） |
| bundle migration | `dist/PhotoSearch/_internal/photo_search/catalog/migrations` 内含 `0001`～`0013` 连续 13 个 `.sql` |
| 源码/bundle hash | `0013_person_split_operations.sql` 两侧 SHA-256 均为 `9DEA2D67726A61A958AF57CCE7DFBEA6A433DC7679C406320F2E5DD2E1CCBDE4`（byte-identical） |
| 全新目录 `packaged_api_smoke.py --work-dir "D:\PhotoSearchSmoke\P3-05B 中文 空格"` | exit 0、`status=ok`、`problems=[]`、`startup_seconds=1.8` |
| split route smoke | `POST /api/v1/persons/999999/split` → `404 PERSON_NOT_FOUND`；无 session → `401`；响应无本机路径 |
| 打包 data root schema | `packaged_person_schema = {schema_version: 13, has_split: true, has_split_group: true, has_face_assign: true, has_face_control: true}` |
| 既有 C 级项 | 前端/places/search/upload/thumbnail/offline/coverage 全部沿用并通过；`coverage_*` 与 `thumbnail` 无回归 |

## 11. D 级具名故障注入（全部零写）

1. 事务中途失败：`publish_derived` 抛错（派生阶段）与 `_best_member_face` 抛错（人物/头像阶段）两次注入，
   person/operation/item/link/control/exclusion/filter/queue/candidate 逐行回到注入前；被回滚的 request_id
   仍可成功使用。
2. 幂等门先于 later revision 与 entity missing（改名 + 删除所选脸后仍 replay）。
3. source/control/assignment 三种 stale 各自独立零写。
4. suggestion / 无归属 / 跨人物 / `MANUAL_UNASSIGNED` / 对 source 的活动排除 / 拆空 source / 缺失脸 /
   500 上限 / 空集合 / 重复脸 全部具名拒绝且行快照不变。
5. `AUTO` 与 `MANUAL/CONFIRMED` 两种输入都变成新人物的人工确认；其他 exclusion 原样保留。
6. source/created 头像、名字、alias、同图未选 sibling 边界；created 不复制名字/alias。
7. rename / merge / face correction / 第二次 split / 新增 `CONFIRMED` 成员分别阻止旧 undo；
   suggestion-only 与不相关改动不阻止。
8. 撤销后 revision 只向前、created redirect、重启可读、旧 created ID 可解析。
9. 图片 `index_outbox`、embedding、space、generation 前后逐项不变。
10. P3-03/P3-04 operation family 的首次/重放/撤销 envelope 回归（231 例回归批 + 完整 1182 例）。

## 12. 未验证项（不阻塞本 PASS，但必须如实记录）

- **真实家庭照片库与真实大规模拆分**未测：夹具是单个 public-domain 合照；500 face 的批量延迟与超大人物
  snapshot 体积只在小夹具验证公式。
- **`source_would_be_empty` 之外的产品语义**（例如「拆分后立刻再拆分新人物」）未做产品侧入口，属 P3-06。
- **会话/进程级并发**：本任务的零写与幂等证据都在单进程内；多进程并发拆分同一 source 未测。
- **旧检测 `face_id` 映射**：模型重跑后的新旧锚点仍属 P3-08，本任务明确不碰。
- **`.pytest-tmp` ACL** 环境事实未修复（按 AGENTS/任务卡不得自动改 ACL）；所有 pytest 均由正常 Windows
  权限路由执行。
- **P2-06B 精排计时断言偶发**：见 §8，未修（不属于本任务范围）。
- 浏览器/DOM 自动化、人物页与选择器、最近操作列表属 P3-06；本任务无 UI 改动。

## 13. 交接记录

```text
任务 ID / 状态：P3-05B / PASS（B + D + C）
起点 HEAD：7ba4c12（P3-05A）；schema_version 12 → 13
新增：0013_person_split_operations.sql、persons/split.py、4 个测试文件 + tests/p3_split_fixtures.py、
  tests/unit/test_p3_person_split_unit.py、tools/diagnostics/person_split_smoke_fixture.py
加性：persons/{contracts,operations,service}.py、catalog/{errors,schema}.py、api/{models,routes,errors}.py、
  tools/diagnostics/packaged_api_smoke.py
核心结论：唯一 POST /persons/{id}/split；source/control/link 三 revision；新人物未命名无 alias；
  所选脸 MANUAL_ASSIGNED + source exclusion；SPLIT_GROUP+FACE_CONTROL 固定顺序；全局 request_id；
  created 撤销后 redirect；相关后续编辑整批冲突；一次 PERSON_CHANGED + PENDING_REPAIR；
  不写图片向量、不改 face_id/embedding/generation
验证：B 1182 例 0 失败（1 既有 skip）+ 专项 44 例 + 回归 231 例 + D 级具名注入 + 真实 CPU 夹具
  （真实检测/合并/拆分/重启/撤销/重启）+ C 级 onedir 重建与打包 smoke（problems=[]，schema 13）
下一任务：P3-05C / C2（GPT-5.6 Sol / high）独立审查
任务卡：docs/tasks/P3-05C.md
```

**下一任务建议使用 C2（GPT-5.6 Sol / high），请在 Codex 中手动切换模型/推理档后继续。**
