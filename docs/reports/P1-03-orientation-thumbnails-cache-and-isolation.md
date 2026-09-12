# P1-03 — 方向、缩略图/预览、缓存分片、坏图隔离

状态：**PASS**（B 级验收全部真实完成；产物发布与中断按 D 级做了故障注入）。
基线：**372d21c**（P1-02 收口提交，`git log -1 --format=%H -- docs/reports/P1-02-*.md`）。
执行模型（记录用）：**DS-L / DeepSeek V4.1 Flash / 低推理**。模型切换始终由用户在 Codex 中手动完成，
本任务未尝试、也未声称自动切换。

冻结契约：**ADR-0002 未修改**；`schema_version` 仍为 **1**（未新增迁移）；未新增依赖
（`pyproject.toml` / `uv.lock` 未改动）。

---

## 1. 交付物清单

| 交付物 | 位置 |
|---|---|
| 派生媒体包（契约、解码/方向、缓存、流水线、状态） | `src/photo_search/media/{contract,decode,cache,pipeline,status}.py` |
| 数据目录解析（`thumbnails/`、`previews/`、`cache/media/`） | `src/photo_search/config.py` |
| 内部入口 `catalog thumbnail` / `catalog preview` / `catalog media-status` | `src/photo_search/ingestion/cli.py` |
| 服务门槛（批量、单资产、状态、磁盘守卫注入点） | `src/photo_search/ingestion/service.py` |
| 媒体结构化错误码 | `src/photo_search/catalog/errors.py`（新增 `SOURCE_FILE_MISSING`、`DECODE_FAILED`、`FORMAT_UNSUPPORTED`、`ARTIFACT_WRITE_FAILED`、`MEDIA_INTERRUPTED`、`ASSET_NOT_FOUND`） |
| 单元测试（解码/方向/格式策略；缓存/原子发布/失败记录） | `tests/unit/test_media_decode.py`、`tests/unit/test_media_cache.py` |
| 集成测试（真实临时夹具、缓存重建、隔离、中断、CLI） | `tests/integration/test_catalog_media.py` |
| 规模/性能只读探针 | `tools/diagnostics/media_scale_probe.py` |
| 本报告、状态更新、下一任务卡 | `docs/reports/P1-03-*.md`、`docs/development-status.md`、`docs/tasks/P1-04.md` |

未改动：ADR-0002、`models/**`、`runtime/**`、`workers/**`、既有迁移 `0001_initial.sql`、P0/P1-01/P1-02 报告、
模型清单、`pyproject.toml`、`uv.lock`。

## 2. 设计决策：为什么本任务不新增表（任务卡开始前检查项的结论）

任务卡要求在「新增只增不改的派生媒体表」与「只靠文件系统 + 结构化结果」之间做出有依据的选择。
本任务选择**不新增表、不写入 catalog**，理由如下：

1. **架构把缩略图/预览判为最低优先级的可重建缓存**（架构 §4.4 第 5 级、§11.1）。把缓存可用状态写进
   P1-01 的受保护数据库，会让「可整体删除重建」的东西拥有第二份权威状态，出现「库里 READY、文件已删」的
   双源不一致，需要在读取路径上不断对账。
2. **artifact 文件本身就是诚实的 READY 标记**：最终文件名只通过「写完 + flush + fsync + 同目录原子替换」
   产生（架构 §5.4「半成品不标记为 READY」）。不存在「先标记 READY 再写文件」的窗口，也不需要迁移。
3. **不引入迁移就完全不动 ADR-0002 §7 的语义**：不触发「已有库需备份 + 64 MiB 余量 + 版本推进」的升级路径，
   不会让已有用户为一个可丢弃的缓存付出一次单向库升级。
4. **P1-04/P1-05 如何判定「已生成 / 需重建」**：`media-status` 用处理器版本 + 内容指纹定位期望路径，
   `published`（最终文件存在且非空）= 可用；否则 `pending`（有可读位置，需生成）；位置不可达则
   `source_missing`；失败原因从结构化失败记录（`cache/media/` 下的 JSON）枚举。三者与批量生成路径使用
   同一套判定，不重复解码、不猜。
