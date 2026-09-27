# ADR-0013 — UpgradeRun、目标集、预算、暂停恢复与 profile 原子激活契约

- 状态：**Accepted**（2026-09-27，P4-03A 阶段 B 第四轮聚焦短审通过；第一轮及修订历史见 §15，最终证据见阶段 B 报告 §8）
- 日期：2026-09-27
- 适用实现：**P4-03B**（`0017` 迁移 + `src/photo_search/upgrade/**` + API/UI + 六项 D 级注入）
- 决策输入：[P4-03 准备包](../reports/P4-03-preparation.md) §2 的五条用户裁决（2026-09-26）、
  [P4-03A 阶段 A 报告](../reports/P4-03A-dsh-contract-draft.md)
- 上游（只读）：ADR-0002 §3.5/§5.4/§7、ADR-0003 §8/§9/§11、ADR-0009 §3.5/§6、ADR-0011、
  主架构 §6.1/§6.2/§6.3/§7.2/§11.2/§11.3.5/§11.3.6/§11.3.7/§12.2/§12.4 第 23～26、28 条、
  V3.1 §2.3/§2.4/§3.2/§3.4/§9/§11
- 机器可读伴随物：[`p4-upgrade-contracts.v1.json`](contracts/p4-upgrade-contracts.v1.json)（SHA-256 见 §16）
- 契约测试：`tests/unit/test_p4_upgrade_contracts.py`
- 被冻结且**不得由本 ADR 改写**的既有契约：ADR-0001～ADR-0012 与全部既有 companion 的语义、
  `search_profiles` 的不可变列集、`active_search_profile` 的单行指针、
  `index_generations` 的 `state`/`is_active` 语义与唯一索引、`index_entries`/`index_outbox`/`artifacts`
  的列与状态、`catalog_filter_revision` 的单一计数器、`POST /api/v1/search` 信封 /
  `query_hash` / 游标 / 摘要语义与**全部错误码**、RRF/BM25/距离公式、P1～P3 人物/地点人工事实

## 1. 决策范围与阶段边界

本 ADR 冻结「一次升级运行」跑起来所需的**全部持久语义**：`upgrade_runs`/`upgrade_targets` 的表形态与闭集、
目标集（差值集）的权威判定、预算与暂停/恢复/取消的状态机、`search_profiles` 原子激活前的门禁、
引用保护边界、失败分类到**既有**错误码的映射，以及 `0017` 迁移的可实现性证明。

P4-03A **只冻结规则**：

- **不实现**任何升级行为，不新增/修改 `0017` 或任何 migration，不改 `SUPPORTED_SCHEMA_VERSION`
  （本卡开工实测仍为 **16**，`0017` 空闲）；
- **不修改**任何 `src/photo_search/**` 生产代码、`pyproject.toml`/`uv.lock`、`PhotoSearch.spec`、
  `tools/packaging/**`、`web/**` 行为；
- **不读**真实照片库；全部证据使用项目内临时目录的合成夹具；
- **不承担** §12.2 的前台性能目标（P4-02J 收口后 F2 仍未关闭），也不承担 P4-02J 收口新发现的
  「投影未追赶 + 后台 drain ⇒ 前台搜索被 `CATALOG_UNREADABLE` 拒绝」归因（独立写锁拒绝归因卡）；
- **不声称**任何拒绝已经在生产代码里生效。

术语在本 ADR 内固定为：

| 术语 | 含义 |
|---|---|
| **升级运行（UpgradeRun）** | 一条把「目标 profile、目标 generation、目标资产集、预算、进度、控制动作」变成持久状态的行；它是**调度/尝试事实**，不是内容权威 |
| **目标集（差值集）** | plan 时一次性快照的资产集合：在当前 ACTIVE 资产中，目标 profile 的 `(space_id, processor_id, dimension)` 下**尚无当前 revision 的 `CONFIRMED` 覆盖**的那些资产 |
| **空间（space）** | ADR-0003 §8 的 `space_id`；同一 `space_id` 内至多一个 `is_active = 1` 的 generation |
| **控制动作** | 暂停 / 恢复 / 取消三个显式请求；三者都只改 `upgrade_runs` 的状态与 `control_revision` |
| **软停** | 到预算时不再领取新目标、已完成保持完成，run 转 `PAUSED`，可 resume 同一 run；**不是**终态 |
| **权威覆盖完整** | 目标集逐资产在该 generation 上存在**当前 revision 的** `CONFIRMED` 条目，且该代没有未确认 outbox、没有 `PENDING_REPAIR` 条目（§7 门禁 ③） |

## 2. 表与列形态

**三张**新表**只由 P4-03B 的 `0017_upgrade_runs.sql` 创建**：`upgrade_runs`、`upgrade_targets` 与
`upgrade_control_requests`（§2.3）。本节冻结它们的稳定列名、类型、可空性、默认值、闭集 CHECK、键与索引。
DDL 的具体拼写由 P4-03B 决定，但只要与本节的列名/闭集/键冲突即为违约。

### 2.1 `upgrade_runs`

| 列 | 类型 / 可空 | 契约 |
|---|---|---|
| `run_id` | `TEXT PRIMARY KEY CHECK(run_id <> '')` | 稳定 ID，由 P4-03B 生成（不透明字符串，≤128 字符） |
| `profile_id` | `TEXT NOT NULL REFERENCES search_profiles(profile_id) ON DELETE RESTRICT` | 目标 profile；**不可变**（ADR-0003 §8 的写入一次触发器已在 `search_profiles` 上） |
| `space_id` | `TEXT NOT NULL CHECK(space_id <> '')` | 冗余记录的空间键，必须与目标 profile 的 `space_id` 相等（门禁 ⑤ 的输入） |
| `image_generation_id` | `TEXT NOT NULL REFERENCES index_generations(generation_id) ON DELETE RESTRICT` | 目标 generation；必须与目标 profile 的 `image_generation_id` 相等 |
| `target_set_digest` | `TEXT NOT NULL` + 64 位小写十六进制 CHECK | plan 快照的确定性摘要（§4.2） |
| `state` | `TEXT NOT NULL CHECK(state IN ('PLANNED','RUNNING','PAUSED','CANCELLED','FAILED','SUCCEEDED'))` | run 状态闭集（§3.1） |
| `pause_reason` | `TEXT NULL CHECK(pause_reason IS NULL OR pause_reason IN ('USER','BUDGET','ENVIRONMENT'))` | 仅 `PAUSED` 可非空（见跨列 CHECK） |
| `pause_detail` | `TEXT NULL` + 长度/字符 CHECK | §3.3；**不得含路径** |
| `budget_targets` | `INTEGER NULL CHECK(budget_targets IS NULL OR (budget_targets BETWEEN 1 AND 100000000))` | NULL = 不限（§5） |
| `budget_seconds` | `INTEGER NULL CHECK(budget_seconds IS NULL OR (budget_seconds BETWEEN 1 AND 86400000))` | NULL = 不限；**墙钟秒** |
| `budget_concurrency` | `INTEGER NOT NULL DEFAULT 1 CHECK(budget_concurrency BETWEEN 1 AND 64)` | 一次领取目标数上限；**不新增 worker、不新增 GPU** |
| `elapsed_ms` | `INTEGER NOT NULL DEFAULT 0 CHECK(elapsed_ms >= 0)` | 累计**运行中**墙钟毫秒；`PAUSED` 期间**冻结**（§5.2） |
| `resumed_at` | `INTEGER NULL CHECK(resumed_at IS NULL OR resumed_at >= 0)` | 仅 `RUNNING` 可非空（见跨列 CHECK）；**当前 RUNNING 段**的墙钟锚点，只由 `clock_owner` 指明的会话使用（§5.2） |
| `clock_owner` | `TEXT NULL CHECK(clock_owner IS NULL OR clock_owner <> '')` | 仅 `RUNNING` 可非空；拥有当前 RUNNING 段的**进程会话**标识（不透明 token）。重启后由恢复事务重锚，见 §5.2 |
| `control_revision` | `INTEGER NOT NULL CHECK(control_revision >= 1)` | §2.4 |
| `last_error_code` | `TEXT NULL` | run 级失败分类（§9）；必须取自既有错误码闭集 |
| `planned_at` / `updated_at` | `INTEGER NOT NULL CHECK(... >= 0)` | UTC epoch 毫秒 |

必须同时成立的三条跨列 CHECK（`upgrade_runs` 级）：

1. `(state = 'PAUSED' AND pause_reason IS NOT NULL) OR (state <> 'PAUSED' AND pause_reason IS NULL AND pause_detail IS NULL)`；
2. `(state = 'RUNNING' AND resumed_at IS NOT NULL AND clock_owner IS NOT NULL)
   OR (state <> 'RUNNING' AND resumed_at IS NULL AND clock_owner IS NULL)`（**resumed_at 与 clock_owner 同生同灭**）；
