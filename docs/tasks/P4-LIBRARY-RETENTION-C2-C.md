# P4-LIBRARY-RETENTION-C2-C — DELETE_VECTOR 生产消费与 LanceDB 重放

**状态**：PASS（限定 producer/consumer/Lance 重放范围）；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：承接 C2-A、C2-B 工作树改动；不提交，不访问真实图库。
**目标**：屏障建立后，为该资产每个可能含向量的 generation 持久化 `DELETE_VECTOR` 事件；实际删除 Lance 向量并确认 SQLite outbox。崩溃、重复运行和旧 UPSERT 重放必须 fail-closed 且幂等。本阶段不删 SQLite `assets`/`artifacts` 或人工记录。

## 必读范围

- V3.0 §5.1–5.2、§6、§7、§10.2、§11.2/11.4、§12.4–12.5；V3.1 §2.4、§3.2/3.4、§4.2、§11。
- `P4-LIBRARY-RETENTION-C2-A.md`、C2-A/B 报告与任务卡。
- 直接实现：`indexing/store.py`、`indexing/maintenance.py`、`artifacts/store.py`、`retention.py`、`catalog/migrations/0003_artifact_outbox_index.sql`、向量代际和维修/恢复入口，以及上述路径的全部相关测试。

## 本卡冻结语义

1. 复用既有 `index_outbox.event_type` 的 `DELETE_VECTOR` 合同；不得修改已发布 migration。若证明合同不足以支持 DELETE，停止并在新 ADR/卡内给出最小迁移设计，不得暗改历史 migration。
2. 只对具有 ACTIVE/COMPLETE retention barrier 的 asset 接受 DELETE_VECTOR；普通 UPSERT 仍被 barrier 拦截。DELETE 按 generation 隔离，record identity 不能跨 generation/space 误删。
3. 生产者必须纳入 `index_entries`、pending/in-flight UPSERT 及故障后外部写入尚未确认的 generation；删除事件与回执身份稳定、可重复计算。旧 UPSERT 不可在删除后再次发布。
4. Lance delete 成功与 SQLite outbox CONFIRMED 之间可崩溃。重启时对同一个事件重复执行删除必须成功；不存在目标 row 视为已删除，不得将 `IN_FLIGHT` 静默确认或丢弃。
5. SQLite 对应索引引用/回执只在外部 delete 后提交确认；失败留持久可重放状态并报告。该阶段不删除 artifact、index_entries 或 asset 行。

## 性能设计

- 链路与风险：屏障资产跨 generation 生产 tombstone，再逐事件调用 Lance delete；风险是全库/整代扫描及对每条向量调用全表读取。
- 做法与边界：按单 asset SQL 索引读取 generation/事件集合；Lance delete 使用 `record_id` 与 `generation_id` 精确谓词，不物化全量向量或重建 generation；每次确认有界批次。
- 验证与停止：用隔离 Lance 根和合成多 generation/重复/故障事件；核对删前/删后逐代行数、重复回放及 SQLite 回执；出现全表 Python materialization、跨代删除或不可恢复状态即停止，不跑 30 万基准。

## 验证与审查

- 首先证明历史 schema 0003 已允许 DELETE_VECTOR；确认不需要 migration；若需修改 schema，按 `docs/database-migrations.md` 执行新库、冻结旧库升级、失败回滚，并按 V3.1 风险执行完整回归。
- 必须使用项目真实 `LanceVectorIndex` 与隔离临时数据目录验证，不可只用 mock/fake index 代替；注入 delete 后、SQLite 确认前故障并重启协调器重放，覆盖不存在 row 幂等、相同事件重复、旧 UPSERT、多个 generation/space。
- JUnit XML 保留实际退出码；Ruff、Python 3.12 编译、`git diff --check`。如无 UI 改动不需浏览器；不访问真实照片库、不加载模型/GPU、不运行大库测试。
- 用户要求的独立 Luna High 子代理复核完整受影响面、外部写入/确认事务竞态、重复回放与证据边界。

## 实施结果

- Schema 0003 已允许 `DELETE_VECTOR`；未修改历史 migration，也未新增 schema migration。
- `activate_asset_retention_cleanup()` 在同一调用事务中建立持久屏障并调度每个已知 `(space_id, generation_id)` 的稳定 DELETE_VECTOR；C2-D/F 必须从实际清理入口调用该 helper。
- `process_next()` 和 `process_batch()` 对真实 Lance generation table 按精确 `record_id` 幂等删除；delete 后、SQLite 确认前崩溃时保持 `IN_FLIGHT`，重启重复执行再确认。旧 UPSERT、修复扫描和投影刷新受屏障阻挡。
- 真实 Lance 合成验收覆盖同一资产 2 个 space、3 个 generation、重复调度、单条/批量确认前故障重放、逐代删除与旧 UPSERT 保持不可领取。
- 受影响集成 JUnit：56 passed、0 failed、0 errors、0 skipped；独立 Lance 定向 JUnit：2 passed、0 failed、0 errors、0 skipped。Ruff、Python 3.12 语法检查、`git diff --check` 通过。
- 独立 Luna High 只读复核：多 space 覆盖和 C2-C 原子 producer helper 已闭合，无新增 blocker；指出后续 C2-D/F 仍需将 helper 接入真实清理入口。未把当前阶段报告为端到端清理验收。
- 未触及真实图库、未运行 GPU/大库测试、未提交或推送。

详情见[执行报告](../reports/P4-LIBRARY-RETENTION-C2-C.md)。

## 排除范围

- 不删除 SQLite assets/artifacts、OCR/GPS/face/人物/合集/收藏/人工记录或任何用户原图。
- 不做 artifact/cache 清扫或 GC-A/B（C2-D）；不解除屏障、不开放一键清理或自动调度（C2-F）。
