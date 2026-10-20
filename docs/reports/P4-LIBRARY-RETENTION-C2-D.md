# P4-LIBRARY-RETENTION-C2-D — 执行报告

**状态**：PASS（限定机器派生 SQLite 清理范围）
**日期**：2026-10-20
**基线**：承接 C2-A/B/C 当前未提交工作树；未提交、未推送。

## 结果

新增安全的单资产清理服务。启动时在单个 SQLite 写事务中重新校验可信来源和扫描证据、在线状态、所有位置仍为 MISSING、全局无 PRESENT 副本、观察期与来源异常、人工/共享/未知引用。提前清理入口只允许跳过年龄门槛，仍需要有效失效证据和其余安全条件。通过后调用 C2-C 的组合 helper，在事务中建立持久屏障并安排每个相关向量 generation 的 `DELETE_VECTOR`。

最终清理在全部向量删除事件 CONFIRMED 后运行。它清除明确资产专属的机器派生表、面部机器向量与队列、投影/cache、失效位置和资产身份，并保留 C2-E 所需的 Artifact/Job 信息。SQLite 删除失败会整体回滚，ACTIVE 屏障保留供安全重试。若清理期间发现位置恢复为 PRESENT，则撤销 ACTIVE 屏障和待删事件，并重新安排现有向量发布。

复核时发现扫描证据仅按 `scan_id` 连接可能接受错配来源的成功扫描。现已要求 `scan_runs.source_id` 与证据来源相等，并新增跨来源污染拒绝用例。

## 验证与审查

- 受影响集成 JUnit：124 tests，0 failed、0 errors、16 skipped。原始 XML：`test-results/P4-LIBRARY-RETENTION-C2-D-affected-r3-20261020.xml`。
- 覆盖资格和人工/未知引用阻断、SQLite FK 完整性、纯机器派生行和 face 数据清理、向量确认前不释放引用、真实 Lance 删除后副本恢复和向量重排、失败回滚及重试、跨来源 scan 证据错配拒绝，以及索引/搜索/调度/人脸/制品相关受影响路径。
- Ruff、Python 3.12 `py_compile`、`git diff --check` 通过。未执行浏览器验收（本阶段没有 UI 变更）。
- 用户指定的独立 Luna H 只读复核：未发现新 blocker；确认 C2-C helper 在清理事务中被调用，检查多 space/generation Lance 回放及新增扫描证据绑定。该复核不是 DS-H；JUnit 由本执行者运行。

## 未完成边界

本阶段不做 Artifact/缩略图/预览/缓存物理 GC，不做人工数据授权删除 UI，不提供一键批处理确认、自动调度或持久进度 UI。父目标仍为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实照片目录或真实 PhotoSearchData，未加载模型/GPU，未跑全仓或大库测试，未提交/推送。
