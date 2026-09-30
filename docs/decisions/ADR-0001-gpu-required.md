# ADR-0001 — GPU 图文推理与平台边界

更新：2026-10-01。用户原要求 Windows/NVIDIA，现明确要求当前 Apple M4 Mac 也可运行。

1. Windows 图文默认 CUDA，Apple Silicon macOS 默认 MPS；两者在任务协议中都记录为 `gpu`，实际 provider 分别为 `torch-cuda`、`torch-mps`。不增加数据库设备枚举，不把 Apple GPU 冒充 CUDA。
2. FG-CLIP 2 Base 默认 GPU FP16，显式 CPU FP32。GPU_REQUIRED 禁止 CPU，CPU_ALLOWED 保留显式 CPU 路径，CPU_PREFERRED 默认 CPU。显式指定的加速器不可用时失败，不静默切换设备。
3. macOS 安装 `onnxruntime`；Windows 安装 `onnxruntime-gpu` 及 CUDA 依赖。Mac OCR/人脸使用 CPU，图文通过 MPS 加速。单环境不混装两种 ONNX Runtime 发行包。
4. 模型权重和 tokenizer 不因操作系统变化；短查询使用 short/64 分支，长描述使用 long/196 分支，不截断超长查询。预处理与模型共同决定空间身份，变更后重新建库。
5. 模型、图库索引、测试集和应用状态在独立数据目录。macOS 默认 `~/Library/Application Support/PhotoSearchData`，Windows 默认 `%LOCALAPPDATA%/PhotoSearchData`。
6. GTX 1660 6GB 仍为 Windows 最低支持目标，需独立实机验收。Mac 结果只代表已测机器；千图 benchmark 不代表十万/三十万图性能，也不替代 Windows 打包验收。

当前实现和实测证据见 [macOS 任务记录](../tasks/macos-benchmark.md)。
