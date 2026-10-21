# P4-WEMM-QUALITY-PILOT — 搜索质量归因与 WeMM 对照

2026-10-20，基线 `900028d`（0.5.0 源码测试版）。**公开数据 PILOT 已完成；正式接入判断 INCONCLUSIVE，暂不执行 Provider 卡。** WeMM 可在本机运行，平均排序有收益，但图片编码中位耗时为Qwen的2.81倍，超过事前两倍参考线；“猫”前50名三者均已100%，不能据此证明解决用户反馈。工具最后一次 I/O 加固的验证与独审见文末。

本轮交付独立评测工具、测试和结论，不注册第三个产品 Provider、不改生产依赖或活动模型。用户已于2026-10-21授权将这些改动提交并推送到实验分支 `codex/upstream-quality-20261020`。模型权重、照片、向量、HTML和原始日志留在本地忽略目录；远端报告包含完整方法、指标、失败说明与复现入口。没有访问私人图库。所有新路径由工作区或命令参数解析，修正 AGENTS.md 中其他开发者的固定目录。

对使用者而言，当前结论是：**搜索质量有提升空间，但尚未证明换WeMM就能解决私人图库的“非猫插队”。** WeMM在这批数据的平均排序更好，建库编码更贵，部分类别也会退步；Qwen1024保留了约75%的2048维平均AP增益，只需两倍原始向量容量，是值得下一步验证的折中。两者都还是候选，产品默认配置没有改变。

## 已同步的产品功能

此前 PR #7 已合入 master。本次快进到最新远端，新增收藏及范围搜索、普通/智能合集与刷新、资产以图搜相似、搜索模型管理/候选建库/停机切换、失效照片清理、OCR原文显示与搜索性能诊断。新库默认 Qwen EXIF v2；版本0.5.0 / Schema42。上游未完成的验收不因本轮同步而转为通过。

## “猫”排序问题：已证实与仍待证实

不能仅凭后排混入其他照片就判定模型出错。当前搜索默认返回有界最相似候选，5000上限不是相关性阈值；弱相关候选也会出现。

1. **融合可以改变视觉排名**：综合搜索把文件基名/OCR/有效地点与向量排名等权RRF融合。当前生产纯函数反例中，向量第100名同时是文字第1名，会超过仅由向量命中的第1名。反例只证明这种机制存在，不证明用户具体照片由此出错。原始结果/JUnit见忽略目录 `fusion-diagnostic*`。
2. **高度匹配只统计，不分段**：Qwen当前参考值为单位向量cosine≥0.25；只统计满足条件的向量候选，不过滤、重排或表示分类概率。融合后这些图片可能不连续。即使另加阈值过滤，也只能隐藏尾部，不能把排靠后的真猫提前。
3. **模型及输入仍可能限制质量**：当前Qwen采用完整图像、最大262144像素预算，原生2048D取前512D并重新归一化。小主体、遮挡、猫图案与真猫混淆可能来自表示能力。没有发现截断后漏归一化、距离排序反向或分页重新洗牌；索引覆盖仍需与实际图库核对。
4. **未发现Qwen位置缓存污染**：锁定Transformers4.57在每次无旧KV输入的调用重新计算rope位置；不能根据WeMM的rope reset补丁推断本项目也有该bug。

## 固定方案与判断边界

seed=20261020；从 COCO train2014 中具备 CC BY / CC BY-SA 许可的21,313张候选选择500张：250张独立随机，其余按猫/狗及混淆类别补足；40张烟测来自同一冻结集合。最终84张标猫、49张标狗。每模型使用同一原图字节和转正RGB，再各自按合法处理器编码；Qwen512/2048来自同次完整输出，WeMM512来自独立完整输出。图片ID、来源、许可、哈希、查询与标签在推理前冻结；失败不替换。

8个类别查询使用 COCO 实例存在性标注；“两只猫、黑色的猫、猫趴在沙发上、人与猫在一起”4个复杂描述只有排名观察，没有伪造精度。公开数据可能已被模型训练使用，是探索性对照，不是独立留出集。全部图片 EXIF orientation=1，最大640×640；不覆盖手机大原图、真实旋转图、HEIC/CR2。没有核对生产向量、活动 Profile 或真实图库覆盖，不能称生产Top50等价。