3. `(pause_reason <> 'ENVIRONMENT') OR (pause_detail IS NOT NULL AND pause_detail <> '')`。

`pause_detail` 的字符 CHECK 逐字为 §3.3 的规则（`length(pause_detail) BETWEEN 1 AND 200`，且 `instr` 不含
`'/'`、反斜杠、`':'`、`char(9)`、`char(10)`、`char(13)`、`char(0)`）。**C0 与 C1 都算控制字符**（O-3）：
正则末尾的 `\u007F-\u009F` 就是这条要求的可执行形式，DDL 至少覆盖分隔符与换行。

索引：`CREATE INDEX idx_upgrade_runs_state ON upgrade_runs(state, updated_at)`（本卡只冻结这一条必要索引；
P4-03B 可增量添加更多）。

### 2.2 `upgrade_targets`

| 列 | 类型 / 可空 | 契约 |
|---|---|---|
| `run_id` | `TEXT NOT NULL REFERENCES upgrade_runs(run_id) ON DELETE CASCADE` | 目标行由 run 拥有 |
| `asset_id` | `INTEGER NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT` | 一个目标至多一行；原图与身份仍属 `assets` |
| `input_revision` | `TEXT NOT NULL CHECK(input_revision <> '')` | plan 时该资产的**内容 revision**，即 `assets.sha256`（ADR-0002 §3.5）；只用于 P4-04 的「已被更新内容取代」判定 |
| `state` | `TEXT NOT NULL CHECK(state IN ('PLANNED','IN_FLIGHT','SUCCEEDED','FAILED','CANCELLED'))` | 目标行状态闭集（§4.4） |
| `reason` | `TEXT NULL CHECK(reason IS NULL OR reason IN ('MODEL_WEIGHTS_MISSING','VRAM_INSUFFICIENT','RUNTIME_UNHEALTHY','SOURCE_OFFLINE','ARTIFACT_MISSING','LEASE_EXPIRED','DISK_SPACE_INSUFFICIENT'))` | `FAILED` 必须非空（见跨列 CHECK） |
| `attempts` | `INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0)` | **只在「进入 `FAILED`」且该 `reason` 的 §9 `counts_toward_attempts = true` 时 +1**（F-E）：显式重试 `FAILED→PLANNED`、有效租约重放 `IN_FLIGHT→PLANNED` 都不加 |
| `lease_token` | `TEXT NULL CHECK(lease_token IS NULL OR lease_token <> '')` | 仅 `IN_FLIGHT` 可非空；领取批次时生成的不透明租约（§4.5） |
| `lease_expires_at` | `INTEGER NULL CHECK(lease_expires_at IS NULL OR lease_expires_at >= 0)` | 仅 `IN_FLIGHT` 可非空；租约到期时刻（UTC epoch 毫秒） |
| `updated_at` | `INTEGER NOT NULL CHECK(updated_at >= 0)` | UTC epoch 毫秒 |

- 主键：`PRIMARY KEY(run_id, asset_id)` ⇒ §4.2 的「同一 plan 重复执行不产生第二行」由**唯一键**保证，
  而不是由应用层的先查后写保证。
- 跨列 CHECK（两条）：
  1. `(state = 'FAILED' AND reason IS NOT NULL) OR (state <> 'FAILED' AND reason IS NULL)`；
  2. `(state = 'IN_FLIGHT' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
     OR (state <> 'IN_FLIGHT' AND lease_token IS NULL AND lease_expires_at IS NULL)` ——
     与 ADR-0002 §3.6 的 `jobs` 租约 CHECK 同形，使「有效租约重放」与「过期租约接管」可被结构区分（F-E）。
- 索引：`CREATE INDEX idx_upgrade_targets_state ON upgrade_targets(run_id, state, asset_id)`。

### 2.3 `upgrade_control_requests`（控制请求的持久幂等载体，F-B）

`sender` 侧每次控制请求（§6）都必须在**与 run 状态变更同一个事务**里写入一行；它就是「同一
`(run_id, request_id)` 跨重启重放」的唯一载体，**不接受**「用当前 state 反推幂等」的实现。

| 列 | 类型 / 可空 | 契约 |
|---|---|---|
| `run_id` | `TEXT NOT NULL REFERENCES upgrade_runs(run_id) ON DELETE CASCADE` | 请求所属 run |
| `request_id` | `TEXT NOT NULL CHECK(request_id <> '' AND length(request_id) <= 64)` | 调用方幂等键（与 §10.2 的请求字段同域） |
| `action` | `TEXT NOT NULL CHECK(action IN ('PAUSE','RESUME','CANCEL'))` | 目标动作闭集（对应 HTTP 路径 `/pause`、`/resume`、`/cancel`） |
| `request_digest` | `TEXT NOT NULL` + 64 位小写十六进制 CHECK | `sha256(canonical_json({action, pause_reason, pause_detail, grant_targets, grant_seconds}))`；**不含** `expected_control_revision`（§3.2） |
| `grant_targets` / `grant_seconds` | `INTEGER NULL` + 与 §5.1 同域 CHECK | 仅 `RESUME` 可非空（§5.4）；记录本次被接受的额度增量 |
| `outcome_state` | `TEXT NOT NULL CHECK(outcome_state IN ('PLANNED','RUNNING','PAUSED','CANCELLED','FAILED','SUCCEEDED'))` | 该请求**产出或观察到**的 run 状态 |
| `outcome_pause_reason` / `outcome_pause_detail` | `TEXT NULL` + §3.3 规则 | 与 `outcome_state = 'PAUSED'` 同生同灭 |
| `outcome_control_revision` | `INTEGER NOT NULL CHECK(outcome_control_revision >= 1)` | 该请求产出/观察到的 `control_revision` |
| `changed` | `INTEGER NOT NULL CHECK(changed IN (0,1))` | `1` = 本次请求真的改变了 run；`0` = 落在已满足状态上的**首次**同义请求（也是幂等记录） |
| `created_at` | `INTEGER NOT NULL CHECK(created_at >= 0)` | UTC epoch 毫秒 |

- 主键：`PRIMARY KEY(run_id, request_id)`；索引：`CREATE INDEX idx_upgrade_control_requests_run ON upgrade_control_requests(run_id, created_at)`。
- 跨列 CHECK（三条）：① `(grant_targets IS NULL OR (action = 'RESUME' AND changed = 1))`；
  ② 同上，`grant_seconds`；③ `(outcome_state = 'PAUSED' AND outcome_pause_reason IS NOT NULL)
  OR (outcome_state <> 'PAUSED' AND outcome_pause_reason IS NULL AND outcome_pause_detail IS NULL)`。
- 与 `changed = 0` 的关系：`changed = 0` 是**首次**同义请求的记录，它的 `grant_*` 必须为 `NULL`；
  真正的重放**不再写行**，由 §3.2 的查找分支返回既有结果。

### 2.4 `control_revision` 的语义与自增规则

`control_revision` 是**每个 run 各一条**的单调整数，语义固定为「这条 run 已被接受的持久状态变更次数」：

- `PLANNED` 插入时为 `1`；
- 每一次**被接受**的 run 状态转换**恰好 +1**；
- **幂等重放 +0**：同一个控制请求落在已经满足的目标状态上（对 `PAUSED` 再 pause、对 `RUNNING` 再 resume、
  对 `CANCELLED` 再 cancel）不改状态、不改 `control_revision`，只回报 `changed=false`、`replayed=true`；
- 目标行的状态变化**不**递增 `control_revision`（它是 run 的控制版本，不是进度版本）；
- 并发控制请求由 `BEGIN IMMEDIATE` + 状态谓词串行化（§6）：先到者写成功并 +1，后到者要么成为幂等重放，
  要么得到 `INVALID_STATE_TRANSITION`。

### 2.5 权威边界（不得形成双权威）

| 问题 | 唯一权威 | `upgrade_runs`/`upgrade_targets`/`upgrade_control_requests` 的角色 |
|---|---|---|
| 哪些资产已在该 generation 可查询 | `index_entries`（+ `index_outbox` 是否确认） | 只**读**，用于门禁 ③ 与目标集；**从不回写** |
| 哪个 profile 在服务 | `active_search_profile` 单行指针 | 只**读**（门禁 ①/⑤）；激活仍由既有 `search/profile.py::activate_profile` 执行 |
| 物理索引代的生存期 | `index_generations.state`/`is_active` | 只**读**；本卡不新增第二个生命周期字段 |
| 人工人物/地点/时间事实 | P1～P3 的既有表 | 完全不涉及；本卡既不读也不写 |

