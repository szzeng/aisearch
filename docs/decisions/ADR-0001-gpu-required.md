# ADR-0001 — CUDA 为必选能力，最低目标 GTX 1660 6 GB

- 日期：2026-09-11。
- 决策状态：ACCEPTED（用户明确要求）；运行实现/硬件验证状态：待 P0-06 / P4 验收。
- 用户依据：“就以gpu为硬指标吧, 最差也是1660的机器. 帮我更新下构架文档还有策略文件.”
- 对应：主架构需求修订 3、V3.1 策略修订 2、P0-06。

## 背景与证据

主架构原有“GPU 可选/CPU 基础路径”，最新策略已有 P0 GPU 门禁，产品 worker 仍只验证 CPU，三者需统一。50 万张图库要求把持续建库吞吐作为选型条件，CPU 能运行不能替代 GPU 交付。

本会话只读 nvidia-smi 结果为 RTX 4060 Laptop GPU、8188 MiB、驱动 616.56。用户的独立 GPU probe 见 P0-06 A0，只证明 provider 被列出，尚未替代正式项目真模型推理证据。GTX 1660 不是本次观测设备。

## 决定

1. 最低支持目标 GTX 1660 6 GB 级别 NVIDIA GPU，正常 AI 模式要求兼容驱动和已验证 CUDA runtime；基础图文 image/text 与人脸 detection/recognition 默认 CUDA。
2. CPU 保留数值对照、诊断和显式慢速降级。默认 CUDA 失败明确报错并暂停相应 AI 任务，保留非 AI 浏览/管理；禁止静默整图/全库 CPU 回退。已记录的 CPU 算子分配与整图降级分别报告。
3. 单 AI worker、有界批次、交互优先、按阶段复用模型，预算包含推理临时占用与桌面余量。固定 batch=1、FP32 为现有验证起点；不依赖 Tensor Core、BF16、TF32、FlashAttention 或 TensorRT；任何精度/批量优化另验收。
4. GPU 正式包和匹配 DLL 可复现锁定，检查传递依赖冲突；CPU 对照优先使用 GPU 包的 CPUExecutionProvider，不在同环境混装 onnxruntime 与 onnxruntime-gpu。未来实施依赖变更须更新 uv.lock，本次不安装或变更依赖。
5. OCR 设备按收益选择；存储、索引、解码等保持原职责。更大模型只在满足最低硬件时才可成为基础默认，否则作为另有要求的可选增强。
6. P0-06 在当前 RTX 4060 上先过 GPU/source/package/一致性/资源/显式降级，再完成授权质量评测；二者通过才可进入 P0-07/P1。GTX 1660 真机的运行库、模型、性能、资源、争用、离线发行包最迟 P4-07/P4-08 验收，不能以限显存实验代替。缺最低配置实机证据时该项 BLOCKED，正式发行不得完整 PASS；P0 后开发可按既有门槛继续。

## 后果与范围

硬件要求提高为 GPU 必选；CPU-only 机器仅提供有限浏览/管理和已验证的显式降级，不承诺完整 AI 性能。模型更换须评估 6 GB 预算，现有 Chinese-CLIP/buffalo_l 身份和向量契约保持不变。GPU 提速倍数、GTX 1660 兼容和性能目前均未测得，不写成已通过。

本次仅同步文档，不实施 P0-06、不扫描真实图库、不追溯撤销 P0-05 的 CPU 阶段 PASS。下一执行任务仍为 P0-06；文档交接无需启动并行任务或自动切换模型。

## 官方资料

- [NVIDIA GeForce 16 系列规格](https://www.nvidia.com/en-us/geforce/graphics-cards/compare/)：GTX 1660 为 Turing、6 GB、无 Tensor Core（2026-09-11 核对）。
- [ONNX Runtime CUDA provider](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)：运行库/DLL 匹配、preload、TF32 配置和 arena 内存限制（2026-09-11 核对）。

资料说明组件特性，不代替锁定版本和目标设备实测。性能目标见主架构第 12.2 节，开发与发行门槛见策略第 5、9.1 节。
