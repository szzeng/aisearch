# P4-03A 阶段 B — 聚焦终审

- 日期：2026-09-27。
- 最新结论：**P4-03A PASS（仅契约冻结）**，ADR-0013 **Accepted**；第四轮最终证据见 §8。§1～§7 保留前几轮 FAIL 与修订裁决的历史记录。
- 被审产物：阶段 A 提交 `33b01a255a6b069ad2c19edb341920fcc8fb9fa1`；本轮 HEAD `d370022`。
- 范围：任务卡阶段 B 与阶段 A 报告 §8 点名的状态、差值集、预算、控制幂等、激活、引用与迁移边界。没有重复全量工程主审，没有修改 ADR、companion、测试或产品代码。
- 开工已有修改：`docs/development-status.md`，未跟踪的全项目重评报告与 `docs/tasks/P4-02L.md`，全部保留。本轮只新增本报告、更新当前卡及状态。

## 1. 裁决与最小反例

### F-A / P1 — 激活门禁遗漏 plan 后新增的 ACTIVE 资产

位置：ADR-0013 §4.2、§7 门禁③（第 336 行）；契约测试 `coverage_complete`（第 759 行）、`activation_refusal`（第 785 行）。

**预期**：主架构 §11.3.6 第 4～7 步要求核对增量差异，最后增量补齐或明确隔离后才能切换；本 ADR §7 又明确没有部分范围开关。plan 差值集保持一次快照，不等于允许激活时漏掉新资产。

**实际**：`coverage_complete` 仅拒绝 `targets & unsettled`，再检查全代 PENDING_REPAIR/未确认 outbox 数。缺覆盖的新资产尚未生成 entry/outbox 时，两个计数均为 0，而且不属于 plan 目标集。

**独立 SQLite 反例**：调用既有 `_activation_fixture()`（run SUCCEEDED、资产 1 已确认），再 `add_asset(connection, 2)`；不为资产 2 创建目标、entry 或 outbox。真实差值谓词返回 `[2]`，`activation_refusal(...run-1)` 却为 `None`；`reference_activate` 将指针从 `prof-old` 改为 `prof-new`，快照发生写入。见探针 `new_asset`。

**最小闭合**：不扩展一次 plan 快照、不实现 P4-04 追赶；冻结激活时重新核对当前可服务范围/最后增量的拒绝条件，不能用「没有 pending 行」证明「不存在缺覆盖资产」。同一激活事务内核验；缺覆盖时保持旧 profile，返回既有码并零写入。

**必补用例**：plan 后新增 ACTIVE 且尚无 entry/outbox；空差值快照后新增资产；原本已覆盖且不在差值集的资产失去覆盖。均拒绝、原指针/代状态/run revision 不变，补齐后正例才成功。

### F-B / P1 — request_id 幂等没有持久承载，状态幂等替代不了请求幂等

位置：ADR §2.1～§2.2、§3.2（第 151 行）、§6、§10.2、§11.4；测试 `control_run`（第 448 行）；companion API 定义。

**预期**：同一 `(run_id, request_id)` 返回已记录结果，同 key 不同动作必须拒绝；跨恢复过程可重放。

**实际**：冻结的两张表没有 request_id、动作/请求摘要、结果记录；`0017` 明文只建两表与索引，未指定其他持久载体。参考模型仅以当前 state 判断同义动作。companion 也没有控制请求 object 定义，`expected_control_revision` 只出现在错误 details 中，不能校验请求闭集。既有 place_operations/search_feedback 有持久请求键，但不能在本卡不改其闭集/语义的前提下充当升级控制日志。

**最小反例**：RUNNING --pause(R1)--> PAUSED --resume(R2)--> RUNNING；重启后重放 pause(R1)。契约要求返回 R1 的已记录结果且不再变更 run，参考模型却再次返回 PAUSED。即使传 expected_control_revision，也必须先识别幂等重放，不能以旧 revision 将 R1 改判为普通冲突。

