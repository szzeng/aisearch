# P4-MEDIA-CR2-A — 佳能 CR2 预览优先、统一画幅与历史补建

**状态**：IMPLEMENTATION PARTIAL / VALIDATION PENDING。**执行日期**：2026-10-13。**核对基线**：`szzeng/aisearch master @ 0f34c0b`（开工时重新读取 HEAD、`docs/development-status.md` 及工作区）。**推荐执行**：GPT-6 Luna / High。**验证**：个人相册，少量定向测试 + 3～5 张有授权的真实 CR2 人工试用；不跑全仓。**依赖**：`P4-LIBRARY-IMAGE-ONLY-A` 已 SCOPED PASS。

## 一、目标和边界

让图库内的佳能 `.cr2` 像正常照片一样：缩略图、1600px 预览、自然语言搜图、人物/人脸、OCR、拍摄时间与有证据的 GPS 地点均能参与现有流程；已收录但历史 `FORMAT_UNSUPPORTED` 的 CR2 能在“继续建库”流程中定向补建。**优先检查内置预览：足够清晰且画幅可靠则使用，否则 RAW 兜底；同一 CR2 的所有下游消费者须共用稳定的规范画幅。原文件只读**，不要求全库重建，不新增 CR2 设置页。

本卡**只增加 CR2 的处理能力**，不是重新设计图片入库。静态图片白名单已在 `src/photo_search/catalog/image_policy.py` 包含 `.cr2`，扫描/SQL 照片资格已完成；解码仍拒绝 TIFF/RAW。**禁止**仅将 `TIFF` 从拒绝集移动到支持集，更不能因扩展名为 `.cr2` 就接收伪装内容。

不扩展 `.cr3/.dng/.nef/.arw` 等其他 RAW，不做 RAW 编辑、上传图片查询中的 CR2 支持、RAW+JPEG 自动去重、GPU 解码专项、UI 重设计及全库迁移。

## 二、开工前必须核对的真实调用链

1. `media/contract.py` 中 `catalog-thumbnail@1`、`catalog-preview@1` 的 320/1600px 契约与缓存版本；`media/decode.py` 的 `load_normalized()/read_normalized_dimensions()/read_orientation()`，`media/pipeline.py` 的失败缓存与 `--retry-failed`。
2. `face/processor.py`、`ocr/processor.py` 共用媒体解码；`media/face_crop.py` 的 `recorded_face_frame()` 优先读取人脸检测文档的 `original_size`，无文档才 `probe_original_frame()`。不得误把 CR2 RAW 尺寸当成检测所用 JPEG 预览尺寸。
3. `indexing/semantic.py::image_eligibility()` 目前依据 Pillow 识别（CR2 常被识别为 TIFF）；`semantic_executor` 使用 `encoder.encode_image(path)`，`indexing/maintenance.py` 的 reexecute 也会直接传原图路径。核对当前活动 provider（Chinese-CLIP、Qwen3-VL 等）及其 worker 实际读图路径，**不能只放开资格判定**。
4. `refresh/pipeline.py` 的 daily 主要使用内容变化集，build/继续建库走分批范围；未变化的历史 CR2 不会因为新增解码器自然变成 delta。
5. `taken_time/exif.py`、`geo/extract.py` 的 Pillow EXIF 路径，`search/upload.py` 的独立上传白名单，以及 Windows 打包入口 `tools/packaging/build_onedir.py`。

复用以上边界，除确有必要外不新增第二套调度、预览缓存、位置解析或搜索服务。

## 三、实施方案

### A. 仅对真正 CR2 做受限解码

建议新增 `media/raw_cr2.py` 作为**唯一 CR2 适配层**。以 `rawpy/LibRaw` 为候选，使用与项目 Python 3.12.10 / Windows x64 兼容、锁定版本的 wheel；更新 `pyproject.toml` 和 `uv.lock`，审查打包时所需 DLL/二进制。禁止动态下载外部解码程序。

