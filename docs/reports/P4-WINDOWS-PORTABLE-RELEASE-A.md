# P4-WINDOWS-PORTABLE-RELEASE-A — 执行报告

**状态：IMPLEMENTED / VALIDATION PENDING**（2026-10-23）。A 的 V4 冻结 Qwen CUDA 验收现已通过；V1 PASS、V2 LOCAL_ISOLATION_PASS、V3 PARTIAL、V4 PASS、V5 PARTIAL、V6 CLEAN_HOST_PENDING。V5 全量回归仍有 21 项范围外失败，V6 无第二台干净 Windows 主机。按用户要求已本地提交，不推送、不发布。

## 构建与产物

- 基线构建：Windows x64，`3d717bb`，Python 3.12.10、PyInstaller 6.22.2、Torch 2.8.0+cu128、Transformers 4.57.0、ONNX Runtime 1.30.0。onedir 15,869 文件 / 11,499,324,195 字节；ZIP 6,258,093,080 字节，SHA-256 `c84c9605ec36767b6d60e5ed0cc9b23f3e91d76bf40e03c9418e6481e3bef84b`。ZIP CRC/目录结构通过，不含权重/数据库/图库。
- 冻结模板发现 CLIP、Qwen 512D、1024D，各自 EXIF v2 身份独立；无权重时报 `WEIGHTS_MISSING`。Torch CUDA DLL 保留在 `torch/lib`，未与 ONNX CUDA DLL 平铺。

## 验证

- **V1 PASS**：冻结资源哈希、迁移/模型清单、native DLL、归档条目和 SHA 已由既有构建证据核对。
- **V2 LOCAL_ISOLATION_PASS**：既有中文＋空格路径 hardlink 克隆运行通过；没有物理独立复制或第二机验收。
- **V3 PARTIAL**：包内 provider/template、缺权重、错误模型文件 hash 拒绝、CUDA 不可用时 GPU-required 拒绝、端口占用与重复启动拒绝均实测通过；无权重浏览器查询仍暴露服务端 `VALIDATION_ERROR` 详情，保留部分状态。
- **V4 PASS**：模型固定 revision `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda`，本轮只读验证 5 个 pinned 文件共 4,266,571,889 bytes，SHA 全通过。冻结 `PhotoSearch.exe demo-search` 使用真实 spawn Worker 在 RTX 4060 Laptop GPU、PyTorchCUDA、FP16、`gpu_required` 下分别运行 512D/1024D；文字和三张合成图片输出维度正确、有限、非零且 L2=1.0，CPU fallback 关闭，峰值显存约 4.26 GB。各自独立隔离数据根扫描 3 张合成 JPEG，冻结 `embedding build-candidate` 分别得到 512D 与 1024D 的 3/3 READY GPU 索引、Lance 3/3；profile 选择再次验证文字/图片向量和绑定完整性。512D 应用 API 文字、图片搜索成功，服务退出并重启后文字搜索仍命中；1024D 文字搜索与重启搜索成功，命中 3 张。原始简报：`test-results/P4-WINDOWS-PORTABLE-RELEASE-A-continued/frozen-qwen-512.json`、`frozen-qwen-1024.json`；小图库数据在 `.tmp/P4-WINDOWS-PORTABLE-RELEASE-A/v4-512` 和 `v4-1024`。无读取照片/catalog 或修改权重。
- **V5 PARTIAL**：原冻结 `packaged_api_smoke.py` 证据为 status=ok，覆盖 session/CSRF/loopback、静态资源、synthetic catalog、HEIC/CR2 与安全拒绝。追加完整 unit/integration：3,544 tests / 21 failed / 0 errors / 52 skipped，JUnit `test-results/P4-WINDOWS-PORTABLE-RELEASE-A-continued/full-regression.xml`；失败均为既存 UI 文案/交互、companion hash 等范围外检查。受影响 Provider/Worker/Manifest 集合 68/68 通过，JUnit `affected-regression.xml`；相关文件 Ruff、`uv lock --check`、`git diff --check` 通过。尝试重跑 packaged smoke 时 D: 可用空间为 0，复制完整 onedir 因 WinError 112 未完成；沿用此前同一构建 hash 的成功 smoke 证据。
- **V6 CLEAN_HOST_PENDING**：没有另一台无 Python/Git/uv/源码/.venv 的 Windows x64 主机。

## 验收矩阵与界限

| 项 | 状态 | 证据边界 |
|---|---|---|
| V1 冻结构建/资源 | PASS | 构建于 `3d717bb`；不代表当前 HEAD 重新构建。 |
| V2 中文路径/启动 | LOCAL_ISOLATION_PASS | hardlink 隔离克隆，不是物理复制/跨机。 |
| V3 Provider/错误态 | PARTIAL | 冻结错误路径覆盖通过；无权重 UI 查询错误详情仍待修复/复验。 |
| V4 Qwen CUDA/Worker | PASS | 512D/1024D CUDA、索引、文字/图片查询、重启；RTX 4060 Laptop。 |
| V5 既有能力/安全/回归 | PARTIAL | 原 smoke 通过；21 项范围外回归失败，重跑 smoke 受空间阻塞。 |
| V6 干净 Windows | CLEAN_HOST_PENDING | 无独立设备。 |

最终不标 LOCAL_PORTABLE_ACCEPTED 或 CROSS_PC_ACCEPTED，因为 V3/V5 不完整且 V6 未做。未发布、未推送。

## 本轮追加 packaged smoke（2026-10-23）

- 复用既有 `dist/PhotoSearch`，在磁盘空闲 13.5 GB 时为 smoke 复制包到 `.tmp/P4-WINDOWS-NEW-PC-READY-packaged-smoke`；程序/API 启动、一次性会话、CSRF、安全拒绝、合成来源刷新、5 项资产、文字搜索、静态资源与媒体验证均完成。命令退出 **1**，唯一 problem 为 `packaged photo_search data resources`。JSON：`test-results/P4-WINDOWS-NEW-PC-READY/packaged-api-smoke.json`。
- 资源清单差异具体为工作树已有 `web/memories.css`、`web/memories.js`，而冻结包构建于 `3d717bb`、早于当前并行未提交 Photo Memories 页面资源；清单 `matches_source=false`。这是包与当前工作树资源版本不一致，不能算 smoke 全通过，也不能归因给 A 的冻结基线自身。该次 smoke 的无 Query Encoder API 负例返回 HTTP 409、`RUNTIME_CAPABILITY_UNAVAILABLE` / `QUERY_ENCODER_UNAVAILABLE`，结构化原因正常；V3 浏览器错误呈现仍待单独实看。
- smoke 保留了完整复制包和合成夹具；未删除。D: 现余约 1.99 GB，不能在此继续复制/构建。
