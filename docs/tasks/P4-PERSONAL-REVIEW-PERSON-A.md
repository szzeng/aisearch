# P4-PERSONAL-REVIEW-PERSON-A — 人物待审任务一次收口

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED**。独立审查已完成，见[人物组审查报告](../reports/P4-PERSONAL-REVIEW-PERSON-A.md)。AVATAR-FIX-A的正常重启/刷新现场确认仍单项PENDING；不重启用户实际服务。按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)收口；未重跑有可核对证据的套件。

入口：AGENTS → development-status → 本卡 → 各项直接报告/当前Git。只读主架构§5.4、8.3～8.3.5、10.3及ADR-0006/0007/0010/0014/0017相关条款；不全仓重读。

## 1. 先复用已独立通过的面

[DETAIL-B工程与Sol报告](../reports/P4-PERSON-DETAIL-B-review.md)已覆盖A/B共享的解码/不可变产物/source白名单/候选绑定/人工保护/头像profile，34/34/16/29项与公开CPUJSON作为历史证据。列清当前后续diff，未变部分引用该报告，不重新完整工程审查或模型对照。ALBUM-PERF、SORT个人范围已PASS，不重开其任务。

DETAIL-A仍需独有手动入口的覆盖确认，不自动追认整卡通过；人物原UX/MATCH/UI增量未获完整独立结论，必须查看其全部新增面与直接调用链。

## 2. 本组完整新增面（每项先证据，缺口才补）

| 原任务/输入 | 必留行为 | 优先复用/缺口候选 |
|---|---|---|
| [UX-A报告](../reports/P4-PERSON-UX-A-implementation.md) | 命名冲突零写入、旧name与重放、合并/更正/撤销、去重成员/分页、几何版本及人工hold | 219项、升级子集3项、已有浏览器；DETAIL-B已审的共享保护只引用 |
| [DETAIL-A报告](../reports/P4-PERSON-DETAIL-A-implementation.md) | 手动选择才入队、重复点击复用、串行runner/连接归属、未授权结果不自动归人、状态/重试 | 24项；相对B尚未覆盖的A分支才看代码/补测 |
| [MATCH-A](../reports/P4-PERSON-MATCH-A-implementation.md)、[MATCH-B](../reports/P4-PERSON-MATCH-B-implementation.md) | 合照候选完整比较、排除/锁/OPEN、已确认关系不拆、READY与代表资格、显式有界补匹配 | 79/216项；按当前策略核对未知spec与旧断言，不能放宽白名单修绿 |
| [IMAGE-A](../reports/P4-PERSON-IMAGE-A-implementation.md)、[AVATAR](../reports/P4-PERSON-AVATAR-FIX-A.md) | 正确照片/脸帧、checksum/content/profile绑定、GET/HEAD冷缓存与拒绝 | IMAGE29项，AVATAR29项与B审查；现场头像只剩正常刷新确认，不重识别 |
| [CLEANUP](../reports/P4-PERSON-CLEANUP-A-implementation.md)、[COVER](../reports/P4-PERSON-COVER-A-implementation.md)、[PREVIEW](../reports/P4-PERSON-PREVIEW-A-implementation.md) | 更正后空组/撤销恢复、只选本人有效封面/他人拒绝、失效头像回退、前后/关闭旧响应不串图 | 13/23/7项；共享成员/权限只检查一次 |

涉及实际实现：persons的albums/read/members/operations/mapping，face的contract/index/service/engine、source runner、media face_crop、直接API/UI/test diff；每张原报告列出的其他新增文件也纳入，不能只挑代表文件做首次独立主审。

## 3. 最小补测与一次基础流程

不默认重跑上述任何整套。存在未覆盖/改变的行为时优先选择 `tests/integration/test_p4_person_albums.py` 中命名冲突、单脸更正及撤销；封面/媒体用原报告点名的直接节点；DETAIL-A手动任务用其原24项对应节点。记录精确选择原因和JUnit；同一个共享拒绝/撤销证明不在各原卡重复跑。

可复用当前已完成的合成浏览器记录：头像→照片→详情→前后→返回；取名/封面、更正一张合照里的单脸→撤销。缺哪步只补哪步，原图识别真实质量留给自愿授权样片，不加载模型。不用私人目录，也不要求重测真实可见速度。

## 4. 结论

输出 `docs/reports/P4-PERSONAL-REVIEW-PERSON-A.md`，按每张原卡列已覆盖、剩余、范围结论；复用B结论不等于重新由本审查完成B。一般DS-H即可完成个人范围收口，SOL_NOT_REQUIRED；新冻结身份规则或人工安全未决才形成精确Sol短裁决，已裁决部分不重复。

必要项缺失保留对应PENDING；关键失败给反例并修直接面。只完成人物组，下一组为地点，其他审查不自动启动。WinError/ORT按实际环境证据处理，不换ACL或扩大GPU专项。
