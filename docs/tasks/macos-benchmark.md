# macOS 运行与公开数据集实测

2026-10-01 完成当前 Apple M4 / 16GB、macOS 27.0、Python 3.12.10 的源码运行适配与真实数据测试。用户授权下载公开 benchmark 和必要模型权重；未扫描私人图库，未比较其他模型。

## 实现

- macOS 安装 ONNX Runtime CPU 包，图文通过 PyTorch 2.14.0 / MPS FP16 运行；Windows 保留 CUDA。显式指定的 GPU 不可用时失败，不静默降级。
- 任务协议仍使用 gpu/cpu，实际 provider 记录 torch-mps/torch-cuda；能力快照、资格证据、查询和建库均识别 MPS。不修改数据库设备枚举。
- 数据默认位于 `~/Library/Application Support/PhotoSearchData`。模型、benchmark 和索引在该目录中，依赖位于项目 `.venv`，Python 解释器位于项目 `.python`；pytest 仅清理 `.pytest-tmp/tests`。
- 按官方 FG-CLIP 2 接口为短查询使用 short/64 分支、长描述使用 long/196 分支；manifest/空间身份同步更新，当前测试索引均已按新身份建立。

## 检索结果

每个数据集使用自己的 1,000 张候选图片，R@K 表示人工标注的目标图片排入前 K 的查询比例；没有用测试集调参数。

| 数据集 | 查询数 | R@1 | R@5 | R@10 | 本轮建库耗时 |
|---|---:|---:|---:|---:|---:|
| COCO-CN 官方 test，中文人工撰写 | 1,053 | 65.34% | 88.51% | 95.35% | 103.72 秒，首次 1,000 张 |
| Flickr8k 镜像 test，英文人工描述 | 5,000 | 78.04% | 94.34% | 97.60% | 70.11 秒，新增 1,000 张 |

模型图像编码平均分别为 87.69 / 60.67 毫秒；模型加载分别为 14.06 / 8.90 秒。真实产品链路经过扫描、调度、产物提交、LanceDB、搜索 API；两次各抽样 30 条查询，热查询 P50/P95 为 108/220 毫秒（1,000 个资产）和 79/131 毫秒（2,000 个资产），均无 HTTP 失败；两次以图搜图原图均排第一。API 抽样与全量模型 Recall 分开记录，API 时间不包含浏览器渲染。

模型进程最高 RSS 约 2.39GiB；MPS 驱动分配快照约 1.02GiB，后者不是峰值，两项不能直接相加。基准在日常桌面环境运行，下载及短回归检查有部分重叠，不作为隔离实验室性能。

额外实测：正常启动入口的网页、能力、真实 TCP 搜索均 HTTP 200；首次查询约 1.93 秒；会话文件权限 0600。OCR 在 CPU 上正确识别 `ORDER 123456`；人脸 CPU 流程在一张公开测试照片上检测并编码 4 张脸。它们仅验证运行，不代表 OCR 或人脸准确率评测。

## 来源与复现

- [COCO-CN 官方项目](https://github.com/li-xirong/coco-cn)，官方指向的 [AIMClab 数据](https://huggingface.co/datasets/AIMClab/COCO-CN)，revision `d40a7aac6fdfa88f5e30c0cd89516feaa8014739`。图片来自 COCO 官方 S3 存储。
- [Flickr8k 数据镜像](https://huggingface.co/datasets/intro/flickr8k)，revision `c7a02cb487da0eca729e133194b0f842cb578c08`。保留镜像 test 划分，每张 5 条描述。
- 数据集清单包含每张图片的 SHA-256。原始成绩及逐查询结果位于应用数据目录 `benchmarks/results/`，两次成绩文件为 `report-coco-cn.json`、`report-flickr8k.json`；产品图库为 `benchmarks/app/`。

```sh
UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv sync --all-groups
uv run python tools/prepare_models/prepare_models.py
uv run python tools/evaluation/download_benchmarks.py
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python tools/evaluation/retrieval_benchmark.py --datasets coco-cn --device mps
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run python tools/evaluation/retrieval_benchmark.py --datasets flickr8k --device mps
```

索引和图片编码记录支持续跑；已有索引时重跑不会得到首次建库时间，成绩会记录缓存数量。下载时曾因磁盘不足中断 OCR；清理本任务创建的临时依赖和 uv 缓存后完成，未删除个人文件。

针对性回归 **222 passed，13.76 秒**；[JUnit](../../test-results/macos-runtime.xml)。执行 `.venv/bin/python -m pytest`，选取 `test_macos_runtime`、`test_search_repair`、`test_p4_02o_semantic_build`、`test_p2_search_api`、`test_p4_02n_query_encoder_wiring`、`test_p3_ocr_contract`、`test_p3_ocr_geometry`、`test_p4_02y_library_refresh`、`test_p2_search_upload`、`test_p4_02t_upload_formats`、`test_p4_02g_a3_contracts`、`test_runtime_device`、`test_p3_face_contract`；参数 `-q -o addopts='' --basetemp=.pytest-tmp/macos-tests --junitxml=test-results/macos-runtime.xml`。Ruff、`git diff --check`、`uv sync --locked --offline --all-groups` 均通过。Python 移至稳定目录后，设备测试再次 **9 passed**，入口 `photo-search --help` 正常；[环境迁移 JUnit](../../test-results/macos-final-environment.xml)。

## 边界与接续

本轮共有 2,000 张真实图片，不代表十万/三十万图库验收；公开数据可能被模型预训练使用，成绩不是对私人图库的保证。未制作 macOS 安装包，未重新验收 Windows/GTX 1660，也未完成仓库规定的独立工程审查。

后续读取：AGENTS → 开发状态 → 本卡 → ADR-0001/0003、设备/worker/建库受影响实现。独立审查需覆盖 provider 与逻辑设备映射、空间身份及本文证据；无需重新开展模型竞赛或复制本机数据到仓库。
