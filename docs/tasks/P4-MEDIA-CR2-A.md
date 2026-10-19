# P4-MEDIA-CR2-A — 佳能 CR2 安全解码与历史人脸/OCR 补建

**状态**：IMPLEMENTATION PARTIAL / VALIDATION PENDING。**执行日期**：2026-10-19（第一阶段续做）。**原实施基线**：`0f34c0b`；**续做基线**：`9adbad5`（origin/master，当前 `master` 含本地并行提交 `5cda4b5`）。**续做主线**：先修复历史 CR2 人脸/OCR 批量补建，再视证据做内嵌预览加速。**验证**：隔离测试库与少量获授权的真实 CR2，按受影响面验证。**依赖**：`P4-LIBRARY-IMAGE-ONLY-A` 已 SCOPED PASS。

## 一、目标和边界

让图库内的佳能 `.cr2` 像正常照片一样：缩略图、1600px 预览、自然语言搜图、人物/人脸、OCR、拍摄时间与有证据的 GPS 地点均能参与现有流程；已收录但历史 `FORMAT_UNSUPPORTED` 的 CR2 能在“继续建库”流程中定向补建。**当前统一使用 RAW 规范画幅；内嵌预览只有能证明方向、裁剪和下游坐标一致才启用，否则继续使用 RAW。历史任务恢复不依赖预览加速。原文件只读**，不要求全库重建，不新增 CR2 设置页。

本卡**只增加 CR2 的处理能力**，不是重新设计图片入库。静态图片白名单已在 `src/photo_search/catalog/image_policy.py` 包含 `.cr2`，扫描/SQL 照片资格已完成；当前已有经过 CR2 内容头确认的 `rawpy` 解码入口，普通 TIFF/其他 RAW 仍拒绝。**禁止**仅将 `TIFF` 从拒绝集移动到支持集，更不能因扩展名为 `.cr2` 就接收伪装内容。

不扩展 `.cr3/.dng/.nef/.arw` 等其他 RAW，不做 RAW 编辑、上传图片查询中的 CR2 支持、RAW+JPEG 自动去重、GPU 解码专项、UI 重设计及全库迁移。

## 二、开工前必须核对的真实调用链

1. `media/contract.py` 中 `catalog-thumbnail@1`、`catalog-preview@1` 的 320/1600px 契约与缓存版本；`media/decode.py` 的 `load_normalized()/read_normalized_dimensions()/read_orientation()`，`media/pipeline.py` 的失败缓存与 `--retry-failed`。
2. `face/processor.py`、`ocr/processor.py` 共用媒体解码；`media/face_crop.py` 的 `recorded_face_frame()` 优先读取人脸检测文档的 `original_size`，无文档才 `probe_original_frame()`。不得误把 CR2 RAW 尺寸当成检测所用 JPEG 预览尺寸。
3. `indexing/semantic.py::image_eligibility()` 现已对通过内容头检查且运行时可用的 CR2 放行；语义 build/reexecute 已通过临时规范 JPEG 传入现有 encoder。继续核对实际活动 provider、不可变产物身份和真实样片文字搜图，不再重复开发已存在的适配。
4. `refresh/pipeline.py` 的 daily 主要使用内容变化集，build/继续建库走分批范围；`face/service.py::detect(force=False)`、`ocr/service.py::enqueue_targets(force=False)` 对已有终态 job 的 enqueue 不会直接重新排队。人脸需区分普通、自动详情、手动详情三种任务，OCR 需核对 active CPU/GPU 和退休 processor。
5. `taken_time/exif.py`、`geo/extract.py` 的 Pillow EXIF 路径，`search/upload.py` 的独立上传白名单，以及 Windows 打包入口 `tools/packaging/build_onedir.py`。

复用以上边界，除确有必要外不新增第二套调度、预览缓存、位置解析或搜索服务。

## 三、实施方案

### A. 仅对真正 CR2 做受限解码

现有 `media/raw_cr2.py` 已作为**唯一 CR2 适配层**，锁定 `rawpy==0.27.1`，接入 `load_normalized()` 并配置 PyInstaller 收集。续做优先复用，不重复开发/无故升级依赖，禁止动态下载外部解码程序。

