# ADR-0002 — 图库身份、迁移、扫描与最小持久任务契约

- 日期：2026-09-11
- 决策状态：**ACCEPTED / FROZEN FOR P1-01 IMPLEMENTATION**
- 所属任务：P1-01 — 资产 / 位置 / 来源 / 扫描最小持久任务 / 迁移与约束
- 决策角色：C2 / GPT-5.6 Sol / high
- 基线：P0-07 已 PASS；P1-01 尚未进入 DS-H 实现
- 依据：`个人图片搜索系统_V3.0_开发主架构.md` §4.1～4.4、§5.1～5.3、§6.2～6.3、§11.1～11.2、§13.1、§15.4；`个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md` §3、§4、§6、§11～§13；`docs/tasks/P1-01.md`
- 本 ADR 只冻结 P1-01 的高风险语义；不实现 P1-02 的完整扫描算法，不改 AI / worker / 向量空间契约。

---

## 1. 决策摘要

P1-01 的实现必须遵守以下不可变边界：

1. **资产身份是内容身份。** `asset_id` 是本地稳定 ID；只有完整 SHA-256 才能创建或命中永久资产身份。文件名、目录、盘符、mtime、部分 hash 都不是资产身份。
2. **位置与资产分离。** 一个资产可有多个 `file_locations`；改名/移动不改变资产身份；同一路径内容替换只重新绑定位置，不覆盖或删除旧资产。
3. **来源身份不等于根路径。** `source_id` 是稳定来源身份；`root_path` 是当前映射，可在未来显式重映射。盘符变化不得通过批量替换绝对路径伪装成身份变化。
4. **负向结论必须晚于完整成功扫描。** 扫描中断、来源离线、权限失败、路径碰撞或其他未完整覆盖情况都不得把未见文件判为 `MISSING`。只有完整扫描成功的最终提交事务可以生成缺失结论并推进 `last_successful_scan`。
5. **迁移必须原子、可拒绝、可恢复。** `schema_version` 是数据库写入兼容性的权威版本。更高版本数据库只允许安全只读诊断，必须拒绝写入；禁止删库重建或“重新扫描”代替迁移。
6. **持久任务由数据库而非内存队列定义。** 逻辑任务唯一键为 **5 个字段**
   `(subject_type, subject_id, input_revision, task_type, processor_id)`，五者全部 `NOT NULL`。任务卡中“四要素”是文字笔误，不改变本契约。
7. **租约是提交资格。** 每次领取产生新的 `lease_token`；心跳、增量结果和最终提交都必须校验当前 token、未过期租约、`input_revision` 与 `processor_id`。迟到结果不得覆盖新执行。
8. **原图永远只读。** 扫描、stat、hash、格式探测不得写入、重命名、移动、删除或修改用户文件及其元数据。

本 ADR 一旦进入 DS-H 实现阶段，DS-H 不得自行修改上述语义。若实现发现必须改变其中任一项，应停止该局部实现并交 C2 复核。

---

## 2. 统一存储约定

### 2.1 SQLite 与时间格式

- 主库位置沿用架构：`<data_root>/catalog/catalog.db`；运行数据不得写入安装目录。
- P1-01 使用标准库 `sqlite3` 和显式 SQL 迁移，不引入 ORM 迁移框架。
- 所有数据库连接必须启用 `PRAGMA foreign_keys = ON`。
- 业务写入由单写协调器串行提交；文件枚举、stat、hash、解码或其他慢 I/O 不得在 SQLite 写事务内执行。
- 除 `mtime_ns` 外，P1-01 新建表中的机器时间统一使用 **UTC Unix epoch milliseconds** 的 `INTEGER`。
- `mtime_ns` 保存文件系统返回的原始纳秒整数，不转换为毫秒。
- 稳定本地 ID 使用 `INTEGER PRIMARY KEY AUTOINCREMENT`，避免删除后复用已经暴露过的 ID。

### 2.2 删除策略

