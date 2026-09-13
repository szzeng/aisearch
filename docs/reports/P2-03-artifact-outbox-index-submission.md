# P2-03 — 通用产物、outbox、幂等索引提交与修复报告

日期：2026-09-13。状态：**PASS（D 级：完整 unit/integration + 具名故障注入/恢复）**。

本任务由用户在 Codex 中手动选择 GPT-5.6 Sol / high 执行；没有自动切换模型、没有启动其他
Agent。全部数据库、artifact 与 LanceDB 夹具均位于 pytest 的 `%TEMP%` 目录，没有读取、移动或
修改真实图库，没有网络下载。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `56a26adfeb2687a96b71a431133f96b1ab3fba42`（P2-02） |
| 开始前工作树 | clean；`master...origin/master [ahead 1]` |
| 前置 migration | 只有 0001/0002，`SUPPORTED_SCHEMA_VERSION = 2` |
| 新 migration | `0003_artifact_outbox_index.sql`；schema_version 2 → 3 |
| 新生产包 | `src/photo_search/artifacts/**`、`src/photo_search/indexing/**` |
| 调度接入 | `Scheduler.submit` 的双重 lease/身份核对、受控 staging、单事务 artifact/outbox/job 终态 |
| API | `/stats` 与 `/capabilities` 增加只读、无路径的 indexing 摘要 |

未修改：ADR-0001～0005、`p2-contracts.v1.json`、`p2-place-contracts.v1.json`、0001/0002、
`pyproject.toml`、`uv.lock`、模型 manifest、`workers/**`、打包/启动入口、地点/SearchProfile/ANN。

## 2. schema_version 3

0003 新增四组权威对象：

- `artifacts`：`artifact_id` 主键 +
  `(processor_id,input_revision,artifact_type,artifact_revision)` 逻辑唯一键；embedding 与非向量的
  space/dimension 组合由 CHECK 固定；只保存数据根内相对 URI；生命周期为
  `PENDING/PUBLISHED/RECLAIMED`。
- `index_outbox`：含 space/asset/generation/projection revision、规范 JSON/checksum、
  `PENDING/IN_FLIGHT/CONFIRMED`、attempts 和稳定错误码；逻辑事件唯一。
- `index_entries`：每 `(space_id,asset_id,generation_id)` 唯一，只有 SQLite `CONFIRMED` 才是
  `SEMANTIC_READY`；缺件使用 `PENDING_REPAIR`。
- `index_generations`：`PENDING/READY/RETIRED`，同一 space 最多一个活动 READY generation。

真实 v2→v3 用例证明：先生成 schema_version=2 备份，旧 asset/job 每列不变，再单事务推进到 3；
注入损坏 0003 时半表整体回滚并保持 v2。既有 migration 套件继续覆盖缺目标对象拒写、higher schema
零写副作用及 fresh 0001→0002→0003 连续性。

## 3. artifact 发布与提交事务

- worker 不能指定绝对/相对存储位置。structured/embedding 使用规范 JSON；binary 使用严格 base64；
  主进程在事务外校验 checksum、byte_size、有限向量、dimension 和整体协议上限。
- 目标 URI 全由 artifact ID/checksum 推导并限制在 `PhotoSearchData/artifacts/pending`；先同目录临时
  文件 + flush/fsync，再在已经重新核对 lease/identity/device 的写事务中 `os.replace`。
- 同逻辑键不同 checksum/ID 为 `ARTIFACT_CONFLICT`；同维不同 space 或 processor dimension 不符为
  `SPACE_MISMATCH`。迟到/过期 lease 在 staging 前拒绝。
- 事务同时写 artifact、embedding outbox、PENDING_REPAIR index entry 和 job 终态；注入发布后、
  COMMIT 前异常时 SQLite 全回滚，已发布但未引用文件被清除，同一 lease 重放后各生成一行。
- `artifacts=[]` 正常 SUCCEEDED；structured/binary 会持久化 artifact，但不创建向量 outbox。

## 4. LanceDB 幂等确认与修复

锁定环境实测 `lancedb 0.38.0` 的原生
`merge_insert(...).when_matched_update_all().when_not_matched_insert_all().execute(...)`，实现按 generation
使用固定 dimension 表；没有使用先删后加，也没有创建 ANN。

处理顺序为：SQLite 领取 outbox → 重新读 artifact/generation/当前 asset 可见性和来源投影 → 校验
payload checksum 与 space → 原生 merge → SQLite 确认 index_entries/outbox → generation READY。
在 merge 后、SQLite 确认前注入崩溃时，事件保持 IN_FLIGHT、READY 仍不可见；重启重放后 Lance 和
index_entries 各只有一条逻辑记录。重复顺序 replay 同样保持一条。