**最小闭合**：在本 ADR 新增面内明确持久幂等载体、键域、请求摘要、结果与 run 状态变更的同事务写入，以及 replay 与 revision 检查顺序；相应修订 §2/§11 的可实现表形态并补请求 schema。不能等 P4-03B 自行发明持久语义。

**必补用例**：R1 pause/R2 resume/重启/R1 重放零写；相同 key 换 action 拒绝；同 key 并发仅一条记录；有旧 expected_control_revision 的同义重放；请求未知字段拒绝。

### F-C / P1 — BUDGET 恢复不能推进，秒预算的运行中记账也未闭合

位置：ADR §5.2～§5.4（第 275～305 行）、§10.2；测试 `budget_stop`、`claim_batch_size`、`test_budget_limit_is_a_pause_that_can_resume`（第 1382 行）。

**反例一（已执行）**：两个待处理目标，budget_targets=1，已成功 1 个。PAUSED(BUDGET) resume 成 RUNNING 后预算不重置、预算上限不变，`budget_stop=True` 且 `claim_batch_size=0`。秒预算同理：elapsed_ms=1000、budget_seconds=1，恢复后依旧到限。现有控制请求没有追加/提高预算的参数，也没有其他获准的预算调整入口。因此只证明「可切到 RUNNING」，没有证明「可恢复处理剩余目标」。

**反例二（文字与模型直接对照）**：§5.2 仅在离开 RUNNING 时执行 `elapsed_ms += now-resumed_at`，§5.3 又以存储的 elapsed_ms 在批次边界判断。持续 RUNNING、resumed_at=0、now=1500、elapsed_ms=0、budget_seconds=1 时，模型仍返回 False。若取 live elapsed 或每批持久记账，需要冻结该规则及锚点更新，避免重复累计。重启时也需说明如何避免把停机区间算作运行时间；当前「按锚点继续」与「不得多记」不足以唯一决定行为。

**最小闭合**：保留累计成功数/elapsed_ms、不自动重置，不放宽用户已裁决的上限语义；定义显式增加额度后 resume 等能够继续同一 run 的路径，并冻结原子性和字段闭集。补运行中批次记账/live elapsed 口径、恢复锚点与最终目标完成和到限同时发生时的优先级。

**必补用例**：目标数/秒数耗尽后原额度下不能领取；显式获得新额度后同 run 只处理剩余目标；多批不停 RUNNING 仍按墙钟到限；暂停冻结、重启不会重复或多计。

### F-D / P2 — companion 只限制词汇，未约束错误码映射组合

位置：companion `ActivationGateRow`（第 280 行）、`FailureRow`（第 322 行）、`FailureMapping`（第 356 行）；契约测试第 1186/1277 行附近。

**独立负探针（已执行）**：

- 把 MODEL_WEIGHTS_MISSING 行改为 `error_code=SOURCE_OFFLINE`、`location=RUN`、`retryable=true`、`counts_toward_attempts=true`，完整 FailureMapping 仍返回 `errors=[]`。
- 把 GENERATION_READY 门禁改为 `refusal_code=ARTIFACT_MISSING`、`details_reason=null`，完整 ActivationGateMatrix 仍返回 `errors=[]`。

ADR 的正确表格尚在，但机器伴随物与测试没有拒绝这些错误组合；测试只拒绝词汇表外的新码，不满足任务卡「映射不允许的组合被拒绝」的明确要求。门禁数组也仅限定 5 个词汇行，没有确保五项各一次。

**最小闭合**：按 reason/gate 用逐值 oneOf/const 固定受约束组合及矩阵成员完整性；加入「使用合法词汇但配对错误」、重复一项漏一项、改 retryable/location/details.reason 的负例。保持零新增错误码和既有顶层信封。

### F-E / P2 — 租约过期同时被定义为零计次重放与一次失败

位置：ADR §4.4（第 243 行）、§4.5（第 256 行）、§9 LEASE_EXPIRED 行。

