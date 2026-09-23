# P4-01C — C2 / Sol high 聚焦终审报告

**现行结论：`SOL SHORT RE-REVIEW PASS`，P4-01C PASS（修复提交 `479f397`）；见 §8。** 下文 §1～7 保留前两轮 FAIL 的原始反例与裁决记录。

**结论：`SOL FOCUSED REVIEW FAIL`。** 被审提交 `48cca93`，父提交 `cd84bcb`。本次仅审 P4-01C 任务卡规定的 §11 幂等与提交顺序、检索参数、谓词等价，以及 DS-H 主审的 R3/R5/R6 和未验证项；未复做全量工程主审。未改生产代码、迁移、契约、错误码或打包面，未 push，未生成 P4-02。

## 1. 读取与验证范围

按交接顺序读取 `AGENTS.md`、`docs/development-status.md` 末尾 P4-01C 两节、DS-H 主审报告、P4-01C 实现报告与任务卡；随后查看 `git show 48cca93 --stat` 及 `retrieval.py` / `store.py` 的提交差分，阅读 ADR-0009 §3.1～3.3/§5.1、ADR-0005 §10、V3.1 §11，以及指定的受影响测试。`git log -2 --oneline` 确认 HEAD 为 `48cca93`，父提交为 `cd84bcb`。

聚焦测试使用项目本地 `.uv-cache` 和 `.pytest-tmp/p401c-sol-focused/run`：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/integration/test_p4_01c_retrieval_blocks.py `
  tests/integration/test_p2_index_submission.py `
  tests/integration/test_p2_acceptance_recovery.py `
  tests/unit/test_p3_person_search_scope.py `
  --basetemp=.pytest-tmp/p401c-sol-focused/run `
  --junitxml=test-results/p4-01c-sol-focused.xml
```

JUnit：**39 tests / 0 failures / 0 errors / 0 skips**，exit 0。首次受限令牌无法创建 `.pytest-tmp` 子目录，出现 pytest setup `WinError 5`；按正常 Windows 权限路由重跑后通过，未修改 ACL。DS-H 已独立完成全量 **1644 / 0 / 0 / 48**，本终审未重跑全量。独立最小探针均使用项目内临时合成夹具，结束后清理；`git status --short` 未列出受控文件改动（仍有既有不可枚举 pytest 临时目录警告）。

## 2. A — outbox 幂等与提交顺序：FAIL

### 成立的顺序执行边界

`process_batch` 一次写事务领取 `PENDING`/`IN_FLIGHT` 事件，置 `IN_FLIGHT`、`attempts+1`、清 `last_error_code`；按 generation 批写 Lance；再以一个 SQLite 写事务逐事件确认。确认事务内调用 `_make_generation_ready`，它先数该 generation 的未确认 outbox。superseded 事件只确认不写；批路径比逐事件路径多一次 READY 收敛机会，但在顺序执行下，未确认事件仍会阻止 READY。`after_lance` 仍位于 Lance 写后、SQLite 确认前；专项测试的重放维持唯一逻辑行。

额外在真实两事件合成夹具中让 `_make_generation_ready` 抛 `RuntimeError`：`process_batch` 报 `INDEX_SUBMISSION_FAILED`，输出 `{'PENDING': 2}`、两条 `index_entries` 仍为 `PENDING_REPAIR`；恢复后重放输出 `{'CONFIRMED': 2}`，Lance 行数仍为 2。说明**无并发**时确认阶段的 SQLite 回滚与重放有效。

### 阻断反例 A1：另一个协调器重领批内事件

`process_next` 和 `process_batch` 都从 `state IN ('PENDING', 'IN_FLIGHT')` 领取，且没有 claim token。以同一真实 catalog 的两条待处理事件和两个 `IndexCoordinator` 交错执行：

1. 甲的 `process_batch(max_events=2)` 领取两条并离开 SQLite 领取事务。
2. 在甲的 `upsert_batch` 调用点，乙执行一次 `process_next()`，重领并确认其中一条 `IN_FLIGHT` 事件；实测 `second_processed True`。
3. 甲写完 Lance，确认时发现该事件已非 `IN_FLIGHT`，报 `CatalogError(INDEX_SUBMISSION_FAILED)`。异常回退只更新仍为 `IN_FLIGHT` 的行。

