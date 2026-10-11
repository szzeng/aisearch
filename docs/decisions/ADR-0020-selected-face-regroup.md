# ADR-0020 — 选中脸移出并重新智能分组

日期：2026-10-11。状态：用户新增需求的加性实现决策 / 当前会话复核；不是独立冻结结论。

## 必要的最小扩展

ADR-0007 的 unassign 必须进入 MANUAL_UNASSIGNED，restore_auto 保留排除且不采用建议；
rematch_unassigned 按 asset 调度，不能用于“只处理选中的脸”。原协议保持不变。
新增 `/api/v1/persons/{person_id}/regroup`、`p4-face-regroup.v1` 与 `FACE_REGROUP`，
0029 只扩展操作表闭合 type，原样保留全部既有表/记录/引用；不建立新归属或新操作真值表。

## 原子状态转换

请求指定 1–500 张唯一 face_id、源人物 revision、每张脸 control 与有效 assignment revision。
全局 request_id 幂等先于 CAS；载荷含请求源 ID、排序后的脸列表和全部 revision。
全批先核验有效源成员、当前资产/支持世代、脸模式/唯一有效关联、OPEN 旧脸或候选 hold。
任意拒绝整体零权威写入。用户明确选择当前人工成员并通过 CAS 才授权解除该脸人工锁；
不自动解除未选脸，OPEN 不被隐式确认或拒绝。

候选索引先按既有持久 outbox 恢复发布（一次上限 2000 事件，无检测、无重新生成向量、
无关联其他脸）。READY/未完成发布或修复检查不满足时拒绝并保留原归属，稍后可重试。
真正权威事务内读取候选和全部保护，然后：

1. 开同一 person_operations 记录，保存每张脸 before。
2. 结束所有选中脸的原有效关联；激活 canonical 源排除，保留其他排除；control 只 +1 到 AUTO_ALLOWED。
3. 用 face/index.py 的 candidates/decide_for_face/_confirm/_suggest 和既有门限逐脸判断。
   候选不含选中脸的旧身份；已建立的本批机器组作为临时候选供后续脸使用。
   同图已有对应人物仍按 SAME_IMAGE_CONFLICT 留建议；canonical 排除持续生效。
4. NEW_GROUP 更保守：至少有另一张不同照片的高质量选中脸达到原 MERGE 门限；
   孤立无可靠匹配、低质量、borderline 和 near-tie 留待整理，不强制新建单脸人物。
5. 保存最终 FACE_CONTROL after 与安全 outcome，按既有顺序一次发布 PERSON_CHANGED、
   filter revision、资产 projection queue 与候选 PENDING_REPAIR；提交后回复历史真实计数。

事务内不进行原图读取、模型推理、文件/向量库写入。没有 unassign/restore_auto 两次请求，
也没有部分完成的后台人脸身份任务：中途故障整批回滚，重启可用同载荷重做；
提交后丢失响应则同 request_id 重放最终结果。派生候选修复自身由旧 outbox 恢复。

## 结果与撤销

grouped_faces 只计实际 CONFIRMED 新组/已有组；new_persons 计真正新建的机器人物；
remaining_faces 是剩余选中脸。建议不计成功，返回值不泄露向量、分数、几何或路径。
重放从最终快照计算原操作结果，不读后来活状态；UNDONE 明示已撤销，不能冒充再次归组成功。

通过原共享 undo 端点复用整批 after 比较及前值补偿，logical/link revision 前进。
新出现的 OPEN hold 同样拒绝本动作的 undo；任何后来人工编辑使整批撤销拒绝。
撤销只恢复所选脸，不删除机器新组身份：空未命名组由原浏览过滤隐藏；
后来加进该组的其他脸或人物命名/合集仍保留，避免撤销越界。
FACE_REGROUP 及其 UNDO 暂不进入旧快照清理资格，保留结果快照与幂等/撤销保护。

## 三种操作与界面

仅移出，留待整理：MANUAL_UNASSIGNED，拒绝自动归组。
移出并重新智能分组：显式选中脸转 AUTO_ALLOWED，保持源排除，按实际结果反馈。
拆成新人物：原 split，全部选中脸成为一个新人物的人工成员，原限制/撤销不变。

整理照片进入模式后显示完成整理，选择与操作独立显示；完成只清空未执行选择，
不写数据库、不撤销已保存动作。正在写入或结果未知待重试时禁止退出/清空/切人物/重复保存。
成功后仍处整理模式且清空旧选择；刷新/取消选择不暗示统一提交。
