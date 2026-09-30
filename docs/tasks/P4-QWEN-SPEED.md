# P4-QWEN-SPEED — Qwen 30万搜索速度最小评估

## 目标

只回答：

> Qwen3-VL-Embedding-2B 用于当前 30 万图库时，搜索会比 Chinese-CLIP 慢多少？

纯测速，不改生产功能。

执行建议：**GPT-6 Luna / medium**。若 2048D generation 无法正确复用既有 30 万 fixture，或 Qwen CUDA 独立环境出现需要跨模块定位的依赖/显存问题，再升 **Luna / high**；本卡不需要 Sol / DS-H 独立审核。

## A. 30万向量检索

复用现有：

```text
tmp/p402f-阶段0/scale-300000-wave/data
```

固定：

```text
300000 vectors
top_k=5000
block=15000
exact search
```

### 512D

直接使用现有 fixture，不重建。

```text
warmup 1
正式 5 次
```

作为本轮基线。

### 2048D

复制现有 30 万 fixture 到临时目录，仅新增：

```text
2048D generation
300000 confirmed index entries
2048D Lance vector table
```

synthetic L2-normalized float32 vectors 即可。

必须：

- 分块生成；
- 分块写 Lance；
- 不在内存保存完整 `300000 × 2048` 矩阵；
- 不修改原历史 fixture。

同样：

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

需要时新增独立脚本：

```text
tools/diagnostics/qwen_speed_probe.py
```

不要修改历史 `p4_02f_stage0.py`。

## B. Query 编码速度

真实测：

```text
Chinese-CLIP
Qwen3-VL-Embedding-2B
```

固定查询：

```text
美女和猫
海边日落
两个人一起吃饭
```

每模型：

```text
warmup 3
正式 10 次
```

记录：

- Query encode P50
- 模型加载时间
- GPU 峰值显存

Qwen 输出只比较：

```text
512D
2048D
```

若 512D 只是同一次 forward 后的 MRL 截断，不重复推理。

### Qwen 环境

使用 `tmp/` 下独立临时 Python 环境。

禁止修改：

```text
pyproject.toml
uv.lock
当前 .venv
```

Qwen 使用真实 CUDA 正常精度路径。

若发生：

```text
CUDA OOM
模型无法在当前 RTX 4060 Laptop 8GB 正常加载
```

直接记录结果。

本卡不改成：

- CPU
- CPU offload
- 量化
- 其它模型

来绕过。

## C. 输出

```text
docs/reports/P4-QWEN-SPEED.md
test-results/p4-qwen-speed.json
```

最终只需要：

| 模型 | 维度 | Query P50 | 30万 Vector P50 | 相对当前 |
|---|---:|---:|---:|---:|
| Chinese-CLIP | 512 | 实测 | 本轮实测 | 1.00× |
| Qwen | 512 | 实测 | 同512D基线 | X× |
| Qwen | 2048 | 实测 | 实测 | X× |

并回答：

```text
Qwen 512D 相对当前约慢多少
Qwen 2048D 相对当前约慢多少

主要额外耗时来自：
Query encoding / vector dimension / 两者
```

## 不做

- 不测准确率
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

```text
ruff check tools/diagnostics/qwen_speed_probe.py
git diff --check
```

只提交测速工具和报告。
