# P4-LIBRARY-VIDEO-EXCLUDE-A（V2）— 视频扫描排除与已入库视频安全退出

**状态**：READY / 仅任务卡，尚未实施。**日期**：2026-10-11。**参考**：`szzeng/aisearch master @ f09c0379`，开工前复核最新 HEAD/Schema 与未提交改动。**推荐执行**：GPT-6 Luna / Medium；视查询资格变更风险可由 Luna / High 定向复核。**测试**：个人项目，只做少量合成定向测试，不做全仓/GPU/真图库。**禁止**修改原始视频、直接删除历史资产/向量/人工归属。

## 一、目标及边界

1. **新视频**：建库、普通 fast 刷新、完整 full 扫描统一在文件枚举阶段忽略常见视频扩展名，避免观察/登记/hash/入队；图片现有功能保持不变。
2. **旧视频**：已有 `file_locations` 视频位置，在其来源下次**成功**扫描后通过原有 `reconcile_missing_detail()` 变为 `MISSING`；原始视频还留在原路径，历史 `assets`、artifact、向量及人工事实不物理删除。**fast 刷新即可**，不强制全量 SHA-256。
3. **正常使用**：旧视频资产退出普通照片列表、普通搜索及相关照片列表；不改变真正缺失的普通照片 `availability=any|present|missing` 的现有语义，也不把“隐藏/缺失”宣称为“永久删除”。

现状：`ingestion/scanner.py::iter_observations()` 对所有普通文件调用 `observe_file()`；`execute_scan()` 在成功收尾执行 `store.reconcile_missing_detail()`。但是 `api/queries.py::asset_page()` 和 `search/retrieval.py::SearchScope` 默认 `availability=any`，所以**只把视频改成 MISSING 仍可能在正常列表/搜索看到它**。`assets` 按内容 SHA 去重，多个路径/来源可共享同一 `asset_id`，不能按“视频位置”武断删除整个资产。

## 二、改动范围

### A. 枚举阶段直接过滤

优先在 `src/photo_search/ingestion/scanner.py::iter_observations`、`observe_file` 之前进行文件名后缀判断。定义可复用、单一来源的视频扩展名政策，例如：

```python
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv",
    ".flv", ".webm", ".mpg", ".mpeg", ".m2v",
    ".mts", ".m2ts", ".ts", ".3gp", ".3g2",
    ".vob", ".asf",
})
```

- 使用 `casefold()` 处理大小写；Windows 相对路径应用统一后缀语义，检查 `.MOV` / `.Mp4`。
- 视频跳过目录观察、`os.stat` 文件观察、SHA、`file_locations` / `assets` 新建和各种 AI 任务。目录枚举本身仍会看到文件名，不应承诺“完全不访问视频目录”。
- 保持扫描流式遍历、路径安全、异常/中断/租约/重试及完整成功才可判缺失的边界；建库和 fast/full 使用同一 `iter_observations`，不单独再写一套扫描器。
- 不引入 `ffmpeg`/`ffprobe`、MIME/魔数嗅探、格式识别依赖；首版**只按扩展名**，不排除 RAW/HEIC/AVIF 等照片。非视频的未知格式维持原策略。

### B. 历史视频自然退出 PRESENT

本次不做清库命令、不新建“扫描后批量清空旧视频”的危险 SQL。沿用现有扫描对账：一次成功扫描中，旧视频路径不再被枚举，于该来源末尾转 `MISSING`，并触发既有搜索投影刷新/变更流。

- 普通 `refresh`（fast）即可；无需重新 hash 其余正常照片，不要求重新建 AI 索引。
- 来源离线、扫描失败、进程中断、取消或租约失效时不得执行缺失对账。
- 旧视频状态转换须幂等：重复刷新不新增视频资产、不重复产生无意义变更。
- 有其他 `PRESENT` **图片**路径的共享资产仍应作为图片保留；共享内容身份、图片原有预览/人物/地点等不误删。

### C. 权威可见性过滤（必须，不能只过滤前端）

在 `api/queries.py`、`search/retrieval.py` 及需要的照片列表入口复核并统一使用**视频专属**资格：一个资产必须有至少一条**非视频后缀的已登记位置**才属于普通照片候选。对 `source_id` 限定查询，合格的非视频位置**必须属于指定来源**；`availability=present/missing` 仍按原合同约束候选，不更改全局默认值 `any`。

