# P4-QWEN-SPEED — Qwen 30 万图库速度评估

日期：2026-09-30。性质：纯测量；未改 `src/**`、`pyproject.toml`、`uv.lock` 或 PhotoSearchData。原始样本见 [`test-results/p4-qwen-speed.json`](../../test-results/p4-qwen-speed.json)；诊断工具为 [`qwen_speed_probe.py`](../../tools/diagnostics/qwen_speed_probe.py)。

## 结论

| 模型/方案 | Query P50 | 30 万 Vector P50 | 估算搜索时间 | 说明 |
|---|---:|---:|---:|---|
| 当前 Chinese-CLIP 512D | 12.1 ms（P4-02N 单次，不重测） | 3420.894 ms | 当前 Service P50 3733.173 ms | 已有基线 |
| Qwen 512D | 72.86 ms | 3420.894 ms（复用基线） | **3806.03 ms，约 3.81 秒** | query encode 是新增成本；预计比现有服务基线多约 73 ms |
| Qwen 2048D | 72.86 ms | **23957.95 ms** | **24343.09 ms，约 24.34 秒** | 同一次 Qwen forward 产生 2048D，再 MRL 截成 512D；未重复推理 |

估算式：512D 使用已有 Service P50 + Qwen Query P50；2048D 使用已有 Service P50 − 512D Vector P50 + 新测 2048D Vector P50 + Qwen Query P50。两者均是 **estimated，不是真实生产 HTTP 端到端实测**。

2048D 向量检索约为 512D 的 **7.003×**，增加约 **20.54 秒**；Query P50 约 **73 ms**。因此主要额外耗时来自**向量维度**，不是 Query encoding。512D 预计约 3.81 秒；2048D 预计约 24.34 秒（约为当前 Service P50 的 6.52×）。

## 测量方法与证据

- 30 万检索固定 `top_k=5000`、`block=15000`，走生产 `LanceVectorIndex / vector_channel` 精确检索路径。2048D 使用分块生成的 synthetic L2-normalized float32 向量，批次 5000 行；未保存完整 `300000 × 2048` 矩阵。
- 临时副本包含复制的 fixture `catalog.db`、原有 512D Lance generation 和新增 2048D generation/table。artifact payload 的完整复制在遍历到 53,018/300,000 个哈希分层目录后停止；它们不参与检索读路径。副本里的这部分临时残留不用于测量，原 fixture 未改动。
- 早期独立随机向量初次测量发现每轮有一个 block 的 Top-K 边界并列、触发 fallback，因此该轮被拒绝。随后仅重建临时 synthetic 表，使查询方向的随机 cosine 分布更分散，再执行正式轮次。最终检查为：`20 blocks`、`300000 scanned`、`fallback=0`、`truncated=false`、返回 5000 项。
- 2048D 检索：预热 1 次；正式样本 `23957.95, 23487.07, 23558.29, 23992.03, 23997.72 ms`，P50 **23957.95 ms**；进程峰值 RAM **1462779904 bytes（约 1.46 GB）**；Lance vector table **2463356517 bytes（约 2.46 GB）**。
- Qwen 查询：固定“美女和猫”“海边日落”“两个人一起吃饭”；每个 query 预热 3 次、正式 10 次，共 30 个样本。使用 Qwen 官方 embedder 的默认 query instruction。模型输出 2048D，512D 为同次 forward 的前缀截断并重新 L2 normalize；`mrl_512d_same_forward=true`。
- Qwen 模型加载 **3156.08 ms**（单独计时，不加入正常搜索估算）。当前 Chinese-CLIP 既有 query evidence 是 P4-02N 的 **12.1 ms 单次样本**，非 P50；本卡未重测。

## CUDA 与 8 GB 显存

在 RTX 4060 Laptop GPU（8188 MiB）完成正常 CUDA 路径：Python 3.12.10、PyTorch `2.8.0+cu128`、Transformers `4.57.0`、`qwen-vl-utils 0.0.14`、torchvision `0.23.0+cu128`。模型运行在 `cuda:0`、FP16 非量化；未使用 CPU offload。Query P50 **72.86 ms**，GPU 峰值 allocated **4265906176 bytes（约 4.27 GB）**、reserved **4284481536 bytes（约 4.28 GB）**，未发生 OOM。因此当前 8 GB GPU 能运行本卡的短文本 Query 编码路径。

环境和约 4.26 GB 模型权重保存在 `tmp/qwen-speed-env/`、`tmp/qwen-speed-model/`，没有放入 PhotoSearchData 或 Git 跟踪文件。模型卡列出 Transformers ≥4.57.0、qwen-vl-utils ≥0.0.14、torch 2.8.0，并说明支持 64–2048D MRL；临时环境使用官方 CUDA 12.8 wheel。[Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)；[PyTorch 官方历史版本安装命令](https://pytorch.org/get-started/previous-versions/)。

## 验证

- `uv run --no-sync ruff check tools/diagnostics/qwen_speed_probe.py` — PASS
- `git diff --check` — PASS
- 未运行 pytest（任务卡明确禁止全量 pytest，也没有要求其它测试）。