- **资格检测要轻量**：结合 CR2 特征头（TIFF 容器中的 Canon CR2 标记）和受控路径，独立分类 `CR2`；`.cr2` 假文件、普通 TIFF、DNG 继续 fail-closed。语义目标选择只做轻量头检查与运行时能力判断，不逐张 RAW `postprocess`。
- **内嵌 JPEG 仅为加速候选**：`rawpy.extract_thumb()` 的 JPEG/Bitmap 尺寸因机型不同；未证明 A1 的方向和裁切关系前继续沿用已实现的 `rawpy.postprocess()`，保留固定 sRGB/8-bit，不放大小图。
- **跨消费者统一画幅**：对每张 CR2 基于文件内容与固定版本策略选定一个“规范像素源”，不因调用请求 320px、1600px、OCR 或人脸尺寸不同而切换。缩略图、预览、常规人脸、OCR、语义输入共用方向、裁剪边界与坐标基准，可各自降采样；不可将 RAW 传感器宽高直接当成预览图的尺寸。
- **方向只变换一次**：记录“预览已经旋转/RAW 旋转/EXIF 方向”中哪个实际生效；验证 1/6/8 方向和横竖样例。优先统一调用现有归一化结果及坐标约定。无检测文档的 `probe_original_frame()` 也须得到规范画幅尺寸。
- **资源与失败**：解码前对元数据尺寸/像素数作基本内存防护，限制 RAW 兜底并发；每文件关闭 rawpy 句柄，异常独立转为原有 `FORMAT_UNSUPPORTED/DECODE_FAILED` 等准确错误，不得把解码器缺件写成文件损坏。不能让单张坏 CR2 阻断整批，也不能影响 JPEG/HEIC 的导入和启动。

### A1. 内嵌预览加速的安全门槛（RAW-only 允许）

佳能 CR2 可能包含全尺寸 JPEG、中尺寸预览或仅小缩略图。**分辨率不同不等于实际裁切不同**；不要求每张 CR2 先完整 RAW 解码再做图像比对。**不能证明内嵌图几何关系时保持 RAW-only，并记录为未启用的性能优化，而非历史补建失败。**

| 观察结果 | 执行策略 |
| --- | --- |
| 有可正常读取的内嵌 JPEG/Bitmap，校正方向后长边 **≥1600px**，且与 RAW 有效画幅方向、比例及裁切坐标关系可靠 | **预览优先**；320px 缩略图、1600px 预览、普通人脸/OCR/AI 均使用同一预览取景基准 |
| 预览小于 1600px，或仅有极小缩略图 | **RAW 兜底**；源本身不足 1600px 则不放大 |
| 预览损坏、方向无法确认、缺失，或与 RAW 有效画幅明显不一致 | **RAW 兜底**；兜底失败则逐文件报告，不影响其他照片 |
| 无法确认 JPEG 与 RAW 的裁切关系 | **保持 RAW 规范画幅**，不混用两种坐标；记录加速待验 |

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

### D. 历史 CR2 人脸/OCR 补建（2026-10-19 续做主线）

**用户目标**：已收录 CR2 升级解码器后，在原来源页使用“继续建库”即可批量补齐可靠的缺口，**无需逐张打开或点击“尝试原图细节识别”**。普通打开只读取已保存人脸并可能生成媒体预览；显式 `POST /assets/{id}/face-detail` 是单张手动原图详情任务。实施前用 job/artifact 前后状态区分“打开后才显示人物”究竟是旧失败、后台完成还是前端重新读取，不能直接把观察当成已证根因。

#### D1. 候选判定与安全恢复