P1-01 不提供物理删除资产或来源的普通业务路径。涉及身份关系的外键默认 `ON DELETE RESTRICT`，不使用级联删除来“清理”资产、位置或扫描历史。

机器生成数据可以在后续阶段重建，但稳定身份和未来人工知识不得因实现便利被级联删除。

---

## 3. 最小表契约

本节冻结字段语义、唯一性、空值边界与首版状态；DS-H 可以增加不改变语义的普通索引，但不得改变主键、唯一键或身份含义。

### 3.1 `metadata`

| 字段 | 契约 |
|---|---|
| `key` | `TEXT PRIMARY KEY` |
| `value` | `TEXT NOT NULL` |

必须存在 `key='schema_version'`。其 `value` 保存十进制整数文本，例如 `1`。

P1-01 当前支持版本为 **1**。

`metadata.schema_version` 是版本权威来源；不得以表是否存在、`PRAGMA user_version` 或代码猜测代替。

### 3.2 `source_roots`

| 字段 | 契约 |
|---|---|
| `source_id` | `INTEGER PRIMARY KEY AUTOINCREMENT`，稳定来源身份 |
| `display_name` | `TEXT NOT NULL`，仅展示，不参与身份 |
| `root_path` | `TEXT NOT NULL`，当前显式映射的绝对 Windows 路径 |
| `volume_hint` | `TEXT NULL`，仅辅助诊断/重映射，不参与唯一身份 |
| `status` | `TEXT NOT NULL`，P1-01 值：`ONLINE / OFFLINE / ERROR` |
| `last_successful_scan` | `INTEGER NULL`，语义为最近一次完整成功的 `scan_runs.scan_id` |

约束：

- `source_id` 不随盘符、挂载点或根目录显式重映射而改变。
- `last_successful_scan` 只能指向同一 `source_id` 且 `state='SUCCEEDED'` 的扫描。
- 扫描失败、阻塞、取消或中断不得推进 `last_successful_scan`。
- `volume_hint` 不能用于自动认定“这是同一个来源”，只能作为未来重映射提示。
- 注册来源时必须在单写事务内检查与现有来源的相等、父子重叠关系；重叠默认拒绝。

### 3.3 `scan_runs`

| 字段 | 契约 |
|---|---|
| `scan_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `source_id` | `INTEGER NOT NULL` → `source_roots.source_id` |
| `started_at` | `INTEGER NULL`，真正开始执行时填写 |
| `finished_at` | `INTEGER NULL`，终态填写 |
| `state` | `TEXT NOT NULL` |
| `error` | `TEXT NULL`，失败/阻塞诊断的紧凑 JSON 文本 |

P1-01 扫描状态：

- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `BLOCKED`
- `FAILED`
- `CANCELLED`

状态语义：

- `QUEUED`：已建立持久扫描请求，尚未开始枚举。
- `RUNNING`：逻辑扫描仍在进行；允许任务级短暂重试，但尚未产生完整扫描结论。
- `BLOCKED`：外部条件阻塞，例如来源离线；不产生缺失结论。
- `FAILED`：当前扫描请求失败；不产生缺失结论。
- `CANCELLED`：当前扫描请求被取消；不产生缺失结论。
- `SUCCEEDED`：完整覆盖本次来源范围，且最终缺失对账与成功标记已经在同一最终事务中完成。

必须保证一个来源最多只有一个非终态扫描请求。P1-01 推荐以 SQLite 部分唯一索引约束：
`source_id` 在 `QUEUED / RUNNING / BLOCKED` 中唯一。

重复调用“发起扫描”时，如该来源已有非终态 `scan_run`，返回现有扫描，不再创建第二个并发逻辑扫描。

#### `scan_runs` 与 `jobs` 状态联动

- `scan_runs` 表示用户可见的逻辑扫描生命周期；`jobs` 表示执行调度生命周期。两者职责不同，不合并为一套状态。
- P1-01 的 `catalog_scan` job 首次进入 `RUNNING` 时，对应 `scan_run` 必须从 `QUEUED` 进入 `RUNNING`。
- job 进入 `SUCCEEDED / BLOCKED / FAILED / CANCELLED` 时，对应 `scan_run` 必须在同一业务提交路径进入同名终态（其中 `SUCCEEDED` 仍须满足 §5.2 的最终原子提交条件）。
- job 的 `RETRY_WAIT` 仅表示调度退避；对应 `scan_run` 继续保持 `RUNNING`，不增加 `RETRY_WAIT` 扫描状态。
- `BLOCKED -> QUEUED` 恢复调度时，对应 `scan_run` 从 `BLOCKED` 恢复为 `RUNNING`，继续同一个逻辑扫描身份，不新建第二个 `scan_run`。

### 3.4 `file_locations`

| 字段 | 契约 |
|---|---|
| `location_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `source_id` | `INTEGER NOT NULL` → `source_roots.source_id` |
| `relative_path` | `TEXT NOT NULL`，保留展示拼写的来源相对路径 |
| `path_key` | `TEXT NOT NULL`，Windows 比较键 |
| `asset_id` | `INTEGER NULL` → `assets.asset_id` |
| `size` | `INTEGER NOT NULL CHECK(size >= 0)` |
| `mtime_ns` | `INTEGER NOT NULL CHECK(mtime_ns >= 0)` |
| `last_seen_scan_id` | `INTEGER NULL` → `scan_runs.scan_id`，且必须属于同一来源 |
| `availability` | `TEXT NOT NULL`，P1-01 值：`PRESENT / MISSING` |

