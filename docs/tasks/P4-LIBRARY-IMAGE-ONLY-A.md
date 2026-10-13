# P4-LIBRARY-IMAGE-ONLY-A — 只允许图片入库；历史非图片文件安全退出

**状态**：SCOPED PASS / 定向合成简单测试通过。**日期**：2026-10-13。**报告**：[执行报告](../reports/P4-LIBRARY-IMAGE-ONLY-A.md)。**核对基线**：`szzeng/aisearch master @ dc0f641`；实施时再次读取 HEAD、开发状态和工作区。**前置**：已完成的 [P4-LIBRARY-VIDEO-EXCLUDE-A](P4-LIBRARY-VIDEO-EXCLUDE-A.md)（SCOPED PASS，25 项定向通过）。**推荐**：GPT-6 Luna / Medium，涉及查询 SQL 的部分可选择 Luna / High 定向审查。**测试**：个人项目，仅必要的小规模合成用例。

## 一、目标与设计决定

用户把“只排除视频”扩大为“**所有非图片文件都排除**”。图片与其他文件可以混放在同一目录；用户不需要手动分类文件夹。

- **新增非图片**：音频、视频、文档、PDF、压缩包、字幕、侧车文件、脚本、系统文件、未知后缀、无后缀文件均不入图库，不创建位置/资产，不 hash，不排 AI 任务；原文件一律不修改。
- **已入库非图片**：在相应来源的下一次**完整成功的 fast 扫描**末尾沿用已有 `reconcile_missing_detail()` 将旧位置标记 `MISSING`；不删除原始文件、历史身份、人工归属、artifact 和向量；重复刷新幂等。
- **正常相册与搜索**：历史非图片不再算普通图片候选；图库浏览、自然语言/纯条件搜索、人物/地点/合集列表及计数按**权威 SQL 资格**在排序/计数/分页前过滤，不能只靠前端隐藏。
- **图片不被误删**：即便当前解码器不支持 RAW、TIFF、AVIF、JXL 等，它们仍是图片，应允许登记/保留。入库资格与缩略图/人脸/OCR/语义处理能力是两个不同判据；图片不能解码时按现有 `FORMAT_UNSUPPORTED` / job 契约处理，不扩大模型工作量。
- **首版只按文件扩展名判断**，不使用 magic/MIME/ffprobe，不对伪装成 `.jpg` 的任意二进制文件承诺识别；不进行自动永久清理。

这是**独立补充任务卡**，不是将已经 PASS 的视频卡状态重置，也不是让视频卡重新开发一遍。

## 二、开工前已核对的代码事实

- `src/photo_search/ingestion/scanner.py::iter_observations` 已调用 `catalog.video.is_video_path(relative)`，视频在 `observe_file()` / stat / SHA 之前跳过。fast/full/建库共用。
- `src/photo_search/catalog/video.py` 已提供 `VIDEO_EXTENSIONS`、`non_video_location_sql`、`photo_asset_sql`；`api/queries.py`、`search/retrieval.py`、`persons/{albums,collections,read}.py`、`places/albums.py` 等使用 `photo_asset_sql`。扩展时优先复用这些已经验证的权威资格入口。
- `src/photo_search/media/contract.py::supported_input_formats()` 只描述**当前能解码**的 JPEG/PNG/WEBP/BMP/GIF/MPO 和运行时可用的 HEIC/HEIF；TIFF/AVIF/JXL/RAW/PSD 等可能被拒绝处理，**不能把解码支持集用作扫描白名单**。
- `src/photo_search/api/local_actions.py::IMAGE_EXTENSIONS` 有比较丰富的相机 RAW 后缀，但其中包含 `.r3d`（RED RAW 视频），不能无审查地整体复制。其列表与扫描政策应统一或显式说明允许的差异。
- 已有 `tools/diagnostics/video_asset_probe.py` 是只读 SQLite 视频历史清点，适合借鉴，不能删除历史视频统计工具或破坏其输出。
- `catalog/store.py::reconcile_missing_detail` 只有完整成功收尾才能写负面状态；默认 `availability=any` 必须继续支持**真正缺失的图片**，不能粗暴统一改成 `present`。