1. 沿用 `refresh/pipeline.py::_drain_scope/_face_stage/_ocr_stage` 和现有 job 队列，不做第二套任务系统。先用**隔离合成/复制测试库**按来源 `PRESENT`、`.cr2` 后缀作粗筛，只对缺口候选检查 Canon CR2 文件头；按 `asset_id + assets.sha256`、受控来源、`task_type/processor_id`、job 状态/`last_error_code`、当前 PUBLISHED artifact 分类。**无 faces 行不等于没识别过**，零脸成功需依据已发布文档及 job 判断。
2. **仅回放可证明属于旧版 CR2 不可解码的终态失败**：优先核对 `FORMAT_UNSUPPORTED`，历史 `DECODE_FAILED` 结合旧错误记录与样片证据判别，不能见码就重跑；允许 `FAILED`、`BLOCKED` 中被证实的旧格式错误。`RETRY_WAIT` 交给原有退避调度，`RUNNING`/有效 lease 不抢占。`CANCELLED`、权重/GPU 缺件、文件损坏、权限故障、无法判断的失败、当前 SHA 不符，均不自动复活。真正从未有当前 profile job 的 CR2，走正常首次入队。
3. **跳过有效结果**：当前 profile 的 `SUCCEEDED` + 有效 PUBLISHED artifact（**含零脸、OCR 空文本**）不重新推理；`SUCCEEDED` 却缺 artifact、校验失败或 profile 身份有歧义，单列一致性异常，不能擅自当格式失败处理。不动旧人工归属、现有 CONFIRMED 向量、其他格式媒体或已发布不可变 artifact。
4. **受控精准 rewind 与防重复**：在事务内以五字段 job 逻辑身份 `(subject_type,subject_id,input_revision,task_type,processor_id)` 和旧状态/错误条件 CAS，仅重置已确认的旧失败；不传来源级 `force=True`。恢复后再次发生相同错误，后续 build **不能无限重试**。优先证明现有持久字段足以安全记录“一次性、按版本补救”；若不能，则在本卡内评审最小**加性恢复记录/迁移**，先备份/升级和中断回滚测试再实施；不得使用进程内 set、mtime/日期启发式或覆盖错误信息充当持久幂等。确需改变冻结任务协议或破坏性迁移则停止上报。
5. **只在显式 build 恢复历史**：`mode=refresh` 仍处理 delta，浏览器 GET 不触发模型。按来源分页/续游标并受控读取，解码 worker 沿用既有 size/mtime/SHA 与权限校验；不为筛选对每张 CR2 重算全 SHA 或完整 RAW。中断/重启、离线来源、双来源同 SHA 时既不漏项也不重复实际推理；不得越界重新处理非选中来源。

#### D2. 三种人脸任务、OCR profile 与人工保护

- 人脸普通 `image_face`、自动原图 `image_face_detail_auto`、手动单张 `image_face_detail` 保持不同 job/产物契约。按已保存的 `face_build_profile` 决定 build 的普通/自动路径；自动 source-detail 复用 `detail_policy.restore`、索引/association；手动原图结果不可自动归人，不将旧普通失败假装为已完成的自动详情。
- OCR 遵守 `ocr/service.py` 当前 CPU/GPU active/retired processor 协调及同一资产互斥，不复活退休 profile、不同 processor 不互相覆盖。
- `CONFIRMED`、`MANUAL_UNASSIGNED`、`TEMPORARY`、`EXCLUDED`、人工命名/封面/合并/撤销、OPEN redetection hold 均受保护。**人工状态主要限制重新归人及旧新脸映射，并非简单的“有人工状态就禁止检测”**；若进行补识别必须遵循已有 hold/映射逻辑且不覆盖人工锚点。

#### D3. 产品与性能（PERF-BOUNDED）

- 使用原“继续建库”按钮、来源选择、现有状态；不新增 CR2 设置页。当前 `face/service.py::enqueue_targets` 的 `queued` 是尝试数，可能包含 `ON CONFLICT DO NOTHING`，**不代表真实新增或成功**。如需增强进度，优先在原 build 阶段结果中加性输出“候选 CR2 照片、实际恢复 job、完成、未完成、保护性跳过/原因”，明确资产去重与 job 数的区别，不改旧 API 含义。
- **链路与风险**：现有来源 `_scoped_asset_page` keyset 分批可以保留；新增分类不得为每个 asset 多次 SQL/反复加载模型，也不能让普通打开页面背负大图库扫描。
- **做法与边界**：优先复用已有 `BUILD_PAGE_SIZE`/keyset 与已有缓存，按页 P 批量查 jobs/artifacts，**仅候选读 CR2 文件头**；核对新增 SQL 次数、索引和必要时的 `EXPLAIN QUERY PLAN`。允许显式 build 分批遍历来源，但已补齐的第二次 build 应有 **0 次新增 CR2 模型推理**；不因本卡强制 30 万性能认证。
- **验证与停止**：小型隔离图库复跑稀疏/密集旧失败、跨页、双来源、取消与重启。报告 SQL/文件头/模型调用次数及真正新增/恢复/完成数量；出现明确规模风险才追加对照。既有约 1.13 秒/张 RAW 解码只是 5 张样片观察，不当作全库吞吐承诺。

#### D4. 必需状态矩阵与审查

