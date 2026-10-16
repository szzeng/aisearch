# ADR-0018 — OCR 产物身份与 GPU 优先、CPU 兜底

- 日期：2026-10-09。
- 状态：**ACCEPTED FOR IMPLEMENTATION / 产品实现未完成**。
- 依据：[聚焦裁决报告](../reports/P4-02Y-L-artifact-identity-sol.md)、ADR-0003 §2.1/§3/§4.2/§5。
- 用户在本次裁决中明确选择“采用 GPU 优先、CPU 自动兜底”；这是 OCR 专属的协调层授权，不授权其他模型自动降级。

## 1. 身份与字节

CPU/GPU tolerance 只能证明识别质量接近，不能代替不可变 structured artifact 的字节相等。
保留原始 confidence、原文、几何和 `confidence <= 0.6` 的低置信度语义，不量化、不丢弃字段，
也不允许持久层用 tolerance 接受同一 logical key 下的不同 checksum。

发布以下两个数值执行配置；旧 `ocr-text@1` 保留历史身份，不原地重定义：

| 配置 | processor_id | spec_revision | policy / supported_devices |
|---|---|---|---|
| CPU FP32 | `ocr-text-cpu-fp32@2` | `ocr-text-ppocrv4-mobile-cpu-fp32-v2` | `CPU_PREFERRED` / `["cpu"]` |
| CUDA FP32 | `ocr-text-cuda-fp32@2` | `ocr-text-ppocrv4-mobile-cuda-fp32-v2` | `GPU_REQUIRED` / `["gpu"]` |

两者保持 `structured_model`、`p2-contracts.v1`、现有模型身份与 pinned weights；不声明向量空间。
payload 字段形状、`output_type=ocr_document.v1`、`artifact_revision=ocr-document.v1` 不变。
`artifact_id = ocr-document:<实际 processor_id>:<input_revision>`；payload、ref、job、result、
spec 与 current projection 必须绑定同一处理配置。只改 artifact ID、追加设备后缀或 checksum 不足以绕过
`UNIQUE(processor_id, input_revision, artifact_type, artifact_revision)`，也不得改这个约束。

新配置 hash 为版本化有效配置的 canonical JSON SHA-256，覆盖 pinned YAML SHA、识别/预处理/输出规则、
静态 provider 配置、FP32 与锁定 runtime package。其配置版本取 `ocr-execution-config.v2`；具体值和 golden
由实现按实际有效配置生成、核对。旧 CONFIG_HASH/历史 payload 不重算。实际 provider、耗时、兜底原因仍在
运行记录，不写入不可变 OCR payload。更换数值执行配置时重新判断身份，不用动态环境值静默复用这些 ID。

## 2. 用户可见结果与兜底

每张图只有一个当前 OCR 结果，继续由 `asset_ocr_text` 及对应 regions 表表达。历史不可变 artifact 可并存；
它们不是两份当前结果。新任务失败时保留原当前结果，只有成功且提交资格有效的新结果才能原子替换它。

- 默认 `auto`：协调层先排 CUDA 配置任务；合格 GPU 成功后结束，不再排 CPU。
- GPU 缺能力/资格、初始化失败，或能明确归因的 GPU 执行故障时，GPU 任务如实失败/阻塞且不发布成功产物；
  协调层再幂等排 CPU 配置任务。CPU 仍须通过自身能力、权重、provider 与提交资格检查。
- 显式 `gpu` 继续为严格 GPU 请求，失败如实返回；显式 `cpu` 为诊断/指定 CPU。自动兜底只属于默认 `auto`。
- GPU worker 不自行改设备，不把 CPU 执行报成 GPU 成功。GPU session provider 不符必须先拒绝 GPU 执行；
  仅确认发生在 session 初始化边界的设备故障才允许协调层兜底，提交信封的 provider/device 不符不允许兜底。
- lease/revision/processor/产物校验冲突、输入或权重损坏、来源不可用、用户取消、未知异常不触发自动兜底。
  成功空文档、低置信度或识别质量不佳也不触发兜底。不能仅判断“非 SUCCEEDED 就换 CPU”。

