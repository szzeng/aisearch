# P4-PERSON-DETAIL-B — 独立DS-H全受影响面审查

日期：2026-10-05，2026-10-09补充。状态：**ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PASS / DETAIL-B REVIEW PASS**。输入：[执行卡](P4-PERSON-DETAIL-B.md)、[最终实现与34项证据](../reports/P4-PERSON-DETAIL-B-implementation.md)、[公开样片真实模型JSON](../reports/P4-PERSON-DETAIL-B-compatibility.json)、[人物批次性能收尾优化](../reports/P4-PERSON-PERF-A.md)、[当前ADR-0017](../decisions/ADR-0017-person-source-detail-auto.md)。另一个同任务原型ADR已标历史取代，不再作为当前规则。

基线`856f26e`加未提交DETAIL-A/B与并行CN／地图／依赖工作区；先按AGENTS→status→本卡核对HEAD、index、worktree，不覆盖其他任务编辑。用户已确认停止另一个实现会话；不得用其旧报告中的“跨profile仍关闭／模型对照没做”代替当前代码和证据。

2026-10-08提交整理：CN父提交为`73bca7e`，A+B按用户授权合并为本地人脸提交；审查当前人脸提交相对该父提交的完整diff，不再尝试拆出A-only快照。提交前JUnit `test-results/P4-PERSON-DETAIL-commit.xml` **34/0/0/0**，旧final34和公开模型JSON保留作历史证据。

2026-10-09头像补充：[P4-PERSON-AVATAR-FIX-A](P4-PERSON-AVATAR-FIX-A.md)修复路由SELECT漏传实际spec_revision导致原图头像409；29项直接回归通过，现场重启待验。审查加入真实已发布普通／source文档的GET/HEAD冷缓存链，而不能只验证recorded_face_frame完整dict或已有crop缓存；仍保持checksum／bbox／内容／profile拒绝。

2026-10-09批次性能补充：[P4-PERSON-PERF-A](P4-PERSON-PERF-A.md)移除了runner在`reconcile`之后重复的每资产索引收尾调用；合成失败/重试用例验证每张处理照片只做一次前置发布。审查确认在归人产生候选、无归人结果、单图失败、暂停和重试下，待处理索引都仍在进入下一张前收敛，且没有漏掉reconcile产生的outbox；使用JUnit `test-results/P4-PERSON-PERF-A.xml` **16/0/0/0**。本次无30万批次吞吐、模型/GPU、真实图库或照片测试，不能据此声称整体人物性能达标。

按V3.1 §2.4由独立DS-H完整审查全部受影响面，含复用DETAIL-A解码／不可变产物／人工链，不能用展示面简审代替。实现者不是本审查者；模型由用户手动切换，不自动派代理。仅§2.2有实际命名触发条件时升级DeepSeek max；收敛后GPT-5.6 Sol/high只聚焦高风险身份／ADR／人工记录与未决项。

## 必查表面