| 场景 | 必须证明 |
| --- | --- |
| 新 CR2、历史 `FORMAT_UNSUPPORTED` | 正常首次入队或旧 job 精确恢复，实际成功发布；第二次 build 不重复模型推理 |
| 历史 `DECODE_FAILED`、伪 CR2、坏文件、JPEG/HEIC | 仅有证据的旧解码失败恢复；无误伤与无限重试 |
| 已成功、零脸、空 OCR、旧普通与自动/手动详情并存 | 不覆盖产物、脸身份/人工事实；识别与归人边界正确 |
| `RUNNING`/lease、`RETRY_WAIT`、取消、重启、双来源 | 不抢占、不漏项、不重复推理，失败及进度可复核 |
| OCR CPU/GPU/退休 profile 与模型缺件 | 不发生同资产 profile 竞争，缺件不被误报成历史格式失败 |
| GET 浏览照片、POST 单张详情、来源继续建库 | 打开不隐式提交检测；手动只单张；build 可批量恢复 |

基于受影响 B + 状态/恢复 D 选择带 JUnit 的 pytest、Ruff、py_compile、diff-check；若实际改共享 job、迁移或公共行为，按 `docs/testing.md` 扩大关联测试而非机械小测。浏览器按 `AGENTS.md` 用隔离合成数据和内置浏览器只验“来源继续建库→进度与结果→重复建库”和“打开 CR2 不触发人脸任务”。任务状态恢复属高风险，需要**独立 DS-H 受影响面审查**：五字段身份、CAS/lease、一次性恢复、OCR profile、人工锚点、零脸及续游标。阶段通过可记 `CR2 RECOVERY VERIFIED`，**不能冒充整卡完成**。

## 四、最小验收（复用历史证据，补测受影响面）

1. **历史恢复**：D4 状态矩阵采用隔离图库与假 face/OCR executor，并记录 job 状态、真实 model invocation 与 PUBLISHED artifact；同时核对重复 build、零脸成功、旧/新 face profile、OCR CPU/GPU、人工保护和中断恢复。`enqueue.queued` 不能当“新任务数”。
2. **真实 CR2/模型**：复用 2026-10-13 五张 CR2 的只读 RAW/EXIF/几何结果。新增至少一张**经授权且含真实人脸**的 CR2 样片验实际模型、检测框和头像/人物候选或人工更正；OCR 需一张有文字样片，活动模型需至少一张 CR2 实际文字搜图（样片可重用）。缺样片/模型则逐项 `VALIDATION PENDING`，不得把假执行器测试说成模型 PASS。竖拍、有效 GPS、不同机型按可得样片验证，未覆盖列范围限制。
3. **解码安全**：fake CR2、TIFF/DNG、损坏源、方向、长边与统一坐标测试继续有效。**安全 RAW-only 是合法的整卡输出**：不为了提速强行启用内嵌 JPEG；若启用必须证明 crop/方向/人脸框/OCR 与现有规范画幅一致，否则保持 RAW 并写明“预览加速未启用”。
4. **回归边界**：JPEG/HEIC/MPO 正常媒体、face/OCR、语义不回退；人工状态、原文件 SHA/mtime、不变向量/不可变 artifact 仍受保护；日常 refresh 不反复全表恢复。仅对变化节点补测，复用可信历史结果；只有符合 `docs/testing.md` 的依赖/迁移/公共无界更改才扩为完整回归。

仅运行实际变更需要的 pytest（JUnit）、Ruff、py_compile、`uv lock --check`、`git diff --check`，必要时做打包收集烟测。**无明确授权不得读取或修改真实 `PhotoSearchData`，不得全库 RAW 重建或为普通任务要求 30 万测量**。

## 五、交付与停止点

