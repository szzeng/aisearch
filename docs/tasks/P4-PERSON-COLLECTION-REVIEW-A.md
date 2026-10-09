# P4-PERSON-COLLECTION-REVIEW-A — 人物合集受影响面审查（交接卡已由当前会话处置）

日期：2026-10-09。状态：**SUPERSEDED / 用户要求在实施会话内完成受影响面审查**。审查记录见 [实施报告的同会话审查节](../reports/P4-PERSON-COLLECTION-A-implementation.md)。该记录不声称为独立 DS-H；本卡原定的独立模型审查没有执行。

## 输入与基线

按顺序读取 `AGENTS.md`、`docs/development-status.md`、本卡、`docs/tasks/P4-PERSON-COLLECTION-A.md`、`docs/reports/P4-PERSON-COLLECTION-A-implementation.md`，再核对当前 Git 状态、diff 和 JUnit。实现开始时 HEAD 是 `31d3690b3d4fbf9a558335a1bb947a00c52df49e`；前置人物收件箱任务已在实施期间提交为 `9b06b286113d2566b3f024f2b03deb2ffc951f92`。以审查时实际 HEAD、工作树和报告为准，不回退或重写该提交。

## 审查范围

完整核对合集任务的全部新增/变更面和直接调用链：

- `src/photo_search/catalog/migrations/0024_person_collections.sql`、`src/photo_search/catalog/schema.py`、`tests/unit/test_catalog_migrations.py`
- `src/photo_search/persons/collections.py`
- `src/photo_search/api/models.py`、`src/photo_search/api/routes.py`、`src/photo_search/api/queries.py`
- `src/photo_search/web/persons.html`、`src/photo_search/web/persons.css`、`src/photo_search/web/persons-albums.js`
- `tests/integration/test_p4_person_collections.py`、`tests/unit/test_p4_person_collection_ui.py`
- `test-results/p4-person-collection-a.junit.xml` 和实现报告中的真实命令/结果

按实现卡逐条审查：schema v23→v24 连续性与外键/索引；名称规范化和唯一性；批量输入 1–500、所有 ID 预验证、加入并忽略同事务与零部分写入；集合 revision 的增量时机；稳定原始 ID、任意深度 `merged_into` 解析、合并/撤销去重及移除全部 canonical 锚点；`MEMBER_PREDICATE` 与活动资产约束；集合成员 SQL 在分页前按 `asset_id` 去重、照片时间排序、>100 成员不经过普通搜索上限；人员/照片游标绑定集合与 catalog revision、跨合集/失效游标拒绝；认证/Origin/CSRF；响应不泄露本机路径或向量；前端异步序列、选择失败保留、合集与单人动作隔离和照片预览。

独立核对“EXPLAIN 命中成员索引”和 100/160 人合成证据是否确实来自报告所述命令与当前代码。只在发现证据缺口、失败或矛盾时补跑对应定向检查；不跑全仓、真实图库、30 万、模型、GPU、打包或浏览器矩阵。

## 必须输出

写入 `docs/reports/P4-PERSON-COLLECTION-REVIEW-A-dsh.md`：覆盖文件/调用链、结论表、ADR/任务卡预期与实际、必要反例和证据、未验证项、是否命中 V3.1 §2.2 六种 DS-MAX 升级条件。只有实际证据命中条件时才建议 DS-MAX。

schema、稳定身份锚点和原子事务属于 V3.1 §11 高风险边界，Sol high 聚焦终审为必需。DS-H 阶段结束后，将状态更新为 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`（若有问题则如实标记），保存精简交接包，并停止；不要标总 PASS，不要开始下一个产品任务。随后生成单独 Sol high 终审卡，输入限于本报告、未决项、关键 diff、相关 ADR/任务卡与最小反例。DS-H 只有发现跨模块根因无法证明、状态语义矛盾、测试/ADR 对立无法判断、二次相关回归、大型耦合状态机或 Sol 要求广泛复查时，才按 §2.2 建议 DS-MAX。
