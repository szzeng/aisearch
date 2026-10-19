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

## 2026-10-19 第一阶段：CR2 人脸补建与批量 SQL 修复

- **基线与根因分析**：在当前 `master` 检查了 `9adbad5`、本卡和上节报告。`9adbad5` 的一次性恢复只覆盖当前 SHA、选中来源、当前 task/processor、`FAILED/BLOCKED + FORMAT_UNSUPPORTED` 且文件头为 CR2 的 job；`DECODE_FAILED`、模型/权重缺件、其他失败码、身份变化、非当前 profile、成功零脸和有效已发布产物都应保留/跳过。`face_build_profile` 在来源范围持久化，普通 `image_face` 与自动详情 `image_face_detail_auto` 不是同一 job；显式 build 在模型权重验证失败时先跳过该阶段，不会把缺件误当成可恢复格式错误。因未获本次 `PhotoSearchData` 授权，无法归因到具体图库资产或比较其缩略图/预览缓存、Face Job、错误码、profile 和 PUBLISHED 文档；没有把这些代码路径推断写成该库的已证根因。
- **已证代码缺陷**：有界 `_drain_scope` 每页调用 `face_targets(asset_ids=...)`/`ocr_targets(asset_ids=...)`，旧实现仍查询所有 PRESENT 资产再在 Python 中筛选；多页建库会反复扫描全库。修复为 SQL 中按 asset ID（以及可选来源）筛选，并将 limit 放入 SQL；空 asset 列表不发 SQL。`face.detect()` 还曾为每个目标单独读取 job ID，改为最多 800 个资产一批查询并按五字段身份过滤当前 revision。
- **隔离验证**：新增测试检查两个 targeter 的参数化 `IN` 范围、空范围零 SQL、人脸 job 身份一次批量读取；追加已有 build profile/零脸夹具，确认第二次 build 对已 SUCCEEDED 项 `steps=0` 且 executor 调用数不增加。定向组合：`44 passed, 0 failed, 14 skipped`，JUnit `test-results/p4-cr2-first-stage-20261019-cr2-affected.xml`；14 项均因 pinned buffalo_l / PP-OCRv4 权重未安装而跳过。一次修复测试插入位置后产生的 fixture `NameError` 已修正并重跑通过；原始 JUnit `test-results/p4-cr2-first-stage-20261019-cr2-bounded.xml` 保留，不覆盖。
- **模型与真实样片边界**：此前明确授权的 `D:\SZ\天空\100CANON` 中五个既有抽样位置仍存在（本轮只核对目录条目和权重能力，未运行人脸模型）。当前 pinned 人脸和 OCR 权重均未安装（对应测试 skip）；因此没有真实人物 CR2 的模型检测框、人脸产物或“继续建库”端到端结果。未读取 `PhotoSearchData`，未写入真实照片/缓存。此前五张样片的 RAW-only 画幅观察只作为历史证据，不等同于本轮缓存/job/profile 对照。
- **检查**：Ruff、相关文件 `py_compile`、`git diff --check` 通过；未修改 schema 0035、未删除错误证据、未强制重跑人脸、未动人工归属。没有触发 30 万图库测量。
- **独立只读代码审查**：审查未发现本次有界查询/job lookup 改动的直接回归；提出 P2：build 幂等用例是 JPEG+合成 executor，CR2 旧格式测试只调用恢复函数，缺少“真实人物 CR2→build→真实 face result”和同一 CR2 重建零推理。此项已纳入必需验收缺口。该审查不是卡片要求的 DeepSeek DS-H。
- **尚未通过/待审**：真实人物 CR2 的实际模型及发布验证、真实缓存/job/profile 对照、内置浏览器继续建库与打开照片不触发任务、D4 余下恢复/人工保护/OCR profile 矩阵、独立 DS-H 全受影响面审查仍 PENDING。当前仅证明 SQL 工作量边界和合成零脸幂等；不得标 `CR2 RECOVERY VERIFIED` 或整卡 PASS。

## 2026-10-19 第二阶段：CR2 缩略图与预览缓存补建

