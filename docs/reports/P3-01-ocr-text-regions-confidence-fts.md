# P3-01 — OCR 任务、原文/区域/置信度与 FTS 更新

**Status:** PASS（B + D + C），executed 2026-09-14。

## 任务记录

| 字段 | 值 |
|---|---|
| 任务 | P3-01 — OCR 任务、原文/区域/置信度与 FTS 更新（V3.1 §8） |
| 计划模型 | DS-L（DeepSeek V4.1 Flash / 低推理）；调度/索引接线部分可标 DS-H，ONNX/Windows 疑难点转 C1 |
| 实际执行 | 用户在本会话开始时声明「已经在 DS-L 了」；本会话无法读取客户端 `config.toml`，故只按用户声明记录，**未做、也不声称任何自动模型切换** |
| 起始基线 | `HEAD = 3ea0cf1`（Windows Codex 环境规则），P2-08 报告提交 `446dbf7`；工作树干净 |
| 实现提交 | 本任务代码、测试与本文档同一提交（`git log --oneline --grep='P3-01'`；仅本地提交，未推送） |
| 上一任务 | P2-08（B + D + 真实浏览器闭环，零生产改动），报告 `docs/reports/P2-08-acceptance-matrix.md` |
| 前置复核 | `SUPPORTED_SCHEMA_VERSION = 8`、migration 连续 `0001`～`0008`、P0-04 固定权重齐备（4 文件大小/SHA-256 全匹配）、RapidOCR 1.4.4 / onnxruntime 1.30.0 已安装 |

## 交付内容

### 1. 处理器契约（沿用 ADR-0003 外壳，不新造协议）

`src/photo_search/ocr/processor.py::ocr_processor_spec()`：

| 字段 | 值 |
|---|---|
| `processor_id` / `task_type` | `ocr-text@1` / `image_ocr` |
| `processor_kind` | `non_model`（冻结契约把 non_model 限定为 `CPU_PREFERRED` + `cpu`，正是本任务要求的设备策略） |
| `device_policy` / `supported_devices` | `CPU_PREFERRED` / `["cpu"]` |
| `runtime_backend` / `runtime_package_id` | `onnxruntime` / `onnxruntime-gpu@1.30.0`（取本进程真实解析到的版本） |
| `config_hash` | P0-04 manifest 里 `config.yaml` 的 SHA-256 = `bf94a1da…7332` |
| `output_type` | `ocr_document.v1`（`artifact_type = structured`，**永不出现** `embedding`/`space_id`/`dimension`） |
| `cancellation_supported` / `max_batch_size` | `true` / `1` |

`JobRequest`/`JobResult` 五字段身份、lease/attempts/迟到提交语义、device 资格矩阵一律未改；`device_policy=auto` 时 OCR 永远解析到 `cpu` + `CPUExecutionProvider`（实测断言）。

### 2. 固定权重加载（禁止隐式回退）

`src/photo_search/ocr/weights.py`：只从 `<data_root>/models/ocr/ppocr-v4-mobile/` 打开 P0-04 记录的
det/cls/rec 三个 ONNX 与 `config.yaml`，**先逐文件校验字节数与 SHA-256**，任一不符即
`OCR_WEIGHTS_MISSING` / `OCR_WEIGHTS_HASH_MISMATCH` 结构化失败。`contract.py` 里的固定表是
P0-04 manifest 的镜像（不在运行时读 `tools/`，因为冻结包不含该目录），并由单元测试逐项对齐
（role/path/bytes/sha256、`config_hash`、预处理阈值、`package_version`）。引擎构造时只把
这四个绝对路径交给 RapidOCR，参数里不会出现 wheel 内的 `site-packages/.../models/*`。

### 3. 有界高分辨率输入与切片

`src/photo_search/ocr/engine.py`：

| 决策 | 值 | 说明 |
|---|---|---|
| 工作图长边上限 | 2000 px | 绝不使用 320/448 缩略图尺寸；超过则 LANCZOS 等比缩小并记录 `scale` |
| 单趟像素上限 | 1.8 MP | 超过即切片 |
| 切片尺寸/重叠 | 1280 px / 160 px | 末片贴边对齐；切片上限 16（超出即 `OCR_INPUT_TOO_LARGE` 结构化拒绝） |
| 声明像素硬上限 | 60 MP | 与 P2-07 上传上限一致；超限拒绝而不解码 |
| 解码路径 | 复用 `media/decode.load_normalized` | 格式门禁 + EXIF 方向归一 + JPEG draft 有界解码 |
| 取消检查 | 每片之间 | `cancel()` 或 60 s（可配）预算耗尽；预算耗尽 = 可重试 `RETRY_WAIT` |
| 低置信度 | 保留（`text_score=0.0`） | 冻结引擎默认 0.5 会丢掉低置信度区域，与本卡「低置信度可见」冲突 |
| 重叠去重 | IoU ≥ 0.5 保留高置信度者 | 只做几何去重，不做文本去重（同一行重复文字不会被合并） |
| 阅读顺序 | 垂直重叠 ≥ 50% 聚成一行，行内按 x | 确定性，同一像素必得同一顺序 |

