# P4-PERSON-DETAIL-B — 独立全受影响面工程审查

日期：2026-10-09。审查基线：DETAIL-A/B 实现提交 `5d7bc13`（其父提交 `73bca7e`），并核对后续受影响改动：`d4f7720` 头像profile字段修复、工作区中的 P4-PERSON-PERF-A runner优化。结果：**ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PASS / DETAIL-B REVIEW PASS**。本报告由非实现者在独立审查任务中编写；按V3.1 §2.4完整受影响面主审，不是性能验收或真实图库验收。

## 范围与方法

以 [DETAIL-B review卡](../tasks/P4-PERSON-DETAIL-B-review.md) 的六项检查为边界，逐链审读实现报告列出的批次/API/runner、jobs/scheduler、face profile/产物/索引、自动归属与映射、refresh默认profile、人物成员视图/头像路由、web状态、0019/0020迁移和当前schema21/0021。相关依据为 [ADR-0017](../decisions/ADR-0017-person-source-detail-auto.md)、主架构§8.3～8.3.5及§11.3.1～11.3.3、V3.1 §2.2～2.4。没有启动服务或访问PhotoSearchData、真实catalog、私人照片，也没有下载模型或跑新增测试。

变更范围核对：DETAIL-A/B 48文件提交相对`73bca7e`；HEAD `d4f7720`修复头像GET/HEAD冷缓存查询投影；P4-PERSON-PERF-A仅删除reconcile后空的每资产收尾索引调用并更新对应回归夹具。工作区有大量搜索、地点、照片信息并行改动；它们不属于本审查，也没有被改写。审查期间未修改产品代码。

## 结论表

| 检查面 | 结果与证据 |
|---|---|
| 身份、兼容与发布 | **通过**。`detail_policy.compatibility_verified()`以固定模型fingerprint、普通/source config hash及spec字面值作为总门；`FaceIndexCoordinator.candidates()`另验证generation确定性ID、READY、space/processor/spec、512维、cosine/L2，且只接受当前artifact绑定的CONFIRMED entry和匹配向量hash。source generation通过自身READY状态参与比较但不设置全库active指针。公开模型JSON为CPU、9组同人/异人双向比较；仅实测v2↔source，报告没有把它冒充为历史v1推理质量证据。未知版本/配置无法进入source自动比较。 |
| 人工知识与有效成员 | **通过**。`superseded_machine_sql`只隐藏已ready、内容与策略一致的旧机器投影，并明确排除人工指定/移出、有效排除、OPEN锚点；成员与相册共用该谓词。`_reconcile_asset`在权威写事务中重读控制revision、canonical、排除、同图占位和映射hold；迟到人工更改只拒绝受影响脸，不扩大到合照其他脸。source跨JPEG draft帧比较要求校验旧产物并使用保存的坐标帧，不能核验的人工锚点保持hold；未见改写既有face/person ID或发布字节。 |
| 批次、迁移与恢复 | **通过**。0019原批次/资产表保留；0020只加坐标帧和自动授权并放宽历史job引用为非唯一索引；当前schema21的0021是地点来源映射加性表，不改变人物批次状态。创建时记录source与asset上界，页面上限50，候选复查会跳过范围中后来变得不合格的资产；runner按持久job恢复，尊重有效lease、暂停后不领下一张，失败重试只复活失败job。现有合成升级证据覆盖schema19→21且保存人工事实/游标。 |
| 调度、资源与默认profile | **通过**。普通、手动source-detail、auto source-detail按task_type/job_id过滤；runner在线程自有service/catalog上串行执行。持久策略在来源metadata中恢复；新集合默认原图，既有集合不会因启动/打开页面自动补跑。同步解码/ONNX暂停粒度、60MP/200脸/120秒限制及单槽模型资源限制均在实现报告如实披露。 |
| API、页面与头像 | **通过**。批次路由在session/CSRF保护下按来源创建/查看/暂停/继续/重试，身份范围没有泄漏路径投影。页面使用request序号屏蔽旧范围响应并轮询持久batch，主页省略profile时复用来源已有选择。头像修复仅补查询投影的实际`spec_revision`；修前source GET和HEAD冷缓存均可复现409，修后普通/source×GET/HEAD测试通过，并保留checksum/face/bbox/content/profile校验。 |
| P4-PERF-A runner删除收尾调用 | **通过**。`reconcile()`仅在归属有变化时在返回下一资产前同步发布它生成的outbox；无归属变更则不产生outbox。runner保留reconcile前的候选发布，删除之后无条件的第二次`process_all`。16项失败继续/只重试回归以及原34项批次测试均有JUnit证据。此证据证明调用不再重复，不证明端到端吞吐有可感知提升。 |