事前冻结参考：猫P20/P50不退步、8类macro AP提升至少0.02、图像编码中位数不超过Qwen两倍、自身峰值allocated不超过10GiB；猫基线P20/P50≥0.95时承认天花板。混合取舍记 INCONCLUSIVE，不能事后改线。

## 候选模型研究

| 方案 | 判断 |
| --- | --- |
| Qwen3-VL-Embedding-2B 512/2048 | 实际约2.13B参数。增加维度无须加载第二套模型，原始向量存储变为4倍；本轮平均排序提高，仍有少数退步。 |
| WeMM-Embedding-2B 512 | 约2.72B总参数、5.44GB权重；中文有官方支持，是本轮主要候选。512维减少索引成本，不能减少权重显存。 |
| GME-Qwen2-VL-2B | 约2.21B，官方仅英文训练，中文保证不足，暂不优先。 |
| Jina-CLIP-v2 | 865M、多语言、较轻；权重CC-BY-NC4.0，分发用途需考虑许可。 |
| SigLIP2 Base NaFlex | 约375M，更轻量，已有对照；此前配对图结果较弱不代表所有类别均差。 |
| Qwen3-VL-Reranker-2B | 候选逐图精排更针对“非猫插队”，但增加查询时图像推理，不能救回未召回图片；本轮不扩大到另一套模型。 |

