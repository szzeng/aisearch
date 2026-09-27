# 中文 CLIP 权重准备（用户授权，2026-09-27）

**性质**：资源步骤。用户 2026-09-27 明确授权「准备真实中文 CLIP 权重，下载的模型放在 D 盘」。本报告不是性能/
实现/发行 PASS；**不改**产品代码、依赖、`uv.lock`、迁移、ADR、既有 manifest 与既有 validation 记录。

## 1. 落盘位置与理由

| 项 | 值 |
|---|---|
| 模型目录 | `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`（2.10 GiB / 13 个文件） |
| HF 下载缓存 | `D:\SZ\project\PhotoSearchData\cache\huggingface`（本次仅落少量元数据） |
| 选择理由 | 保持产品登记布局 `<data_root>\models\chinese_clip\vit-b-16`，整条路径在 `D:\SZ\project` 下且**在仓库之外**（不违反「模型不进源码目录」「应用数据离开安装目录」）；C 盘仅剩 10.4 GB，D 盘 99.2 GB |
| C 盘数据根 | `C:\Users\28288\AppData\Local\PhotoSearchData` 仍只含 `runtime\`，本次**未写入** |

依赖无需变更：本机已锁 `torch 2.14.0+cpu`、`torchvision 0.29.0+cpu`、`transformers 4.46.3`、`onnx 1.22.0`、
`onnxruntime-gpu 1.30.0`（提供 `onnxruntime` 模块）、`pillow 11.3.0`，与既有 manifest 记录的版本一致。

## 2. 命令与实际结果

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
$env:HF_HUB_CACHE='D:\SZ\project\PhotoSearchData\cache\huggingface'
$env:HF_HUB_DISABLE_TELEMETRY='1'
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY -ErrorAction SilentlyContinue   # 本机代理指向死端口 127.0.0.1:9
New-Item -ItemType Directory -Force -Path 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16','D:\SZ\project\PhotoSearchData\cache\huggingface' | Out-Null

# 1) 下载 + ONNX 导出（新增 manifest 想写到模型目录，未覆盖仓库 manifest）
uv run --no-sync python tools/prepare_models/prepare_chinese_clip_v2.py `
  --model-dir 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16' `
  --manifest 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16\manifest-chinese-clip-vit-b-16.json'

# 2) 字节核对：实际文件 vs 仓库 manifest
# 3) 数值等价 + Top-K
uv run --no-sync python tools/prepare_models/verify_chinese_clip_v2.py `
  --model-dir 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16' `
  --manifest 'tools/prepare_models/manifests/chinese-clip-vit-b-16.json' `
  --result 'test-results/p4-02m-model-validation.json'

# 4) 真实 worker（产品入口，离线）
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
uv run --no-sync python -m photo_search demo-search `
  --model-dir 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16' --device cuda `
  --output 'test-results/p4-02m-demo-search-cuda.json'
```

| 步骤 | 结果 |
|---|---|
| 1) 下载 + 导出 | 5 个仓库文件与两个 ONNX 全部生成；脚本**最后一步**报 `status: fail, stage: onnx_export, exception_type: PackageNotFoundError, message: No package metadata was found for onnxruntime`，`completed_steps: [download, onnx_export]` |
| 2) 字节核对 | **7/7 大小与 sha256 全部与仓库 manifest 一致**（含 `image_tower.fp32.onnx` `c8ac42d3907c57b0…`、`text_tower.fp32.onnx` `4699a6ef73554451…`、两个 checkpoint `5997ef32…`/`7b7b583c…`、`vocab.txt 45bbac6b…`） |
| 3) 数值等价 | `status: pass`：image `max_abs_difference=1.9073486328125e-06`、text `1.52587890625e-05`、最低 cosine `0.9999998807907104`、Top-K 一致 `[0,1,2,4,3]`；`config.json` 记录 `projection_dim=512` |
| 4) 真实 worker | exit 0：`device=cuda`、`CUDAExecutionProvider`、`is_degradation=false`、`cpu_fallback_disabled=true`；image `1320/1320`、text `395/395` 节点全 CUDA；`top_k` = red `0.44083234667778015` / green `0.41859498620033264` / blue `0.40591099858283997`（与 P0-06 打包记录一致） |

### 2.1 关于第 1 步的失败：**是环境命名不一致，不是模型问题，也不阻塞**

`prepare_chinese_clip_v2.py` 在组装 manifest 时执行
`importlib.metadata.version("onnxruntime")`（`prepare_chinese_clip_v2.py:135-146`），而本机安装的发行名是
`onnxruntime-gpu`（提供同名 `onnxruntime` 模块，`pyproject.toml` 的 `override-dependencies` 明确只保留这一份）。
因此该行抛 `PackageNotFoundError`，**下载与两个 ONNX 导出在它之前都已完成**。

处置：**不修工具、不写重复 manifest**——第 2 步证明仓库既有 manifest 已逐字节描述这份 D 盘模型，
所以「新增 manifest」本身就是多余的。若将来确实需要重新生成 manifest（例如换 revision 或换 torch 版本），
再单独修这一行并走正常卡流程；本轮不顺手改 `tools/**`。

### 2.2 Windows 权限注意（环境，不是模型）

第 4 步首次在受限令牌下运行时失败于 `multiprocessing` 管道：
`PermissionError: [WinError 5] 拒绝访问`（`multiprocessing/connection.py:575 CreateFile`）。
按本仓库既有正常权限路由（不使用受限令牌、不改 ACL）重跑即通过。

## 3. 未验证 / 边界 / 遗留

- 本报告只证明**权重可加载、可编码、CUDA 可用、离线可跑**；**不是**检索质量、§12.2 达标的证据，
  也不是真实 HTTP/前端证据。
- 产品默认模型目录仍是 `%LOCALAPPDATA%\PhotoSearchData\models\chinese_clip\vit-b-16`（C 盘，当前不存在）。
  D 盘这份目前只能由显式 `--model-dir` 使用（`demo-search`、建库/再执行 CLI）。
  **API 进程如何解析模型目录（数据根放 D，或新增显式模型目录设置）属于 [P4-02N](../tasks/P4-02N.md) 的接线决定**，
  本报告不预设，也不改 `config.py`/`_default_model_dir()`。
- 未做：GTX 1660 最低配置、真实图库建库、真实查询样本、动态批处理、FP16/量化、冷缓存/长时运行。
- 未改：`uv.lock`、`pyproject.toml`、`src/**`、`tests/**`、迁移、ADR、`tools/**`、既有 manifest 与 validation 记录。

## 4. 复现与环境注意

- 本机 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 指向死端口 `http://127.0.0.1:9`（P4-00C-A 已记录同一坑），
  下载前必须临时清空；`NO_PROXY` 保留本机地址即可。
- 证明离线可用：加载时设 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，且
  `ChineseCLIPModel.from_pretrained(..., local_files_only=True)`（verify 脚本内）与 worker 都不触网。
- 若 huggingface.co 不可达，可另设 `HF_ENDPOINT=https://hf-mirror.com` 重试；本轮**未需要**它。
- 产物 JSON（`test-results/p4-02m-model-validation.json`、`test-results/p4-02m-demo-search-cuda.json`）
  按 `.gitignore` 的 `test-results/` 不入库，关键数字已在本报告逐条引用。
