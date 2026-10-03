# P4-PERSON-UX-A-review — 独立人物相册受影响面审查

状态：READY / INDEPENDENT REVIEW PENDING。唯一下一人物阶段；不继续新增功能、性能研究或真实库重检。模型建议DS-H/高推理，用户手动选择并新开会话；不启动Agent或自动切模型。

入口：AGENTS → development-status → 本卡 → [实现报告](../reports/P4-PERSON-UX-A-implementation.md)和[ADR-0014最小决策](../decisions/ADR-0014-person-album-geometry-repair.md)。实现起点fe46753；本会话提交以 `feat(persons): simplify albums and add stored-face rematching` 定位，包含门限补充；保留并行首页/搜索/Qwen改动。读取主架构§5.4、§8.3～8.3.5、§9.3/9.5、§10.1/10.3/11.3.3，策略§2.2/2.3/2.4/11/12，ADR-0006/0007/0010直接受影响条款。

完整工程审查本卡报告列出的全部产品/工具/测试改动和直接调用链，核对JUnit219/0/0/0、升级补充子集3/0/0/0、Ruff/Node及合成浏览器证据。仅在证据缺口或具体新反例时补最小定向JUnit；用户已要求简单测试，不机械重跑全量、性能、模型、包。不得访问真实库/PhotoSearchData或大夹具、下载权重、调整阈值或依赖。

必审：SQLite权威成员去重/分页/游标绑定；命名碰撞事务零写入、旧name摘要及重放顺序；单脸/邻脸/人工锁/排除/撤销；旧产物共存与v2身份；NO_FACE发布后的旧机器可见性和旧人工锚点/OPEN映射hold；明确v1/v2同空间兼容的源码依据、READY/确认门禁、旧outbox重放、每个canonical人物的总代表上限、未知版本不套用例外；同源session/CSRF、只读脱敏及页面失败/竞态处理。不能把当前实现者测试称为独立审查。

输出docs/reports/P4-PERSON-UX-A-review.md及最新status。命中V3.1§2.2真实条件才建议max并说明编号/反例。涉及身份/人工状态和ADR-0014未冻结边界，Sol high聚焦裁决为下一必选阶段；工程审查通过后生成具体聚焦终审卡并停止，不重复全diff，也不提前标总任务PASS。普通缺陷交实现修复并只重测直接受影响面。

补充审查面：报告末节的NULL存储头像→只读有效成员补选、移除/排除后头像失效、同一合照的不同人物person_faces有界投影、页面当前人脸标记。核对新增JUnit20/0/0/0及蓝/绿人物同照片截图，不扩展真实模型/全量测试。

同日用户新增门限授权与重匹配：[P4-PERSON-MATCH-A卡](P4-PERSON-MATCH-A.md)、[实现报告](../reports/P4-PERSON-MATCH-A-implementation.md)，定向JUnit79/0/0/0。加审face contract/service/cli及reconcile可选asset_ids全改动：历史0.56校准不伪改为0.50新测量；归人策略不更改检测/不可变产物身份；已存v1/v2特征重匹配的选择scope、索引确认/排除/人工锁/hold、新零脸发布和旧机器退役、未知空间、错误data-root零建库及limit。既有CONFIRMED关系不拆散，无真实库执行；本轮只有CLI，没有新UI。上述内容属于同一完整受影响面主审，不另开性能或真实数据专项。
