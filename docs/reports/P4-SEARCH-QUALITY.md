# 搜索质量实测与改进

2026-10-16，基于远端 `200c891`，分支 `codex/search-quality`。PR #5 已合入；此次同步增加的上游提交仅改任务卡，产品代码与实验起点 `b619252` 一致。本轮聚焦搜索，不代表全产品正式发布验收。

## 已落地的修复

1. **中文专名不再被删字。** 原来把“和田”变成“田”、“成都”变成“成”，会找出大量无关结果。现在保留连续中文，只删除空格、标点等明确分隔的独立停用词；“深圳，海边”作为两个条件，而不是强求两个词紧挨着。双引号精确短语、英文/数字前缀、安全转义和长度上限继续生效。
2. **正确说明文字命中来源。** 原来只检查文件名和地点，漏掉 OCR；多个词分别命中不同字段时也无法解释。现在先确认整条查询确实命中，再在六个文字字段中找参与匹配的词；引号内短语保持整体，不跨字段拼凑。
3. **避免重复执行全文检索。** SQLite 对 `rowid IN (...)` 可能为每个候选 ID 重启 MATCH。使用 `+rowid` 排除该查询计划，让它先遍历匹配列表再筛选候选；参数仍绑定，结果与未优化的正确实现逐条核对。

存储规范化、字符流、FTS 世代、模型向量空间和持久配置均未改变，无需重建照片索引。没有新增产品依赖，`uv.lock` 未变。架构文档同步纠正了“已经使用 jieba”的旧描述。

**文字查询的取舍：** `杭州的照片` 现在是完整字面短语；`杭州 的 照片` 会丢弃独立的“的”。旧实现能偶然召回的一些连写自然句会在文字通道少召回，语义通道仍独立编码原查询。不能把专名修复称为所有自然语言查询都变好了。

## 数据与结果

合成文本集含 76 条文件名/OCR/地点记录、30 条查询，按查询家族分为各 15 条调整/留出查询。使用真实 SQLite Catalog、FTS/BM25 和生产 RRF；未虚构模型分数。冻结配置后再比较留出集，原始失败及逐查询结果保存在忽略目录。

| 留出文本集（13 条有正例、2 条无正例） | 修复前 | 修复后 |
|---|---:|---:|
| nDCG@20（越相关的结果越靠前，最高 1） | 0.8663 | 1.0000 |
| Recall@50（已标正例找回比例） | 92.31% | 100% |
| 返回列表前 20 项中相关结果比例 | 65.38% | 88.46% |
| 无正例却返回结果的查询 | 1/2 | 0/2 |

这是专门寻找缺陷的小型合成集，不是实际图库“准确率 88%”。以固定 20 项作分母的 P@20 实际仅为 0.0577→0.0615；返回结果少，不能混淆两种指标。该集不覆盖上面的自然句取舍。英文/编号仍允许前缀匹配，因此 `CD5678` 可能带出更长编号；需要完全匹配时使用双引号，本轮未据留出集继续调参。

30 万条落盘 FTS 元数据、5000 条分散候选，分别测短 OCR 与长 OCR（只有目标 5000 行加长）。新版每场景 30 次暖调用，对照各 3 次：

| 命中来源解释步骤，中位数 | 六列语义正确但旧查询计划 | 当前实现 |
|---|---:|---:|
| 短 OCR | 11,434.79 ms | 9.238 ms |
| 长 OCR | 10,505.88 ms | 9.231 ms |

两场景均与独立真值完全一致，部分匹配干扰项无误报。原始两列实现漏解释 3333/5000 条，不用它作语义等价对照。这里不包含向量推理、排序、网络或页面绘制；首次调用也不是清空系统缓存的磁盘冷测。

