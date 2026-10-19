# P4-EMBEDDING-DEFAULT-QWEN-A 执行报告

## 目的

让新图库在没有活动 SearchProfile 时默认使用普通版 `qwen3-vl-embedding-2b`。活动 Profile 继续是现有图库的唯一模型路由依据。普通首次建库遇到 SQLite generation 缺失但确定物理 Lance 表存在时拒绝写入并指向已有维护隔离流程。

## 改动

- `src/photo_search/models/embedding/registry.py`：默认模型改为普通 Qwen provider；没有 provider 描述时返回不可用，不回退 Chinese-CLIP。
- `src/photo_search/refresh/pipeline.py`：首次建库缺失模型配置/权重或模型加载失败时，在分类/入队之前报告 FAILED 和原因；日常增量刷新原行为不变。
- `src/photo_search/indexing/semantic.py` 与 `src/photo_search/refresh/pipeline.py`：首次建库在分类/入队/source-built 标记前检查 Lance，实际建索引写入前再复核；发现无 SQLite generation 的物理表或 Lance 状态无法核实时停止。
- `src/photo_search/runtime/model_resource_gate.py`：门禁代理补齐建库调用所需的单图 `encode_image()`。
- 定向测试覆盖默认普通 Qwen、已有 CLIP 活动 Profile 保持权威、缺权重/CUDA 错误、孤儿表拒绝、正常首次建库 Profile 激活，以及候选模型不自动激活。

## 验证

- `tests/unit/test_p4_embedding_provider.py`、`test_p4_02o_semantic_build.py`、`test_p4_embedding_default_build.py`、`test_p4_02n_query_encoder_wiring.py` 与普通首次建库集成用例：**58 passed**。模型工厂返回空 encoder 的 CUDA/运行时失败分支也明确返回建库失败。
- `tests/unit/test_runtime_device.py -k 'qwen or strict'`：**1 passed**。
- 受影响 Python 文件 Ruff：通过。
- `git diff --check`：代码改动通过；测试文件原有 CRLF 行尾会被 git 默认检查误报，按 `core.whitespace=cr-at-eol` 检查后通过。
- 首次集成验证发现门禁包装器缺少单图编码代理；实现 `GatedEncoder.encode_image()` 后完整重跑通过。
- Luna High 复核指出孤儿表检查和 encoder factory 检查都必须发生在分类/入队之前。已前移两项检查；新增断言确认孤儿表、缺模型、CUDA 失败及 encoder 返回 None 时 jobs、outbox 和 source-built marker 均不变。最终跟进复核确认顺序问题闭合，错误状态与 Profile 不变量一致。

## 验收边界

未真实加载 Qwen 权重或 CUDA 建库，因此真实模型闭环仍 PENDING。未触及 `D:\SZ\project\PhotoSearchData`，未进行 30 万图库测试。原始实现已提交 `176d189`（未推送）；本次 EXIF v2 follow-up 尚未提交。

## 2026-10-20 默认版本调整

用户随后指定新图库默认使用 `qwen3-vl-embedding-2b-exif-v2`。已将 `EmbeddingRegistry.default()` 切换到 EXIF v2，并更新启动接线测试；已有活动 Profile 仍优先，CLIP 活动 Profile 回归检查通过。普通版与 EXIF v2 manifest 指向相同的 `models/qwen3_vl_embedding` 权重目录。

最小定向验证 4/4 通过，JUnit 保存于 `test-results/p4_embedding_default_qwen_exif-v2-junit.xml`。真实 Qwen/CUDA 未运行；本次 follow-up 尚未提交。