- **资格检测要轻量**：结合 CR2 特征头（TIFF 容器中的 Canon CR2 标记）和受控路径，独立分类 `CR2`；`.cr2` 假文件、普通 TIFF、DNG 继续 fail-closed。语义目标选择只做轻量头检查与运行时能力判断，不逐张 RAW `postprocess`。
- **像素优先内置 JPEG**：`rawpy.extract_thumb()` 可能提供 JPEG 或 Bitmap，尺寸由具体相机/文件决定，并非固定规格。按 A1 判据检查有效尺寸、方向、几何相容性；预览缺失、过小、损坏或无法安全使用时才执行 `rawpy.postprocess()` RAW 兜底。RAW 输出固定 sRGB、8-bit 参数；不放大低分辨率源。
- **跨消费者统一画幅**：对每张 CR2 基于文件内容与固定版本策略选定一个“规范像素源”，不因调用请求 320px、1600px、OCR 或人脸尺寸不同而切换。缩略图、预览、常规人脸、OCR、语义输入共用方向、裁剪边界与坐标基准，可各自降采样；不可将 RAW 传感器宽高直接当成预览图的尺寸。
- **方向只变换一次**：记录“预览已经旋转/RAW 旋转/EXIF 方向”中哪个实际生效；验证 1/6/8 方向和横竖样例。优先统一调用现有归一化结果及坐标约定。无检测文档的 `probe_original_frame()` 也须得到规范画幅尺寸。
- **资源与失败**：解码前对元数据尺寸/像素数作基本内存防护，限制 RAW 兜底并发；每文件关闭 rawpy 句柄，异常独立转为原有 `FORMAT_UNSUPPORTED/DECODE_FAILED` 等准确错误，不得把解码器缺件写成文件损坏。不能让单张坏 CR2 阻断整批，也不能影响 JPEG/HEIC 的导入和启动。

### A1. 内置预览与 RAW 的单文件确定性选择（重点验收）

佳能 CR2 可能包含全尺寸 JPEG、中尺寸预览或仅小缩略图。**分辨率不同不等于实际裁切不同**；不要求每张 CR2 先完整 RAW 解码再做图像比对。

| 观察结果 | 执行策略 |
| --- | --- |
| 有可正常读取的内嵌 JPEG/Bitmap，校正方向后长边 **≥1600px**，且与 RAW 有效画幅方向、比例及裁切坐标关系可靠 | **预览优先**；320px 缩略图、1600px 预览、普通人脸/OCR/AI 均使用同一预览取景基准 |
| 预览小于 1600px，或仅有极小缩略图 | **RAW 兜底**；源本身不足 1600px 则不放大 |
| 预览损坏、方向无法确认、缺失，或与 RAW 有效画幅明显不一致 | **RAW 兜底**；兜底失败则逐文件报告，不影响其他照片 |
| 无法确认 JPEG 与 RAW 的裁切关系 | 不得凭宽高比相同就假定可精确跨画幅映射；普通处理可统一使用规范预览，涉及 RAW 高精度人脸时选统一 RAW 或标记该项待处理 |

- **判据**：参考 LibRaw 的可见/有效像素区域，不用含黑边的 sensor `raw_width/raw_height` 直接代替照片画幅。相对宽高比差异 **≤1%** 可作为预筛建议值，执行者用真实样片确认/调整；这不是“同一裁切原点”的证明，若需跨画幅映射，必须掌握裁切偏移与缩放。
- **稳定性**：同一 SHA、同一策略版本，无论先生成缩略图、预览还是做人脸/OCR/语义，选择同一个规范像素源，不因调用者需要的输出尺寸而改变。可复用现有内容寻址衍生物；不引入新的全局缓存/数据库表。策略改动若影响旧 CR2 输出，需明确受影响 CR2 的重算与版本处理，不能隐性覆盖已有确认的产物。
- **source_detail 人脸例外**：现有 `face/processor.py` 有保留源图细节的路径，不得让其他格式的人脸精度回退。CR2 若采用 JPEG 规范预览但 source_detail 又要使用 RAW，必须证明对应裁切偏移/缩放并实测头像坐标；不能证明则该 CR2 统一使用 RAW，或以已选规范 JPEG 处理且**明确记录细节降级**，不得虚称 RAW 级精度或生成错位人脸框。
- **性能**：预览满足条件时不先完整 RAW 解码；RAW 兜底默认单张并发、受像素与内存上限保护，异常/取消及时释放句柄。输出非敏感计数 `cr2_preview_used / cr2_raw_fallback / cr2_decode_failed` 以及样片方向/宽高对照，不记完整本地路径。

如内嵌 JPEG 与 RAW 的取景范围不一致，优先统一为 RAW 规范画幅或拒绝不安全的下游坐标计算；**绝不能悄悄混用两种画幅产生错位人脸**。不增加永久 RAW 副本。

### B. 接入媒体、人物、OCR 与时间地点

