# 纯向量检索：准确度、同类工程方案与本机成本

2026-10-21；基线 `a57d7d5`。**实验完成（限定公开数据与本机），不自动切换生产方案**。500图和800对真实CUDA实验、独立复算与测量工具有界审查均完成。

用户确认错序照片全部只有向量命中。这把主要排查方向指向**图像表示与相关性排序**：一张照片的整体向量不一定能突出角落的小猫；“整体很像猫照片”也不等于“画面里一定有猫”。现有检查未发现距离反向、截断未归一化等实现错误。提高“高匹配”阈值只会隐藏结果，不能把后排真正相关照片搬到前面。没有用户私人图库，本报告不声称已复现或修复其具体照片。

## 实际工程怎样做

核对官方文档和固定版本源码，未运行这些产品，也不拿它们的宣传或硬件数字代替本项目实测。

| 工程 | 已核实做法 | PhotoSearch可借鉴的部分与限制 |
|---|---|---|
| [Immich](https://docs.immich.app/features/searching/)；源码 `166e9355` | 图片后台编码、查询时编码文本；可选CLIP/SigLIP和多语言模型；模型变化后重新编码图库，支持元数据过滤 | 保留离线建库、在线轻查询；模型应按语言、速度和质量选择。换Postgres/VectorChord不能让同一组向量突然更懂猫 |
| [PhotoPrism](https://docs.photoprism.app/developer-guide/vision/label-models/)；源码 `8e724ef1` | 当前默认本地ONNX分类器EfficientFormerV2-S2约12.7M参数；中心方块与整图输入分别分类并合并标签；整图最多保留4:3画幅，极宽两端仍会裁掉；标签有来源和阈值 | 常见物体标签与开放语义搜索可分工；有界局部输入有真实工程先例。本轮四角70%方案是对该思路的不同实现，并非复制它的模型/参数；分类器也可能漏小主体 |
| [LibrePhotos 1.3](https://docs.librephotos.com/blog/1-3-0/)；源码 `3735817d` | 一次OpenCLIP图像计算同时供标签、语义搜索和相似图使用；缓存词表文本向量，按模型自己的logit scale作词表softmax选标签 | 应复用已有图像计算，避免每张图分别跑多个大模型。但同一向量生成的标签不是独立视觉证据；Qwen没有可直接照搬的CLIP标签标定值，多物体图还会有类别竞争 |
| [Ente](https://ente.com/ml/) | MobileCLIP v1、ONNX、本地离线图像向量与轻量文本查询；文本塔量化并强调图像预处理一致性 | 说明低延迟产品常把重活放在导入阶段。没有证据表明其Magic Search依赖逐候选大模型重排，不能将这一做法归给它 |

源码证据：[PhotoPrism输入](https://github.com/photoprism/photoprism/blob/8e724ef17786e2b01a985c02971d7f544182312c/internal/photoprism/mediafile_label_inputs.go)、[标签合并](https://github.com/photoprism/photoprism/blob/8e724ef17786e2b01a985c02971d7f544182312c/internal/ai/vision/labels_prepared.go)、[LibrePhotos词表评分](https://github.com/LibrePhotos/librephotos/blob/3735817d309579eb17cd2051f8c353197f62857c/apps/backend/service/tags/openclip/openclip.py)。因此本轮优先评估已有模型的表示质量、局部补充和查询成本，没有为了换技术栈而迁移数据库。

## 同一批图片的结果

复用上一轮500张公开COCO图片，250张随机、250张按目标/混淆类别选取；8类对每张图都有按COCO实例标注生成的判定，其中84张标猫。COCO仍可能漏标；主表“汽车”按car，公交/卡车另属类别。指标AP同时惩罚前排混入不相关图片和相关图片排后，**不是分类准确率**。所有本轮方案是在已看过的数据上探索，不是独立留出验证；训练数据重合未知。另4条颜色/数量/关系查询没有完整真值，不算精度。设备为RTX 5070 Ti 16GB，开始已有约5.3GB其他显存占用；不是4060/1660验收。

| 方案 | 八类平均AP | 猫AP | 前100找回标猫图 / 84 | 成本与判断 |
|---|---:|---:|---:|---|
| Qwen整图512（当前配置） | 80.23% | 95.45% | 79 | 基线 |
| Qwen整图1024 | 83.68% | 97.08% | 80 | 不多做神经网络前向；原始向量容量2倍 |
| Qwen整图2048 | 84.83% | 97.80% | 81 | 原始向量容量4倍；现有512缓存不能还原完整向量 |
| WeMM512（上轮复用） | 86.82% | 98.14% | 81 | 图编码中位约2.81倍，car类别明显退步，不能凭总分切换 |
| Qwen2048，更高像素预算 | 84.73% | 98.11% | 81 | 每图中位62.60→77.48ms；未见整体收益 |
| Qwen2048，整图＋四角，各自评分取最大 | 85.88% | 98.38% | 83 | 编码约4.5倍算术成本，相对整图2048向量存储5倍；随机半集未改善 |
| Qwen2048，五视图平均成一条向量 | 84.81% | 96.99% | 81 | 存储可省，但不能保留取最大值的质量收益 |
| Qwen2048＋重排Top50 | 87.75% | 97.80% | 81 | 每query多约3.29秒候选处理；猫原前50已全对，内部换序不改变猫AP |
| Qwen2048＋重排Top100 | 89.28% | 98.28% | 81 | 每query多约6.61秒候选处理；人类别AP下降0.92个百分点 |

1024/2048提升并非模型推理慢2/4倍：模型本来产生2048维，目前仅保存前512维。30万条float32原始向量分别约0.614/1.229/2.458GB，不含索引、ID、旧代与临时数据。五条2048向量则约12.29GB，是当前单条512的20倍，不能把单向量容量当成多视图成本。更大维度仍增加向量读取/检索成本，本轮未测30万端到端延迟。

查询表达也实测了中文原词、中文照片描述、英文词、英文照片描述、“画面中有…”以及前四种等权均值。2048维的平均AP依次为84.83/74.44/84.94/75.72/78.87/83.15%；不建议默认扩写或平均。英文单词在512维平均提高2.29个百分点，但有类别退步，而且不能据此自动翻译复杂中文查询。

更高像素预算由262144改为524288，图片最大边仅640，本试验不能回答手机大原图是否收益更大。SDPA/eager对固定32图配对，中位62.49/63.57ms，未显示值得改默认的速度收益；最小向量余弦约0.999867，非逐位一致。

## 猫的具体难例与局部方案的代价

厨房左下角的小黑猫（COCO 320875）：整图512/2048名次273/121，四角取最大后为81/76；增大像素预算反而287/128。这个例子支持“背景占据整体表示、局部补充能救回”的解释。

但COCO的猫还包含远处疑似狮子的猫科动物（119938）和公交照片背景墙上的猫图案（327610）。面积≤1%的6张标猫图，各整图方案仅3张进Top100，不能说成漏掉3只家猫。指标保持原标签，解释单列，不按结果删除难例。

五视图取最大在2048维全500平均提高1.05个百分点，随机250却下降0.26，定向250提高1.96；car/person分别下降约0.68/1.84个百分点。额外四图编码中位合计221.57ms，加原整图62.60ms约284ms/图（不同轮次的算术估计，不是成对端到端吞吐）。任何一个局部相似就会提高整张图评分，也会增加误报。因此值得作为难例补充研究，不适合立即全库默认跑五份。

这种取舍与用户反馈直接相关：五视图虽然多找回两张标猫图，第一张未标猫结果却从第77提前到72；Top100重排则把首张未标猫推到80，但无法扩大召回集合。“找到更多猫”和“少让别的图插队”须分别衡量。

## 候选重排与模型选择

Qwen3-VL-Reranker-2B固定revision `4bd860ac4f15ad1897a214615cccc700f8f71818`，约4.27GB文件、Apache-2.0。查询与图片一起输入，直接判断相关性；它增加了新视觉判断，和对旧向量重新排序不同。官方脚本固定校验，本工具遇图片错误严格停止，避免官方NULL替代；使用yes-no原始logit排序，避免FP16 sigmoid将多个高分压成一样的1。该分数不是经过校准的“有猫概率”。

先完成16对烟测，再评估Qwen2048的8类各Top100共800对；同一评分额外比较仅重排Top20/50的成本。重排只改变候选内部次序，原TopK外保持原序，Recall@K必须不变。厨房小猫原第121名，Top100重排不可能救回；不能把裁剪的召回收益记给重排。

| 只重排前K张 | 八类平均AP | 八类平均P20 | 八类平均P50 | 每query候选处理总耗时中位 |
|---|---:|---:|---:|---:|
| 不重排 | 84.83% | 93.13% | 72.00% | 无额外看图 |
| K=20 | 85.46% | 93.13% | 72.00% | 1.32秒 |
| K=50 | 87.75% | 98.13% | 72.00% | 3.29秒 |
| K=100 | 89.28% | 97.50% | 75.50% | 6.61秒 |

K=100的随机/定向半集AP分别85.21→88.93、85.12→90.15%；7类AP升、人下降0.92个百分点。K=50前20略好，K=100整体AP和前50更好，不能用一个指标说谁全面胜出。猫前50本已全对，所以K=50对猫AP没有收益；另一张小黑猫140831在K=100时98→77，而厨房黑猫仍121。

事后按更宽泛的car|truck|bus解释“汽车”，76张正例的AP仍88.64→90.22%，但P50从96→92%；Top50重排AP89.91%、P50仍96%。这项敏感性只帮助解释词义，不替换主表定义，也没有重新调模型。

耗时为同轮已测图片解码＋预处理＋推理逐对求和，再取8条查询中位；K100范围6.46–6.97秒，**不是8条查询各重复30次的稳定P95，更不是HTTP端到端耗时**。真实800对处理53.22秒、模型加载2.63秒、含校验/启动等进程总64.95秒；自身GPU峰值allocated/reserved约4.05/4.08GiB。它与Embedding串行测量，没有证明双模型常驻、后台争用或首屏预算达标。

其他模型按任务与成本筛选：[SigLIP2 Base上一轮中文配对检索较差](P4-SEARCH-QUALITY.md)，不因Immich支持就默认替换；[So400m版](https://huggingface.co/google/siglip2-so400m-patch16-naflex/tree/cc24074f717b612951c2dead130904ab9b65a81e)全模型约1.136B而非400M，尚未实测。[NVIDIA Nemotron VL 1B](https://huggingface.co/nvidia/llama-nemotron-embed-vl-1b-v2/tree/582e3bf72aee355e3c59ed89de53543c5b0657ee)系列实际约1.678B且主打文档页/图表，与相册小主体任务不同。[Jina v4](https://huggingface.co/jinaai/jina-embeddings-v4/tree/853c867b65b749f3c3c72a06868140d842e04f06)约3.755B、多向量更重且有研究许可限制。本轮优先同一Qwen的可解释取舍，没有把榜单高低当作私人照片质量结论。

来自PhotoPrism/LibrePhotos的下一条工程路线是**导入时生成可纠正的机器物体标签，开放描述继续走向量搜索**；可进一步用小型目标检测器为猫狗生成局部框，再只给必要区域算向量。这比每次搜索对100张图跑大模型更有希望控制长期成本。但本轮尚未验证该检测器与独立数据，不能把COCO训练检测器在COCO train2014上的成绩当泛化证明；机器标签不能覆盖人工事实，也不应当成必需硬过滤而漏掉未识别的猫。

**本机取舍建议**：普通搜索优先准备同一Qwen的1024或2048候选空间，2048质量更好、1024容量折中；需要重新编码只有512缓存的图库，不能就地改维度或活动身份。可选精细搜索再考虑Top50重排，明确等待/取消/结果会话和模型资源预算；它更能改善多类前排质量，但对本次已经全对的猫前50无提升。五视图、提高像素预算、自动查询扩写和WeMM默认替换均不推进。离线轻量标签/局部检测是来自同类工程的下一项有价值实验，先用独立数据验证，再决定是否接入。以上是候选架构建议，本轮没有发布生产行为变更。

## 复现与验证范围

复用[上一轮报告](P4-WEMM-QUALITY-PILOT.md)的公开数据准备和Qwen/WeMM基线。用Python3.12.10、torch2.8.0+cu128、transformers4.57.0、qwen-vl-utils0.0.14的既有Qwen兼容环境；不改生产依赖。

```powershell
# $py 指向已准备的兼容解释器；以下从仓库根执行，输出目录必须全新。
& $py -m tools.evaluation.vector_quality_probe query --sample-file .tmp/wemm-pilot/datasets/sample.json --queries .tmp/wemm-pilot/datasets/queries.json --model-dir PhotoSearchData/models/qwen3_vl_embedding --baseline-dir .tmp/wemm-pilot/runs/full/qwen --out-dir .tmp/vector-quality/query
# 将 query 与输出末段分别改为 highres / crop5，串行运行。
& $py -m tools.evaluation.vector_quality_reranker prepare --output .tmp/vector-quality/reranker-plan.json
& $py -m tools.evaluation.vector_quality_reranker run --plan .tmp/vector-quality/reranker-plan.json --output .tmp/vector-quality/reranker-run
# reranker目录需包含固定版本模型文件及scripts/qwen3_vl_reranker.py，按checked-in lock校验。
& $py -m tools.evaluation.vector_quality_metrics derive --query-dir .tmp/vector-quality/query --highres-dir .tmp/vector-quality/highres --crop5-dir .tmp/vector-quality/crop5 --out test-results/vector-quality-derived.json
& $py -m tools.evaluation.vector_quality_metrics rerank --reranker-dir .tmp/vector-quality/reranker-run --reranker-plan .tmp/vector-quality/reranker-plan.json --out test-results/vector-quality-reranker-derived.json
& $py -m tools.evaluation.vector_quality_metrics analyze --extra-rankings test-results/vector-quality-derived.json --extra-rankings test-results/vector-quality-reranker-derived.json --out test-results/vector-quality-metrics.json
& $py -m pytest -q tests/unit/test_vector_quality_probe.py tests/unit/test_vector_quality_metrics.py tests/unit/test_vector_quality_reranker.py --basetemp=.pytest-tmp/vector-quality --junitxml=test-results/vector-quality.xml
```

代码交付仅为3个可复用离线工具、对应单测、2份小型固定计划/权重清单与本卡/报告/状态。原始图片、模型、向量、JSON/JUnit和官方研究源码留在忽略目录。

验证：probe17、metrics32、reranker27项定向单测通过（76项）；Ruff、diff-check通过。真实query/highres/crop5、16对smoke、800对重排均exit0，输入/源码/权重身份可核验；最终分析含34种配置，独立累积命中算法复算全部分层指标，并重建800条评分的24组候选排名。独审发现的未完成基线拒绝、查询文本绑定、comparison输入与query绑定、重排计划/运行配置匹配均已修复并通过反例复测，真实GPU数据未发现错配，无需因纯拒绝校验增量重跑GPU。属于冻结GPU证据＋最终CPU复算与验证的组合，不声称最终源码单次全仓全绿。

原始失败保留：初轮probe单测一处测试调用漏传维度；独立审查反例在修复前按预期失败；额外attention探针一次NumPy非标量转float报错exit1，改用item后同32图配对exit0。没有删除失败、跳过失败图片或改ACL。证据目录为 `test-results/p4-vector-quality-tuning/`、`test-results/p4-vector-quality/` 与 `.tmp/vector-quality/`。

生产src、模型清单、活动空间、私人图库均未改；未验证30万搜索、双模型并驻、HTTP等待/取消、真实原图离线及迁移切换，不能称产品已接入重排或用户问题已解决。本轮没有提交或推送。
