# P4-00C 独立 DS-H 全量工程主审报告

被审对象：**P4-00C-A（HEIC 业务实现）** + **P4-00C-B（HEIC 环境与正式包兼容）**。
本报告是 `docs/tasks/P4-00C-B.md` 停止点要求的**独立 DS-H / DeepSeek V4.1 Flash / 高推理**全量工程主审，
只有本报告通过才允许把总任务标 **P4-00C PASS** 并生成下一卡。

主审原则：不引用被审报告的任何结论作为证据。下表每一行都来自本会话真实执行的命令与产物文件；被审报告的
数字只用于对比是否一致。

## 1. 观察基线与被审交付物

- Git 基线：`2b8a751`（`P4-00A/B` 收口提交）。主审开始时：`14 modified + 9 untracked`（不含被忽略的
  `.pytest-*` 临时目录），与本卡两段报告 §11/§8 的自述一致。
- 生产面改动：`src/photo_search/media/{heif.py(新),contract.py,decode.py,face_crop.py}`、
  `src/photo_search/geo/extract.py`、`src/photo_search/taken_time/exif.py`、
  `src/photo_search/search/upload.py`、`pyproject.toml`、`uv.lock`。
- 环境面改动：`tools/packaging/build_onedir.py`（+5 行）、`tools/diagnostics/packaged_api_smoke.py`、
  `tools/diagnostics/heif_degraded_bundle.py`（新）、`ruff.toml`、`.gitignore`。
- 测试面改动：`tests/unit/test_p4_heic_media.py`（新）、`tests/integration/test_p4_heic_pipeline.py`（新）、
  `tests/p4_heic_fixtures.py`（新）、`tests/unit/test_media_decode.py`、`tools/diagnostics/media_artifact_fingerprint.py`（新）。
- 文档面：两份被审报告 + `docs/development-status.md`。
- 环境事实（非产品缺陷，记录备查）：工作树内存在受限令牌无法枚举的 `.pytest-tmp-p308c-sol-20260922a/`
  （`git status` 会打印 `Permission denied` 警告）；`ruff check .` 在该令牌下会打印一条 `os error 5` 遍历警告，
  但结论仍是 `All checks passed!`。本主审未改任何 ACL。

## 2. 独立复现的命令与结果

| 项目 | 命令 | 独立结果 |
|---|---|---|
| B 级全量 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-basetemp-p400c-review/full --junitxml=test-results/p4-00c-review-full.xml` | **1633 / 0 / 0 / 48**，exit 0，203.501 s（`test-results/p4-00c-review-full.xml`） |
| 聚焦套件 | 同上，目标 `test_p4_heic_media.py test_media_decode.py test_p2_search_upload.py test_p4_heic_pipeline.py` | **98 / 0 / 0 / 0**，exit 0（`test-results/p4-00c-review-focused.xml`） |
| Lint / 空白 / 锁 | `uv run --no-sync ruff check .`；`git diff --check`；`uv lock --check` | 全部 exit 0；lock `Resolved 96 packages` |
| C 级真实构建 | `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0；`dist/PhotoSearch` 3405.4 MB / 2756 文件；`PhotoSearch.exe` 35 877 794 B |
| 打包 smoke（健康包） | `packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir <.packaged-review-smoke-healthy 中文 空格>` | **status=ok / problems=[]**（`test-results/p4-00c-review-packaged-smoke.json`） |
| 降级三变体 | `heif_degraded_bundle.py --remove {libheif,extension,both}` + 对应 `--heif-degraded` smoke | 三份全 **status=ok / problems=[]**（`test-results/p4-00c-review-degraded-*-smoke.json`） |
| D-5 产物指纹 | `media_artifact_fingerprint.py`，两侧分别用 `PHOTO_SEARCH_SRC=.p4-00c-a-scratch/baseline/src`（`git archive 2b8a751 src` 解出）与当前 `src` | 两侧各 18 行；文件 sha256 均 `b8df55b5afc164919530fd95aaed61586c8088db52b1f5f1d4477a36514e7b9c` |
| 禁止面差分 | `git diff --stat 2b8a751 -- src/photo_search/catalog/migrations docs/decisions src/photo_search/taken_time/contract.py src/photo_search/web PhotoSearch.spec` | 为空；全基线仅 `tools/packaging/build_onedir.py` +5 行 |

basetemp 的父目录需先存在（本机 pytest 对显式 `--basetemp` 不做 `parents=True`），这是环境事实；
`%TEMP%` 写入与 FTS5 探针必须走正常 Windows 权限路由，否则会得到 `WinError 5` / `unable to open database file`
的**环境失败**（不是代码证据）。以上两次干扰均为本主审会话内的环境处置，未改代码、未改 ACL。