仅为 CR2 在 `load_normalized`/规范尺寸入口增加适配；不改 `plugin_format_decision('TIFF') == ('TIFF', False)` 现有语义，也不全局开放 RAW。沿用缩略图和预览的缓存路径、原子发布和内容 SHA 身份。若旧 CR2 只有失败缓存，按已有重试机制恢复；**不要为增加支持格式而全局提升 thumbnail/preview processor ID**，除非有清楚证据证明现有已发布图片输出语义发生变化。

`face/processor.py`、`ocr/processor.py` 应透过共享 CR2 入口获得规范像素，保留现有人脸检测文档 `original_size`、BBOX 和裁脸缩放契约；不存在可信坐标时不猜。保留手工人物归属、忽略、合并与撤销记录，不自动重分类既有人工结果。

时间/GPS 先用真实样片验证当前 Pillow EXIF 是否可读；仅不兼容时增加 CR2 专门的最小读取后备，保留 `DateTimeOriginal`、子秒和时区的原有置信度逻辑，不从文件修改时间推造“相机拍摄时间”，无 GPS 仍按 ABSENT/原有错误处理。元数据始终读取**原始 CR2**，不能用临时 JPEG 缺失的 EXIF 覆盖拍摄时间或 GPS。

### C. 语义搜索真正贯通两个方向

- `image_eligibility()` 对验证通过、运行时可解码的 CR2 返回可参与建库；不能将同类 TIFF 一并放行。
- **在语义编码器公共输入边界**，仅对 CR2 转换为临时的规范 RGB JPEG/PNG，然后传给既有 `encode_image(path)`；转换图必须来自同一规范画幅，失败/取消后清理临时文件。先核实当前仓库真实存在且已启用的 provider/worker，不凭旧报告假定某适配器存在；避免改 Chinese-CLIP/Qwen 等模型参数、预处理尺寸、向量维度、空间/processor identity 或搜索排序。
- 覆盖普通语义建库、增量刷新与 `indexing/maintenance.py` 的 reexecute；不要只在某一个模型适配器的 `Image.open` 中做兼容。
- 对已 CONFIRMED 的同 SHA 历史 CR2 向量**直接保留**，不能复写不可变 artifact 触发 `ARTIFACT_CONFLICT`。如证实旧向量由与规范像素不兼容的历史处理生成，先报告受影响项及后续独立重建方案，不擅自换代整个向量空间。

此处的“能搜”是**图库 CR2 作为结果被文字检索**；搜索栏上传 CR2 作为查询图属于另一套受限白名单，本卡不动。

### D. 历史 CR2 补建路径

要求明确实现“已存在但失败/被跳过的 CR2 如何恢复”：

1. 复用现有 build/“继续建库”、`catalog thumbnail/preview --retry-failed` 和任务幂等恢复，不设计第二条全库建库流程。
2. 在**显式继续建库**中复用现有 build 的分批范围遍历，对已登记 CR2 做有界、可续游标的历史缺口检查（无缩略图、旧 `FORMAT_UNSUPPORTED`、缺语义索引/人脸/OCR 等），仅调度缺少的派生结果；内容未变也允许补建。尽可能在来源/CR2 候选筛选阶段收窄范围；日常普通刷新不反复全表扫描历史。
3. 仅定向重试旧 CR2 格式不支持的任务/失败缓存，**不可全局设置 `retry_failed=True`** 重试其他失败 JPEG/HEIC；其他错误保持既有处理。成功发布/已确认 artifact 必须跳过，避免 immutable artifact 冲突；不重置人工人物、地点、合集、忽略与撤销历史。
4. 如果现有 UI“继续建库”只触发部分环节，最小修正该入口的调度；不要新加设置页。给出 UI 使用步骤及必要时现成 CLI 的兜底命令。缺少适用的实际任务状态时不虚构成功。

历史补建的接受标准是 CR2 有进度、有缺口统计、可中断继续；成功 JPEG 和旧向量不重算。

## 四、最小验收（4 组，不跑全仓）

