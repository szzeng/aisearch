# P4-PERSON-UX-A-review — 独立人物相册受影响面审查

状态：READY / INDEPENDENT REVIEW PENDING。唯一下一人物阶段；不继续新增功能、性能研究或真实库重检。模型建议DS-H/高推理，用户手动选择并新开会话；不启动Agent或自动切模型。

入口：AGENTS → development-status → 本卡 → [实现报告](../reports/P4-PERSON-UX-A-implementation.md)和[ADR-0014最小决策](../decisions/ADR-0014-person-album-geometry-repair.md)。实现起点fe46753；本会话提交以 `feat(persons): simplify albums and add stored-face rematching` 定位，包含门限补充；保留并行首页/搜索/Qwen改动。读取主架构§5.4、§8.3～8.3.5、§9.3/9.5、§10.1/10.3/11.3.3，策略§2.2/2.3/2.4/11/12，ADR-0006/0007/0010直接受影响条款。

完整工程审查本卡报告列出的全部产品/工具/测试改动和直接调用链，核对JUnit219/0/0/0、升级补充子集3/0/0/0、Ruff/Node及合成浏览器证据。仅在证据缺口或具体新反例时补最小定向JUnit；用户已要求简单测试，不机械重跑全量、性能、模型、包。不得访问真实库/PhotoSearchData或大夹具、下载权重、调整阈值或依赖。

必审：SQLite权威成员去重/分页/游标绑定；命名碰撞事务零写入、旧name摘要及重放顺序；单脸/邻脸/人工锁/排除/撤销；旧产物共存与v2身份；NO_FACE发布后的旧机器可见性和旧人工锚点/OPEN映射hold；明确v1/v2同空间兼容的源码依据、READY/确认门禁、旧outbox重放、每个canonical人物的总代表上限、未知版本不套用例外；同源session/CSRF、只读脱敏及页面失败/竞态处理。不能把当前实现者测试称为独立审查。

输出docs/reports/P4-PERSON-UX-A-review.md及最新status。命中V3.1§2.2真实条件才建议max并说明编号/反例。涉及身份/人工状态和ADR-0014未冻结边界，Sol high聚焦裁决为下一必选阶段；工程审查通过后生成具体聚焦终审卡并停止，不重复全diff，也不提前标总任务PASS。普通缺陷交实现修复并只重测直接受影响面。

补充审查面：报告末节的NULL存储头像→只读有效成员补选、移除/排除后头像失效、同一合照的不同人物person_faces有界投影、页面当前人脸标记。核对新增JUnit20/0/0/0及蓝/绿人物同照片截图，不扩展真实模型/全量测试。

同日用户新增门限授权与重匹配：[P4-PERSON-MATCH-A卡](P4-PERSON-MATCH-A.md)、[实现报告](../reports/P4-PERSON-MATCH-A-implementation.md)，定向JUnit79/0/0/0。加审face contract/service/cli及reconcile可选asset_ids全改动：历史0.56校准不伪改为0.50新测量；归人策略不更改检测/不可变产物身份；已存v1/v2特征重匹配的选择scope、索引确认/排除/人工锁/hold、新零脸发布和旧机器退役、未知空间、错误data-root零建库及limit。既有CONFIRMED关系不拆散，无真实库执行；本轮只有CLI，没有新UI。上述内容属于同一完整受影响面主审，不另开性能或真实数据专项。

本轮图片展示补充：[P4-PERSON-IMAGE-A](P4-PERSON-IMAGE-A.md)、[报告](../reports/P4-PERSON-IMAGE-A-implementation.md)。补审ID URL历史浏览器缓存绕开和ETag重新验证；face-crop@2对校验后已发布v1/v2检测文档original_size帧的只读映射、checksum/输入/face_id/bbox绑定、缺失/损坏拒绝、无文档旧行的原图头路径、SQLite查询与线程池边界。检测spec/face_id/向量/人工归属均不改；29项定向JUnit和合成旧缓存/大JPEG/EXIF6/页面证据通过，真实图库未访问。旧954cf00的图片尝试被3bab1c3撤回，不作为本轮证据或当前实现。

2026-10-04整理补修：[P4-PERSON-CLEANUP-A](P4-PERSON-CLEANUP-A.md)、[报告](../reports/P4-PERSON-CLEANUP-A-implementation.md)，JUnit13/0/0/0。补审未命名有效成员筛选在分页前执行，空身份保留但整理区隐藏，named/all目标选择不变，更正/合并/命名与撤销恢复、共享合照剩余成员不误隐藏；成功状态3秒收起与撤销条10秒独立，连续提示计时重置，旧计时不清除后续错误。仅只读列表与提示展示改变，人工事务/schema不变，无真实库访问。

2026-10-04封面新增：[P4-PERSON-COVER-A](P4-PERSON-COVER-A.md)、[报告](../reports/P4-PERSON-COVER-A-implementation.md)，JUnit23/0/0/0。补审现有avatar_face_id展示元数据PUT完整model/route/albums/UI调用链：session/CSRF、严格形状、canonical/revision/有效成员事务校验、他人/建议/人工移出/排除拒绝零写入；只更新persons行、不改过滤revision/索引/人工归属，独立PUT不伪写RENAME日志，既有撤销拒绝覆盖后来的封面。UI只可选本人、重复提交/失败/晚到预览结果、相册与卡片同步和失效补选；无schema或旧操作类型变化。仅合成API及实际JS最小DOM，未做真实图库/浏览器验证。

2026-10-04照片导航：[P4-PERSON-PREVIEW-A](P4-PERSON-PREVIEW-A.md)、[报告](../reports/P4-PERSON-PREVIEW-A-implementation.md)，JUnit7/0/0/0。补审实际页面顺序/分页ID去重/首尾禁用、人物搜索相同scope与session/cursor、加载失败重试、重复点击、关闭或新列表时旧续页不切图、旧人脸响应不覆盖新照片；封面保存期间禁用导航与列表外预览隐藏导航。只前端展示/既有有界分页，无API/数据语义改动；最小DOM证据，不冒充真实浏览器验证。

2026-10-04按研究推荐补修：[P4-PERSON-MATCH-B](P4-PERSON-MATCH-B.md)、[实现证据](../reports/P4-PERSON-MATCH-B-implementation.md)，JUnit216/0/0/0。补审face/contract/index/service、persons/albums、API model/routes、persons.html/实际JS及三份新测试完整调用链：同图已归属与代表候选按人物汇总后比较、不能简单剔除占位者强选第二名、人工排除不经同图列表绕回；确认关系/entry/artifact/READY及精确v1/v2例外、live32候选/8多样代表、canonical上限与去重复策略、旧代表标志不重建与index_state口径；.985选代表参数不伪称校准，ENGINE_CONFIG/spec/face_id未改。

加审只读原因汇总与列表可见性/成员/OPEN hold一致、未READY即使entry确认仍等待、质量首因/人工移出优先、过滤游标绑定与API脱敏；补匹配POST的session/CSRF/严格参数、after续批及所选队列范围、启动和逐资产400发布事件上限、持久队列与暂缓/异常重试、已有CONFIRMED关系和人工知识不改；前端仅显式点击、20张一批无自动循环、重复请求/失败保留进度与旧响应保护。API照片级上限不等于全请求400事件或固定响应时间；续批不是静态快照，暂缓项之后可从头处理。仅合成API与Node最小DOM；真实效果/浏览器/容量未验，不重复真实库、模型或性能专项。[原图细节后续卡](P4-PERSON-DETAIL-A.md)尚未实现，不延伸ADR-0014几何兼容例外。
