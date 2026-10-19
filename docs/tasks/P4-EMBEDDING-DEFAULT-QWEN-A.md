# P4-EMBEDDING-DEFAULT-QWEN-A — 新图库首次建库默认 Qwen

**日期**：2026-10-20
**基线**：`c6f4e8125d0bc3c3101f6e45d6ac0ea4522c8c6a`；执行前 fetch 后与 `origin/master` 相同
**状态**：实现完成，定向验证完成；Luna High 独立复核通过
**范围**：无活动 SearchProfile 的普通首次建库选择 `qwen3-vl-embedding-2b`；已有库继续严格按活动 Profile 路由；阻止无 generation 的旧 Lance 表被普通建库复用；保留候选建库不自动激活。

## 实施与不变量

- `EmbeddingRegistry.default()` 只返回普通 Qwen provider，不根据安装状态回退到 Chinese-CLIP；`_encoder_wiring()` 仍先查活动 Profile，因此已有 CLIP 图库不受默认值影响。
- 普通首次建库在缺少模型权重/manifest或 Qwen CUDA 加载失败时，在分类/入队前以失败状态和具体原因返回，不将该状态伪装成已跳过成功，也不留下已标记完成的 source build。
- 普通首次建库在分类、入队和写 source-built 标记前检查确定 generation 对应 Lance 表，`semantic.build_index()` 写入前再复核；SQLite generation 不存在但物理表存在时拒绝写入，提示使用现有嵌入维护隔离流程。检查 Lance 状态本身失败也 fail closed。
- 修复 `GatedEncoder` 对单图 `encode_image()` 的代理，确保现有资源门禁包装器仍能服务正常首次建库。
- Profile 激活依旧要求 generation 已发布并通过既有 `activate_profile` 安全门；已有其它活动空间只生成候选 Profile，不移动活动指针。候选任务既有 `activate_if_unset=False` 保持不变。

## 性能设计与验收

- **链路与风险**：首次建库多一次 generation 精确行查询；仅当无 generation 且 vectors 目录存在时读取 Lance 表名，不增加逐图 SQL 或全表统计。
- **做法与边界**：复用 registry、活动 Profile、已有 Lance 表命名和 activation gate；不新增默认偏好存储或缓存，不做大图库/真实 GPU 测量。
- **验证与停止**：隔离合成图库定向状态/故障测试及现有单击首次建库路径；只在相关测试、Ruff、diff 检查通过后结束，不对真实 Qwen/CUDA或容量作结论。

## 验证命令

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --python 3.12.10 -- pytest -q tests/unit/test_p4_embedding_provider.py tests/unit/test_p4_02o_semantic_build.py tests/unit/test_p4_embedding_default_build.py tests/unit/test_p4_02n_query_encoder_wiring.py tests/integration/test_p4_02y_i_source_build.py::test_one_build_click_on_a_new_source_scans_indexes_and_searches --junitxml=test-results/p4_embedding_default_qwen-focused-final-junit.xml --basetemp=.pytest-tmp/p4_embedding_default_qwen_focused_final
uv run --python 3.12.10 -- pytest -q tests/unit/test_runtime_device.py -k 'qwen or strict' --junitxml=test-results/p4_embedding_default_qwen-runtime-junit.xml --basetemp=.pytest-tmp/p4_embedding_default_qwen_runtime
uv run --python 3.12.10 -- ruff check src/photo_search/models/embedding/registry.py src/photo_search/indexing/semantic.py src/photo_search/refresh/pipeline.py src/photo_search/runtime/model_resource_gate.py tests/unit/test_p4_embedding_provider.py tests/unit/test_p4_02o_semantic_build.py tests/unit/test_p4_02n_query_encoder_wiring.py tests/unit/test_p4_embedding_default_build.py tests/integration/test_p4_02y_i_source_build.py
```

## 结果与边界

- 首轮单击建库用例在已存在的资源门禁包装器处暴露缺少 `encode_image()` 代理；新增代理后该用例通过，并确认开始前无活动 Profile、完成后正确激活本次生成 Profile。
- 最终定向组合 **58 passed**；Qwen/严格 CUDA 定向 **1 passed**；Ruff 通过。确认活动 CLIP 路由、普通 Qwen 缺权重、CUDA 拒绝及 encoder 返回空值均明确失败且不改变 jobs/outbox/source-built 标记、孤儿 Lance 表拒绝、generation 发布后首次激活、候选 Profile 不自动切换。Luna High 指出的检查顺序问题已修复并跟进确认闭合。
- `git diff --check` 对原有 CRLF 的 `test_p4_02n_query_encoder_wiring.py` 新增行按字面报 CR 字符；以 `git -c core.whitespace=cr-at-eol diff --check` 忽略该文件既有行尾格式后通过。其余改动为 LF。
- 未运行真实 Qwen/CUDA 图片建库；未访问真实 `D:\SZ\project\PhotoSearchData`；未做 30 万图测试。
- 不自动提交。
