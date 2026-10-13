# P4-MEDIA-CR2-A — 佳能 CR2 图片显示、AI 处理与历史补建

**状态**：READY / 仅任务卡，未实施。**日期**：2026-10-13。**核对基线**：`szzeng/aisearch master @ e275c54`（开工时重新读取 HEAD、`docs/development-status.md` 及工作区）。**推荐执行**：GPT-6 Luna / High。**验证**：个人相册，少量定向测试 + 3～5 张有授权的真实 CR2 人工试用；不跑全仓。**依赖**：`P4-LIBRARY-IMAGE-ONLY-A` 已 SCOPED PASS。

## 一、目标和边界

让图库内的佳能 `.cr2` 像正常照片一样：缩略图、1600px 预览、自然语言搜图、人物/人脸、OCR、拍摄时间与有证据的 GPS 地点均能参与现有流程；已收录但历史 `FORMAT_UNSUPPORTED` 的 CR2 能在“继续建库”流程中定向补建。**原文件只读**，不要求全库重建，不新增 CR2 设置页。

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
- **像素优先内置 JPEG**：`rawpy.extract_thumb()` 可返回 JPEG 字节或 Bitmap；检查尺寸、有效性、横竖方向和裁切。无内嵌图、图损坏或不足以满足选定统一画幅策略时，才使用 `rawpy.postprocess()` 兜底；RAW 输出固定 sRGB、8-bit 的可重复参数。不要因为 1600px 上限而放大低分辨率源。
- **跨消费者统一画幅**：对同一 CR2 根据文件内容和稳定策略确定一个“规范像素源”，而非因调用请求 320px 或 1600px 而在内嵌 JPEG 与 RAW 之间随意切换。确保缩略图、预览、人脸检测、OCR、语义输入具有一致方向、构图/裁剪与比例；若预览与 RAW 活动区域不同，必须明确映射或统一改用 RAW，不能发布错位人脸框。
- **方向只变换一次**：记录“预览已经旋转/RAW 旋转/EXIF 方向”中哪个实际生效；验证 1/6/8 方向和横竖样例。优先统一调用现有归一化结果及坐标约定。无检测文档的 `probe_original_frame()` 也须得到规范画幅尺寸。
- **资源与失败**：解码前对元数据尺寸/像素数作基本内存防护，限制 RAW 兜底并发；每文件关闭 rawpy 句柄，异常独立转为原有 `FORMAT_UNSUPPORTED/DECODE_FAILED` 等准确错误，不得把解码器缺件写成文件损坏。不能让单张坏 CR2 阻断整批，也不能影响 JPEG/HEIC 的导入和启动。

若真实样片显示内嵌 JPEG 与 RAW 严重不一致，宁可选统一 RAW 路径或把该文件报告为不可处理，也不能悄悄混用产生错误人脸/搜索数据。记录采用内嵌图或 RAW 兜底的数量即可，不增加永久原图副本。

### B. 接入媒体、人物、OCR 与时间地点

仅为 CR2 在 `load_normalized`/规范尺寸入口增加适配；不改 `plugin_format_decision('TIFF') == ('TIFF', False)` 现有语义，也不全局开放 RAW。沿用缩略图和预览的缓存路径、原子发布和内容 SHA 身份。若旧 CR2 只有失败缓存，按已有重试机制恢复；**不要为增加支持格式而全局提升 thumbnail/preview processor ID**，除非有清楚证据证明现有已发布图片输出语义发生变化。

`face/processor.py`、`ocr/processor.py` 应透过共享 CR2 入口获得规范像素，保留现有人脸检测文档 `original_size`、BBOX 和裁脸缩放契约；不存在可信坐标时不猜。保留手工人物归属、忽略、合并与撤销记录，不自动重分类既有人工结果。

时间/GPS 先用真实样片验证当前 Pillow EXIF 是否可读；仅不兼容时增加 CR2 专门的最小读取后备，保留 `DateTimeOriginal`、子秒和时区的原有置信度逻辑，不从文件修改时间推造“相机拍摄时间”，无 GPS 仍按 ABSENT/原有错误处理。

### C. 语义搜索真正贯通两个方向

