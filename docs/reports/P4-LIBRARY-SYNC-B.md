# P4-LIBRARY-SYNC-B 执行报告

**状态：SCOPED PASS / USER-DIRECTED LUNA-H REVIEW ROUTE EXCEPTION**
**日期：2026-10-11**
**基线：** `master @ cc321f71e8615cc5e611d30c13fa5aa66a9b2e8b`；工作树包含上一张 A 卡已验收但尚未提交的改动。本卡只在 A 的页面基础上串行添加同步结果展示。

## 结果

- 本轮结束时直接读取 `RefreshStatus.counters` 显示来源成功/失败数量及文件变化摘要。无变化时明确显示“未发现文件变化”和检查的位置数；有变化时区分新增位置、缺失位置、重新校验位置、新内容和重新关联内容，计数使用现有后端结果。
- 无论底层 `state` 是什么，只要 `sources_failed > 0`，页面都会明确提示“部分来源扫描失败”，摘要显示成功/目标数与失败数；按图库明细列出来源展示名、安全错误类型和重试提示。错误 `message` 不渲染，避免泄露路径。`rebound_assets` 表述为“重新关联已有内容”，不推断照片移动。
- “按图库明细”展开显示各来源已有扫描统计、位置/内容变化记录和单来源扫描耗时；另显示现有累计 `scan_ms`。未增加文件枚举、hash、API 请求、扫描后处理、后端状态协议或持久化统计。
- 状态区域改为合法的 `div` 容器，并保留 `role=status` 与 `aria-live`；脚本缓存版本更新。

## 验证

任务卡定向合成 JUnit：**114/0/0/0**，文件 `.pytest-tmp/P4-LIBRARY-SYNC-B-final.xml`。覆盖范围包括：

- `tests/unit/test_api_refresher_queue.py` 与 `tests/unit/test_api_frontend.py`，含无变化摘要、增量字段区分、扫描中和结束后独立来源失败提示（包括底层 `state=COMPLETE`）、来源级错误/名称、路径不回显和 A 卡按钮行为桩。
- 既有合成扫描回归：无变化不做 AI 工作；新增内容与副本重新关联；改名/缺失和恢复事实；离线不生成 MISSING；fast/full 对同大小与 mtime 伪装字节替换的差异；新增 A→B 同 SHA 全局同步用例确认原位置 MISSING、新位置 PRESENT、asset_id 不变且 rebound 计数为 1。
- `node --check src/photo_search/web/app.js`、`python -m py_compile tests/unit/test_api_refresher_queue.py tests/integration/test_p4_02y_library_refresh.py`、针对三个相关 Python 测试文件的 Ruff、`git diff --check` 全部通过。

测试期间最初的 DOM 桩结构和浏览器 HTML 解析各暴露一个适配问题；已更新桩模拟 `textContent` 清空子节点，并将非法的 `<p>` 中嵌套 `<div>` 改为合法 `<div>`。最终整组复测通过。

## 独立复核与边界

- 按用户明确指定的 **LUNA / High** 子代理独立检查 B 卡新增 UI 映射、来源状态/错误显示、隐私、DOM/键盘展开、测试证据及直接数据来源。复核发现的无效 HTML 容器已修复；最终结论 **PASS / finding=none**。
- V3.1 §2.4 默认要求 DS-H 完整受影响面主审；只读展示专项条款限定 P4-06，不覆盖本卡。因此记录为**用户指定 LUNA / High 路由例外**，不是 DS-H PASS。复核仅将 A 卡既有实现作为数据来源核对，不重复认领 A 卡审查。
- 未运行完整 B/C/D 套件、真实浏览器/图库、用户 `PhotoSearchData`、模型/GPU 或大规模性能测试；未改扫描器、数据库、schema、原图或 AI 任务。
- 建议手测：在隔离测试图库中验证 0 变化、一个来源增加/改名/复制、图库间同 SHA 移动、删除后恢复，以及 A 成功/B 离线时的汇总与明细；确认离线来源不被表述为永久删除。

下一项为已有 [P4-LIBRARY-RETENTION-C](../tasks/P4-LIBRARY-RETENTION-C.md)。本轮不启动该任务；其 C2 永久删除仍需单独授权。
