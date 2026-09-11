# 2026-09-11 — GPU 架构与策略对齐

状态：**PASS**（仅文档任务，非 P0-06 GPU 验收）。

## 用户请求与交付

用户明确确定 GPU 为硬指标，最低为 1660 级别机器，要求同步架构与策略。当前 Codex 会话完成文档修改，不声明任何模型切换。

- 主架构需求修订 3：最低 GTX 1660 6 GB、CUDA 默认图文/人脸路径、CPU 显式降级、6 GB 调度预算、依赖/打包、性能目标与 P0/P4 门槛。
- V3.1 策略修订 2：同步产品硬约束、P0 默认设备/故障/打包验收、P4 最低配置真机发行门禁。
- ADR-0001：保存用户决策、事实、成本与验证边界。
- development-status 与 P0-06 卡：同步既有独立 GPU probe、当前机与最低目标的区别、验证要求和后续交接。
- 没有代码、数据结构、模型 manifest、pyproject.toml 或 uv.lock 变更；未扫描真实图库、下载模型或运行 P0-06。

## 验证命令与结果

- 只读：nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader，退出 0；RTX 4060 Laptop GPU, 8188 MiB, 616.56（本会话上一轮复核）。
- 只读：git status --short，初始工作区干净；读取 AGENTS.md、状态、P0-06、当前架构/策略，rg 定位 GPU/CPU/CUDA 条款。首次 rg docs/decisions 提示目录尚不存在，随后创建本次所需 ADR 目录。
- NVIDIA 官方对照确认 GTX 1660 6 GB/Turing/无 Tensor Core；ONNX Runtime 官方说明确认运行库/DLL、TF32、provider 与显存限制边界，来源见 ADR。
- 文档检查：通过 PowerShell 将检查脚本传给 .venv/Scripts/python.exe -，退出 0；6 份 UTF-8 Markdown、5 个本地链接、围栏配对、末尾换行/空白均通过；旧 GPU 可选/CPU 基础路径条款已移除，P0-06 门槛保留，P0-07 未生成。检查覆盖新增 ADR/报告（git diff 默认不包含未跟踪文件）。
- uv run ruff check .：退出 0，All checks passed!。
- git diff --check：退出 0，无输出；git -c core.quotepath=false status --short 确认仅四份既有 Markdown 与两份新增 Markdown 改动，未改代码/依赖。
- 人工审查 git diff：主架构硬件/默认路径/调度/精度/打包/性能/P0/P4 条款与策略、任务卡一致；修正 A0 证据边界为 provider 可见，不将独立 probe 视为真实模型 CUDA 验收。

按策略 A 级文档变更检查；无相关业务代码变更，pytest/模型 smoke/构建不适用于本次文档验收，不借本次检查标记运行验证通过。所需 GPU source/packaged smoke 已列入 P0-06，最低配置实机验收列入 P4。

本机默认沙箱存在已观察到的 setup refresh/helper_unknown_error 启动错误，使用经审核的升级权限命令读取/编辑仓库文档；未改动仓库外文件。

## 未验证项与交接

- 当前正式模型/worker CUDA 执行、数值一致性、重复延迟/显存、GPU 包：P0-06 待执行。
- 授权 2000 图/100～200 查询质量：P0-06 待执行；无授权来源则 B BLOCKED，不得扫描图库补齐。
- GTX 1660 6 GB 实机兼容/性能/显存/包：尚未测试，最迟 P4-07/P4-08 完成，缺设备则该项 BLOCKED，不可宣称最低配置已支持。
- 下一唯一任务仍为 docs/tasks/P0-06.md，建议 DS-H / DeepSeek V4.1 Flash / 高推理，用户手动切换；环境问题按策略建议 C1 继续同任务。没有生成或执行 P0-07。