### 4. 结果与产物（原文/检索副本分离）

结构化 artifact（`ocr-document.v1`，content-addressed、**确定性**：同内容同字节）保存
**原文**（不改写数字/日期/编号）＋按阅读顺序的区域（polygon/box、text、confidence）＋
处理器/权重指纹/`config_hash`/引擎配置/输入 revision/切片几何。artifact 里**没有**任何
墙钟时间、路径或 job id：同一逻辑任务的确定性重放必须复用同一 artifact 行。

SQLite 侧（迁移 `0009`）只保存：

* `asset_ocr_text`：当前 OCR 文档的**检索副本** `search_text`（规范化后的分词文本）＋
  `input_revision`/`text_revision`/`processor_id`/`spec_revision`/`model_fingerprint`/
  `artifact_id`/`region_count`/min-mean-max 置信度/`tokenizer_version`；
* `asset_ocr_regions`：**区域元数据**（`region_index`、`box_json`、`confidence`、`text_revision`）。

原文与 polygon 只存在于 artifact；两个表都没有原文列。隐私边界沿用 P2-03A：坐标/原文不进普通
日志、URL、错误详情、反馈与向量库，普通响应只给计数/状态。

### 5. FTS 更新（同事务排队、幂等、可重放）

`0009` 把 `search_projection_queue` 的 cause 从 0006 冻结的四值扩到**五值**（新增
`OCR_TEXT_CHANGED`），并新增两个触发器：`asset_ocr_text` 的 INSERT 或
`search_text/input_revision/tokenizer_version` 的 UPDATE 都在**同一短事务**里把该 asset 排进
既有队列。重建队列表时用「快照表 → DROP → CREATE → 回填」的方式完成，0006 的 8 个触发器
SQL **一字未改**（未做 `ALTER TABLE RENAME`，因此触发器引用不会被改写）。

投影侧唯一改动是 `search/documents.py`：`document_source()` 现在从
`asset_ocr_text` 读取 `ocr_text`，并且**只在 `input_revision = assets.sha256` 时**才采用——
这是「内容变化前的旧文本绝不附着到新内容」的强制点。写入路径仍是 P2-04 的
`search_projection_queue` → `sync_document()` → FTS5 触发器，`catch_up` 仍是唯一投影入口。

### 6. 强制重新识别入口

`ingestion/**` 属本卡禁止修改范围，因此入口放在新包内、沿用 `catalog ...` 风格：
`python -m photo_search.ocr.cli`，子命令 `weights` / `status` / `enqueue` / `run` / `recognize`，
`--json` 输出、退出码 0/1/2。强制重识别复用**同一五字段逻辑任务**（`enqueue --force` 把
SUCCEEDED/FAILED/CANCELLED/BLOCKED/RETRY_WAIT 回拨为 QUEUED 并清零 attempts），不新增 job 行；
RUNNING 且租约未过期的任务不会被触碰。不新增长驻线程：`run` 把队列跑空即返回。

### 7. 如实状态

`GET /api/v1/capabilities` 新增 `ocr` 段（只读、路径/文本/坐标全无）：
`processor_id`、`task_type`、`model_fingerprint`、`config_hash`、`weights_present`（只做存在性/大小
检查）、`documents`（当前 revision 的文档数）、`stale_documents`（旧 revision 的历史文档数）、
`regions`、`low_confidence_regions`、`low_confidence_threshold`、`jobs`（按状态计数）、
`pending_jobs`、`blocked_jobs`、`projection_queued`、`state ∈ {NOT_RUN, LAGGING, BLOCKED, SYNCED}`。
未运行就是 `NOT_RUN`，落后/排队/有旧 revision 文档就是 `LAGGING`——**不把「未运行」显示成
「图里没有这段文字」**。

## 修改文件

新增：