## 已核对验证证据

- `test-results/P4-PERSON-DETAIL-B-final34.xml`：34 tests，0 failures / 0 errors / 0 skipped。
- `test-results/P4-PERSON-DETAIL-commit.xml`：34 tests，0 failures / 0 errors / 0 skipped；报告记录的Python 3.12.10定向命令退出码0。
- `test-results/P4-PERSON-PERF-A.xml`：16 tests，0 failures / 0 errors / 0 skipped。
- `test-results/P4-PERSON-AVATAR-FIX-A.xml`：29 tests，0 failures / 0 errors / 0 skipped。
- `docs/reports/P4-PERSON-DETAIL-B-compatibility.json`：固定权重和两种config hash与实现门禁一致，9组公开夹具比较通过；provider为CPUExecutionProvider。
- 报告记录的相关Ruff、Node语法和scoped diff-check结果退出码0。没有因审查而重跑整套测试：现有证据在当前worktree可读、计数和目标均一致，卡允许复用成功证据。

## 列表速度反馈

用户反馈“人物列表加载速度没有提高”。这个观察与P4-PERSON-PERF-A改动目标一致：PERF-A优化的是后台source-detail批次每处理一张照片时的SQLite索引收尾，不会改变人物列表HTTP响应或头像下载路径，因此不能预期它让列表变快。

静态代码检查发现列表本身存在可量化的逐人物查询形态：`people_page()`最多取40人后，对每个人调用`_album_summary()`；它调用`person_summary()`分别查询有效脸数和去重照片数，再单独查一个有效头像。因此一页最多约120个成员/头像查询，另叠加最多40个懒加载头像HTTP请求。它是值得单独测量和优化的高概率开销点，但本轮没有合成规模计时、SQL trace或浏览器网络时序，不能断言它就是用户现场的唯一瓶颈，也没有把它修进身份/恢复审查范围。建议后续建立聚焦人物列表响应的测量/优化任务：记录API耗时、SQL查询数和头像请求耗时，再将计数和头像选择批量化并保留`MEMBER_PREDICATE`语义。

## 未验证边界与升级判断

没有真实浏览器闭环、应用重启、实际schema迁移catalog、私人识别准确率/错归、GPU或30万吞吐证据。DETAIL-A独立审查未完成；本次按DETAIL-B卡要求检查其被复用的解码/产物/人工链，不追认A审查通过。未发现V3.1 §2.2六类DS-MAX升级条件：没有无法定位的跨模块异常、状态矛盾、测试与ADR冲突或修复级联回归。DS-H阶段结论收敛，但人物身份/跨profile/人工知识属于卡面指定高风险聚焦项，故主审交接时要求独立会话完成Sol聚焦短审；现已完成，见文末终审结果。

## Sol聚焦终审压缩交接（主审时快照，终审结果见下节）

