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

## 2026-10-19 历史人脸/OCR 失败恢复续做

- **实现范围**：仅显式 `build` 的分页阶段调用恢复分类；查询当前页所选来源的 `PRESENT` `.cr2`，限定当前 face/OCR task 与 processor profile、终态 `FAILED/BLOCKED` 和 `FORMAT_UNSUPPORTED`。校验 job `input_revision` 等于当前 asset SHA 后才检查 CR2 内容头。历史 `DECODE_FAILED` 暂不恢复，因为尚无证据可区分旧解码器不兼容与文件损坏。普通 refresh、打开照片和手动单张详情不启动批量恢复。
- **一次性恢复与保留证据**：加性 schema 0035 表 `cr2_job_recoveries` 记录旧状态、错误、消息和五字段身份；事务内无 lease 条件 CAS，保留原错误并重置精确 job。相同 job 第二次失败时因持久 marker 不会再次自动回放。当前 profile 有效 PUBLISHED artifact 会跳过（零脸/空 OCR 同样算有效完成）；无效 artifact 计作完整性异常。恢复结果仅扩充现有 build 结果计数。OCR build drain 限定本页资产与 active CPU/GPU processor，不改变其他调用的默认行为。
- **验证**：新增合成 CR2 集成用例覆盖 `FORMAT_UNSUPPORTED` 一次恢复、原错误留存、恢复任务再次失败后不再 rewind：1 passed，JUnit `test-results/p4-cr2-recovery/focused-final-2.xml`。受迁移影响的 unit/integration 原始组合为 3212 项：3152 passed、9 failed、51 skipped，JUnit `test-results/p4-cr2-recovery/unit-integration-final-2.xml`。其中迁移恢复 fixture 漏列新表，已补后针对性复跑 1 passed，JUnit `test-results/p4-cr2-recovery/migration-final.xml`；原始完整运行仍如实记录。其余 8 项失败为人物页面资源缓存断言 2 项、搜索 UI harness 1 项、favorites API/UX 2 项、storage UI 1 项、timeline UI 2 项；没有 CR2 恢复用例失败。Ruff、相关模块 `py_compile`、本任务涉及文件的 `git diff --check` 通过。一次未指定隔离 basetemp 的定向重跑触发共享 pytest temp 清理 `WinError 5`；改用隔离 `.pytest-tmp/p4-cr2-recovery/focused-final` 后通过，未改 Windows ACL。
- **性能边界**：在来源现有分页内一次批量查询候选 jobs，并按候选批量查 artifact；只对匹配 SHA 的旧格式失败读 CR2 头。没有增加普通打开扫描或全库 SHA 重算。尚未实测稀疏/密集页 SQL、文件头和模型调用计数，也未证明第二次完整 build 的 0 推理。
- **Luna High 独立复核及修正**：首轮发现两项 P2：published payload 自身 profile 校验不足；分类后到 CAS 前未再次核对 asset SHA。现已增加普通/自动详情 face 文档 profile 身份检查，并在恢复 marker INSERT 和 job UPDATE 的 CAS 谓词中复核 `assets.sha256 = jobs.input_revision`。补充测试覆盖 profile mismatch 和 SHA 在 CAS 前改变；CR2 定向测试 2 passed，JUnit `test-results/p4-cr2-recovery/luna-fix.xml`。Luna H 对修正的跟进复核未发现新问题。任务卡规定的独立 DS-H 审查仍待执行，Luna H 结果不代替该门槛。
- **未完成**：D4 其他状态矩阵、来源重复/重启/取消、人工人脸事实与各类详情 profile、OCR active/retired 与模型缺件检查仍待测；未做内置浏览器流程、真实 CR2 人脸/OCR/语义模型端到端及独立 DS-H 全受影响面审查。故当前仍 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**，不记 `CR2 RECOVERY VERIFIED` 或 `SCOPED PASS`。未访问 `PhotoSearchData`。
