# P4-PERSON-IMAGE-A — 人物相册错图和人脸裁剪偏移

2026-10-03。用户重新报告人物相册整图缩略图与预览不一致、小圆图偏离人脸；这是本轮明确修复授权，覆盖上一轮“这里不处理小图”的范围限制。起点3bab1c3。个人项目按此前授权仅简单、直接相关合成测试。

入口：AGENTS→status→P4-PERSON-UX-A→本补充卡；主架构§5.4、§8.3、§10.3，策略§2.4、§3.2/3.4；ADR-0014人工知识与几何身份保护。实现前核对Git，另一个任务的缓存/几何展示修复已经撤回，撤销提示仍保留；不依赖旧状态中的成功陈述。

范围：ID媒体URL重新验证，人物页新URL绕开浏览器已有的一年immutable缓存；人脸裁剪读取校验后的已发布v1/v2检测文档original_size坐标系，修复JPEG draft尺寸与完整原图尺寸的混淆。独立face-crop@2使旧错误裁剪不再被复用，两个轴按实际尺寸映射；无检测文档的旧人工/合成行沿用原图头尺寸，真实缺失/损坏文档拒绝猜测。

不改检测预处理/spec/face_id、不可变人脸产物、向量、名字、人工锁/排除/归属，不运行模型/重识别/全库重检，不扫描真实图库或PhotoSearchData。不改schema/依赖，不运行性能、全量或打包，不自动提交或推送。

简单验证：合成大JPEG经实际draft→分析替身→ArtifactStore发布→真实API裁剪，颜色区域证明居中；覆盖EXIF旋转、旧错误裁剪不复用、文档内容/脸绑定及checksum拒绝、HTTP缓存和条件请求；原API/人物相册直接回归，Ruff/Node/diff-check，保存JUnit。实现报告和status如实区分合成结果、真实照片未验证与独立审查PENDING。后续审查沿用P4-PERSON-UX-A-review，加审本补充展示/安全链路，不另开模型或性能任务。

结果：IMPLEMENTED / SCOPED VALIDATION COMPLETE；定向JUnit29/0/0/0、Ruff/Node/diff-check和合成缓存/相册/预览浏览器验证通过，详见[报告](../reports/P4-PERSON-IMAGE-A-implementation.md)。用户真实照片未访问，独立审查仍PENDING。
