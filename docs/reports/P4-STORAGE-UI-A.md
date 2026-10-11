# P4-STORAGE-UI-A 执行报告

日期：2026-10-11
状态：**SCOPED PASS / 用户确认报告通过后已开放维护门闩；真实图库与浏览器验收未做**

## 最新结果摘要（2026-10-11）

界面已实现维护项目复选多选和批次结果逐项显示。用户确认审查报告通过并明确授权开闸后，`MAINTENANCE_REVIEWED=True`；用户可勾选一项或多项并确认，系统会在下次正常启动时按固定顺序串行处理，逐项保存结果；单项失败后继续剩余项目，批次中断不会自动重放。无需等待图库完整校验扫描结束；「检查维护项目」会单独刷新维护预览，建议先刷新并根据状态选择。

本次门闩开放后的定向 JUnit 为 **28 passed / 0 failed / 0 errors / 0 skipped**，含 UI 状态/API 排队/启动消费闭环、原拒绝路径、JS 渲染桩清理量断言及脚本缓存版本检查；Ruff、Python 编译、Node 语法和 diff-check 通过。报告下方的 **23 项**是启动 SyntaxError 修复后的历史基线，**26 项**是多选改动旧基线，**27 项**是开闸前 P2 修复结果。真实浏览器缓存行为和用户图库闭环未验证。

## 已实现

- 「我的图库 → 图库管理与分析明细 → 存储与维护」包含空间健康、维护预览、手动备份三个区域。快速概览不递归；用户主动检查时在工作线程运行有界扫描，结果展示扫描时间、各目录占用和 partial/busy 状态。
- 维护预览复用一次受扫描锁保护的文件清单，复核 Artifact/缓存/预览候选，并分别读取向量和 VACUUM 状态。没有在页面加载时执行深扫、备份或维护。
- 备份目录可在 UI 设置并检查绝对路径、可写性、空间、data-root 和已登记来源边界、链接/junction。只有用户点击「备份人工整理数据」才使用 SQLite backup API 创建 catalog 快照；独立临时文件经 integrity_check 后以不覆盖的原子硬链接发布。每次生成唯一文件，不轮换或自动删除旧文件。测试包含合成人物、合集成员及人工地点。
- 一次性维护协调器实现白名单操作、CSRF 保护、短时请求、data-root/catalog 身份核对、原子消费、失败不自动重试、启动 lease 前执行并复用原 GC-A/GC-B 入口；没有自动备份。手动备份不包含原图、LanceDB/外部人脸产物或模型。用户后续要求维护改为多选批次：一次确认一个非空、去重白名单，下次启动按固定顺序串行执行，逐项记录结果；单项失败后继续剩余选择，中断后不自动重放。
- `runtime/storage-maintenance-request.json` 与消费中请求从新备份排除；旧备份恢复也过滤这些授权，避免一次性许可被恢复后重放。

## 独立审查门槛

用户确认审查报告通过并明确授权开放门闩；当前 `MAINTENANCE_REVIEWED=True`，确认按钮可按勾选状态启用，排队 API 在正常 session/Origin/CSRF 保护下接受白名单请求。启动器会在会话、模型/GPU 和 HTTP 服务初始化前消费请求。门闩开放路径通过合成 API→启动消费测试；没有在真实图库上执行维护。

维护算法与授权边界仍限于本任务已有的合成证据。报告通过后，门闩已按用户明确指令开放，并补验了启用后的 API/启动消费闭环；本结论不扩大为全仓、真实图库或真实浏览器 PASS。

后续审查入口：[P4-STORAGE-UI-A-REVIEW](../tasks/P4-STORAGE-UI-A-REVIEW.md)。

## 定向验收