同一 IN_FLIGHT 目标租约过期，§4.4 允许 `IN_FLIGHT → PLANNED`，明确「进程中断/租约过期的重放，不增加 attempts」；§4.5 又规定租约接管必走 `IN_FLIGHT → FAILED(reason=LEASE_EXPIRED)`、attempts+1。两条转换在参考矩阵内均被允许，未按触发事件区分。实现可合法选择零计次路径，违反用户「takeover 计一次失败」。

**最小闭合**：分清仍有效租约的进程重放与过期租约接管，冻结唯一接管路径与计次位置；禁止相同 takeover 因重放重复加一。说明随后 FAILED→PLANNED 是该次接管的一部分还是另一次显式重试，不留双计歧义。

**必补用例**：同一过期 lease 的首次接管计一次、再次恢复计零；旧 token 迟到结果拒绝；普通中断重放与显式重试分别按冻结规则断言。

## 2. 直接证据缺口（随修订面一起补，不扩大任务）

- **O-1**：§7 门禁⑤要求复用 `validate_profile_readiness`，但其既有 CAPABILITY_MISSING 错误含 `runtime_package_id`，新 `UpgradeApiErrorDetails` 闭集拒绝该字段（探针 `readiness_error`）。另有 health_status、processor_id、capability_revision、role、profile_id 等既有分支字段需要逐项对照。需明确逐值保留或投影规则并校验真实错误对象，不能把 boolean runtime_ok 当作全部错误信封证据；不新增顶层字段。
- **O-2**：参考 `reference_activate` 在写事务外做 gate check，拒绝测试是在写入前抛错；它没有证明校验到提交期间的并发稳定性或已经写两次后发生故障的回滚。`ARTIFACT_READABLE` 参考实现仅 `is_file()`，没有实际打开读取。阶段 A 证据能证明静态拒绝零写、简单正例指针同事务，不能升级为真实并发/中途故障/文件可读证明。P4-03B 的 D 级必须落实，ADR 应清楚要求门禁与指针搬移同一权威事务及读取失败的零写义务。
- **O-3**：§3.3 文字禁止 C0/C1，实际 regex 只排除 C0 与 DEL，C1 的 U+0080～U+009F 未排除。需统一文字、schema、应用校验和负例。

## 3. 点名边界结论表

| 卡面边界 | 本轮结论 |
|---|---|
| 表形态、唯一键、外键、control_revision | 基础闭集/键通过；请求幂等持久承载不通过（F-B） |
| run 状态机、终态不可恢复 | 闭集及拒绝矩阵通过；控制请求幂等不通过（F-B） |
| 目标差值谓词、一次快照、唯一键 | 当前行/陈旧行/未确认 outbox 及唯一键正反例通过；激活最后增量边界不通过（F-A）；追赶仍不实现 |
| 预算、墙钟冻结、恢复 | PAUSED 冻结与上下界通过；可推进的 BUDGET 恢复/运行中记账不通过（F-C） |
| pause/resume/cancel | CANCELLED 不可恢复通过；请求重放与过期接管计次不通过（F-B/E） |
| 激活六门禁、失败零写 | 静态拒绝与指针正例通过；覆盖漏检不通过（F-A），错误组合不通过（F-D）；真实 D 级仍未实现 |
| 五类引用、人工事实保护 | 逻辑 blocker 参考模型及默认保留 1 通过；不声称物理清理实现已验收 |
| 零新增错误码、失败分类 | 所有码可在真实模块解析、HTTP 状态通过；组合闭合不通过（F-D/E/O-1） |
| API/UI 闭集与既有顶层信封 | 现有投影和顶层信封通过；请求定义缺失（F-B）、readiness details 需闭合（O-1） |
| 0017 / 16→17 / 旧文件不变 | 空号/版本/16 个迁移和 9 个旧 companion 哈希通过；持久请求载体会影响 0017 表形态，须先解决 F-B |
| P4-03B 验收前提 | B/D/C 条件与 BLOCKED 已列明；本卡失败，暂不授权实现、不生成 P4-03B 卡 |

## 4. 验证与可复现方法

