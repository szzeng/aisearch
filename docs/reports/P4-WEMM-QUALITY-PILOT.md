# P4-WEMM-QUALITY-PILOT — A 阶段预检与合成 GPU Smoke

日期：2026-10-20（本地）
状态：**PAUSED（2026-10-22，按用户指示）**。A 阶段合成 GPU smoke 已完成：固定 revision 权重已核对完整 SHA-256，隔离 Python 3.12.10 环境准备完成；BF16 模型在 cuda:0 完整驻留，并通过一段中文及一张合成 JPEG 的 image-only smoke。仅合成输入；未读取真实照片、未进行质量比较。B 真实照片对照未开始，C 未启动；当前无实验在运行。

本轮实现基线为 `900028dd984f0f9ad74cbff2fb036e23e87d39b8`（已用 `git rev-parse HEAD` 复核）。开工时工作树仅有其他任务遗留的未跟踪 `.pytest-tmp-*` 目录；全部保留。

## 预检结论

项目绑定的数据根存在，活动 Profile 指向 `qwen3-vl-embedding-2b-exif-v2@9f2f7e710d6d` 的 512D READY generation；活动空间为 `qwen3-vl-embedding-2b-exif-v2:e32b91ab5b58c4ea`。SQLite 只读查询得到 4,320 assets 与该 generation 的 4,319 index entries。该差额仅为快照事实，本轮没有调查或改变缺项。Qwen 所需的三个命名模型文件可见，但本次只读取文件 stat，没有校验其内容或本机推理状态。

当前活动身份不是 WeMM。WeMM 8 个官方推理路径必需文件在开工时未于给定活动数据根的预期模型目录发现；授权下载后权重已存放于项目忽略的独立实验目录。

GPU 快照为 NVIDIA GeForce RTX 4060 Laptop GPU，8,188 MiB 总显存、3,351 MiB 空闲显存，驱动 617.42。官方模型权重远端大小为 5,441,695,216 bytes（约 5.07 GiB），单权重文件就超过本次空闲显存。即使按 8 GiB 总量扣除该文件，理论剩余也仅约 2.9 GiB，尚未计模型运行时、图像输入和临时张量；官方资料没有给出本卡的最低 VRAM 需求。该初始快照不满足权重驻留条件，因此停止并等待用户正常释放资源；没有结束任何进程。随后在释放后的独立窗口完成 BF16 模型驻留及合成 smoke。实测的 smoke 峰值见 A 运行记录；这不代表真实照片质量，也不能外推其他显卡或 B 批量运行。

当前仓库所在卷可用空间约 94.7 GiB。模型权重与 tokenizer 合计约 5.46 GB；为权重、隔离 Python 环境、Torch/Transformers 依赖、缓存及中间结果预留 15–20 GB 总空间是保守估算，不是已下载后的实测占用。

SQLite 预检使用 `mode=ro`、`PRAGMA query_only=ON`，从不调用 `Catalog.open()`。若 WAL 存在但 SHM 缺失则拒绝读取；若 WAL 存在则以普通只读连接读取 WAL，不使用 `immutable=1`；无 WAL 时才采用 `immutable=1`。活动目录的 DB/WAL/SHM 文件大小和修改时间在短查询前后相同。该短时 stat 对照不能排除外部并发 writer 在查询区间之外发生变化，因此只支持本次身份快照，不构成全库静止或无并发写的证明。没有扫描全库内容、复制向量或访问图片。

## 官方实现身份与静态审查