5. `assets.width/height/format` 仍为 **NULL**：ADR-0002 §3.5 明确它们是「内容派生信息、不参与身份」，
   P1-03 不再写库可让派生媒体路径与受保护数据彻底解耦；方向修正后的实际尺寸与源格式在批量汇总里返回，
   P1-04/05 也可直接从 artifact 读取。是否落库留给后续任务按真实 UI 需求决定（需要时按 ADR-0002 §7 走迁移 + C2）。

本任务也**不创建 jobs 行**：媒体产物是可重建缓存，每个资产每种产物各建一行会给受保护库增加大量行，
而任务卡的批量入口本身「可重复执行且可中断」，其成功/失败/不支持计数与原因在 `--json` 汇总里完整给出
（任务卡 §5 的 `jobs.progress` 规则是「若写入」才适用；本任务不写入，因此不存在绕过 ADR-0002 §6.5 的可能）。

## 3. 媒体契约与实测值

### 3.1 Pillow 能力实测（Pillow 11.3.0，本机真实探测，非记忆）

`probe_capabilities()` / `tools/diagnostics/media_scale_probe.py --json` 的 `capabilities` 字段：

| 能力 | 实测结果 |
|---|---|
| WebP 保存 `features.check("webp")` | **True**（本机可用，但 P1-03 未把 WebP 作为交付目标，故**不声称支持** WebP 输出） |
| JPEG 编解码 `features.check("jpg")` | True |
| TIFF 读取 `features.check("libtiff")` | **True**（Pillow 能读 TIFF：实测 `Image.open` + `load()` 均成功） |
| zlib `features.check("zlib")` | True |
| EXIF 方向读取 `ImageOps.exif_transpose` | 存在且可用；`Image.Exif` 可写（夹具用） |
| `Image.MAX_IMAGE_PIXELS` | 89,478,485（≥ 该值仅告警，≥ 2 倍抛 `DecompressionBombError`） |
| JPEG DCT 缩尺解码（`Image.draft`） | 可用（实测 3200×2400 请求 320 长边 → 解码为 800×600，内存降到 1/16） |

**格式策略**（内容判定，不看扩展名）：解码 `JPEG / PNG / WEBP / BMP / GIF`；
`TIFF（含 NEF/ARW/DNG/CR2 等 TIFF 族）/ MPO / HEIF / HEIC / AVIF / JXL / ICO / PSD / TGA / PPM / PGM / PBM /
PDF / EPS / RAW(CR3)` 一律 `FORMAT_UNSUPPORTED`。TIFF 被拒是**契约要求**（架构 §5.4「TIFF/RAW 未验证前显示格式暂不支持，
不把失败伪装成完成」），而不是 Pillow 不能读。

### 3.2 方向与尺寸契约

- 每个输入先按 EXIF Orientation（1～8，含 3 组镜像变体）归一化，再做任何缩放；归一化后的像素帧是产物与
  后续坐标/尺寸结果的唯一基础。
- 网格缩略图：长边 **320 px**、JPEG q85、LANCZOS、**不放大**（长边 < 320 px 的输入保持原尺寸）。
- 详情预览：长边 **1600 px**、JPEG q88、按需懒生成、可单独清理。
- 原图只读：只以 `rb` 打开；发布前后各校验一次位置行的 `size`/`mtime_ns`，与库中观测不一致时放弃本次产物
  （`INPUT_UNSTABLE`），因此不会把「新内容的像素」挂到「旧内容身份」下。
- 透明度输入合成到白底后按 RGB 存 JPEG；灰度输入保持 `L` 存 JPEG；CMYK/16 位等一律转 RGB（**不做色彩管理**，见 §8）。

### 3.3 实测典型文件大小与耗时（临时夹具，`media_scale_probe.py`）

