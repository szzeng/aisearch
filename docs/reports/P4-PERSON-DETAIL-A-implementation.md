# P4-PERSON-DETAIL-A — 实现与定向验证

日期：2026-10-04。状态：**IMPLEMENTED / 定向验证通过 / 独立DS-H受影响面审查待做**。

## 实现

- 普通人脸路径保持原配置和payload形状。详情路径复用 buffalo_l 与 SCRFD 640 检测、ArcFace 112 对齐；原图按EXIF方向规范化后完整解码一次（JPEG不使用draft，最多6000万像素），仍只检测一轮。工作帧坐标按实际x/y尺寸比例映射回源帧，再从原图局部裁剪和对齐，不把整张大图送入识别网络。任务总时限沿用120秒。
- 新增独立`image_face_detail`任务、spec/config hash、artifact revision、face ID与generation身份。相同输入及检测框不会覆盖普通v2结果；同输入已有已发布详情产物时再次点击直接复用。SQLite job仍为持久队列，API使用单线程runner唤醒，多个显式选择串行处理；进程重启丢失的只是内存唤醒状态，重新点击会唤醒未完成job。
- 详情产物/人脸在人物照片预览中单独标注，只能由用户显式“更正这张脸”指定人物，不进入自动向量索引outbox或人物association队列。新旧profile映射、OPEN hold与后续requeue按实际spec读取；测试覆盖详情profile hold不会串到v2。旧人工归属/control保留。安全人脸裁剪读取同时校验详情artifact/spec revision与源帧。
- 详情API复用现有session/CSRF边界。失败可重试，重复请求复用durable job，成功后刷新照片；关闭预览不会取消已入队任务。未授权或未触发真实图库/PhotoSearchData访问。

## 验证

最终定向JUnit：**24 tests / 0 failed / 0 errors / 0 skipped**，结果：[JUnit XML](../../test-results/P4-PERSON-DETAIL-A-targeted.xml)。覆盖实际源帧比例/关键点映射、单次检测、正常/详情解码参数、源细节裁剪与新旧身份分离、详情artifact帧profile校验；synthetic API session/CSRF/任务去重、SQLite新旧profile隔离与人工anchor/OPEN hold、runner串行去重；实际前端JS小DOM入队/轮询/失败重试/刷新；既有安全人脸缩略裁剪回归。

通过：指定Python文件Ruff、`node --check src/photo_search/web/persons-albums.js`、改动文件`git diff --check`。Pytest首次在sandbox清理项目`.pytest-tmp`时遇WinError 5；按AGENTS.md正常Windows权限路线重跑，JUnit退出码0。开发期一条profile mapping断言因合成锚点关键点不满足候选几何条件而失败；修正夹具使用锚点自身关键点后，该定向用例通过。

## 未验证与交接

未跑全量unit/integration、既有人物216项、30万容量/性能、打包或跨GPU。未运行真实buffalo_l推理、真实照片小样本或真实浏览器；故不报告模型兼容准确率、识别改善或实际耗时。运行时会沿用现有设备资格和worker会话；3～5张授权问题照对照留作个人试用，真实效果待确认。

下一步按[独立审查卡](../tasks/P4-PERSON-DETAIL-A-review.md)由独立DS-H完成完整受影响面审查；高风险身份/人工知识结论再按策略交Sol high聚焦核对。没有审查前不将整卡标PASS、不推广跨profile自动比较。