`schedule_projection_refresh(connection, asset_ids, projection_revision, now_ms)` 是供 P2-03A 及后续
人工知识事务复用的公共入口：它与权威 revision 在同一 SQLite 事务排入已有向量的刷新事件；没有
已确认向量的资产无需事件，首次索引会读取当时最新 SQLite。晚到 revision-0 事件被 revision-1 压过，
不会覆盖最新投影或错误恢复 READY。

`audit_and_schedule_repairs` 会从现存 embedding artifact 重建被删/损坏的确认；缺失或损坏 artifact
标为 RECLAIMED 并要求确定性 re-execution。`republish_reclaimed` 不把旧 SUCCEEDED job 非法转回
QUEUED，而是核对相同 checksum 后恢复派生产物，再走新 outbox；测试证明原 job 仍为 SUCCEEDED，
修复完成。活动 generation、未确认 outbox、index_entries 引用均保护产物；临时/孤立文件只有超过
宽限期且无引用才会清理。

## 5. D 级场景映射

| # | 实测闭环 |
|---|---|
| 1 | v2→v3、备份、旧行逐列不变；中断回滚；既有缺件/higher-schema 用例继续通过 |
| 2 | 临时文件残留宽限；checksum/size/storage URI 错误零 PUBLISHED/outbox/READY |
| 3 | artifact/outbox/job 终态写入后注入 COMMIT 前崩溃：全回滚；同 lease 重放各一行 |
| 4 | SQLite 已提交后 outbox 可重放；Lance 与 index_entries 各一行 |
| 5 | Lance 已落盘、SQLite 未确认：READY=false；重启确认后 true |
| 6 | 重复提交和过期 lease 携 artifact：零新增持久行；新 lease 正常提交 |
| 7 | structured/binary/embedding 都真实发布；空结果不写 artifact/outbox |
| 8 | 同维不同 space：`SPACE_MISMATCH`，无 artifact/outbox/Lance 行 |
| 9 | 删除 index_entries 后从 artifact 修复；回收后同一结果再执行可恢复，旧 SUCCEEDED 不阻塞 |
| 10 | fresh temp、有引用/活动 generation/未确认 outbox 被保留；aged orphan 才删除 |
| 11 | `/stats`、`/capabilities` indexing 字段无本机路径；既有 scheduler/jobs/higher-schema 套件通过 |
| 12 | 同进程连续两次 merge/replay 后逻辑行数仍为 1；晚到旧投影不回滚新 revision |

专项文件：`tests/unit/test_p2_artifacts.py`、
`tests/integration/test_p2_index_submission.py`、
`tests/integration/test_p2_index_repair.py`，共 17 个用例。

## 6. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run pytest -q tests/unit/test_p2_artifacts.py tests/integration/test_p2_index_submission.py tests/integration/test_p2_index_repair.py` | exit 0，17 passed |
| `uv run pytest -q tests/unit tests/integration` | exit 0，433 collected，432 passed / 1 skipped |
| `uv run ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv lock --check` | exit 0，`Resolved 95 packages` |
| 锁定 Lance API 探针 | 0.38.0；`merge_insert`/update-all/insert-all/execute 均存在并由专项真实写入验证 |

最终完整测试是在生产代码冻结后重跑；成功输出采用 quiet 模式，只保留进度、exit code 与收集数量。

## 7. C 级判定

未修改 `workers/**`、正式运行环境、打包/启动入口或依赖，也未新增独立 package-data 目录。0003 位于
现有 migrations package-data 目录，冻结包的 `--collect-data photo_search` 机制和“0001..supported
缺任一脚本即 SCHEMA_INCONSISTENT”保护保持不变。因此本任务未触发 C 级 onedir smoke。

## 8. 未验证项

1. 没有常驻 indexing 线程、跨进程并发写、ANN 构建/维护或真实模型 embedding；本卡只用确定性
   合成向量和同进程顺序重放。
2. P2-03 的 binary 走跨进程 JSON base64，因此实际还受 `MAX_JOB_RESULT_CHARS=262144` 限制；大型
   worker staging 引用需要未来单独冻结受控 URI 协议，当前不接受 worker 路径。
3. generation ID 在 P2-03 由 space+processor 稳定派生，首次全确认后自动成为该 space 的唯一活动
   generation；SearchProfile 组合激活、升级/回退和多代切换仍属于 P2-06/P4。
4. repair/reclaim 是内部服务接口，尚无写 API；本任务只新增只读诊断。
5. P2-02 记录的真实设备/常驻调度、跨进程单写压力及真实端口 smoke 仍未新增承诺。

## 9. 结论与交接

P2-03 的 artifact/outbox/LanceDB/index_entries 崩溃可重放闭环已按 D 级真实验收，因此标记 PASS。
下一任务是 `P2-03A`：在 schema_version=3 和本任务投影刷新接口上，实现 0004 地点核心 migration、
GPS 事实、地点目录、人工归属/取消锁/恢复自动、批量/撤销和纯 SQLite 地点筛选/API；不提前实现
P2-03B 地名包/附近城市建议或 P2-03C 页面。
