# ADR-0014 — 个人相册加性命名与人脸几何修复边界

日期：2026-10-03；状态：实现采用的最小决策，独立工程审查/高风险聚焦裁决待完成，非正式冻结。范围仅P4-PERSON-UX-A；历史ADR及companion原文件不改写。

用户授权个人相册优化及简单验证。新 `PUT /persons/{id}/label` 与旧 `/name` 分开：label在事务内遇到其他活动同名人物返回PERSON_REVISION_CONFLICT，details.reason=NAME_IN_USE及target_person_id，全部零写入。前端显示明确的并入按钮，再调用已有merge用例。旧name仍自动同名合并、摘要不加新字段；新label意图摘要包含merge_by_name=false，跨两入口同request_id不等价。重放仍早于revision检查。不新增schema、错误码或合并/撤销算法。

scale保持既有[0,1]正向比例语义，bbox/landmarks保持方向修正原图坐标。修正会影响几何及face_id，因此spec/preprocess更新v2，artifact_revision更新face-document.v2，artifact_id及generation_id带新revision；v1保持历史ID算法及不可变字节。新旧人工知识只通过ADR-0010映射审查衔接，不自动迁移归属/锁/排除。

兼容例外仅明确的face-buffalo-l-scrfd-arcface-v1→v2几何修复。源码analyze_image的变化发生在source_box/source_landmarks和document.scale；working_image、decode_detections、以工作图关键点对齐的识别输入与归一化均未变化，权重、processor及FACE_SPACE_ID相同，故可复用同空间代表匹配。不推断其他版本或不同模型仅因同维而兼容。

旧活动v1不因v2部分建库而切换。仅READY v2的已确认代表与旧活动代表一起读取，核验space/processor/dimension/metric/normalization；按canonical人物、face_pixels DESC/face_id ASC再限制总代表数。代表必须有CONFIRMED entry和有效成员，人工未归类/排除仍禁止；未来未知spec不应用例外。旧outbox修复仍以旧revision发布/确认，人工操作根据实际face.spec_revision预约其世代。此例外不复制全库、不自动重检、不改变Threshold，不提供通用激活或模型升级功能。

个人相册分页复用SQLite权威成员，游标绑定人物/过滤revision；名字列表绑定查询/分类/revision。自动未归组读取同时保留尚无新已发布产物的旧事实及旧人工知识，OPEN映射留在原高级审查入口。新的公开摘要不返回bbox、landmarks、vector、路径或单对相似度。

依据、直接调用链和小样本证据见[P4-PERSON-UX-A报告](../reports/P4-PERSON-UX-A-implementation.md)。独立审查重点为旧name契约兼容、新label幂等、不可变产物身份、人工映射hold及有界同空间兼容；不得据本次决策追认模型质量/全量/发行PASS。