`upgrade_targets.state = 'SUCCEEDED'` **不等于**「该资产已可查询」：可查询性只由 `index_entries` 判定。
这条边界禁止用 run 的进度冒充权威覆盖。

## 3. run 状态机

### 3.1 闭集与转换矩阵

闭集：`PLANNED / RUNNING / PAUSED / CANCELLED / FAILED / SUCCEEDED`。**允许**的转换恰好是：

| from → to | 触发 | 前置条件 |
|---|---|---|
| `PLANNED → RUNNING` | 启动（首次领取） | 目标集已快照；全局调度器未持久暂停，否则 `SCHEDULER_PAUSED` |
| `PLANNED → PAUSED` | 用户在启动前暂停 | `pause_reason = USER` |
| `PLANNED → CANCELLED` | 用户取消未启动的 run | 终态 |
| `RUNNING → PAUSED` | 用户暂停 / 到预算 / 环境阻塞 | §3.3 的 reason；`BUDGET` 只在批次边界（§5.3） |
| `RUNNING → SUCCEEDED` | 目标集全部到达终态且无未决失败 | 不等于可激活；激活另需 §7 |
| `RUNNING → FAILED` | run 级不可恢复失败（如 `DISK_SPACE_INSUFFICIENT`） | `last_error_code` 取自既有闭集 |
| `RUNNING → CANCELLED` | 用户取消 | 终态；未处理目标转 `CANCELLED` |
| `PAUSED → RUNNING` | 用户恢复（含 `BUDGET`/`ENVIRONMENT` 解除后） | §6；`SUCCEEDED` 目标跳过 |
| `PAUSED → CANCELLED` | 用户取消 | 终态 |
| `PAUSED → FAILED` | 环境阻塞被判定为不可恢复 | `last_error_code` 取自既有闭集 |

**终态**：`CANCELLED / FAILED / SUCCEEDED` —— 三者都**不允许**再转换回 `RUNNING`。
任何不在上表的 `from → to` 都必须以既有 `INVALID_STATE_TRANSITION` 拒绝，且**零写入**。
`PAUSED → SUCCEEDED` 不在允许表内：软停之后必须经过 `RUNNING` 才能收敛。

### 3.2 幂等重放

控制请求（§6）带 `request_id`，其持久载体是 §2.3 的 `upgrade_control_requests`。**同义重放必须在任何
revision 检查之前判定**（F-B），顺序逐字冻结为：

1. 在 `BEGIN IMMEDIATE` 事务内按 `(run_id, request_id)` 查找：
   - 命中且 `request_digest` 相同 ⇒ 返回该行记录的 `outcome_state`/`outcome_pause_reason`/
     `outcome_pause_detail`/`outcome_control_revision`，回 **`changed = false`、`replayed = true`**，
     **零写入、不重新判定状态、不做 revision 检查**。
     **`changed` 的语义逐字冻结为「本次调用是否写了 run」**；「原请求是否改变过 run」由日志行的
     `changed` 列单独保存（`log_changed_means`），两者不得混用（F-B1）；
   - 命中但 `request_digest` 不同 ⇒ 既有 `VALIDATION_ERROR`（422，`details.reason = IDEMPOTENCY_CONFLICT`），零写入；
2. 未命中时再依次判定：状态谓词（§3.1/§6）→ `expected_control_revision`（提供且不等于当前值 ⇒
   既有 `INVALID_STATE_TRANSITION`，`details.reason = CONTROL_REVISION_STALE`）；
3. 接受则**在同一事务内**应用状态转换（`control_revision + 1`）并插入请求行；
   **真正的首次同义请求**（目标状态已成立**且**请求参数与已成立状态一致）只插入日志行
   （`changed = 0`、`grant_*` 为 NULL），**不得 UPDATE `upgrade_runs` 的任何列** ——
   包括 `updated_at`、`pause_reason`、`pause_detail`（F-B2）。「同义」的定义逐字为：
   `PAUSE` 比较 `pause_reason` 与 `pause_detail`；`RESUME` 要求**不带** `grant_*`；`CANCEL` 无参数。

目标状态已成立但**参数不一致**（例如 `PAUSED(BUDGET)` 上再 `pause(USER)`、或 run 已经是 `RUNNING`
时 `resume` 带 `grant_*`）⇒ 既有 `INVALID_STATE_TRANSITION`（409，
`details.reason = CONTROL_NOOP_ARGUMENT_MISMATCH`），**零写入**（连日志行也不落），
**禁止**「返回成功但静默丢弃参数」。

`request_digest` 的字段闭集 = `{action, pause_reason, pause_detail, grant_targets, grant_seconds}`，
**不含** `expected_control_revision`：后者只是本次调用的前置条件，把它纳入摘要会让「带旧 revision 的
同义重放」被误判为冲突。既有 `place_operations`/`search_feedback` 的幂等键不能充当本表：它们的
闭集与语义属于 P1～P3，不得在本卡内改写。

### 3.3 `PAUSE_REASON` 与 `detail` 规则

`PAUSE_REASON` 闭集：`USER`（用户显式暂停）、`BUDGET`（任一预算先到）、
`ENVIRONMENT`（环境阻塞，如磁盘空间不足、运行时不可用）。

`pause_detail` 是**可选**的标识式短文本，规则逐字冻结为：

- 长度 `1..200` 字符（空串等同缺失）；
- **禁止**：`/`、反斜杠、`:`、`\t`、`\n`、`\r`、`NUL` 以及其他 C0/C1 控制字符；
  即必须匹配 `^[^\u0000-\u001F\u007F-\u009F/\\:]{1,200}$`（`\u007F-\u009F` 逐字排除 DEL 与 C1）；
- `pause_reason = 'ENVIRONMENT'` 时 `pause_detail` **必须非空**（要能区分是哪一种环境阻塞）；
  `USER`/`BUDGET` 允许省略；
- `detail` **不得**含本地路径（含盘符、UNC、扩展长度前缀的任何拼写）；它不得成为路径、坐标或查询文本的
  旁路。API 层仍按既有统一 `Redactor` 兜底（主架构 §10.3）。

## 4. 目标集（差值集）

### 4.1 权威谓词

目标集 = plan 时**一次性快照**的下述成员，且只使用**既有** revision 口径
（`index_entries.confirmation_revision` 与 `index_outbox.projection_revision`），**不得自造第二套 revision**：

```sql
SELECT a.asset_id
FROM assets AS a
WHERE a.state = 'ACTIVE'
  AND (
        NOT EXISTS (   -- ① 没有「当前 revision 的 CONFIRMED 覆盖」
            SELECT 1 FROM index_entries AS e
            WHERE e.space_id = :space_id AND e.asset_id = a.asset_id
              AND e.generation_id = :generation_id
              AND e.state = 'CONFIRMED'
              AND e.confirmation_revision >= COALESCE(
                    (SELECT MAX(o.projection_revision) FROM index_outbox AS o
                     WHERE o.space_id = :space_id AND o.asset_id = a.asset_id
                       AND o.generation_id = :generation_id), 0))
        OR EXISTS (    -- ② 或该三元组还有未确认的 outbox 行（投影未落定）
            SELECT 1 FROM index_outbox AS o2
            WHERE o2.space_id = :space_id AND o2.asset_id = a.asset_id
              AND o2.generation_id = :generation_id
              AND o2.state <> 'CONFIRMED')
  );
```

即「已落定」（`settled`）的定义是：**存在**当前 revision 的 `CONFIRMED` 条目，
**并且**该三元组**没有**未确认的 outbox 行。目标集是 `settled` 的补集。

三条必须同时成立的性质（companion `$defs/TargetSetPredicate` 逐条为 `const true`）：

1. **`requires_missing_or_stale_confirmed_entry`**：不存在 `CONFIRMED` 条目、或该条目的
   `confirmation_revision` **低于**该三元组的当前权威 revision
   （= `COALESCE(MAX(projection_revision), 0)`），两种情形都算「缺当前覆盖」；
2. **`unsettled_by_unconfirmed_outbox_row`**：只要该三元组还有 `state <> 'CONFIRMED'` 的 outbox 行，
   即使 `CONFIRMED` 条目已经是当前 revision，投影也**未落定**，该资产仍是目标；
3. **`requires_asset_available`**：`assets.state = 'ACTIVE'`。

- `(space_id, processor_id, dimension)` 来自目标 generation 行本身（`index_generations`），
  **不**从 profile 推导维度（冻结的 profile 文档没有 `dimension`）；
- 只为**目标 generation** 判定：目标集是「相对该 profile 的目标代」的差值，不是相对当前活动代的差值；
- 该谓词与 P4-04 的「旧版追赶」共用同一口径，但**本卡不实现追赶**。

