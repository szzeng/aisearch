# P4-PERSON-DETAIL-A — 独立DS-H全受影响面工程审查

日期：2026-10-04。状态：**PENDING**。实现报告：[实现与定向验证](../reports/P4-PERSON-DETAIL-A-implementation.md)。本卡关键规则已由用户完成SOL核对；本阶段负责独立工程审查，不重做该规则核对或重复昂贵实验。

## 审查方式

- 由独立DS-H按V3.1 §2.4与主架构§11做完整受影响面主审，检查实际HEAD/diff和本报告JUnit证据。实现者不是本审查者；模型由用户在Codex中手动切换。
- 只有V3.1 §2.2点名的升级条件被证据触发时才升DS-MAX。DS-H无阻塞后，Sol high只核对身份/ADR/人工知识不变量和未决项，不重复全diff walkthrough。
- 不运行完整unit/integration、既有人物216项、30万性能/质量矩阵、打包、真实库扫描、真实模型准备或未授权真实样片。发现具体回归风险时只加跑与反例直接相关用例。

## 必查表面

1. `face.engine`/`media.decode`：普通路径字节与默认行为未变；详情路径JPEG完整解码、方向和像素上限、单轮640检测、实际横纵缩放映射、局部源裁剪/边界、ArcFace 112对齐、原图帧质量尺寸和截止时间正确。
2. `face.contract`/`processor`/artifact发布：face/spec/config/artifact/generation身份彼此隔离且可恢复；详情schema闭合、哈希可重算；重复/中断发布、陈旧输入和重试不覆盖v2或已发布不可变产物。
3. `service`/scheduler/API runner：SQLite job唯一身份包含详情任务类型；重复点击复用、终态可重排、持久job与单线程唤醒并发/关闭安全；runner自己的Catalog连接仅在其工作线程打开；只执行显式入队详情任务，设备资格/真实runtime记录沿用既有契约。
4. `mapping`/`face.index`/人物写事务：旧人工指定、未归类、排除、封面及撤销不丢；详情profile映射、OPEN hold、确认/拒绝/撤销/requeue/repair按实际spec；详情profile不进入自动index或自动association，不借v2 asset-keyed队列覆盖普通任务；普通v2对详情hold隔离。
5. `persons.albums`/`media.face_crop`/API/UI：详情结果只在目标照片可见且显示profile；ArtifactStore内容、frame和face ID核验完整；人工指定是显式动作；POST与job读取保留session/CSRF、脱敏及路径边界；前端并发照片/重复点击/关窗/失败重试/旧响应/完成刷新无竞态。
6. 验证与报告：读完JUnit XML，确认23项是真实执行结果；Ruff、Node、scoped diff-check范围对应改动；未验证真实照片效果、浏览器、全量与打包均保持准确表述。检查`docs/development-status.md`和实现报告链接/状态一致。

## 交付与停止

输出独立报告，逐项记录PASS、发现、反例与证据；直接修复仅限具体审查缺陷，重跑失败用例及直接回归。DS-H完成后，由Sol high聚焦核对身份/ADR/人工知识不变量与任何未决项，并将结果回写本卡及development-status。不得因DS-H或本卡完成而宣称真实识别准确率或30万性能通过。
