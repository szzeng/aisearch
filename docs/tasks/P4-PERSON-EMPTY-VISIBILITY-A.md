# P4-PERSON-EMPTY-VISIBILITY-A — 空人物自动隐藏与列表统一

**日期：2026-10-11**  
**状态：READY（任务卡；尚未实施、未运行测试）**  
**设计基线：master / 70fc82ab95a6c17225e09e3577851c0592a80a1b（执行前复核最新 HEAD）**  
**推荐：GPT-6 Luna / Medium；个人项目，定向简单测试即可。**

## 1. 背景与现状

用户在人物相册将某人物的所有照片移出后，已命名的 0 张照片人物仍显示在列表；之后与其他人物合并，容易造成「两个人物都消失」的使用困惑。

已核对代码：
- src/photo_search/persons/albums.py::people_page：kind=unnamed 已使用活动照片 + MEMBER_PREDICATE 排除空人物，kind=named/all 尚未统一。
- src/photo_search/persons/read.py::person_page：按 kind 使用不同条件；named 以及部分 all/unnamed 路径仍可能列出空人物。
- src/photo_search/web/persons-albums.js：普通列表按 named/unnamed 展示，已有 refreshPeopleViews/notifyPeopleChanged 等刷新入口。
- tests/integration/test_p4_person_cleanup.py::test_empty_or_ineffective_groups_are_filtered_before_paging：旧断言允许已命名空人物出现在结果，需按新规则修正。
- src/photo_search/persons/operations.py::_apply_merge：合并保留旧 person ID 与 merged_into 重定向；本任务不得变更人物合并、人工排除和撤销语义。

## 2. 产品决定

**任何活动 canonical 人物，只要当前有效照片数为 0，即从日常人物列表隐藏；数据库身份和历史不删除。**

| 人物状态 | 正常人物/整理列表 |
| --- | --- |
| 已命名，有有效照片 | 显示 |
| 未命名，有有效照片 | 显示 |
| 已命名，0 张有效照片 | 不显示 |
| 未命名，0 张有效照片 | 不显示 |
| 已忽略人物，0 张有效照片 | 已忽略列表也不显示 |
| 空人物后来获得有效照片 | 在刷新后恢复显示 |
| 已合并来源（merged_into 非空） | 不重复显示，旧 ID 仍可解析 |

“有效照片”应沿用既有 MEMBER_PREDICATE 的实际有效归属，并以活动资产为准；SUGGESTED、MANUAL_UNASSIGNED、活动排除、失效资产都不能使空人物重新显示。不能只按 face_person_links 原始行数判断。

这只是列表可见性规则。**通过人物 ID 直接读取、历史回放及撤销，不应因列表隐藏而被删改或阻断。**

## 3. 最小实现

### A. 统一列表 SQL（主要改动）

在 people_page() 中复用/提取当前 kind=unnamed 的有效成员 EXISTS 条件，将它应用于 kind=all、named、unnamed；原有 kind 只负责名称过滤。对 visibility=visible/hidden/all、quality_view、collection_state 和 q 仍保留既有含义。

关键要求：
- SQL 必须在 COUNT、排序、LIMIT 与 cursor 分页前过滤空人物，不能前端事后删卡。
- 保留 photos_desc、photos_recent_desc、person_id 的排序与分页稳定性；total、has_more、next_cursor 一致。
- 在 clean 质量视图中保留现有过滤口径，不能让只有无效脸的人物混入。
- 不新增逐人物统计查询，不扫描全部人物至 Python；优先复用 SQL 和现有索引，维持 40 人分页性能。
- 对齐页面展示的 asset_count 与空人物判定；若发现二者现有口径不一致，只做与本卡直接相关的最小修正并测试，不扩大成计数架构重做。

### B. 对齐另一个人物列表入口

核对 persons/read.py::person_page() 与 API 实际使用方：kind=all/named/unnamed 应遵循相同的空人物不可见规则，仍不重复展示 merged_into 来源。禁止改变 GET /persons/{id} 详情或旧 ID canonical 解析能力。

### C. 前端只复用既有刷新

使用现有变更通知/局部刷新机制，保证移出最后一张脸后正常列表退出，重新分配或撤销后又能显示。确认同名/未命名/忽略模式切换无陈旧空卡；无需新增空人物开关、维护页或全新前端状态体系。

### D. 合并安全最小验证，不重做合并

添加一个隔离合成检查：空人物 A 与有照片 B 合并后，canonical 目标依旧可以读取原有有效照片，旧 ID 能解析，人物列表能找到有照片的目标（位于正确的命名分区）。核对 A→B，并在成本允许时核对 B→A；不把“合并来源人物不单列”误判为数据丢失。

若测试暴露人工排除、MANUAL_UNASSIGNED 或真实合并逻辑导致有效照片意外归零，**不要靠隐藏掩盖，也不要清空人工排除记录**；记录具体复现和风险，另立修复卡，本卡不得声称合并问题已治本。

## 4. 明确禁止

- 不删除空人物，不新增 GC、不改维护页面、不引入保留期。
- 不改 Schema/migration、person ID、合并重定向、人工锁/排除、历史/撤销语义。
- 不重新识别人脸，不重跑索引，不访问、迁移或清理用户真实 PhotoSearchData。
- 不顺带重构人物合并、人物合集、排序、查询架构；不引入配置开关。

## 5. 简单测试与验收

以现有 tests/integration/test_p4_person_cleanup.py 为主，少量新增/修改断言：

1. 已命名/未命名各有 0 张、有照片；机器仅建议/人工移出/活动排除/失效照片的空人物均不显示；所有 kind 的 total 正确。
2. limit=1 分页夹着多位空人物：第一页与后续页只返回有效人物，has_more/cursor 正确；照片数排序正常。
3. 移出最后一张有效脸 → 空人物退出；重新指定或合法撤销后恢复显示；人物 ID 与原有记录仍在。
4. 空人物合并有照片人物，核对 canonical 目标有效照片不减少、来源 ID 可解析、列表只显示合并后的目标；如已有测试已覆盖可复用。

运行**直接受影响**的 pytest -q（输出 JUnit XML）、Ruff、如涉及 JS 则 node --check、git diff --check；检查真实退出码，报告明确 PASS/FAIL/未验范围。不跑全仓回归、GPU/模型、30 万照片性能、打包或真实浏览器矩阵。

若涉及新的高风险身份或人工知识业务语义，停止范围扩大并记录需要单独审核的原因；本卡原则上只允许只读列表筛选。

## 6. 交付与完成判据

- 代码与对应最小测试；
- docs/reports/P4-PERSON-EMPTY-VISIBILITY-A.md（写明实际命令/结果、未验事项）；
- 更新 docs/development-status.md，保留其他任务原记录。

完成时应满足：所有日常人物列表不显示 0 有效照片的 canonical 人物；照片恢复后人物正常重现；分页/统计不出错；旧身份、人工知识、历史、撤销均保留。

**当前仅提交任务卡，不代表修复完成。**