## 三、实施范围

### A. 建立静态图片文件扩展名白名单（唯一权威）

新增 `src/photo_search/catalog/image_policy.py`（文件名可依架构调整），集中维护：
- `IMAGE_EXTENSIONS: frozenset[str]`
- `is_image_path(relative_path: str) -> bool`
- 给普通图片资产 / 来源范围使用的 SQLite 图片位置资格谓词（复用现有 `photo_asset_sql` API 或兼容包装）。

**必须包含：**
1. 常规照片/位图：`.jpg/.jpeg/.jpe/.jfif/.png/.webp/.bmp/.gif/.mpo/.tif/.tiff/.heic/.heif/.hif/.avif`。
2. 常见图片与设计/扩展图片：`.jxl/.jp2/.j2k/.jpf/.jpx/.ico/.psd/.tga/.ppm/.pgm/.pbm`；SVG 等确属独立图片文件格式可收录，但不可因此声称已有预览能力。
3. 相机照片 RAW 家族：最少 `.dng/.cr2/.cr3/.nef/.nrw/.arw/.sr2/.srf/.srw/.raf/.rw2/.orf/.pef/.raw/.rwl/.3fr/.x3f/.k25/.kdc/.dcr/.mos/.mef/.erf/.iiq` 等，参考现有本机打开图片白名单进行审查并补齐重要品牌；不强制本卡新增 RAW 解码。

**明确排除**：`.r3d`（RED 视频）、`.mov/.mp4`（包括 Live Photo 配对视频）、`.mp3/.wav/.flac`、`.pdf/.docx/.txt/.json/.csv/.zip/.7z/.rar`、`.aae/.xmp/.thm` 等侧车或伴随文件、可执行文件、无后缀与其他不在白名单内的后缀。注意：`.eip` 等照片封装项目不应仅凭名字纳入；如不能确定其是否独立图片，首版排除并在报告列为后续扩展候选。所有大小写不敏感。

**控制策略**：扫描白名单应为部署时稳定、纯 Python/SQLite 可复用的**静态文件类型策略**；不能依赖 GPU、Pillow 插件/驱动可用性，也不能因 HEIC 运行时暂缺解码器便导致原有 HEIC 被标记 MISSING。明确文件名欺骗不在首版识别范围。

### B. 用白名单替换扫描入口的排除视频逻辑

`iter_observations` 对文件：保留现有目录遍历/符号链接/junction 不跟随逻辑，在 `entry.is_file` 后或同等安全位置，先判 `is_image_path(relative)`，非图片直接 `continue`，在 `observe_file` 之前完成，不新增 hash。

复用现有 fast/full/建库统一入口，不创建第二套扫描；不改扫描状态机、lease、中断恢复、原子缺失对账。目录枚举仍会看到文件名，但非图片不做文件 stat/hash。

### C. 将原“非视频”照片资格升级为“有合格图片路径”

将已有 `catalog.video.photo_asset_sql` 改为依赖**统一图片白名单**；可保留 `video.py` 的旧 API 作为向后兼容薄包装，或经影响面核对后一起转接到 `image_policy.py`。旧 `is_video_path` / 视频诊断仍应工作，不能简单删除。

- 具有登记位置的 asset：**至少一个合格图片路径**，才是普通照片候选。
- `source_id` 限定下：该来源必须存在合格图片位置，不能凭其他来源的图片误通过。
- `availability=present/missing/any` 保持原有语义，`MISSING` 的真实图片仍支持历史查询；旧非图片即使 `any` 也不混入普通搜索。
- 同一个 asset 具有图片+非图片多个路径时保留图片身份，非图片路径不赋予“图片来源”资格；零位置孤儿资产保留之前的历史/诊断语义。
- SQL 资格应用在列表、候选、计数、排序、游标、向量/FTS 通道和 Top-K 之前；检查快速向量优化/A3 的资格证明，无等价性证明时回退权威路径，避免 Top-K 后过滤漏图。
- 复用正在使用的 `photo_asset_sql`，覆盖 `api/queries.py`、`search/retrieval.py`、人物列表/人物合集、地点相册等**现有实际入口**；不为将来的 UI 2.0 页面大规模提前改造。 `api/local_actions.py` 若复用白名单须保留动作权限边界、受控路径校验；至少确保 `.r3d` 不再被认作可打开的图片。
- 关注 20 万照片规模的查询成本：白名单扩展不能靠在 Python 逐行过滤结果；尽量复用索引 `file_locations.asset_id` 等按资产候选位置检查，不添加大规模全表扫描 / per-asset Python N+1；只做定向 `EXPLAIN QUERY PLAN` 检查，不在本卡做大型性能基准。

