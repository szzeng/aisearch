# P4-MEDIA-CR2-A 执行记录

**状态：IMPLEMENTATION PARTIAL / VALIDATION PENDING**（2026-10-13）

## 已完成

- 固定依赖 `rawpy==0.27.1` 并更新 `uv.lock`；按 TIFF 容器 CR2 标志识别内容，不凭 `.cr2` 扩展名放行。RAW 解码串行执行，限定最大像素数，统一输出 LibRaw 方向校正后的 sRGB 8-bit 图像。非 CR2 TIFF 仍受拒绝策略约束。
- 媒体缩略图/预览、普通人脸、source-detail 人脸、OCR 复用 `load_normalized()`；缺少人脸检测文档时，CR2 原图坐标探测读取同一规范方向的有效画幅尺寸。
- 语义资格只对 CR2 内容头且 rawpy 可用时放行。建库和 `maintenance.reexecute_reclaimed_artifacts()` 都把同一规范 RGB 解码结果写入应用数据目录下的临时 JPEG，再调用已有 encoder 接口；encoder 返回后清理临时文件。
- 继续建库分批阶段额外核验 `.cr2` 文件头，仅对缩略图/预览失败缓存明确为 `FORMAT_UNSUPPORTED` 的 CR2 定向使用 `retry_failed=True`。不会因此重试其他格式或其他错误码；每日刷新不做历史全表补扫。
- PyInstaller onedir 配置收集 `rawpy` 包与本机运行时二进制。媒体能力探测增加 CR2 运行时事实及进程内 `cr2_preview_used`、`cr2_raw_fallback`、`cr2_decode_failed` 非敏感计数。

## 验证

- 定向组 A：`37 passed`：`test_p4_cr2_media.py`、`test_media_decode.py`、`test_p4_02o_semantic_build.py`。
- 定向组 B：`90 passed`：HEIC、MPO、人脸/OCR 几何、媒体缓存/失败隔离及语义 reexecute。
- `ruff check`（本任务变更的 Python 文件）：通过。
- `python -m py_compile`（适配器、媒体、人脸、语义、refresh、打包入口）：通过。
- `uv lock --check`：通过。
- `git diff --check`：通过。
- PyInstaller collector smoke：rawpy 0.27.1 可导入，收集到 `rawpy._rawpy` hidden import 和 `raw_r.dll`、`vcomp140.dll`。未构建完整 onedir 包。
- 两份 JUnit：`test-results/junit-p4-media-cr2-a.xml`、`test-results/junit-p4-media-cr2-a-regression.xml`。
- 两个已有测试的断言按当前已生效的图片白名单收敛：视频不再进入语义待选集合；`.txt` 不再作为媒体失败资产登记。该调整未改变产品实现。

## 待完成验收与限制

- 开始执行时未收到样片路径；用户随后授权使用 `D:\SZ\天空\100CANON`。真实样片补测见下节。没有访问真实 `PhotoSearchData`，没有改动原图。
- 为避免把“宽高比接近”误当作裁切原点相同，目前一律使用 RAW 规范画幅；`extract_thumb()` 内嵌预览尚未启用。因此预览优先策略本身尚未完成真实样片校准，`cr2_preview_used` 当前应为 0，不能宣称符合 A1 的真实样片门槛。
- 语义路径用假 encoder 验证了临时规范输入与清理；没有可授权 CR2 样片与活动 provider/model 的文字搜图结果，实际模型验证待完成。
- 历史补建现已覆盖媒体失败缓存的精确 `FORMAT_UNSUPPORTED` 定向重试；历史人脸/OCR 终态任务的 CR2 定向 rewind 尚未实现或验证。未执行实际“继续建库”，也未证明第二次真实 CR2 建库无重复。
- 完整打包运行和 Windows 真实 RAW 解码未验证。未运行全仓套件、GPU/大图库测试。

因此本卡不能记为 SCOPED PASS。样片目录现已授权并完成 5 张补测；仍需解决内嵌预览几何证据不足，并补齐真实人脸/OCR推理、有效 GPS/方向覆盖、活动模型验证和历史失败任务续建核对。

## 2026-10-13 样片续验

- 用户授权目录 `D:\SZ\天空\100CANON` 后，从按文件名排序的 2307 个顶层 CR2 中取 5 个分位样本，只读解码。报告不保存完整源路径或坐标。
- 5/5 均由 `rawpy.extract_thumb()` 取得 JPEG 内嵌预览，方向标记为 1、尺寸均为 6000×4000；LibRaw RAW 有效画幅均为 6024×4020，`flip=0`，规范解码尺寸均为 6024×4020。单次 `raw_cr2.decode()` 为 1.101–1.183 秒，均值 1.130 秒。进程计数为 `cr2_preview_used=0`、`cr2_raw_fallback=5`、`cr2_decode_failed=0`。
- 对预览与 RAW 输出做了只读 SIFT/RANSAC 几何诊断：4/5 样本有跨四象限内点支持，1/5 的匹配覆盖和拟合不可靠；不同样本的拟合变换也不一致。比例相近（预筛差异 <1%）不足以证明坐标映射/裁切原点可靠，因此本次不启用内嵌预览，也不将该诊断当作 crop PASS。当前 RAW 规范画幅使缩略图/预览、普通人脸、source-detail 解码入口和 OCR 解码入口都返回 6024×4020、`source_format=CR2`、`orientation=1`；真实人脸/OCR 模型推理与框坐标仍未验证。
- 5/5 的 `DateTimeOriginal`/digitized 字段及子秒可由现有 EXIF 时间读取器读取；方向为 1，无时区偏移。GPS IFD 在这些样本中只有版本标签、无坐标；现有解析结果为 `INVALID` 且无可搜索坐标。没有有效 GPS 样片，真实有效 GPS 兼容性仍待测。5/5 解码前后 SHA-256、文件大小和 mtime 均不变。
- 重新运行任务卡定向集合：9 个测试模块 **127 passed / 0 failed / 0 errors / 0 skipped**；JUnit：`test-results/junit-p4-media-cr2-a-validation.xml`。Ruff、`py_compile`、`uv lock --check`、本任务已跟踪文件的 `git diff --check` 均通过。PyInstaller `collect_all('rawpy')` smoke 检出 `rawpy._rawpy`、`raw_r.dll`、`vcomp140.dll`；没有构建完整 onedir 包。
- 未访问真实 `PhotoSearchData`，因此没有活动语义模型/provider 的 CR2 文字搜索证据，也没有真实目录建库/继续建库或第二次幂等证据。历史 `FORMAT_UNSUPPORTED` 媒体缓存的合成定向重试已有单测；CR2 旧终态人脸/OCR任务的定向恢复仍未实现/验证。

**续验结论：仍为 IMPLEMENTATION PARTIAL / VALIDATION PENDING，不是 SCOPED PASS。** 未覆盖竖拍/不同预览尺寸/有效 GPS、真实人脸与 OCR 推理、活动模型搜图、续建幂等和完整 frozen package；内嵌预览几何证据不足以安全启用。