- `image_eligibility()` 对验证通过、运行时可解码的 CR2 返回可参与建库；不能将同类 TIFF 一并放行。
- **在语义编码器公共输入边界**，仅对 CR2 转换为临时的规范 RGB JPEG/PNG，然后传给既有 `encode_image(path)`；失败/取消后清理临时文件。避免改活动 Chinese-CLIP/Qwen provider 的模型参数、预处理尺寸、向量维度、空间/processor identity 或搜索排序。
- 覆盖普通语义建库、增量刷新与 `indexing/maintenance.py` 的 reexecute；不要只在某一个模型适配器的 `Image.open` 中做兼容。
- 对已 CONFIRMED 的同 SHA 历史 CR2 向量**直接保留**，不能复写不可变 artifact 触发 `ARTIFACT_CONFLICT`。如证实旧向量由与规范像素不兼容的历史处理生成，先报告受影响项及后续独立重建方案，不擅自换代整个向量空间。

此处的“能搜”是**图库 CR2 作为结果被文字检索**；搜索栏上传 CR2 作为查询图属于另一套受限白名单，本卡不动。

### D. 历史 CR2 补建路径

要求明确实现“已存在但失败/被跳过的 CR2 如何恢复”：

1. 复用现有 build/“继续建库”、`catalog thumbnail/preview --retry-failed` 和任务幂等恢复，不设计第二条全库建库流程。
2. 在**显式继续建库**中对已登记的 CR2 做一次有界、可续游标的历史缺口检查（无缩略图、缓存为旧 `FORMAT_UNSUPPORTED`、缺语义索引/人脸/OCR 等），仅调度确实缺少的派生结果；内容未变也允许补建。日常普通刷新不反复全表扫描历史。
3. 只重试可归因于先前格式不支持的任务；其他原因的失败沿用既有策略，避免无限重试和意外重做人脸。成功发布/已有 artifact 必须跳过；旧人工人物/地点/合集事实不得重置。
4. 如果现有 UI“继续建库”只触发部分环节，最小修正该入口的调度；不要新加设置页。给出 UI 使用步骤及必要时现成 CLI 的兜底命令。缺少适用的实际任务状态时不虚构成功。

历史补建的接受标准是 CR2 有进度、有缺口统计、可中断继续；成功 JPEG 和旧向量不重算。

## 四、最小验收（4 组，不跑全仓）

1. **格式/解码**：合成假 CR2、普通 TIFF、DNG、损坏 CR2 的拒绝行为；真实 CR2 中内嵌 JPEG 与 Bitmap/缺图分支能按能力被验证，格式辨认不逐张 RAW 解码；插件不可用时 JPEG 正常。
2. **方向/人物**：3～5 张已获授权的真实 Canon CR2，尽量含横拍、竖拍、人物、时间及有/无 GPS。检查缩略图与 1600px 预览一致，人脸框与头像没有方向、位置偏移，OCR 至少能执行且不会因格式被拒；检查前后原文件 SHA/mtime 不变。
3. **语义/补建**：使用小型隔离图库与假 encoder 检查 CR2 规范输入、同一来源/索引补齐、临时文件清理、已 CONFIRMED 幂等、`reexecute`；若有可用实际模型，再做**1 张 CR2 的真实文字搜图 smoke**，未做则标注 MODEL VALIDATION PENDING。
4. **历史与回归**：预置旧 CR2 失败缓存/任务、不变的 JPEG 和已发布 CR2 artifact；“继续建库”能够补缺且第二次无重复工作，普通刷新不扫所有历史；选取现有 JPEG/HEIC/MPO 和媒体、人脸、OCR、语义的少量受影响测试。

仅运行涉及模块的 pytest、Ruff、`python -m py_compile`、`git diff --check`、`uv lock --check`；若修改 PyInstaller 收集规则，做轻量打包导入/解码验证，不强制全量正式安装包。**不访问真实 `PhotoSearchData`、不执行数据库破坏性操作、不做整库 GPU/20 万性能与全仓回归**。没有授权真实样片不得假造 E2E PASS。

## 五、交付和停止点

- 代码、锁定依赖、受影响测试；`docs/reports/P4-MEDIA-CR2-A.md` 写明具体解码路径、真实样片覆盖、历史补建方法、不同 provider 证据及未测内容；更新 `docs/development-status.md`。
- **SCOPED PASS** 需：真实 CR2 能显示且方向正确；人物/人脸和 OCR 走通；一个实际活动模型的 CR2 文字搜图验证通过；历史格式拒绝能安全恢复；JPEG/HEIC/MPO 不回退、原图和人工归属零修改。
- 若缺真实 CR2 或活动模型/打包条件，允许完成文档/代码/合成验证，但对应项目标 `VALIDATION PENDING`，不得写完整 PASS。
- 发现必须迁移 Schema、替换全局向量空间、整体改写搜索或人脸系统才能完成时，**停止扩大范围并报告阻塞**，不要在本卡自行实施。

**推荐：GPT-6 Luna High 独立完成，按当前仓库最小实现，不单独要求 DSH/全套回归。**
