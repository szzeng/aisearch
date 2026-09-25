# P4-03 准备包 — UpgradeRun 契约输入与开工前核对

**用途**：作为 [P4-03](../tasks/P4-03.md)（UpgradeRun/targets、预算、暂停恢复、profile 原子激活）
**契约阶段（P4-03A）**的输入。本文只做只读核对与裁决记录，**不改产品代码、不新增迁移**。
基线：`aa33f65`（P4-02J 实现提交）；`SUPPORTED_SCHEMA_VERSION = 16`，migrations 最大 `0016` ⇒
**`0017` 空闲**（开工时须再复核一次）。**A3 仍未实现/未启用；P4-02F 非性能 PASS；§12.2 未改。**

## 1. 开工前核对结果（卡面要求"必须实测确认"的项）

| 卡面假设 | 实测 | 结论 |
|---|---|---|
| 下一个迁移编号是 `0017` | `SUPPORTED_SCHEMA_VERSION = 16`，目录最大 `0016_jobs_claim_order.sql` | ✅ 0017 空闲（开工再复核） |
| 是否已有 UpgradeRun / 升级表 | `rg -i upgrade src` 只命中 `0010_face_core.sql` 的无关字串 | ✅ 全部要新建 |
| `search_profiles` 结构 | 0007：`profile_id` PK；`image_generation_id` → `index_generations` 且 **`ON DELETE RESTRICT`**；`fusion_config_json` 闭集校验；可选 reranker | ✅ 引用保护已有一半 |
| `index_generations` 状态机 | 0003：`state ∈ {PENDING,READY,RETIRED}`；`CHECK(is_active=0 OR state='READY')`；每 space 至多一个 active（partial unique index） | ✅ 激活前不变量由 DB 保证 |
| 原子激活地基 | `search/profile.py::activate_profile` 已是单事务同时搬 active 指针与 `is_active`（注释明确不会半移动） | ✅ 只需补"引用完整"门禁 |
| 既有错误码可复用 | `RUNTIME_CAPABILITY_UNAVAILABLE` / `SOURCE_OFFLINE` / `LEASE_EXPIRED` / `STALE_LEASE` / `ARTIFACT_MISSING` / `DISK_SPACE_INSUFFICIENT` / `SCHEDULER_PAUSED` / `INVALID_STATE_TRANSITION` 均存在 | ✅ 无需新增错误码 |
| 真实权重 / GTX 1660 | 项目内无 `data` 目录/固定权重；本机为 RTX 4060 | ❌ 真实升级吞吐与显存 **BLOCKED**，本卡只做契约 + 确定性注入 |

## 2. 五条裁决（P4-03A 契约输入）

1. **目标资产集（差值集，显式落库）**：`upgrade_targets` 每行 `(run_id, asset_id, input_revision)`，
   唯一键 `(run_id, asset_id)` 幂等重放；**plan 时快照一次**，集合 = 当前 ACTIVE 资产中在目标 profile 的
   `(space_id, processor_id, dimension)` 下**尚无当前 revision 的 `CONFIRMED` 覆盖**的资产。
   带 `input_revision` 供 P4-04 的"旧版追赶"判断目标是否已被更新内容取代。
2. **预算 = 上限，任一先到即软停**：`budget_targets`（NULL=不限）、`budget_seconds`
   （**墙钟，PAUSED 期间冻结**，按累计 `elapsed_ms` 记录）、`budget_concurrency`（一次领取目标数，
   **不新增 worker/多 GPU**）。到限 ⇒ 不再领取新目标、已完成保持完成、run 转
   **`PAUSED(reason=BUDGET)`**（**用户 2026-09-26 已确认：到预算走 PAUSED，而非终止**），可 resume 同一 run。