## 3. A 段业务面逐条复核（读实现 + 跑真实路径）

1. **格式闭集**：`contract.supported_input_formats()` = 基础集 ∪（真实加载成功时的 `HEIC/HEIF`），
   `refused_input_formats()` 在不可用时把两者并回拒绝集；`media/contract.py` 顶层常量只是
   import 时快照，生产调用点（`decode.plugin_format_decision` / `_unsupported`、
   `upload.upload_refused_families`、`routes.capabilities` 经由 `MediaSpec.as_dict()`）全部读函数。
   主审用 grep 核对：`src/**` 中没有第二处读旧常量的判定路径。
2. **注册唯一性与幂等**：`src/photo_search/media/heif.py` 是唯一出现 `pillow_heif` 的生产模块
   （`tests/unit/test_p4_heic_media.py::test_only_one_source_module_talks_to_pillow_heif` 把这条规则固定成断言），
   `ensure_heif_support()` 用 `_LOCK` + `_PROBED` 幂等，并且**原子**：注册前快照
   `Image.{OPEN,ID,SAVE,SAVE_ALL,MIME,EXTENSION}`，try 内强制读一次 `libheif_version()`，失败即原地回滚。
3. **fail closed**：不可用时不抛异常，`heif_available()` 为假 ⇒ `Image.open()` 抛
   `UnidentifiedImageError` ⇒ 嗅探 `HEIF` ⇒ 拒绝闭集 ⇒ 结构化 `FORMAT_UNSUPPORTED`。健康包/降级包两侧都
   在打包进程里得到同一结论（见 §4）。
4. **坏文件隔离**：截断、`ftyp` 空壳是每文件 `DECODE_FAILED`，同一批次好文件照常 READY；
   `test_a_supported_format_with_broken_bytes_is_a_decode_failure` 与集成 D-1 分别覆盖单元与批次层。
   该断言相对基线的**语义变化是任务要求的方向**（HEIC 之前是「识别并拒绝」，现在是「可解码 ⇒ 坏字节是解码失败」），
   不是削弱既有期望：`test_unverified_formats_are_reported_as_unsupported` 用 AVIF 补位，拒绝闭集覆盖率未降低。
5. **方向语义**：`_restore_recorded_orientation()` 只在 HEIF 且容器**无** `irot`/`imir` 时把
   `info["original_orientation"]` 写回内存 EXIF，再交给同一个 `ImageOps.exif_transpose`；
   `read_orientation()` 用同一判别器，因此 `probe_original_frame()` 与解码帧一致。四个组合
   （两种形态 × 方向 6/8）都用四象限颜色对照独立期望图，能同时抓漏旋转、反向旋转和双重旋转；
   非 HEIF 图像的行为与基线逐字相同（`exif` 为空或越界仍返回 1）。
6. **上传面裁定**：`UPLOAD_INPUT_FORMATS = {JPEG,PNG,WEBP,BMP,GIF}` 成为该端点**自有**的冻结白名单（**已被 [P4-02T](P4-02T-upload-format-policy.md) 修订，见该报告 §2/§6**），
   `_decode_upload` 不再借用库内能力；`P2-07` 的既有测试文件未被修改却仍然全绿，说明该端点的既有断言
   未被削弱。真实 HTTP 样本得到 `409 FORMAT_UNSUPPORTED`、`details.format="HEIF"`、
   `details.supported_formats` 为上传白名单，与报告 §5 一致。
7. **依赖**：`pyproject.toml` 精确固定 `pillow-heif==1.8.0`，`uv.lock` 96 包；Pillow 未升级；
   `P4-00A/B` 契约面（`taken_time`/`capture_desc`/`search_projection`/伴随物/`0001`～`0015`）相对基线零改动。

## 4. B 段环境面逐条复核

**4.1 最小差异与根因**：`tools/packaging/build_onedir.py` 只加 `--collect-all pillow_heif`；`PhotoSearch.spec`
（`.gitignore` 内、由构建生成）未被改动。主审确认该规则的必要性：`pillow_heif` 只被
`media/heif.py` 在**能力探测之后**动态 import，PyInstaller 的静态分析看不到它，而 wheel 的 `.pyd` 与
`libheif-*.dll` 是 site-packages 根目录的**同级**两个文件（delvewheel 布局）。

**4.2 包内组件与一致性**（主审独立重建后直接核对文件哈希，不只看 smoke 的自述）：

