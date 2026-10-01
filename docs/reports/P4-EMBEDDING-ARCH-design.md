# P4-EMBEDDING-ARCH — 任务设计与仓库核对记录

日期：2026-09-30。结论：**规划文档完成；生产实现未开始，未标实现 PASS**。

## 1. 用户决定

新增 P4-Embedding-Provider，执行 ID 为 P4-EMBEDDING-ARCH：实现可插拔语义模型架构，首批 Chinese-CLIP + Qwen3-VL-Embedding-2B 512D。优先快速在现有应用使用；仅简单定向测试，复用已有性能证据，不再做性能专项。

交付：[任务卡](../tasks/P4-EMBEDDING-ARCH.md)、[接入策略](../plans/P4-EMBEDDING-ARCH.md)，以及主架构 §8.5 / V3.1 §9.2 的限定范围同步。

## 2. 当前核对证据

- Git HEAD `2d5fd94`（`docs: record Qwen speed measurement`），设计前 `git status --short` 空；本轮不提交或修改产品代码。
- AGENTS/status/current W card、通用交接、launcher 已读取；因本次涉及跨模块模型/空间/profile，主架构和 V3.1 已完整对齐，实施卡列出后续需读的具体章节。
- ADR-0003 已冻结最小 adapter、ProcessorSpec、space 和 SearchProfile；0007 不可变 profile 和活动指针已有。ADR-0013 已接受，但生产 UpgradeRun 模块未发现，不能列为复用实现。
- worker 固定 ChineseClipOnnxAdapter；query_encoder 的 `_config_hash`、`pinned_embedding_spec`、`_runtime_facts` 和默认模型路径均有旧模型/ONNX 假设。semantic_spec 复用同一旧函数；refresh 两种建库路径最终调用立即激活的 ensure_search_profile。这些是本卡实际贯通范围。
- search 的绑定已验证 space/dimension；原子 activate_profile 可复用，READY 不证明所有当前资产都有覆盖，新增停机选择用例必须额外检查权威覆盖。
- pyproject 的 transformers `>=4.46,<4.47` 与已测 Qwen 4.57.0 不兼容；新 adapter 不能只存在于临时 venv。
- `search/service.py` 全局强匹配门限 1.3、`web/search.js` 对所有 engaged vector 显示计数，必须增加模型空间对应的可用性策略。
- P4-02Y-W/L 保持独立审查待做；没有将任何历史任务追认 PASS。

## 3. 速度与模型证据边界

已核对 [P4-QWEN-SPEED](P4-QWEN-SPEED.md)、原始 `test-results/p4-qwen-speed.json`、诊断工具和本地参考脚本。
Qwen 短文本 query P50 72.86 ms、FP16 CUDA 已有证据；512D 约 3.81 s 是估算，不是 Qwen 真图片索引/HTTP 实测。新接入只补小图库图文闭环和显存/OOM证据，不重测 30 万向量曲线。

在线核对：[官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)、[官方参考仓库](https://github.com/QwenLM/Qwen3-VL-Embedding)。支持 MRL 64–2048D；本地参考采用 last-token pooling、默认输入指令、max_length 8192，以及自动 CUDA/CPU 选择。本轮拟固定 512D 与更小图像像素预算；wrapper 必须覆盖自动 CPU 和远程输入行为。

本轮未运行模型、未复制/下载权重、未安装依赖、未读取私人图库。没有声明 Qwen 图像路径、最低配置或包运行已通过。

## 4. 取舍与范围

采用现有持久协议 + 轻量 registry；新旧模型各自独立空间，普通搜索固定一个完整活动 profile。保持历史 CLIP ID，不做破坏性迁移。
提供受控停机选择用例，小图库验证后才能个人启用；不实现后台双版追赶、完整 UpgradeRun/UI、在线无停顿切换或发行包。
用户要求的定向验证例外仅限本卡，已同步策略。保留空间/覆盖/设备/失败恢复最小注入与独立 DS-H → Sol 审查；免除全量 pytest、重复性能和大质量矩阵，不免除本卡列出的真实 smoke。

必审未决项：独立停机维护选择不创建 UpgradeRun，不伪造 `RUN_SUCCEEDED`；ADR-0013 的升级 API 六门禁未改变。DS-H/Sol 裁定它是否可合法复用 ADR-0003 入口；如必须纳入 run，该边界保持 BLOCKED，另立最小闭环。权威覆盖/artifact 可读性/runtime readiness 与指针更新必须同事务，不能用停机或维护租约代替原子校验。

## 5. 本轮文档验证与下一步

验收等级 A（说明/范围/顺序文档）：本地链接、条款编号、任务/状态一致性、Git diff-check；具体结果见本节末。
不运行 pytest，本轮未改生产代码、机器可读契约、依赖或可执行工具。

实际结果：项目 `.venv/Scripts/python.exe` 文档核对退出码 0；新任务/策略/报告与状态的本地链接存在、代码围栏闭合、无行尾空白，主架构 §8.5 和策略 §9.2 可定位，状态 23 行。`git diff --check` 退出码 0。最终范围为 3 份新增规划文档 + 主架构/执行策略/开发状态更新；无产品代码/依赖变更。

下一步：按任务卡由 DS-H / DeepSeek V4.1 Flash / 高推理实现；模型切换由用户手动完成。设计不标正式协议冻结，不自动开启实现或其他会话。