3. **暂停=软停、取消=终态不可恢复**：暂停在**当前批次提交后**生效，在途批次保持可重放（沿用 outbox 语义），
   不清产物；恢复从持久状态继续、已 `SUCCEEDED` 目标跳过；取消保留已完成产物与人工事实、未处理目标标
   `CANCELLED`，且**不允许 resume 已取消的 run**。三个控制动作都是单事务改状态 + `control_revision`
   自增（`BEGIN IMMEDIATE` + 状态谓词），非法转换返回既有 `INVALID_STATE_TRANSITION`。
4. **"引用完整"门禁（激活前必须全部成立；失败不写任何行）**：
   ① 目标 generation `state='READY'`；② 本次 run 已 `SUCCEEDED`（或满足冻结的完整性策略）；
   ③ 该空间权威覆盖完整（目标集逐资产 `index_entries.state='CONFIRMED'` + `projection_revision` 为当前版本、
   `PENDING_REPAIR = 0`、该代 outbox 无未确认）；④ 引用 artifact 存在且可读（`ARTIFACT_MISSING` 结构化拒绝）；
   ⑤ 引用保护：被活动 profile / 在途查询 / UpgradeRun / 回退任务引用的 generation 不得 retire；
   ⑥ 失败即整体回滚，返回既有错误码，**不新增信封字段**。本清单须写成 companion matrix + 契约用例。
5. **失败分类零新增错误码**（复用既有闭集 + 冻结 `reason` 枚举）：模型缺失 →
   `RUNTIME_CAPABILITY_UNAVAILABLE{reason=MODEL_WEIGHTS_MISSING}`；来源离线 → `SOURCE_OFFLINE`（记在目标行）；
   显存不足 → `RUNTIME_CAPABILITY_UNAVAILABLE{reason=VRAM_INSUFFICIENT}`（或 `DEVICE_POLICY_CONFLICT`）；
   租约过期 → `LEASE_EXPIRED`/`STALE_LEASE`（可重试，takeover 计一次失败）；产物缺失 → `ARTIFACT_MISSING`；
   磁盘/暂停/非法转换 → `DISK_SPACE_INSUFFICIENT`/`SCHEDULER_PAUSED`/`INVALID_STATE_TRANSITION`。

## 3. 待冻结的枚举草案（P4-03A companion）

- **run 状态**：`PLANNED / RUNNING / PAUSED / CANCELLED / FAILED / SUCCEEDED`（闭集，非法转换拒绝）。
- **PAUSE_REASON**：`USER / BUDGET / ENVIRONMENT` + `detail` 字符串（上限与内容规则由契约定死）。
- **目标行状态**：`PLANNED / IN_FLIGHT / SUCCEEDED / FAILED / CANCELLED`；`FAILED` 必须带冻结 `reason`。
- **失败 `reason` 闭集**：`MODEL_WEIGHTS_MISSING / VRAM_INSUFFICIENT / SOURCE_OFFLINE / ARTIFACT_MISSING /
  LEASE_EXPIRED / DISK_SPACE_INSUFFICIENT / RUNTIME_UNHEALTHY`（与既有错误码组合使用）。

## 4. 执行顺序与边界

- **先收口 P4-02J**（`asset_id` 覆盖维护，30 万档复测进行中；它**不加迁移**，0017 完整留给 P4-03）。
- P4-03A（契约冻结）→ Sol high 聚焦终审冻结 → P4-03B（`0017` 迁移 + `src/photo_search/upgrade/**` +
  API/UI + 六项 D 级注入）→ **DS-H 完整受影响面主审**（P4-03 卡面 `Sol 必选`，不得记 `SOL_NOT_REQUIRED`）。
- **BLOCKED（开工即记）**：真实模型权重、真实升级吞吐/显存、GTX 1660 最低配置；只做契约与确定性注入验证。
- 禁止面照 P4-03 卡面：`0001`～`0015` 内容、既有 ADR/companion 语义、信封/`query_hash`/游标/错误码、
  RRF/BM25/距离、P1～P3 人工事实、打包面、`pyproject.toml`/`uv.lock`；不得引入 Redis/Celery、
  分布式调度、多 GPU、第二向量后端；不得顺手做 P4-04/P4-05/P4-07。