| 包内相对路径 | 字节数 | SHA-256 | 与 `.venv`/`src` 来源 |
|---|---|---|---|
| `_internal/_pillow_heif.cp312-win_amd64.pyd` | 59904 | `58ccecb79a7e5265e3e25154683635ed801a34a347dab81024c0e9b089de10b4` | 同源哈希一致 |
| `_internal/libheif-30fc1b3ceee9088934615cbf4948eba3.dll` | 2201088 | `6223de1181ea35f394b380f2142be64406659ebf7026a2cb61d181c16e42ae9b` | 同源哈希一致 |
| `_internal/web/app.js`（抽查 P4-00B 缺陷类） | — | `f1bec87e3174…` | 与 `src/photo_search/web/app.js` 一致 |
| `_internal/photo_search/web/search.js` | — | `14fde130b7ab…` | 与源码一致 |
| `_internal/photo_search/catalog/migrations/0015_taken_time.sql` | — | `4e2cfa09cc4f…` | 与源码一致（同 P4-00B 冻结值） |

smoke 的 R1 断言（P4-00B 主审建议）已落地：包内 `photo_search` 的**全部**非 `.py` 数据资源
`matches_source=true`、`paths == expected_paths`，共 **27** 个（15 个 migration + 12 个 `web/*`）；
`bundled_migrations.matches_source=true`。P4-00B 那类「陈旧 `app.js`」在本次重建中不存在。

**4.3 打包 HEIC 端到端（打包进程，不是源码进程）**：`asset_count=5`、`scan_state=SUCCEEDED`；
HEIC 缩略图 `200 image/jpeg`、预览 `200`，尺寸 **[320,240]** 与同像素同方向的 JPEG 孪生 **[320,240]** 完全一致；
损坏 HEIC `409 DECODE_FAILED` 且进程未崩（`health=200`，`app_stderr_tail` 无 traceback）；
能力清单**宣传** HEIC。打包 data root：`schema_version=15`、`rules_version=1`，`asset_metadata`、
capture 索引、闭集来源齐全。

**4.4 打包服务语义**：同墙钟的对照对经打包详情端点发布为
`EXIF_ORIGINAL/HIGH/offset=480min` 与 `EXIF_ORIGINAL/MEDIUM/offset=null`，
两者 `taken_at=2022-07-05T18:31:07`、`taken_at_sort=63792642667000` 完全相同、`label=capture`；
两条 `asset_geo_facts` 均为 `VALID/WGS84`、经纬度一致。夹具的 civil key 与 geo-fact id 由**源码侧**契约计算，
由**打包侧**读回，因此这条断言确实在检验两侧一致，而不是自证。报告已把「生产 taken-time 路径未在包内运行」
写成边界，主审接受该边界（生产者语义由 A 段真实调度器/抽取器证据覆盖）。

**4.5 降级 fail closed（独立副本）**：`libheif` / `extension` / `both` 三变体各跑一次，
`present_kinds == expected_kinds`，HEIC 缩略图/预览/坏文件**三者**均 `409 FORMAT_UNSUPPORTED`，
能力清单不宣传 HEIC，JPEG 孪生仍 `200` 得到 `320×240`，既有缓存字节不变
（`offline_thumbnail_bytes_unchanged=true`）。这正是 C1 段发现、A 层修复的缺陷（半注册的 Pillow 打开器
把 `FORMAT_UNSUPPORTED` 退化成 `DECODE_FAILED`）的真实回归面；单元侧
`test_a_registration_that_fails_after_installing_the_opener_is_rolled_back` 忠实复现了「先注册打开器、后读运行库失败」的
注册顺序，因此该修复不会在下一次 pillow-heif 升级中静默丢失。

## 5. 主审额外独立探针（被审报告未覆盖）

1. **缺失 codec 插件（不是缺 libheif）**：在独立副本中删除 `libde265-*.dll` 与 `libx265-*.dll`
   （保留 `_pillow_heif` 扩展与 `libheif-*.dll`）后重跑打包 smoke：HEIC 三者仍 **`409 FORMAT_UNSUPPORTED`**，
   能力清单仍**不**宣传 HEIC，JPEG 孪生仍 `320×240`。
   ⇒ 结论：能力探测覆盖的不只是「libheif 能否加载」，还包括其 HEVC codec 是否可用，降级面比被审报告声称的更强。
   （该次运行唯一的 `problems` 项是我故意用错 `--heif-degraded` 模式造成的组件清单期望不匹配，
   不是产品缺陷：`test-results/p4-00c-review-probe-missing-codec-smoke.json`。）
2. **基线侧身份确认**：指纹工具的「改动前」一侧显式验证真的加载了 `git archive 2b8a751 src` 那棵树
   （`module_file` 指向 scratch 基线、`photo_search.media.heif` 不存在、支持集为 `{BMP,GIF,JPEG,PNG,WEBP}`），
   因此 D-5 的「逐字节不变」不是同树自比。