公开视觉集使用 [COCO-CN 作者标注](https://huggingface.co/datasets/AIMClab-RUC/COCO-CN)，固定种子选取 240 张许可可核对的图片（151 张 CC-BY、89 张 CC-BY-SA），60 张调整、180 张留出；留出集有 194 条中文描述。下载约 40MB 图片、15.44MB 中文标注，并按需读取 COCO 许可元数据；保留每张来源/许可/哈希，图片不进 Git。使用本机已有权重和 **RTX 5070 Ti 16GB**，所有比较方案预先确定：

| 留出集方案 | 对应图片排第一 | 排前五 |
|---|---:|---:|
| Chinese-CLIP 中心裁剪（现有预处理） | 81.96% | 99.48% |
| CLIP 完整画面缩放补边 | 84.54% | 100% |
| CLIP 中心与整图各编码一次、相似度取最大 | 85.05% | 100% |
| Qwen 512 维 | 86.08% | 98.97% |
| Qwen 1024 维 | 87.63% | 100% |
| Qwen 2048 维 | 89.69% | 100% |

双视图相对中心裁剪提升 3.09 个百分点（7 条改善、1 条退步；按图片分组 bootstrap 95% 区间 0.51～6.09）。Qwen 2048 相对 512 维提升 3.61 个百分点（9 条改善、2 条退步；区间 0～7.37）。维度比较从同一次 2048 维输出取前缀并分别归一化，未把不同空间混入活动索引。

这些是“小图库中找回配对标注图片”，不是穷尽人工标注后的语义 precision；其他图片也可能符合描述，训练数据重合未知。不能外推 30 万张照片或 GTX 1660 的效果。本轮不修改模型默认值：双视图增加编码/索引成本，512→2048 维令 30 万条 float32 原始向量从约 614MB 增至 2.46GB（均不含索引），不代表模型前向计算慢四倍。

CLIP 整轮 39.69 秒；Qwen 整轮 259.12 秒，其中加载 **202.84 秒**，257 条文字编码 25.36 秒、240 张图片 29.74 秒。文字单次 p50/p95 为 23.24/42.44ms。冷加载包含校验、框架导入、CUDA 初始化、权重读取/搬运等，尚无分阶段计时，不能断言主因；这是后续值得定位的性能问题。不同预处理的运行顺序存在缓存影响，不据此宣称哪种更快。

## 调研后保留与暂缓的方案

- **保留 SQLite FTS5、LanceDB、现有模型和等权 RRF。** 当前可确认的问题来自查询构造/执行计划。SQLite 已支持 BM25 字段权重；RRF 按名次融合，归一化分数融合也有[研究依据](https://arxiv.org/abs/2210.11934)，但需要真实图像加 OCR 的联合相关性标签，纯文本样本不足以选全局权重。
- **暂不引入 jieba。** 隔离实验的 46 个刻意刁钻样例中，旧策略 22、当前 38、jieba 42 个通过；但 17 个专名当前全对，jieba 仅 14 个，还需约 0.51 秒初始化和 72MiB 内存。总分更高不等于适合直接替换；词典仍会误拆作品名。实验依赖未进入产品环境。
- **后续优先扩大整图/双视图和维度试验。** [SigLIP2 NaFlex](https://huggingface.co/google/siglip2-base-patch16-naflex)提供多语言及画幅适应方向；[Qwen3-VL Reranker](https://github.com/QwenLM/Qwen3-VL-Embedding)可针对少量候选精排，但需要额外模型、显存与延迟实测。本轮未下载这些权重。MobileCLIP2 的研究用途模型许可也不适合直接作为产品替换项。

## 验证与复现

受影响后台验证 **538 通过、19 条件跳过，exit 0，324.33 秒**；覆盖查询、FTS 投影、混合搜索、OCR 搜索、地点/人物/合集过滤、profile/session、迁移和拒绝路径。19 项均为隔离数据根未配置 PP-OCRv4 权重的真实推理测试，未新增 skip/xfail；本轮没有改 OCR 推理。验证前/后/当前 221 个源码、测试及依赖文件身份一致。

合成评测工具 28 项、公开评测工具 7 项、基准工具 4 项分别通过，2 个基准实测场景通过。独立审查覆盖查询/来源解释和公开工具，主代理完整复核合成工具与基准；修正公开 ZIP 实际解压限额、缓存文件路径校验，并补上评测输出防覆盖。公开结果全部 12 组从保存向量独立重算一致。基准 CLI 参数化后测量主体 AST 不变，复用原测量证据。Ruff 全 src/tests/tools 通过；初始回归失败均保留并有修复后证据。

这是受影响面验证，**不是一次全仓最终回归**。未改依赖、迁移语义、UI，也未合入新的产品变动，故按任务卡不重跑全套或浏览器。上一轮真实双页面冲突恢复仍保留在原任务卡为 PENDING，不被本轮覆盖。

以下在仓库根运行，使用 Python 3.12.10 项目环境；先令 `$env:UV_CACHE_DIR='.uv-cache'`，`$env:PYTHONPATH='src;.'`，临时目录设为项目 `.tmp`。输出名应使用尚不存在的新路径；文本评测和基准工具拒绝覆盖，公开视觉 `run` 拒绝非空输出目录且不改写旧失败记录，`prepare` 仍允许复用下载缓存：

```powershell
$py = '.tmp/runtime-review-20261015/full-env/Scripts/python.exe'
& $py tools/evaluation/search_quality.py --split tuning --result test-results/repro-tuning.json --freeze-to test-results/repro-config.json
& $py tools/evaluation/search_quality.py --split holdout --config test-results/repro-config.json --result test-results/repro-holdout.json
& $py tools/benchmarks/search_text_evidence.py --output test-results/repro-evidence.json
& $py -m pytest -q tests/unit/test_p2_search_tokens.py tests/unit/test_search_text_evidence.py tests/unit/test_search_quality_evaluation.py tests/unit/test_public_search_quality.py tests/unit/test_search_text_evidence_benchmark.py --basetemp=.pytest-tmp/quality-repro --junitxml=test-results/quality-repro.xml
& $py tools/evaluation/public_search_quality.py prepare --output .tmp/quality-public --count 240 --tuning 60 --seed 20261016
$env:HF_HOME = "$PWD/.tmp/quality-hf-cache"
$env:USE_TORCH = '0'
& $py tools/evaluation/public_search_quality.py run --dataset .tmp/quality-public/dataset.json --output .tmp/quality-clip --model clip --model-dir PhotoSearchData/models/chinese_clip/vit-b-16 --device cuda
$env:USE_TORCH = '1'
& $py tools/evaluation/public_search_quality.py run --dataset .tmp/quality-public/dataset.json --output .tmp/quality-qwen --model qwen --model-dir PhotoSearchData/models/qwen3_vl_embedding --device cuda
```

完整受影响验证命令与退出码在 `test-results/search-quality-20261016/affected-execution.json`；原始 JUnit/对照/身份核验均在同目录。公开图片、向量和最终指纹在 `.tmp/search-quality-20261016/`。它们是本机可复核证据，不随 Git 分发；仓库仅保留代码、回归测试、小型合成夹具及本摘要。