- 继续在原 [执行报告](../reports/P4-MEDIA-CR2-A.md) 追加续做记录：旧失败判据、处理范围、任务身份与恢复方案、第一次和第二次 build 的分类/模型计数、真实 CR2 与模型结果、人工保护、性能风险、未验证项；更新 `docs/development-status.md`，保留 2026-10-13 历史证据。
- **历史恢复阶段通过**：D1～D4 的一次性定向恢复、失败不死循环、成功/零脸不重跑、人工状态/OCR profile 不误伤、重启与重复来源、独立审查全部通过，可写 `CR2 RECOVERY VERIFIED`。
- **整卡 `SCOPED PASS`**：历史恢复通过；至少有已授权真实 CR2 人脸、OCR、活动语义搜索的端到端验证；显示/方向/人脸框一致，JPEG/HEIC/MPO 不回退。A1 证据不足时维持 RAW-only 仍可按**安全 RAW 功能范围**收口，但必须写“内嵌 JPEG 加速未实现”，**不可声称预览优先已经完成**。无竖拍/GPS/完整 frozen 证据要明确限定未覆盖的能力与发行范围。
- **停止点**：需要重写全局向量空间、冻结协议或破坏性数据库迁移时先停下说明；如果为一次性恢复确需最小加性记录/迁移，必须遵循 D1 的备份/兼容/回滚审查。不得抹除原失败证据、改变人工事实或声称已运行真实图库。

**执行建议**：留在同一任务卡内先完成 D1～D4，无需另建 B 卡。历史恢复阶段需要产品实现和受影响面验证；只有文档整理才可按文档任务处理。

### 2026-10-19 历史 CR2 人脸/OCR 恢复续做

- 新增显式 build 专用的恢复入口：仅对当前页资产、所选来源中 `PRESENT` 的 `.cr2`，检查对应 face/OCR profile 的终态 `FORMAT_UNSUPPORTED` job；校验 job 输入 SHA 与当前 asset SHA 一致后才读取 CR2 文件头。日常 refresh 和普通页面读取不触发此恢复。无法证明旧 `DECODE_FAILED` 属于旧格式不兼容，故保守保留，不自动重放。
- 新增 schema 0035 `cr2_job_recoveries` 持久记录；按五字段身份、原终态错误、无 lease 做事务 CAS，保留原失败证据并只允许一次重试。恢复后再失败不会循环。有效的当前 profile PUBLISHED artifact（含零脸/空 OCR）跳过；损坏或身份不一致单独计数，不把它伪装成格式失败。build 结果附加恢复分类计数，OCR 队列 drain 限定在本次 build 页资产和 active processor。
- 定向 CR2 测试覆盖 FORMAT_UNSUPPORTED 精确恢复、原始错误留存、二次失败不重复恢复、当前 SHA 在 CAS 时变化则拒绝恢复，以及发布文档 profile 身份匹配：2 passed（JUnit `test-results/p4-cr2-recovery/luna-fix.xml`）。完整 unit/integration 原始结果：3212 项，3152 passed、9 failed、51 skipped（JUnit `test-results/p4-cr2-recovery/unit-integration-final-2.xml`）。其中迁移全量表 fixture 遗漏新加表名，已补后针对性重跑 1 passed（JUnit `test-results/p4-cr2-recovery/migration-final.xml`）；原完整运行不重写为 PASS。其他 8 项为 2 条人物页面缓存断言、搜索 UI harness、favorites API/UX、storage UI 与 timeline UI 断言。未发现 CR2 恢复用例失败。另一次无 `--basetemp` 的重跑在 pytest 清理共享 `.pytest-tmp` 时遇到 Windows `WinError 5`，已使用隔离 `--basetemp` 完成 CR2 定向用例；未改 ACL。
- Ruff、相关 Python `py_compile`、`git diff --check` 通过。schema 0035 要求兼容性测试更新；geo 数据集 reviewed-through 更新到 35，0034 历史 schema fixture 固定到 34。工作区同期有 favorites/UI 改动，9 个回归失败不全属于本卡。
- Luna High 子代理独立审查发现两个 P2：published payload 未验证文档自身 profile 身份，恢复 CAS 未在事务中复核当前 asset SHA。现已修正并补测试：普通/自动详情 face payload 要求与所选 task/profile 相符；INSERT marker 与 UPDATE job 均要求 `assets.sha256 = jobs.input_revision`。两个 CR2 定向测试通过（JUnit `test-results/p4-cr2-recovery/luna-fix.xml`），Ruff、py_compile 和本任务文件 diff-check 通过。Luna H 复核修正后无新增 finding。**此项只完成 Luna H 子代理复核，不替代卡片规定的 DS-H。**
- **仍 PENDING**：D4 完整状态矩阵（成功/零脸/空 OCR、profile 和人工锚点、lease/重试/取消、双来源、重启、CPU/GPU/retired OCR、模型缺件）未全覆盖；内置浏览器来源继续建库/重复建库与“打开不触发任务”未验；真实 CR2 人脸/OCR/语义模型端到端未验；独立 DS-H 受影响面审查未执行。故不标 `CR2 RECOVERY VERIFIED`，本卡保持 IMPLEMENTATION PARTIAL / VALIDATION PENDING。