* `src/photo_search/catalog/migrations/0009_ocr_documents.sql`
* `src/photo_search/ocr/{__init__,contract,weights,engine,processor,service,cli}.py`
* `tests/p3_ocr_fixtures.py`、`tests/unit/test_p3_ocr_{contract,geometry}.py`、
  `tests/integration/test_p3_ocr_{end_to_end,lifecycle,cli,migration}.py`
* `tools/diagnostics/ocr_smoke_fixture.py`、`docs/diagnostics/ocr-smoke-checklist.md`

加性修改：

* `src/photo_search/catalog/errors.py`（5 个 extra 码：`OCR_WEIGHTS_MISSING`、
  `OCR_WEIGHTS_HASH_MISMATCH`、`OCR_RESULT_TOO_LARGE`、`OCR_DEADLINE_EXCEEDED`、`OCR_INPUT_TOO_LARGE`）
* `src/photo_search/catalog/schema.py`（`SUPPORTED_SCHEMA_VERSION = 9` + 注释）
* `src/photo_search/scheduler/engine.py`（`submit()` 内同事务 OCR 持久化钩子，惰性导入）
* `src/photo_search/scheduler/projection.py`（`ocr_summary()`；导入仅含字符串的
  `photo_search.ocr.contract`）
* `src/photo_search/search/documents.py`（`ocr_text` 派生，**只**加了一个取值函数与一处字典项）
* `src/photo_search/api/{models,routes}.py`（`CapabilitiesResponse.ocr` + `/capabilities` 的 `ocr` 段）
* `tools/diagnostics/packaged_api_smoke.py`（C 级新增 `coverage_ocr_block` 断言）
* 三处版本/环境耦合的既有机械断言（见 §7）

**未修改**：`0001`～`0008`、冻结 ADR 与 JSON schema、`ADR-0002/0003/0005` 语义、五字段 job 身份、
lease/attempts/迟到提交、artifact/outbox 提交顺序、P2-03A/B 地点状态机与幂等、P2-04 tokenizer
世代与 `place_text` 来源规则、P2-05 RRF/并列/`hit_reason`、P2-06A/B 会话/profile/反馈语义、
P2-07 上传限制；`places/**`、`geo/**`、`ingestion/**`、前端资源、打包入口、`pyproject.toml`、
`uv.lock` 全部零改动（依赖仍是 95 包）。

## 迁移与不变量

`0009_ocr_documents.sql` 是连续增量：`schema_version 8 → 9`，`0001`～`0008` 逐字节不变，
遵守 ADR-0002 §7（只读预检、备份、单事务推进、缺件/过新拒写）。实测：

* v8（含 `assets`/`file_locations`/`search_documents`/队列行/中文空格路径）→ v9：备份内
  `schema_version = 8` 且无 OCR 表、无 OCR 触发器；单事务推进到 9；旧的
  `search_projection_queue` 行逐列原样保留；四值旧 cause 仍可写，未知 cause 被 CHECK 拒绝；
  `verify_schema(9)` 通过；再次 migrate 无动作；
* 注入损坏 0009：`MIGRATION_FAILED`，库仍为 8，OCR 表与临时快照表都不残留；
* `DROP asset_ocr_regions` → `SCHEMA_INCONSISTENT` 且 `missing_objects` 指向该表；
  过新库（`schema_version = 10`）→ `SCHEMA_TOO_NEW` 且主库字节不变；
* 表级不变量：`region_count > 0` 必须有 min/mean/max 且有序；`box_json` 必须是合法 JSON；
  confidence ∈ [0,1]；OCR 文档写入即自动排队 `OCR_TEXT_CHANGED`。

## 验收

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | 0 | JUnit `test-results/junit.xml`：`tests=877 failures=0 errors=0 skipped=1`，208.9 s |
| 本任务新增用例（`--collect-only` 计数） | 0 | 75 例：contract 24、geometry 20、end_to_end 4、lifecycle 13、cli 8、migration 6 |
| `uv run --no-sync ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | clean（无空白错误） |
| `uv run --no-sync uv lock --check` | 0 | `Resolved 95 packages`（依赖未变） |
| C：`uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | 0 | 真实 onedir 重建完成，bundle 3.3 GB |
| C：`uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P3-01` | 0 | `status=ok`、`problems=[]`；`coverage_ocr_block = {processor_id: ocr-text@1, state: NOT_RUN, weights_present: false, documents 0, regions 0, …}` |
| 打包审计 | — | `_internal/photo_search/catalog/migrations/` 含 `0001`～`0009`；`0009` 与源码 SHA-256 一致（`28FF83C9…6A08`） |
| 真实夹具：`uv run --no-sync python tools/diagnostics/ocr_smoke_fixture.py` | 0 | `status=ok`，12.22 s；见 §8 |