| 夹具 | 张数 | 缩略图 | 预览 |
|---|---|---|---|
| 合成平滑 1600×1200 JPEG | 200 | 1,776 ms 总 / **8.88 ms/张** / 112.6 张·s⁻¹ / **3,499 B/张** | 2,624 ms 总 / **13.12 ms/张** / **43,981 B/张** |
| 合成平滑 1280×960 JPEG | 500 | 3,584 ms 总 / **7.17 ms/张** / 139.5 张·s⁻¹ / **3,587 B/张** | 未测（本次未开 `--preview`） |
| 合成含高斯颗粒 4032×3024 JPEG | 20 | 785 ms 总 / **39.25 ms/张** / 25.5 张·s⁻¹ / **6,285 B/张** | 3,351 ms 总 / **167.55 ms/张** / **597,096 B/张** |
| 合成 12000×9000（108 MP）JPEG | 1 | **36 ms** → 320×240 | **97 ms** → 1600×1200 |

说明：夹具为合成图（平滑色带 + 色块；`--noisy` 追加高斯颗粒），只用于**实测**吞吐/耗时/体积，不代表真实
照片分布；真实照片体积通常落在平滑与颗粒之间。任务卡要求的「实测典型文件大小与生成耗时」由该表与
§5 的命令与退出码共同支撑。

内存（Windows 峰值工作集，`GetProcessMemoryInfo`）：

- 500 张 1280×960：媒体阶段增量 **0.72 MiB**（总峰值 32.1 MiB）；
- 200 张 1600×1200：媒体阶段增量 0（总峰值 43.5 MiB，扫描阶段已更高）；
- 108 MP 单张（夹具由子进程写入，保证基线干净）：解码+编码峰值增量 **53.6 MiB**（总峰值 80.2 MiB）。
  若按全分辨率 RGB 解码，仅像素缓冲就需要约 324 MiB；`Image.draft` 缩尺解码把这一项降到 1/64 左右。

## 4. 缓存布局、原子发布与失败记录

```text
<data_root>/thumbnails/catalog-thumbnail@1/<sha256[:2]>/<sha256[2:4]>/<sha256>.jpg
<data_root>/previews/catalog-preview@1/<sha256[:2]>/<sha256[2:4]>/<sha256>.jpg
<data_root>/cache/media/<processor_id>/<sha256[:2]>/<sha256[2:4]>/<sha256>.json   # 仅失败原因
```

- **内容寻址**：文件名是完整 SHA-256（content identity），因此同内容多位置/多来源天然共享一个产物；
  「两级目录分片」= `[:2]` 与 `[2:4]`。
- **处理版本进路径**：`catalog-thumbnail@1` / `catalog-preview@1` 冻结了长边、输出格式、质量、重采样、
  方向策略与 draft 策略；版本变化必然换目录，旧版本产物可分辨且绝不混用（测试覆盖）。
- **原子发布**：临时文件写在目标分片目录内（`<sha256>.<pid>-<token>.tmp`），写完 `flush` + `fsync` 后
  `os.replace` 到最终名；失败路径删除自己的临时文件；替换成功后再核对最终文件大小。
- **失败记录**：只对**确定性内容结论**（`DECODE_FAILED`、`FORMAT_UNSUPPORTED`）写结构化 JSON
  （`code`、`message`、`format`、`input_size`、`input_mtime_ns`、`source_id`、`locations_tried`、`created_at`）。
  同一字节的确定性失败在后续批量中跳过重解码（可 `--retry-failed` 强制重试）；`SOURCE_FILE_MISSING`、`INPUT_UNSTABLE`
  属瞬时状态，**不写记录**（避免把「断盘」写成「坏图」）。记录文本已剥离来源根的绝对路径，不含图片内容或 OCR 类文本。
- 删除整个 `thumbnails/`、`previews/`、`cache/media/` 后可从 catalog 重建；不触碰资产身份、位置关系或任何人工数据
  （`assets/`、`file_locations/`、`jobs/`、`scan_runs/` 的行在媒体生成前后逐行相同，测试断言）。