本轮独立 A 级命令：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py --basetemp=.pytest-tmp/p4-03a-sol-20260927 --junitxml=test-results/p4-03a-sol-contracts.xml
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- pytest exit 0；JUnit `test-results/p4-03a-sol-contracts.xml`：**23 tests / 0 failures / 0 errors / 0 skipped**（0.259 s）。
- Ruff、lock-check、diff-check 均 exit 0。23 项全绿不覆盖 F-A～F-E 的完整语义，不能替代冻结裁决。
- 独立只读探针：通过 `.venv/Scripts/python.exe -` 从 stdin 执行，exit 0，结果 `test-results/p4-03a-sol-probes.json`（忽略目录内，不入库）。它验证的是上述缺陷可复现，不是修复通过证据。
- 探针复现入口：用 `importlib.util.spec_from_file_location` 加载 `tests/unit/test_p4_upgrade_contracts.py`，调用本报告点名的 fixture/model；F-D 用 `EXPECTED_FAILURE_MAPPING` 建正例后只替换首行，ActivationGateMatrix 用 `EXPECTED_GATES` 建正例后只替换 GENERATION_READY 的错误组合；其余字段保留 companion const 值。
- 一次试图把诊断脚本放进已有 `.pytest-tmp` 根被 WinError 5 拒绝；改用 stdin，未改 ACL。正式 pytest 未发生环境失败。
- **未跑** B 级全量、D 级实现注入、C 级打包；本轮零产品/迁移/依赖改动。没有访问真实照片库、下载模型或测真实吞吐。

## 5. 回退范围与交接

按 P4-03A 阶段 B 与 V3.1 §2.3：**仅输出裁决/反例/回退范围，修复交回 DS-H**。保持 ADR Proposed，P4-03A FREEZE PENDING，不生成 P4-03B、不开始实现。

下一步在同一 P4-03A 契约新增面内关闭 F-A～F-E 和 O-1～O-3，补与它们直接相关的契约正反例、独立 JUnit 与压缩交接包；随后仅短审这些修订面及直接回归。推荐 DS-H / DeepSeek V4.1 Flash / 高推理，由用户手动选择；本轮未自动切换或启动其他 agent。现有反例都已定位，无大范围反查需求，不据此触发 V3.1 §2.2 #6 或自动建议 DS-MAX。

真实权重、升级吞吐/显存、GTX 1660、30 万迁移/plan 耗时仍未验证/BLOCKED。**A3 未实现/未启用、§12.2 未改、F2 未关闭、P4-02F 非性能 PASS**。

## 6. 第二轮聚焦短审 — DS-H 修订面（2026-09-27）

**结论：仍不予冻结；原 F-B 及与它直接相连的首次同义请求语义尚未闭合。** 不重复第一轮全量判据；本轮只核对 `P4-03A-dsh-repair.md` §6 指定的修订面。HEAD 仍为 `d370022`，被审内容为未提交工作树；companion SHA-256 `06e413754938ecdb9d1bda0136e186b8f29773069094cffcdeeb7f6cb510da48` 已独立核对。ADR Proposed、P4-03A 未 PASS、不生成 P4-03B。

### 6.1 F-B1 / P1 — 重放响应在 ADR/参考模型与 companion 之间相反

- ADR §3.2 第 193 行：重放回 `changed=(recorded.changed)`、`replayed=true`。
- `apply_control_request` 第 2162 行按此返回原 changed；新测试第 2550 行明确要求 replay.changed=True。
- companion `ControlIdempotencyRule.const.replay_response` 第 601 行却固定 `changed=false,replayed=true`；`UpgradeControlResponse` 第 1235～1280 行亦明文禁止 changed=True 与 replayed=True 同时出现。

**独立最小反例**：RUNNING → pause(R1,USER) → resume(R2) → replay R1（带旧 expected_control_revision=2）。本轮确认持久查找优先、run 快照不变，这部分修复成立；但按参考模型生成的完整响应：