沙箱说明：本次会话的受限令牌无法创建/列举 pytest 的 basetemp（`.pytest-tmp` 的 DACL 拒绝
访问，即使在 `pytest -q ...` 之前手工新建目录也一样），因此上述 pytest/ruff/lock/pyinstaller/
smoke 命令都在沙箱外（提权）执行；uv 缓存始终使用项目内 `.uv-cache`。这是本机既有环境事实，
不是本任务引入的行为。

## D 级故障注入（具名场景）

| 场景 | 断言 |
|---|---|
| 缺权重（模型根无文件） | job `BLOCKED`、`last_error_code=OCR_WEIGHTS_MISSING`、`blocking_reason=SOURCE_UNAVAILABLE`、`attempts=0`，无 artifact/OCR 行/队列行；错误消息不含本机路径 |
| 权重被替换（同字节数不同内容） | `OCR_WEIGHTS_HASH_MISMATCH`，同样零写入 |
| 权重缺件时执行 CLI 工作命令 | 在**排队之前**就返回结构化失败，不产生 blocked job |
| 提交前崩溃（`jobs.finish` 注入异常） | 事务整体回滚：无 artifact 行、无 OCR 行、无队列行、job 仍 `RUNNING`、artifacts 目录无孤儿文件；故障消失后同一 Result 再次提交即成功 |
| 内容变化后旧结果 | 同路径换内容 → 新内容是新 asset（ADR-0002 内容身份），旧文本不附着到新 asset（`document_source.ocr_text == ""`），重新识别后只出现新文本 |
| 迟到旧 revision | 把现有文档的 `input_revision` 改成旧值（触发器自动排队）→ 投影 `ocr_text` 变空、FTS 不再命中、`ocr_summary.stale_documents=1` 且 `state=LAGGING`；强制重识别后 revision/文本恢复且只有一行 |
| 到期租约 + 迟到提交 | 第一个 worker 的 lease 过期后由第二个 worker 接管，旧 Result 提交被 `STALE_LEASE` 拒绝且零副作用；接管者正常完成后才写入文档 |
| 用户取消 + 迟到结果 | `scheduler.cancel` 后 job 为 `CANCELLED`，迟到结果被结构化拒绝且零写入 |
| 执行器内取消 | 返回 `CANCELLED`，无 artifact/OCR 行 |
| 预算耗尽 | `RETRY_WAIT` + `OCR_DEADLINE_EXCEEDED` + `attempts=1` + `run_after` 前移，无写入 |
| 重放/强制重识别 | `text_revision` 1→2、`ocr_document_id` 变化、artifact 校验和不变（复用同一 content-addressed 文件）、区域行数不变、`search_documents` 行与 `updated_at` 不变、job 只有一行 |
| 队列语义 | OCR 提交后队列 cause 为 `OCR_TEXT_CHANGED`、`search_documents` 尚未派生；`catch_up` 派生后 `remaining=0`，再次 `catch_up` 为 0 processed（幂等） |

## 真实夹具证据

`tools/diagnostics/ocr_smoke_fixture.py`（`%TEMP%\P3-01 OCR 验收`，仅合成图片，使用系统
`msyh.ttc` 渲染真实中文与数字）：

| 图片 | 像素 | 工作尺寸 | 切片 | 区域数 | 置信度区间 | 耗时 |
|---|---|---|---|---|---|---|
| `invoice.jpg`（发票 金额 1280 元／车牌 苏A12345／护照 MRZ P<CHN／编号 20260914／合计 3760.50） | 1400×900 | 1400×900 | 1 | 5 | 0.9719 – 0.9999 | 2.58 s |
| `dense.jpg`（3 列 × 4 行高文字密度页） | 1800×1200 | 1800×1200 | 2 | 20 | 0.7899 – 0.9998 | 6.02 s |
| `blank.jpg`（空白页） | 800×500 | 800×500 | 1 | 0 | — | 0.91 s |

（引擎会话构建 0.74 s；单张耗时来自 `recognize_image` 直接测量，脚本自身报告三段合计 12.22 s。）

* 发票图检索副本（规范化后）：`发 票 金 额 1280 元 车 牌 苏 a12345 护 照 mrz p chn 编 号 20260914 合 计 3760 50`；
  FTS 命中实测：`发票`、`1280`、`20260914` 都命中发票图（`fts_hits = {1280: [3], 20260914: [3], 发票: [3]}`）。