核心约束：

`UNIQUE(source_id, path_key)`

语义：

- `location_id` 是“某来源下已记录位置”的行身份，不是内容身份。
- `asset_id` 在完整 hash 未确认前必须允许为空。
- 观察到同一路径 size/mtime 发生变化时，必须先把该位置视为内容待确认；不得继续把旧 `asset_id` 当作当前位置已确认内容。
- 完整 SHA-256 确认后，资产 upsert 与位置绑定必须在同一短事务中完成。
- 改名/移动必须保证 `asset_id` 不变。实现若能无歧义证明是同一位置迁移，可以更新原位置行并保留 `location_id`；若无法无歧义证明，可创建新位置并在后续完整成功扫描后把旧位置标记 `MISSING`。**业务正确性不得依赖 location_id 跨改名稳定。**
- 同一路径内容替换时允许保留该 `location_id`，但必须改绑到新内容对应的 `asset_id`；旧资产不覆盖、不删除。
- 来源离线时不得批量修改现有位置的 `availability`。

### 3.5 `assets`

| 字段 | 契约 |
|---|---|
| `asset_id` | `INTEGER PRIMARY KEY AUTOINCREMENT`，稳定内容身份 |
| `sha256` | `TEXT NOT NULL UNIQUE`，64 位小写十六进制完整 SHA-256 |
| `byte_size` | `INTEGER NOT NULL CHECK(byte_size >= 0)` |
| `width` | `INTEGER NULL` |
| `height` | `INTEGER NULL` |
| `format` | `TEXT NULL` |
| `created_at` | `INTEGER NOT NULL` |
| `state` | `TEXT NOT NULL`；P1-01 只写 `ACTIVE` |

冻结规则：

- `sha256 + byte_size` 一经创建不得就地修改。内容变化必须创建或复用另一个资产。
- `sha256` 是唯一内容键；`byte_size` 是一致性校验，不是第二套身份。
- 同一个 SHA-256 命中已有资产时，若观察到 `byte_size` 与已有值不一致，视为数据库/输入完整性异常，必须报错，不得静默覆盖。
- `width / height / format` 是内容派生信息，不参与身份；P1-01 可以为空。媒体是否能解码也不得改变 SHA-256 内容身份。
- 0 字节候选文件的 SHA-256 仍是合法内容身份；是否属于有效图片由后续媒体阶段判定。
- “所有位置都缺失”不等于资产删除；资产和未来人工知识仍保留。

### 3.6 `jobs`

字段按任务卡完整保留：

