# P4-SOURCE-RESUME-A — 图库断点续建入口最小修复

日期：2026-10-08。状态：**READY / 未实施**。立卡基线：`master @ a5aaab19`。
性质：P4-02Y-I 的遗漏入口小修，不重新实现断点续建。个人项目，简单测试。
建议：**GPT-6 Luna / Medium** 执行；用户自行切换模型。

## 1. 现象与根因

用户新加图库后，首次建库中途关闭服务；重启时语义索引可能已可搜，但地点／人物等分析仍未开始。来源明细可能只出现「快速刷新此来源」，无法从页面触发补齐全量历史资产。

历史已实现：
- P4-02Y-I 的 `mode="build"` 支持按来源持久 backlog 续建、复用已完成任务；`mode="refresh"` 只追本轮变化。[任务卡](P4-02Y-I.md)、[实现报告](../reports/P4-02Y-I-source-build-and-delete.md)。
- 2026-09-30 的状态修复引入 `NOT_STARTED` / `PENDING`，但 `web/app.js::sourceActionMode` 只认 `semantic.BUILDING` 或增强 `IN_PROGRESS` 为 `continue`。[状态修复](../reports/P4-02Y-I-stage-state-fix.md)。
- P4-HOME-UX-A 已实现进程内建库排队、同 scope 去重、按钮「已排队／正在建库」；进程重启清空等待队列，持久分析进度不丢。[排队报告](../reports/P4-HOME-UX-A-build-queue.md)。

此外，近期人物来源默认 `image_face_detail_auto`，而 `refresh/progress.py::ENRICHMENT_STAGES` 的人物仍查询 `image_face`。因此本卡**不以增强阶段的 `NOT_STARTED/PENDING` 直接推断未完成**，防止按钮永久误报；进度统计差异另行处理。

## 2. 最小交付

仅改 `src/photo_search/web/app.js` 与一个直接相关的现有测试文件（如 `tests/unit/test_api_refresher_queue.py`）；确有必要才改缓存版本标识，禁止顺手扩散。

1. 未建库时保留「开始建库」，按原有 `mode="build"` 运行。
2. 既有判断为 `continue` 时保留「继续建库」，不改变判定逻辑。
3. 既有判断为 `refresh` 的来源，**保留「快速刷新此来源」**，并增加次要操作按钮「检查并补齐建库」。
4. 新按钮仅调用现成 `refreshLibrary([Number(source.source_id)], "build")`；接入 `data-build` 和 `data-build-label` 等已有控制，继承活动禁用、排队、同来源去重与按钮恢复逻辑；勿单建轮询器。
5. 已有 `pollRefresh`／`loadSources` 负责运行结果和来源状态重新读取。浏览器缓存版本仅在当前项目机制确有需要时更新。
6. 按钮是**用户主动发起的补齐检查**，不是“证明还有未完成任务”；不将一轮请求终态等同全阶段完成。既有 `BUILD_ROUND_CAP` 等限制若尚有 backlog，允许用户再次点击。

## 3. 严格不做

- 不做服务启动自动续跑、不做持久排队或并行建库。
- 不改 `refresh/progress.py` 的阶段状态／人物 profile 统计；不改 `refresh/pipeline.py`、`api/refresher.py`、API、schema、迁移、模型与向量逻辑。
- 不强制重算已完成语义向量；沿用现有 `build` 幂等复用，明确全量补齐仍会遍历来源资产，可能花时间。
- 不访问、扫描、修改用户真实图库或 `PhotoSearchData`；不跑真实模型、不下载模型。
- 地名包／人脸／OCR 模型缺失照旧显示不可用，不宣称点击就能消除缺件。

## 4. 最小验证

以现有 Node VM／小 DOM 替身测试实际 `app.js` 为优先，断言：
1. `build`／`continue` 原入口不变；`refresh` 状态同时有「快速刷新此来源」和「检查并补齐建库」。
2. **点击补齐按钮**确实以该 `source_id` 调用 `mode="build"`；快速刷新仍为 `mode="refresh"`（不只查静态字符串）。
3. 其他建库正在运行时遵循「已排队／正在建库」与去重的原有按钮机制，运行结束后恢复。

执行对应定向 `pytest -q`（按仓库规范输出 JUnit XML）、`node --check src/photo_search/web/app.js`、`git diff --check`；只改 JS 时无需对全仓运行 Ruff。禁止全量 pytest、30 万规模测试、GPU／OCR／人脸真实推理及性能基准。

**用户手测（不由执行者触碰私人数据）**：重启服务刷新首页，展开一个语义已建而增强未完的来源，点击「检查并补齐建库」，观察地点／人物等阶段是否向前推进；缺少地名包或权重时核对如实提示。个人场景下确认后即可结束，不要求自动恢复。

## 5. 验收与交接

新增按钮可用于重启后手动触发原有 `build`，不丢快速刷新、不破坏建库排队／去重；现有向量／人工归属／原图不因本 UI 修复被更改。提交短实现记录（实际改动、定向测试结果、未实测项），更新 `docs/development-status.md` 交接。

范围限于前端入口与直接测试：执行者自检足够，不安排独立全量审查；若实现中发现必须改变调度、持久状态或人物进度协议，**停止扩范围并单独报告**。