探针最终输出：

```text
first_failure CatalogError INDEX_SUBMISSION_FAILED
outbox [(1, 'PENDING', 1), (2, 'CONFIRMED', 2)]
entries [(1, 'PENDING_REPAIR'), (2, 'CONFIRMED')]
```

因此“批失败则整批回 `PENDING` / 批内不得部分确认”对可交错的两个协调器不成立。`process_next` 允许重领 `IN_FLIGHT` 是既有协议行为；批路径把它扩展为一批内混合确认的可观测状态。若架构本来要求全局单维护者，需要 DS-H 找到并证明该排他保证，把适用前提写清；当前 `src/` 尚无该协调器的生产维护入口，不能以未接线代替并发语义证明。

**受影响边界与测试：** `src/photo_search/indexing/store.py` 的 `process_batch` 领取、确认及异常回退，及 `process_next` 的交错领取；应在 `tests/integration/test_p4_01c_retrieval_blocks.py` 增加确定性交错注入，并以 `tests/integration/test_p2_index_submission.py` 的逐事件重放协议作直接回归。此处不代 DS-H 选择实现修法。

同一 `(space, asset, generation, revision)` 的 `UPSERT_VECTOR` 与 `DELETE_VECTOR` 在 schema 中因 `event_type` 不同可共存；目前 `src/` 无 `DELETE_VECTOR` 生产者，故未把这一前瞻情形算成本卡现存阻断。新增生产者前必须按 `record_id` 去重并增加用例（R6）。

## 3. B — 块大小、精确 top-k 与标量索引：FAIL

### 阻断反例 B1：完全并列距离改变 asset_id 序列

“全局 top-k 属于各块 top-k 并集”只有在**每块与最终合并使用同一确定性全序**时才能保证逐条序列。当前 `LanceVectorIndex.search()` 先让 Lance 执行 `.limit(top_k)`，取得行后才在 Python 按 `(distance, asset_id)` 排序；被引擎截掉的并列项不会再进入合并。

最小实际块大小探针：在项目内临时 Lance 表倒序写入 `asset_id=5001..1`，每条向量均为 `[1.0, 0.0]`，查询也为 `[1.0, 0.0]`，`top_k=2`。用合成权威候选流调用**实际** `retrieval.vector_channel`，只把 `scope_condition` 钉为无筛选：

```text
block_size=1000: hits=[(999, 0.0), (1000, 0.0)], blocks=6, truncated=False
block_size=5000: hits=[(4999, 0.0), (5000, 0.0)], blocks=2, truncated=False
```

同一表、同一查询、无 ANN、无过滤、无截断，仅块大小从旧默认 1,000 改为新默认 5,000，返回的 `asset_id` 序列不同。完全并列时由 Lance 先截断是既有设计缺口，但本卡改变块大小后实际触发任务卡不变量 1 的前后不一致。现有 600 行非并列专项用例与主审 30 万合成向量对照均不能覆盖它。

**受影响边界与测试：** `src/photo_search/indexing/store.py` 的引擎 `limit`、`src/photo_search/search/retrieval.py` 的块合并；在 `tests/integration/test_p4_01c_retrieval_blocks.py` 增加 1,000/5,000 块、完全并列、`top_k=2` 的真实 Lance 回归，并复核是否需要在引擎截断前定义并执行总排序。不要仅在截断后排序。

`MAX_VECTOR_BLOCKS=1000` 本身未改，仍会报告 `truncated`；因每块容量增大，约 100 万与约 500 万候选时两版本的覆盖范围会不同。30 万验收档位不触发上限。冷、热读路径未新增缓存或跨请求状态。

### 阻断反例 B2：索引元数据拒绝未被报告为 unavailable

`ensure_scalar_index()` 仅将 `create_index()` 放入 `try`，`table.list_indices()` 在其外。对一个真实临时 Lance 表注入 `list_indices()` 抛 `RuntimeError('index metadata unavailable')`，函数直接抛 `RuntimeError`，没有返回声称的四态之一；`process_all()` 会在处理 outbox 前中断。读查询仍可不依赖标量索引正确执行，`has()` 的 `count_rows(filter=...)` 被拒后也有全量扫描回退，但维护入口的“拒绝即报告、不得失败”没有覆盖此种 unavailable 路径。