- **基线**：当前 HEAD `ca1e877` 包含第一阶段提交 `b44df10`；没有改回 schema 0035 的人脸/OCR 一次性恢复机制。
- **实现**：显式 build 的 thumbnail 分页先一次性批量取本页 CR2 asset/SHA/location 身份；对本页 CR2 调用既有 `catalog-preview@1` pipeline 补缺失预览。已发布缓存仍由 `MediaGenerator` 命中并绕过 RAW 解码；按 SHA 内容寻址，当前 processor 版本失效时新路径自然补建。daily refresh 与前台媒体 GET 不运行来源批量工作。
- **旧失败**：在页面媒体生成前快照旧 `FORMAT_UNSUPPORTED` 缓存；只对无有效 artifact、文件头验证为 CR2 的历史 thumbnail/preview failure retry。新失败不在同一页立即重试；失败重试写入 `cr2_format_retry.attempted` 和完整 prior failure，后续 build 跳过，避免无限重试且保留原证据。其他失败码不做自动恢复。
- **隔离验证**：新增 synthetic CR2 header + monkeypatched decoder 的媒体/API 集成用例。首次 build stage 生成 thumbnail/preview 时 decoder 长边调用为 `[320, 1600]`；第二次 stage 两者新增生成均为 0、无新增 decoder 调用；随后第一次 `/preview` GET 在 decoder 被设置为“调用即失败”时仍返回 200。旧缓存 `FORMAT_UNSUPPORTED` 失败各被 retry 一次，失败 JSON 保留 prior message 与 attempted marker，第二次 build 不再 retry。受影响 `tests/unit/test_p4_cr2_media.py tests/integration/test_api_files.py`：**19 passed, 0 failed, 0 skipped**；最终 JUnit `test-results/p4-media-cr2-phase2-20261019/cr2-media-focused-final.xml`。Ruff、相关 `py_compile`、`git diff --check` 均通过。
- **验证边界**：集成使用合成 CR2 头与假 decoder，调用 build 实际共用的 `_thumbnail_stage`，不是真实“继续建库”API整条流水，也未使用真实 CR2 RAW 数据/LibRaw 模型；因此 RAW-only 的真实 CR2 缓存补建和解码耗时仍待真实样片验证。没有触碰 `PhotoSearchData`、原始图片或人脸归属；没有启用内嵌 JPEG、全局搜索改动或 30 万性能测量。独立 DS-H 受影响面审查、真实 CR2 首开和重复建库证据仍 PENDING，整卡继续为 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**。
- **复现**：`.venv\Scripts\python.exe -m pytest -q tests/unit/test_p4_cr2_media.py tests/integration/test_api_files.py --junitxml=test-results/p4-media-cr2-phase2-20261019/cr2-media-focused-final.xml --basetemp=.pytest-tmp/p4-media-cr2-phase2-final`。

## 2026-10-19 专项性能审查与最小优化

