# P4-LIBRARY-SYNC-B（V2）— 文件变动同步结果与可靠性反馈

**状态**：SCOPED PASS / 用户指定 LUNA-H 复核路由例外。**日期**：2026-10-11。**执行报告**：[P4-LIBRARY-SYNC-B.md](../reports/P4-LIBRARY-SYNC-B.md)。**前置**：A 卡的按钮及全局作用域；扫描核心仍使用现有 P4-02Y。**参考仓库**：`szzeng/aisearch master @ 3b9d472`，开工时 HEAD 为 `cc321f7`，工作树含未提交的 A 卡已验收改动。**实现建议**：GPT-6 Luna / Medium；独立 LUNA / High 子代理复核 PASS / finding=none。只做合成小测试；此结论不是 DS-H PASS。

## 一、本卡只负责“同步后告诉用户发生了什么”

A 卡负责入口、全局队列、主要按钮；**B 卡不得再次重排首页入口或重写 A 的刷新动作**。本卡重点把 `refresh/state.py` 已记录的、真实可核的变化汇总展示到 `web/app.js`。

现有代码里 `refresh/pipeline.py::_scan_one` 已把每个来源扫描结果追加到 `RefreshStatus.sources`，并汇总 `counters`：`sources_requested/scanned/failed`、`enumerated`、`unchanged_reused`、`hash_targets`、`new_assets`、`rebound_assets`、`locations_added`、`locations_missing`、`content_delta_count`、`location_delta_count`。**首先直接读取这些字段，禁止重新数文件、重新 hash 或多加一个扫描后处理流程。**

## 二、具体显示规则

本轮结束呈现一句可理解的摘要及可展开的“按图库明细”：

> 同步了 2/3 个图库；发现新增位置 12 个、变为缺失的位置 3 个；验证文件内容 13 个，其中真正新内容 8 个，重新关联已有内容 5 个。1 个图库未完成扫描（可重试）。

上述为**示例文案**，数字必须来自现有后端真实结果，不得硬编码。标记：

| 内部字段 | 向用户显示 | 必须说明的边界 |
|---|---|---|
| `sources_scanned / requested / failed` | 已成功同步 / 本轮目标 / 失败来源 | 一项失败不能显示“所有图库已同步” |
| `enumerated` | 本轮检查文件位置 | 非唯一照片数，重复副本会重复计 |
| `unchanged_reused` | 未变化、跳过内容校验的位置 | 不等于“复用 AI 向量数” |
| `hash_targets` | 重新校验文件内容的位置 | 包括新路径、修改、恢复，不代表新照片 |
| `new_assets` | 真正新内容 | 是 asset 内容身份计数，不代表位置 |
| `rebound_assets` | 重新关联已有内容 | **不能**宣称“移动 N 张”——也可能是副本或恢复 |
| `locations_added` | 新文件位置 | 跨目录移动会产生新位置，同时旧位置 MISSING |
| `locations_missing` | 本轮新确认缺失的旧位置 | 不等于“永久删除照片”，也不是当前全部缺失位置存量 |

默认摘要只显示有意义的 3–5 项；0 变化说“未发现文件变化”，若任何来源失败则说“已同步成功的图库未发现变化，另有 N 个图库失败”，不能笼统说“无变化”。详情展示全部统计及扫描耗时（现有 `scan_ms`），用户无需理解 `delta`。不是本轮保存的统计，在应用重启后不当历史记录显示；持久 `last_successful_scan` 可单独显示。

## 三、正确性边界与错误显示

1. **扫描完成事务保持不变**：只有该来源完整成功扫描并完成所有新位置内容绑定，才调用 `reconcile_missing_detail` 标记 `MISSING`。离线、磁盘错误、中断、权限不足，不得清空全图库也不得生成假缺失数。
2. **同内容身份不变**：移动、改名、跨来源副本按 SHA-256 重新关联相同 `asset_id`，已确认同 revision 的语义/人脸/OCR 可用产物保持复用；不能根据同名/EXIF/近似视觉猜测人工归属迁移。
3. **扫描失败独立反馈**：当前 `refresh/pipeline.py` 的最终 `state` 主要根据语义/增强结果推导，`sources_failed` 已记录但不直接纳入全部终态判断。UI **必须以 `sources_failed` 判定“部分来源同步失败”**；先不改已冻结的后端状态含义。若需要改为 `PARTIAL_FAILED` 协议，另报风险并补直接回归，不悄悄改契约。
4. **按来源清楚诊断**：复用 `status.sources[{source_id,state,code,...}]`，与展示名关联；展示安全的错误码/短文本，不输出绝对路径，不把“来源离线”误判为用户删除全部照片。
5. **移动最快的正确预期**：常规刷新仍遍历选定目录的所有文件名和 stat；对稳定未变位置不重新 SHA-256、原图解码和 AI；新位置一般仍需一次 SHA-256。禁止虚假承诺“只检测变化文件”或 O(变动数量) 整轮耗时。
6. **完整校验仍保留**：同一路径字节替换却精确保持 size 和 mtime 的极端情况，`fast` 可能漏检；高级 full scan 可重新哈希发现。不增加“无条件重识别所有 AI”的按钮。
7. **全部范围统一口径**：全局同步失败来源需要可定位；单来源只展示单来源结果。A/B 两卡修改 `app.js` 时串行施工，优先采用 A 现有 DOM，避免大范围重构。

## 四、5 组极简验收

1. 0 变化小图库：`hash_targets=0`、无 AI 重算；UI 显示“未发现变化”，单位不误写成照片数。
2. A 增加新图、A 内改名、增加已有内容副本：摘要区分 `locations_added/new_assets/rebound_assets`；已发布结果不重复推理。
3. A→B 移动同 SHA，随后全部同步：旧路径 MISSING、新路径 PRESENT、资产身份不变；“重新关联已有内容”不得写成精确移动计数。
4. A 删除 1 张后成功扫描、稍后重新放回：缺失被标记，恢复后复用；机器/人工数据未删除。B 离线或中断时 B 不得产生新的 MISSING 标记。
5. A 成功、B 离线：即便底层返回 `state=COMPLETE`，UI 仍写“仅 A 已同步、B 失败”；反复点击不会发生原图修改。含大小和 mtime 伪装替换的小 fixture 用既有 fast/full 测试复用，不另造大套件。

优先 `tests/integration/test_p4_02y_fast_scan.py` 和 `test_p4_02y_library_refresh.py` 的直接用例、`tests/unit/test_api_frontend.py` 的小 DOM。执行 Node 语法、受影响 Python Ruff/编译、少量 pytest/JUnit、diff-check。**无 schema 迁移，无大规模性能测试，无全仓回归。**

## 五、交付/不做

提交清晰的用户文案、来源级汇总和少量定向测试记录；报告注明暂时离线只影响可用性，不代表数据删光。**不改**GC、业务删除、人物/地点归属、缩略图清理、OCR 退役、USN 文件监测，也不默认改变全站 `availability=any/present/missing` 合同。失效数据统计属于 C 卡。