从[官方模型 API](https://huggingface.co/tencent/WeMM-Embedding-2B/tree/bbd6cd4bf52cfc6716f752a2df80b2706720bd95)固定的 Hugging Face revision 为 `bbd6cd4bf52cfc6716f752a2df80b2706720bd95`；官方[仓库代码](https://github.com/Tencent/WeMM-Embedding/tree/e8175bbff1071b580b3a6df8ec2c106e8383a361) 在查询时解析到 commit `e8175bbff1071b580b3a6df8ec2c106e8383a361`。模型仓库 metadata 声明 `Apache-2.0`。`model.safetensors` 的远端 LFS SHA-256 为 `e1a1ad752808c26965aa97d37bf4a9bca71d838513f41e83790cb1e71cac6e59`，大小 5,441,695,216 bytes；tokenizer 远端 LFS SHA-256 为 `40e444c744512f423da4c8443c47c21e22ff76056ba4e9796a81c04c13a9daf0`，大小 19,990,378 bytes。用户已授权下载。固定 revision 的 9 个官方小文件下载并核对；tokenizer SHA-256 与远端 LFS OID 匹配。5,441,695,216-byte 权重已从固定 revision 官方 HTTPS 端点下载到 `.tmp/wemm-pilot/weights/model.safetensors`；本地实际大小准确匹配，完整 SHA-256 `e1a1ad752808c26965aa97d37bf4a9bca71d838513f41e83790cb1e71cac6e59` 与远端 LFS OID 匹配。tokenizer SHA-256 `40e444c744512f423da4c8443c47c21e22ff76056ba4e9796a81c04c13a9daf0` 也已匹配。下载与散列记录在忽略目录 `download-verification.json`。curl 展示传输到100%，本次会话结束时未保留可读取的进程 exit 字段；另一个同 revision 1-byte HTTP Range 探针返回206且 curl exit 0。完整字节数及 SHA-256 核验作为实际模型文件完整性证据。

只读下载并审查了固定 revision 的 README、LICENSE、配置、模板和两个 Python 文件；文件 SHA-256 与 HF revision 的 Git blob 身份收在忽略目录 `.tmp/wemm-pilot/official-manifest-r1.json`。官方说明的直接 Transformers 路径使用 `transformers==5.2.0`、`qwen-vl-utils==0.0.14`、`AutoProcessor`/`AutoModel`、`trust_remote_code=True` 与 `model.embedding(**inputs)`，示例使用 BF16 并将完整模型移到 CUDA。512D 应从同一次 2048D 输出截取前缀后 L2 normalize。官方 embedding chat template 以 `<embedding>` 结尾；远程 model class 对 `last_hidden_state` 按 attention mask 取末有效位置并归一化。静态检查的核心代码未见网络请求、子进程调用或文件写入；`trust_remote_code` 仍代表执行仓库 Python 代码；固定 revision 源码已静态审查并在 CPU 阶段导入类定义；其后已在隔离环境实例化模型、加载本地权重并执行有限 GPU forward。GPU smoke 检查了 raw output dtype/device、末有效 embedding token、向量有限性、归一化前缀与重复稳定性；不覆盖完整运行时安全审计或真实质量。

## 工具与验证

新增的离线入口目前只实现 `preflight`。它只 stat 指定顶层模型文件，不递归哈希权重；输出明确标注 `integrity_checked=false`、`runtime_tested=false`、`inference_started=false`。写出结果限制在项目忽略目录 `.tmp/wemm-pilot/`，拒绝输出目录中的 symlink/junction，并以独占创建方式拒绝覆盖已有 `preflight.json`。`smoke`、`compare`、`report` 子命令尚未实现；不将预检代码称为模型运行或质量评估。

本轮验证结果：定向 pytest **8 passed，exit 0**（JUnit：`test-results/p4-wemm-quality-pilot/preflight-junit-r4.xml`）；Ruff **exit 0**；真实目录 preflight CLI **exit 0 / PRECHECK_ONLY**，原始快照在 `.tmp/wemm-pilot/preflight-r4/preflight.json`；`git diff --check` **exit 0**。预检 CLI 原始命令的真实数据路径只留在本机执行记录，本文不保存该路径。

可复现命令（`$dataRoot` 在本机设置为本次已授权的数据根；勿把其值复制到共享材料）：

```powershell
$testPython = "$PWD\.venv\Scripts\python.exe"
& $testPython tools/evaluation/wemm_quality_pilot.py preflight --data-root $dataRoot --out-dir ".tmp/wemm-pilot/preflight-next" --max-assets 50 --seed 20261020 --device cuda:0
# JUnit 输出路径与 basetemp 每轮使用新的名称，且测试输出为真实执行结果。
$env:PYTHONPATH = (Get-Location).Path
& $testPython -m pytest -q tests/unit/test_wemm_quality_pilot.py `
  --junitxml="test-results/p4-wemm-quality-pilot/preflight-junit-NEXT.xml" `
  --basetemp=".pytest-tmp/wemm-quality-pilot-preflight-NEXT"
& $testPython -m ruff check tools/evaluation/wemm_quality_pilot.py tests/unit/test_wemm_quality_pilot.py
git diff --check
```

## 边界与后续门禁

用户已授权固定 revision 的 WeMM 权重与实验环境。实验依赖已装入 `.tmp/wemm-pilot/venv`，Python 3.12.10；torch 2.8.0+cu128、torchvision 0.23.0+cu128、transformers 5.2.0、qwen-vl-utils 0.0.14 均从独立 venv 导入。CPU 环境检查 `environment-check.json` 记录初始化前 `torch.cuda.is_initialized=false`；GPU smoke JSON 记录实际 CUDA 使用。`environment-freeze.txt` 记录已解析包版本。`UV_CACHE_DIR`、`TEMP`、`TMP`、`HF_HOME`、`HF_HUB_CACHE`、`HF_MODULES_CACHE` 均显式位于项目忽略目录；离线导入检查设置 HF offline 开关。未改生产 `pyproject.toml`、`uv.lock` 或 `.venv`。权重已完成 SHA-256 核验。A 的实际 BF16 模型运行与 smoke 已通过；B 仍须等待 R3 独立证据检查和主代理明确放行，并在读取真实图片前再次核对资源与只读边界。B 及资源评估完成后才讨论 C，绝不自动推进到 Provider。

尚未验证：FP16、真实图片 EXIF 与解码、Qwen/WeMM 同图向量对照、真实查询排名、HTML 报告、质量标签、批量性能可接受性、GTX 1660 及完整 B/C 试验。A 仅证明指定 revision 在本机 RTX 4060 Laptop GPU 的 BF16 合成文本/图像 smoke 可运行；不代表质量通过。无独立标签即使后续取得排序，也不能给出 precision 或 GO。

## 独立复核

Luna High 对本阶段完整改动面（只读 preflight 工具、合成安全测试、报告、任务卡与状态摘要）做了独立有界复核，核对 mode=ro/WAL 与缺失 SHM 拒绝、DB/WAL/SHM 短时 stat、输出目录和 JSON leaf 的 symlink/junction 拒绝及独占创建、固定官方 revision 与 9 个本地官方小文件 SHA-256/HF Git blob 身份、远端权重 LFS 元数据及当时未下载状态（限于初轮复核时点）、JUnit 和 blocked 口径。复核中指出的输出文件覆盖风险及术语容易把 named-file stat 误写成完整性/运行就绪问题均已修正，跟进复核无剩余 blocker。由于改动仅为隔离的测量预检工具/测试/文档，没有生产调用链、schema、依赖或产品行为变化，不触发完整产品面升级；本复核不替代未来权重下载后的模型 smoke、B/C 试验及其质量结论。

## 授权后准备与 A 阶段 GPU smoke

用户授权后建立独立 Python 3.12.10 环境，安装 torch 2.8.0+cu128、torchvision 0.23.0+cu128、transformers 5.2.0、qwen-vl-utils 0.0.14。关键模块的 `__file__` 均在 `.tmp/wemm-pilot/venv`，`torch.version.cuda=12.8`，CPU 导入检查记录 `torch.cuda.is_initialized=false`；没有加载模型。完整冻结清单和环境变量/下载命令记录在忽略目录 `environment-freeze.txt`、`environment-check.json`、`preparation-commands.md`。Tokenizer 与权重均已下载并匹配固定远端 LFS SHA-256，权重大小准确为 5,441,695,216 bytes。HF Hub SDK 的 Xet 首次尝试停滞，保留其 partial 后只终止了本任务自己的下载会话，再启用官方 HTTPS 路径；用户进程未受影响。

使用内存合成 RGB 图（256×192）完成 CPU 预处理流程检查：固定版本 AutoProcessor 载入本地 processor/tokenizer，`qwen_vl_utils.process_vision_info` 使用官方 patch size 16；输出 `pixel_values=[280,1536]`、`image_grid_thw=[[1,14,20]]`。CPU 预处理检查本身未实例化模型；其后另行通过 GPU smoke 加载模型与权重。原始记录在忽略目录 `processor-synthetic-check-r2.json`。最终 token 核验显示文本 input_ids 为 11 token、图像为 76 token，末有效 ID 均是 248077；图像为 `pixel_values=[280,1536]` 与 `image_grid_thw=[[1,14,20]]`。

默认模板文本虽以 `<|im_end|>` 结束，本地 tokenizer 的 `TemplateProcessing` 会在单序列后处理自动追加专用 token id 248077。固定 revision 的文本与合成图 CPU 输入中，attention mask 最后有效 token 均为 `<embedding>`，且没有重复；官方 Transformers 示例无需手工追加 token 或切换 vLLM 专用模板。详细摘要在忽略目录 `template-comparison.json` 和 `tokenizer-postprocessor-check.json`。CPU 输入/视觉预处理检查单独不代表模型运行；后续 R3 GPU smoke 已通过。

用户最初通知 GPU 空闲后的资源复核仍不足以装载权重；用户正常退出 PhotoSearch 后再次复核显存满足模型加载，完成了合成 GPU smoke。期间没有真实照片处理或质量对照。


用户通知 GPU 空闲后的只读资源复核（2026-10-20）：nvidia-smi 显示 cuda:0 RTX 4060 Laptop GPU 8,188 MiB total / 4,606 MiB used / 3,351 MiB free，driver 617.42；Compute Apps 列表有 3 个进程，单进程显存为 N/A。单权重文件 5,441,695,216 bytes（约 5,189 MiB）已超过当前空闲显存，因此没有尝试 CUDA 模型加载或 smoke，也没有停止用户进程。脱敏进程链查询显示两个 Python 进程属于 `photo_search` 服务及 `multiprocessing.spawn` 子进程，稍后复查时相关 PID 已不存在；没有自动重试 GPU。脱敏快照位于忽略目录 gpu-smoke-gate.json。用户正常退出相关应用后，最新只读复核为 7,877 MiB free；其后 A smoke 已执行。



## A 阶段 GPU smoke（R1/R2/R3）

初次 R1 尝试因本任务 Windows 内存采样 helper 的 ctypes HANDLE 错误，在模型加载前失败；当时 CUDA 已初始化。修正采样 helper 后，R2 文本与合成图运行成功，但图像消息误含额外中文指令，因此只保留为 image+text runtime smoke，不将其作为官方 image-only 语义证据。R2 原始 JSON 保留。

R3 使用独立脚本 `.tmp/wemm-pilot/run_gpu_smoke_a_r3_image_only.py` 与输出 `.tmp/wemm-pilot/smoke-a-gpu-bf16-r3-image-only.json`，exit 0。运行前 `nvidia-smi` 为 RTX 4060 Laptop GPU、8,188 MiB 总量、80 MiB 已用、7,877 MiB free；无旧 PhotoSearch worker。模型只从本地固定 revision 文件加载，HF/Transformers offline；全参数在 `cuda:0` 且 BF16，无 offload。输入是一段中文和一张严格 image-only 的 256×192 RGB JPEG，SHA-256 `a3c5a8a5601f6e1a1a1d461e50cc87f7fabe52b3e86900ca5f45c69169a5930b`，1,398 bytes。保持官方 processor 与 `qwen_vl_utils` 流程，patch shape `[280,1536]`，grid `[[1,14,20]]`。

文本及图像最终有效 token 均为 `<embedding>` id 248077。三次输出 full 2048 均有限，raw dtype/device `torch.bfloat16` / `cuda:0`；full 向量 L2 norm 文本 1.0002453、图像 1.0018910。由同一 full 输出截取的 512 前缀转 fp32 后归一化，norm 均为 1.0；三次间 2048 与 512 最大绝对差均为 0。image-only forward-only 秒数为 0.2392、0.1547、0.1525；H2D+forward+D2H 分别 0.2400、0.1554、0.1532。文本 forward-only 为 0.5058、0.1393、0.1473。image-only 首次调用峰值 allocated/reserved 为 5,491,527,168 / 5,607,784,448 bytes；进程峰值 working set 6,209,163,264 bytes，private bytes 8,844,566,528。`load_cpu_seconds` 和 `.to(cuda)` 阶段计时不是冷启动时间；R2/R3 单次 forward 计时也只称 forward-only。Transformers 使用 fast processor 默认警告和 optional fast kernel 缺少后回退 Torch 的提示均记录在运行输出；本次能完成推理。

该 A smoke 为合成输入运行性检查，不是质量评估。当前尚无真实样本向量/排名。B 的真实照片阶段等待独立审查核对 R3 输入与身份字段并由主代理放行。
