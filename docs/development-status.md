# Development status

更新：2026-09-29。当前规则：主架构**需求修订 6**、执行策略**修订 8**。本文件只保留最新交接，目标 ≤100 行；旧记录见[原样归档](development-status-archive-2026-09-27.md)，默认不读全文。

## 当前任务 P4-02Y-I — 来源建库与管理闭环（实现完成，待独立复核）

**IMPLEMENTED / 待独立 DS-H 窄受影响面复核（2026-09-29；[实现报告](reports/P4-02Y-I-source-build-and-delete.md)、
[任务卡](tasks/P4-02Y-I.md)）。** 基线 `master @ 714d4ea`（卡面基线 `b17ded6`），schema 仍 **16**、`0017` 未实施、
ADR/依赖/`uv.lock` 零改动；daily `refresh` 档终态判定与 P4-02Y 逐行一致（唯一加性差异：未就绪时
`search_ready_basis` 由 `null` 变为原因字符串）。产品改动：`refresh/pipeline.py`（`mode="build"`：先分类后编码、
`BUILD_PAGE_SIZE=5000`/`BUILD_ROUND_CAP=10000` 有界循环、`_scoped_asset_page`/`_drain_scope` 按持久 scope 分页跑六个
既有增强阶段、`SEARCH_READY` 由持久 backlog 判定 `semantic-backlog-empty`、`note_search_gate`）、
新增 `refresh/progress.py`（纯 SQL 的来源级进度：扫描计数 + 语义 `total/confirmed/pending/failed/queued` +
五个增强 stage 的 `handled/pending` + `served_binding`/`enrichment_availability`）、`catalog/store.py`
（`delete_source`：先清 `asset_geo_facts`/`asset_ocr_text`/`faces` 的 `location_id` 引用，再同事务删 location→scan job→
`scan_runs`→`source_roots`；`QUEUED/RUNNING` scan 与活动 refresh 均结构化 409）、`ingestion/service.py`、
`indexing/semantic.py`（加性 `after_asset_id` keyset 分页）、`api/routes.py`（`POST /library/refresh` 加 `mode`、
`GET /sources*` 加 `progress`、新增 `DELETE /api/v1/sources/{id}`）、`api/models.py`、`api/refresher.py`、
`api/context.py`、`refresh/state.py`、`web/{app.js,index.html,styles.css}`。语义分母口径 = 该来源**被语义层分类为
图片的资产集**（有 `image_embedding` job 行 ∪ 当前 generation CONFIRMED），文档/视频不进分母；「✓ 可以语义搜索」
仅当 `pending == 0 且 confirmed > 0`。验证：新定点 14 例（`test-results/p4-02y-i-focused.xml`，一图一验收全覆盖）、
P4-02Y 两文件 + `test_api_sources_assets.py` + `test_api_frontend.py` 全绿、B 级全量（见报告 §6/§8）、
`ruff`/`uv lock --check`/`git diff --check` 全绿（沙箱 pytest basetemp `WinError 5` 按既有环境限制改走正常权限路由）。
**未验证 / 不授予 PASS**：独立 DS-H 复核未进行（卡面要求另开会话）、20-30 万真实建库墙钟与分页成本、
face/OCR 真实权重与 place resolver 数据集、浏览器计时（`BROWSER_TIMING BLOCKED`）、孤儿 asset/artifact/vector
物理 GC（卡面排除）、跨进程并发写者。**下一步 = 独立 DS-H 窄受影响面复核；不自动启动 P4-02Z。**

**独立 DS-H 窄受影响面复核 = `REVIEW FAIL`（1 阻断 B1 + 2 非阻断 F1/F2，`SOL_NOT_REQUIRED`；
[复核报告](reports/P4-02Y-I-dsh-narrow-affected-surface-review.md)）**：B1 = scope 内没有可确认内容时
`ensure_search_profile` 向 `search_profiles.image_generation_id` 写入一个从未发布的 generation ⇒ 外键失败，
抛出的 `sqlite3.IntegrityError` 不是 `CatalogError` ⇒ 无理由 FAILED + 「继续建库」永久重复失败（daily 档同洞，
归因既有洞，被本卡第一次放到主入口上）；F1 = 被拒内容不进 pending、卡片只显示 `confirmed/total` ⇒ 可谎称「可搜索」；
F2 = 「已扫描但无可索引图片」与「未建库」在持久状态里不可区分。
**[B1 修复轮](reports/P4-02Y-I-b1-fix-round.md) 已完成**：`indexing/semantic.py` 新增 `generation_published()` 守卫、
`ensure_search_profile()` 返回结构化拒绝（不抛异常、不写行）；build 档新增 `semantic-nothing-to-index` /
`semantic-confirmed-nothing` 终态并把未就绪原因写入 `failed_stages`；daily 档不再谎报 `semantic-confirmed`
（改 `GENERATION_NOT_PUBLISHED`）；卡片新增 `NO_IMAGES`/`UNINDEXABLE` 状态 + `failed/queued` 计数 + 状态词
「✓ 可以搜索，N 张无法编码」；持久标记 `metadata['source_build:<id>']`（复用既有 kv 表，无新 schema）区分未建库与
无可索引图片；多来源 backlog 改为 union（共享 asset 不再计两次）。修复后：本卡定点 18 例、5 文件 159/0/0/0
（35.314 s）、B 级 **1864/0/0/48**（310.355 s）、复核探针复跑无异常（无未捕获异常、终态均带 basis/原因；
删除边界的手检已固化为常驻用例）。
**下一步 = 修复后的独立窄复核（另开会话）；不自动启动 P4-02Z。**

## 上一任务（P4-02Y-T，已完成，保留记录）— 当前 HEAD 打包 + 最小 smoke + 开始个人试用