```json
{"contract_version":"p4-upgrade-contracts.v1","request_id":"R1","run_id":"run-1","state":"PAUSED","pause_reason":"USER","pause_detail":null,"control_revision":2,"changed":true,"replayed":true}
```

`contract_errors(response, 'UpgradeControlResponse')` 返回 `$.replayed: const`、`$.changed: const`。所有字段都齐全，失败原因就是重放的 changed/replayed 配对。31 项成功测试没有把该真实完整响应再送入 companion。

**裁决/最小闭合**：统一 changed 到底表示「原请求曾改变 run」还是「本次调用写了 run」；原请求的 changed 可仍保存在持久日志，但公开响应、幂等 rule、ADR 与模型/断言必须是同一语义。不得只删除测试或把完整响应校验跳过。必补 first/no-op/replay 三种完整响应正例，并把另一种冲突配对作为负例；重放仍零写、保持旧 revision 检查优先级豁免。

### 6.2 F-B2 / P2 — changed=false 的首次请求仍修改 run

位置：ADR §2.3、§3.2 第 199 行（首次同义请求插入日志，**但不动 run**）；参考 `apply_control_request` 第 2181～2221 行及其无条件 UPDATE。

**独立最小反例**：

1. `_running_fixture()` → pause(P1,BUDGET,now=2000)，run 为 PAUSED/BUDGET、control_revision=2。
2. 新键 pause(P2,USER,now=3000)。模型只用 `state != target` 决定 changed，因此返回 changed=False、replayed=False、revision=2；实际把 `pause_reason` 从 BUDGET 改为 USER，把 `updated_at` 从 2000 改为 3000。
3. 再用新键 pause(P3,USER,now=4000)，参数与当前原因一致，仍无条件改 `updated_at` 为 4000。

这不是幂等日志正常插入造成的差异：独立取值只比较 `SELECT * FROM upgrade_runs`，run 本身已变。新测试只检查请求日志中 changed=0，没有断言完整 run 行不变；`catalog_snapshot` 也不包含 run.updated_at、budget、elapsed 或 clock 字段。

**裁决/最小闭合**：对真正首次同义 no-op，仅插日志、不要 UPDATE run；同目标状态但 reason/detail 等不同的请求必须有冻结的拒绝或显式变更规则，不能 changed=False、revision 不动却修改原因。补完整 run 行（全部列）前后相同断言，单独断言日志增加一行；重复键真正 replay 两者均不写。

**同一 payload 判定面的补充**：RUNNING 上 RESUME(G1,grant_targets=2)（旧 cap=1）被模型接受为 changed=False，cap 仍 1，日志 grant_targets=NULL，但 digest 已包含这次授权。§5.4 仅定义 grant 与 PAUSED→RUNNING 同事务，尚未冻结这个状态下应拒绝还是接受另一类显式修改。请与 no-op 参数判定一起闭合，不能成功响应后静默丢弃授权。此项不单列新主审方向。

### 6.3 原失败边界的本轮结论

| 边界 | 本轮独立结论 |
|---|---|
| F-A 当前差值集完整性 | 原新增资产/空快照/旧覆盖丢失反例均已改为拒绝零写，补齐正例通过；闭合 |
| F-B 持久载体、请求闭集与 replay 优先 | 第三张表/摘要域/顺序结构成立；迟到旧请求不再二次暂停，但完整响应有 F-B1、首次 no-op 有 F-B2；未闭合 |
| F-C 增量额度、运行中墙钟、重启锚点 | exhausted→带 grant 恢复路径、两段账本与恢复规则已补，原反例闭合；RUNNING+grant 的新键判定随 F-B2 补齐；§5.4「不重置 resumed_at/clock_owner」应改为累计数不重置、运行段锚点按 §5.2 新建，避免文字歧义 |
| F-D 错误组合 | const 并集/精确成员数/uniqueItems 的正反例通过，原错误配对已拒绝；闭合 |
| F-E 过期接管计次 | 文字唯一路径和 attempts 唯一计次点已对齐；租约字段 CHECK/重试不双计通过。真实 token/时间谓词接管及并发重放仍属 P4-03B D 级，参考用例未真正执行第二次接管，不能声称产品已验证；原文字矛盾闭合 |
| O-1 既有拒绝 details | 原 runtime_package_id 缺键已补，真实 binding/readiness 路径键集探针通过；本轮未发现旧缺键反例残留；闭合原缺口，不扩大为全部产品错误路径验收 |
| O-2 同事务/实际读取 | 显式 BEGIN IMMEDIATE、门禁与指针同事务、提交前故障回滚、实际 open/read 用例通过；闭合契约层缺口，真实磁盘/并发 D 级仍未测 |
| O-3 C1 | ADR/四处 schema 正则及 U+0085/U+009F 负例对齐；闭合 |