### 4.2 plan 快照与幂等

1. plan 时**只算一次**，把结果逐资产写入 `upgrade_targets(run_id, asset_id, input_revision)`，
   `state = 'PLANNED'`，`attempts = 0`；
2. `PRIMARY KEY(run_id, asset_id)` 保证重复 plan **不产生第二行**；重复 plan 必须走
   `INSERT ... ON CONFLICT DO NOTHING`（或等价）而不是先查后写，并**不**重置既有行的
   `state`/`attempts`/`reason`；
3. `target_set_digest` = `sha256(canonical_json({space_id, processor_id, dimension, generation_id,
   targets: [[asset_id, input_revision], ...] 按 asset_id 升序}))`；同一 plan 重放必须得到同一摘要，
   摘要不一致即为**不同的 plan**，必须显式报错而不是合并；
4. **空目标集是合法的**：所有 ACTIVE 资产都已有当前覆盖时，run 不创建任何目标行；
   该 run 的 `SUCCEEDED` 是正常路径（它随后仍要过 §7 门禁 ③）。
5. **plan 快照 ≠ 激活完整性**（F-A）：差值集只在 plan 时算一次；**激活时必须在同一权威事务内
   重新评估当前差值集**（§7 门禁 ③）。plan 之后新出现的 ACTIVE 资产不属于目标集，但只要它缺覆盖，
   激活就必须被拒绝 —— 「该代没有任何 pending 行」**不能**证明「不存在缺覆盖资产」。

### 4.3 `input_revision` 与「已被更新内容取代」的接口（只为 P4-04 留接口）

- `input_revision` 记录 plan 时该资产的 `assets.sha256`；
- 判定接口（本卡**只冻结签名语义，不实现**）：
  `superseded(run_id, asset_id)` 为真 ⇔ 当前 `assets.sha256 != upgrade_targets.input_revision`
  或该资产已不再 `ACTIVE`；
- 本卡不定义「取代后如何补算」，也不允许 P4-03B 顺手实现追赶：P4-03B 只能记录并暴露该判定。

### 4.4 目标行状态机与 `FAILED.reason`

闭集：`PLANNED / IN_FLIGHT / SUCCEEDED / FAILED / CANCELLED`。**允许**的转换恰好是：

`PLANNED→IN_FLIGHT`（领取：生成 `lease_token`/`lease_expires_at`）、`PLANNED→CANCELLED`、
`IN_FLIGHT→SUCCEEDED`、`IN_FLIGHT→FAILED`、`IN_FLIGHT→PLANNED`（**仅限租约仍有效**的进程中断重放：
清空租约、**不增加 `attempts`**）、`FAILED→PLANNED`（显式重试：清空 `reason`、**不增加 `attempts`**）、
`FAILED→CANCELLED`。

**过期租约只有一个接管路径**（F-E）：租约到期后的接管以
`IN_FLIGHT → FAILED(reason = 'LEASE_EXPIRED')` 完成，并按 §4.5 计一次失败；
**不存在**「过期后直接回 `PLANNED`、零计次」的允许路径。

**终态**：`SUCCEEDED / CANCELLED`（两者都不再转换）。必须拒绝的例子：`SUCCEEDED→PLANNED`、
`SUCCEEDED→FAILED`、`CANCELLED→PLANNED`、`IN_FLIGHT→CANCELLED`（取消只作用于未领取的目标：
`IN_FLIGHT` 的取消先走「批次提交 + 回 `PLANNED`」再取消，以保证在途批次可重放）。

`FAILED` **必须**带 §9 闭集里的 `reason`；非 `FAILED` 的行 `reason` 必须为 `NULL`。

### 4.5 同一资产失败后的重试规则

- 目标行**永不删除、永不改 `asset_id`**（唯一键是 `(run_id, asset_id)`）；
- **`attempts` 的唯一自增点**（F-E）：进入 `FAILED` 且该 `reason` 在 §9 映射为
  `counts_toward_attempts = true`（`SOURCE_OFFLINE`/`ARTIFACT_MISSING`/`LEASE_EXPIRED`）。
  阻塞类（`MODEL_WEIGHTS_MISSING`/`VRAM_INSUFFICIENT`/`RUNTIME_UNHEALTHY`）与 run 级
  `DISK_SPACE_INSUFFICIENT` **不**自增；
- 由该唯一自增点直接得到：`FAILED → PLANNED`（显式重试）与 `IN_FLIGHT → PLANNED`（有效租约重放）
  都**不自增**；一次过期租约的接管只在 `IN_FLIGHT → FAILED(LEASE_EXPIRED)` 那一次自增，
  **重放同一接管（同一 `lease_token`）计零**；接管之后的 `FAILED → PLANNED` 是独立的一次显式重试，
  不再计次、也不重复计次；
- 迟到 worker 的结果按既有 `lease_token`/`input_revision`/`processor_id` 校验被拒；
- `MODEL_WEIGHTS_MISSING`/`VRAM_INSUFFICIENT`/`RUNTIME_UNHEALTHY` 属**阻塞**分类：
  它们**不**消耗普通重试预算（不因同一失败配置无限重试），只在运行时资格变化后才可 `FAILED → PLANNED`；
- 重试不得跨越 `run_id`：跨 run 的追赶属 P4-04。

## 5. 预算语义

### 5.1 三个预算字段

| 字段 | 语义 | 边界 |
|---|---|---|
| `budget_targets` | 本 run 允许**成功完成**的目标数上限 | `NULL` = 不限；否则 `1..100000000` |
| `budget_seconds` | 本 run 的**墙钟**运行时间上限（秒） | `NULL` = 不限；否则 `1..86400000`（1000 天） |
| `budget_concurrency` | **一次领取**的目标数上限 | `1..64`，默认 `1`；不新增 worker，不新增 GPU，不并行多进程 |

`0`、负数、超过上限的值在**构造/plan 时**即以既有 `VALIDATION_ERROR`（422）拒绝；
事实表另有 CHECK 兜底。要表达「不限」必须用 `NULL`，不允许用 `0` 或魔法大数。

### 5.2 墙钟与 PAUSED 冻结

账本只有两段（F-C），**必须一起用**，缺一不可：

- **已结账段 `elapsed_ms`**：只在**离开** `RUNNING`（转 `PAUSED`/`CANCELLED`/`FAILED`/`SUCCEEDED`）时
  结账一次：`elapsed_ms += now - resumed_at`，随后 `resumed_at = NULL`、`clock_owner = NULL`；
- **未结账段 `now - resumed_at`**：只在 `state = 'RUNNING'` **且 `clock_owner = 本会话`** 时计入；
- **有效运行时长（判定用）**：
  `effective_elapsed_ms(now) = elapsed_ms + (now - resumed_at if state='RUNNING' and clock_owner=本会话 else 0)`；
- **连续 RUNNING 不得只读 `elapsed_ms`**：§5.3 的预算判定必须用 `effective_elapsed_ms`，否则
  一个 1 秒的预算在「从不离开 RUNNING」的运行里永远不会到限；
- `clock_owner` 是**当前 RUNNING 段的归属会话**：进入 `RUNNING`（`resume` 或重启恢复）时
  写 `resumed_at = now, clock_owner = 本会话`；只有 owner 会话可以结账离开 `RUNNING`；
- **重启与停机**：进程重启后，`clock_owner` 必然是**上一个**会话 ⇒ 恢复事务必须先重锚
  （`clock_owner = 本会话, resumed_at = 恢复时刻`）**才允许领取或改状态**。重锚**不把停机区间计入**
  `elapsed_ms`（允许少记、**不得多记**），也不会重复累计已经结账的段；
- `PAUSED` 期间两段都不动（这就是「暂停期间冻结」的可核对形式）；
- `budget_seconds` 的比较对象是 `effective_elapsed_ms / 1000`，不是进程存活时间、不是 CPU 时间。

### 5.3 判定点与「任一先到即软停」

判定点**只在批次边界**（一次领取批次全部提交之后），不在批次中间：

```
effective_elapsed_ms := elapsed_ms + (now - resumed_at if state = 'RUNNING' and clock_owner = 本会话 else 0)

stop := (budget_targets IS NOT NULL AND completed_targets >= budget_targets)
     OR (budget_seconds IS NOT NULL AND effective_elapsed_ms >= budget_seconds * 1000)
```

到限即：**不再领取新目标**（连一个都不领），已完成保持完成，run 转 `PAUSED(pause_reason = 'BUDGET')`。
`budget_concurrency` **不是**停止条件：它只把单次领取限制为
`min(budget_concurrency, remaining_targets)`，达到上限后 run 继续运行下一批次。

