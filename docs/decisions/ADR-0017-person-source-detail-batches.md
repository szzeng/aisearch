# ADR-0017 — 人脸原图细节 profile、批次与自动归组边界

日期：2026-10-05。状态：**历史原型方案，已由本轮完整实现方案取代，不是当前生效规则**。同任务当前决策及真实对照证据见 [ADR-0017 后续实现](ADR-0017-person-source-detail-auto.md)及[实现报告](../reports/P4-PERSON-DETAIL-B-implementation.md)。保留本文用于说明 Luna 部分实现的原始边界；下文“跨 profile 关闭／schema19／模式重试”等描述不代表完成后的实际行为。

## 背景

DETAIL-B 要为旧图库提供一次启动、持久恢复的原图补识别，并让之后的建库/增量默认使用 DETAIL-A 原图裁脸。普通 v2 与 source-crop 使用不同检测框、裁剪和质量像素语义；相同权重、空间与向量维数不能代替真实比较证据。DETAIL-A 的人工结果及前置独立审查仍保留原状态。

## 决定

1. source-crop 使用独立 `spec_revision`、任务类型和 artifact revision。普通 `face_association_queue` 保持 asset 唯一语义；source-crop 使用 `(asset_id, spec_revision)` 独立队列。DETAIL-B 的旧库批次只处理 SQLite 已知机器未确认/建议/符合 SMALL 条件的人脸所在 asset，不扫描磁盘、不把完整 asset ID 列表放入内存。
2. 新库/增量的人脸处理选项默认为 `source_detail`，用户可对新请求显式选 `lightweight`。实际 job 的 task type 固定处理路径，已发布且位置仍匹配的 source-detail artifact 可经 checksum 验证后复用，不再运行模型；变化的输入仍按新 revision 建立新产物。
3. 同一 source-detail generation 内，只有没有其他 face profile 的 asset 才进入自动 association。与旧 profile 同图的新结果可以发布到隔离的候选世代，但不会和旧向量自动比较或自动归入人物。跨 profile 互认保持关闭，待授权的 3–5 张小样本、真实已安装模型双向/异人对照、前置 DETAIL-A 与本卡 DS-H 审查及后续 Sol 聚焦裁决后另行决定；没有证据不得以同维向量放开。
4. 人脸推理由一个进程内共享锁串行；批次游标、边界、计数、每 asset 进度和 job 身份均持久化。暂停在照片边界生效，重试复用原逻辑 job，启动时恢复 QUEUED/RUNNING 批次。未完成的 job 或其派生 index/association 不得被批次误报为完成。
5. 自动/人工控制、有效排除与 OPEN redetection hold 均优先于机器归组。此次实现不修改 DETAIL-A artifact 字节、普通 v2 身份、人物 ID，也不自动改名、合并人物或补扫从未检测到的脸。

## 后果与限制

- schema 19 增加独立的 source-detail association queue、batch、batch asset 进度表；旧队列约束不变。
- 新用户请求默认走 source-crop，但 SCRFD 仍在 640 检测画布运行，完全未检出的脸不会因此出现。
- 与历史 profile 同图的 source-detail 结果待人工复核；本 ADR 不声明跨 profile 识别率、速度或错归率。
- library refresh 本身沿用现有进程内刷新生命周期；任务类型和已提交 face jobs 可持久恢复，刷新尚未到人脸阶段时的重新发起选择由用户确认。不得将这点描述成刷新运行本身具备持久队列。

## 实施与核查

代码与小型合成验证见 [DETAIL-B 实现报告](../reports/P4-PERSON-DETAIL-B-implementation.md)。独立核查范围见 [DS-H 审查卡](../tasks/P4-PERSON-DETAIL-B-review.md)。本决策未经独立审查，不是正式冻结；审查未完成及真实互认门禁未验证前，不标整体 PASS。