一手来源：[WeMM固定代码](https://github.com/Tencent/WeMM-Embedding/tree/e8175bbff1071b580b3a6df8ec2c106e8383a361)、[WeMM固定权重](https://huggingface.co/tencent/WeMM-Embedding-2B/tree/bbd6cd4bf52cfc6716f752a2df80b2706720bd95)、[Qwen](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)、[GME](https://huggingface.co/Alibaba-NLP/gme-Qwen2-VL-2B-Instruct)、[Jina](https://huggingface.co/jinaai/jina-clip-v2)、[SigLIP2](https://huggingface.co/google/siglip2-base-patch16-naflex)、[Qwen精排](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B)。更多固定revision与许可核对留在忽略目录官方研究摘要。

## 实测质量：均分提高，不等于每种需求都提高

下表每格依次是 **P@20 / P@50 / AP（%）**。前两项表示前20/50张中符合该类别标注的比例；AP综合考察所有标注相关图片是否排在前面，越高越好，**不是分类正确率**。狗只有49张、鸟32张、自行车27张、玩具熊23张，P@50的理论上限本来就不足100%。

| 查询（相关图数） | Qwen512 | Qwen2048 | WeMM512 |
| --- | --- | --- | --- |
| 猫（84） | 100 / 100 / 95.45 | 100 / 100 / 97.80 | 100 / 100 / 98.14 |
| 狗（49） | 100 / 70 / 79.84 | 100 / 80 / 86.35 | 95 / 86 / 89.85 |
| 鸟（32） | 85 / 50 / 75.50 | 95 / 54 / 84.08 | 100 / 58 / 92.13 |
| 汽车：COCO car（61） | 100 / 78 / 82.20 | 95 / 86 / 85.19 | 85 / 72 / 70.32 |
| 自行车（27） | 75 / 40 / 72.24 | 80 / 46 / 76.26 | 90 / 48 / 85.69 |
| 人（237） | 95 / 96 / 83.03 | 100 / 98 / 89.69 | 100 / 100 / 94.54 |
| 椅子（64） | 85 / 66 / 63.93 | 85 / 70 / 67.97 | 95 / 68 / 70.92 |
| 玩具熊（23） | 90 / 42 / 89.62 | 90 / 42 / 91.28 | 100 / 42 / 92.94 |

| 8类平均AP，各类等权 | Qwen512 | Qwen2048 | WeMM512 |
| --- | --- | --- | --- |
| 全部500张 | 80.23% | 84.83% | 86.82% |
| 独立随机250张 | 80.91% | 85.21% | 85.01% |
| 按类别补足250张 | 80.99% | 85.12% | 88.79% |

WeMM 相比 Qwen512 全集平均AP增加6.59个百分点，但随机半集没有超过Qwen2048。分层排名是在各自250张内部计算，不能把分层均分简单相加当全体AP。

**猫的中后段确有改善，也有反例。** 首张COCO未标猫的图片出现在第73/77/80名；“前50全对”不等于84张猫全在最前。房间角落小黑猫（140831）由Qwen512第205名升到WeMM第84名，Qwen2048为98；洗脸池中明显的猫（265447）却由第2名落到WeMM第61名，Qwen2048为8。最后一张标猫图（327610）分别在359/472/347名，视觉主体是一辆公交车、猫出现在背景图案，说明“含该类别”与“照片主要拍什么”是不同的评价目标。图示见本地 `test-results/p4-wemm-quality-pilot/visual-examples.jpg`；不据少量看图改写冻结标签。

**汽车的差异不能直接叫模型认错。** 视觉核对发现WeMM前20中按COCO car计为阴性的3张，实际是两张皮卡/卡车和一张校车，中文“汽车”可以包含它们。补做明确的事后敏感性分析，将相关范围放宽为car或truck或bus（76张），固定查询和排名不变：三者P20均100%，P50依次92/96/82%，AP为85.40/88.64/80.05%。词义解释了前20的差异，但放宽后WeMM中后段仍逊于Qwen；此分析没有替换事前主指标。

## 实测资源与运行边界

实际设备为 RTX 5070 Ti 16GB，Python3.12.10；不是材料中的4060 8GB/1660。WeMM模型及remote code固定revision/hash，Apache2.0及第三方声明已核。只执行所需1205字节模型类，不运行SGLang改安装包补丁。Transformers5.2.0安装在独立环境；现有4.57.0与模型目录保持不变。两模型分进程串行，禁止CPU/offload或修改像素预算掩盖OOM。

两模型40张烟测、正式500张均完成，所有文本→图像→同文本重复向量cosine=1、最大差值=0；全部模型参数实际驻留CUDA，WeMM为BF16、Qwen为FP16，无OOM/CPU卸载。WeMM固定HF revision为 `bbd6cd4bf52cfc6716f752a2df80b2706720bd95`，源码为 `e8175bbff1071b580b3a6df8ec2c106e8383a361`；Qwen沿用固定EXIF v2清单，实验2048维身份独立保存。

| 正式500图，本机观察 | Qwen（512/2048共用编码） | WeMM512 |
| --- | --- | --- |
| 图像编码中位 / P95 | 62.60 / 74.53 ms | 175.68 / 197.73 ms |
| 预热后11条不同查询中位 | 30.41 ms | 125.05 ms |
| GPU峰值allocated / reserved | 4.128 / 4.301 GiB | 5.169 / 5.281 GiB |
| 进程RAM峰值 | 8.664 GiB | 5.801 GiB |
| 正式加载观察 | 8.43秒，续跑8.50秒 | 10.22秒 |

编码耗时包括处理器和GPU执行，不含逐图文件读取/hash/统一解码；P95是本轮500图的描述统计，不是重复查询SLA。Qwen正式运行发生一次结果保存PermissionError：首次52.62秒退出2，同源断点恢复13.92秒退出0，合计66.55秒；指标保留原有逐图计时，资源取两次attempt最大。不能把续跑13.92秒当整轮500图耗时，也不能称单次无失败通过。WeMM单次114.17秒退出0。

Qwen首次烟测加载69.33秒、WeMM11.11秒，正式重载已明显不同；系统文件缓存/库初始化未控制，不能宣称严格冷热启动优势。全卡监控开跑前已有约5.3GB桌面/其他进程占用，表中是Torch自身计数，不把全卡用量全归给模型。本机WeMM缺少可选FlashLinearAttention/CausalConv内核时使用原生Torch GPU实现；没有同机A/B，不能把速度差全部归因于这些内核。WeMM合法默认像素预算也高于现有Qwen，结果代表两套部署规格，不是控制所有网络因素的纯架构消融。

离线12查询×500图矩阵乘法经预热、随机交错各30次后，中位数Qwen512/2048/WeMM512约0.077/0.245/0.075ms；不含排序、数据库和HTTP。原单次顺序计时含BLAS初始化，不作方案速度比较。未做30万认证。

## 判断与后续优先级

**INCONCLUSIVE：WeMM有潜力成为可选模型，本轮不足以推进正式Provider，更不足以替换默认Qwen。** 平均AP收益达到事前参考，但编码约2.81倍超过两倍线，猫前排有天花板，车辆定义/部分图片退步说明取舍真实存在。实际16GB卡和≤0.41MP图成功不能推出8GB/GTX1660/手机大原图可用。

这个成本并不意味着WeMM不可用：较重的图像编码主要影响首次建库和新增照片，预热文本中位125ms也不必然无法接受。若愿意以建库时间换512维的小索引，它仍有吸引力；暂缓接入还因为本轮未证明私人问题改善，且类别与分层收益不一致，不能只凭越过一个时间参考线否决模型。

更值得优先做的下一步是：在实际问题图上区分纯视觉排序、文字融合影响和索引覆盖；明确用户希望“含一只小猫也算”还是“主要拍猫”；再对比Qwen2048、小主体区域向量或有界精排。Qwen2048本轮平均AP增加4.60个百分点，编码无需新模型，但30万float32原始向量由约0.614GB变为2.458GB（不含索引开销），且旧512向量无法还原丢掉的维度，仍需候选空间重算，不能原地改维度。区域向量可能补小主体，代价是额外编码/存储；精排只重看候选照片，能缓解插队，但增加查询延迟，救不回未召回图片。本轮没有把这些未测方案宣称为已优化。

**补充的1024维折中实验（事后探索）**：只增加一个中间档，从已保存的Qwen完整输出截前1024再归一化，不重跑GPU、不遍历多个维度挑最好，也不改事前主比较。独立CPU执行退出0并保留JUnit：

| Qwen维度 | 全500平均AP | 随机250平均AP | 定向250平均AP | 30万原始float32向量算术容量 |
| --- | --- | --- | --- | --- |
| 512 | 80.23% | 80.91% | 80.99% | 0.614GB |
| 1024 | 83.68% | 83.67% | 84.35% | 1.229GB |
| 2048 | 84.83% | 85.21% | 85.12% | 2.458GB |

1024在全集保留约75%的2048维平均AP增益，只需两倍原始向量容量，是更温和的后续候选。它并非处处更好：全集平均P20/P50分别95.63/70.00%，2048为93.13/72.00%；相对512，1024的汽车P20降5个百分点，随机子集猫AP也从1降至0.9909。整体排名和前排偏好有取舍，不能在同集事后选档后宣称已找到普遍最佳默认值；也没有因此修改生产空间或活动模型。

## 复现与验证

运行入口 `python -m tools.evaluation.wemm_quality_pilot {preflight,smoke,encode,compare,report}`；数据入口 `python -m tools.evaluation.prepare_wemm_coco --out-dir .tmp/wemm-pilot/datasets`。所有输出限项目忽略目录、照片输入只读；工具无启动时隐式下载。WeMM模型按 `tools/evaluation/wemm_model_lock.json` 的revision、文件集合和SHA准备，不能混入可变main。隔离环境是Python3.12.10、torch2.8.0+cu128、torchvision0.23.0+cu128、transformers5.2.0、qwen-vl-utils0.0.14；Qwen使用原4.57.0环境。完整freeze和GPU wheel URL/hash保存在 `.tmp/wemm-pilot/`，不改生产lock。

本次两个解释器分别为 `.tmp/runtime-review-20261015/full-env/Scripts/python.exe` 和 `.tmp/wemm-pilot/env/Scripts/python.exe`；这是本机证据路径，不是工具硬编码。下面在仓库根目录运行，`$qwenPython` / `$wemmPython` 指向上述相应环境，另选新的 `$run` 可避免覆盖历史失败：

```powershell
$env:PYTHONPATH = "$PWD;$PWD/src"
$env:UV_CACHE_DIR = "$PWD/.uv-cache"
$run = '.tmp/wemm-pilot/runs'
& $qwenPython -m tools.evaluation.wemm_quality_pilot preflight --out-dir "$run/preflight" --qwen-model-dir PhotoSearchData/models/qwen3_vl_embedding --wemm-model-dir .tmp/wemm-pilot/models/WeMM-Embedding-2B
& $wemmPython -m tools.evaluation.wemm_quality_pilot smoke --out-dir "$run/smoke" --sample-file .tmp/wemm-pilot/datasets/smoke40.json --queries .tmp/wemm-pilot/datasets/queries.json --model wemm --wemm-model-dir .tmp/wemm-pilot/models/WeMM-Embedding-2B
& $qwenPython -m tools.evaluation.wemm_quality_pilot smoke --out-dir "$run/smoke" --sample-file .tmp/wemm-pilot/datasets/smoke40.json --queries .tmp/wemm-pilot/datasets/queries.json --model qwen --qwen-model-dir PhotoSearchData/models/qwen3_vl_embedding
& $wemmPython -m tools.evaluation.wemm_quality_pilot encode --out-dir "$run/full" --sample-file .tmp/wemm-pilot/datasets/sample.json --queries .tmp/wemm-pilot/datasets/queries.json --model wemm --wemm-model-dir .tmp/wemm-pilot/models/WeMM-Embedding-2B --smoke-run-dir "$run/smoke/wemm"
& $qwenPython -m tools.evaluation.wemm_quality_pilot encode --out-dir "$run/full" --sample-file .tmp/wemm-pilot/datasets/sample.json --queries .tmp/wemm-pilot/datasets/queries.json --model qwen --qwen-model-dir PhotoSearchData/models/qwen3_vl_embedding --smoke-run-dir "$run/smoke/qwen"
& $qwenPython -m tools.evaluation.wemm_quality_pilot compare --out-dir "$run/full" --labels .tmp/wemm-pilot/datasets/labels.json
& $qwenPython -m tools.evaluation.wemm_quality_pilot report --out-dir "$run/full" --sample-file .tmp/wemm-pilot/datasets/sample.json --max-previews 500
```

每个GPU命令必须检查实际退出码再执行下步，不能因为无输出认为成功。新I/O补丁会改变源码身份，未来encode须用新目录重做对应smoke；现有向量保留原编码源码身份。HTML在 `.tmp/wemm-pilot/runs/full/report.html`，12查询并排Top20/50，共369张去重预览，0读取失败、0缺预览。

独立CPU实现从原始.npy重新检查哈希、单位长度、同图RGB、完整资产/查询身份，并重建三种排名和所有类别/分层指标，实际退出0；只有WeMM“人”查询两个深部近同分位置因float64/float32发生互换（差1.73e-7），Top20/50集合和已报告指标一致。原始JSON、执行退出码、JUnit、GPU监控、下载中断和两张图片重下记录均在忽略的 `.tmp/wemm-pilot/` / `test-results/p4-wemm-quality-pilot/`，不进入Git。未更换失败样本、未改标签迎合模型。

定向工具测试采用**基线＋增量证据**：最终核心/CLI 51项（包括真实Windows拒绝delete-share句柄的测试）、catalog 30项、准备器17项JUnit通过，无失败/错误/skip；Ruff与diff-check通过，独立受影响面复核闭合。原GPU编码CLI SHA为 `81f29bde…`，I/O加固后为 `135e8b2f…`，精确差异与副本可校验：只对Windows短暂文件替换占用作最多6次、累计0.93秒重试，并保留数值错误码和跨恢复失败历史，不更改ACL、模型输入、排序或指标。最初PermissionError未记录winerror，不能确定就是监控读文件导致；真实占用测试验证了该类故障修复。

这是冻结基线GPU＋独立CPU复算＋I/O定向增量，**不是最终源码的一次完整GPU/全仓全绿**。只改隔离测量工具，不触发产品全仓回归或真实应用浏览器验收；不以这些测试代替生产模型接入、私图库质量和小显存设备验证。