## 5. 实际运行的命令与退出码

```text
uv run pytest -q tests/unit tests/integration            # exit 0，180 passed, 1 skipped in 111.20s
uv run ruff check .                                       # exit 0，All checks passed!
git diff --check                                          # exit 0，无输出
.venv\Scripts\python.exe -m pytest -q tests/unit/test_media_decode.py tests/unit/test_media_cache.py
                                                          # exit 0，27 passed
.venv\Scripts\python.exe -m pytest -q tests/integration/test_catalog_media.py
                                                          # exit 0，19 passed
```

规模/性能（只读探针，夹具与数据根都在临时目录；无真实图库访问）：

```text
python tools/diagnostics/media_scale_probe.py --files 200 --preview --json
  200 files 1600x1200  scan 1786 ms  thumbnail 1776 ms (8.88 ms/file, 112.6/s, 3499 B/artifact)
                       preview 2624 ms (13.12 ms/file, 43981 B/artifact)  peak 43.5 MiB  delta 0 MiB
python tools/diagnostics/media_scale_probe.py --files 500 --image-size 1280x960 --json
  500 files 1280x960   scan 2376 ms  thumbnail 3584 ms (7.17 ms/file, 139.5/s, 3587 B/artifact)
                       peak 32.1 MiB  delta 0.72 MiB
python tools/diagnostics/media_scale_probe.py --files 20 --image-size 4032x3024 --noisy --preview --json
  20 files 4032x3024   scan 1630 ms  thumbnail 785 ms (39.25 ms/file, 6285 B/artifact)
                       preview 3351 ms (167.55 ms/file, 597096 B/artifact)
python tools/diagnostics/media_scale_probe.py --files 0 --huge --json
  108 MP source 1,725,759 B → thumbnail 36 ms 320x240，preview 97 ms 1600x1200，
  媒体阶段峰值增量 53.6 MiB（子进程写夹具，基线干净）
```

退出码契约（沿用 P1-01/P1-02）：批量跑完 **0**（内含被隔离的单文件失败，计数与原因在 `--json` 汇总）；
结构化 `CatalogError`（`DISK_SPACE_INSUFFICIENT`、`ARTIFACT_WRITE_FAILED`、`ASSET_NOT_FOUND`、`MEDIA_INTERRUPTED` 等）**1**；
用法错误（未知/非法参数、`preview` 未给 `--asset-id`/`--limit`、负数 limit）**2**。

## 6. 任务卡要求真实执行的媒体场景（1～10）与映射