1. **兼容与身份。** 固定权重及两种配置哈希、v1/v2/source字面允许组合和v1证据范围；generation确定性身份／READY／processor／space／维度／L2、artifact与CONFIRMED entry真绑定；未知版本或任一配置漂移关闭自动比较。source不切全库active，已发布字节／face／person身份不变。本轮模型实测v2↔source，不把它写成历史v1运行质量证据。
2. **人工与有效视图。** 相册／整理页／封面／成员／计数／同图占位共享旧机器替代规则，保留人工指定／移出／有效排除／OPEN；合照机器结果不能绕过另一张人工脸。自动写事务重读迟到编辑，映射确认／拒绝／撤销按真实spec重新排队与repair，撤销恢复hold。JPEG draft跨帧转换依赖旧artifact校验，无法核验人工帧保守hold，不改变旧记录／快照／阈值。
3. **状态及恢复。** 保留0019，通过0020正常备份升级、当前HEAD schema21及其0021地点加性表；批次历史job引用可重复而推理逻辑身份唯一。固定scope／upper id／每页50、重复点击、启动唤醒、暂停不领下一张、lease等待不抢占、单图失败继续、只重试失败、缓存只恢复派生。零脸及needs_review／成功计数不伪装归人完成。确认发布中断可恢复且跨profile队列不覆盖。
4. **调用与资源。** jobs/scheduler按task_type/job_id领取，普通／手动／自动三类互不偷领；worker自有Catalog线程、关闭／唤醒／重复点击安全。新建／增量默认原图，metadata持久轻量选择和重试／重启；离线／超限不得静默用预览图。共享建库模型槽、缓存生命周期、60MP／200脸／120秒及同步调用暂停粒度诚实。
5. **API与页面。** session／CSRF、来源范围和脱敏；旧响应不污染新范围，进度／暂停／继续／失败重试正确。一个点击持续执行，首页省略profile沿用选择并去重，手动单张按钮仍正常，打开页面不自动补跑历史。
6. **完整变更面。** 对照报告表格检查API、catalog/schema/migrations0019/0020/当前0021、face全链、jobs／scheduler、refresh、persons及web；直接复用的media、operations／face correction／undo也覆盖。检查B原JUnit `test-results/P4-PERSON-DETAIL-B-final34.xml` **34/0/0/0** 和本次 runner 回归 `test-results/P4-PERSON-PERF-A.xml` **16/0/0/0**；targeted.xml曾被并行10项局部测试覆盖，不拿局部证据代替完整复验。核对静态检查及真实CPU JSON，假向量不证明准确率，CPU耗时不证明GPU或30万性能。

## 验证与交付

沿用个人项目限定例外：复用可核对的34项JUnit、Ruff／Node／diff与公开模型证据；具体缺陷才补反例及直接回归。不运行完整unit/integration／历史216项／30万容量／打包／跨GPU，不启动真实库、扫描私人照片或下载模型。真实浏览器、私人效果及实际catalog升级保持未验。

输出独立报告，逐项记录发现、受影响链、反例、修复与证据；直接修复限于具体缺陷，回写任务卡／报告／status。DS-H收敛后交Sol high聚焦终审，不重复全diff工程审查。前置A独立审查未完成，需要覆盖复用链而不能追认；按当前B逐资产授权与兼容门禁审查，不能机械沿用A时期“详情只供人工”的旧边界。

## 2026-10-09 独立主审结果

完整受影响面工程审查已完成，主审当时结论 **ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING**（终审现已完成，见下节）。详见[独立审查报告](../reports/P4-PERSON-DETAIL-B-review.md)，包含六项检查结论、证据复核、未验边界和Sol压缩交接。未发现V3.1 §2.2升级DeepSeek max的实证条件。主审随后交接[Sol聚焦终审卡](P4-PERSON-DETAIL-B-sol-review.md)裁决兼容身份、世代/队列和人工知识不变量；该阶段现已通过，审查闭环结果见下节。

用户反馈人物列表加载未见提速。P4-PERSON-PERF-A只优化批次runner每资产收尾，不改变列表API；静态发现列表每页逐人物查成员计数和头像，最高约120条此类查询/40项。没有现场时序或SQL trace，未将其视作主审缺陷或声称已量化；建议另立列表性能测量/优化任务。
## 2026-10-09 审查闭环

**SOL FOCUSED REVIEW PASS / DETAIL-B REVIEW PASS**。四项身份、跨profile、人工知识及撤销/requeue边界与ADR-0017一致，未发现阻塞问题；详见[聚焦终审卡](P4-PERSON-DETAIL-B-sol-review.md)及[主审报告终审结果](../reports/P4-PERSON-DETAIL-B-review.md)。复核既有34/34/16/29项JUnit与公开CPU JSON，未重跑成功套件、未改产品代码。PASS限于本卡工程审查闭环，不覆盖真实图库/浏览器/现场迁移/私人准确率/GPU/30万性能，亦不追认DETAIL-A独立整卡通过。