**批次边界的判定顺序逐字冻结**（F-C，避免「最后一格同时完成且到限」的歧义）：
① 若没有任何目标行处于 `PLANNED`/`IN_FLIGHT` **且**没有任何 `FAILED` 行 ⇒ `RUNNING → SUCCEEDED`
（**完成优先于到限**）；② 否则若 `stop` 为真 ⇒ `RUNNING → PAUSED(BUDGET)`；③ 否则继续下一个批次。

### 5.4 到限后的行为（禁止面）

- **不得**删除任何已完成的目标行或产物；
- **不得**把到限写成 `FAILED` 或 `CANCELLED`（它们是不同的终态，且 `CANCELLED` 不可恢复）；
- **不得**把 `PAUSED(BUDGET)` 的目标行标成失败；
- 可 resume 同一 run：`PAUSED → RUNNING`，只处理未 `SUCCEEDED` 的目标行，
  已 `SUCCEEDED` 的行**跳过**且不重复提交（幂等键即 `(run_id, asset_id)`）；
- resume 后预算**不重置**：`budget_targets` 按本 run 的累计成功数比较，`effective_elapsed_ms` 从冻结值继续；
- **本卡不放宽上限，但必须给「同一 run 合法继续」留一条路径**（F-C）：`resume` 请求可以携带
  `grant_targets`（仅当 `budget_targets` 非 `NULL` 时合法）与 `grant_seconds`（仅当 `budget_seconds`
  非 `NULL` 时合法），语义是**增量**：`budget_targets_new = budget_targets_old + grant_targets`、
  `budget_seconds_new = budget_seconds_old + grant_seconds`；每个增量必须 ≥1，且相加后不得超过 §5.1 的
  上界，否则用既有的 `VALIDATION_ERROR`（422）拒绝、**零写入**、run 保持 `PAUSED`；
- 额度授权与 `PAUSED → RUNNING` 在**同一事务**内完成，并作为 `grant_targets`/`grant_seconds` 记录在
  §2.3 的请求行里；它**不重置累计数**（已完成目标数、已结账的 `elapsed_ms`）。**运行段锚点不沿用**：
  `resumed_at`/`clock_owner` 按 §5.2 由**本次**进入 `RUNNING` 的会话新建（F-B2 的措辞纠正）；
- 带 `grant_*` 的 `resume` **只在 `PAUSED → RUNNING` 上合法**：新的、带 `grant_*` 的请求在其状态判定之后
  必须核验**源状态 = `PAUSED`**（F-B2-G）。三种落空形态逐字冻结：
  ① 源状态为 `PLANNED`（或任何非 `PAUSED`）⇒ 既有 `INVALID_STATE_TRANSITION`
  （409，`details.reason = GRANT_SOURCE_STATE_NOT_PAUSED`），**零写入**；
  ② run 已经是 `RUNNING` 时再带 `grant_*` ⇒ 属「目标状态已成立但参数不一致」，按 §3.2 以
  `CONTROL_NOOP_ARGUMENT_MISMATCH` 拒绝；
  ③ 两种情况都**不得**「成功返回并静默丢弃授权」。核验发生在任何 run/日志写入之前；
  **已命中的同义重放不受影响**（§3.2 的重放查找在最前面，成功授权后在 `RUNNING` 重放同一 key 仍是零写重放）。

## 6. 暂停 / 恢复 / 取消

三个控制动作都是**单事务**：`BEGIN IMMEDIATE` → 状态谓词 → 状态更新 + `control_revision + 1` →
`COMMIT`。实现必须复用既有 `catalog/db.py::Catalog.write()` 的单写者事务，不得新增第二个写路径。

| 动作 | 目标状态 | 语义 |
|---|---|---|
| pause | `PAUSED(pause_reason)` | 在**当前批次提交之后**生效；在途批次保持可重放（沿用 outbox 语义）；**不清产物** |
| resume | `RUNNING` | 从持久状态继续；`SUCCEEDED` 目标跳过；不得重复提交；可同时携带 §5.4 的 `grant_targets`/`grant_seconds`（增量，同一事务，**源状态必须是 `PAUSED`**） |
| cancel | `CANCELLED` | 保留已完成产物与 P1～P3 人工事实；**未处理**目标（`PLANNED`/`IN_FLIGHT` 批次回退后的 `PLANNED`）转 `CANCELLED`；**不允许 resume** |

- 非法转换（如对 `CANCELLED` 的 run 求 resume、对终态求 pause）返回既有
  `INVALID_STATE_TRANSITION`（409），`details` 复用既有键（`reason` + `run_id` + `state`），
  **不新增信封字段**；
- 控制请求可幂等重放（§3.2 的顺序：**先按 `(run_id, request_id)` 查找，命中的同义重放不做状态与
  revision 判定**；`changed = 0` 的首次同义请求也要落一行）；
- `PAUSED → RUNNING` 时，凡不是当前 `clock_owner` 的会话都必须先按 §5.2 重锚
  `resumed_at`/`clock_owner`（同一事务），非 owner 会话不得直接搬状态；
- 全局调度器持久暂停时，`PLANNED → RUNNING` 与 `PAUSED → RUNNING` 返回既有 `SCHEDULER_PAUSED`（409），
  run 状态不变、零写入；
- 并发控制请求由 `BEGIN IMMEDIATE` 串行化：不允许出现「两个都成功」或「都失败」的中间态。

## 7. 激活门禁与原子性（六条，失败不写任何行）

激活仍由既有 `search/profile.py::activate_profile` 在**一个** SQLite 事务内完成（指针与
`index_generations.is_active` 一起搬）。本 ADR 在它之上**只增加**前置门禁；激活 API 与
`active_search_profile`/`search_profiles` 的既有语义不变。

| # | 门禁 | 判定 | 不成立的拒绝 |
|---|---|---|---|
| ① | `GENERATION_READY` | 目标 `index_generations.state = 'READY'` | `INVALID_STATE_TRANSITION`，`details.reason = GENERATION_NOT_READY` |
| ② | `RUN_SUCCEEDED` | 本次 `upgrade_runs.state = 'SUCCEEDED'`（完整性策略即此，本卡不引入「部分范围」开关） | `INVALID_STATE_TRANSITION`，`details.reason = RUN_NOT_SUCCEEDED` |
| ③ | `AUTHORITATIVE_COVERAGE_COMPLETE` | **激活时在该代上重新评估当前差值集**（§4.1 谓词，作用于**当前全部 ACTIVE 资产**，而不只是 plan 目标集）必须为**空**；且该代 `COUNT(index_entries.state = 'PENDING_REPAIR') = 0`；且该代 `COUNT(index_outbox.state <> 'CONFIRMED') = 0`（与 `store.py::_make_generation_ready` 同一谓词）。plan 之后新增的 ACTIVE 资产、以及原本已覆盖但已失去覆盖的资产，都在这里被抓到（F-A） | `INVALID_STATE_TRANSITION`，`details.reason = COVERAGE_INCOMPLETE` |
| ④ | `ARTIFACT_READABLE` | 该代每个被引用 `artifacts.storage_uri` 解析得到的文件**被真实打开/读取一次**（不是只做 `is_file()`）；任一读取失败即判不可读（O-2） | `ARTIFACT_MISSING`（409） |
| ⑤ | `REFERENCES_PROTECTED` | run 记录的 `(profile_id, space_id, image_generation_id)` 与当前 `search_profiles` 行逐字相等；既有 `validate_profile_readiness`（运行时 capability + 该 processor 与可选 reranker 的资格证据）通过；目标 generation 未被**已取消的回退任务**引用 | 引用不一致 → `PROFILE_GENERATION_MISMATCH`，`details.reason = PROFILE_REFERENCE_CHANGED`；运行时 → `RUNTIME_CAPABILITY_UNAVAILABLE`，`details.reason ∈ {CAPABILITY_MISSING, RUNTIME_UNHEALTHY, QUALIFICATION_MISSING, RERANKER_QUALIFICATION_MISSING}` |
| ⑥ | 原子性义务 | ①～⑤**任一条**不成立 ⇒ 整体回滚、**零写入**、返回上表的**既有**错误码、**不新增信封字段** | — |

**门禁与指针必须在同一事务内**（O-2）：①～⑤ 的评估、`active_search_profile` 的搬移与
`index_generations.is_active` 的更新必须在**同一个** `BEGIN IMMEDIATE` 事务内完成；**禁止**「先在事务外
门禁、再进入事务写」的两段式实现。任一条件不成立、或提交前发生任何故障 ⇒ 事务回滚、**零写入**。