1. **解码和选择规则**：假 CR2、普通 TIFF、DNG、损坏 CR2 不被误放行；覆盖足够大的 JPEG、小于 1600px 预览、Bitmap、缺内嵌图、方向异常、几何不相容与 RAW 兜底。相同文件不同调用顺序（缩略图/预览/普通人脸/OCR/语义）选择一致；资格探测不解整张 RAW，解码器不可用时 JPEG 仍正常。
2. **真实方向/人物**：使用 3～5 张获授权真实 CR2，尽量含不同预览尺寸、横竖拍、人脸、时间及有/无 GPS；记录内嵌图与 RAW 有效画幅尺寸、宽高比、方向及选择结果。检查普通/source_detail 人脸框和头像坐标、OCR 能执行、原文件 SHA/mtime 不变。样片未覆盖的分支记为 PENDING。
3. **语义/补建**：小型隔离图库与假 encoder 验证 CR2 规范输入、历史不可索引变为可索引、临时文件清理、已 CONFIRMED 幂等、`reexecute`；若存在可用活动模型，再用 **1 张 CR2 实测文字搜图**，未测标 MODEL VALIDATION PENDING。
4. **历史与回归**：预置 CR2 旧格式失败缓存/任务、**一个非 CR2 失败缓存**、不变 JPEG 与已发布 CR2 artifact；“继续建库”只补 CR2 缺口，不动其他失败，第二次无重复，日常刷新不扫历史；跑 JPEG/HEIC/MPO、媒体、人脸、OCR、语义的少量受影响测试。

仅运行涉及模块的 pytest、Ruff、`python -m py_compile`、`git diff --check`、`uv lock --check`；若修改 PyInstaller 收集规则，做轻量打包导入/解码验证，不强制全量正式安装包。**不访问真实 `PhotoSearchData`、不执行数据库破坏性操作、不做整库 GPU/20 万性能与全仓回归**。没有授权真实样片不得假造 E2E PASS。

## 五、交付和停止点

- 代码、锁定依赖、受影响测试；`docs/reports/P4-MEDIA-CR2-A.md` 写明内置预览与 RAW 有效画幅的样片对照、预览优先/RAW 兜底计数、方向/人脸坐标证据、定向历史补建、活动 provider 和未测内容；更新 `docs/development-status.md`。
- **SCOPED PASS** 需：真实 CR2 能显示且方向正确；有证据表明合格预览优先、不合格 RAW 兜底；人物/人脸（含执行到的 source_detail）和 OCR 坐标正确；一个实际活动模型的 CR2 文字搜图验证通过；历史格式拒绝安全恢复；JPEG/HEIC/MPO 不回退、原图及人工归属零修改。
- 若缺真实 CR2 或活动模型/打包条件，允许完成文档/代码/合成验证，但对应项目标 `VALIDATION PENDING`，不得写完整 PASS。
- 发现必须迁移 Schema、替换全局向量空间、整体改写搜索或人脸系统才能完成时，**停止扩大范围并报告阻塞**，不要在本卡自行实施。

**推荐：GPT-6 Luna High 独立完成，按当前仓库最小实现，不单独要求 DSH/全套回归。**

## 2026-10-13 执行记录

已落地 RAW 解码、共享规范画幅接入、语义规范临时输入、PyInstaller rawpy 收集以及历史媒体 `FORMAT_UNSUPPORTED` 定向重试。定向组 A **37 passed**、定向组 B **90 passed**，Ruff、py_compile、`uv lock --check`、`git diff --check` 通过。详见[执行报告](../reports/P4-MEDIA-CR2-A.md)。

本次没有收到已授权样片目录路径；内嵌预览暂未启用，实际只验证合成输入和假 encoder。真实 CR2 画幅/方向、人脸/OCR/时间/GPS、活动模型搜图、真实历史续建与完整打包仍待验证；人脸/OCR 旧终态任务的 CR2 定向恢复也未完成。**不可记 SCOPED PASS**。收到样片路径后继续本卡，不新开后续任务卡。

### 样片续验（2026-10-13）

用户已授权 `D:\SZ\天空\100CANON`。只读抽取 5 个分位 CR2 样片：全部为 6000×4000 JPEG 内嵌预览与 6024×4020 RAW 有效画幅，LibRaw `flip=0`。SIFT/RANSAC 几何诊断未能对全部样片证明稳定裁切映射，故继续统一使用 RAW，`cr2_preview_used=0`、`cr2_raw_fallback=5`、`cr2_decode_failed=0`；单张解码均值 1.130 秒，原文件 SHA/大小/mtime 未变化。共享人脸/OCR解码入口均返回 RAW 规范画幅；真实模型推理未运行。时间与子秒可读，但样片不含有效 GPS 坐标，未覆盖竖拍/有效 GPS。定向测试 **127 passed**（JUnit `test-results/junit-p4-media-cr2-a-validation.xml`），Ruff、`py_compile`、`uv lock --check`、`git diff --check`、PyInstaller rawpy collector smoke 均通过。活动模型文字搜索、实际续建幂等、旧人脸/OCR终态任务定向恢复及完整打包仍待验证；详见[执行报告](../reports/P4-MEDIA-CR2-A.md)。本卡保持 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**，不标 SCOPED PASS。
