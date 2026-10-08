# P4-SOURCE-PROGRESS-A — 人物阶段识别自动原图任务

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过**。基线：`457789a`。
性质：P4-SOURCE-RESUME-A 明确留出的进度统计残项；个人小修。
建议：GPT-6 Luna / Medium；模型切换由用户手动决定。

## 现象与根因

来源卡「人物」阶段一直显示「未开始」，因为 `refresh/progress.py::ENRICHMENT_STAGES` 只按旧 `image_face` 任务统计；来源默认自动原图人物识别将任务写为 `image_face_detail_auto`。两类任务共用 `face-model@1` processor。进度投影没有观察自动任务，因此 `attempted == 0`。

## 目标与范围

让人物阶段进度统计同时观察 `image_face` 与 `image_face_detail_auto`，并继续按来源、资产当前 `sha256` 和同一 processor 统计。队列中任一类型的当前 revision 工作应显示 IN_PROGRESS；两类任务累计覆盖当前资产后显示 COMPLETE；只有过期任务仍不得计入 handled。数据以 distinct asset 计数，避免同一资产存在旧/新两类任务时超过 total。

只允许修改：

- `src/photo_search/refresh/progress.py`
- `tests/integration/test_p4_02y_i_source_build.py` 或同模块直接相关测试
- 本任务报告、`docs/development-status.md`

严格不做：

- 不改变任务调度、人物识别算法、模型/profile、worker、API/schema/migration 或 UI 文案；
- 不将手动 `image_face_detail`（非自动任务）混入人物自动阶段统计；
- 不访问真实图库或 `PhotoSearchData`，不加载模型、不下载数据；
- 不运行全量、GPU、性能或真实照片验证。

## 验收

在 `%TEMP%` 合成 Catalog 中为多个资产插入当前 revision 的旧/自动任务记录，验证：自动任务活动态计入 pending，旧任务终态仍计入 handled，混合任务按 distinct asset 统计，结束后状态转 COMPLETE；旧 revision 仍不计 handled。

运行对应 integration pytest 并输出 JUnit XML、定向 Ruff、`git diff --check`。最终报告记录实际结果与未验证边界。验收等级 A（单一进度投影小修）。