正常路径专项测试确认：无表 `no_table`；成功创建后 `created → present`；`create_index()` 被拒为 `unavailable`；`has()` 过滤器被拒仍正确。`no_table` 会在每轮 `process_all()` 重新检查，但不重复创建，也不影响结果。

**受影响边界与测试：** `src/photo_search/indexing/store.py:ensure_scalar_index` / `process_all`；在现有 `test_ensure_scalar_index_reports_every_state` 增加 `list_indices` 拒绝注入和 outbox 维护结果断言。

## 4. C — SQL 谓词等价：PASS

- `source EXISTS` → `asset_id IN (SELECT ...)`：`file_locations.asset_id` 为 NULL 时两者均不命中；重复位置不改变资产集合。`availability='missing'` 仍为 `NOT EXISTS`，没有引入 `NOT IN` 的 NULL 陷阱。
- `person any/all` 的每个子查询仍复用 `persons/members.py` 的唯一 `MEMBER_PREDICATE`。专项测试覆盖 `MANUAL_UNASSIGNED`、活动排除、`SUGGESTED`、多人 all/any；`faces.asset_id NOT NULL`。canonical / 合并旧 ID 仍由 `search/service.py` 的解析器在查询前处理，检索层只接收 canonical ID。
- `scope_condition` 参数顺序仍为 source → availability → person → capture → place；人物与来源、地点、时间仍为 AND。ADR-0009 §3.1～3.3/§5.1 与 ADR-0005 §10 的照片级权威口径未变。
- 专项用例通过；DS-H 的基线真代码对照记录 half 真数据分页摘要相同，四档独立集合计数与生产 SQL 一致。thin 真数据的前后全集等价仍属 §8 未验证项。

## 5. D — 主审未决项与最终口径

- **R3：接受**“语义等价，下一次触碰 ADR-0009/检索协议时补注释”。本卡禁止改 ADR；无需为 SQL 形状独立阻断。
- **R5：接受，列为 P4-02 正式容量验证的前置项**：“语义 outbox 的生产维护入口”必须有可验证接线。`src/` 中目前没有 `IndexCoordinator.process_all/process_next` 的生产调用点；`face` 索引的同名调用不是该语义协调器。本次 A1 又要求 DS-H 明确入口的单工作者或排他领取边界。未接线前不可把维护侧标量索引的约 3.5～4 s 成绩当真实运行承诺。
- **R6：接受**。新增 `DELETE_VECTOR` 生产者之前，按 `record_id` 去重并加同键 UPSERT+DELETE 用例；当前不是已有生产路径。

DS-H §8 的未验证项均**接受其诚实标注，但不接受将其算作已通过**：真模型向量效果未测；ANN §12.3 的 0.95 未达/未验证；§12.2 API P95 ≤ 1 s 未达（30 万无索引约 19.5 s、有标量索引约 3.5～4 s）；A3 未实现；多世代混批未专项注入；GTX 1660 实机受阻；thin 真数据集合等价仅有间接证据。主审未重复完整 30 万 `--mode scale`，已记录其独立对照范围；本终审亦不重复。

## 6. 裁决与交回

