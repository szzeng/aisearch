# P4-QWEN-SPEED — Qwen 30万搜索速度最小评估

## 目标

只回答：

> Qwen3-VL-Embedding-2B 用于当前 30 万图库时，搜索速度大约会变成多少？

纯测速，不改生产功能。

执行建议：**GPT-6 Luna / medium**。若 2048D generation 无法正确复用既有 30 万 fixture，或 Qwen CUDA 独立环境出现需要跨模块定位的依赖/显存问题，再升 **Luna / high**；本卡不需要 Sol / DS-H 独立审核。

## 已有基线：直接引用，不复测

Chinese-CLIP 与 512D 的 30 万检索已有正式数据，本卡不得重复测量。

P4-02Y-V：

```text
300000 vectors
512D
top_k=5000
block=15000
20 blocks
300000 scanned
fallback=0
truncated=false

Vector P50  = 3420.894 ms
Service P50 = 3733.173 ms
```

Qwen 若输出 512D，对 LanceDB 仍是 30 万条 512D float32 向量，因此 **Qwen 512D 的向量检索成本直接采用该 512D 基线**。

不重建 512D fixture，不复测 Chinese-CLIP。

## A. 唯一新增的30万向量测试：2048D

复用既有 30 万 fixture 的临时副本：

```text
tmp/p402f-阶段0/scale-300000-wave/data
```

只新增：

```text
2048D generation
300000 confirmed index entries
2048D Lance vector table
top_k=5000
block=15000
exact search
```

使用 synthetic L2-normalized float32 vectors。

必须：

- 分块生成；
- 分块写 Lance；
- 不在内存保存完整 `300000 × 2048` 矩阵；
- 不修改原历史 fixture；
- 走当前生产 `LanceVectorIndex / vector_channel` 路径。

测速：

```text
warmup 1
正式 5 次
```

记录：

- Vector P50
- RAM
- vector table 磁盘大小
- blocks
- scanned
- fallback
- truncated

必须确认：

```text
20 blocks
300000 scanned
fallback=0
truncated=false
```

需要时新增：

```text
tools/diagnostics/qwen_speed_probe.py
```

不要修改历史 `p4_02f_stage0.py`。

## B. 建立一次性 Qwen 测速环境

当前项目没有 Qwen3-VL-Embedding-2B 的运行依赖和模型权重。

本卡负责在 `tmp/` 下建立**独立、可删除的临时环境**，按 Qwen 官方要求安装测试所需依赖并下载：

```text
Qwen/Qwen3-VL-Embedding-2B
```

例如：

```text
tmp/qwen-speed-env/
tmp/qwen-speed-model/
```

具体目录可由执行者按官方工具实际布局决定。

禁止修改：

```text
pyproject.toml
uv.lock
当前 PhotoSearch .venv
```

禁止把模型权重、临时环境或缓存提交 Git。

## C. 只测 Qwen Query 编码

不再测试 Chinese-CLIP。

固定查询：

```text
美女和猫
海边日落
两个人一起吃饭
```

Qwen：

```text
warmup 3
正式 10 次
```

记录：

- Query encode P50
- 模型加载时间（单独记录，不算正常查询）
- GPU 峰值显存
- 实际 CUDA/device/dtype
- 输出维度

只比较：

```text
512D
2048D
```

若 512D 只是同一次 forward 后的 MRL 截断，不重复执行模型推理。

### GPU 规则

使用当前 RTX 4060 Laptop 的真实 CUDA 正常运行路径。

若发生：

```text
CUDA OOM
模型无法正常加载
依赖无法建立
```

直接记录真实结果并停止对应测试。

本卡禁止为了跑通改成：

- CPU
- CPU offload
- 量化
- 其它模型

## D. 输出

```text
docs/reports/P4-QWEN-SPEED.md
test-results/p4-qwen-speed.json
```

最终只需要：

| 模型/方案 | Query | 30万 Vector | 说明 |
|---|---:|---:|---|
| 当前 Chinese-CLIP 512D | 引用已有证据 | 3420.894 ms | 当前基线 |
| Qwen 512D | 新实测 | 3420.894 ms | 直接复用512D检索基线 |
| Qwen 2048D | 新实测 | 新实测 | 完整维度 |

同时引用当前已有：

```text
30万 Service P50 = 3733.173 ms
```

基于“已有基线 + Qwen Query 实测 + 2048D Vector 实测”给出简洁估算，并明确标记为 **estimated，不是真实生产 HTTP 端到端实测**。

最终回答：

```text
Qwen 512D：预计搜索速度约为多少，主要新增耗时是什么
Qwen 2048D：预计搜索速度约为多少，主要新增耗时是什么
当前 8GB GPU 是否能正常运行 Qwen3-VL-Embedding-2B
```

## 不做

- 不测准确率
- 不复测 Chinese-CLIP
- 不复测 512D 30 万检索
- 不编码真实 30 万照片
- 不正式接入 Qwen
- 不改生产 `src/**`
- 不改 SearchProfile
- 不改 FTS / RRF
- 不接 reranker
- 不改主项目依赖
- 不跑全量 pytest
- 不做 Windows 正式打包

## 最小检查

若新增诊断脚本：

```text
ruff check tools/diagnostics/qwen_speed_probe.py
git diff --check
```

只提交测速工具和报告；`tmp/` 环境、模型、缓存及大体积测试数据不提交。