3. **包体体积**：`dist/PhotoSearch` 3405.4 MB / 2756 文件；其中 `libx265-217-*.dll` 22.7 MB 是**编码器**，
   本产品只需解码。

## 6. ADR / 任务卡 / 冻结面一致性

- 允许范围：A 卡的允许域（`pyproject.toml`+`uv.lock`、`media/**`、`geo/**`、`taken_time/exif.py`、
  `search/upload.py`、相关测试与报告）与 B 卡的允许域（`PhotoSearch.spec`/`tools/packaging/**`、
  `packaged_api_smoke.py`、新增诊断工具与报告）**均未越界**：唯一的环境面改动是那 5 行收集参数，
  唯一的业务面改动是 `media/heif.py` 注册原子化。
- 禁止面：`ADR-0001～0011`、`docs/decisions/contracts/*`（全部伴随物）、`0001`～`0015`、
  `SUPPORTED_SCHEMA_VERSION`、`taken_time`/`capture_desc` 契约、`search_projection` 8 键、P3 人物/地点信封、
  `src/photo_search/web/**`、`PhotoSearch.spec`：`git diff --stat 2b8a751 -- <这些路径>` 为空（§2 表末行）。
- 上传面：任务卡 §4 要求的「显式裁定 + 真实 HTTP 结构化拒绝样本」两者都在（§3.6），且**未**产生
  加性协议变更，因此 A 卡「否则记 `SOL_NOT_REQUIRED`」的条件成立。
- 依赖与迁移：`uv lock --check` 96 包；迁移与 schema 未动。
- V3.1 §2.2 六类条件逐条核对：无跨模块无法定根的异常；无 transaction/revision/undo 矛盾
  （只有单点注册顺序缺陷，根因与最小反例都已闭合）；测试与 ADR 无冲突；没有第二/第三处回归；
  大 diff 中无多状态机耦合（改动集中在媒体格式策略 + 打包收集）；无 Sol 提问。
  ⇒ **不升 DS-MAX**，也不新增 Sol 聚焦终审：HEIC/媒体格式策略不在 V3.1 §11 的高风险清单内，
  §9 的 P4-00C 行本身也只要求「C1 环境 + DS-H 全量工程主审」。

## 7. 主审发现

**阻断级：无。**

### R1（P3，非阻断，打包审计覆盖面）

`packaged_api_smoke.heif_components()` 与三种 `--heif-degraded` 变体只覆盖 `_pillow_heif*` 与
`libheif-*.dll`；而包内实际还有 5 个 delvewheel 同级运行库被 PyInstaller 作为扩展的传递依赖收进来：
`libde265-0-863aced2…dll`（960000 B，与 `.venv` 同源哈希一致）、`libx265-217-8a7f7f4e…dll`（22667339 B）、
`libgcc_s_seh-1-7579db3c…dll`（147968 B）、`libstdc++-6-a168feb8…dll`（2652672 B）、
`libwinpthread-1-cd4f4bcf…dll`（62976 B）。它们不在审计清单与降级矩阵里，属于「覆盖声明窄于真实依赖」。
本次已用 §5.1 的补充探针证明缺 codec 时仍 fail closed，风险已被独立覆盖；建议在 **P4-07/P4-08** 的
正式包验收里把组件清单扩展为扩展 + 全部同级运行库（名称/字节/SHA-256），并把「缺 codec DLL」列为第四个
降级变体。**不影响本卡 PASS。**

### R2（P3，非阻断，能力快照重复）

`contract.SUPPORTED_INPUT_FORMATS` / `UNSUPPORTED_INPUT_FORMATS` 现在是 import 时快照，其值取决于
import 那一刻插件是否加载成功；生产调用点已全部改读 `supported_input_formats()` /
`refused_input_formats()`，但这两个常量仍作为「可过期副本」留在模块里（`tests/unit/test_media_decode.py`
还在读它）。建议下一次触碰该模块时把常量收敛为函数（或标注为 deprecated），避免将来出现
「同一个进程里两份能力答案」。**不影响本卡 PASS。**

### R3（P3，非阻断，方向判别器的失败方向）

`heif.container_orientation_applied()` 读不到私有句柄时返回 `None`，按已裁定的规则「保持 pillow-heif
自己的声明」——这在极端情况下是**偏向错图**而不是偏向拒绝。当前由「pillow-heif 精确钉版本 +
`test_the_pinned_encoder_really_writes_both_orientation_shapes` 断言两种形态可观测」这一对约束守住。
建议把「升级 pillow-heif 必须同时保留/复核该判别器测试与两形态夹具」写进 P4-07 的依赖升级清单。
**不影响本卡 PASS。**