| # | 场景 | 结果 | 证据（测试） |
|---|---|---|---|
| 1 | EXIF Orientation 2/3/6/8（含镜像变体）→ 方向正确 | PASS（1～8 全测；与独立变换表逐像素一致，镜像/旋转同时由四象限颜色与尺寸区分） | `test_media_decode.py::test_every_exif_orientation_is_normalised_before_any_resize`、`test_catalog_media.py::test_thumbnail_batch_publishes_orientation_correct_jpegs` |
| 2 | 损坏/截断 JPEG → 单失败隔离、结构化原因、整批完成 | PASS（`DECODE_FAILED` + 记录，其余文件继续生成） | `test_broken_and_unsupported_inputs_are_isolated_with_reasons`、参数化 `test_undecodable_content_is_a_decode_failure` |
| 3 | 0 字节 / 非图片内容 / 不支持 RAW(TIFF) | PASS（0 字节与文本 → `DECODE_FAILED`；TIFF/NEF/CR3/HEIF → `FORMAT_UNSUPPORTED`，绝不伪装成功） | 同上 + `test_unverified_formats_are_reported_as_unsupported` |
| 4 | 生成中被杀 → 无 READY 半成品；重跑补齐且不重复生成 | PASS（`interrupt_after` / cancel / `KeyboardInterrupt` / `os.replace` 失败四种注入） | `test_interrupted_batch_publishes_nothing_half_written_and_resumes`、`test_a_cancelled_or_interrupted_run_writes_nothing`、`test_a_failed_artifact_write_aborts_without_a_ready_artifact`、`test_a_leftover_temporary_file_is_never_a_ready_artifact` |
| 5 | 删除整个缓存目录 → 可重建，资产/位置/人工数据不变 | PASS（逐行比对六张核心表不变，原图 size/mtime 不变，重建成功） | `test_deleting_the_cache_rebuilds_without_touching_catalog_or_originals` |
| 6 | 重复请求同一资产（批量 + 单资产）→ 幂等 | PASS（不重算、不重复文件、产物 mtime 不变；确定性失败也不重复解码） | `test_repeat_runs_and_single_asset_requests_are_idempotent`、`test_cli_thumbnail_output_stays_stable_across_repeated_runs` |
| 7 | 所有位置 MISSING / 来源离线 → 已有缩略图仍可用；未生成时返回「原文件不可用」 | PASS（`SOURCE_FILE_MISSING`，不写失败记录，不产生删除结论） | `test_unavailable_sources_keep_published_artifacts_and_report_the_rest`、`test_an_offline_source_still_serves_published_thumbnails` |
| 8 | 中文/空格/长路径来源 + 只读原图 → 全部成功，原文件属性不被修改 | PASS（>220 字符真实 Windows 路径 + 中文空格目录；只读位保留，来源树 size/mtime 完全一致） | `test_read_only_originals_and_awkward_paths_are_generated_untouched`、`test_media_generation_never_writes_inside_a_source_root` |
| 9 | 磁盘空间不足（注入 `disk_usage`）→ 结构化错误 + 非零退出码，既有产物不被删除 | PASS（`DISK_SPACE_INSUFFICIENT`，已发布产物保留，恢复后补齐） | `test_disk_shortage_stops_the_batch_and_keeps_existing_artifacts` |
| 10 | 规模/性能：200～1000 张真实 JPEG 夹具，吞吐/单张耗时/缓存占用/峰值内存 | PASS（见 §3.3、§5；探针只写临时目录） | `tools/diagnostics/media_scale_probe.py` |

额外覆盖（任务卡边界情况）：极小图不放大、PNG/BMP/GIF/WEBP/灰度输入、Alpha 合成、同内容多位置
（一个已消失仍能用另一个）、旧处理版本产物不混用、扫描后内容变化不污染旧身份、
`--json` 与结构化错误/退出码、`media-status` 失败原因枚举。

## 7. D 级产物发布故障注入结果

任务卡规定：新增 schema 迁移（0002）**或任何跨事务的产物发布**都要补做 D 级故障注入。本任务**未新增迁移**，
但存在「文件系统产物发布」这一跨事务动作，因此按 D 级执行了对应注入：

| 注入 | 期望 | 实测 |
|---|---|---|
| 迁移中途失败 → 回滚且版本号不前进 | 不适用（无 0002；`schema_version` 仍为 1） | 不适用；既有 P1-01 注入测试 `test_failed_migration_rolls_back_and_keeps_the_old_version` 在本次全量运行中仍 PASS |
| 产物写入中断（`os.replace` 抛错） | 最终名不存在 READY 半成品；临时文件被清理；已发布产物不受影响 | PASS：`ARTIFACT_WRITE_FAILED`，`.jpg` 0 个、`*.tmp` 0 个；恢复后重跑生成成功 |
| 批量中途被杀（`interrupt_after` / cancel / Ctrl-C） | 只留下完整产物；无半成品；重跑补齐且不重复 | PASS：2/4 完成，`*.tmp` 0 个，重跑 `generated=2, already_published=2` |
| 残留临时文件（模拟上次被杀） | 绝不被当作可用缩略图；可观测、可清理 | PASS：`is_published` 为 False、`temp_files=1`、`--prune-temp` 删除后 artifact 不受影响 |
| 重跑一致性 | 结果一致、不重复、不重算 | PASS：产物 mtime 不变，`already_published` 计数正确 |

## 8. 未验证项、限制与「不可声称」