**门禁⑤复用的既有拒绝逐字保留**（O-1）：`validate_profile_readiness` / `reranker_readiness` /
`resolve_profile_binding` / `check_profile_servable` 产生的错误对象（`code`、`message` 与 `details` 的**键集**）
**逐字沿用**既有 P2-02/P2-06 契约，本 ADR **不做投影、裁剪或重命名**；`$defs/UpgradeApiErrorDetails` 的键集
已按这些真实错误对象逐键枚举，§16 的契约测试直接对真实错误对象逐键核对。**不得新增顶层信封字段。**

门禁 ⑥ 的可核对形式（P4-03B 的 D 级注入必测）：在拒绝路径前后对
`active_search_profile`、`index_generations`（`is_active`、`state`、`updated_at`）、
`upgrade_runs.control_revision` 做**真实 SQLite 前后计数/取值断言**，必须逐项相同；
并另做一次「事务内已搬指针后抛出」的注入，断言指针与 `is_active` 都回到原值（回滚证据）。

## 8. 引用保护边界

generation 的 `RETIRED`/清理**被阻止**，当且仅当该 generation 被以下任一引用持有：

| 引用种类 | 来源 |
|---|---|
| `ACTIVE_PROFILE` | `active_search_profile.profile_id` → `search_profiles.image_generation_id` |
| `LIVE_SEARCH_SESSION` | 进程内检索会话/游标绑定的 `image_generation_id`（TTL 内） |
| `UPGRADE_RUN_TARGET` | `upgrade_runs.image_generation_id` 且 run 未处终态 |
| `ROLLBACK_TASK` | 回退计划（P4-04 引入）持有的 generation 引用 |
| `BACKUP_MANIFEST` | 备份清单（P4-05 引入）登记的 generation 引用 |

- 被拒时返回**既有** `INVALID_STATE_TRANSITION`（409），`details.reason = GENERATION_REFERENCED`，
  `details.blocking_references` 是上表闭集的子集；
- 「默认仅保留前一个版本」：`retain_previous_versions` 默认 `1`，保留时长与空间上限是**配置字段**
  （设置页/配置），本卡只冻结默认值与可配置性，不冻结具体数值；
- 清理**永不**删除人工知识、人工操作历史或原图；清理不得越过引用保护，
  也不得用「先删后报错」的方式让回退计划失去目标；
- 门禁 ⑤ 与本节是同一张引用表的两个方向：⑤ 判「能否切换」，本节判「能否退役/清理」。

## 9. 失败分类映射表（零新增错误码）

`reason` 闭集与其映射**已冻结**（reasons 见 §2.2 的 CHECK；映射如下）：

| `reason` | 既有错误码 | `details.reason` | 记录位置 | 可重试 | 计入 `attempts` |
|---|---|---|---|---|---|
| `MODEL_WEIGHTS_MISSING` | `RUNTIME_CAPABILITY_UNAVAILABLE` | `MODEL_WEIGHTS_MISSING` | 目标行 | 否（阻塞，待能力变化） | 否 |
| `VRAM_INSUFFICIENT` | `RUNTIME_CAPABILITY_UNAVAILABLE` | `VRAM_INSUFFICIENT` | 目标行 | 否（阻塞，待能力变化） | 否 |
| `RUNTIME_UNHEALTHY` | `RUNTIME_CAPABILITY_UNAVAILABLE` | `RUNTIME_UNHEALTHY` | 目标行 | 否（阻塞，待能力变化） | 否 |
| `SOURCE_OFFLINE` | `SOURCE_OFFLINE` | —（无） | 目标行 | 是（来源恢复后） | 是 |
| `ARTIFACT_MISSING` | `ARTIFACT_MISSING` | —（无） | 目标行 | 是（修复路径） | 是 |
| `LEASE_EXPIRED` | `LEASE_EXPIRED`（`STALE_LEASE` 保留给迟到 worker 的拒绝） | —（无） | 目标行 | 是（接管计一次失败） | 是 |
| `DISK_SPACE_INSUFFICIENT` | `DISK_SPACE_INSUFFICIENT` | —（无） | **run 级**（`last_error_code`），并把 run 转 `PAUSED(ENVIRONMENT)` | 是（空间释放后） | 否 |

控制面拒绝（不对应目标行 `reason`）：

| 情形 | 既有错误码 |
|---|---|
| 全局调度器持久暂停，run 不能进入/回到 `RUNNING` | `SCHEDULER_PAUSED` |
| 非法 run/目标行状态转换、被引用 generation 的退役/清理 | `INVALID_STATE_TRANSITION` |

**禁止**：隐式 CPU 降级（ADR-0001/ADR-0003 的设备资格契约）、静默切换 profile 或 generation、
把阻塞写成遮蔽词、把同一失败配置无限重试、新增任何错误码。

## 10. API / UI 最小契约

### 10.1 只读状态入口

- `GET /api/v1/upgrade-runs?limit=&cursor=` → `$defs/UpgradeRunPage`
  （`limit` 默认 50、上限 200；`cursor` 为不透明 keyset）。
- `GET /api/v1/upgrade-runs/{run_id}` → `$defs/UpgradeRunDetail`
  （`targets_limit` 默认 100、上限 500；目标行按 `asset_id` 升序 keyset）。
- 参数层不可解析的 `run_id` → 既有 `VALIDATION_ERROR`（422）；**格式合法但不存在**的 `run_id` →
  既有 `INVALID_STATE_TRANSITION`（409，`details.reason = RUN_NOT_FOUND`）。
  理由：ADR-0002 §9 的最小错误集里没有「升级运行不存在」这一码，而本卡**零新增错误码**；
  P4-03B 若需要 404 语义，必须走 §14 的变更控制申请新码，不得私自添加。
- 投影字段闭集见 `$defs/UpgradeRunProjection` / `$defs/UpgradeTargetProjection`：
  只含状态词、计数、预算、时间戳与 `target_set_digest`；**不含**路径、坐标、查询文本、人工事实。

### 10.2 控制入口

- `POST /api/v1/upgrade-runs/{run_id}/pause`、`/resume`、`/cancel`
  → `$defs/UpgradeControlResponse`。
- 请求体闭集（F-B）：`$defs/UpgradeControlRequest` = 必填
  `{contract_version, request_id, action}` + 可选
  `{expected_control_revision, pause_reason, pause_detail, grant_targets, grant_seconds}`；
  其中 `action ∈ {PAUSE, RESUME, CANCEL}`（对应上面三个小写路径），`pause_reason` 仅在 `PAUSE` 上合法，
  `grant_*` 仅在 `RESUME` 上合法（增量，§5.4）。未知字段一律拒绝。
- 判定顺序（§3.2）：**先查 `(run_id, request_id)`**；同义重放直接返回记录结果
  （`changed = false`、`replayed = true`），**不做**状态谓词与 `expected_control_revision` 判定；
  未命中时才依次判状态谓词与 revision（不匹配 ⇒ 既有 `INVALID_STATE_TRANSITION`，
  `details.reason = CONTROL_REVISION_STALE`），并在**同一事务**内落 §2.3 的行。
  响应的 `changed` 只表示**本次调用**是否写了 run；首次同义 no-op 只落日志行、不改 run，
  参数不一致则按 `CONTROL_NOOP_ARGUMENT_MISMATCH` 拒绝。
- 沿用既有 `POST` + CSRF/session 保护（主架构 §10.3 与 P1-04 的会话契约），本卡不新增权限模型。

### 10.3 错误信封（逐字不变）

失败一律返回现有信封：顶层键**恰好** `code`、`message`、`request_id`（外加可选的既有 `details`），
`X-Request-ID` 头沿用既有约定；`details` 复用既有关键名（`reason`、`field`、`run_id`、`state` 等），
不得新增顶层字段。**`POST /api/v1/search` 的请求/响应信封、`query_hash`、游标与摘要一个字都不改。**

### 10.4 UI

- P4-03B 只新增**只读**升级状态展示（run 状态、预算/进度计数、`PAUSED` 原因、目标行的
  状态与 `reason` 计数），必须在既有页面集合内选择落点（主架构 §10.1），不得新增第二个前端框架；
- 三个控制动作必须有显式确认，且**不得**在 UI 层把 `PAUSED(BUDGET)` 显示成失败/完成；
- **完整设置页（升级/回退/CPU/GPU/覆盖/备份信息）属 P4-06**，本卡与 P4-03B 都不实现它。

## 11. 迁移与兼容契约（为 P4-03B 提供可实现性证明，本卡不实现）

1. 迁移文件：`src/photo_search/catalog/migrations/0017_upgrade_runs.sql`（**本卡不创建**；
   实测目录最大为 `0016_jobs_claim_order.sql`，`0017` 空闲）。内容 = §2 的**三张**表
   （`upgrade_runs`、`upgrade_targets`、`upgrade_control_requests`）及其索引；除这三张表与索引外不得新增对象；