- **重复 RAW 解码**：显式 build 的一页 CR2 原先先生成 thumbnail，再生成 preview；两次 `MediaGenerator.build()` 分别调用 `load_normalized()`，所以两种缓存均缺失时每个 CR2 做两次完整 RAW decode。新增配对路径：只对 build 页内、当前来源范围确认的 CR2 缺失缓存，从规范方向的 RAW 图像一次解码，分别按现有 320/1600 contract 直接生成两个 JPEG；已经发布的单项缓存照常跳过。普通照片、daily refresh、前台打开路径不改。输出使用 Pillow 相同 aspect rounding、Lanczos、reducing gap、quality；多种横竖画幅的 paired 输出与既有独立 `render_jpeg()` 逐字节相同，规范原图不被缩放/变更，RAW 方向及人脸坐标空间保持原入口。
- **冷/暖合成测量**：隔离 catalog、合成 CR2 文件头和假 decoder，输入 6000×4000 synthetic frame。此前同一专项的独立渲染路径记录冷缓存 253.62 ms、decoder 调用长边 `[320,1600]`（2 次）；配对路径两次重复测量为 217.03/227.70 ms，decoder 仅调用一次（长边 1600），两种产物共 668,025 bytes 且与旧 renderer 输出完全一致。暖缓存为 0.62/0.65 ms，新增 decode=0、缓存生成=0；首次 preview GET 使用禁止 decoder 的替身仍成功。该单样本 timing 只作为本机合成对照，不是吞吐承诺；两个缓存仍都需要，所以最终缓存盘占用不减少。优化减少了一次完整 RAW decode 和相应分配/解码 CPU。
- **恢复 SQL 与索引计划**：历史查询原来按 `(processor_id,state)` 读取旧失败 job，再用 `a.asset_id=CAST(j.subject_id AS INTEGER)` 连接，合成 DB 的 `EXPLAIN QUERY PLAN` 为 `idx_jobs_processor_state`，每页以 8 个资产过滤前仍反复读取该 profile 的历史状态集合。新 join 用字符串规范 job identity `j.subject_id=CAST(a.asset_id AS TEXT)`；SQLite 计划先按来源/当前页资产走 `idx_file_locations_present_source_asset`，再按 `(subject_type,subject_id)` 走 `idx_jobs_subject`。在 3,000 assets / 15,000 jobs / 3,000 条相符旧 CR2 失败的隔离库中，100 次相同 8-ID 查询都返回 800 行：旧路径 230.26 ms，新路径 4.14 ms。恢复 artifact 查询以前即使有 job ID 候选，planner 仍选 `idx_artifacts_lifecycle` 扫 PUBLISHED 子集；现在显式使用既有 `idx_artifacts_job` 按当前候选 job IDs 批量查找。集成测试对实际 SQL 执行 EXPLAIN 并断言两条索引路径。空来源列表现在直接返回，不退化为全来源查询；asset IDs、task/profile、失败类型及原有 source/page 条件均保留。schema 0035 marker、CAS、旧错误证据和 identity 未变。完整合成 EXPLAIN/计时见 ignored `test-results/p4-media-cr2-perf-review/sql-plan-benchmark.txt`。
- **幂等/范围审查**：已有 Face build profile 合成用例重复执行后 steps=0、executor 调用不增加；媒体冷/暖测试证明 CR2 缓存命中后 decode/generate 均为 0。OCR 实现复核显示 enqueue 对已完成五字段 identity 不会 rewind，queue drain 仅认 QUEUED/RETRY_WAIT/BLOCKED/RUNNING job；补充 OCR CLI 测试在真实 pinned weights 可用时重复 drain 并断言 steps=0。本环境该测试因 pinned 权重缺件跳过，未作为本机 OCR 推理验收。配对缓存 candidate 与 location SQL 限定 build 页和本次所选来源；没有为每日 refresh 或浏览请求加扫描/任务。
- **变更与测试**：`test_p4_cr2_media.py`、`test_media_decode.py`、`test_api_files.py`、一次性 CR2 recovery 集成测试、Face profile 幂等测试组合 **52 passed / 0 failed / 0 skipped**（JUnit `test-results/p4-media-cr2-perf-review/focused-final.xml`）。另行 OCR 重复 drain 用例 **1 skipped**（缺模型权重，JUnit `test-results/p4-media-cr2-perf-review/ocr-idempotency.xml`）。缓存 timing JUnit：`test-results/p4-media-cr2-perf-review/cache-cold-warm.xml`。Ruff、受影响模块 `py_compile`、`git diff --check` 通过。一次早期 EXPLAIN 测试在 artifact SQL 增加 `INDEXED BY` 后仍用旧 substring 匹配而失败；仅测试 selector 修正，定向复跑通过，原 JUnit 保留在 `evidence.xml`。
- **Luna High 独立复核**：首轮指出 P2：配对生成发现来源内 `.cr2` 不是 CR2 header 时，fallback 的普通 `build()` 会重新查该资产所有位置，可能读出所选来源外文件。修复为 `build()` 支持并应用 `source_ids`，fallback 透传相同来源集合；新增伪 CR2 + 另一来源位置的隔离测试，断言 SQL 参数和 decoder 路径均限于所选来源。复核跟进确认 P2 已关闭、无新增问题，并运行 31 项定向媒体/恢复测试通过。此为本专项性能差异的 Luna H 复核，不替代任务卡要求的完整 CR2 恢复面 DS-H。
- **未验证与剩余风险**：没有真实 CR2/LibRaw 冷解码对照，没有真实人脸人物或 OCR 模型运行，也未读取 `PhotoSearchData`。没有运行 30 万库性能测试。合成 image bytes 证明 resize 路径等价，不证明所有相机 RAW 解码器的色彩/方向实现。任务卡要求的真实人物 CR2→face result、OCR 文字样片、active model、人工状态/历史状态矩阵、浏览器继续建库及独立 DS-H 受影响面审查仍 PENDING；内嵌 JPEG 继续关闭。P4-MEDIA-CR2-A 仍 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**，本次局部性能审查不代表整卡 PASS。
- **复现**：`uv run --no-sync pytest -q tests/unit/test_p4_cr2_media.py tests/unit/test_media_decode.py tests/integration/test_api_files.py tests/integration/test_p4_02y_library_refresh.py::test_cr2_format_failure_is_recovered_once_and_original_error_is_retained tests/integration/test_p4_person_detail_b.py::test_build_actual_face_stage_profiles_zero_faces_and_paused_job_isolation --junitxml=test-results/p4-media-cr2-perf-review/focused-final.xml --basetemp=.pytest-tmp/p4-media-cr2-perf-review-final`；OCR 专项按 `test-results/p4-media-cr2-perf-review/ocr-idempotency.xml` 运行。未提交 Git。