- **视频专属**：保留缺失照片在 `any/missing` 的既有结果，不能用简单“强制 availability=present”代替。
- **混合位置**：相同 `asset_id` 有视频/图片位置时，保留图片；视频位置不能让它错误命中视频来源筛选。
- **零位置资产**：保留现有孤儿/历史语义，不将其猜测为视频，也不因此删除人工知识。
- 过滤必须在 SQL 候选资格、计数及分页/Top-K **之前**生效；保证 FTS、语义、纯过滤、搜索计数、`asset_page` 与 keyset 游标一致。不允许用前端过滤或先限额再过滤造成漏页。
- 优先抽取可复用的轻量 SQL 后缀判断，避免各业务入口复制视频扩展名集合；避免每次普通查询扫描全部 `file_locations` 或增大 per-asset N+1，利用已有按 `asset_id` / `source_id` 的访问路径。开工时检查搜索快速通道/A3 是否依赖“无额外资格”假设，**资格不等价时禁用捷径并沿已有权威路径回退**，不能在排序后补过滤。
- 限定修改确实会显示该类资产的图库浏览、搜索、人物/地点/合集等照片入口；不要重做 UI、重构所有页面或扩展成人工资产删除。

### D. 历史视频清点（只读、小脚本）

新增例如 `tools/diagnostics/video_asset_probe.py`，从现有 SQLite 只读、按来源统计：
- 视频位置 `total/PRESENT/MISSING` 数量；
- 只有视频位置的唯一资产数；
- 同时具有视频和非视频位置的共享资产数；
- 最多少量示例 `asset_id`、相对路径、状态（不输出原始视频内容/用户机敏感路径）。

不遍历真实图库、不扫描视频文件、不读取向量；位置数和唯一 asset 数分开说明。若已有适配 CLI 可复用；不为本卡新增 UI 控件或长驻后台任务。

## 三、明确不做

- 不删除、移动、修改原始视频；不修改 `assets` 身份或图片人工归属。
- 不直接 `DELETE FROM assets`、删除 `file_locations` 历史、删除 Lance 向量或清理 artifacts/previews；不变更 GC-A/B、失效保留 C1/C2 规则。
- 不做 schema 迁移、版本升级、模型重跑、全库重建、复杂文件类型检测或高级设置。
- 旧视频变 MISSING 后仍可出现在**诊断/保留预览**，但不得作为正常照片候选；这不是物理空间回收。若未来确需永久删除视频历史，请另立获授权的安全清理卡。

## 四、最小验收（3～4 组合成小库）

1. **新入库与大小写**：`a.jpg`、`b.heic`（按现有支持能力）、`c.mp4`、`d.MOV`。扫描后照片正常；视频无新 `file_locations/assets`、无 hash/AI job。图片扩展名不被误伤。
2. **历史视频+重复 fast 刷新**：构造原先 `PRESENT` 的 `old.mp4` 和 `photo.jpg`，运行生产 fast 刷新（或复用生产扫描+搜索可见性链路）；视频变 `MISSING` 且不再出现在普通 `GET /assets`、自然语言/纯条件搜索中（包括默认 `any`）；重复刷新不复活，普通 MISSING 照片原先查询能力不变。
3. **共享/跨来源+分页**：同一 asset 的视频位置与图片位置（如有必要合成同内容绑定），保证图片仍显示、按仅视频的来源过滤不命中；补一条连续分页与总量相等的小用例，覆盖 SQL 排名/分页前筛选。
4. **失败与原文件保护**：来源离线或扫描中断不得把原 PRESENT 错标 MISSING；对合成原始视频检查内容/大小/mtime 前后不变。诊断脚本的数量与数据库事实一致。

复用现有 `tests/integration/test_catalog_scan_streaming.py`、`test_p4_02y_fast_scan.py` 等夹具；新增最少必要的集成/单元断言即可。只跑受影响 pytest、Ruff、Python 编译、修改 JS 时做 Node 语法和 `git diff --check`。**不跑全仓、不碰真实 PhotoSearchData、不跑 GPU/大规模性能基准**。若共享 SQL 条件引发显著全表扫描，只做一个定向小型查询计划检查并改用现有索引/候选路径，不升级为大型性能专项。

## 五、交付与通过标准

提交受影响实现、最小测试及一份简短 `docs/reports/P4-LIBRARY-VIDEO-EXCLUDE-A.md`，记录：新视频不入库、旧视频经成功 fast 刷新变 MISSING、普通列表/搜索不再显示旧视频、共享图片不受影响、原视频零修改、历史数据未物理删除、未执行的测试/风险。

**PASS** 必须有上述最小验证真实结果；仅编写过滤后缀、只标记 MISSING 或只在前端隐藏视频，均不能算完成。