* 区域行（SQLite 侧，发票图）：`[31,40,425,90] 0.9998`、`[30,111,362,162] 0.9997`、
  `[28,183,436,236] 0.9719`、`[30,255,365,306] 0.9999`、`[31,325,322,379] 0.9998`——按阅读顺序
  自上而下，坐标在图内。
* 空白页是**诚实的空文档**：`region_count=0`、`search_text=''`、无区域行、无 min/max 置信度，
  但 `asset_ocr_text` 行存在；文件名检索仍命中（OCR 空结果没有抹掉其它派生列）。
* `GET /api/v1/capabilities` 的 `ocr` 段为封闭计数集合，响应体不含 `1280`/`20260914`、
  不含 `polygon`/`"box":`；`ocr.state` 在识别前为 `NOT_RUN`、识别并 catch-up 后为 `SYNCED`。

## 隐私边界（实测）

* 普通日志/错误/响应：job 错误消息、`/capabilities`、`/jobs` 中都没有本机路径、识别原文或单区域坐标；
* artifact JSON 里没有本机路径（只含相对概念与形状信息），`space_id`/`dimension` 缺席；
* `asset_ocr_regions` 只存 box/confidence，原文只在 artifact；
* 会话保护边界未放宽：没有新增任何返回原文/坐标的 HTTP 端点。

## 未验证项与已知边界

1. **真实复杂版面/语料未验证**：竖排、艺术字/低对比度、手写、旋转/倾斜、多语言混排、
   真实截图库规模与吞吐；没有人工标注的准确率/阅读顺序质量结论。
2. **引擎自带空格处理**：固定引擎拼行时会去掉行内空格（`222 999` → `222999`），因此对
   被合并后的数字段做「单个数字段」前缀检索不一定命中。产品**不纠正**数字/日期/编号，
   检索副本忠实反映引擎输出；这是已知的检索保真度边界，未做词元级重切。
3. **检索副本的 tokenizer 世代**：`search_text` 在写入时按当时的 tokenizer 规范化；更换
   tokenizer 世代后 `rebuild_all` 无法重新分词 OCR 文本（原文只在 artifact 里），需要重跑 OCR
   或由后续任务定义重投影路径。`filename_text`/`place_text` 不受影响。
4. **引擎缓存边界**：进程内按「四文件 大小+mtime 签名」缓存校验结果与会话；同一进程内
   同尺寸同 mtime 的内容替换不会被重新哈希（与 P1-06 §5.1 同类边界）。每个新进程首次使用
   都会重新校验 SHA-256。
5. **产品侧自动排队未接**：扫描不会自动排 OCR 任务，本任务只提供受控入口（CLI）与幂等队列；
   冻结包内没有常驻调度线程，打包应用的 `weights_present` 因此可能是 `false`（不撒谎）。
6. **区域元数据尚无服务入口**：本卡把原文/坐标留在 artifact、普通响应只给计数；前端资源在
   本卡冻结，因此「页面里看到原文与区域」需要后续任务（新页面/详情接口）来交付。
7. **规模/并发/性能**：跨进程并发提交未压测；大库吞吐、切片对超长页面的延迟、
   16 MiB artifact 上限与 2000 区域上限都只在小夹具上验证公式；60 MP 声明像素上限之外的
   行为只做了单测注入（非真实超大图）。
8. **断盘/离线来源**：沿用 P1-06 记录——「断盘」仍是重命名/移走来源根，未做真实 USB/SMB
   卷级故障；离线来源会得到 `BLOCKED` 且不覆盖既有文档（本任务只覆盖了缺件/改动/迟到路径）。
9. **GPU 未使用（设计如此）**：OCR 是 `non_model` + `CPU_PREFERRED`，未做 GPU/CPU 质量或
   性能对照；P2-06B 的精排/真实编码器欠项不受本任务影响。
10. **P0-04 权重许可**：再分发条款与 P0-04 记录一致（RapidOCR Apache-2.0、权重未逐文件核实），
    本任务未改变。

## 既有机械断言的调整（必须说明）

1. `tests/unit/test_catalog_migrations.py::CORE_TABLES` 增加 `asset_ocr_text`/`asset_ocr_regions`
   （严格的表集合断言，属版本耦合）；
2. `tests/integration/test_p2_scheduler_recovery.py::V6_TABLES`（历史名，实际是「当前完整表集」）
   同步增加两张 OCR 表；