## 2026-10-19 artifact identity recovery SQL follow-up

- **发现**：`artifacts.job_id` 指向首次发布该逻辑产物的 job；schema 0003 的逻辑唯一键实际是 `(processor_id, input_revision, artifact_type, artifact_revision)`。手动 `image_face_detail` 与自动 `image_face_detail_auto` 使用同一 processor、输入 SHA、structured 类型及 source-detail artifact revision，因而可合法复用同一 artifact 行。原 `job_id IN (candidate_job_ids)` lookup 会漏掉由另一 profile job 发布的共享产物，把已有发布结果误判为缺失并恢复失败 job。
- **修复**：恢复仍只在本页已验证 CR2 候选上批量查找，改按候选 `input_revision` 与完整逻辑唯一身份匹配 `PUBLISHED structured` artifact，补全 artifact type 条件后由 SQLite 选择既有 `sqlite_autoindex_artifacts_2` 复合唯一索引；EXPLAIN 断言为 `SEARCH artifacts USING INDEX sqlite_autoindex_artifacts_2`，无 artifact 全索引扫描。未增表/索引、未改变 schema、job identity、恢复 marker、原错误或人工状态，也未触碰 RAW 解码优化。
- **回归测试**：新增隔离合成场景：手动 source-detail job 为成功产物的 `job_id`，自动 source-detail job 是同 SHA 的 `FORMAT_UNSUPPORTED` 候选；恢复正确计为 published skip，不 rewind 自动 job、不写 recovery marker。原 CR2 一次性恢复用例继续验证无共享产物时仍能恢复。最终 **2 passed / 0 failed**，JUnit `test-results/p4-cr2-artifact-identity/focused-final.xml`；查询计划、逻辑键筛选和不使用 job_id 条件均有断言。首次测试因误选主键自动索引出现 1 项断言失败，随后经隔离库 `PRAGMA index_list/index_info` 校正为复合唯一索引 `_2`，保留原始结果于 `test-results/p4-cr2-artifact-identity/focused.xml` 与 `focused-r2.xml`。
- **边界**：只用隔离合成数据库/CR2 header，不访问 `PhotoSearchData` 或真实照片，不运行 30 万图库测试，不重新执行 RAW 优化。该修复验证 artifact 共享与恢复身份逻辑，不证明真实图库中存在此模式或真实模型/CR2 端到端。P4-MEDIA-CR2-A 仍 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**。