GPU→CPU 的交接在短事务中保留 GPU 失败状态/脱敏原因并幂等入队 CPU，原有结果保持可用。
若原 GPU 为可自动恢复的 BLOCKED/RETRY_WAIT，应通过既有合法取消转移停止它，保留原故障原因；
不得在 GPU 恢复后又自动执行该旧分支覆盖 CPU 当前结果。重启应从持久 job 状态恢复 CPU 分支，
不能靠进程内变量记住兜底。已有 jobs 字段和两配置五字段键优先用于保存/追溯，不新增全局调度协议。

## 3. force、重放与历史

force 在选定配置内复用原五字段 job；跨设备是不同 processor 的任务，不修改旧行的身份。
同资产/输入的 OCR 分支须串行协调：有 live lease 不换配置；尚未执行的竞争分支通过既有合法取消处理，
不能让旧 GPU/CPU 任务晚到覆盖新的选择。提交仍验证当前 lease、输入、处理配置与实际设备。

相同配置、相同完整 payload 字节可复用 artifact；不同配置可发布各自产物。同配置输出不同字节仍返回
`ARTIFACT_CONFLICT`，不能启动 CPU 兜底掩盖它。force 的新合法执行沿用现有 text_revision 递增语义；
重复终态 JobResult 不因此获得第二次提交资格。

旧 `ocr-text@1` artifact/current projection 继续按旧规则读取，不重命名、不修改 checksum、不猜测原设备。
新写入不再使用该共享身份；历史未完成 job 不自动改身份或全库重算，显式目标重识别走新配置，
旧 live lease 保持保护。新版本读写端同步支持两配置及 legacy reader；旧二进制兼容性不在本次承诺内。

本 ADR 不证明同一设备/不同硬件每次推理字节确定。location_id 也仍在完整 payload 中；
本轮反例固定位置。任何未版本化的字节差异继续安全拒绝，不扩大为位置归一化或任意结果覆盖协议。

## 4. 落地与验收

唯一下一步：[P4-02Y-L-IDENTITY-A](../tasks/P4-02Y-L-IDENTITY-A.md)。必须覆盖双向 force/replay、
GPU 成功不排 CPU、故障兜底/重启/失败保留旧结果、禁止兜底反例、legacy reader 与 projection 原子性。
本裁决只更新文档；产品修复、独立工程复核与原任务待验项仍未完成。

## 5. 后续处理配置修订（2026-10-16）

`P4-OCR-PREFIX` 在既有版本/退役机制上发布 CPU/GPU `@3` 配置，修复切片去重丢前缀和方向分类误翻长行；不改变 payload 字段、artifact revision、current projection、提交或设备兜底协议。

- 新工作统一使用 `ocr-text-cpu-fp32@3` / `ocr-text-cuda-fp32@3`，spec revision 对应 `...-v3`，有效配置版本为 `ocr-execution-config.v3`。config hash 同时覆盖文本/几何去重和有界方向复核规则，仍使用原 pinned 权重。
- 保留 `@1` 和 CPU/GPU `@2` 的历史读取、原 config hash 和不可变字节；它们明确退役，不运行新算法。旧未完成任务复用已有有界 CAS 收尾，live/异常租约继续保护。已有 current OCR 不因升级自动重算或被删除；用户选择的目标重识别成功后才原子替换 current，失败保持旧结果。
- 去重只在文字、物理行、覆盖和截断端点都有证据时合并；不确定或冲突读数保留，原文/置信度不改写。方向分类继续启用；只有实际翻转 180° 后得到空/低置信结果的裁剪，追加至多一次原方向识别，且结果置信度超过 0.6、提高至少 0.1 才替换。
- 这次是既有处理器版本机制的后续配置，不新增 schema、全局任务协议、依赖或自动全库迁移。实施与验收范围以 [最终变更说明](../reports/P4-UPSTREAM-MERGE-20261016.md) 为准，历史验收结论不追认。