`job_id, subject_type, subject_id, input_revision, task_type, processor_id, state, priority,
attempts, max_attempts, run_after, lease_token, lease_expires_at, worker_id, progress,
last_error_code, last_error_message, created_at, updated_at`

冻结约束：

- `job_id`：`INTEGER PRIMARY KEY AUTOINCREMENT`。
- `subject_type / subject_id / input_revision / task_type / processor_id` **五个字段全部 `NOT NULL`**。
- 逻辑唯一键：
  `UNIQUE(subject_type, subject_id, input_revision, task_type, processor_id)`。
- `subject_id` 使用 `TEXT`，以容纳不同 subject 类型的稳定 ID；P1-01 的扫描任务保存十进制 `scan_id` 字符串。
- `input_revision` 与 `processor_id` 创建后不可修改。
- `priority`：整数，数值越大优先级越高。
- `attempts`：已失败或因租约丢失而消耗的执行次数，初始 `0`。
- `max_attempts`：默认 `3`，必须大于 0。
- `run_after`：UTC epoch ms，初始为创建时间。
- `progress`：`0.0～1.0`。
- `lease_token / lease_expires_at / worker_id`：仅 `RUNNING` 时允许非空；离开 `RUNNING` 时同一事务清空。
- `last_error_code / last_error_message`：最近一次阻塞/失败诊断；成功后可清空。

P1-01 扫描任务固定身份：

- `subject_type = 'scan_run'`
- `subject_id = str(scan_id)`
- `input_revision = '1'`
- `task_type = 'catalog_scan'`
- `processor_id = 'catalog-scan@1'`

“发起扫描”必须在同一事务中创建 `scan_runs` 和对应 `jobs`，避免只有一边存在。

---

## 3.7 数据库约束与应用事务约束的边界

能由 SQLite 直接、清晰表达的约束必须下沉到数据库层，包括 `PRIMARY KEY`、`NOT NULL`、`UNIQUE`、普通外键与局部 `CHECK`。

跨行、跨状态或需要同时检查多张表当前值的语义，由 catalog 单写事务强制并以测试锁死；P1-01 不为此引入复杂 trigger。典型例子包括：

- `source_roots.last_successful_scan` 必须指向同一 `source_id` 且 `scan_runs.state='SUCCEEDED'`；
- `file_locations.last_seen_scan_id` 必须属于同一来源；
- `scan_runs` 与 `jobs` 的状态联动；
- 完整扫描成功时缺失对账、扫描终态和 `last_successful_scan` 的原子更新。

实现不得因为这些语义不能被单个普通外键完整表达，就降低约束或改写身份模型。

---

## 4. Windows 路径与 `path_key`

### 4.1 展示路径与比较键分开

`root_path` / `relative_path` 保留用户和文件系统可识别的展示拼写；唯一性只依赖 `path_key`。

P1-01 的 `path_key` 必须由**来源相对路径**产生，不包含盘符和 `root_path`。因此未来来源根目录显式重映射不会导致全库位置键变化。

### 4.2 固定算法

对来源相对路径按以下顺序生成 `path_key`：

