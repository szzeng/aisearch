# P4-PERSON-AVATAR-FIX-A — 原图人脸头像409修复

> **2026-10-09个人待审范围更新（当前优先）：** 按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)与[本组唯一入口](P4-PERSONAL-REVIEW-PERSON-A.md)收口。下文保留历史实现要求；冲突的重复全量/30万/模型/打包/固定多轮审查及浏览器组合要求，以本次个人基础范围为准。先复用可核对证据，缺口才补最小测试；首次审查覆盖完整未审新增面，身份、人工状态、撤销、迁移/恢复保护仍必留。此更新未执行审查或新增PASS；已有现场未验事项照实保留。

2026-10-09。用户反馈人物头像均显示暂时无法加载，GET face thumbnail返回409。基线`d94279b`加并行搜索／查看器工作区；仅处理头像接口与已发布人脸profile的字段传递，保留其他改动。

读取：AGENTS→development-status→本卡；复用P4-PERSON-IMAGE-A的媒体范围、DETAIL-B实际profile校验。主架构§5.4／§8.3／§10.3，策略§2.4、§3.2/3.4；不改协议或架构，无需全文重读。

目标：合成已发布普通／source-detail文档通过真实GET/HEAD冷缓存头像路由；原图必须携带实际spec_revision供坐标帧校验。只补查询投影，保留checksum、内容revision、face_id、bbox及profile校验，不用猜测或不安全fallback绕过409。

验证：`test_p3_face_thumbnail.py`、`test_p4_person_image_frames.py`、`test_p4_person_face_geometry.py`，直接合成缩略图API回归、checksum／bbox／内容拒绝、source-profile匹配拒绝／几何用例，新增普通／原图×GET／HEAD反例；pytest -q，.uv-cache／.pytest-tmp项目内，JUnit `test-results/P4-PERSON-AVATAR-FIX-A.xml`；Ruff、scoped diff-check。不跑全量、模型、GPU、真实图库、PhotoSearchData或重识别，不自动提交／推送。

交付：实现报告与status说明已复现错误码、修复、定向结果和现场重启待验证。该补充沿用DETAIL-B独立受影响面审查，加入真实published source文档的头像路由链；不能用本次自检代替独立审查。

结果：**IMPLEMENTED / 定向29项通过 / 现场重启待验证**。普通路径修前通过，原图GET/HEAD修前均409 `FACE_STATE_CONFLICT / face_frame_invalid`；仅补查询spec_revision后29/0/0/0，Ruff/diff-check通过。[实现报告](../reports/P4-PERSON-AVATAR-FIX-A.md)。
