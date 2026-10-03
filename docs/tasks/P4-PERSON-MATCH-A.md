# P4-PERSON-MATCH-A — 个人相册0.50归人门限与已存特征重匹配

日期：2026-10-03。IMPLEMENTED / SCOPED VALIDATION COMPLETE；独立审查PENDING。

用户授权“帮我改一下，改完后如何让人脸重新匹配，需要重新建库吗”，覆盖P4-PERSON-UX-A原禁止调门限的限制；保留“个人项目，完成后简单测试”的范围。本轮只实现，不对真实库执行。

读取：AGENTS→status→P4-PERSON-UX-A/报告→直接实现；主架构§8.3～8.3.4、§11.3.3，策略§2.4、§3.2/3.4；ADR-0007人工控制、ADR-0010 hold、ADR-0014明确v1/v2兼容。

范围：自动归人cosine相似度0.56→0.50；检测0.50、建议0.29与全部质量/冲突/人工保护不变。历史CALIBRATION保留0.56推导，新ASSOCIATION_POLICY说明公开产品默认值参考及用户试用选择，不伪称新测量。检测spec、预处理、face_id、artifact、generation及ENGINE_CONFIG身份不变。

新CLI rematch复用规范向量和候选发布/确认，只选择ACTIVE、当前内容、同空间buffalo_l v1/v2、无CONFIRMED归属且无人工锁的脸。排除和hold复用既有事务门禁。新v2产物（包括零脸）已发布后旧机器脸不复活；异空间不处理。可选asset_ids限制reconcile不处理未选中的旧queue。错误data-root不创建空库。保留既有AUTO/MANUAL归属、人物名/ID；不自动合并已拆成多个组的人物。

禁止：真实库/PhotoSearchData访问、自动重匹配/重检测、模型下载/加载、schema/依赖、全量/30万/性能/打包。没有新网页入口或破坏性重聚类。

简单验收：0.4999/0.50/0.53边界；v1/v2旧门限下已排空建议重匹配；人工移除/指定/排除与向量/检测不变；低质量、hold、重复运行、limit=0、缺失索引、错误目录、零脸新发布/异空间。定向JUnit＋Ruff＋diff-check。

结果79/0/0/0，CLI帮助/Ruff/diff-check通过，见[报告](../reports/P4-PERSON-MATCH-A-implementation.md)。后续必要阶段沿用[P4-PERSON-UX-A-review](P4-PERSON-UX-A-review.md)，加审本卡新增面；不自动启动Agent或切模型。真实效果与独立审查未验证，不标正式PASS。