**`SOL FOCUSED REVIEW FAIL`。** 至少 A1、B1 已给出真实最小反例；B2 是额外的维护失败边界。依 V3.1 §11 把这些反例和直接受影响测试交回 DS-H 做最小修复，再由 C2 只复审失败边界及直接回归。本会话不接管实现。P4-01C 不能落为 PASS，P4-02 不能生成；`docs/development-status.md` 的既有 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING` 是终审前状态，应由后续收口流程根据本报告更新。

## 7. 对 DS-H 修复提交 `a457cba` 的 Sol 短复审（2026-09-23）

**结论：`SOL SHORT RE-REVIEW FAIL`。** 本节只复审 §2～3 的失败边界及其直接回归；原 §4 的谓词等价 PASS 与 §5 的 R3/R5/R6、未验证项口径不变。被审修复是 `a457cba`（父提交 `48cca93`），工作树无生产文件改动。DS-H 记录的全量 1647 / 0 / 0 / 48 与 30 万重测作为其工程证据引用，本短复审未重复。

### 7.1 B1/B2：原反例已收口

- **B1 PASS**：`search_candidates` 让 Lance 返回至多 `len(ids)` 行，并在 Python 的 `(distance, asset_id)` 排序之后才取请求的 `top_k`；`CANDIDATE_ROW_COLUMNS` 排除了向量列。用本报告原来的 5,001 行全并列、倒序写入探针调用实际 `vector_channel`：块 1,000 与 5,000 都返回 `[1, 2]`，分别遍历 6/2 块，均 `truncated=False`。新专项用例也覆盖块 5/8/1000/5000 的并列排序。
- **B2 PASS**：`list_indices()` 已纳入索引维护的异常保护，元数据目录被拒时报告 `unavailable`；新增用例验证此时 `process_all` 仍把 outbox 排空。缺表 `no_table` 与成功 `created → present` 的既有口径不变。

同一受影响测试命令（见 §1，`--basetemp=.pytest-tmp/p401c-sol-focused/rereview`、`--junitxml=test-results/p4-01c-sol-rereview.xml`）实跑 **42 / 0 failures / 0 errors / 0 skips**，exit 0。绿灯覆盖原 A1 的“两个写者处理同一 revision”，但不覆盖下述“较新 revision 先落盘、旧写者后落盘”。

### 7.2 A1：修复引入可观测的旧 revision 回写，仍为阻断

修复后的 `process_batch` 在预处理时仅检查一次 `_is_superseded(event)`，随后到 Lance 批写之间没有保护。确认阶段遇到同一事件已 `CONFIRMED` 就跳过，但此时**本批可能已经把较旧 revision 写回 Lance**。用项目内两资产真实 catalog/Lance 夹具和真实 `publish_embedding` 生产路径确定性交错：

1. 提升 `catalog_filter_revision` 至 1，为资产 1 发布旧 artifact，形成 revision 1 事件。甲 `process_batch(max_events=1)` 领取事件、通过 `_is_superseded`、算好 revision 1 的记录。
2. 在甲的 `upsert_batch` 入口先暂停甲：再提升 revision 至 2，并用 `publish_embedding` 发布新 artifact。乙 `process_batch(max_events=2)` 重领旧事件并处理新事件，跳过旧写入、写 revision 2、确认两条。
3. 甲恢复，执行先前已经准备好的 revision 1 `upsert_batch`。确认时见旧事件为 `CONFIRMED`，按新逻辑跳过并成功返回。

实际输出：

```text
second_applied 2
first_applied 1
outbox [(1, 'CONFIRMED'), (2, 'CONFIRMED')]
entry revision=2, state=CONFIRMED
lance projection_revision=1
semantic_ready=True
```

也就是说，SQLite 宣称 revision 2 已确认且 `semantic_ready=True`，实际向量行却被甲回退到 revision 1。`vector_channel` 的候选遍历只看 `index_entries.state='CONFIRMED'`，因此可发布这条旧向量行。这直接违反任务卡不变量 2/3、P2-03 的“旧投影事件不得回滚最新状态”，且比原始“批内假失败”更严重。根因在 `src/photo_search/indexing/store.py` 的 `_is_superseded`（批写前单次检查）、`upsert_batch` 和“已确认即跳过”的确认分支之间；当前新并发测试仅固定同一 revision 的重领。

**交回 DS-H 的最小受影响测试：**在 `tests/integration/test_p4_01c_retrieval_blocks.py` 中把上述两 revision 交错加入 A1 的直接回归，断言最终 `index_entries.confirmation_revision == Lance.projection_revision == 2`、outbox 均确认且 `semantic_ready` 只对应最新派生行；同时保持 `tests/integration/test_p2_index_submission.py::test_late_old_projection_event_reads_latest_state_and_cannot_rollback` 的逐事件语义。仅在确认阶段重查 SQLite 状态不足以阻止已发生的旧 Lance 写；修法需覆盖“旧写者最后落盘”的顺序。本终审不接管修复。

**状态：P4-01C 仍不能标 PASS；不得生成 P4-02。** 依 V3.1 §11 再交回 DS-H 处理此 A1 反例，修复后的 Sol 短审只复核该交错及直接回归。R5 的生产维护入口和单/多维护者口径仍须列为 P4-02 前置项。

## 8. 对 DS-H 第二轮修复提交 `479f397` 的 Sol 短复审（2026-09-23）

**结论：`SOL SHORT RE-REVIEW PASS`，可落库为 `P4-01C PASS`。** 本轮只复审 §7.2 的“旧 revision 最后落盘”及直接回归，同时核对 §7.1 已收口的 B1/B2 没有被新 diff 改动。被审提交 `479f397` 的父提交为 `a457cba`；修复只改 `src/photo_search/indexing/store.py`、直接测试和文档。禁改的迁移/契约/API/错误码/打包/依赖面相对父提交无改动。

### 8.1 A1 两种顺序的判定

`process_next` 和 `process_batch` 仍先用独立事务领取并提交 `IN_FLIGHT`。随后用**同一个 `BEGIN IMMEDIATE` 写事务**覆盖权威复读、`_is_superseded`、Lance 写入、逐事件确认与 `_make_generation_ready`：

1. **另一协调器在该事务开始前已重领并确认同一事件**：批路径先筛掉非 `IN_FLIGHT` 的被领事件，不再写或确认它；自己剩余的事件正常写与确认。新增交错用例断言第二协调器成功确认 1 条、本批返回 1、两条 outbox 都 `CONFIRMED`、向量表无重复逻辑行。
2. **甲的旧事件先进入写事务，发布者欲插入新 revision**：SQLite 写锁使发布者及第二协调器无法跨越检查→Lance 写入窗口；甲提交后发布新 revision，再处理新事件，最后写入的一定是新 revision。独立重放 §7.2 的真实 `publish_embedding` 两资产探针输出：`other_writer=blocked`、`same_catalog=blocked`，`first_applied=1`、`second_applied=1`，outbox `{'CONFIRMED': 2}`，`index_entries.confirmation_revision=2`、Lance `projection_revision=2`、`semantic_ready=True`。新测试同时比较了所存向量与新旧向量的方向。
3. **新 revision 在甲写事务开始前已出现**：甲在该事务内重做 `_is_superseded`，会只确认旧事件而不写旧行；新事件待写或已由另一协调器写入。先前“旧记录已算好、乙先确认新 revision、甲最后回写旧行”的窗口不再存在。

`after_lance` 仍在引擎写后、SQLite 确认前抛出；该写事务回滚，先前已提交的领取事务保留 `IN_FLIGHT`，重放仍以 `record_id` 幂等。确认阶段抛错的全批回滚及 `PENDING` 重放路径未改。这里接受 DS-H 将引擎写纳入 SQLite 写事务这一提交顺序调整；写锁持有时间与 5 秒 busy timeout 在真实生产维护入口接线后的容量/争用验证中继续测量，不能用本卡合成夹具推导生产延迟承诺。

### 8.2 直接验证与未决口径

本轮实跑 `uv run --no-sync python -m pytest -q -p no:cacheprovider`，目标为 P4-01C 专项、P2 提交/恢复/补查、P3 人物搜索五个文件，`--basetemp=.pytest-tmp/p401c-sol-focused/rereview2`，JUnit `test-results/p4-01c-sol-rereview2.xml`：**46 / 0 failures / 0 errors / 0 skips**，exit 0。DS-H 全量 JUnit `test-results/p4-01c-fix2-full.xml` 经本轮读取确认为 **1648 / 0 / 0 / 48**；专项 9/0/0、其受影响面 46/0/0。DS-H 30 万重建夹具证据为 18 080 ms / 60 块、Recall@20/@50 = 1.000，四档过滤和组合计数不变；本短审未重跑全量与 30 万档。`git diff --check` exit 0。

B1 的全并列距离与 B2 的索引目录拒绝保持 §7.1 的 PASS；C 的谓词等价保持 §4 的 PASS。R3（ADR 下次补集合形式注释）、R6（未来 DELETE 生产者前去重）、§5 列出的真模型向量、ANN 质量、API P95、A3、多世代混批、GTX 1660、thin 真数据等未验证项均不因本轮通过而变为已验证。**R5 仍必须成为 P4-02 前置项**：接通语义 outbox 的生产维护入口，并验证此新长写事务在生产并发/容量下的争用与 busy-timeout 边界。

**最终裁决：P4-01C PASS。** 本轮未修改生产代码、未 push。遵照本终审任务的既定边界，本会话不生成 P4-02；生成下一卡须另有用户指示。模型切换仍由用户手动完成。