### D. 旧非图片的统计与操作说明

复用视频只读探针方式，新增或扩展**只读**工具（例如 `tools/diagnostics/non_image_asset_probe.py`）：
- 按图库来源输出非图片位置总数、PRESENT/MISSING 及按扩展名分类 Top N；
- 统计仅非图片位置资产数、图片+非图片共享资产数、图片位置数（分别按位置与唯一 asset 计，不能相加冒充图片数）；
- 支持小量样例和 JSON，样例只用相对路径；严格只读数据库、不遍历真实图片、不读取原文件/向量。
- 工具可以在操作前后运行核对：部署前估计已有多少非图片记录、刷新后仍有多少处于 PRESENT。**历史位置变 MISSING 不是永久数据清除**。

无需新增复杂 UI 设置；简短说明用户升级后点击“刷新图库”，旧非图片自然退出现役图库。

## 四、简单测试（3～4 组合成，不跑全仓）

1. **混合目录 + 大小写**：`photo.JPG`、`i.heic`、`raw.DNG`、`scan.TIFF`、`image.avif`、`clip.MOV`、`audio.mp3`、`doc.pdf`、`metadata.XMP`、`file.zip`、`README`；首次 fast/full 仅图片路径登记，非图片无新 asset/hash/job。对有合法图片后缀但解码能力不足的文件，仅要求保留其入库资格，不要求 AI 成功识别。
2. **历史入库→成功 fast 刷新**：合成库预置已入库 `doc.pdf`、`audio.mp3`、`clip.mov`、`photo.jpg`，普通刷新后旧非图片位置转 MISSING；默认 `availability=any` 列表/搜索/纯筛选不展示非图片，真实缺失图片仍可查询；再次刷新幂等，原文件内容和 mtime 不变。
3. **共享身份、来源与分页**：同一 asset 绑定图片+非图片位置，在图片来源可见、仅非图片来源不可见；人物/地点/合集复用权威资格，计数和两页 keyset/搜索候选一致；不能先限额后删结果。
4. **失败安全 + 统计**：来源离线/扫描中断绝不能把旧位置判为 MISSING；只读诊断按分类数量正确；验证视频旧策略没有回归，`.r3d` 必须排除，HEIC 即便解码器不可用也不能从扫描白名单消失。

复用 `tests/integration/test_p4_library_video_exclude.py`、已有 scanner/persons/places/search 小夹具；只增加少量必要测试。仅做受影响面 pytest、Ruff、Python `py_compile`、`git diff --check`；改动 JS 时才做 Node 语法。**不跑全仓、不访问真实 PhotoSearchData、不跑 GPU/真实 20 万图库性能测试、不做真实数据库破坏性操作**。

## 五、非目标与验收门槛

**不得**：Schema 迁移、删原文件/移动原文件、直接删除旧 `assets/file_locations`、人工归属、预览缓存、OCR/人脸/地点事实、向量/模型产物；不改 GC / retention 和任何永久删除授权边界；不扩展成完整 MIME 安全扫描、UI 视频库、文件管理器或 AI 文件分类产品。

**PASS 条件**：入库阶段只接纳图片后缀；已经入库的非图片经来源成功快刷标 MISSING、正常照片搜索/列表彻底排除，真正缺失图片仍可查、共享图片不受损；有只读清点、原文件零修改证据；定向测试及静态检查通过。交付 `docs/reports/P4-LIBRARY-IMAGE-ONLY-A.md`，写明被支持/被排除后缀、真实未测范围及本卡未执行任何永久清理。