2. `SUPPORTED_SCHEMA_VERSION` **16 → 17**，且 `catalog/schema.py::migration_files()` 的
   连续性要求（`0001`..`0017` 无跳号）继续成立；
3. **只增不改**：`0001`～`0016` 的字节**逐字不变**（§16 的哈希清单由契约测试锁定），
   不重建、不改写任何既有表/索引/触发器；
4. `0017` 只做 SQLite 可事务化操作（建**三张**表 + 索引），`metadata.schema_version` 在同一事务内
   作为最后一步推进（ADR-0002 §7.3）；失败/中断 ⇒ 回滚，版本号保持 16，重启可重放；
5. **没有 down migration**：回滚 = 事务回滚或从迁移前备份恢复（ADR-0002 §7.2），
   不允许「降版本号」或「删表重建」；
6. 只读兼容：更高版本的旧程序照 ADR-0002 §7.4 返回既有 `SCHEMA_TOO_NEW`；
7. **未使用升级功能的旧库行为逐字不变**：`0017` 不改变任何既有查询、状态机、投影、
   搜索信封与错误码；不激活升级功能时，新表的行数为 0，且不参与任何既有读路径；
8. 迁移前备份与磁盘预算沿用 ADR-0002 §7.2（默认 `当前逻辑数据库大小 + 64 MiB`），
   空间不足 → 既有 `DISK_SPACE_INSUFFICIENT`，不得「腾地方」后才发现无法回退（主架构 §11.3.8）。

## 12. P4-03B 的前置验收清单

**D 级六项注入（逐条要真实前后计数/取值）**：

1. 升级中杀进程 → 重启从持久状态恢复（`PLANNED`/`IN_FLIGHT` 批次可重放，无重复逻辑记录）；
2. 暂停 → 恢复（在途批次可重放，`SUCCEEDED` 目标跳过，`control_revision` 每次只 +1）；
3. 预算耗尽中断（`PAUSED(BUDGET)`，`elapsed_ms` 在 `PAUSED` 期间不变，可 resume）；
4. 激活失败原子回滚（门禁 ①～⑤ 逐条注入，断言零写入）；
5. 旧 generation 在切换期间继续服务（切换前后旧 profile 查询不被破坏）；
6. 被引用 generation 的清理被拒（`INVALID_STATE_TRANSITION` + `GENERATION_REFERENCED`）；
7. **控制请求幂等跨重启**（F-B）：`pause(R1) → resume(R2) → 重启 → 重放 R1` 必须返回 R1 的记录结果、
   `replayed=true`、**零写入**且 run 仍为 `RUNNING`；同 key 换 action 拒绝；同 key 并发只落一条；
8. **额度授权继续同一 run**（F-C）：`PAUSED(BUDGET)` 下原额度不能领取任何目标；带 `grant_*` 的 resume
   在同一事务内抬升上限并只处理剩余目标；超出 §5.1 上界的增量被拒且零写入；
9. **激活时重评估差值集**（F-A）：plan 后新增一个无 entry/outbox 的 ACTIVE 资产，激活必须被拒绝且
   指针/`is_active`/`control_revision` 全部不变；补齐后才通过；
10. **门禁与指针同一事务 + 真实读取**（O-2）：在事务内搬完指针后注入异常，断言指针与 `is_active` 回滚；
    artifact 读取失败（不可读而非仅不存在）同样返回 `ARTIFACT_MISSING` 且零写入。

**B 级**：`uv run --no-sync python -m pytest -q tests/unit tests/integration`（JUnit XML）+
`uv run ruff check .` + `git diff --check` + `uv lock --check`。

**C 级**：仅当 P4-03B 改动打包资源（`PhotoSearch.spec`/`tools/packaging/**`）时才加；
本卡预期**不加**。

**BLOCKED（P4-03A 即刻记录，不得用替代数据伪装）**：真实模型权重（项目内无 `data` 目录/固定权重）、
真实升级吞吐与显存、GTX 1660 最低配置——本机为 **RTX 4060**，不得用 RTX 4060 数据或显存限制模拟替代
（主架构 §12.2、V3.1 §9.1）。

## 13. 必须固定的最小反例（边界）

1. `CANCELLED → RUNNING`、`SUCCEEDED → RUNNING`、`FAILED → RUNNING`、`PAUSED → SUCCEEDED`：
   全部拒绝（终态或未在允许表内）；
2. `pause_detail` = `"C:\\photos"`、`"a/b"`、`"a\nb"`、长度 201：全部拒绝；
   `pause_reason = ENVIRONMENT` 且 `pause_detail = NULL`：拒绝；
3. `budget_targets = 0` / `-1` / `100000001`、`budget_seconds = 0`、`budget_concurrency = 65`：全部拒绝；
   `NULL` 与边界值 `1`/上限值：接受；
4. 目标行 `state='FAILED'` 且 `reason IS NULL`：拒绝；`state='PLANNED'` 且 `reason` 非空：拒绝；
5. 到限后把 run 写成 `FAILED` 或 `CANCELLED`：拒绝（必须是 `PAUSED(BUDGET)`）；
6. 同一 plan 重复执行产生第二行：被 `PRIMARY KEY(run_id, asset_id)` 拒绝；
7. 「已在该代有当前 `CONFIRMED` 覆盖」的资产进入目标集：拒绝（谓词必须只选缺覆盖的）；
8. 门禁 ①～⑤ 逐条不成立时的**任何写入**：拒绝（前后计数必须相同）；
9. 被活动 profile / 在途会话 / 未终态 runs / 回退任务 / 备份清单引用的 generation 被清理：拒绝；
10. 错误信封出现第四个顶层键、或新增错误码：拒绝；
11. plan 后新增、且在该代尚无 entry/outbox 的 ACTIVE 资产存在时执行激活：拒绝（F-A，门禁 ③ 必须
    在该代上重新评估**当前**差值集，而不是只核对 plan 目标集）；
12. `pause(R1) → resume(R2) → 重放 R1`：返回 R1 记录结果、`replayed=true`、run 状态与
    `control_revision` 不变（F-B）；同一 `request_id` 换成另一个 action：拒绝；
13. `changed=false` 的首次同义请求**必须落一行**（`grant_*` 为 `NULL`）；把它当成「不写行」即违约（F-B）；
14. `PAUSED(BUDGET)` 且原额度已耗尽时直接 resume：可以切到 `RUNNING`，但 `claim_batch_size` 必须为 0，
    不得因此声称「可继续处理剩余目标」（F-C）；只有带 `grant_*` 的 resume 才允许继续；
15. `grant_targets` 加在 `budget_targets IS NULL` 的 run 上、或增量使上限超过 §5.1 上界：拒绝（F-C）；
16. 连续 `RUNNING` 且从未离开 RUNNING 时，用 `elapsed_ms` 单独判定预算：违约；必须用
    `effective_elapsed_ms`（F-C）；同一段停机被计入 `elapsed_ms`：违约（重锚不得多记）；
17. 同一过期租约被重放：`attempts` 第二次不得再 +1；`FAILED → PLANNED` 显式重试：`attempts` 不变（F-E）；
18. 把某 `reason` 配上合法但错误的 `error_code`/`location`/`retryable`/`counts_toward_attempts`，
    或把某门禁配上合法但错误的 `refusal_code`/`details_reason`，或门禁数组重复/缺项：拒绝（F-D）；
19. `pause_detail` 含 `U+0085`/`U+009F`（C1）：拒绝（O-3）；
20. 重放任一控制请求后返回完整的 `changed=true, replayed=true` 响应：拒绝 —— 响应 `changed` 只表示
    「本次调用是否写了 run」，重放必须是 `changed=false, replayed=true`（F-B1）；
21. 真正的首次同义 no-op（目标状态已成立 + 参数一致）改写了 run 的**任何**列（含 `updated_at`、
    `pause_reason`、`pause_detail`）：违约；只允许插日志行，run 行必须逐列不变（F-B2）；
22. `PAUSED(BUDGET)` 上再 `pause(USER)`，或 run 已是 `RUNNING` 时 `resume` 带 `grant_*`：拒绝
    （`INVALID_STATE_TRANSITION` + `CONTROL_NOOP_ARGUMENT_MISMATCH`），零写入且不落日志行（F-B2）；
23. 把额度授权解释成「沿用旧的 `resumed_at`/`clock_owner`」：违约；锚点必须按 §5.2 由本次进入
    `RUNNING` 的会话新建，只有累计数（已完成数、已结账 `elapsed_ms`）不被重置；
24. `PLANNED`（或任何非 `PAUSED`）的 run 携带 `grant_targets`/`grant_seconds` 执行 `resume`：拒绝
    （`INVALID_STATE_TRANSITION` + `GRANT_SOURCE_STATE_NOT_PAUSED`），run 整行与日志行数都不变（F-B2-G）；
    `PAUSED` + `grant_*` 的两种正例仍成功，成功后再用同一 key 在 `RUNNING` 重放仍是零写重放。

