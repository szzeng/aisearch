# P4-PERSON-COVER-A — 手动选择人物封面

> **2026-10-09个人待审范围更新（当前优先）：** 按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)与[本组唯一入口](P4-PERSONAL-REVIEW-PERSON-A.md)收口。下文保留历史实现要求；冲突的重复全量/30万/模型/打包/固定多轮审查及浏览器组合要求，以本次个人基础范围为准。先复用可核对证据，缺口才补最小测试；首次审查覆盖完整未审新增面，身份、人工状态、撤销、迁移/恢复保护仍必留。此更新未执行审查或新增PASS；已有现场未验事项照实保留。

日期：2026-10-04。状态：IMPLEMENTED / 简单验证完成；独立审查PENDING。用户授权新增人物相册封面选择，保持个人使用简单流程与小型测试。

读取：AGENTS → development-status → P4-PERSON-CLEANUP-A → 本卡；已读主架构§8.3～8.3.5、策略§2.4/3.2/3.4；直接相关albums/members/person bundle及受保护API/UI。

范围：复用persons.avatar_face_id，选择当前人物有效成员的人脸作为封面；入口“更换封面”→照片预览→“设为封面”，合照不得错选他人。保存后相册与人物卡片更新，持久化，并延用失效头像的只读补选。新增独立PUT元数据接口，校验session/CSRF、canonical ID、person revision及有效成员。只更新头像、人物revision/updated_at；不改名字、归属、人工控制、原图、schema/索引/模型。封面通过再次选择覆盖，不写虚假的RENAME日志、不扩展旧操作类型；既有撤销检测新的revision拒绝覆盖后来选择。

验证：用户个人项目简单测试，定向合成API（保护、持久保存、拒绝他人/建议/移出/排除、过期零写入、合并ID、无语义副作用、旧撤销冲突）与实际JS最小DOM（选择/提交/同步/失败/防重复）+Ruff/Node/diff-check，JUnit报告。无真实库、模型、全量、性能、打包或服务重启。

收尾：报告/status及现有人物独立审查卡加审本面，不把实现验证当独立PASS。

完成：定向JUnit23/0/0/0、Ruff/Node/diff-check通过，见[实现与简单验证](../reports/P4-PERSON-COVER-A-implementation.md)。无真实库/服务重启；重启刷新生效，无需建库。下一人物阶段沿用P4-PERSON-UX-A-review。