### 6.4 本轮独立验证

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py --basetemp=.pytest-tmp/p4-03a-sol-r2-20260927 --junitxml=test-results/p4-03a-sol-r2-contracts.xml
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- 本轮 JUnit **31 / 0 / 0 / 0**，pytest exit 0；Ruff、lock-check、diff-check 均 exit 0。这表示已有用例通过，不能消除上述独立反例。
- 只读 stdin Python 探针 exit 0，结果 `test-results/p4-03a-sol-r2-probes.json`。字段 replay/schema_errors、noop_changed_reason、same_reason_noop、running_grant 固定上述实际值。
- 探针复现用 `importlib.util` 加载当前契约测试模块：调用 `_running_fixture`、`apply_control_request`；给 replay 返回补齐 contract_version/request_id/run_id/pause_detail 后送入 `contract_errors`；no-op 使用各不同 now_ms，前后取完整 upgrade_runs 行。全部为合成内存 SQLite，无真实照片/模型。
- 被审新增生产、迁移、依赖、既有 ADR/companion/搜索信封改动仍为 0，0017 未创建；未重跑 B 全量、未做真实 D/C 或性能实验。

### 6.5 停止点与下一次短审输入

修复继续交回 DS-H，仅闭合 F-B1/F-B2、RUNNING+grant 参数分支与 §5.4 的锚点措辞，补完整响应/完整 run 行断言及 JUnit；下一轮只短审这些点与直接回归。不重开已闭合的 F-A/D/E/O-1～O-3，不启动 P4-03B。已定位问题，无大范围反查，不建议 DS-MAX；模型由用户手动选择。**ADR Proposed、P4-03A 未 PASS、A3 未实现/启用、F2 未关闭、§12.2 未改、P4-02F 非性能 PASS**。

## 7. 第三轮聚焦短审 — 第二轮修订面（2026-09-27）

**结论：原 F-B1/F-B2 点名反例已关闭，但额度状态前置条件仍有一处直接漏分支，暂不冻结。**
本轮只按 DS-H 修订报告 §8.6 核对控制响应、no-op、额度参数与直接回归，不重推此前已闭合面。
HEAD `d370022`，被审为未提交工作树；companion SHA-256 已独立核对为
`bdf8180af0d9f535d966ea02c05a3a2deab101140606d68fd5630e67888197b5`。

### 7.1 已关闭的点

- **F-B1 关闭**：ADR §3.2、companion、模型、完整响应测试一致规定公开响应 changed 表示本次调用是否写 run；重放固定 changed=false/replayed=true。原请求 changed=1 保留在日志。独立重放探针完整响应通过 schema，run 整行与日志行数均不变，旧 expected_control_revision 不阻断重放。
- **F-B2 原 no-op 反例关闭**：同义 pause 新键仅多一行日志，run 整行不变；PAUSED(BUDGET) 上 pause(USER) 拒绝 CONTROL_NOOP_ARGUMENT_MISMATCH，run/日志均不变；RUNNING 上 grant_targets 与 grant_seconds 两种新键请求均按相同规则拒绝、零写。
- **锚点措辞关闭**：§5.4 已区分累计数不重置与 resumed_at/clock_owner 按本次进入 RUNNING 新建。
- **F-E 补充证据通过**：新增 takeover_expired_lease 真实执行未到期/到期/第二次接管，第二次拒绝，attempts 仍 1。此为契约层参考证据，产品并发与 token 谓词仍待 P4-03B。
- 32 项契约用例全绿，包括 F-A/C/D/E 与 O-1～O-3 的直接回归。