## 14. 变更控制

本 ADR 与 companion 的任何字段名、闭集、错误码映射、谓词或门禁语义的改动都属于**契约变更**，
必须走 ADR 变更控制：在新的修订轮里给出最小反例、影响面与「预期 / 实际」差异，并重新走 Sol 聚焦终审。
`0001`～`0016` 的字节、既有 ADR/companion 语义、`POST /api/v1/search` 的**任何**字节，
都不在本 ADR 的可变更范围内。

## 15. 状态记录

- **阶段 A（DS-H，2026-09-27）**：本 ADR 状态为 **Proposed**；companion 与契约测试已建立，
  契约测试与 ruff/diff-check/lock-check 已跑（见 [P4-03A 阶段 A 报告](../reports/P4-03A-dsh-contract-draft.md)）。
  任务状态写 `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`；
  **不得**标 P4-03A PASS、**不得**转 Accepted、**不得**生成 `docs/tasks/P4-03B.md`；
- **阶段 B 第一轮（C2 / GPT-5.6 Sol / high，2026-09-27）**：判 **FAIL / CONTRACT FREEZE PENDING**
  （见 [P4-03A 阶段 B 报告](../reports/P4-03A-sol-contract-freeze.md)）。五处阻断：
  **F-A** plan 后新增 ACTIVE 资产缺覆盖但无 pending 行时门禁仍放行；**F-B** `request_id` 幂等没有持久载体
  与请求 schema；**F-C** 预算耗尽后 resume 无法继续、且运行中墙钟记账未闭合；
  **F-D** companion 只限制词汇、未约束 reason/门禁与错误码的组合，也未要求矩阵成员完整；
  **F-E** 过期租约同时被允许「零计次重放」与「计一次失败」。报告 O-1（readiness 错误 details 闭合）、
  O-2（事务/真实读取证据）、O-3（C1 字符）一并列为修订面；
- **修订面（DS-H，2026-09-27，本轮）**：按上述边界最小修订本 ADR、companion 与契约测试，
  并新增 §2.3（持久幂等表）、§5.2 的时钟账本、§5.4 的额度授权与 §7 的「同一事务 + 真实读取」；
  证据见 [P4-03A 修订面报告](../reports/P4-03A-dsh-repair.md)。修订面**只覆盖上述失败边界与直接回归**；
- **阶段 B 后续修订轮（历史）**：只短审上述修订面与直接回归，不做全量重推；
  通过后才把本 ADR 转 **Accepted**、标 P4-03A PASS 并生成 `docs/tasks/P4-03B.md`；
- 阶段停止点：Sol 不通过时只输出裁决、最小反例与回退范围，交回 DS-H 修复后只短审原失败边界；
  **ADR 在通过前保持 Proposed，且不得被实现或引用为已冻结契约。**

**§2.2 升级判定**：本卡未命中 V3.1 §2.2 六类具名条件（跨模块根因不明 / transaction·revision·undo 互扰 /
测试与 ADR 不符 / 一次修复引发多处回归 / 大 diff 多状态机耦合 / Sol 要求大范围反查）。
阶段 B 若提出需要大范围反查，按第 6 条升 **DS-MAX**。

**最终冻结（2026-09-27，第四轮聚焦短审）**：F-B2-G 源状态前置条件与 replay 优先已闭合；
独立契约 JUnit 33/0/0/0、数量/秒数授权正负及重放探针均通过，Ruff/lock-check/diff-check 通过。
本 ADR 转 **Accepted**，**P4-03A PASS（仅契约冻结）**；生成 P4-03B 任务卡但不启动实现。
最终报告：[P4-03A 阶段 B §8](../reports/P4-03A-sol-contract-freeze.md)。
`0017` 未实现，schema_version 仍为 16，真实迁移/D 级/模型/最低配置验证仍待 P4-03B 与后续任务。

## 16. 伴随物、哈希与契约测试

- `docs/decisions/contracts/p4-upgrade-contracts.v1.json`
  SHA-256：`f92dece59b1a16e896af68a2c0484b5b061ce004e62fbe3245a6827dea4469e8`
- 既有伴随物在本 ADR 下**逐字节不变**（由契约测试锁定哈希）：`p2-contracts.v1.json`、
  `p2-place-contracts.v1.json`、`p3-acceptance-matrix.v1.json`、`p3-face-correction-contracts.v1.json`、
  `p3-face-redetection-contracts.v1.json`、`p3-person-search-contracts.v1.json`、
  `p3-person-split-contracts.v1.json`、`p4-a3-contracts.v1.json`、`p4-time-contracts.v1.json`。
- `0001`～`0016` 迁移文件的 SHA-256 由契约测试锁定；`0017` 在 P4-03A 下**必须不存在**。
- `tests/unit/test_p4_upgrade_contracts.py` 固定：companion 的合法性与闭合性（含无孤立定义）、ADR 记录的哈希、
  run/目标行闭集与转换矩阵（含全部负例）、`PAUSE_REASON` 与 `detail` 规则（含 C1 负例）、
  目标行 `FAILED.reason` 与租约列的 `IN_FLIGHT ↔ lease_*` 规则、`reason` ↔ 错误码的**逐值组合**
  （合法词汇但错配即拒绝；矩阵成员重复或缺项即拒绝）、预算边界与「任一先到」正反例、
  到限 ⇒ `PAUSED(BUDGET)`、批次边界判定顺序、`effective_elapsed_ms` 与重锚（含「不得多记停机」）、
  取消不可 resume、控制请求幂等的持久语义（digest、重放优先于 revision、`changed=0` 也落行）、
  额度授权算术与上界、目标集差值谓词（**真实 SQLite 行**）、plan 幂等（唯一键）、
  激活时重评估**当前**差值集（plan 后新增资产必须被拒）、六条门禁逐条正反例与**零写入**断言、
  门禁与指针同一事务（事务内故障回滚）、artifact **真实读取**失败路径、
  readiness 既有拒绝的 `details` 键集 ⊆ 冻结键集、引用保护、错误信封未新增顶层字段、
  `0017`/`16 → 17`/`0001`～`0016` 只读断言。

## 17. 后果与未验证项

- **收益**：升级运行第一次有唯一、可实现、可拒绝的持久语义；预算、暂停/恢复/取消都是持久事实；
  激活沿用一个事务与既有错误码；引用保护使回退目标不会被顺手清理；
- **代价**：目标集必须在 plan 时算一次并落库（30 万资产的 plan 成本留给 P4-03B 实测）；
  预算上限（1 亿 / 1000 天）是保守上界，不是已测容量；
  「RUN_NOT_FOUND ⇒ 409」是为零新增错误码而做的保守取舍（见 §10.1）；
- **代价（F-A 的直接后果）**：门禁 ③ 在激活时按**当前**差值集判定，因此「持续有新资产进库」的库在
  不冻结写入的情况下**无法激活**；切换前必须有一个「补齐最后增量 + 短暂停止写入」的协调窗口，
  或在未来由**另一张卡**冻结「明确隔离部分范围」的策略。本卡不提供该开关，也不放宽门禁；
- **代价（F-B/F-C/F-E 的直接后果）**：`0017` 需要第三张表，控制面从「无状态动作」变成「有持久幂等日志的
  动作」；`upgrade_runs` 多一个 `clock_owner` 列、`upgrade_targets` 多两个租约列；
  resume 请求多两个可选增量字段。这些都属本卡新增面，不触碰任何既有 ADR/companion；
- **未验证（不得当成 PASS）**：真实权重与真实文本塔上的升级吞吐/显存、GTX 1660 最低配置、
  `0017` 在真实 30 万库上的迁移耗时、plan 目标集在 30 万资产下的耗时、真实模型切换窗口内的前台争用、
  40 万扩容、崩溃窗口内重建的幂等、P4-04 的旧版追赶与回退演练；
- **F2 未关闭、A3 未实现未启用、§12.2 未改、P4-02F 非性能 PASS** —— 本 ADR 不改变这四项状态。

## 18. 失败与回退

- 若冻结契约（ADR-0002/0003/0009/0011 等）无法支持 §2～§11 的语义，或必须在 P4-03A 内改生产代码/
  迁移才能自洽：**停止**，给出最小矛盾与影响面，转独立协议/工程裁定，不在本卡夹带实现；
- 本 ADR 与其 companion 在阶段 B 通过前**不得**被实现或引用为已冻结契约；
- 真实模型权重/GTX 1660 缺失 ⇒ 相关条目如实记 **BLOCKED**，不得用 RTX 4060 数据替代。
