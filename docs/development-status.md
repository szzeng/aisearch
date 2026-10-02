# Development status

更新：2026-10-02。当前规则：主架构**需求修订 8**、执行策略**修订 10**。本文件只保留最新交接，目标 ≤100 行；旧记录见[原样归档](development-status-archive-2026-09-27.md)，默认不读全文。

## 当前任务 P4-EMBEDDING-ARCH — 可插拔语义 provider + Qwen3-VL 512D（**实现完成；独立审查待做**）

实现报告：[P4-EMBEDDING-ARCH 实现](reports/P4-EMBEDDING-ARCH-implementation.md)；独立审查卡：[P4-EMBEDDING-ARCH-review](tasks/P4-EMBEDDING-ARCH-review.md)；[执行卡](tasks/P4-EMBEDDING-ARCH.md)、[接入策略](plans/P4-EMBEDDING-ARCH.md)、[设计核对](reports/P4-EMBEDDING-ARCH-design.md)。状态 **IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**；下一步 **DS-H 全受影响面工程审查 → Sol high 聚焦终审**，模型由用户手动选择。本轮完成 registry/factory/Qwen adapter、`embedding list|prepare-local|build-candidate|select-profile`、候选不激活、受控停机选择的事务内覆盖/outbox/artifact/Lance/资格门禁、页面 `strong_match_available`，并把主环境锁定到 torch `2.8.0+cu128`/torchvision `0.23.0+cu128`/transformers `4.57.0`/qwen-vl-utils `0.0.14`。已按用户要求把 Qwen 装入真实数据根 `D:\SZ\project\PhotoSearchData` 并完成全库切换（3182 张图像、候选建库 17.4 分钟、受控选择覆盖 3182/3182、真实 HTTP 三查询 200）。证据：定向 JUnit **203 tests / 0 failed / 0 errors / 6 skipped**、真实双 provider CUDA smoke **24/24**（`test-results/p4-embedding-arch-smoke.json`，12 合成图、8 中文 query、2 上传自查询、Qwen 峰值显存 4.29 GB）、现有页面文字+上传各一次真实浏览器确认；Ruff/`uv lock --check`/`git diff --check` 通过。未验证项见报告 §5（OOM/GTX 1660 未注入、Lance 行数核对 ≤2 万行、CLIP 侧未做候选选择演练、未打包、未跑性能专项）。onnxruntime 的 PyTorch CUDA 主版本提示已消除：ONNX worker 进程改为 `USE_TORCH=0`（torch 不再进入该进程），见报告 §5.6。真实数据根的日常增量/切回只做机制验证；切回 CLIP 需先补建那张 HEIC。额外全量套件发现 4 个**既有**失败（`p2-contracts.v1.json` 冻结哈希与 `c1d0a34` 不一致，非本卡改动），建议路由 P4-02Y-L 审查，见实现报告 §7。

## P4-PLACE-AUTO-A — 个人GPS地点相册（**PASS — 本机个人地点相册与小样本验证**）

实现报告：[P4-PLACE-AUTO-A](reports/P4-PLACE-AUTO-A-implementation.md)；[唯一功能卡](tasks/P4-PLACE-AUTO-A.md)、[简化方案](plans/P4-PLACE-AUTO.md)、[核对记录](reports/P4-PLACE-AUTO-design.md)。新增地点相册 API/UI、别名地名入口、省份可选包 INDEX 和有界 GPS/解析补齐；人工 assignment/取消及旧搜索语义保留。合成集成覆盖24张照片及边界/分页/控制；JUnit **40/0/0/0**，Ruff 与 diff-check 通过；浏览器验证 Hangzhou入口→杭州网格/预览→改一张到上海后重载保持上海1/杭州23；再将另一张留空取消归类，刷新仍为杭州23且首张为照片 #2，未被GPS归回。仅临时合成图库；未访问真实个人库，不声称真实省份包已安装、行政区准确或大规模性能。P2-04A维持已删除；P4-EMBEDDING-ARCH独立审查状态不变。

## P4-02Y-L — OCR CUDA 设备契约（**实现验收通过；独立 DS-H 审查待做**）

实现报告：[P4-02Y-L OCR CUDA](reports/P4-02Y-L-ocr-cuda.md)；审查卡：[P4-02Y-L-review](tasks/P4-02Y-L-review.md)。Sol 冻结通过；RTX 4060 Laptop 实测 auto 3/3 job 走 CUDA，显式 CPU 3/3 成功；CPU/GPU 输出等价，round-total 中位数 5227.72/843.33 ms（6.199×），synthetic refresh/build 3/3 CUDA。P2/OCR JUnit **119 passed / 0 failed / 0 errors / 19 skipped**；Ruff、lock、diff-check 通过。P4-02Y-L 总任务仍**待独立 DS-H 全受影响面审查**，未标 PASS。

## 待审任务 P4-02Y-W — 5000 搜索结果 + 高度匹配（实现与验收完成，独立审查待做）

实现报告：[P4-02Y-W](reports/P4-02Y-W-5000-results-strong-match.md)；独立审查卡：[P4-02Y-W-review](tasks/P4-02Y-W-review.md)。当前暂定阈值 `distance <= 1.3`（约 `cosine >= 0.35`）；边界测试/文档已同步。此前 B 级 JUnit **1878/0/0/48**、30 万 smoke 及静态检查早于此门限修正，按用户要求未重跑测试，故新门限未验证；总任务仍待独立 DS-H 复核。

## 旁路纯测量 P4-QWEN-SPEED — 完成

报告：[P4-QWEN-SPEED](reports/P4-QWEN-SPEED.md)；原始样本：`test-results/p4-qwen-speed.json`。Qwen query P50 **72.86 ms**；2048D 30 万向量 P50 **23957.95 ms**，20 blocks / 300000 scanned / fallback 0 / truncated false。8 GB RTX 4060 Laptop FP16 CUDA 路径成功；估算搜索时间：512D **3.81 s**、2048D **24.34 s**（均非 HTTP 端到端实测）。

## 上一任务 P4-02Y-V — `top_k` 性能测试（DONE）

纯测量完成（2026-09-30；[报告](reports/P4-02Y-V-top-k.md)、[任务卡](tasks/P4-02Y-V.md)、原始数据 `test-results/p4-02y-v-top-k.json`）。30 万固定夹具两轮交错共 60 样本；向量前缀检查 5/5 通过。Service P50：K200 3894 ms、K500 3946 ms、K1000 4132 ms、K2000 4162 ms、K3000 4340 ms、K5000 3733 ms；K5000 非单调偏快，结论未见可信拐点。纯性能数据不单独决定产品上限。