1. **C2 独立复核未执行。** 本报告是 DS-L 的实现与验证证据，不是复核结论。本任务未改身份/唯一约束/迁移/WAL/状态机/
   缺失判定语义；若后续要新增派生媒体表（例如把 P1-04 的 UI 需求落库），必须按 ADR-0002 §7 走迁移并交 C2。
2. **TIFF/RAW/HEIF 明确不支持。** Pillow 实测能读 TIFF，但 P1-03 按契约拒绝（多页、色彩、RAW 语义未验证）。
   在专门任务验证前，不得声称「已支持 TIFF/RAW/HEIC」，也不得把 `FORMAT_UNSUPPORTED` 当成已完成的产物。
3. **无色彩管理。** ICC profile 未应用，输出按朴素 sRGB 视作；CMYK、16 位灰度等一律直接转 RGB；透明通道合成到白底。
   这些是显式取舍，不得声称「色彩与原图一致」。
4. **JPEG draft 缩尺解码。** 源图较大时先按 ≥ 目标长边的 DCT 比例解码（不会解码到小于目标），再 LANCZOS 缩放；
   预览在极端大图上会略低于全分辨率来源的细节（换来约 1/64 的像素缓冲）。不得声称「预览与全分辨率解码逐像素一致」。
5. **`assets.width/height/format` 仍为 NULL。** 本任务不写 catalog（见 §2）。方向修正后的尺寸与源格式在批量汇总返回，
   也可从 artifact 读取；是否持久化留给后续任务决定。
6. **单线程、单解码。** 实测 112.6～139.5 张·s⁻¹（1600×1200 平滑）、25.5 张·s⁻¹（4032×3024 颗粒）；
   未按架构 §6.4「先 2 个解码进程再按测量调整」建池，属 P2 调度任务范围。
7. **`media-status` 为 O(资产数) 诊断命令**：对未生成资产做一次廉价存在性探测，并遍历当前版本的缓存目录统计体积；
   未做增量维护。超大库的实时进度/状态需要 P2 的调度与索引世代工作。
8. **未验证**：真实数十万～百万级图库、网络盘/外接盘反复插拔、跨进程并发同批生成、真实照片分布下的产物体积与质量、
   生产级并发与前台争用。未做任何真实照片库扫描（任务卡要求）。
9. **不可声称**：「缩略图质量已通过人工评估」「TIFF/RAW/HEIC 已支持」「色彩与原图一致」「媒体流水线已完成生产级并发验证」
   「P1-04/P1-05 已完成」。

## 9. 交接记录

```text
任务：P1-03 方向、缩略图/预览、缓存分片、坏图隔离
状态：PASS（B 级验收 + 产物发布/中断故障注入；C2 复核未执行，如需独立审查请按 V3.1 §11 指定 C2）
基线：372d21c（P1-02 收口）
冻结契约：ADR-0002 未修改；schema_version 仍为 1；未新增依赖
媒体契约：缩略图 catalog-thumbnail@1（长边 320，JPEG q85，LANCZOS，不放大）；预览 catalog-preview@1（长边 1600，JPEG q88，懒生成）
缓存布局：<data_root>/{thumbnails,previews}/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.jpg；失败记录 <data_root>/cache/media/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.json
原子发布：同目录临时文件 + flush/fsync + os.replace；半成品永不出现于最终名
失败隔离：DECODE_FAILED / FORMAT_UNSUPPORTED 记结构化记录并跳过重解码（可 --retry-failed）；SOURCE_FILE_MISSING / INPUT_UNSTABLE 为瞬时状态不落记录
入口：catalog thumbnail | catalog preview（懒，需 --asset-id 或 --limit）| catalog media-status（--failures/--prune-temp）
下一任务：P1-04 — sources/assets API、受控文件访问、本机会话保护（DS-H / DeepSeek V4.1 Flash / 高推理；
          Windows 路径与本地访问边界由 C1 / GPT-5.6 Terra / medium 聚焦检查）
```
