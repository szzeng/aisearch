# P4-LIBRARY-RETENTION-C2-A — 可信失效观察与批量异常保护

**状态**：PASS（限定 C2-A，组合证据）；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：`master @ 6626e4f`；实施前 HEAD 与远端 `origin/master` 相同（本地领先 5 个提交），工作树干净。
**目标**：仅为以后清理建立可信的缺失观察证据和来源批量异常保护；本卡不执行逻辑/物理删除，不开放清理按钮或自动删除。

## 必读范围

- `个人图片搜索系统_V3.0_开发主架构.md`：§3.2–3.3、§4、§5.1–5.2、§6、§7、§10.2、§11.2/11.4、§12.4–12.5。
- `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`：§2.4、§3.2/3.4、§4.2、§11。
- 实现：`catalog/store.py`、`ingestion/scanner.py`、`catalog/migrate.py`、`catalog/schema.py`、`library_retention.py`、`api/routes.py`、现有存储 UI。
- 验证：`tests/integration/test_catalog_scan.py`、`tests/integration/test_p4_library_retention.py`、`tests/unit/test_catalog_migrations.py`及相关 fixtures。

## 本卡冻结的证据语义

1. 观察起点只能来自来源的完整成功扫描最终事务。不得使用 `assets.created_at`、EXIF、文件 mtime、旧 `MISSING` 标记或扫描开始时间回填。
2. 对每个资产，所有当前登记且曾保存该资产位置的来源都必须在观察起点建立前各自完成一次成功扫描；扫描后仍为 MISSING、无 PRESENT 位置、来源 ONLINE 时，最后一个来源确认时刻才成为新的 `first_missing_at`。无位置资产不进入观察。
3. 任意来源出现 PRESENT 时立即撤销观察资格；来源离线/错误、扫描失败/中断、来源登记变更、异常批次或引用状态未知均阻止并重置资格。升级前已有 MISSING 只有在上述新成功扫描证据齐备后才能开始 30 天计时。
4. 批量异常按单来源完整扫描衡量：`newly_missing_asset_count >= 100 AND > 30%`，或 `>= 10 AND > 80%` 的扫描前该来源唯一 PRESENT 资产基线时触发。分子/分母均按唯一 asset_id。异常持久化并阻断本次新缺失证据；仅显式人工确认可解除，解除后仍需新的成功扫描证据建立观察。
5. 自动候选到期后仍须所有相关来源完成新的成功扫描复核；此卡只显示复核/资格状态，不执行删除。

## 性能设计

- 链路与风险：每次完整来源扫描最终事务更新该来源本次确认的失效资产，风险是逐资产 SQL/N+1 和延长 SQLite 单写事务。
- 做法与边界：用集合式 SQL 和现有 `file_locations(asset_id/source_id)` 索引，只处理本次扫描关联资产；预览沿用有界 keyset，不触碰媒体或 LanceDB。
- 验证与停止：扫描状态/恢复、旧 MISSING、跨来源、批量异常和迁移故障定向验收；检查写事务工作量，不新增 30 万延迟或容量结论，出现逐资产额外事务即停止并改为批量实现。

## 实施与验证

- 新增连续加性 migration；旧 migration 不修改，提升 `SUPPORTED_SCHEMA_VERSION`，保留已有数据与人工记录。
- 在成功扫描最终事务原子写入来源扫描证据、观察状态和异常批次；失败/中断扫描不得写入成功失效证据。
- 扩展只读 retention API/UI 区分观察中、需来源复核、异常待人工确认、零位置、有效副本、引用受保护；未知引用继续 fail-closed。
- 运行迁移三项最小隔离检查（新库、冻结旧库升级、失败回滚）及完整受影响 B/D；迁移语义变化触发 `pytest -q tests/unit tests/integration` 全量回归，保留 JUnit XML 和实际退出码。
- `ruff`、`py_compile`、Node `--check`、`git diff --check`；无删除逻辑、真实图库、全模型/GPU/30 万测试。
- UI 为本卡受影响面，需在内置浏览器真实应用和项目本地隔离合成数据中检查一次状态与刷新流程。若浏览器工具限制则标 PENDING。
- 用户指定的独立 **Luna High 子代理**复核完整 C2-A diff、迁移/扫描事务/资格状态和必需证据；发现的问题已修复，跟进复核无新增 finding。

## 排除范围与停止条件

- 不删除资产、人工信息、索引向量、缓存或 Artifact；不写 DELETE_VECTOR；不改模型选择、扫描发现语义或原图。
- 不提供自动清理开关、一键清除、清理屏障、人工记录删除和物理空间承诺。
- 本卡以新库/旧库/回滚迁移检查、完整受影响增量、内置浏览器和独立复核组合证据验收。全量 unit/integration 基线存在 61 项跨任务失败；本卡相关 migration、place-package schema 兼容、retention UI 和扫描路径均已通过最终受影响 JUnit 119 项（0 failed、0 errors、0 skipped）。按组合证据标记本阶段 PASS，不将全量运行称为通过。

## 后续闭环

按序进入 `P4-LIBRARY-RETENTION-C2-B`（持久屏障/回执与派生写入拒绝）→ `C2-C`（DELETE_VECTOR 生产消费与 Lance 重放）→ `C2-D`（SQLite 机器派生数据清理及 GC-A/B）→ `C2-E`（人工记录授权/安全删除；无安全解除规则的引用继续跳过）→ `C2-F`（确认预览、持久进度、恢复、自动调度及完整 UI）。每一阶段单独更新本报告和状态；总目标只有全部必需阶段验收后才算完成。
