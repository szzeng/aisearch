# P4-PERSON-DETAIL-B — 独立DS-H全受影响面审查

日期：2026-10-05。状态：**PENDING**。输入：[执行卡](P4-PERSON-DETAIL-B.md)、[最终实现与34项证据](../reports/P4-PERSON-DETAIL-B-implementation.md)、[公开样片真实模型JSON](../reports/P4-PERSON-DETAIL-B-compatibility.json)、[当前ADR-0017](../decisions/ADR-0017-person-source-detail-auto.md)。另一个同任务原型ADR已标历史取代，不再作为当前规则。

基线`856f26e`加未提交DETAIL-A/B与并行CN／地图／依赖工作区；先按AGENTS→status→本卡核对HEAD、index、worktree，不覆盖其他任务编辑。用户已确认停止另一个实现会话；不得用其旧报告中的“跨profile仍关闭／模型对照没做”代替当前代码和证据。

2026-10-08提交整理：CN父提交为`73bca7e`，A+B按用户授权合并为本地人脸提交；审查当前人脸提交相对该父提交的完整diff，不再尝试拆出A-only快照。提交前JUnit `test-results/P4-PERSON-DETAIL-commit.xml` **34/0/0/0**，旧final34和公开模型JSON保留作历史证据。

按V3.1 §2.4由独立DS-H完整审查全部受影响面，含复用DETAIL-A解码／不可变产物／人工链，不能用展示面简审代替。实现者不是本审查者；模型由用户手动切换，不自动派代理。仅§2.2有实际命名触发条件时升级DeepSeek max；收敛后GPT-5.6 Sol/high只聚焦高风险身份／ADR／人工记录与未决项。

## 必查表面

1. **兼容与身份。** 固定权重及两种配置哈希、v1/v2/source字面允许组合和v1证据范围；generation确定性身份／READY／processor／space／维度／L2、artifact与CONFIRMED entry真绑定；未知版本或任一配置漂移关闭自动比较。source不切全库active，已发布字节／face／person身份不变。本轮模型实测v2↔source，不把它写成历史v1运行质量证据。
2. **人工与有效视图。** 相册／整理页／封面／成员／计数／同图占位共享旧机器替代规则，保留人工指定／移出／有效排除／OPEN；合照机器结果不能绕过另一张人工脸。自动写事务重读迟到编辑，映射确认／拒绝／撤销按真实spec重新排队与repair，撤销恢复hold。JPEG draft跨帧转换依赖旧artifact校验，无法核验人工帧保守hold，不改变旧记录／快照／阈值。
3. **状态及恢复。** 保留0019，通过0020正常备份升级schema20；批次历史job引用可重复而推理逻辑身份唯一。固定scope／upper id／每页50、重复点击、启动唤醒、暂停不领下一张、lease等待不抢占、单图失败继续、只重试失败、缓存只恢复派生。零脸及needs_review／成功计数不伪装归人完成。确认发布中断可恢复且跨profile队列不覆盖。
4. **调用与资源。** jobs/scheduler按task_type/job_id领取，普通／手动／自动三类互不偷领；worker自有Catalog线程、关闭／唤醒／重复点击安全。新建／增量默认原图，metadata持久轻量选择和重试／重启；离线／超限不得静默用预览图。共享建库模型槽、缓存生命周期、60MP／200脸／120秒及同步调用暂停粒度诚实。
5. **API与页面。** session／CSRF、来源范围和脱敏；旧响应不污染新范围，进度／暂停／继续／失败重试正确。一个点击持续执行，首页省略profile沿用选择并去重，手动单张按钮仍正常，打开页面不自动补跑历史。
6. **完整变更面。** 对照报告表格检查API、catalog/schema/migrations0019/0020、face全链、jobs／scheduler、refresh、persons及web；直接复用的media、operations／face correction／undo也覆盖。检查最终独立JUnit `test-results/P4-PERSON-DETAIL-B-final34.xml` **34/0/0/0**；targeted.xml曾被并行10项局部测试覆盖，不拿局部证据代替完整复验。核对静态检查及真实CPU JSON，假向量不证明准确率，CPU耗时不证明GPU或30万性能。

## 验证与交付

沿用个人项目限定例外：复用可核对的34项JUnit、Ruff／Node／diff与公开模型证据；具体缺陷才补反例及直接回归。不运行完整unit/integration／历史216项／30万容量／打包／跨GPU，不启动真实库、扫描私人照片或下载模型。真实浏览器、私人效果及实际catalog升级保持未验。

输出独立报告，逐项记录发现、受影响链、反例、修复与证据；直接修复限于具体缺陷，回写任务卡／报告／status。DS-H收敛后交Sol high聚焦终审，不重复全diff工程审查。前置A独立审查未完成，需要覆盖复用链而不能追认；按当前B逐资产授权与兼容门禁审查，不能机械沿用A时期“详情只供人工”的旧边界。