3. `tests/integration/test_p2_search_feedback_migration.py` 的 `result.applied == (8,)` 改为
   `tuple(range(8, SUPPORTED + 1))`：本文件的主体仍是 0008 步，后续增量迁移追加到同一连续链。

另外一处**非版本耦合**的既有断点必须如实记录：
`tests/integration/test_catalog_identity_lifecycle.py::test_duplicate_content_shares_one_asset_and_one_artifact`
断言夹具路径长度 > 220，但该长度取决于 pytest 的 basetemp。仓库最近的
`f71c486 Align pytest configuration with local test environment rules` 把 basetemp 改成仓库内短路径
`.pytest-tmp`，使该断言只剩 217 字符而失败（用长 basetemp 单独跑同一用例可通过，已实测）。
修复方式保持原意：深目录按需补齐到 > 220 字符后再写文件，不再依赖临时根落在哪里。此改动只
影响夹具，不改变任何产品行为或断言强度。

## 交接记录（策略 §8.4）

```text
任务 ID / 状态：P3-01 / PASS（B + D + C）
实际模型 / reasoning / 速度：用户声明已在 DS-L（DeepSeek V4.1 Flash / 低推理）；客户端 config 未在本会话读取，未做也未声称自动切换
基线提交号：HEAD 3ea0cf1（P2-08 报告提交 446dbf7），工作树干净
已完成行为：新增 photo_search.ocr 包（固定权重校验、有界长边 2000px + 1280/160 切片、IoU 去重、确定性阅读顺序、低置信度保留、取消/预算检查、结构化 ocr_document.v1 产物、同事务持久化、受控 CLI 入口与只读状态投影）；新增迁移 0009（asset_ocr_text/asset_ocr_regions + 队列第五个 cause OCR_TEXT_CHANGED + 两个触发器，schema 8→9）；search_documents.ocr_text 经既有队列/sync_document 投递，且只在 input_revision=assets.sha256 时生效；/api/v1/capabilities 新增 ocr 段
修改文件与接口/迁移变化：新增 0009 与 ocr 包/测试/诊断工具；加性改动 errors/schema/scheduler.engine/scheduler.projection/search.documents/api.models/api.routes/packaged_api_smoke；未改 0001～0008、ADR/JSON schema、places/geo/ingestion/前端/打包入口/依赖（95 包）
实际命令和结果：pytest 877 例 0 失败 1 跳过（exit 0）；本任务新增 75 例；ruff/diff/lock clean；真实 onedir 重建 + packaged_api_smoke problems=[] 且 coverage_ocr_block=NOT_RUN/ocr-text@1；打包内含 0001～0009；真实夹具 invoice 5 区域、dense 20 区域/2 切片、blank 0 区域，发票图 FTS 命中 发票/1280/20260914
报告/详细日志路径：docs/reports/P3-01-ocr-text-regions-confidence-fts.md；JUnit：test-results/junit.xml；打包 smoke：D:\PhotoSearchSmoke\P3-01-report.json
未验证项和具体阻塞：真实复杂版面/手写/竖排/多语言与真实截图库规模；引擎去空格导致的数字段检索保真度；tokenizer 世代切换无法重新分词 OCR 文本；产品侧自动排队/常驻调度尚未接入；区域元数据尚无服务入口；跨进程并发与大库吞吐未压测；真实 USB/SMB 断盘未验证；无阻塞
失败修复轮次：0 个产品缺陷轮次；1 个测试夹具的环境耦合修复（长路径夹具）
下一任务及必须读取的文件：P3-02（人脸检测对齐、特征、候选索引、保守自动关联），任务卡 docs/tasks/P3-02.md，执行模型 DS-H（ONNX/Windows/性能环境问题转 C1，复用 P0-06 已验收 GPU runtime）；需读 AGENTS.md、docs/development-status.md、本卡、P0-04/P0-06 报告、主架构 §4.2/§4.3/§8.3/§9.2、策略 §3/§7/§8/§11/§12
可见 GPT 额度观察：未测量
DS 全任务请求用量/缓存/费用：客户端未提供用量明细；本任务脚本未直接调用 DeepSeek API
独立审查者与结果：本任务不需要独立审查
```

## 下一任务

**P3-02 — 人脸检测对齐、特征、候选索引、保守自动关联**（任务卡 `docs/tasks/P3-02.md`），
推荐 **DS-H**。它复用 P0-06 已验收的 GPU runtime，不重新做 GPU bring-up；
ONNX/Windows/性能类环境问题按策略转 **C1**。