### R4（P4，信息项）

冻结包 3405.4 MB；其中编码器 `libx265` 占 22.7 MB，产品只用解码。若后续关心发行体积，可在
P4-07 评估是否排除该 DLL（需先确认 libheif 不依赖它加载）。

## 8. 未验证项（继承 A/B 报告，本主审未推翻，仍不得当 PASS）

1. 真实设备 HEIC 样本（本机无）：全部结论来自合成夹具。
2. HEIC 变体：10/12-bit、网格（tiled）、多帧/序列（`msf1`/`hevs`）、带 alpha/深度/辅助图。
3. HEIC 的 XMP 方向、以及「容器 `irot` 与 EXIF 方向同时非 1」的非规范文件。
4. 大 HEIC（如 48 MP 手机原图）在源码进程与打包进程里的真实耗时/峰值内存；30 万容量下的 HEIC 吞吐。
5. 多进程/并发下的 pillow-heif 线程与 DLL 加载行为；`pillow-heif` 版本错配。
6. 打包进程的 taken-time **生产**路径（`python -m photo_search.taken_time.cli` 不能在 onedir 里运行）；
   本卡只验证服务/读回语义。
7. 真实 Windows 用户环境的安装/升级/卸载回归；GTX 1660 实机与 CUDA 路径（本卡全为 CPU/打包面）。
8. 本主审未做的：未复跑 A 段记录式浏览器步骤（该步骤属 P4-00B 与本卡无关）、未做 30 万规模基准
   （属 P4-01/P4-02）。

## 9. 结论

- **P4-00C-A：PASS（B + D）**；**P4-00C-B：PASS（C）**；**P4-00C：PASS**。
  判定依据是上表全部必需验收（B 全量、聚焦、D 级注入、真实构建 + 打包 smoke、降级 fail closed、
  真实 Git/哈希一致性、ADR/任务卡一致性）均由本主审独立复现，且无阻断级发现。
- `SOL_NOT_REQUIRED` 复核通过：upload 白名单未变、无任何已冻结信封/错误码集合变更、
  ADR/companion/migration 零改动，唯一的业务修复是把降级答案恢复成契约早已要求的 `FORMAT_UNSUPPORTED`。
- 未命中 V3.1 §2.2 六类条件，不升 DS-MAX，不需要额外的 Sol 阶段。
- 下一任务：**P4-01 — 10 万/30 万 exact/ANN 对照、索引维护、过滤分布与并发调度基准**
  （`docs/tasks/P4-01.md`）。推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**，与当前档一致，可继续使用当前模型。

## 10. 本主审产出的证据文件

| 文件 | 内容 |
|---|---|
| `test-results/p4-00c-review-full.xml` | B 级全量 1633 / 0 / 0 / 48 |
| `test-results/p4-00c-review-focused.xml` | 聚焦 98 / 0 / 0 / 0 |
| `test-results/p4-00c-review-packaged-smoke.json` | 健康包 status=ok / problems=[] 与全部打包断言字段 |
| `test-results/p4-00c-review-degraded-libheif-smoke.json` | 去 libheif 变体 |
| `test-results/p4-00c-review-degraded-extension-smoke.json` | 去扩展变体 |
| `test-results/p4-00c-review-degraded-both-smoke.json` | 两者皆去变体 |
| `test-results/p4-00c-review-probe-missing-codec-smoke.json` | 主审补充探针：缺 libde265/libx265 仍 fail closed |
| `test-results/p4-00c-review-fingerprint-baseline.txt` / `-current.txt` | D-5 两侧 18 行载荷哈希，sha256 相同 |

一次性材料（`.packaged-*` 降级副本、`.packaged-review-*` smoke 工作目录、`.p4-00c-a-scratch` 基线树）
只是可丢弃验证材料，已被 `.gitignore`/`ruff.toml` 排除，主审结束前清理。

## 11. 收口提交

主审通过后，P4-00C-A/B 的全部交付物（14 modified + 10 new，含两份阶段报告、本主审报告、
`docs/tasks/P4-01.md` 与 `docs/development-status.md`）作为**一个版本**入库：

```text
9b1ff20 P4-00C: HEIC decode/GPS/orientation isolation, packaged closure, DS-H A+B review and P4-01 card
        （父提交 2b8a751；24 files changed, 3100 insertions(+), 46 deletions(-)）
```

提交后工作树不再包含本卡任何未提交交付物；`git show --check 9b1ff20` 无空白错误。未推送（推送需单独授权）。
