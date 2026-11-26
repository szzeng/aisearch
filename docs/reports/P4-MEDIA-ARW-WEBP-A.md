# P4-MEDIA-ARW-WEBP-A 执行记录

2026-11-26，基线 `73bd582`，开始时工作区干净，master 比本地 origin/master 超前一个提交。未访问真实图库、未提交、推送或发布。

## 前置检查与停止原因

- `raw_cr2.py` 使用共享 RAW 锁、120MP 限制和 rawpy 0.27.1；现有媒体、语义、face crop 和恢复入口明确只识别 CR2。仓库 tests 中未发现 ARW 样片。rawpy 当前公开对象无相机 make/model 属性，ARW 内容鉴别和真实样片仍需验证。
- 0035 `cr2_job_recoveries` 保存 job 五字段身份及原终态错误，约束没有格式字段，但只有在来源、身份、并发及历史状态矩阵验证后才能确认通用复用。
- 媒体 `build_cr2_pair` 在解码结束并失败后才通过 `_record_failure` 写入 `cr2_format_retry`；写失败被捕获，进程在写标记之前退出亦会留下未标记的旧失败。成功发布后调用 `clear_failure`，原失败记录被清除。直接复用不能保证本卡要求的严格一次性与原失败证据保留。本卡明确规定机制不能安全复用时先报告最小方案并等待确认，故未改产品代码。
- 建议最小方案：利用现有内容寻址媒体失败记录，在自动重试前持久保存原失败与已尝试标记；先验证同一 SHA/processor/kind 的并发互斥和原子提交，标记失败则不执行重试，成功发布仍保留可查的原失败证据。中断后的已消耗标记不自动重试。具体实现需证明不改变 CR2 既有语义或解释必要改动；不新增表、迁移或第二套缓存。
- 用户允许调整任务卡，采用上述方案并继续核查。公开 CC0 样片 [Sony a7 III](https://github.com/tellodaniel/revelraw-sample-raw-files) 下载至忽略的项目临时目录：rawpy 可打开并报告 6024×4024；当前 Pillow `Image.open` 报 `UnidentifiedImageError`，现有拍摄时间和 GPS 提取因此不能直接用于该样片。仅用于诊断的本机 tifffile 能读到 SONY、ILCE-7M3 和原始时间/时区字段；不能据此声称已实现适配，且尚未确定新增依赖或打包方案。该样片无竖拍或 GPS 验证结论。
- 媒体失败记录属于可清理的派生缓存：记录清理、损坏或外部删除后，不能保证原始失败证据和跨清理的一次性限制。任务卡现明确这一边界；job 的一次性恢复仍需满足 0035 的持久身份和 CAS。

## 验证与未完成范围

隔离目录执行 `.venv/Scripts/python.exe -m pytest -q tests/unit/test_media_decode.py tests/unit/test_p4_02t_upload_formats.py --junitxml=test-results/p4-arw-webp/webp-existing.xml --basetemp=.pytest-tmp/p4-arw-webp-existing`：退出码 0，39 passed。包含既有普通 WebP 解码及上传格式回归，不能证明 WebP 完整图库、透明、EXIF/GPS、损坏隔离、真实样片或冻结包。

用户接受本卡所记录的媒体缓存清理边界。新增 `tests/unit/test_p4_webp_integrity.py`：透明图片转白底 JPEG、上传读取、原始 WebP 的 EXIF 时间/GPS 与损坏隔离，3 passed（`test-results/p4-arw-webp/webp-integrity-r1.xml`，退出码 0）；`tests/integration/test_api_files.py::test_webp_scans_and_serves_jpeg_despite_corrupt_neighbour` 通过（`test-results/p4-arw-webp/webp-api-r1.xml`，退出码 0），覆盖隔离扫描、损坏邻图、JPEG 缩略图/预览和缓存命中。Ruff、py_compile、diff-check 通过。单元/API 证据不能代替浏览器、冻结包或真实 WebP 全链路。

公开 Sony a7 III 样片 SHA-256 `250784580ea527442c09004417bb0eead484f2bf3ee8f9121a776ac65bb50d0f`：独立 rawpy 0.27.1 手工解码得到 RGB 6024×4024，再生成 320×214 和 1600×1069 JPEG；这只是依赖可行性实验，不是产品 ARW 解码或方向验收。

## 实施与阶段验证（2026-11-26 续做）

- 新增 `raw_arw.py`，要求 TIFF 内 Sony Make/Model、Sony RAW CFA 标记、排除 DNG，并以 LibRaw unpack 校验；`.arw` 后缀本身不放行，伪造 JPEG/TIFF 和 DNG 拒绝。与 CR2 复用解码锁、120MP 防护和 sRGB 8-bit RAW 画幅；解码器先打开元信息检查像素上限，再执行 unpack，尺寸探测只读元信息。常规媒体成对生成先做仅结构候选筛选，真正放行须成功完整解码；历史失败恢复先额外 unpack 验证，不能把解码入口计数当作所有路径的 RAW 解包次数。
- 统一媒体、face/OCR、face frame 和语义构建/maintenance 接入；编码器使用临时规范 JPEG，成功及异常均清理。原始容器 EXIF 用于时间和 GPS；没有从临时 JPEG 取元数据。来源分页成对生成沿用原缓存与 JPEG 契约。
- 0035 job 表沿用现有五字段身份、原错误、CAS/lease 和一次性约束，扩展经内容确认的 ARW；媒体失败重试在解码前经 catalog 写事务串行登记并留存旧错误，写入失败拒绝重试，中断后不重试，产物成功也保留记录。按用户确认，该媒体记录随缓存清理/损坏可失去；不是永久审计。来源候选会检查同资产多个选中位置。原有 `cr2_retry_assets` 字段为符合条件的候选计数，标记写失败时不代表实际执行。
- 媒体恢复成功时保留原错误及成功标记，后来清理单个 JPEG 可正常再生成；若再生成失败，保留原始失败证据并按新失败阻止循环。未知 `DECODE_FAILED` 即使来源 mtime 改变也不会在来源 build 中自动重试该产物；另一个没有失败的产物仍可生成。对应 JUnit `retry-regenerate-r1.xml` 和 `decode-failure-r1.xml` 各 4 passed，`regeneration-failure-r1.xml` 1 passed。进程中断有注入，断电下文件系统持久性未实测。
- 公开样片 a7 III（SHA 见上）、[a7S 与 A330](https://github.com/sdcb/Sdcb.LibRaw.TestData)（SHA 分别 `a35ebb2fbec929daa5beb20d1ce5c15a8aac7b1a7a231455387f3df8a7442e07`、`e5737fc8debb5ddea8d557de050565f3516635e093d246450ea2b3d1a27947a3`）在源码路径得到 6024×4024、2784×1872、3880×2608 RGB，尺寸探测一致，均生成 320/1600 JPEG 并读到拍摄时间、Make/Model；a7 III 显式 +01:00，其余无 offset。三张均横拍、无 GPS；不能替代竖拍/GPS 或人脸、OCR、真实模型搜索验收。
- 三样片隔离扫描及来源 build：首次 `load_normalized` 计数 3、缩略图 3 和预览 3；第二次新增计数 0。最终代码另对 a7 III 的首次生成插桩，记录 LibRaw open、unpack、postprocess 各 1 次；第二次各项均无新增。历史失败恢复的内容确认另需 unpack。此为单机调用次数，不是延迟结论。使用替身 encoder 验证真实样片规范 JPEG 输入及临时清理，未验证真实模型检索质量。
- 公开 Canon 40D CR2 样片（SHA-256 `ba644e7dd2abe74eca260e67f0206ff113bf0f62e710f8130611e964d6be5bf1`）在调整后解码为 1944×1296 RGB；该图与 a7S 分别和原 `rawpy.imread` 相同处理参数比较，像素数组完全一致。并用替身断言超像素上限时不执行 unpack，尺寸探测不执行 unpack。`recovery-r7.xml` 定向 58 passed。未将单样片作为 CR2 全机型验证。
- 受影响验证为多轮有重叠证据：`affected-r2.xml` 260 passed；后续格式门控 `gating-r1.xml` 133 passed；恢复与最终差量 `recovery-r4.xml` 16 passed、`final-delta-r1.xml` 17 passed、`claim-r1.xml` 1 passed。覆盖伪造 JPEG/TIFF、DNG、上限、EXIF/GPS 合成、缓存、失败写入、中断、源范围、数据库恢复和两连接竞争。`arw-unit-r2.xml` 首次失败源于测试夹具 TIFF 写入方式，修正后 `arw-unit-r3.xml` 通过；`recovery-r2.xml` 首次失败源于测试未捕获包装后的中断异常，修正后 `recovery-r3.xml` 通过。两轮原始失败 XML 保留，不称一次全量通过。Ruff、Python 编译及 diff-check 通过。
- 当前代码最终受影响 pytest 使用 ARW/CR2、媒体缓存与并发、HEIC、语义、时间/GPS、WebP、face/OCR 几何、扫描/API、refresh 和相应集成测试：`test-results/p4-arw-webp/final-affected-r1.xml`，实际退出码 0，**323 项：319 passed / 4 skipped / 0 failures / 0 errors**。四个 skipped 均为未安装 P0-04 指定 PP-OCRv4 权重的真实 OCR 端到端测试，未下载模型；不把 skipped 算通过。本次未运行完整仓库测试。Ruff、相关 Python `py_compile` 和 `git diff --check` 通过。
- 当前代码 onedir 构建退出码 0，见 `test-results/p4-arw-webp/build-onedir-r9.log`；仅用系统 PATH、移除 `PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV` 的冻结 `PhotoSearch.exe`，在独立数据根扫描 a7 III ARW、Google 公开 WebP 与合成透明 WebP，均生成 JPEG 缩略图和预览（`test-results/p4-arw-webp/frozen-smoke-r9.json`）。分别核验产物尺寸：ARW 320×214 / 1600×1069，真实 WebP 320×214 / 550×368，透明 WebP 320×160 / 800×400。为限定媒体 smoke，构建未传入可选地点数据包，进程记录缺少该数据包的警告；此结果不证明地点功能、跨机、GPU 或正式发布资格。

真实竖拍、GPS、face/OCR 真实模型和语义检索、完整恢复故障矩阵及独立 DS-H 全影响面审查仍 PENDING；不标 SCOPED PASS。未跑全仓、30 万测试或真实图库。