### 7.2 F-B2-G / P2 — 仅限 PAUSED→RUNNING 的额度请求在 PLANNED 上仍被接受

**位置**：ADR §5.4 第 403 行明确「带 grant_* 的 resume 只有在 PAUSED→RUNNING 真的发生时才合法」。
参考 `apply_control_request` 第 2260 行却仅判 `changed and new_state == 'RUNNING'`，未要求旧 state 为 PAUSED；
`PLANNED→RUNNING` 本身在通用 run 转换矩阵内，所以该分支会放行。companion BudgetGrantRule 仅固定 action=RESUME，
也未固定授权的源状态/转换。

**独立最小反例（数量、秒数两种均已执行）**：合法 PLANNED run，原 budget_targets=1；调用
`apply_control_request(run_id='run-1',request_id='PLAN_GRANT',action='RESUME',grant_targets=2)`。
实际响应 RUNNING/control_revision=2/changed=true/replayed=false、完整响应 schema 零错误；预算变成 3，日志记录 grant=2。
把字段换成 budget_seconds/grant_seconds，同样由 1 变成 3，run 写入且新增一行日志。
这直接违反本轮新冻结的「仅 PAUSED→RUNNING」范围，并非产品实现/D 级未测，也没有扩大到其他控制路径。
见独立探针 `planned_grant`、`planned_grant_seconds`。

**最小修复与必补断言**：

1. 在 replay 查找之后、任何状态/额度/日志写入之前，对带 grant_* 的**新键**请求核验源 state=PAUSED；其他状态结构化拒绝，零写。保持先命中旧请求则可直接重放，避免成功授权后在 RUNNING 重放被误拒。
2. ADR/companion/参考模型统一授权允许的源状态与既有拒绝码/reason，不改变一般 PLANNED→RUNNING 启动转换，不引入产品代码。
3. PLANNED+grant_targets、PLANNED+grant_seconds 均拒绝，完整 run 行及日志不变；PAUSED+grant 两种正例仍成功；成功 grant 请求在 RUNNING 用同 key 重放零写、不再次加额度；既有 RUNNING 新键拒绝继续保持。

仅有这处额度前置条件待闭合。其余已关闭点不重新展开，修复交回 DS-H 后短审此分支及上述正负回归。

### 7.3 本轮验收与停止点

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py --basetemp=.pytest-tmp/p4-03a-sol-r3-20260927 --junitxml=test-results/p4-03a-sol-r3-contracts.xml
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- 独立 JUnit **32 / 0 / 0 / 0**，pytest exit 0；Ruff、lock-check、diff-check 均 exit 0。
- 合成内存 SQLite 探针从 stdin 执行，exit 0，输出 `test-results/p4-03a-sol-r3-probes.json`。
  复现入口同 §6.4 的 importlib 加载方式；PLANNED 夹具用 `_running_fixture(budget_targets=1)` 后按 CHECK
  将 state 置 PLANNED、resumed_at/clock_owner 置 NULL，再调用上述控制请求。秒数夹具替换对应两个字段。
- 本轮仅修改本报告、状态、P4-03A 卡；不修改 DS-H 的 ADR/companion/测试修订，也不修改产品/迁移/依赖。
  未重跑 B 全量、真实 D/C、模型或性能实验，没有访问真实照片库。
- **ADR-0013 保持 Proposed、P4-03A 未 PASS，未生成/启动 P4-03B**。单个已定位分支不需大范围反查，
  不建议 DS-MAX。**A3 未实现/未启用、F2 未关闭、§12.2 未改、P4-02F 非性能 PASS**。

## 8. 第四轮聚焦短审 — 最终冻结（2026-09-27）

