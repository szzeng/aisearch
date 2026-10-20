# P4-LIBRARY-RETENTION-C2-D — 机器派生记录清理与安全引用解除

**状态**：PASS（限定机器派生 SQLite 清理范围）；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：承接 C2-A/B/C 当前工作树改动；不提交，不访问真实图库。
**目标**：建立可恢复的有界单资产清理步骤：实际清理入口复核资格、调用 `activate_asset_retention_cleanup()` 建立屏障并调度向量删除；所有 DELETE_VECTOR 完成后，再清除纯机器派生 SQLite 记录及失效位置/资产身份，保留未经授权的人工数据和共享引用。

## 必读范围

- V3.0 §5.1–5.2、§6、§7、§10.2、§11.2/11.4、§12.4–12.5；V3.1 §2.4、§3.2/3.4、§4.2、§11。
- C2-A/B/C 任务卡与报告，尤其 C2-C 的 producer helper 和 Luna H 结论。
- 直接实现：`library_retention.py`、`retention.py`、`indexing/store.py`、`catalog/store.py`、所有候选 asset_id 的纯机器派生表、外键依赖、搜索/人物/地点/合集修订号与相关测试。

## 冻结语义

1. 清理前必须在同一事务重新检查当前可信扫描、来源在线及成功复核、全局无 PRESENT 副本、异常已解决、活动引用及人工数据；预览结果不能作为过期授权。
2. 有效副本、原图路径/字节和未经明确授权的人工记录不可删除。当前 D 阶段默认只接受无人工保护记录的资产；任何无法解释的 asset/face/person 引用 fail closed。
3. 资格通过后必须调用 C2-C 的 `activate_asset_retention_cleanup()`；屏障和每代 DELETE_VECTOR 事件在同一 SQLite 事务持久化。向量事件未全部 CONFIRMED 前，不删除被引用的 artifacts、index_entries、asset 或 generation 记录。
4. 仅删除明确机器派生、资产专属且外键/共享引用允许的行。不得关闭外键检查、级联抹除或删除被其他有效资产使用的人物、合集、共享缓存和身份记录。
5. 阶段和进度必须持久、重复调用幂等；单资产失败只保留 ACTIVE 屏障和可重试状态，不虚报完成、不重新发布向量。
6. 外部文件回收及 artifact/thumbnail/cache GC-A/B 接入另属 C2-E；本阶段只释放确认为资产专属的数据库派生记录和身份关系。

## 性能设计

- 链路与风险：资格复核、确认向量清除后删除多个机器派生表；风险是逐资产 N+1 关联查询、长事务和遗漏共享引用。
- 做法与边界：一次事务内按 asset_id 批量/有界读取依赖；清理批次最多 20 个资产；复用现有索引和修订号，不扫描全库 Python 对象、不遍历 Lance 数据。
- 验证与停止：隔离合成资产、真实 FK/`foreign_key_check`、共享人物/合集引用及中断重试；发现人工数据被误删、向量未确认即删依赖或跨资产影响即停止；无证据不跑 30 万基准。

## 必须验证与审查

- 定向用例包含：纯机器数据清理；有 PRESENT 副本/来源离线/扫描过期/异常时拒绝；未经授权的人工作品保留；跨资产共享人物/合集/缓存不会连带删除；屏障事件未确认时机器记录不删；阶段提交失败后重试；相关修订号和搜索/人物/地点读取不再展示已清理资产；外键完整。
- 复用 C2-C 真实 Lance 证据；另测服务层真实 transaction/foreign-key 行为及 D 阶段实际 cleanup entry 调用 `activate_asset_retention_cleanup()`。
- 按 risk-based affected surface 运行带 JUnit XML 的 pytest、Ruff、Python 3.12 编译、`git diff --check`。无 UI 改动不做浏览器验收；不访问真实图库、不加载模型/GPU、不做大库基准。
- 使用用户指定 Luna High 子代理对完整受影响面只读复核；范围或风险变化时依 V3.1 扩展。

## 排除范围

- 不删除或修改用户原始照片文件；不清除未经授权的人工记录；不做缓存/artifact 物理 GC、自动调度或确认 UI（C2-E/F）。

## 实施结果

- `begin_asset_retention_cleanup()` 在同一 SQLite 写事务重新核验来源在线、全位置 MISSING、可信成功扫描证据、30 天资格（手动提前入口仅跳过年龄条件）、来源异常、人工与未知引用，再调用 C2-C `activate_asset_retention_cleanup()` 原子建立屏障并生产向量删除事件。
- `finalize_asset_retention_cleanup()` 等待所有 `DELETE_VECTOR` 确认后再清理资产专属机器派生 SQLite 行、面部机器向量/队列、搜索/地点投影、失效位置和资产身份；事务失败回滚后保留 ACTIVE 屏障供重试。PRESENT 副本恢复时撤销临时屏障/删除事件并安排向量恢复。
- 外键检查、共享与人工引用拒绝、确认后清理、延迟至 Lance 删除回执、回滚重试及真实 Lance 上的恢复路径均由合成测试覆盖。自审额外收紧证据 join，确保 `scan_runs.source_id = evidence.source_id`，跨来源证据污染 fail closed。
- 受影响回归 `test-results/P4-LIBRARY-RETENTION-C2-D-affected-r3-20261020.xml`：124 tests，0 failed、0 errors、16 skipped。Ruff、Python 3.12 `py_compile`、`git diff --check` 通过。独立只读 Luna H 复核未发现 blocker；未运行测试的复核结论与本次由主执行者运行的 JUnit 证据分别记录。
- 未触及原始文件；本阶段不包含物理文件 GC 或 UI/自动调度。未运行全仓测试、GPU/模型或大库基准；未提交/推送。