- **基线/改动**：`73bca7e..5d7bc13`的DETAIL-A/B完整提交；后续`d4f7720` source头像路由profile投影修复；P4-PERSON-PERF-A runner改动见其实现报告/当前worktree。主审没有改产品代码。
- **建议结论**：工程面未见阻塞，`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。
- **只需裁决的边界**：① v1/v2/source白名单与准确性证据只支持本轮实测的v2↔source；② source generation READY但不激活全库；③迟到人工控制/排除/OPEN与JPEG跨帧不可验证hold；④有效成员排除旧机器投影的谓词与撤销后requeue。
- **最小证据/反例**：final34覆盖映射确认→撤销→真实spec重排、未知身份门禁、JPEG跨帧人工保护和schema升级；公开CPU JSON为9组双向组合；avatar29覆盖普通/source文档冷缓存GET/HEAD及拒绝条件。
- **输入范围**：本报告、当前review卡、ADR-0017上述边界，以及上述JUnit/XML和兼容JSON。不要重复全diff或重跑完整测试；如果发现直接矛盾，只补读取被点名符号/测试及最小反例。
- **尚未做**：现场列表测量、真实图库/浏览器/GPU/30万验证；另立人物列表性能卡，不并入Sol身份裁决。

## 2026-10-09 聚焦终审结果

结论：**SOL FOCUSED REVIEW PASS / DETAIL-B REVIEW PASS**。未发现四项指定高风险语义的阻塞缺陷，也未发现报告、ADR与代码间需要补反例的直接矛盾。本次为聚焦裁决，复用已完成的独立工程主审；没有重新执行全diff审查或成功套件，没有修改产品代码。

| 必裁决边界 | 裁决与直接依据 |
|---|---|
| v1/v2/source白名单与证据 | 通过。`face/detail_policy.py::compatibility_verified`固定权重fingerprint、普通/source配置hash及spec字面值；未知身份或配置漂移关闭source桥。v1↔v2沿用ADR-0014的同识别像素几何例外，source具有独立spec/preprocess/artifact/generation身份；本轮公开CPU JSON只实测v2↔source的9组比较，不是v1质量复测或私人准确率证据。 |
| source世代与候选绑定 | 通过。`face/index.py::active_generation/candidates`核对确定性generation ID、READY、space、processor、spec、512维、cosine/L2；只读取当前artifact绑定的CONFIRMED entry，并要求候选与canonical embedding的vector hash相等。`_make_generation_ready`明确不将source设为全库active；此处`active_generation()`对source表示其自身可用性。这些门禁与不可变发布身份共同约束比较，不以READY单一字段推断兼容。 |
| 写事务人工保护与跨帧 | 通过。`face/index.py::_reconcile_asset`在权威写事务重读logical control revision、人工锁、有效排除、canonical、同图占位与OPEN hold；source同时重新生成映射候选，人工旧锚点继续保护。`media/face_crop.py::recorded_face_frame`通过现有发布字节checksum及face/bbox/input/profile绑定恢复坐标帧；`persons/mapping.py::_detail_comparison_face`按两轴换算，`unverified_detail_hold_face_ids`在真实旧人工产物帧不可验证时hold整张照片的source脸。 |
| 有效成员、撤销与A复用链 | 通过。`detail_policy.superseded_machine_sql`只在同内容、策略和ready派生状态下排除旧机器投影，保留人工指定/移出、MANUAL链接、有效排除及OPEN锚点；成员、相册与候选复用该谓词。`persons/operations.py::_repair_candidates`按face实际spec预约世代，`mapping.requeue_asset/undo_face_mapping_operation`在撤销恢复OPEN后按实际source spec重新排队并恢复hold；无授权的DETAIL-A source保持人工处理边界。B复用A的产物/人工链已覆盖，不追认A独立整卡通过。 |

证据复核：直接解析 `test-results/P4-PERSON-DETAIL-B-final34.xml`、`test-results/P4-PERSON-DETAIL-commit.xml`，均为 **34/0/0/0**；PERF-A为 **16/0/0/0**，AVATAR-FIX-A为 **29/0/0/0**。对照关键测试源码中的未知spec/config拒绝、source非active、旧机器替代、晚到人工锚点、JPEG帧不可验证hold及映射确认→撤销→source重排断言。兼容JSON的fingerprint/config hash、CPU provider及9组范围与实现/报告一致。当前PERF-A差异仍仅删除reconcile后的重复收尾；reconcile在有归属决策时发布其outbox，未改变上述身份边界。

本轮命令：Git状态/基线核对，关键符号与测试源码定点读取，PowerShell XML解析，scoped `git diff --check`；未运行pytest、模型或服务。没有新增测试PASS声明；既有JUnit属于被复核的历史运行证据。当前基线HEAD `d4f7720`，含工作区PERF-A runner及直接测试维护；其他并行改动保留。

DETAIL-B独立审查闭环现可标PASS，限于卡面授权的定向证据与工程/聚焦审查。真实图库、私人识别准确率、实际catalog现场升级、浏览器/重启、GPU/30万吞吐仍未验；人物列表性能需另立测量任务。没有扫描私人照片、访问PhotoSearchData、改ACL、下载模型或推送。下一步由用户选择现场验收或独立性能任务，本轮不自动接续。
