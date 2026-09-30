# 搜索可靠性与模型接入修复

2026-10-01：代码实施完成，本机针对性验证通过；目标硬件和独立工程审查尚未完成，不标正式验收 PASS。用户授权直接选型、删除替代实现，不做模型对比或大型基准。

## 已落地

- 图文固定 FG-CLIP 2 Base 768D；PyTorch CUDA FP16、显式 CPU FP32。模型代码、权重、tokenizer 和预处理均进入空间身份；文件校验后离线加载，空间变化需重建索引。
- OCR 固定 RapidOCR 3.9.2 / PP-OCRv6 small。统一模型安装入口；Windows torch/torchvision 使用官方 CUDA 13.0 源，更新依赖锁及打包入口。
- worker 全部请求共用交换锁，进程退出及时报错、下一次请求可重启；重启后仍核对空间。初始化失败允许重试。
- 搜索与能力检查移出 API 事件循环，队列有界，SQLite 连接在线程内创建和关闭。
- 建库 SQL 分页，修复重复照片跨来源选择；人脸/OCR 选择在 SQL 内限量。后台取消在选择和任务边界生效。
- Lance 连接/表句柄复用并更新版本；分块召回只保留所需 Top-K。上传与图库统一图片方向处理，OCR 命中提供说明。
- 删除被替代模型适配器、导出工具、失效评测脚本及重复审查文档；保留一个安装入口、当前任务卡和必要诊断。

## 验证

macOS ARM64，Python 3.12.10；仅使用合成夹具，未扫描真实图库、未下载预训练权重。

```sh
PYTHONPATH=src:.pytest-tmp/audit-packages .pytest-tmp/audit-python/cpython-3.12.10-macos-aarch64-none/bin/python3.12 -m pytest tests/unit/test_search_repair.py tests/unit/test_p4_02o_semantic_build.py tests/integration/test_p2_search_api.py tests/unit/test_p4_02n_query_encoder_wiring.py tests/unit/test_p3_ocr_contract.py tests/unit/test_p3_ocr_geometry.py tests/integration/test_p4_02y_library_refresh.py tests/unit/test_p2_search_upload.py tests/unit/test_p4_02t_upload_formats.py tests/unit/test_p4_02g_a3_contracts.py tests/integration/test_ai_worker_lifecycle.py tests/unit/test_runtime_device.py tests/unit/test_p3_face_contract.py -q -o addopts='' --basetemp=.pytest-tmp/search-repair-tests --junitxml=test-results/search-repair-core.xml
```

结果：**215 passed，5 skipped，5.85 秒**。跳过项需要真实模型或 CUDA；[JUnit](../../test-results/search-repair-core.xml)。修改的 56 个 Python 文件语法检查和 Ruff 通过，`git diff --check` 通过；`UV_CACHE_DIR=.uv-cache uv lock --check --python .pytest-tmp/audit-python/cpython-3.12.10-macos-aarch64-none/bin/python3.12` 通过；主程序及模型安装器 `--help` 通过。

另检查了固定版本的官方 FG-CLIP 2 预处理器、微型随机参数实例的图像/文本接口，以及 RapidOCR 配置解析；这些只证明接口兼容，不代表真实模型精度或性能。

## 下一步与边界

在获得模型下载及图库使用授权的 Windows NVIDIA 机器上，按 README 安装权重并重建图文索引，验证真实编码、搜索和打包启动。GTX 1660 6GB、十万/三十万图库吞吐及检索准确率未实测；本机测试不替代这些证据。依仓库规则，本轮契约/状态改动仍需独立 DS-H 受影响面工程审查，不能以本轮自检替代。

接续阅读：`AGENTS.md` → `docs/development-status.md` → 本卡；主架构 §3/5/6/7/8/9/11.3、执行策略 §2.4/3/5，以及受影响实现与测试。不重新开展模型对比，不新增评测平台。