**P4-03A PASS（契约冻结），ADR-0013 Accepted。** 本轮只核对修订报告 §9.4 的额度源状态前置条件及直接回归；前几轮已闭合面不重做全量主审。
被审 HEAD `d370022` 的未提交工作树，companion SHA-256 独立核对为
`f92dece59b1a16e896af68a2c0484b5b061ce004e62fbe3245a6827dea4469e8`（本轮未改 companion）。

| 剩余边界 | 本轮证据与裁决 |
|---|---|
| F-B2-G：PLANNED+grant 两种负例 | ADR §5.4、BudgetGrantRule.allowed_source_state、参考模型源状态检查一致；数量/秒数新键均返回 INVALID_STATE_TRANSITION / GRANT_SOURCE_STATE_NOT_PAUSED，run 整行及日志不变；关闭 |
| PAUSED+grant 两种正例 | 独立数量/秒数探针均由 1→3，完整响应通过 schema；累计数保持、运行段锚点由进入 RUNNING 新建；通过 |
| 成功授权后 RUNNING 同 key 重放 | 两种字段均 changed=false/replayed=true，带旧 expected_control_revision 仍重放成功；run/日志不变、额度仍为 3，不二次授权；通过 |
| RUNNING 新 key 带 grant | 数量/秒数均拒绝 CONTROL_NOOP_ARGUMENT_MISMATCH，run/日志不变，保留此前已冻结规则；通过 |
| 无 grant 的 PLANNED→RUNNING | 一般启动转换保持成功，没有被授权前置条件误伤；通过 |
| 原 F-A～F-E、F-B1/B2 与 O-1～O-3 | 本轮 33 项契约及直接回归全通过，既有拒绝/无写/故障回滚/完整响应/租约计次证据继续成立；无未决阻断 |

**判定顺序**：同义 replay 优先；新键先经既有状态谓词，再检查授权源状态/额度条件，之后才能写 run 与日志；RUNNING 的参数不一致仍在 no-op 分支拒绝。不得把成功授权后的旧请求当成新键重判。

### 8.1 独立验收

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_upgrade_contracts.py --basetemp=.pytest-tmp/p4-03a-sol-r4-20260927 --junitxml=test-results/p4-03a-sol-r4-contracts.xml
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- 独立 A 级 JUnit **33 tests / 0 failures / 0 errors / 0 skipped**，pytest exit 0。
- stdin Python 合成 SQLite 探针 exit 0，结果 `test-results/p4-03a-sol-r4-probes.json`：数量/秒数的 PLANNED 拒绝、PAUSED 授权、RUNNING 同键重放与新键拒绝均独立断言，完整响应通过 companion；全部 run 整行/日志计数符合义务。
- Ruff、lock-check、diff-check exit 0；Accepted/报告/状态/下一卡写回后以 JUnit `test-results/p4-03a-sol-r4-final-contracts.xml` 复核，仍为 **33 / 0 / 0 / 0**、exit 0。
- 本轮只改冻结状态、审查报告与交接文档、生成下一卡；DS-H 的 companion/参考模型/测试未改。生产代码、migration、SUPPORTED_SCHEMA_VERSION、依赖、既有 ADR/companion 与搜索协议零本轮改动。

### 8.2 范围与交接

本 PASS 仅冻结 ADR-0013 与 companion，不代表升级功能可用。`0017` 仍未创建，SUPPORTED_SCHEMA_VERSION=16；
产品迁移、重启/并发/故障 D 级、真实模型/吞吐/显存、GTX 1660、30 万 plan 与迁移成本均未执行或 BLOCKED。
本轮未重跑 B 全量、C 打包、真实模型或性能实验；未访问真实照片库、未下载模型。

下一任务 **P4-03B**，卡：[P4-03B](../tasks/P4-03B.md)，推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**，由用户手动选择。
仅生成卡，不启动实现；实现后独立 DS-H 完整受影响面工程主审，随后必选聚焦终审。F2/A3 性能轨道仍独立：
**A3 未实现/未启用、F2 未关闭、§12.2 未改、P4-02F 非性能 PASS**。
