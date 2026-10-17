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
- **后续优先扩大整图/双视图和维度试验。** [SigLIP2 NaFlex](https://huggingface.co/google/siglip2-base-patch16-naflex)提供多语言及画幅适应方向；[Qwen3-VL Reranker](https://github.com/QwenLM/Qwen3-VL-Embedding)可针对少量候选精排，但需要额外模型、显存与延迟实测。第一阶段未下载；SigLIP2获准后的追加实测见文末，Reranker仍未下载。MobileCLIP2 的研究用途模型许可也不适合直接作为产品替换项。

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

## 同类项目方案对照（第二阶段，2026-10-16）

按用户要求继续查官方文档及固定源码；不是安装竞品后的整体验收。已合流远端 `0dd903d` 的有界人物整理功能，52项受影响测试通过；该提交和前次遗留浏览器PENDING继续由原卡跟踪。本阶段复用已有权重、独立公开/合成数据，不改变生产搜索默认值。

| 项目与定位 | 核对到的实际方法 | 对本项目的判断 |
|---|---|---|
| [Immich](https://docs.immich.app/features/searching/)，自托管照片库，源码 `e5094d5` | Postgres + VectorChord；OCR把中文转为重叠双字，再做trigram模糊匹配；不同模型使用对应预处理 | 借鉴OCR错字候选；不能把模糊匹配当作精确编号/短语，也没有理由仅为相同功能换数据库 |
| [Ente Photos](https://ente.com/ml/)，端到端加密照片产品，源码 `0f6c3b6` | 本机MobileCLIP，余弦门槛0.175；查询向量缓存与预热；USearch在源码中受功能开关控制 | 门槛值得校准实测，数值不能跨模型搬用；其OCR是按需文字选择，不是我们这样的全图库OCR索引 |
| [LibrePhotos](https://docs.librephotos.com/docs/user-guide/search/)，自托管照片库，源码 `28fb70f` | OpenCLIP + FAISS精确内积；文字匹配与过阈值的语义结果取并集，文档明确最终列表不按相似度排序；同一次图像编码还能生成标签 | 它的31.3是未归一化内积，不能作为我们的cosine阈值；现有RRF继续保留。复用向量做受控标签是后续候选 |
| [PhotoPrism](https://docs.photoprism.app/user-guide/search/filters/)，自托管照片管理，源码 `9321352` | 标签/关键词关系加SQL条件，可选模型生成描述；审查的照片搜索路径不是CLIP+BM25融合 | 明确筛选和可解释标签值得借鉴；自动描述不等同于可靠OCR |
| [digiKam](https://docs.digikam.org/en/left_sidebar/similarity_view.html)，本地桌面照片管理，官方Invent `cb31e36` | Haar小波指纹用于相似图/重复图/草图；Tesseract是显式批量后处理 | 感知指纹与中文语义搜索互补，不能替代语义向量或字节身份 |

关键实现：[Immich OCR词法](https://github.com/immich-app/immich/blob/e5094d54e95fc5c7d6f39990485835b15c0707bf/server/src/utils/database.ts#L372)、[Ente门槛及缓存](https://github.com/ente-io/ente/blob/0f6c3b6367a07b8d55e5d134682749c2389afde3/mobile/apps/photos/lib/services/machine_learning/semantic_search/semantic_search_service.dart#L35)、[LibrePhotos精确索引](https://github.com/LibrePhotos/librephotos/blob/28fb70f944e00ad749ee77e9b2eecc78ff98b610/apps/backend/image_similarity/retrieval_index.py)、[PhotoPrism搜索](https://github.com/photoprism/photoprism/blob/9321352a7dac030ff51eade6f43044ac39a1b1e7/internal/entity/search/photos.go)、[digiKam小波指纹](https://invent.kde.org/graphics/digikam/-/blob/cb31e36b97ee1c0a06cc0c1b7d576f745ff98973/core/libs/database/haar/haar.cpp)。LibrePhotos的64位pHash去重仍是全对全比较，不能直接扩到30万张；digiKam的GitHub镜像已旧，本次核对的是官方Invent。

### 实际尝试一：容忍OCR错字

当前/旧文字策略走真实SQLite Catalog/FTS。Immich方向用SQLite Python UDF近似Postgres文档定义的完整词区间trigram Jaccard，固定默认门槛0.5；没有部署Postgres或复现其GIN执行计划，不声称这是Immich整机效果。分别输出六字段适配和OCR-only结果，原产品的OCR过滤也不等于本实验相似度排序。

在冻结的新40条文本、22查询上，六字段近似法能找回4/4自然句和4/4错字目标，当前字面FTS两组均0/4；代价是编号返回精度87.5%→50%、跨字段条件100%→37.5%、无相关查询误报0/2→1/2。例如查ZX9019带出ZX9012/ZX9013，“深圳 灯塔”带出珠海灯塔。原76条记录/30查询也没有整体变好：当前正例召回100%、无相关误报0/4，模糊近似为92.3%、3/4。均为刻意设计的小组，不能当总体准确率。

因此本轮保留精确默认行为；模糊候选适合后续独立的“纠错/放宽”入口。它还需要有界候选索引，不能把这里的小数据UDF全扫描放进30万图库。

### 实际尝试二：整图、融合与阈值

固定600张新的公开COCO-CN图片，来自官方train部分，按本项目用途重新固定200张调整、400张确认，seed20261017；确认有453条中文描述。旧240张的ID与内容SHA均排除，新增集内部重复内容也拒绝。这里的“未见”指本项目未使用，不保证模型训练未见。下载图片约96.5MB，许可/来源/文件指纹保留；实际GPU仍为RTX5070Ti16GB。

方案在结果前声明：中心裁剪、整图补边、两视图取最大、两视图平均后归一化为一条向量、Qwen512，以及CLIP+Qwen等权RRF和按调整集均值/标准差归一化后的等权分数。维度实验继续保留原512/1024/2048对照；没有按新确认结果网格调参。

| 新确认集：400图、453条中文描述 | 对应图片排第一 | 排前五 |
|---|---:|---:|
| CLIP 中心裁剪 | 68.87% | 92.27% |
| CLIP 整图补边 | 70.42% | 91.61% |
| CLIP 两视图取最大 | 72.85% | 92.72% |
| CLIP 两视图平均单向量 | 70.64% | 92.49% |
| Qwen 512维 | 72.19% | 96.03% |
| CLIP+Qwen 等权RRF | 75.28% | 96.03% |
| CLIP+Qwen 调整集标准化分数融合 | 77.92% | 96.25% |

双视图最大值相对中心裁剪 +3.97个百分点，31条改善/13条退步，按图片分组bootstrap 95%区间[0.69,7.63]；单均值向量只+1.77，区间[-1.04,4.54]，未证明可以保留全部收益。标准化融合相对Qwen512为+5.74，39条改善/13条退步，区间[2.59,8.95]；RRF为+3.09，区间[-1.30,7.17]。区间均未作多重比较校正，限于预声明方案的小图库配对检索。宽/高画幅子集92图110查询单列于原始结果，不据此追加规则。

这里融合的是**两个图像模型的检索结果**，不是已经证明文字FTS与图像通道应该改用该公式。两模型错误有互补：只CLIP正确50条、只Qwen正确65条；但融合仍有13条相对Qwen退步。更大的确认集也显示第一阶段小集的高分不能外推。

Qwen维度独立重算：512/1024/2048的Top1为72.19%/72.63%/74.61%。2048相对512 +2.43个百分点，区间[0,4.99]，1024 +0.44，区间[-1.56,2.49]。仍未据此改默认。

**阈值诊断：** 直接照搬Ente的0.175，在400图库里CLIP平均返回398.69张，Qwen返回71.93张。仅在调整集选择保留至少95%已知配对的阈值（CLIP0.41618、Qwen0.35507）后，确认集平均候选降至6.79/5.39张，但已知目标也只保留94.04%/96.03%。其他图片未穷尽标注，因此不能把它叫precision，也不能证明真实“无相关图”的拒绝效果。当前默认不增加这个硬截断。

**成本与决定：** 双视图最大值、双模型标准化融合是最值得继续的候选；平均成单向量尚未证明足够好。双视图/双模型512维裸向量约1.23GB/30万图，而一条512维约614MB；双模型还要两次查询编码及两个独立空间。当前产品只有一个活动图像世代，不能用一处排序改动偷偷启用双模型；本轮保持默认，不修改持久格式/依赖或真实索引。FAISS/USearch/VectorChord没有在本轮替换测试，同行使用它们不等于我们的精确有界筛选应该改成ANN。

本轮CLIP模型加载26.75秒、文字单次p50 3.35ms；Qwen加载23.27秒、文字p50 28.22ms。与上轮203秒加载不同，但模型代码未改、未控制系统冷缓存，不能声称冷启动已修复。模型运行串行，未证明双模型同时常驻显存或产品端到端延迟。

### 第二阶段验证与复现

新增/扩展工具测试：公开数据准备12项、视觉重放21项、文字对照9项均通过；上游合流受影响52项通过，实际exit0。两次模型运行、数据准备、fit/evaluate和Qwen维度重算均有实际退出记录及JUnit。独立审查推动补上文字实验依赖指纹、视觉captions类型等完整性校验；修复前后文字208条结果和探索视觉排名完全一致。工具/夹具变化不触发未变产品全仓重跑。本轮未新增UI行为，不把上游既有浏览器缺口算作通过。

先按前文设置项目Python、临时目录及缓存，再运行；模型目录须已有权重，输出使用新路径：

```powershell
& $py tools/evaluation/public_search_quality.py prepare --output .tmp/peer-repro/data --source-split train --exclude-dataset .tmp/search-quality-20261016/datasets/dataset.json --count 600 --tuning 200 --seed 20261017
# 使用前文 run 命令分别生成 .tmp/peer-repro/clip 与 qwen，dataset 改为 .tmp/peer-repro/data/dataset.json
& $py -m tools.evaluation.peer_search_quality fit --dataset .tmp/peer-repro/data/dataset.json --clip-run .tmp/peer-repro/clip --qwen-run .tmp/peer-repro/qwen --plan tools/evaluation/peer_search_plan.json --mode confirmation --exclude-dataset .tmp/search-quality-20261016/datasets/dataset.json --output test-results/peer-repro-frozen.json
& $py -m tools.evaluation.peer_search_quality evaluate --dataset .tmp/peer-repro/data/dataset.json --clip-run .tmp/peer-repro/clip --qwen-run .tmp/peer-repro/qwen --plan tools/evaluation/peer_search_plan.json --mode confirmation --exclude-dataset .tmp/search-quality-20261016/datasets/dataset.json --frozen test-results/peer-repro-frozen.json --output test-results/peer-repro-result.json
& $py -m tools.evaluation.peer_text_search --freeze-to test-results/peer-text-plan.json
& $py -m tools.evaluation.peer_text_search --plan test-results/peer-text-plan.json --result test-results/peer-text-result.json
& $py -m pytest -q tests/unit/test_public_search_quality.py tests/unit/test_peer_search_quality.py tests/unit/test_peer_text_search.py --basetemp=.pytest-tmp/peer-repro --junitxml=test-results/peer-repro.xml
```

首次复现须先按第一阶段prepare命令生成旧240图排除清单；公开图片与向量不随Git分发。原始材料在 `.tmp/peer-search-20261016/`、`test-results/peer-search-20261016/`。固定方案随代码保存于 `tools/evaluation/peer_search_plan.json`；旧240的新变体结果只作二次探索，新600为本轮确认。

### SigLIP2 NaFlex追加对照

用户单独批准后，下载[Google SigLIP2 Base NaFlex](https://huggingface.co/google/siglip2-base-patch16-naflex/tree/b53b807d3a2d5e2b3911292f2d69e5341cdc064c)，固定revision `b53b807d`，Apache-2.0。8文件共1,539,627,432字节，逐项核对官方LFS SHA256或Git blob身份；权重留在忽略的 `PhotoSearchData/models/siglip2-base-patch16-naflex/`。小型文件校验清单 `tools/evaluation/siglip_model_lock.json` 随代码保存。

同一600图及原200/400划分，原生768维、float32、batch1；官方NaFlex最多256块、每块16×16像素，尽量保留画幅比例。文本仅转小写，固定64 tokens和官方填充，不加英文分类模板；图像EXIF转正/RGB后交官方processor。没有调参或改生产配置。这是已经看过基线结果后的追加固定对照，不是新的盲测。

| 400图、453条查询 | 配对图片排第一 | 排前五 |
|---|---:|---:|
| Chinese-CLIP默认 | 68.87% | 92.27% |
| Chinese-CLIP双视图最大值 | 72.85% | 92.72% |
| Qwen512 | 72.19% | 96.03% |
| Qwen2048 | 74.61% | 96.69% |
| SigLIP2 NaFlex768 | 62.03% | 88.08% |

SigLIP2相对默认CLIP为46条改善、77条退步，Top1差值−6.84个百分点，图片分组bootstrap 95%区间[−12.04,−1.64]；相对Qwen512差值−10.15，区间[−15.34,−4.93]。区间未作多重比较校正；配对命中率仍不是穷尽相关性precision。当前证据不支持换成该版本。独立核对666句最长32 tokens、没有触发64长度截断；原预声明宽/高画幅子集为69/110命中（62.73%），未显示足以替换的收益。

RTX5070Ti上的实际编码：666条文字共3.19秒，中位数3.45ms、p95 5.64ms；600图片共6.30秒，中位数9.80ms、p95 11.66ms。同步计时包含预处理、传输、归一化，第一次文字/图片分别593.56/363.85ms，未丢弃。PyTorch峰值分配1.52GB、保留1.62GB，不含框架分配器以外的CUDA开销或其他进程；没有据此宣称GTX1660资格通过。权重/processor加载2.86秒，另有文件核验3.08秒、导入3.07秒、CUDA初始化0.14秒；完整进程20.99秒。下载后文件缓存已热，不当作冷启动承诺。

768维令30万张裸float32向量约922MB，比512维多50%；本样本中文效果更低，所以保留现有模型。模型运行和CPU比较实际exit0；新增13项工具测试与Ruff通过，复用未改旧工具/产品证据，没有重新执行全仓测试。真实模型调用验证了NaFlex的pixel mask和spatial shapes路径，工具保护模型/数据/运行环境身份与旧证据。

复现：先按固定revision准备模型目录并用锁文件校验，使用前文Python/项目缓存环境；运行后执行CPU比较，不需要再次编码旧模型：

```powershell
& $py -m tools.evaluation.siglip_search_quality run --dataset .tmp/peer-search-20261016/datasets/dataset.json --model-dir PhotoSearchData/models/siglip2-base-patch16-naflex --model-lock tools/evaluation/siglip_model_lock.json --output .tmp/siglip-repro
& $py -m tools.evaluation.siglip_search_quality compare --dataset .tmp/peer-search-20261016/datasets/dataset.json --run-dir .tmp/siglip-repro --clip-run .tmp/peer-search-20261016/clip-confirmation --qwen-run .tmp/peer-search-20261016/qwen-confirmation --plan tools/evaluation/peer_search_plan.json --exclude-dataset .tmp/search-quality-20261016/datasets/dataset.json --output test-results/siglip-repro-comparison.json
& $py -m pytest -q tests/unit/test_siglip_search_quality.py --basetemp=.pytest-tmp/siglip-repro --junitxml=test-results/siglip-repro.xml
```

## 本地产品完善（2026-10-17）

本阶段最初按用户要求关闭PR #6并保留本地；用户随后重新授权提交、推送并新建PR。产品基于远端 `2e0e550`，提交前又合入 `4ed7e9d` 的两份收藏设计卡（没有收藏功能实现）。架构和组件可按实测收益调整，不再把原固定选型作为限制。这一阶段有实际产品/UI变化，与前面的测量工具阶段分别验证。

- **按文字找**：新增显式文字模式，直接查已有SQLite FTS，覆盖文件名、有效地点名称和OCR。输入里的姓名、地名、年份不再自动变成筛选条件，适合截图、票据、编号；不加载图像模型。手动筛选照常生效，分页/时间浏览/返回快照绑定模式；达到5000候选时提示可能还有更多。引号限定完整短语，未识别的图片文字无法命中，备注等预留列未宣传为已实现。
- **人物组合不漏图**：原来在A的人物相册里选“B或C”，程序会错误改成“A、B、C全部同框”。现在固定相册与额外选择独立取交集，即 `A AND (B OR C)`；切换相册不能续用旧游标，查询途中人物合并会拒绝发布混合结果。取消固定相册会保留原查询的人物条件。
- **整理后页面同步**：纠正移出人物按钮的动作名；保存人脸更正后刷新人物/合集照片、计数和日期摘要，同时保持当前预览。头像从有效相册摘要读取，避免被不含显示头像的原始人物记录覆盖。迟到响应不能覆盖重新打开的相册；保存成功但刷新失败会分别说明。
- **浏览和说明**：修复完整详情返回时丢失当前预览、跨页“下一张”没有等待续页完成的问题；文字结果显示“文件名/图片文字”等匹配位置，无模型时提示可用的文字入口。简化搜索说明，保留实际功能边界。
- **少量候选的数据库核对**：只检查一页照片时，原SQL仍重复生成整个人物相册的成员集合。现在不超过5000个候选时，从照片ID索引查来源和人脸归属；沿用同一套人工锁定、排除、识别替代规则。全库召回和总数统计保留适合大集合的SQL，没有用全库Top-K后过滤换速度。

### 30万条的实际成本

同一30万条合成元数据（不含图片文件）、同一当前源码，只在基准进程中切换优化前后的候选核对函数。7类请求各5次，表中为后4次中位数，包含进程内ASGI请求的鉴权/CSRF、SQLite、排序和序列化，不含网络与浏览器绘制；测试串行，未同时跑模型或其他pytest。不是远端完整版本与本地完整版本的性能对比，也不保证操作系统冷缓存。所有首/续页照片ID及顺序一致，模型、向量与自然语言解析调用均为0。

| 场景 | 优化前 | 优化后 |
|---|---:|---:|
| “订单”文字查询 | 187ms | 176ms |
| “深圳 订单” | 179ms | 180ms |
| 精确数字/日期文字 | 174ms | 172ms |
| 常见词，约27万匹配、保留5000候选 | 795ms | 791ms |
| 无匹配 | 109ms | 102ms |
| 来源＋人物 | 1064ms | 695ms |
| 人物合集 | 1122ms | 1114ms |

上述来源/人物阶段，续页一次测量为188.08→4.56ms（约41倍），合集续页193.36→190.70ms，当时尚无明显改善。合集随后单独优化，见下方追加结果。首次范围计数、常见词FTS及5000候选解释仍是主要成本；没有把小批索引探测强行用于全库扫描。小规模随机波动不当作收益。原始阶段剖析曾与回归并行，只用于定位，表格使用随后空闲配对结果。

### 取舍和验证

目前保留SQLite、现有向量实现及默认模型，是基于收益与成本的判断：这次慢点主要来自重复SQL，修执行路径即可获得确定收益；换数据库还需迁移、部署和恢复成本。新SigLIP2在这批中文图片上较差；双模型融合有质量收益，但增加编码、显存和索引空间，尚未验证6GB显存下的完整工作流。模糊文字能容忍错字，却会混淆编号/多条件。本轮没有为一个分数启用这些默认替换；后续优先候选为显式纠错入口、可选双模型检索及合集大范围统计优化。

后台首轮638节点637通过，唯一失败是摘要参考测试没包含新增模式；45项增量修正并保留时间规范化断言。固定人物交集37项、其他入口及真实第二连接并发合并31项、候选优化20项和120项消费者回归均exit0。UI226节点中5个旧文案断言失败，后续99项全部闭合；此前旧viewer夹具、动作名、日期摘要和头像问题的失败材料也保留。当前后台/UI共**894个节点组合通过**（重叠去重），加上未受影响的102项人物后台合流证据；不相加冒充单次全仓结果。Ruff、JS语法和diff-check完成。

真实内置浏览器使用17张合成图，9项观察通过：单脸移出变空相册、合照忽略/恢复、前后张、头像/日期同步、无模型文字搜索、详情返回、预览第10→11张跨页、时间排序，以及固定爸爸相册内ANY返回2张/ALL返回1张。截图在工具中实际查看；本会话无支持的文件导出，未伪造截图文件。合成梯度图不证明真实OCR或人脸识别精度；未验原生200%缩放、6GB显卡或上游既有双页面冲突/合集管理余项，这些不计通过。

完整范围与复现环境见任务卡和 `docs/testing.md`。核心回归可运行：

```powershell
$py = '.tmp/runtime-review-20261015/full-env/Scripts/python.exe'
& $py -m pytest -q tests/integration/test_search_text_mode.py tests/integration/test_search_candidate_recheck.py tests/unit/test_search_text_mode_ui.py tests/unit/test_person_preview_navigation_ui.py --basetemp=.pytest-tmp/local-product-repro --junitxml=test-results/local-product-repro.xml
```

全部原始命令/退出码、节点归并、初始失败和浏览器观察留在忽略的 `test-results/peer-search-20261016/`，主要入口为 `local-product-combined-evidence.json`、`text-mode-backend-incremental-execution.json`、`text-mode-300k-idle-comparison.json` 和 `final-browser.json`。30万诊断复用 `.tmp/peer-search-20261016/test_text_mode_300k.py`，环境开关及原库备份/规范化步骤保留在同目录；原合成库未修改，不把历史实验库的修复当作正式迁移验收。这组API数字依赖本机保留的完整合成库和诊断脚本，尚无干净克隆即可运行的完整复现入口；仓库中的回归测试与文字/模型工具不能代替该API对照。源码、回归测试和同一份报告进入变更，权重、图片、数据库和运行日志不进入Git。

### 追加：合集、安装校验与开发效率

- **合集核对**：先解析合集当前对应的人物，再按候选照片ID查人脸和静态归档；不再为检查一页照片生成十几万条完整成员集合。合并链、人工排除、归档版本、来源和可用性继续使用原规则；超过5000候选、全量召回和统计保留原SQL。独审确认默认SQL在8组参数下逐字相同。
- **安装损坏权重**：旧 `prepare-local` 将大于等于4MiB、大小相同的文件直接视为相同，可能永远跳过损坏文件。现在用1MiB分块比较完整内容，内存不随权重大小增长；源即目标时避免自复制。CLIP/Qwen都验证尾字节损坏会修复、完整文件不重复复制、坏源不会覆盖已安装文件。没有改模型身份或索引格式。
- **测试及文档**：同组23项候选测试先生成并关闭模板，再复制每例独立数据库，仍经过正常兼容性检查、事务和全部原断言；模板结束时核验哈希未变。本次JUnit耗时1.479秒，历史同节点30.972秒因负载不同不计算加速比。README补可复制安装/启动步骤和三种搜索的实际前提；测试说明使用通用`.venv`，不再依赖某次本机临时环境。

30万同库API按“旧→新→旧”各运行7场景×5次，只切换合集核对路径。合集首屏热中位旧两轮为1684/1121ms，新790ms；续页旧两轮298/195ms，新3.75ms。未改路径也出现明显系统波动，保留全部结果，只将约1.12→0.79秒作为观测，不宣称精确百分比或响应保证。独立SQL核对20/1500/5000候选分别为181.56→0.07ms、200.12→21.16ms、214.52→49.21ms；所有ID/顺序/候选信息相同，模型/向量/自然语言解析调用为0。仍不含真实图片、网络和浏览器绘制。

新增范围共108个不同测试节点通过（23候选、57合集消费者、28模型提供方，与上一轮有重叠），全部JUnit和真实exit0。旧安装实现的2项失败复现保留，当前实现闭合；全src/tests/tools Ruff、diff及文档示例/链接核对通过。合集SQL/夹具生命周期和模型修复均经独立代理复核；未改UI、依赖或迁移，不重跑未变浏览器/模型推理。直接复验按 `docs/testing.md` 环境使用 `local-polish-verification.json` 中列出8个文件的命令。原始入口为同一忽略目录的 `local-polish-verification.json`、`collection-300k-comparison.json`、`candidate-fixture-efficiency-summary.json`。全范围统计仍需计算；API每请求新建连接，单连接缓存无实际收益，故未引入额外跨连接缓存协议。旧任务PENDING继续保留；PR #6保持关闭，此次按用户新授权另提PR。
