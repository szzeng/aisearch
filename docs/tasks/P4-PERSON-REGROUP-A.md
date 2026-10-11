# P4-PERSON-REGROUP-A — 选中脸重新智能分组与整理模式完成按钮

日期：2026-10-11。状态：SCOPED PASS / 用户指定当前会话受影响面复核。

用户在已完成的 P4-PERSON-UX-SIMPLIFY-A 上明确新增两项，仅执行本卡：
1. 移出并重新智能分组，保留原人物排除、复用已存向量、安全门限和候选索引。
2. 整理照片进入后变为完成整理，退出仅清空选择，每次归属操作独立保存。

上游 UX 卡的“只整合入口”边界不能容纳新写协议；因此另立本张小范围业务卡，
采用 [ADR-0020](../decisions/ADR-0020-selected-face-regroup.md) 的加性协议，不修改原三个 action 的语义。
用户原“直接这里复核”沿用，记录当前会话受影响面复核，不能称独立 DS-H/Sol 冻结通过。
用户明确只要必要定向测试，按此执行，不重复全 B/D、真实图库/GPU或性能门禁。

接手：AGENTS → development-status → 本卡 → ADR-0020、ADR-0007 §3–8、ADR-0010 §3.4–5；
实际代码 face/index.py、face/service.py rematch_unassigned、persons/face_correction.py、
split.py 选择/CAS、operations.py、retention.py、API 与两份人物 JS。
本轮为新增状态协议核对了当前主架构与 V3.1 策略；不改阈值、模型或全人物结构。

实现范围：一个新端点/operation type/0029 迁移；复用 FACE_CONTROL before/after 与补偿撤销，
事务内只更新选中的 face_id。中断未提交全回滚；提交后同 request_id 返回原结果，
索引发布继续使用既有持久 outbox。没有两个部分成功的人工更正请求。

定向验收：合成 10 脸同人/已有人物/不同人/低质量/歧义/相似度不足/无同伴；
源排除、其他人工归属与合照未选脸、控制/关联/源 revision、OPEN hold；
重复/中断恢复/幂等冲突/撤销冲突；v28 历史迁移/回滚/备份与旧程序拒写；
整理/完成按钮、选择数量、取消、人物切换、读取刷新和忙时保护；
直接受影响的旧人脸协议、补匹配、人物 UI、JS 语法、Ruff、JUnit 和 diff-check。

报告：[P4-PERSON-REGROUP-A](../reports/P4-PERSON-REGROUP-A.md)。定向 JUnit 133/0/0/0 + 72/0/0/0，最终忙时退出补充 8/0/0/0（去重 197 项）；Ruff、Python 编译、Node 与 diff-check 通过。未做本轮真实浏览器/真实图库/GPU/全仓/性能/实际库升级验证。
完成后转用户少量试用，不自动继续重建、模型、性能、清理或发行任务；用户已授权本次人物任务本地提交，未授权推送。