**PASS / PERSONAL TRIAL STARTED（2026-09-29，HEAD `b17ded6`；[报告](reports/P4-02Y-T-personal-trial-start.md)、
[任务卡](tasks/P4-02Y-T.md)）。** 从当前 HEAD 重新构建 onedir：`uv run --no-sync python
tools/packaging/build_onedir.py` exit 0，`dist/PhotoSearch/PhotoSearch.exe` 36 308 235 B / sha256 `2da907e2…012d5`，
包内 21 个 NVIDIA DLL、16 个迁移、`web/` 资源齐全。最小 packaged smoke（复用
`tools/diagnostics/packaged_api_smoke.py`，另补**刷新入口最小检查**）`status=ok / problems=[]`：启动 1.2 s、
schema 16、迁移/`photo_search` 数据资源/HEIF 组件与源码逐字节一致、无权重分支 409 诚实拒绝、
`POST/GET /api/v1/library/refresh` 202+同 id 终态且无模型时 `search_ready=false`（`MANIFEST_UNCONFIGURED`，不谎报）。
**真实个人试用 smoke（冻结 exe + 真实数据根 `D:\SZ\project\PhotoSearchData` + 真实中文 CLIP/CUDA）五步全 PASS**：
A 启动 1515 ms、`/` 与 `/web/app.js` 200、4 来源 ONLINE、包内 stdout `semantic channel READY` + `query_encoder_available=true`
（首次 capabilities 冷启动 2096.8 ms）；B「刷新图库」202 → `SEARCH_READY` 825 ms / `COMPLETE` 831 ms，`enumerated=3682`、
`unchanged_reused=3682`、**`hash_targets=0`/`hash_bytes=0`**、semantic `SKIPPED_NO_TARGETS`；C 8 条真实中文查询
**8/8 200** 各 20 命中、`vector engaged`、top1 缩略图 200（暖机 59–111 ms）；D 在 `D:\SZ\pic\P4-02Y-T 试用新增\`
新增 8 张重编码图片 → `hash_targets=8`/`new_assets=8`/semantic 8/8/projection 8 → `SEARCH_READY` 2615 ms，
`试用新增` 查询 top1–2 即新资产（id 3659–3666）；E `Ctrl+Break` 退出（lifespan shutdown 正常）→ 重启 1016 ms 后
generation/profile 不变、`/library/refresh` 回 `IDLE`、3 条查询 3/3 有结果、新资产仍可检索；原图安全：4 来源 3682 文件
`path|size|mtime_ns` 摘要前后逐字节相同。非阻断 F1–F4（沙箱 `%TEMP%` 限制冻结进程写上传临时文件、Ctrl+Break 退出码 3、
无权重夹具刷新终态只会显示「刷新失败」、未覆盖面/OCR 权重与 >50 000 delta）。**未做/未验证**：RC1 正式验收、浏览器渲染与首屏计时、
P4-02W A/B/C/D 重测、30 万/40 万、GTX 1660、ANN/A3、installer/MSI、打包产物内 face/OCR。**停止点：不启动 P4-02Z。**

**提交前 Git 核对**：本卡的打包/试用/证据对应 `b17ded6`；提交时 HEAD 已被**并行会话**推进到 `2235ab3`
（`[P4-02Y-I 主审复审]`，含 `7387915` 来源建库 build 档 / 持久来源进度 / 删除来源、`73c5f37` B1 修复，
报告见 `reports/P4-02Y-I-*.md`、卡 `tasks/P4-02Y-I.md`；本文件不重复其结论）。P4-02Y-I 改了
`refresh/{pipeline,progress,state}.py` 等，故 `dist/PhotoSearch` 与本次 packaged smoke / 试用证据**早于当前 HEAD
一步**；已在报告 §9 记录，并确认本卡给 `packaged_api_smoke.py` 补的刷新入口检查在 `2235ab3` 的每日档语义下
仍然成立（无模型 ⇒ `MANIFEST_UNCONFIGURED` + `search_ready=false` + 终态 `FAILED`）。**未做**：在 `2235ab3` 上
重新 build/smoke/真实试用（若要打包与试用证据对齐当前 HEAD，需重跑 P4-02Y-T §2–§4）。

## 上一任务（P4-02Y，已完成，保留记录）— 一键快速刷新图库（增量扫描 → 增量派生 → 可搜索）

**PASS（2026-09-29；独立 DS-H 窄受影响面工程主审 = `ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`，
[主审报告](reports/P4-02Y-dsh-narrow-affected-surface-review.md)）。** 基线 `bc84fd3` + P4-02Y 的产品/测试
diff（仍未提交；`d59bcd0`/`277b1d3`/`4f6e737` 只改任务卡，`3b70df6`/`bc84fd3` 是 P4-02Y-T 立卡），
schema 仍 16，`0017` 未实施。
[任务卡](tasks/P4-02Y.md)、[实现报告](reports/P4-02Y-library-refresh.md)。产品改动：`ingestion/scanner.py` 新增
`execute_scan(mode="full"|"fast")`（fast 只 hash 无法复用既有完整绑定位置 + 本轮 `MISSING→PRESENT` 恢复 +
有界 `content_delta`/`location_delta`；`full` 逐位置全量 SHA-256 语义不变）、`catalog/store.py`（`renamed`/
`restored`/`asset_id` 加性字段、`iter_unbound_seen_locations`、`reconcile_missing_detail`）、新增
`refresh/{pipeline,state}.py`、新增 `api/refresher.py` 与 `POST/GET /api/v1/library/refresh`（加性，202 + 只读状态，
单进程一个 active refresh）、`web` 入口（刷新图库 / 快速刷新此来源 / 阶段文案）、
`workers/query_encoder.py` 补 `health()`+`encode_image(path)`（缺则刷新永远补不了嵌入，属本轮发现的真实缺陷）。
§10 批量 observation：默认 **128**（128/256 在 30 万 no-op 上差异在噪声内 ⇒ 取单事务更短的 128，停止扫参）。
验证：定点 **40/0/0/0**、B 级 **1846/0/0/48**（262.823 s，JUnit 实读）、`ruff`/`uv lock --check`/`git diff --check` 全绿。
§11-A（30 万 no-op，真实产品 HTTP 入口）：`hash_targets=0`、`hash_bytes=0`、**0 张原图被打开做 hash / 0 张解码**、
各增强阶段 0 新工作、catalog 逐行摘要前后相同、并发合法搜索 0 拒绝、refresh wall 30.2–31.0 s（体验目标 1–3 分钟）。
§11-B（30 万 + 300 新 + 10 副本 + rename10 + delete10，真实 pinned Chinese-CLIP/CUDA）：只 hash **310** 个位置、
`new_assets=300`、`rebound_assets=10`、semantic 300/300/310/310/0、thumbnail 生成 300 复用 10、taken-time 320、
GPS 310、face/OCR/place-resolver `SKIPPED_UNAVAILABLE` 不阻塞、**start→SEARCH_READY 50 195 ms**、
`COMPLETE`+`search_ready=true`、9 次并发合法搜索 0 拒绝、SEARCH_READY 时真实 HTTP 搜索命中新增资产第 1 位。
证据：`test-results/p4-02y-{focused,full}.xml`、`test-results/p4-02y-noop-{128,128b,256,256b}.json`、
`test-results/p4-02y-delta.json`；工具 `tools/diagnostics/p4_02y_refresh_scale.py`（夹具只读，工作副本内做一次
`artifacts.input_revision` 合成修正使 selector 自洽：300000→0，夹具本身零改动）。
**独立 DS-H 窄受影响面工程主审已完成（2026-09-29）= `ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`**（[主审报告](reports/P4-02Y-dsh-narrow-affected-surface-review.md)：
①fast 只复用「已完整 SHA-256 确认 + asset_id 非空 + size/mtime 未变」的绑定，changed 在同一事务清空
`asset_id`，绑定入口仍只接受完整 hash ⇒ size/mtime 未成为内容身份；②`full` 仍是默认模式、逐位置全量
hash、`count_unbound==0` 与「仅完整成功后 reconcile MISSING」未动；③delta 有界（50 000）+ 溢出回退 +
不进公开信封，下游 thumbnail/taken-time/GPS/place/face/OCR 全只吃 delta，30 万档只 hash 310 / 只派生
300；④批量 observation 的 PATH_KEY_COLLISION、changed 清空、MISSING 原子对账、lease（每批一次
qualification）、中断续跑语义逐一保持；⑤SEARCH_READY 由「projection SYNCED + delta 在当前 generation
CONFIRMED」决定、在增强阶段之前宣告，face/OCR/GPS 非硬前置；⑥no-op 四份 JSON 0 hash / 0 AI / catalog
逐行相同；⑦无 ADR/schema/既有 API 契约变化）。主审**未重跑** 30 万性能测试与 B 级回归（B 级 JUnit 的
1806 个既有用例对应现状 `src/**`，两个新集成文件由主审在当前工作树独立复跑 **40/0/0/0**，
`test-results/p4-02y-review-focused.xml`），并重跑 `ruff`/`uv lock --check`/`git diff --check` 全绿。
非阻断 F1–F3：①报告中「start→SEARCH_READY 50 195 ms」是工具观测上界（流水线自记 39 538 ms，两者都达标）；
②30 万档「可搜索」探针在终止态之后执行，半途取证由门禁顺序 + 定点测试支撑；③单轮变化 >50 000 时该轮
D..G 增强被跳过且后续 refresh 不再捡起（catalog/投影/语义/可搜索性不受影响，缩略图有按需入口）。
**未验证 / 不授予 PASS**：浏览器计时（`BROWSER_TIMING BLOCKED`）、face/OCR 真实权重、place resolver 数据集、
GTX 1660、冷缓存、40 万、soak、真实个人图库；30 万历史向量为容量工具合成向量，故其相关性/质量不在本卡结论内。
（其中「打包产物内刷新」与「真实个人图库」已由上面的 P4-02Y-T 覆盖。）**P4-02Y-T 已于 2026-09-29 完成 →
PERSONAL TRIAL STARTED；P4-02Z 保持 DEFERRED，等真实使用后确需正式命名 RC1 再执行。**

## 当前任务 P4-02X — bounded block-size 最小产品收敛

**PASS（2026-09-28；独立 DS-H 窄受影响面工程主审 = `ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`，[主审报告](reports/P4-02X-dsh-narrow-affected-surface-review.md)）。** [P4-02X](tasks/P4-02X.md)：固定采用 `SCOPE_BLOCK_SIZE=15000`，不再扫参；依据最新 [P4-02M bounded block-size 复测](reports/P4-02M-bounded-block-size-retest.md)，15000/20 blocks 已取得约 −13.6% service 配对收益，而 30000/10 仅再多约 1–2 个百分点但单块 fallback 规模翻倍。执行策略：只改一个产品常数 → 最小正常 bounded 等价验证 → 至少一次 `resolved=False` tied-distance/exhaustive fallback → 必要受影响回归 → 复用 P4-02W 场景 A 做真实中文 CLIP + CUDA + HTTP 的 30 样本暖机文字验收。历史基线 P95=5076.4 ms，门槛 ≤5000 ms。若仍 >5 s，记 `IMPLEMENTED / PERFORMANCE STILL FAIL` 并停止，不在本卡改 20000/30000，不启动 A3/ANN；后台 D 留到后续整体性能验收。**结果（基线 `b42c341` + 未提交 diff）：产品改动仅 `src/photo_search/search/retrieval.py` 的 `SCOPE_BLOCK_SIZE` 5000 → 15000（+11/−1；schema/迁移、依赖、`uv.lock`、ADR/companion、信封/错误码/游标、`search_candidates_bounded`/`resolved`/fallback 逻辑、全序、RRF/BM25、scheduler 零改动；文件 SHA256 `23749299…b9426`）。** ①正常 bounded（30 万夹具、`top_k=20`）：`engine_blocks=20`、`bounded_calls=20`、`fallback_calls=0`、每块 21 = `top_k+1` 行、`scanned=300000`、`truncated=false`、最终 `(asset_id, distance)` 与改动前 5000 基线**逐项相同**；（DS-H 主审 F1 修正）离线 A/B 本轮不可比、不作结论（15000 n=4 vs 5000 n=2，两个夹心对照互相矛盾）；受控差值取同会话真实 HTTP 对照：15000 相对 5000 P50 −11.1% / P95 −7.3%，离线配对 −13.6% 取自 P4-02M 复测 `08e012c`。②边界并列：30 行全等距夹具下 block 5/8/15000 的 `fallback_calls` = **6/4/1 == blocks**（确实触发 `resolved=False → exhaustive fallback`），三档页面均等于穷举参考、`truncated=false`、候选 30/30 未丢；回退单块实测 5000 → 69.6 / 15000 → 183.9 / 30000 → 630.2 ms ⇒ 全量回退最坏外推 4.18 / **3.68** / 6.30 s（15000 即便退化也不慢于 5000，30000 会明显变慢 ⇒ 选 15000 的量化依据）。③定点 **38 / 0 / 0 / 0**、B 级 **1806 / 0 / 0 / 48**（257.2 s，均 JUnit 实读）、`ruff`/`uv lock --check`/`git diff --check` 全绿（沙箱内 pytest basetemp `WinError 5` 为既有环境限制，按正常权限路由重跑，未改 ACL）。④**真实中文 CLIP `vit-b-16` + RTX 4060 Laptop/CUDA + 真实 HTTP + 暖机 n=30：P50 3473.7 / P95 3760.2 / max 3770.8 / 失败 0 / 每样本 `engine_blocks=20` ⇒ P95 ≤ 5000 达标，保留 15000**（冷启动单列 6161.6 ms；前后夹具一致）；**同会话把常数临时改回 5000 的对照 P95 = 4058.4 ms 也达标** ⇒ 与 P4-02W 历史 5076.4 之差**不能全部归因本改动**（机器/缓存/会话状态差异为主），本改动受控效果 = 本会话 **−11.1% P50 / −7.3% P95** + 离线交错配对 **−13.6% service**；真实模型 in-process 归因 `vector_ms` p50 3163.7（P4-02W 同口径 4598.8）、`bounded_calls=20`、`fallback_calls=0`。诊断侧最小调整：`p4_02f_stage0.py`（`PRODUCT_BLOCK_SIZE` 改为导入产品常数防漂移 + `stages` 补 bounded 引擎桶）、`p4_02w_personal_trial.py`（`attribution` 新增只观测的 `_BoundedCallLedger`，因为 HTTP 信封不含 `fallback_calls`）。报告 [P4-02X](reports/P4-02X-block-size-15000.md)，证据 `test-results/p4-02x-{offline-equivalence,fallback-probe,warm-text,warm-text-control-5000,attribution}.json` 与 `test-results/p4-02x-{focused,full}.xml`。**未验证**：后台 D、浏览器首屏、上传搜图、40 万、GTX 1660、冷缓存、真实图库、soak；不改 20000/30000、不启动 A3/ANN/第二后端。**独立 DS-H 窄受影响面工程主审已完成（2026-09-28）= `ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`**（[主审报告](reports/P4-02X-dsh-narrow-affected-surface-review.md)：改动仅 `SCOPE_BLOCK_SIZE`；`vector_channel → search_candidates_bounded → resolved=False → 穷举回退` 逐行未变；并列夹具 `fallback_calls == blocks`（6/4/1）且页面等于穷举参考；30 万正常路径 `engine_blocks=20`、通道级 `truncated=false`、页面与 5000 参数逐项相同；定点 38/0/0/0 + B 级 1806/0/0/48（JUnit 实读）；真实 HTTP n=30 P95=3760.2 可复算；无 ADR/schema/API/数据安全变化）。本卡已收口 **PASS**（仅本卡常数收敛这一条）；报告 §3 离线 A/B 数字已按主审 F1 修正。执行：DS-H / DeepSeek V4.1 Flash / 高推理（模型切换由用户在 Codex 中手动完成）。**已由 `295f7ad`（[P4-02X 收口]）本地提交**（`src/photo_search/search/retrieval.py`、`tools/diagnostics/p4_02f_stage0.py`、`tools/diagnostics/p4_02w_personal_trial.py`、`docs/reports/P4-02X-block-size-15000.md`、`docs/tasks/P4-02X.md`、`docs/development-status.md`）；本轮主审与 F1 数字修正为未提交 diff。

## 上一任务（P4-02M bounded block-size 复测，纯测量，产品零改动）

**结果（2026-09-28，基线 `ae7074a`）：新 bounded 路径下 `SCOPE_BLOCK_SIZE = 5000 / 60 blocks` **不是**最优，但也谈不上必须马上改。** 30 万 wave 夹具、替身编码器、`top_k=20`、交错 `5000 → 候选档 → 5000 → 候选档`（18 个 5000 控制样本 + 每候选 3 样本 + 每形态 1 次预热）：**最快 = 30000 / 10 blocks**，`vector_channel` 中位 2832.2 ms、完整 service 中位 3138.0 ms，配对（候选 ÷ 两侧 5000 控制样本）**vector −16.7% / service −14.8%**；15000/20 与 20000/15 紧随（vector 配对 ×0.859 / ×0.845），**50000/6 反而回升（×0.923）⇒ 收益在 ≈10 次调用处饱和，不是「调用越少越好」**；**没有任何档位稳定劣于 5000**，故卡面的停止分支（「简单减少 block 数无明显收益」）不成立。6 档的 `(asset_id, distance)` 页面与 5000 基线**逐项相同**（`vector_channel` 与完整 service 两侧）、同档重复样本页面一致、`truncated` 全 false、`scanned=300000`、`fallback_calls` 全 0（该夹具无边界并列）。走新算法已由新埋点证实：`bounded_calls == engine_blocks`（60/30/20/15/10/6），每块请求与实际返回都是 `top_k+1 = 21` 行 —— 此前诊断工具**没有**覆盖产品方法 `search_candidates_bounded`（P4-02M 复审 F2），本轮先补最小诊断埋点（`TimedIndex.search_candidates_bounded` + fallback 计数 + `_BlockSizeOverride`）才测。**本轮产品零改动**：唯一 diff = `tools/diagnostics/p4_02f_stage0.py`（+367/−1，新增 `bounded-blocks` 模式），`src/**`/`tests/**`/schema/依赖/`uv.lock`/ADR 零改动、`SCOPE_BLOCK_SIZE` 仍 5000，`ruff`/`uv lock --check`/`git diff --check` 全绿。另做 bounded vs 穷举直接对照（只暴露 `search_candidates` 的替身强制走旧路径；5000 与 30000 各 1 样本/路径、冷进程背靠背）：两档页面与穷举**逐项相同**，且 30000 档 bounded 比穷举快 **24.8%**（5000 档两者同速 0.996）⇒ **P4-02F §13「减少调用数无收益」是穷举路径（`limit=len(ids)`）专属结论**，P4-02M 把每块返回量压到 `top_k+1` 之后，减少调用数才首次有收益。报告 [P4-02M-bounded-block-size-retest](reports/P4-02M-bounded-block-size-retest.md)，证据 `test-results/p4-02m-bounded-blocks-300k.json`、`test-results/p4-02m-bounded-smoke.json`、`test-results/p4-02m-bounded-vs-exhaustive.json`。**未验证**：真实模型/API 与真实图库、**边界并列回退分支（本轮一次未触发）**、GTX 1660、冷缓存、后台并发、40 万档、`top_k=200` 形态；不授予任何 §12.2/发行 PASS（P4-02W 的 `FAIL/PARTIAL` 结论不变）。**建议的唯一下一步（未立卡、未实施）**：一张有界产品改动卡，在 15000/20 与 30000/10 之间定常数（15000 拿到约 −14% 且回退最坏开销只有 30000 的一半），并在含边界并列的夹具上复测 `resolved=False` 回退代价；该改动属产品变更 ⇒ 需独立 DS-H 完整受影响面主审。
## 上一任务（P4-02W，已完成，保留记录）— 30 万本机个人试用性能验收（纯测量/验收/决策卡）

**结果（2026-09-28）：`PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL`。** 真实 HEAD `f9b9708`（master，工作树干净），`src/**`/`tests/**`/schema/依赖/`uv.lock` 零改动；按用户主动裁决完成 A→D→B→C 全量（覆盖卡面「A FAIL 即停」）。真实中文 CLIP `vit-b-16` + RTX 4060 Laptop/CUDA，30 万夹具母版只读、A/B/C 复用同一稳定目录、仅 D 用一次性副本。**A 暖机文字（n=30）：P50 4508.0 / P95 5076.4 / max 5172.4，失败 0，门槛 5000 ⇒ 超 76.4 FAIL**。**D 后台真实建库（scan 12000→10632 新资产；build targets/queued/steps/drain.applied 均 10632、batches 54、confirmed_delta 10632、write_rejections 0；收尾 CONFIRMED 310632）与文字并发（n=30）：P50 6320.2 / P95 8144.9 / max 8618.8，失败 0（0 内部争用拒绝，前 8 样本投影 `LAGGING`）⇒ 超 8000 FAIL，且真 overlap 仅 26/30（backlog 定容偏小，方法学事实）**。**C CUDA 上传（n=30）：P50 4526.2 / P95 4658.7 / max 5035.4，失败 0 ⇒ PASS**。**B 浏览器首屏：`BROWSER_TIMING BLOCKED`**（无记录式真实浏览器计时能力，卡面禁新增自动化依赖）；HTTP 层诊断 搜索 P95 4201.4、首屏 HTTP 链路 P95 4243.3 ms（非浏览器时间）。**§6 离线冒烟：PASS**（部分离线重扫后 `availability=missing`、列表 2.2 ms 不阻塞；整源缺失重扫结构化 `SOURCE_OFFLINE`；缓存缩略图仍 200，诚实态由 `availability` 承载）。诊断归因：**精确向量通道 ≈ full service 93%**（vector p50 4598.8 / encode 72.9 / fts 1.5 / authority 27.6 / pending 273.7 ms；`engine_blocks=60`、`scanned=300000`），与 P4-02M §7 一致。**唯一最小修复卡建议（未实施）**：只降精确向量通道单次检索执行成本（`vector_channel` 的 60 次 keyset 分块引擎往返 + 30 万候选分块 SQL 扫描与合并；当前已走 bounded 路径，穷举回退仅见边界并列未证明时），保持完整有序结果等价，同时服务 A 与 D；**不启动** A3/ANN/FAISS/SQL/索引/scheduler/model。报告 [P4-02W](reports/P4-02W-personal-trial-performance.md)，证据 `test-results/p4-02w-{warm-text,background-overlap,upload-search,browser-first-screen,attribution,offline-smoke}.json`；`ruff`(.)/`git diff --check` 全绿，`stats` 独立复算 `matches=true`。**扩容版容量证据专项审查 R2 已完成（独立 DS-H，2026-09-28）= `REVIEW PASS`**（[R2 复审](reports/P4-02W-dsh-capacity-evidence-rereview.md)：D 真实后台成立、C 30 样本可信、B `BLOCKED` 合理、CUDA provider 证据已落实、报告与 JSON 一致、总体保持 `FAIL/PARTIAL`；非阻断 R2-F1～F5）；未 push。

## 上一任务（P4-05 已完成，保留记录）

**[P4-05 — 停机一致性备份 + 空目录恢复 + 迁移保护（个人试用切片）](tasks/P4-05.md)：PASS（仅本卡，2026-09-28；DS-H 完整受影响面与 Sol 聚焦终审闭合）。** [R2 聚焦短复审](reports/P4-05-sol-disclosure-r2-rereview.md)：**SOL-F1R2-1 关闭**，空/未知声明输出 `unknown`，合法旧式声明明确未记录 HTTP lease，新式仅披露实际记录的 lease，均不夸大为全体写者停机证明。实现方定向 JUnit 实读 **19 passed / 0 failures/errors/skipped**（17.030 s）；本轮独立真实 helper 五案例探针 **5/0/0/0**，退出码 0；git diff-check 全绿。此前[完整受影响面主审](reports/P4-05-dsh-f1r2-affected-surface-review.md)的恢复等价性/失败边界/全量证据沿用（1801 tests、1753 passed、48 skipped，277.417 s），本轮未重跑全量。**F1R2-2 接受为非阻断**：操作员须停掉 HTTP、第一方维护 CLI 和直接写库/派生物进程，持续整个复制期；不扩维护 CLI 租约。B16/junction 等保留项仍未验证；§12.2、发行 remap/备份、升级回退、打包、真实图库演练不因本卡 PASS 而通过。当前本卡完成；本轮不生成下一卡、不启动下一实现，已本地提交 `e7cb598`，未 push。
**上一任务 [P4-02V — 争用下合法前台搜索被拒的最小修复](tasks/P4-02V.md)：PASS（产品面，2026-09-28；F1 口径已由用户裁决接受）。** [主审](reports/P4-02V-dsh-affected-surface-review.md)、[实现报告](reports/P4-02V-contention-fix.md)：方向 1「有界等待 100 ms + 降级」；产品改动仅 `search/documents.py`(+71/−8)+一处断言(+2)，信封/错误码/§12.2/ADR 零改动。主审独立复现：before 20 样本/13 真重叠/**5 拒绝** → after 20/14/**0 拒绝**；B 级 **1787/0/0/48**（240.97 s）独立复跑；`deferred_batches`**不进入公开信封**（三处调用者逐处核对）；`p2-contracts`/ADR 未规定该错误、`api/models.py` 本就含 `SYNCED|LAGGING`、§4.5.3 要求短事务分批派生 ⇒ 无 ADR 未决项，记 **`SOL_NOT_REQUIRED`**。**F1 已裁决（用户，2026-09-28）= 接受「纯文字形状 ≥30 + 忠实形状 16」口径 ⇒ 无未决项，本卡记 `PASS`（仅该条闭环）**；F2（读路径依赖 `indexing.maintenance`，建议后续移到 `catalog/errors.py`）与 F3（100 ms 预算下搜索可能以 `LAGGING` 返回，刚编辑文档可能这一次未进文字投影）为非阻断建议。**裁决后按 V3.1 §9 第 3 步处理「基本备份/恢复尚缺」的安全闭环（当前无卡，需另立），本卡不自动续接加速卡。** [报告](reports/P4-02V-contention-fix.md)：选定方向 1「搜索侧**有界等待 100 ms + 降级 + 保持可观测**」——`catch_up` 拿不到写锁时保留待补项、按既有 `search_projection.state=LAGGING` 返回，不再抛 `CATALOG_UNREADABLE`；产品改动仅 `src/photo_search/search/documents.py`（+71/−8）+ 一处既有断言（+2），schema/依赖/`uv.lock`/§12.2/ADR/错误码/信封零改动。前后对照（同夹具、同驱动、同 drain 入口，资产 444 000 不变）：新嵌入发布 + 全量检索 + 4 并发 **22 拒绝 → 0 拒绝**（32 vs 16 真重叠）；既有向量重投影 + 纯文字 **7 → 0 拒绝**（35 → **55 真重叠**）；子进程 drain 形态 8/8 真重叠 **0 拒绝**；降级样本延迟从「等满 5 s 被拒」降到 **271 ms（p50）/320 ms（p95）**。验证：新增 4 单测、点名 **66/0/0/0**、B 级 **1787/0/0/48**（281.8 s）、`ruff`/`uv lock --check`/`git diff --check` 全绿。未达标：最忠实形状 after 仅 16 个真重叠（<30，受该形状单样本 32–37 s 限制）；4 并发全量检索 30 s 量级与 §12.2 后台 P95 ≤ 8 s 仍未达标（既有性能问题）。**下一步 = 独立 DS-H 完整受影响面工程主审（新会话）；若判定触碰 §9.3/ADR 承诺则加 Sol 聚焦终审。** 推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**（由用户在 Codex 中手动切换）。**P4-02U 专项审查与 R2 复审均已完成（`REVIEW PASS`，[R1](reports/P4-02U-dsh-capacity-evidence-review.md)/[R2](reports/P4-02U-dsh-capacity-evidence-rereview.md)）⇒ 本卡实现前置已满足；但候选方向 2「2×500 ms 退避」按实测饥饿时长（进程臂 6.30 s、线程臂 32.45 s）算术不足 ⇒ 需重算退避预算或改选方向 1/3。** 依据 [P4-02J §5/§7/§8.3](reports/P4-02J-contention-closure.md)：30 万→36 万、投影未追赶且 drain 在场 ⇒ **合法前台搜索 4/9 被拒**（`CATALOG_UNREADABLE: database is locked`，各等满 5 s busy timeout），无 drain 对照 0/12，而 drain 自身计时写锁 max 0.657 s、`write_rejections=0` ⇒ 归因未定（候选：WAL checkpoint 或未计时写路径）；§12.2 后台档明确「不得因内部争用拒绝合法验证请求」。**本卡产品零改动**，只做最小反例 + 锁归属/checkpoint 四臂对照；**归因清楚前不动产品代码**；独立审查 = 容量证据专项审查（独立 DS-H 新会话）。
**[P4-02U — 后台 drain 在场时前台合法搜索被拒的写锁归属](tasks/P4-02U.md)：MEASUREMENT DONE（R2，2026-09-27，产品零改动；F1～F8 已闭合，R2 复审 `REVIEW PASS`）。** [报告](reports/P4-02U-lock-attribution.md)：**持锁者 = drain 自己的 `claim`/`apply` 写事务**（不是 WAL checkpoint、不是未计时写路径、不是外部进程）——30 万档线程臂 **5/6 被拒**、进程臂 **4/6 被拒**（各等满 5 s busy timeout），无 drain 对照 0/4；drain 单条事务 max 467/692.6 ms，但批间空隙（含前一事务 COMMIT，不是可竞争空闲）p50 1.58 ms ⇒ **写锁占空比 98.96%**；线程臂自身最长连续不可用 **32 449.4 ms**、进程臂 **6 295.8 ms**；PASSIVE checkpoint ≤62.2 ms、逐拒绝窗口内 ≤8.0 ms、busy=0、WAL ≤4.24 MB ⇒ 排除。本报告是测量证据；按卡面要求的**独立 DS-H 容量证据专项审查（新会话）已于 2026-09-27 完成**：[审查报告](reports/P4-02U-dsh-capacity-evidence-review.md) 裁决 **`REVIEW FAIL（需修订后复审；归因方向成立）`**（R2 修订后已由 [复审报告](reports/P4-02U-dsh-capacity-evidence-rereview.md) 记 **`REVIEW PASS`**）—— 独立复算支持「持锁者 = drain 的 `claim`/`apply`；checkpoint、未计时写路径、外部写者均被排除；无 drain 0/4、线程臂 5/6、进程臂 4/6；被拒原文一致」，F1～F8 已完成 R2 修订（[报告 §0](reports/P4-02U-lock-attribution.md)）：每次运行独立命名 + `analyze` 复算入口 + `arm_valid` 守卫 + 真实 `after`；报告统一最终 JSON 口径（线程臂 713/591、控制臂 99/62）并写明定义与探针 ≥~85 ms 口径；新增同状态交错 A/B 臂（running 2/4 被拒、held 0/4）；checkpoint 逐拒绝窗口对齐（窗口内 max 4.9–8.0 ms、busy=0）；线程臂自身最长连续不可用 **32 449.4 ms**；命令/退出码/ruff/diff-check 与不跑全量 pytest 的理由写入 §7。审查方独立复跑 `ruff check .` 与 `git diff --check` 均 exit 0。**R2 复审结论：`REVIEW PASS`（容量证据专项，2026-09-27）**——我的独立复算与新增交错臂证伪测试一致（8 段 blocked run 与 drain 写窗口重叠 98.7–100%；drain 暂停段 10.2/9.4/8.8 s 内 0 blocked）。**不授予任何性能/§12.2 PASS。**
**上一任务 [P4-02M — 30 万精确检索单点优化](tasks/P4-02M.md)：PASS（仅本卡单点优化，2026-09-27，已提交 `270ca3d`）。** [DS-H 主审](reports/P4-02M-dsh-affected-surface-review.md)产品面通过；[聚焦终审](reports/P4-02M-sol-focused-review.md)裁决 F1 闭合：K+1 块内边界证明、并列回退穷举、oracle 语义保留，无须改 ADR-0012。B 级 JUnit 实读 1783/0/0/48（48 skipped，沿用主审与实现产物，本轮不复跑）；约 15% 中位收益仅每臂 4 样本观察，非 P95/§12.2 达标。F2 埋点漂移限制保留、F3 为测试加固建议；本轮文档检查通过，不启用 A3，不自动生成或启动后续加速实现。
首轮因「真实搜索接线未实现」按卡面第 3 条停止；该缺口已由 [P4-02N](tasks/P4-02N.md) 关闭，P4-02N/O/Q/S/T 产品面均 `ENGINEERING REVIEW PASS` ⇒ 当时重新 READY（本轮已完成实现）。选点输入 = [报告 §7 的 30 万 Flat-Exact 基线](reports/P4-02M-exact-search-optimization.md)：纯 flat-exact **10.9 ms** vs 产品完整路径 **≈4.85 s** ⇒ 瓶颈在引擎往返/整行物化与 `pending_projection_page` 链路，不在距离计算；只修一个主瓶颈，不引入 NumPy 产品索引/FAISS/ANN/第二后端/A3。执行建议 DS-H / DeepSeek V4.1 Flash / 高推理，模型切换由用户手动完成。
**本轮收口记录（历史）**：P4-02S 真实库口径 = 3143 可显示可搜 + 36 RAW 可搜不可显示 + 479 非图片；自检索 26/26 top1=自身。后续候选（不属 P4-02M）：RAW 预览支持 + `reexecute` 解码路径归一化；人工标注的 20–30 条已知目标质量集（无人工标注 ⇒ 不做相关性结论）。
**收口明细（历史）**：**P4-02N/O/Q/S 已完成独立 DS-H 完整受影响面审查（[报告](reports/P4-02N-O-Q-S-dsh-affected-surface-review.md)）：P4-02N 产品面通过、P4-02O/Q 不通过（F1：`build --source-id` 恒抛 SQL 语法错误）、P4-02S 不通过（F2 上传闸门仍拒 MPO／报告归因错误、F3 格式策略未版本化）。F1 已修（[修复轮报告](reports/P4-02O-Q-dsh-fix-round.md)：来源谓词合成 + 新单测 + 真实库 dry-run `479/91/0` + B 级 1774/0/0/48）；F4/F5/F7/F9 同批改完。**F2 已裁决并实现**（放宽 P2-07 上传白名单，见 [P4-02T](tasks/P4-02T.md)）；**F3 已裁决（2026-09-27，采纳 DS-H 推荐）**：不 bump `catalog-thumbnail@N`（避免 2800 张缩略图无谓重建）；升级动作 = `catalog thumbnail --retry-failed`；发行级自动失效登记为 P4-07 显式要求。**F1 有界复审已完成**（修复轮 §6：无过滤 SQL 逐字不变、过滤是纯增量条件、占位符顺序/负例/`limit`×来源组合通过、全仓同类写法无同样缺陷）⇒ **P4-02O/Q 记 `ENGINEERING REVIEW PASS`（产品面）**。**第 2 步（[P4-02T](tasks/P4-02T.md) 格式口径收口）已实现**：上传白名单显式扩到 `{JPEG,PNG,WEBP,BMP,GIF,MPO,HEIC,HEIF}`、HEIC 方向归一复用媒体侧同一函数、两处 P2-07 断言随卡显式修订；真实模型端到端 9/9（上传 MPO 命中自身首帧、旋转 HEIC 命中自身、TIFF 仍 409）。**[P4-02T 主审](reports/P4-02T-dsh-affected-surface-review.md)完成并 PASS（产品面）**：上传 vs 库内解码 8 例逐像素一致、复跑四文件 86/0、B 级 1783/0/0/48、真实驱动 9/9 ⇒ **P4-02N/O/Q/S/T 产品面全部收口，均非 §12.2/打包 PASS。****

## 当前交付目标

- 30 万张刚需；当前电脑、固定一套模型先做个人试用，保留现有单机 SQLite/LanceDB 架构。
- 主架构 §12.2 本机试用目标：**暖机普通文字 P95 ≤ 5 s 已由 P4-02X 达标**（n=30 真实模型/HTTP P95 3760.2 ms；同会话 5000 对照 4058.4 ms 亦达标）；首屏 ≤ 6 s（B `BROWSER_TIMING BLOCKED`）、上传搜图 ≤ 5 s、后台建库文字 ≤ 8 s（D 首轮 P95 8144.9 FAIL）**仍未通过或未测**；GTX 1660 与正式发行不在本结论内。
- 原图只读、人工知识/筛选正确、恢复和基本一致性备份不能削减。达到试用全部门槛即转日常使用，不自动续做加速卡。
- 个人试用不等于最低GPU或正式发行支持；历史1秒/F2未通过结论不追认PASS。

## 已有成果与保留边界

- P0～P3的既有阶段成果及P4已完成任务保留，详细逐项记录在历史归档及任务报告，不重新验收无关阶段。
- P4-03A **PASS（仅契约冻结）**，ADR-0013 Accepted；[最终报告§8](reports/P4-03A-sol-contract-freeze.md)。独立契约JUnit33/0/0/0；尚无升级实现，不能当实现/B/D通过。
- P4-02H契约冻结保留：[r8报告](reports/P4-02H-focused-review-r8.md)。**A3未实现/未启用；P4-02F非性能PASS，F2历史未关闭。**
- **P4-02T 上传格式口径收口（ENGINEERING REVIEW PASS：产品面）**：[主审](reports/P4-02T-dsh-affected-surface-review.md)、[报告](reports/P4-02T-upload-format-policy.md)、[卡](tasks/P4-02T.md)。`UPLOAD_INPUT_FORMATS` 显式扩为 8 项（含 MPO/HEIC/HEIF）、上传解码前注册 pillow-heif 并复用媒体侧方向归一；真实模型端到端 9/9（MPO→首帧 200、旋转 HEIC→自身 200、TIFF→409）⇒ **审查 F2 的"上传不能收 MPO/HEIC"缺口闭合**。
- **P4-02S MPO 首帧 + 质量冒烟（ENGINEERING REVIEW PASS：产品面）**：[审查](reports/P4-02N-O-Q-S-dsh-affected-surface-review.md)、[报告](reports/P4-02S-mpo-support-and-quality-smoke.md)。`MPO` 从拒绝清单移入受支持（首帧语义 + 客观对照 + 单测 24/0）；缩略图 2800→**3143**、unsupported 379→**36**（只剩 RAW）；自检索 30 抽样 **26/26 top1=自身**（另有 1 超限 + 3 个 MPO 当时被上传白名单拒绝、原报告误记为 RAW，见 F2；**P4-02T 后 MPO/HEIC 已可作上传查询**）、上传 P95 1497 ms；12 条查询中位 62–88 ms、规范查询 32 样本 P50 87.5/P95 95.1 ms。
- **P4-02R 只读诊断（DONE）**：[报告](reports/P4-02R-embedding-thumbnail-coverage-diagnosis.md)。抽样 13/379：Pillow 识别 `MPO`×6/`TIFF`×7，媒体契约与语义探测判定**一致**（都拒绝），`load_normalized` 全 `FORMAT_UNSUPPORTED`，而 PIL 直读全可用 ⇒ 379 个嵌入是 P4-02Q 之前的历史残留（dry-run 已 `targets=0`）。推荐把 MPO 首帧纳入受支持格式（[P4-02S](tasks/P4-02S.md)），RAW 保持拒绝。
- **P4-02P 真实图库试建库（用户授权，DONE）**：[报告](reports/P4-02P-real-library-pilot-build.md)。4 个授权目录 → 3658 资产；真实模型建库 3179 张 CONFIRMED + 479 非图片资产 out_of_scope（[P4-02Q](tasks/P4-02Q.md) 修了 HEIC 注册与非图片不计失败）；缩略图 2800；暖机查询 32 样本 P95 88.8 ms。未闭合：379 张 FORMAT_UNSUPPORTED（有嵌入无缩略图）、质量集、30 万档。
- **P4-02O 语义建库入口（ENGINEERING REVIEW PASS：产品面）**：[修复轮](reports/P4-02O-Q-dsh-fix-round.md)、[报告](reports/P4-02O-semantic-index-build.md)。新增 `indexing/semantic.py` 与 `indexing.cli build`：按内容修订增量登记嵌入任务、真实图像塔执行、drain 确认、runtime 证据、profile 创建+激活。真实闭环：18 张合成图 exit 0 / 5453 ms（含模型冷加载）/ generation READY / 18 CONFIRMED / profile 激活 / 第二次 dry-run 0 目标 / 无绑定文本查询 200 且 top1 命中查询族。未扫描真实图库、未做并行与断点续跑。
- **P4-02N 真实查询编码器接线（IMPLEMENTED / DS-H 主审：产品面通过，F4/F9 已修）**：[报告](reports/P4-02N-real-query-encoder-wiring.md)。新增 worker 适配器与启动接线；有权重分支（真实模型→真实索引→真实 HTTP）暖搜索 23.0 ms、top1 命中语义目标、7/7 检查通过；无权重分支真实启动 `query_encoder_available=false` + 409 `QUERY_ENCODER_UNAVAILABLE`/`IMAGE_ENCODER_UNAVAILABLE` 逐字不变。产品改动 3 个文件 + 1 个测试文件；§12.2/GTX1660/真实图库仍未验证。
- **P4-02M 入口核对→停止分支（+ 2026-09-27 只读 Flat-Exact 基线）**：[报告](reports/P4-02M-exact-search-optimization.md) §7。30 万档：NumPy flat-exact **10.9 ms**（另计读矩阵 589 ms/586 MiB）、Lance single-pass 346.961 ms（复用 P4-02F）、产品完整精确路径 ≈4.85 s（复用 P4-02J/K）⇒ 瓶颈不在距离计算；NumPy Top-20 与引擎 Top-20 顺序 20/20 一致。默认启动无查询编码器（`query_encoder_available=false`、文本搜索409 `QUERY_ENCODER_UNAVAILABLE`）、登记模型目录不存在⇒真实端到端BLOCKED；按卡面第3条未进入性能实现，`src/**`/依赖/schema/§12.2零改动，未跑全量测试。唯一后续=[P4-02N](tasks/P4-02N.md)。
- 已有检索证据：[预算](reports/P4-02K-budget-and-a3-decision.md)、[争用](reports/P4-02J-contention-closure.md)、[P4-02F§13闸门A上界](reports/P4-02F-retrieval-latency.md)。合成service曾约5.35秒中位；另轮同会话空闲7.73秒/后台15.53秒中位，7样本P95约20.47秒；不能混为当前真实API结果。60块引擎调用与`limit=len(ids)`全行物化是主成本，闸门A参数空间实测不可达（最好约7.38秒/6次）。
- 投影未追赶+后台drain曾4/9合法搜索拒绝，具体锁来源未定；真实权重/默认搜索接线、真模型端到端、GTX1660、冷缓存/长时运行等仍未验证。

## 后置任务

- [P4-03B](tasks/P4-03B.md)：DEFERRED，完整模型升级实现；P4-04演练随之后置。契约不撤销。
- P4-07（未立卡）：**发行/升级必须让"媒体格式策略变更"自动失效旧派生状态** —— 启动时按需重试或版本化处理器标识（`catalog-thumbnail@N`）。个人试用期用 `catalog thumbnail --retry-failed` 手工步骤（2026-09-27 F3 裁决）。
- [P4-02L](tasks/P4-02L.md)：DEFERRED，ANN参数曲线；[P4-02I](tasks/P4-02I.md)：DEFERRED，A3隔离实验，仅确有必要时恢复。
- 渐进显示、复杂设置、多模型、35～40万压力观察和多机型发行不作近期前置。基本备份/恢复有缺口时优先单独闭合。

## 最近完成与验证

P4-02T（[主审](reports/P4-02T-dsh-affected-surface-review.md)、[报告](reports/P4-02T-upload-format-policy.md)）：主审独立复核上传 vs 库内解码 8 例逐像素一致（含两帧 HEIC 对抗例）、复跑四文件 86/0、B 级 1783/0/0/48（237.1 s）、真实驱动 9/9；两处文档问题（`upload.py` docstring、P4-00C 旧裁定未标修订）已在审查轮修正。白名单 8 项、上传解码先注册 pillow-heif 并把媒体侧 `restore_recorded_orientation` 提升为公开函数复用；变异对照证明去掉归一会让 `[False-6]/[False-8]` 两例失败；新单测 9/0、点名四文件 86/0、B 级 **1783/0/0/48**（238.8 s）、真实模型诊断 9/9；两处 P2-07 旧断言（HEIC 上传必须 409）随卡改为新口径。修复轮（P4-02O/Q，[报告](reports/P4-02O-Q-dsh-fix-round.md)）：F1 来源谓词合成修复 + 新增来源过滤单测（点名单测 9/0）+ `cli.py` 文案 + F4/F5/F7/F9 文档；真实库 `build --dry-run` 三 case（无过滤 479 / `--source-id 3` 91 / 不存在 0）落 `test-results/p402-fix-o-dry-run.json`；B 级 **1774/0/0/48**（236.5 s）、ruff/lock/diff-check 全绿；F2/F3 待裁决。DS-H 完整受影响面主审（2026-09-27，[报告](reports/P4-02N-O-Q-S-dsh-affected-surface-review.md)）：独立复跑单测 66/0、B 级 1773/0/0/48、ruff/lock/diff-check 全绿、P4-02N 两分支 7/7、P4-02O 驱动 13/13、4 帧 MPO 探针与只读库核对一致；结论 REVIEW FAIL（F1 阻塞，F2/F3 需裁决，F4/F5 文档/证据项）。P4-02S：单测 24/0（MPO+HEIC 契约）、真实库缩略图 +343、自检索 26/26、12 条查询 P50 87.5/P95 95.1 ms（32 样本）、B 级全量 1773/0/0/48（301.8 s）。P4-02R：只读诊断 13/379 抽样，链路判定矩阵见报告。P4-02P/Q：真实图库 3658 资产 → 3179 图片 CONFIRMED（479 非图片 out_of_scope）、缩略图 2800、暖机文字查询 P95 88.8 ms（32 样本）。P4-02O：单测 5/0、点名集成 59/0、真实建库驱动 13/13、B 级 1766/0/0/48。P4-02N：单测 13/0、点名集成 17/0、B 级 1761/0/0/48、两个诊断工具各 7/7。此前的 P4-02M 只读核对：：入口运行时核对 `uv run --no-sync python -c '…'` exit 0（模块级app/无参`create_app()`/默认`ApiContext` 的 `query_encoder()` 全为 `None`）、编码器实现面扫描无 `QueryEncoder` 实现类、登记模型目录缺失；`ruff check .`/`uv lock --check`/`git diff --check` 见[报告§5](reports/P4-02M-exact-search-optimization.md)。另有**用户授权的资源步骤**（2026-09-27）：真实中文 CLIP 权重已准备到 `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`，与仓库 manifest 逐字节一致、数值等价校验与真实 worker `demo-search --device cuda` 均通过，见[权重准备报告](reports/2026-09-27-chinese-clip-weights-preparation.md)；该步骤不改代码/依赖/schema，也未写入 C 盘数据根。本轮没有运行产品测试、容量或打包，不授予任何性能或实现 PASS。

## 工作树交接

**最新一轮（2026-09-29，P4-02Y-I 来源建库与管理闭环）**：基线 `714d4ea`（`[P4-02Y-I 立卡]`，master）。
产品/测试/报告已按卡面本地提交 = **`7387915`**（16 个文件） + 复核修复轮 **`73c5f37`**（9 个文件，含复核报告与
B1 修复轮报告；`src/photo_search/{refresh/pipeline.py,refresh/progress.py,refresh/state.py,catalog/store.py,
ingestion/service.py,indexing/semantic.py,api/{routes.py,models.py,refresher.py,context.py},web/{app.js,index.html,styles.css}}`、
`tests/integration/test_p4_02y_i_source_build.py`、`docs/reports/P4-02Y-I-source-build-and-delete.md`、`docs/tasks/P4-02Y-I.md`）。
**本文件未提交**：它同时带着上一卡 P4-02Y-T 的未提交交接文字与旁证报告（`docs/reports/P4-02Y-T-personal-trial-start.md`
仍未入库、`docs/tasks/P4-02Y-T.md` 与 `tools/diagnostics/packaged_api_smoke.py` 仍未提交），按「不得代为提交」的交接约定
留待用户决定；提交本文件前请先决定 P4-02Y-T 那三项如何处理，否则 HEAD 的链接会悬空。未 push。
下一动作 = P4-02Y-I 的独立 DS-H 窄受影响面复核（另开会话），不是自动开工；P4-02Z 保持 DEFERRED。

**最新一轮（2026-09-29，P4-02Y-T 打包与个人试用启动）**：基线 `b17ded6`（`[P4-02Y 收口]`，master）。本轮
**未提交**改动只有三项：`tools/diagnostics/packaged_api_smoke.py`（新增「刷新图库」入口最小检查：202 +
同 `refresh_id` 走到终态 + 无模型时 `search_ready=false`/`MANIFEST_UNCONFIGURED`）、新增
`docs/reports/P4-02Y-T-personal-trial-start.md`、`docs/tasks/P4-02Y-T.md` 状态行与本文件。`src/**`、`tests/**`、
依赖/`uv.lock`、schema（仍 16）、ADR 零改动；`dist/`、`test-results/`、`tmp/`（含一次性试用驱动
`tmp/p4-02y-t/{trial,probe,shutdown_probe}.py`）按 `.gitignore` 不入库。真实试用刻意保留的产物：
`D:\SZ\pic\P4-02Y-T 试用新增\`（8 张重编码图片；删除后再点「刷新图库」只会把它们标记为 `missing`）。
未 push；下一动作不是自动开工，而是进入真实日常试用（一事一卡），P4-02Z 保持 DEFERRED。

本轮基线 `549075f`（master，工作树干净），schema 仍 16，0017 未实施。ADR-0013/companion/契约测试、P4-03A 报告与卡、全项目评估/个人试用评估均已随 `549075f` 入库，历史状态归档保留。本轮只新增/修改：`docs/reports/P4-02M-exact-search-optimization.md`、`docs/reports/2026-09-27-chinese-clip-weights-preparation.md`、`docs/reports/P4-02N-real-query-encoder-wiring.md`、`docs/tasks/P4-02N.md`（+状态行）、`docs/tasks/P4-02M.md` 状态行、`docs/tasks/P4-02O.md`、`docs/reports/P4-02O-semantic-index-build.md`、`docs/development-status.md`、`src/photo_search/workers/query_encoder.py`、`src/photo_search/indexing/semantic.py`、`src/photo_search/indexing/cli.py`、`src/photo_search/__main__.py`、`src/photo_search/api/context.py`、`tests/unit/test_p4_02n_query_encoder_wiring.py`、`tests/unit/test_p4_02o_semantic_build.py`、`tools/diagnostics/p4_02n_*.py`、`tools/diagnostics/p4_02o_semantic_build.py`、`docs/reports/P4-02N-O-Q-S-dsh-affected-surface-review.md`（DS-H 审查报告）、`docs/reports/P4-02O-Q-dsh-fix-round.md`（修复轮报告）、`docs/diagnostics/search-smoke-checklist.md`（F4 口径）、`src/photo_search/search/upload.py`、`src/photo_search/media/decode.py`、`tests/unit/test_p4_02t_upload_formats.py`、`tools/diagnostics/p4_02t_upload_formats.py`、`docs/tasks/P4-02T.md`、`docs/reports/P4-02T-upload-format-policy.md`、`docs/reports/P4-02T-dsh-affected-surface-review.md`（主审）、`docs/reports/P4-00C-A-heic-business-implementation.md` 与 `docs/reports/P4-00C-dsh-engineering-review.md`（旧白名单裁定加修订指针）、`tests/unit/test_p2_search_upload.py` 与 `tests/integration/test_p4_heic_pipeline.py`（P2-07 断言修订）、任务卡 P4-02N/O/Q/S/T 的状态行；`docs/development-status.md` 追加审查+修复结论行（D 盘权重与 `test-results/`、`tmp/` 产物在仓库外/被忽略，不入库）。**2026-09-27 后续**：`334af99`（P4-02N/O/Q/S/T 收口）与 `e56c906`（30 万 Flat-Exact 基线）已本地提交；其后本轮对 P4-02M 历史残留的修正（本文件、`docs/tasks/P4-02M.md` 状态行与门禁、`docs/reports/P4-02M-exact-search-optimization.md` §4 溯源）**未提交**；另有 P4-02U 测量卡与专项审查产物未提交：`docs/tasks/P4-02U.md`、`docs/tasks/P4-02V.md`、`docs/reports/P4-02U-lock-attribution.md`、`docs/reports/P4-02U-dsh-capacity-evidence-review.md`、`tools/diagnostics/p4_02u_lock_attribution.py`；未 push，不得批量覆盖或代为提交。
