# P4-PERSON-DETAIL-B — GPT-5.6 Sol / high 聚焦终审

日期：2026-10-09。阶段：**SOL FOCUSED REVIEW PASS / DETAIL-B REVIEW PASS**。上一阶段完整工程主审见[独立审查报告](../reports/P4-PERSON-DETAIL-B-review.md)。任务卡原建议模型：**GPT-5.6 Sol / high（C2）**；模型路由为建议，本轮未执行或声称模型切换。聚焦终审已完成，结论见文末。

## 目标

只裁决DS-H报告列明的人物身份、跨profile兼容和人工知识边界；判断ADR-0017与当前实现及最小证据是否一致。完成后把聚焦结论写回本卡、主审报告与`docs/development-status.md`。若无问题，才关闭DETAIL-B审查；真实图库、私人准确率、人物列表性能、GPU/30万吞吐仍属于未验或另立任务。

## 只读输入

按`AGENTS.md → docs/development-status.md → 本卡 → docs/reports/P4-PERSON-DETAIL-B-review.md`读取。按主审报告列出的符号查看ADR-0017及关键实现；不重读完整架构/策略、不复制全diff、不重跑成功套件。

## 必裁决边界

1. v1/v2/source白名单及其证据范围：公开CPU样片只实测v2↔source，不把它外推为v1本轮质量证据。
2. source generation满足READY/space/processor/spec/dimension/metric/normalization后可用于比较，但不会接管全库active指针；artifact、CONFIRMED entry和向量hash绑定是否足够。
3. 自动写事务迟到控制revision、人工指定/移出、有效排除、同图占位与OPEN hold；source跨JPEG draft帧转换要求旧产物可验证，无法验证时保守暂停归属。
4. 旧机器替代的有效成员谓词、操作撤销后按真实spec重排/requeue以及DETAIL-B复用DETAIL-A人工保护链。

## 证据与边界

可复用：`test-results/P4-PERSON-DETAIL-B-final34.xml`和`...-commit.xml`（34/0/0/0）、`test-results/P4-PERSON-PERF-A.xml`（16/0/0/0）、`test-results/P4-PERSON-AVATAR-FIX-A.xml`（29/0/0/0）、`docs/reports/P4-PERSON-DETAIL-B-compatibility.json`（公开夹具9组、CPU）。仅当报告、ADR或代码间出现具体矛盾时，才补最小直接反例及对应测试。

主审未发现需要DeepSeek max的V3.1 §2.2条件；无未解决工程缺陷。人物列表逐人物N+1查询是独立性能线索，主审无现场时序/SQL trace证据，不属于本次身份裁决。不得据此宣称私人准确率、真实图库、schema现场升级、浏览器、GPU或30万性能已验。

## 结果写法与停止点

通过时写`SOL FOCUSED REVIEW PASS`，确认ADR-0017上述高风险语义；然后DETAIL-B审查闭环才可标PASS。发现问题时写具体不变量、调用链、最小反例及最小修复范围，退回DS-H定点修复；不自行扩大为新功能或全量重审。无论结论如何都不运行真实图库任务、不扫描私人照片、不改ACL、不下载模型、不推送。

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
