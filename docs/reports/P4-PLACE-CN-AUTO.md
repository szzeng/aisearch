# P4-PLACE-CN-AUTO 执行报告

状态：**IMPLEMENTED / VALIDATION PENDING**（2026-11-26）。不标任务 PASS：真实浏览器及完整验收矩阵尚未完成。用户指定 Terra High 作简要独立受影响面复核，结果见下。

## 实现

refresh 的 GPS 阶段后增加可选 CN 阶段。首次或旧库使用现有持久全量游标；普通刷新最多处理 5,000 条，build 使用现有 5,000 × 10,000 的安全上限，100 条批次提交。全量完成后按当前 GPS 身份反查缺失结果；revision 未变时跳过反查。metadata 键包含 dataset、包内容 SHA 和 processor；仅在缺项为空、活动包与全量 run 未改变且地图 revision 与快照一致的事务内提交标记。初始化采用 insert-or-ignore，避免并发初次启动重置已有进度。写入时沿用活动包、run 与当前 GPS 身份检查；不触碰人工地点表，不新增表或 migration。手动 API 共享该处理逻辑，在受管理工作线程执行，默认不重置；显式 restart 保留。

## 验证

- 合成 5,101 条有效 GPS：先执行两个 100 条全量批次，再由 build CN 阶段处理剩余 4,901 条；增量删除结果后首次处理 5,000 条，第二次处理 101 条且全部补齐。该测试直接调用 CN 阶段，并非从扫描到真实 UI 的完整建库验收；只有一张带位置的照片，不代表 5,101 张完整照片端到端建库。
- 受影响 GPS/refresh、CN、人工地点、地图 API、resolver 和 UI 定向 JUnit `test-results/P4-PLACE-CN-AUTO-final-r2.xml`：91 passed、0 failed、0 skipped。随后仅改 CN 并发初始化，同一 CN/UI 受影响集合 `test-results/P4-PLACE-CN-AUTO-final-r3.xml`：18 passed。两次为组合证据，不称同一节点完整运行。
- 首轮 `test-results/P4-PLACE-CN-AUTO-initial.xml` 为 68 项、2 failed：阶段顺序断言未包含新增阶段，UI 测试仍要求默认 restart；更新为任务规定的新行为后复跑通过，原记录保留。Ruff、py_compile、Node 语法和 diff-check 通过。
- 合成 5,101 条均已有结果的反查 EXPLAIN：GeoFact 使用整数主键范围，结果使用复合主键覆盖索引，无临时排序。原默认计划使用空间索引及临时排序；选定主键遍历后三次约 32.84、2.26、2.29 ms（首次调用和后两次，非冷盘保证），仅代表该样本和本机。
- GPS GeoFact 身份变更后的增量解析与人工状态保留：额外 JUnit `test-results/P4-PLACE-CN-AUTO-gps.xml` 1/1。
- 内置浏览器清单返回 `Local browser access is unavailable for this chat`，未打开真实页面；UI 状态、交互及窄窗口待验。完整 build→地点相册、实际照片 GPS 更新、批次中断后重启、包/processor 切换、损坏包及取消等任务卡矩阵尚未全部验收；已有独立 CN 人工操作回归不替代本任务完整验证。原计划的 DS-H 全影响面审查未执行；用户指定的 Terra High 简要复核不能表述为 DS-H 全面审查。

## Terra High 独立简要复核（用户指定）

独立只读复核发现两项：仅凭历史全量 complete 显示已自动整理会掩盖增量待处理；手动更新每批显示的数字重置。现改为不预先宣称完成，UI 按本次点击累计每批 scanned/processed，全量 API 的 total 字段沿用持久 run 累计。复核者跟进确认两项关闭、无新增阻断；未自行运行测试或进行浏览器验收。修复后 CN/UI JUnit `test-results/P4-PLACE-CN-AUTO-terra-fix.xml` 19/19（含跨批累计、暂停续做和全量累计），Ruff、py_compile、Node 语法及 diff-check 通过。按用户最新要求完成简要复核，其范围和模型不同于原定 DS-H 全影响面审查；功能验收不因复核通过而完成。

## 复现

```powershell
$env:UV_CACHE_DIR='.uv-cache'
.venv\Scripts\python.exe -m pytest -q tests/integration/test_p4_place_cn.py tests/integration/test_p4_02y_library_refresh.py tests/unit/test_p4_place_cn_ui.py tests/integration/test_p4_place_unified.py tests/integration/test_p4_place_map_api.py tests/integration/test_p2_place_resolver.py --junitxml=test-results/P4-PLACE-CN-AUTO-final-r2.xml --basetemp=.pytest-tmp/P4-PLACE-CN-AUTO-final-r2
```

未使用真实照片、用户 catalog、地名包或模型；未运行全仓回归、正式容量/Windows 发行验收，未构建或发布包，未提交推送。