执行命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --python 3.12.10 pytest -q tests/unit/test_p4_storage_ui.py tests/integration/test_p4_storage_ui.py tests/integration/test_p4_storage_gc.py tests/integration/test_p4_storage_gc_b.py --basetemp=.pytest-tmp --junitxml=docs/reports/P4-STORAGE-UI-A-junit.xml
```

结果：**23 passed / 0 failed / 0 errors / 0 skipped**。包含手动快照一致性和旧快照保留、路径越界拒绝、CSRF 拒绝、用户点击后才生成备份、人工人物/合集/地点留存、过期/身份改变/持锁/一次性消费和审查门闩、恢复时不复活旧授权，以及 GC-A/GC-B 合成用例。详细 JUnit 见 [P4-STORAGE-UI-A-junit.xml](P4-STORAGE-UI-A-junit.xml)。

其他任务卡要求检查：相关文件 Ruff 通过；Python `compileall` 通过；`node --check src/photo_search/web/app.js` 通过；`git diff --check` 通过。

## 未验证边界

- 未运行全仓回归、真实浏览器、真实 PhotoSearchData、用户图库、GPU 或大规模图库。
- 备份证明为合成 catalog 的 SQLite 一致性快照；未做灾难恢复演练，也不声称快照包含原图、LanceDB 或外部产物。
- 自动维护通过内部合成调用路径验证；因审查门闩关闭，未开放真实 UI 确认至下次启动的用户闭环。

## 提交后启动回归修复（2026-10-11）

HEAD `e4481ea` 的 `storage_ui.py` 曾被写成单行且包含字面量 `\n`，导致个人试用启动时导入该模块触发 `SyntaxError`。已恢复 Python 源码与相关 Markdown 报告的 LF 换行；逻辑未变，`MAINTENANCE_REVIEWED=False` 仍保持关闭。

修复后按任务卡重跑：存储 UI 与 GC-A/GC-B 定向 pytest **23 passed / 0 failed / 0 errors / 0 skipped**；Ruff、Python 编译、Node 语法、模块导入、`git diff --check` 均通过。JUnit 已更新。未访问真实 PhotoSearchData/图库。

## 多选批量维护验收（2026-10-11）

按任务卡定向命令重跑，JUnit **26 passed / 0 failed / 0 errors / 0 skipped**。新增用例验证复选框入口、非空/白名单/去重校验、服务端固定顺序、单项异常继续处理其余勾选项、逐项结果检查点和批次一次性消费。自动维护门闩仍关闭，本测试不执行真实停机清理，也不替代独立 GPT-6 Luna High 审查。

## 多选批次复核与 P2 修复（2026-10-11）

此前复核发现以下两项 P2；已按报告字段口径修复，并新增 API/UI 断言。

- **已修复 P2 — 前端脚本缓存版本未更新。** `index.html` 已将引用升级为 `/web/app.js?v=p4-storage-ui-a-2`，确保多选改动更新脚本缓存版本；定向 API/UI 用例核对该引用。
- **已修复 P2 — 批次 UI 未显示每项实际清理量。** 结果行现在显示可用的 `deleted_files` 和实际释放字节（`freed_bytes`、`actual_bytes_freed` 或 `actual_reclaimed_bytes`）；向量维护读取阶段 `actual_reclaimed_bytes` 并汇总显示。定向 API/UI 用例检查各类字段接入。

P2 修复完成后门闩仍曾保持 `False`；用户之后确认报告通过并明确授权开放，随后补验启用路径，详见下节。

修复后按任务卡定向命令重跑：JUnit **27 passed / 0 failed / 0 errors / 0 skipped**。新增 Node DOM 桩实际执行批次结果渲染，验证 Artifact 删除文件数/释放字节、向量阶段释放字节、VACUUM 实际释放字节；API/UI 测试验证 `app.js` 缓存版本为 `p4-storage-ui-a-2`。Ruff、Python `compileall`、`node --check`、`git diff --check` 均通过。未访问真实 PhotoSearchData/图库，未运行全仓回归或真实浏览器。

## 用户确认报告通过与门闩开放（2026-10-11）

用户明确确认报告通过并授权打开门闩。`src/photo_search/storage_ui.py` 设为 `MAINTENANCE_REVIEWED=True`。新增合成集成用例验证启用状态可从维护预览读取、受保护 API 可排入多选批次、下次启动消费器按规范顺序执行每项并记录结果，第二次调用不重放；原门闩关闭拒绝用例改为显式模拟 `False`，继续验证拒绝安全性。

按任务卡定向命令复测：JUnit **28 passed / 0 failed / 0 errors / 0 skipped**。Ruff、Python `compileall`、Node `--check`、`git diff --check` 通过。仅合成 data root；未访问真实 PhotoSearchData/图库，未做真实浏览器或全仓回归。本次是用户确认审查报告通过后的门闩开放和本会话启用路径验证，不宣称执行了独立审查或真实图库验收。

## P2 修复后的当前会话复核（开闸前，2026-10-11）

复核确认报告摘要与当前代码、定向测试源码及 JUnit XML 一致：脚本 URL 已更新到 `p4-storage-ui-a-2`；批次结果 UI 显示逐项删除文件数和实际释放量；Node 渲染桩覆盖 Artifact、向量维护阶段和 VACUUM 的显示；JUnit XML 为 **27 tests / 0 failures / 0 errors / 0 skipped**。`git diff --check` 通过。

本节记录用户授权开闸前的证据核对：当时没有重跑测试，也没有做真实浏览器验证，门闩仍为 `MAINTENANCE_REVIEWED=False`。其后用户确认报告通过并授权开闸，且已完成启用后的合成 API/启动消费闭环，见上一节。
