# P4-BUILD-STATUS-ACCURACY-A 执行报告

状态：IMPLEMENTED / SOL REVIEW PASS / VALIDATION PENDING（2026-11-27）。基线 `master e9f9b4e`，未提交、推送或发布。

## 原因和修改

- 语义活动进度原将 Outbox 事件当作照片、刷新路径使用本轮 delta 作分母；现每个编码批次复用来源卡 `semantic_breakdown`，按 PRESENT 位置、当前 `assets.sha256` 与 artifact revision、space/processor 派生 generation 和 CONFIRMED 状态统计唯一 asset，建库使用来源并集，日常刷新使用全库 scope。数量是范围总体覆盖，不是本轮处理数。允许权威计数变化时回退；不修改确认协议。
- OCR 原把 QUEUED/RUNNING/RETRY_WAIT 合并；现在按当前 revision 和唯一照片，以有效结果、RUNNING、QUEUED、RETRY_WAIT、BLOCKED、FAILED、UNATTEMPTED 为优先级互斥汇总，`pending` 仍是三种活动状态之和。旧响应显示等待处理或执行。
- 原运行状态没有线程存活字段，重复活动调用刷新进度时间，前端缺失真实进度时间时使用普通更新时间兜底。现快照用现有线程 `is_alive()` 加 `worker_alive`，保留 `running`；仅活动变化且允许标记进度时更新时间，缺失时未知，资源等待与超过 30 秒的提醒分开。线程退出优先显示异常；不认定死锁，不执行恢复。
- 候选任务曾附加到当前模型文字。现候选任务状态与按钮位于各模型卡独立区域；普通轮询保留按钮，任务切换后刷新模型卡的权威状态，活动模型标题不被候选轮询改写。

涉及 `refresh/pipeline.py`、`refresh/progress.py`、`refresh/state.py`、`api/refresher.py`、`api/models.py`、`web/app.js`、`web/index.html` 与定向测试。无 schema、租约、事务、GPU 调度或恢复机制改动。

## 验证

- 合成图片及桩编码器：`tests/integration/test_p4_02y_i_source_build.py::test_activity_uses_confirmed_assets_not_outbox_event_delta`；每批强制报告夸大的确认事件数，逐批活动数与实际范围统计及来源卡相等；通过。独立 SQLite 小夹具覆盖重复确认、历史 revision、其他 generation、失效位置和 OCR 多状态优先级。
- 最终定向（包含 Node 前端模拟）：`python -m pytest -q tests/unit/test_build_status_accuracy.py tests/unit/test_api_refresher_queue.py tests/unit/test_refresh_progress_state.py tests/unit/test_embedding_management_status.py tests/unit/test_p4_embedding_management_fix.py tests/integration/test_p4_02y_i_source_build.py::test_activity_uses_confirmed_assets_not_outbox_event_delta --basetemp=.pytest-tmp/status-final4 --junitxml=test-results/status-accuracy-final4.xml`：34 passed、1 skipped，退出 0。
- 受影响组合：`python -m pytest -q tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_library_refresh.py tests/integration/test_p4_ocr_v4_upgrade.py tests/integration/test_p4_source_detail_stability.py tests/unit/test_p4_embedding_default_build.py --basetemp=.pytest-tmp/status-combined --junitxml=test-results/status-accuracy-combined.xml`：126 passed、3 failed，退出 1。三个均为 `test_first_build_reports_missing_qwen_or_cuda_without_clip_fallback` 的参数化节点，断言缺模型时不创建任务，但实际分类先入队。将 pipeline 临时换为 HEAD 原始文件后在相同环境重跑同三节点仍 3 failed（`status-accuracy-baseline.xml`，pytest 退出 1）；已恢复本次文件。它们保持未解决，未删除或弱化断言。
- 早期进度测试因旧文案断言失败两次，按新提醒文案更新断言后重跑通过（`status-accuracy-c.xml`、`-c2.xml`、`-c3.xml`）。其他早期 A 80/80、B 44/44，最终应以组合及定向证据理解，不声称单次完整套件通过。
- `.venv/Scripts/ruff.exe check .`、`python -m compileall -q src/photo_search tests`、`node --check src/photo_search/web/app.js`、`git diff --check` 通过。原始 JUnit 在忽略目录 `test-results/`。
- 工作量：每语义批次一次已有全范围聚合，不逐照片执行 SQL；OCR 沿用按资产关联的 EXISTS，现有 jobs subject 索引、index_entries state/generation 索引及位置索引供查询使用。没有新增容量、延迟或发行结论。

## 尚未验证

按用户要求独立 Sol 全影响面复审通过，原卡指定的 DS-H 审查未进行；必需的实际运行、OCR 状态、候选运行/失败及窄窗口浏览器验收未完成。曾在用户要求停止 computer-use 前通过新会话打开隔离空库并看到图库管理页/API 200，仅为空状态观察，不构成受影响状态验收。未进行 Windows 发布包实测、真实模型/GPU 或真实图库验证；不声称 Issue #9 的积压及历史诊断解决。最初四项问题依据源码确认，未在修改前分别建立四个失败的合成测试。由于上述缺项及保留的失败，不能标 PASS。


## 独立 Sol 复核与修复（2026-11-27）

首轮独立全影响面复核发现两项 P2 和一项 P3：候选终态保留旧模型身份/切换资格；每批语义聚合在合成数据上成本过高且空同步也聚合；重复轮询重建暂停按钮使焦点及错误反馈丢失。修复为：语义统计以去重 scope JOIN 代替重复 membership 并跳过无工作分支；OCR 用短路 CASE 生成互斥分类；候选任务变化后请求新权威模型状态、保留活动标题、失效旧切换按钮，普通轮询复用按钮并保留操作错误。进一步关闭过期轮询停止、状态请求失败重试、动作完成后旧响应覆盖新状态及重绘后迟到错误反馈问题。独立 Sol 对最终变化复审通过，无新增阻塞；原卡指定 DS-H 未执行，不将 Sol 冒称 DS-H。

独立只读小规模内存对照：5,000 资产语义 SQL 从约 2.57 秒到约 0.008 秒，结果均为 3,334/5,000；200 资产六种 scope 新旧结果相同。20,000 资产 OCR 混合状态约 81→60 ms、全成功约 14→8 ms。为单机局部观测，不是延迟或容量保证。无真实图库。

修复后的受影响组合：`.venv/Scripts/python.exe -m pytest -q tests/unit/test_build_status_accuracy.py tests/unit/test_api_refresher_queue.py tests/unit/test_refresh_progress_state.py tests/unit/test_embedding_management_status.py tests/unit/test_p4_embedding_management_fix.py tests/unit/test_p4_embedding_check_ux.py tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_library_refresh.py tests/integration/test_p4_ocr_v4_upgrade.py tests/integration/test_p4_source_detail_stability.py --basetemp=.pytest-tmp/status-review-final2 --junitxml=test-results/status-review-final2.xml`：160 passed、1 skipped，退出 0。该组合未包含前述三个基线失败节点，原失败及原基线复现继续保留，不称其已解决。`ruff check .`、compileall、Node 语法和 diff-check 再次通过。独立复核验证了慢状态响应、暂停失败迟到、重试及身份更新；浏览器和窄窗口仍 PENDING。