1. 输入必须是相对路径；拒绝带驱动器、UNC 根或绝对路径的输入。
2. `/` 统一为 `\`。
3. 使用 Windows 词法规则执行 `ntpath.normpath`。
4. 拒绝结果为 `.`、以 `..` 逃逸来源根或包含非法根跳转的路径。
5. 使用 `ntpath.normcase` 生成大小写不敏感的比较键。
6. **不执行 Unicode NFC/NFKC 归一化。**
7. **不调用 `resolve()`，不把符号链接/目录联接目标路径写入 key。**
8. `\\?\` 仅可作为 I/O 层长路径传输前缀，不得进入 `root_path`、`relative_path` 或 `path_key` 的持久语义。

如果真实文件系统中出现两个不同目录项却得到相同 `path_key`，必须报告
`PATH_KEY_COLLISION` 并使本次扫描不能进入 `SUCCEEDED`，不得把两者静默合并。

### 4.3 来源重叠

注册来源时使用同一套 Windows 词法比较语义比较绝对根路径，按路径组件边界判断：

- 完全相同 → 拒绝。
- 新来源是已有来源祖先 → 拒绝。
- 新来源是已有来源子目录 → 拒绝。

错误必须返回冲突的现有 `source_id`，便于 UI 未来给出合并/重映射提示。

若 `<data_root>` 等于来源或位于来源目录树内部，也必须拒绝该来源，避免扫描自己的数据库、缓存和模型目录。

### 4.4 符号链接与目录联接

- 默认不递归进入目录符号链接、junction 或其他会把枚举带出当前目录树的 reparse-point 目录。
- 若用户要扫描其目标，目标目录必须作为独立来源显式注册。
- P1-01 不通过“跟随链接后再去重”解决来源边界问题。

---

## 5. 扫描事务与缺失判定边界

### 5.1 正向事实可以增量提交，负向事实不可以

扫描进行中允许安全提交以下“正向事实”：

- 看到了某个位置；
- 该位置当前 stat 为某个 `size / mtime_ns`；
- 完整 SHA-256 已验证并可绑定到某个资产；
- 同内容在另一个位置出现。

这些事实即使扫描随后失败也仍然是真实观察，可以保留。

以下属于“负向事实”，**只能在完整成功扫描的最终阶段产生**：

- 某个以前存在的位置本次没有看到；
- 该位置应标记 `MISSING`；
- `source_roots.last_successful_scan` 应推进到当前 `scan_id`。

### 5.2 完整成功的原子边界

一次扫描只有满足以下全部条件才允许进入 `SUCCEEDED`：

1. 来源在本次定义的完整范围内完成枚举；
2. 未发生导致覆盖不完整的来源离线、权限失败、路径键碰撞、未处理的目录枚举错误；
3. 本次需要提交的正向位置/资产写入已完成；
4. 基于当前 `scan_id` 的缺失对账已完成；
5. `scan_runs.state='SUCCEEDED'`、`finished_at`、`source_roots.last_successful_scan=scan_id`
   与缺失对账在同一个最终 SQLite 事务中提交。

如果最终事务失败，扫描保持非成功状态；不得出现“版本/状态已成功但缺失对账只做了一半”的结果。

P1-01 只需为该规则建立最小可验证闭环；完整稳定等待、hash 前后校验、移动识别和生产级缺失算法仍属于 P1-02。

### 5.3 来源离线与中断

- 扫描开始前发现根目录不可达：`source_roots.status='OFFLINE'`，扫描/任务进入 `BLOCKED`；现有位置不变。
- 扫描过程中来源消失或覆盖范围无法完整枚举：本次扫描不得 `SUCCEEDED`；现有未见位置不变。
- 权限/访问失败：记录结构化错误，扫描不得 `SUCCEEDED`。
- 进程被杀、机器重启或执行者失联：持久 job 保留；租约到期后允许恢复执行。恢复可以重新枚举当前 `scan_run`，不要求 P1-01 实现目录级断点游标。
- 重启恢复不得创建第二个并发扫描请求；同一来源的非终态 `scan_run` 继续作为逻辑扫描身份。

### 5.4 同路径内容变化

当已知位置的 size/mtime 与当前观察不一致：

1. 在短事务内更新该位置观察值并把 `asset_id` 置空，表示内容身份待重新确认；
2. 在事务外计算完整 SHA-256；
3. 提交前再次确认该位置仍对应本次观察；若输入已变化，丢弃本次 hash 结果并重新排队；
4. 在一个短事务中 upsert 资产并把位置绑定到确认后的 `asset_id`。

P1-02 会补齐稳定等待和更完整的 hash 前后校验。P1-01 不得因为尚未实现 P1-02 就保留已明确过期的旧资产绑定。

---

## 6. Job 状态机、租约与迟到提交

### 6.1 合法状态迁移

普通迁移：

```text
QUEUED     -> RUNNING | CANCELLED
RUNNING    -> SUCCEEDED | RETRY_WAIT | BLOCKED | FAILED | CANCELLED
RETRY_WAIT -> QUEUED | CANCELLED
BLOCKED    -> QUEUED | CANCELLED
```

`SUCCEEDED / FAILED / CANCELLED` 在 P1-01 中为终态。新的扫描请求创建新的 `scan_run + job`；不复用已成功/失败的扫描身份。

特殊恢复操作：

```text
RUNNING(expired lease) -> RUNNING(new lease)
```

这不是普通自循环。只有旧租约已经过期、当前事务确认还没有其他新租约时才允许原子接管，并必须生成新的 `lease_token`。

非法迁移必须返回结构化错误，不得静默接受。

### 6.2 `attempts` 语义

- 初始 `attempts = 0`。
- 普通可重试执行失败一次，`attempts += 1`。
- 发现过期租约并准备接管时，把上一个丢失执行计为一次失败，`attempts += 1`。
- `BLOCKED` 不消耗普通重试次数。
- 当失败次数达到 `max_attempts` 时进入 `FAILED`，不得无限重试同一失败配置。
- 确定性永久错误可以直接 `FAILED`，无需把计数耗完。

### 6.3 领取

领取必须在一个短 SQLite 写事务中完成：

1. 选择可运行 `QUEUED` 任务，或按恢复规则处理过期 `RUNNING`；
2. 验证 `run_after <= now`；
3. 写 `state='RUNNING'`；
4. 生成新的高熵 `lease_token`；
5. 写 `lease_expires_at` 与 `worker_id`；
6. 提交后才允许执行事务外 I/O。

### 6.4 心跳

心跳更新必须至少匹配：

- `job_id`
- `state='RUNNING'`
- 当前 `lease_token`
- `lease_expires_at > now`

心跳只能延长当前租约；旧 token、已过期 token 或非 RUNNING 状态返回 `LEASE_CONFLICT`，不得重新激活任务。

### 6.5 结果提交资格

任何会改变持久业务结果的 worker/执行者提交，都必须在写事务内重新校验：

- `job_id`
- `state='RUNNING'`
- `lease_token` 与当前行完全一致
- `lease_expires_at > now`
- `input_revision` 与任务创建时一致
- `processor_id` 与任务创建时一致

任一不匹配时，本次结果是迟到/过期结果，**不得写资产绑定、扫描成功、进度终值或其他覆盖性结果**。

建议错误分类：

- `LEASE_CONFLICT`
- `STALE_LEASE`
- `STALE_INPUT_REVISION`
- `PROCESSOR_MISMATCH`

“旧 worker 最后完成”永远不能覆盖“新 worker 已重新领取后的结果”。

---

## 7. Schema 迁移契约

### 7.1 版本发现

迁移器启动时按以下规则判断当前版本：

1. 数据库文件不存在：版本 `0`。
2. 数据库存在且没有业务表：版本 `0`。
3. 存在 `metadata` 且 `schema_version` 为合法整数：使用该值。
4. 数据库已经存在业务表但没有合法 `schema_version`：返回 `SCHEMA_INCONSISTENT`，拒绝猜测、删库或自动重建。
5. `schema_version > 程序支持版本`：返回 `SCHEMA_TOO_NEW`，拒绝所有写入。

P1-01 当前最大支持版本为 `1`，迁移文件从 `0001_initial.sql` 开始，版本号必须连续、不得跳号。

### 7.2 迁移前保护

对已有非空数据库执行任何升级前：

1. 停止/阻止新的业务写入；
2. 检查目标数据卷剩余空间；
3. 用 SQLite backup API 创建一致性备份到数据根的备份目录；
4. 备份成功后再执行迁移。

P1-01 的默认磁盘预算至少覆盖：

`当前逻辑数据库大小 + 64 MiB 迁移余量`

后续若某个迁移需要更多临时空间，必须由该迁移显式提高预算，不能降低到不足以保存完整备份。

新建空库不要求为“空数据库”创建无意义备份，但仍必须通过最小磁盘空间检查。

### 7.3 原子迁移

P1-01 的迁移只允许使用 SQLite 可事务化操作：

- 每个版本升级在受控事务内执行；
- `metadata.schema_version` 必须作为该迁移的最后逻辑步骤，在同一事务内推进；
- 任一 SQL 失败或进程在提交前终止，事务回滚，版本号保持旧值；
- 重启后从旧版本重新执行，结果必须幂等。

P1-01 **禁止**引入不能被事务保护的迁移步骤。未来若确实需要非事务迁移，必须另做 C2 决策并引入显式可恢复阶段标记。

### 7.4 更高版本数据库

当数据库版本高于当前程序支持版本时：

- 可以进行不会写库的版本/健康诊断；
- 所有业务写入口必须返回 `SCHEMA_TOO_NEW`；
- 不得自动删除表、复制部分数据到新库、降低版本号或提示用户“重新扫描即可”。

已有稳定 ID 和未来人工知识优先于“让旧程序勉强启动”。

---

## 8. 事务原子性最低要求

以下组合必须是单事务原子操作：

1. 新建 `scan_run` + 对应 `catalog_scan` job。
2. 完整 hash 命中/创建 `asset` + 把当前 `file_location.asset_id` 绑定到该资产。
3. 任务领取：状态 + 新 `lease_token` + 过期时间 + worker。
4. 任务离开 `RUNNING`：目标状态 + 清空租约字段 + 错误/进度更新。
5. 完整扫描最终提交：缺失对账 + `scan_runs=SUCCEEDED` + `finished_at` + `source_roots.last_successful_scan`。
6. 同路径内容确认替换：新/既有资产选择 + 位置重新绑定。

事务中禁止：

- 文件枚举；
- 文件 hash；
- 图片解码；
- 模型推理；
- HTTP；
- 长时间 sleep / 重试等待。

---

## 9. 结构化错误最小集合

P1-01 的内部服务/CLI 至少区分以下错误码：

- `SOURCE_NOT_FOUND`
- `SOURCE_OFFLINE`
- `SOURCE_OVERLAP`
- `DATA_ROOT_INSIDE_SOURCE`
- `ACCESS_DENIED`
- `PATH_KEY_COLLISION`
- `DISK_SPACE_INSUFFICIENT`
- `SCHEMA_TOO_NEW`
- `SCHEMA_INCONSISTENT`
- `INVALID_STATE_TRANSITION`
- `LEASE_CONFLICT`
- `STALE_LEASE`
- `STALE_INPUT_REVISION`
- `PROCESSOR_MISMATCH`

错误对象至少包含：

```text
code
message
details (可选)
```

CLI `--json` 必须把错误作为结构化结果并返回非零退出码；不得只在 stdout 打印一行文字后返回成功。

`scan_runs.error` 保存适合诊断的紧凑 JSON 文本，至少保留 `code` 与 `message`；不得把个人图片内容、OCR 原文或不必要的隐私数据写入错误字段。

---

## 10. 人工知识与机器状态边界

P1-01 尚未创建人物、标签、人工注释、OCR、向量或 outbox 表，但必须为后续阶段保留以下不变量：

- 人工知识只能挂在稳定内容/人物等业务身份上，不能依赖临时任务或不稳定路径。
- 机器重新计算、模型升级、重新扫描不得覆盖人工字段。
- 资产与位置不得合并为一张“照片表”。
- 来源离线、位置缺失是可用性状态，不得改写资产内容身份。
- 后续新增人工表时，不得通过级联删除资产来清理机器产物。

---

## 11. P1-01 实现模型可自行决定的内容

在不改变本 ADR 语义的前提下，DS-H 可以自行决定：

- Python 模块拆分与类/函数命名；
- 普通非唯一索引；
- 查询 SQL 的具体写法；
- 日志格式；
- 测试夹具组织方式；
- 连接池/只读连接的轻量封装；
- 备份文件名中的时间格式；
- 租约时长、心跳间隔等可配置数值，只要测试能可靠覆盖过期与续租；
- P1-01 临时夹具中的候选文件集合。

以下内容 **不得自行决定或修改**：

- `asset_id / source_id` 的身份含义；
- 完整 SHA-256 唯一内容身份；
- `(source_id, path_key)` 唯一性；
- `path_key` 的来源相对、Windows 词法、无 Unicode 归一化原则；
- `last_successful_scan` 的 scan_id 语义；
- “只有完整成功扫描才能产生缺失结论”；
- jobs 的五字段唯一逻辑键及非空性；
- job 状态迁移和迟到提交校验；
- schema 高版本拒绝写入、迁移原子性和备份原则；
- 原图只读；
- P1-01 不提前创建人物、向量、FTS、outbox、索引世代表。

若实现必须改变上述任一项，停止局部实现并交 C2 更新本 ADR。

---

## 12. 对 P1-01 验收的直接要求

DS-H 实现完成后，C2 聚焦复核至少检查：

1. `assets.sha256` 真正由数据库唯一约束保护，不只是 Python 先查后插。
2. `UNIQUE(source_id, path_key)` 真正在数据库层生效。
3. 五字段 job 逻辑键全部非空，未受 SQLite `NULL` 唯一性漏洞影响。
4. 迁移失败不会出现“表只建了一半但 schema_version 已推进”。
5. 更高 schema 版本不会触发删库/重建。
6. 扫描中断/来源离线不会把未见位置批量标 `MISSING`。
7. 完整扫描成功的缺失对账与 `last_successful_scan` 是同一最终事务。
8. 同字节多位置只产生一个资产。
9. 同路径换内容只改位置绑定，旧资产仍存在。
10. 旧 `lease_token`、过期 token、旧 revision、错误 processor 的提交都不会写入结果。
11. 中文、空格、长路径 fixture 使用真实 Windows 路径语义测试；若长路径或 reparse-point 行为受本机/权限影响，按任务卡升级 C1，不由 DS-H 改契约。
12. 所有测试只使用临时夹具，不读取真实照片库。

---

## 13. 明确延后到 P1-02 或以后

本 ADR 不要求 P1-01 提前实现：

- 稳定等待窗口；
- 生产级 hash 前后双重 stat 策略；
- watchdog 增量提示；
- 可靠的同盘 file-id 移动加速；
- 大规模缺失对账性能优化；
- decode pool / thumbnail/subsample pipeline；
- 缩略图与预览；
- HTTP sources/assets API；
- FTS / LanceDB / outbox / index generation；
- 人脸、OCR、人物人工状态；
- 模型推理调度与 GPU 设备资格。

P1-01 只实现足够验证本 ADR 的最小持久闭环；允许使用**最小、确定性的扫描实现**来验证数据库不变量与恢复语义，该扫描实现不等于最终生产扫描算法。P1-02 再补齐稳定等待、生产级 hash 前后校验、移动/缺失判定与规模化扫描行为。不得因为“以后会做”而削弱当前身份、事务、租约或迁移约束。

---

## 14. 决策后果

优点：

- 文件改名、重复、多盘、断盘、替换和未来重映射不会混淆内容身份。
- 扫描失败只会留下可证明的正向观察，不会制造大批误删除。
- SQLite 崩溃恢复和任务重领具有明确提交资格，迟到执行者无法覆盖新结果。
- 数据库升级失败不会以“重新扫描”牺牲稳定 ID 和未来人工知识。
- P1-02～P3 可以在不重排基础身份的前提下继续扩展。

代价：

- 同一路径内容变化时宁可暂时解除旧资产绑定，也不展示可能错误的内容关系。
- 来源重映射、同盘移动优化和完整缺失算法需要后续任务继续实现。
- 更严格的路径碰撞策略在极少数 Windows case-sensitive 目录中会选择报错而不是猜测合并。
- 版本迁移会占用额外备份空间，这是保护长期数据的必要成本。

该取舍与本项目“数据可靠优先于便利和吞吐”的主架构原则一致。