## 2026-10-13 执行记录

已落地 RAW 解码、共享规范画幅接入、语义规范临时输入、PyInstaller rawpy 收集以及历史媒体 `FORMAT_UNSUPPORTED` 定向重试。定向组 A **37 passed**、定向组 B **90 passed**，Ruff、py_compile、`uv lock --check`、`git diff --check` 通过。详见[执行报告](../reports/P4-MEDIA-CR2-A.md)。

本次没有收到已授权样片目录路径；内嵌预览暂未启用，实际只验证合成输入和假 encoder。真实 CR2 画幅/方向、人脸/OCR/时间/GPS、活动模型搜图、真实历史续建与完整打包仍待验证；人脸/OCR 旧终态任务的 CR2 定向恢复也未完成。**不可记 SCOPED PASS**。收到样片路径后继续本卡，不新开后续任务卡。

### 样片续验（2026-10-13）

用户已授权 `D:\SZ\天空\100CANON`。只读抽取 5 个分位 CR2 样片：全部为 6000×4000 JPEG 内嵌预览与 6024×4020 RAW 有效画幅，LibRaw `flip=0`。SIFT/RANSAC 几何诊断未能对全部样片证明稳定裁切映射，故继续统一使用 RAW，`cr2_preview_used=0`、`cr2_raw_fallback=5`、`cr2_decode_failed=0`；单张解码均值 1.130 秒，原文件 SHA/大小/mtime 未变化。共享人脸/OCR解码入口均返回 RAW 规范画幅；真实模型推理未运行。时间与子秒可读，但样片不含有效 GPS 坐标，未覆盖竖拍/有效 GPS。定向测试 **127 passed**（JUnit `test-results/junit-p4-media-cr2-a-validation.xml`），Ruff、`py_compile`、`uv lock --check`、`git diff --check`、PyInstaller rawpy collector smoke 均通过。活动模型文字搜索、实际续建幂等、旧人脸/OCR终态任务定向恢复及完整打包仍待验证；详见[执行报告](../reports/P4-MEDIA-CR2-A.md)。本卡保持 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**，不标 SCOPED PASS。

### 第一阶段续做（2026-10-19）

- 检查 `9adbad5`、本卡与报告后确认：已有 CR2 人脸缺口可能来自旧 job 的 `FORMAT_UNSUPPORTED`、当前 build profile 与旧 task 不一致、其他失败码/身份变化或模型能力缺失；有效 PUBLISHED（包括零脸）须跳过。没有读取真实 `PhotoSearchData`，无法判定其具体缺口类型或对照真实缩略图/预览缓存和 job/profile。
- 修复 `face_targets()`、`ocr_targets()` 在 `asset_ids` 模式的全图库读取：ID/来源筛选及 limit 现在进入 SQL，空集合直接返回。修复 face 任务身份获取逐资产 `read_one` 为最多 800 个 asset IDs 一批的 SQL 查询，随后严格匹配 subject/revision/task/processor。
- 受影响组合实际运行 **44 passed / 0 failed / 14 skipped**；14 个 skip 都是 pinned 人脸/OCR 权重缺件。JUnit、命令和根因分析在[执行报告](../reports/P4-MEDIA-CR2-A.md)。Ruff、`py_compile`、`git diff --check` 通过，无 30 万图库测量。
- 合成 build profile 测试的第二次建库以已完成零脸 `.jpg` 验证 `steps=0` 和无新增 executor 调用；这不是 CR2 实际模型闭环。独立只读代码审查未发现查询改动直接回归，但指出仍缺“真实人物 CR2→显式 build→真实 face result”和同一 CR2 第二次 build 零推理的端到端证据。此审查不替代任务规定的 DeepSeek DS-H。
- 本机 pinned 人脸/OCR 权重均未安装。真实人物 CR2 模型推理与结果发布、当前图库缓存/job/profile 对照、完整 D4、浏览器验证及任务卡要求的独立 DS-H 审查仍 PENDING。保留 schema 0035 一次性恢复和旧错误证据，不重跑所有人脸、不改人工归属。当前阶段不标 `CR2 RECOVERY VERIFIED`，整卡仍不是 PASS。
