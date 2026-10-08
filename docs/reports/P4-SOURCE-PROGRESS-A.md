# P4-SOURCE-PROGRESS-A — 人物阶段识别自动原图任务

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过**。

## 修改

人物进度现在聚合 `image_face` 和 `image_face_detail_auto`，两者都使用 `face-model@1`，统计范围仍限定来源当前可读资产及对应 SHA-256 revision。`handled`、`pending`、`attempted` 改为按 distinct asset 计数，防止同一照片同时留有两类任务时重复计数。旧 revision 的终态不会计入 `handled`；自动任务当前 revision 的 QUEUED/RUNNING 等状态进入 `pending`，因而 UI 会显示「处理中」。没有改调度、识别算法、API/schema 或 UI 文案。

集成回归用三个合成资产验证旧任务终态、自动任务排队/终态、旧 revision，以及同一资产双 task type 不重复计数；进度按 IN_PROGRESS → PENDING → IN_PROGRESS → COMPLETE 正确变化。

## 验证

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_02y_i_source_build.py::test_face_progress_includes_auto_detail_jobs_without_double_counting_assets --junitxml=test-results/p4-source-progress-a-focused.xml --basetemp=.pytest-tmp/p4-source-progress-a-focused
uv run --no-sync pytest -q tests/integration/test_p4_02y_i_source_build.py --junitxml=test-results/p4-source-progress-a.xml --basetemp=.pytest-tmp/p4-source-progress-a
uv run --no-sync ruff check src/photo_search/refresh/progress.py tests/integration/test_p4_02y_i_source_build.py
git diff --check
```

结果：针对性用例 **1/0/0/0**；完整来源建库进度集成文件 **20/0/0/0**；Ruff 与 diff-check 通过。结果文件：`test-results/p4-source-progress-a-focused.xml`、`test-results/p4-source-progress-a.xml`。

## 未验证

未重启用户服务、未访问真实图库或 `PhotoSearchData`，也未运行模型。需刷新/重启应用后确认来源卡人物状态能随实际自动任务推进；若无 face 权重，仍应显示「不可用」。
