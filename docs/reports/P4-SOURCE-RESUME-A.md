# P4-SOURCE-RESUME-A — 图库断点续建入口最小修复

日期：2026-10-09。实现与定向自检完成；用户图库手测待做。

## 修改

来源卡保持原来的「快速刷新此来源」操作，并在原 `refresh` 分支增加次要操作「检查并补齐建库」。按钮复用 `data-build` / `data-build-label`，点击调用既有 `refreshLibrary([source_id], "build")`。因此继续沿用当前活动禁用、排队、同来源去重、轮询和终态恢复机制；没有改动 `sourceActionMode` 的既有 `build` / `continue` / `refresh` 判定。

直接相关的 Node VM 小 DOM 回归覆盖：已建库但状态为 `PENDING` 的来源仍显示快速刷新并增加补齐按钮；实际点击分别发送来源 ID 与 `refresh` / `build` 模式；已有 `build` / `continue` 入口判定未变；补齐按钮排队禁用/显示「已排队」，任务终态恢复原标签。

## 验证

命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_api_refresher_queue.py --junitxml=test-results/p4-source-resume-a.xml --basetemp=.pytest-tmp/p4-source-resume-a
node --check src/photo_search/web/app.js
uv run --no-sync ruff check tests/unit/test_api_refresher_queue.py
git diff --check
```

结果：JUnit **4 passed / 0 failed / 0 errors / 0 skipped**；Node 语法检查、定向 Ruff 与 diff-check 均通过。JUnit 文件：`test-results/p4-source-resume-a.xml`。

## 未验证

未重启用户服务，也未触碰真实图库或 `PhotoSearchData`。用户可在重启服务并刷新首页后，对语义已建且增强阶段未完成的来源点击「检查并补齐建库」，确认阶段进度按现有依赖向前推进；缺少地名包或模型时，仍应显示对应不可用状态。本修复提供用户主动补齐入口，不证明某个阶段尚有 backlog，也不改变一轮请求的终态语义。
