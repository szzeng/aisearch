# P4-02Y-L — CPU/GPU OCR artifact identity 聚焦裁决

日期：2026-10-09。状态：**RULING COMPLETE / IMPLEMENTATION REQUIRED**。原推荐 **GPT-5.6 Sol / high**，用户手动选择；本次由用户指定的当前 Codex 会话完成聚焦裁决，未自动切换模型。

结果：[裁决报告](../reports/P4-02Y-L-artifact-identity-sol.md)、[ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)。拒绝以 quality tolerance 复用当前共享 structured identity；冻结版本化 CPU/CUDA processor，保留 raw confidence 与严格 checksum。用户本次明确选择 GPU 优先、CPU 自动兜底、每图一个当前结果；协调层用独立任务执行该策略，不允许 worker 假报设备。定点 JUnit 3/0/0/0，双向旧身份冲突和键隔离探针已验证；新产品路径尚未实现。下一步仅交接 [P4-02Y-L-IDENTITY-A](P4-02Y-L-IDENTITY-A.md)，推荐 DS-H / 高推理；OCR 总任务仍 PENDING。

## 唯一裁决问题

当前 OCR CPU/GPU 共享 `processor_id=ocr-text@1` 与 artifact identity，但真实 dense fixture 的 OCR 文本/几何相同、置信度最大差 0.0062746；artifact 原样序列化置信度，因此相同 input revision 得到相同 `artifact_id`、不同 checksum。现有 `ArtifactStore.persist_result` 将其拒绝为 `ARTIFACT_CONFLICT`。force re-recognition 跨设备会因此无法更新结果。

决定：任务既有 CPU/GPU tolerance 是否足以支持复用该 structured artifact identity？若否，批准的最小语义是什么，如何避免静默改写冻结的置信度/低置信度与投影语义？裁决必须给出一个明确的身份/规范化方向、其版本化落点，以及成功/拒绝反例；不能仅写“输出等价”。

## 输入

- [主审报告](../reports/P4-02Y-L-review-dsh.md) §高风险发现。
- [Sol 冻结报告](../reports/P4-02Y-L-sol-contract-freeze.md) 的决定 5 与 [ADR-0003](../decisions/ADR-0003-p2-processing-runtime-and-search-profile-contract.md) §2.1、§4.2、§5。
- 实现：`src/photo_search/ocr/processor.py` 的 `artifact_id_for`、`build_payload`、`artifact_ref`；`src/photo_search/ocr/engine.py` 的 confidence 输出；`src/photo_search/artifacts/store.py` 的重复 logical key checksum 检查；`src/photo_search/ocr/service.py` 的 force requeue。
- 可复核历史输入：`test-results/p4-02y-l-cuda-compare.json` 与实现报告中的真实 CPU/GPU 对照结果；主审直接以该文件重建了同 ID / 不同 checksum 反例。

## 边界

只裁决 identity 与结果字节语义；不做产品实现、不加载模型、不重跑速度/GPU/私人图库、不改变 `non_model`/structured_model 契约或启动模型切换主审。裁决若批准修改，结束后另立最小实现任务，直接覆盖 CPU→GPU 与 GPU→CPU 的 force/replay 反例、历史 artifact 行为、projection 原子性和回归测试。
