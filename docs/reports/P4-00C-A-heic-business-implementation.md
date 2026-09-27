# P4-00C-A 报告 — HEIC 业务实现：格式策略、方向/GPS/拍摄时间复用与坏文件隔离

本卡是 **P4-00C 的第一段（业务实现段）**，只做业务与数据语义：把 HEIC/HEIF 纳入已冻结的
解码 / GPS / 拍摄时间路径，证明它与同等 JPEG 得到一致结论，并让坏文件按 P1-03 既有语义被隔离。
**本卡不碰打包环境**：`PhotoSearch.spec`、`tools/packaging/**`、`tools/diagnostics/packaged_api_smoke.py`
与 C 级冻结包验收全部属第二段 **P4-00C-B**，本报告不声称任何 C 级通过，也不声称 P4-00C 整体通过。

观察基线：提交 `2b8a751`（P4-00A/B 收口提交，父提交 `8e48fc7`），开始前 `git status --short` 干净。
本卡全部测试均为合成夹具 + 项目内临时 data root，未扫描、未修改任何真实照片库。

## 1. 改动文件

生产代码（6 个文件 + 1 个新模块）：

| 文件 | 改动 |
|---|---|
| `src/photo_search/media/heif.py` | **新增**：唯一的 pillow-heif 注册点、可用性探针、`original_orientation` 与容器 `irot`/`imir` 读取 |
| `src/photo_search/media/contract.py` | 支持/拒绝闭集改为按**真实可解码能力**计算；`HEIC`/`HEIF` 进入支持闭集，其余拒绝项不变 |
| `src/photo_search/media/decode.py` | 解码前注册插件；恢复 HEIC 被清掉的 EXIF 方向；能力探针新增 `heif_support` |
| `src/photo_search/search/upload.py` | 上传面改为**显式自有**冻结白名单 `UPLOAD_INPUT_FORMATS`（语义不变，见 §5） |
| `src/photo_search/geo/extract.py`、`src/photo_search/taken_time/exif.py`、`src/photo_search/media/face_crop.py` | 只有一行最小改动：打开文件前调用幂等的 `heif.ensure_heif_support()` |
| `pyproject.toml`、`uv.lock` | 新增固定版本 `pillow-heif==1.8.0`；锁定包数 95 → 96 |

测试与工具：

| 文件 | 内容 |
|---|---|
| `tests/p4_heic_fixtures.py` | **新增**：合成 JPEG/HEIC 夹具（两种方向证据形态、GPS、EXIF 时间子 IFD） |
| `tests/unit/test_p4_heic_media.py` | **新增 19 例**：格式闭集、能力清单、解码/缩放、方向四象限对照、坏文件、插件缺失 fail closed、上传面裁定、子 IFD 可见性、注册唯一/幂等 |
| `tests/integration/test_p4_heic_pipeline.py` | **新增 12 例**：真实扫描 → 缩略图/预览、坏文件隔离、写入期变更、双位置确定性、GPS/拍摄时间 JPEG↔HEIC 对照、capabilities 与上传 HTTP 拒绝 |
| `tests/unit/test_media_decode.py` | 更新格式策略断言（HEIF 现在可解码、AVIF/TIFF/RAW 仍拒绝），新增「可解码格式的坏字节必须是 `DECODE_FAILED`」3 例 |
| `tools/diagnostics/media_artifact_fingerprint.py` | **新增**：D-5 的产物指纹工具（可用 `PHOTO_SEARCH_SRC` 指向基线源码树复现「改动前」一侧） |

**未改动**（逐条核对 `git diff --stat 2b8a751 -- <path>` 为空）：`PhotoSearch.spec`、`tools/packaging/**`、
`tools/diagnostics/packaged_api_smoke.py`、ADR-0001～0011、`docs/decisions/contracts/*.json` 全部伴随物、
`0001`～`0015` 迁移、`SUPPORTED_SCHEMA_VERSION`、`taken_time`/`capture_desc` 契约、`search_projection` 8 键、
P3 人物/地点信封、`src/photo_search/web/**`。

## 2. 依赖：版本、来源与配对依据

- 版本：**`pillow-heif==1.8.0`**（`pyproject.toml` 精确固定；`uv.lock` 内 `specifier = "==1.8.0"`）。
- 来源：PyPI wheel `pillow_heif-1.8.0-cp312-cp312-win_amd64.whl`，
  sha256 `921784d39b5e3b9bcbfcf96ff05e78bc5a15ced5d171cf4e3987d06c04da71bf`，6604433 B。
- 配对依据：该分发 `Requires-Dist: pillow>=11.1.0`；本项目 Pillow 约束 `>=10,<12` 且锁定 **11.3.0**，
  已满足，**不需要升级 Pillow**，也没有观测到任何既有格式产物的字节变化（§8 D-5）。
- 运行时：wheel 自带 **libheif 1.23.4**（`pillow_heif.libheif_version()`），无需系统 codec。
- 离线可取性：本机与项目 `.uv-cache` 中原本都没有该 wheel，需一次性联网获取（已在用户授权下完成）。
  本卡未使用自研解析、系统 codec 或 OpenCV 兜底。
- 交付 P4-00C-B 的打包事实（现在只记录，不处理）：扩展与运行库落在 **site-packages 根目录**
  （delvewheel 布局，由 `pillow_heif` 的 `os.add_dll_directory(site-packages)` 加载）：
  `_pillow_heif.cp312-win_amd64.pyd` 59904 B sha256 `58ccecb79a7e5265e3e25154683635ed801a34a347dab81024c0e9b089de10b4`；
  `libheif-30fc1b3ceee9088934615cbf4948eba3.dll` 2201088 B sha256
  `6223de1181ea35f394b380f2142be64406659ebf7026a2cb61d181c16e42ae9b`；
  另有 `libde265-0-*.dll`、`libx265-217-*.dll`、`libgcc_s_seh-1-*.dll`、`libstdc++-6-*.dll`、
  `libwinpthread-1-*.dll`。
- 在 P4-00C-B 完成前，**现有冻结包不含 pillow-heif**：打包进程里 `heif_available()` 为假，HEIC 会走本卡的
  fail-closed 分支得到结构化 `FORMAT_UNSUPPORTED`（不是启动失败、不是 `DECODE_FAILED`、不是空图）——
  这既是本卡「插件不可用」语义的直接推论，也是 P4-00C-B 降级反例的预期基线。

## 3. 方向语义（本卡最重要的一手实测）

`pillow-heif` 的插件在打开图像时把 EXIF 方向**读出后把该标签改写成 1**，并把读到的值放在
`Image.info["original_orientation"]`（`pillow_heif.set_orientation`）。因此 HEIC 不能照抄 JPEG 的
「只读 EXIF 标签再 `exif_transpose`」假设。实测（`test-results/p4-00c-a/orientation-probe.py` 的等价值已
固化进测试）：

| 形态 | 容器 `irot` | 解码后像素 | `original_orientation` | EXIF 274 | 朴素路径的后果 |
|---|---|---|---|---|---|
| A：编码器能看到方向（`exif=` 字节） | 有（`('irot', 270)`） | **已旋转** 200×320 | 6 | 1 | 正确 |
| B：容器无变换、EXIF 有方向 | 无（`()`） | **未旋转** 320×200 | 6 | 1 | **横向错图**（标签已被清掉，`exif_transpose` 变成 no-op） |

裁定与实现：

1. 解码前用 `heif.container_orientation_applied()`（读 pillow-heif 自己的容器句柄）判断 libheif 是否
   已应用 `irot`/`imir`；
2. **只在容器没有该变换时**把 `original_orientation` 写回内存中的 EXIF 标签，然后交给**同一个**
   `ImageOps.exif_transpose` 调用完成归一化 —— 与 JPEG 走完全相同的代码路径；
3. `read_orientation()`（P3-06 人脸裁剪用来算归一化原图尺寸）使用同一规则，因此
   `probe_original_frame()` 与 `load_normalized()` 在 A/B 两种形态下都给出同一个 200×320；
4. 该判别器依赖 pillow-heif 的私有句柄，因此 pillow-heif **精确钉死版本**，并用
   `test_the_pinned_encoder_really_writes_both_orientation_shapes` 显式断言两种形态可观测 —— 版本一旦
   变化，测试会先失败而不是静默出图。读不到该属性时（`None`）保持 pillow-heif 自己的声明，不自行发明旋转。

两种形态 × 方向 6/8 的四个组合都用四象限颜色对照了「独立期望图」，可同时抓住漏旋转、反向旋转与双重旋转。

## 4. 格式策略与失败闭集

- `SUPPORTED_INPUT_FORMATS` 现在是**函数**（`supported_input_formats()`）的取值：基础集合
  `JPEG/PNG/WEBP/BMP/GIF` 加上**当且仅当本进程真的加载了 pillow-heif + libheif** 的 `HEIC/HEIF`。
  `capabilities` 的 `supported_input_formats`（根与 `media.thumbnail`/`media.preview`）读同一函数，
  所以「能识别」与「能解码」不会再被写成同一件事。
- 继续拒绝（闭集不变）：`AVIF/JXL/TIFF/MPO/ICO/PSD/TGA/PPM/PGM/PBM/PDF/EPS/RAW`。
  AVIF 仍是拒绝项：pillow-heif 1.8 的 opener 只接受 HEIF brand
  （`heic/heix/heim/heis/hevc/hevx/hevm/hevs/mif1/msf1`），AVIF 文件仍不可识别 → 嗅探成 `AVIF` →
  结构化 `FORMAT_UNSUPPORTED`（`tests/unit/test_p4_heic_media.py::test_the_other_refused_families_are_still_refused`）。
- 判定只看内容不看扩展名：HEIC 字节命名为 `.jpg` 仍按 HEIF 解码，JPEG 字节命名为 `.heic` 仍按 JPEG 解码。
- 坏文件（截断、只有 ftyp 头、正文不是图像）→ 每文件 **`DECODE_FAILED`**（既有 details 键），
  绝不写成 `FORMAT_UNSUPPORTED`、不写伪产物、不中断批次（§6、§8 D-1）。
- 插件/libheif 不可用 → **fail closed**：`heif_available()` 为假，`HEIC/HEIF` 进入拒绝闭集，
  HEIC 得到结构化 `FORMAT_UNSUPPORTED`（details 的 `supported_formats` 不含 HEIC），
  能力清单同步不再宣传 HEIC；其它格式与既有缓存完全不受影响。该分支在业务层可注入
  （单点 `heif.ensure_heif_support`），单元测试用「把 HEIF 从 Pillow 注册表移除 + 注入不可用」复现。
- 大图限制未为 HEIC 放宽：`Image.MAX_IMAGE_PIXELS` 的像素安全上限对 HEIC 同样生效
  （超过即结构化 `DECODE_FAILED`）。HEIC 不适用 JPEG 的 DCT 降采样捷径，峰值内存只由既有像素上限约束。

## 5. 上传面裁定：`POST /api/v1/search/image` 仍拒绝 HEIC

任务卡 §4 要求显式裁定，不得默认漂移。本卡选择**不动上传面**，理由与证据：

1. P2-07 冻结的是这个**查询输入**端点的字节/像素/解码时间三类上限与白名单；它不写目录、不建资产。
   上传面此前是**借用**媒体层的 `plugin_format_decision`，本卡把它的白名单显式化为
   `UPLOAD_INPUT_FORMATS = {JPEG, PNG, WEBP, BMP, GIF}`，让「库内能力」与「查询输入白名单」成为两个
   被分别测试的事实，而不是偶然一致。**（已被 [P4-02T](P4-02T-upload-format-policy.md) 修订：该白名单现在显式包含 `MPO` 与 HEIF 族，因为库内已支持它们；“显式自有、必须显式变更”的机制不变。）**
2. 让上传面跟随库内能力属于一次**加性协议变更**：按任务卡要求必须同步更新 P2-07 的静态与集成断言，
   并把最终 Sol 聚焦终审改为必选。本卡是两段式 P4-00C 的第一段（其后还有 C1 环境段与独立 DS-H 的
   A+B 全量主审），在此额外插入一次模型切换会话不符合本卡停止点。
3. 不一致已被记录成可测试的事实：HEIC 上传得到 **HTTP 409 `FORMAT_UNSUPPORTED`**，
   `details.format = "HEIF"`，`details.supported_formats` 为**上传白名单**（不含 HEIC），
   而库内 `supported_input_formats` 仍含 HEIC。

真实 HTTP 样本（`tests/integration/test_p4_heic_pipeline.py::test_the_upload_endpoint_refuses_a_heic_with_a_structured_409`，
真实 ASGI 应用 + 会话 + CSRF + 合法绑定三元组）：

```
POST /api/v1/search/image   (multipart image=上传 原图.heic, content-type=image/heic)
→ 409 {"code": "FORMAT_UNSUPPORTED", "message": "format HEIF is not supported by this build yet",
       "details": {"format": "HEIF", "supported_formats": ["BMP","GIF","JPEG","PNG","WEBP"]}}
```

若后续需要手机相册直传 HEIC，应作为**独立的加性协议变更**处理（P2-07 断言 + Sol 聚焦终审）。

## 6. JPEG↔HEIC 对照结论（B 级核心）

统一夹具（同一墙钟时间、同一 `OffsetTime*`、同一组坐标）下：

| 对照 | JPEG | HEIC | 结论 |
|---|---|---|---|
| 有偏移 `EXIF_ORIGINAL` + 亚秒 + `Make/Model` | `EXIF_ORIGINAL` / `HIGH` / civil key / camera | 逐字段相同 | `asset_metadata` 六个派生字段**完全相等**（`tests/integration/test_p4_heic_pipeline.py::test_taken_time_matches_between_a_jpeg_and_a_heic_twin`） |
| 无偏移 | `MEDIUM` | `MEDIUM` | 无偏移不写 UTC，两格式一致 |
| 只有 `EXIF_ROOT`（`0x0132`+`0x9010`） | `EXIF_ROOT`（文件时间） | 相同 | 诚实性一致，未参与时间过滤 |
| GPS（`0x8825`，WGS84 + datum） | `VALID`/`WGS84` | 相同 | `raw_gps_json` 与 `raw_fields_checksum_sha256` **逐字节相同** |
| GPS 方向非法（`LongitudeRef="X"`） | `INVALID` 且无可搜索坐标 | 相同 | `(0,0)`、缺块、坏分母、未知基准的既有决策未改动 |
| 无 GPS 块 | `ABSENT`/`ABSENT` | 相同 | 缺块仍是成功抽取，不是失败 |

子 IFD 可见性已实测：HEIC 上 `get_ifd(0x8825)` 与 `get_ifd(0x8769)` 均可见。上面三组拍摄时间对照
分别覆盖 `0x9003`、`0x9004`、`0x9011`、`0x9012`、`0x9291`、`0x9292`、`0x0132`、`0x9010`
（以及 `0x010F`/`0x0110` 的 `camera` 归一化）：若是某个标签在 HEIC 上不可读，对照行会立刻不等。
缩略图/预览/GPS/拍摄时间都只使用已发布事实，未为 HEIC 新增第二套判定。

## 7. B 级（自动化）证据

```
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
  --basetemp=.pytest-basetemp-p400ca/full2 --junitxml=test-results/p4-00c-a-full.xml
```

- `test-results/p4-00c-a-full.xml`：**1632 tests / 0 failures / 0 errors / 48 skipped**（190.0 s；
  48 个 skip 全为未安装 pinned 权重的环境跳过，与 P4-00B 基线同类）。
- `test-results/p4-00c-a-focused-unit.xml`：**85 / 0 / 0 / 0**（`test_p4_heic_media` 19、
  `test_media_decode`、`test_p2_search_upload`）。
- `test-results/p4-00c-a-d-level.xml`：**31 / 0 / 0 / 0**（本卡新增的两个 HEIC 文件：19 单元 + 12 集成）。
- `uv run --no-sync ruff check .`：All checks passed（受限令牌下会打印一条既有 `os error 5` 遍历警告；
  正常权限路由下无警告）。
- `git diff --check`：clean；`uv lock --check`：**Resolved 96 packages**（新增 1 个包，见 §2）。
- P4-00A/B 契约面未动：`test_p4_time_contracts.py`（39 例）与 `tests/unit/test_p4_taken_time.py` 在全量套件中通过；
  `0001`～`0015`、`p2/p3/p4` 全部伴随物、ADR-0001～0011 相对基线零改动（见 §8 D-6）。

覆盖清单（对应任务卡「必须覆盖」）：格式闭集与能力清单一致、HEIC 解码与 AVIF/JXL/TIFF/RAW 仍拒绝、
损坏/截断 HEIC 隔离、带方向 HEIC 的缩略图/预览（两种方向证据形态 × 方向 6/8）、带 GPS 的 JPEG↔HEIC 对照、
带 `OffsetTime*` 的拍摄时间对照、插件不可用的 fail closed（业务层注入）、既有 JPEG/PNG/WebP/BMP/GIF 全回归、
P4-00A/B 契约测试与 companion/迁移哈希不变。

## 8. D 级（故障注入，逐条）

全部注入都走生产代码路径（真实扫描器、真实媒体批次、真实调度器与抽取器、真实 HTTP 应用）。

| # | 夹具与命令 | 结果证据 |
|---|---|---|
| D-1 坏文件同批 | 同一来源放 1 好 HEIC + 1 好 JPEG + 截断 HEIC + 只有 ftyp 头 HEIC + AVIF；`pytest tests/integration/test_p4_heic_pipeline.py::test_a_corrupt_heic_is_isolated_next_to_healthy_photos` | 扫描 `SUCCEEDED`；批次 `generated=2 / failed=2 / unsupported=1`，`failures` 的 code 集合为 `{DECODE_FAILED, FORMAT_UNSUPPORTED}`；两个好文件各自有产物；三个坏文件各自有失败记录，记录内不含库绝对路径；批次未中断、无伪产物 |
| D-2 写入期被替换/删除 | 用生产 `pipeline.load_normalized` 包装器在解码后改写字节（append）或删除文件；`::test_a_heic_changed_while_its_thumbnail_is_built_is_never_published[replaced/deleted]` | 替换 → `input_unstable=1`、`generated=0`、`artifacts_written=0`、目标产物不存在；删除 → `source_missing=1`、无产物；恢复原始字节与观察值后重跑 → `generated=1` 且产物出现（「留待下次」） |
| D-3 同内容双位置 | 同一份 HEIC 字节复制到同来源两个路径（外加一张 JPEG 孪生图）；`::test_identical_heic_content_at_two_locations_is_deterministic` | 两个位置 → 同一 asset（sha256 相同）、JPEG 是另一个 asset；首次 `generated=2`、`failures=[]`；第二次 `already_published=2` 且产物字节不变；删掉**首个位置**与产物后重跑仍 `generated=1` 且字节与首次**逐字节相同**（按 `location_id` 升序取位确定） |
| D-4 插件/libheif 不可用 | 把 HEIF 从 Pillow 注册表移除并注入不可用；`tests/unit/test_p4_heic_media.py::test_a_build_without_the_heif_plugin_fails_closed` | HEIC → `FORMAT_UNSUPPORTED`（`format="HEIF"`，`supported_formats` 不含 HEIC）；`capabilities` 的 `heif_support.available=false`、`formats=[]`、支持集不含 HEIC；同一进程内 JPEG 仍正常解码（其它格式与既有缓存不受影响） |
| D-5 存量产物逐字节相同 | `tools/diagnostics/media_artifact_fingerprint.py` 对固定夹具（JPEG 方向 1/3/6/8、灰度 PNG、带 alpha PNG、WebP、BMP、GIF；每种各算 thumbnail 与 preview 载荷）输出 18 行 `格式/尺寸/方向/sha256`。**「改动前」一侧由 `git archive 2b8a751 src` 解出的基线源码树产生**（`PHOTO_SEARCH_SRC` 指向该树） | 两侧文件 **sha256 完全相同**：`test-results/p4-00c-a/fingerprint-baseline.txt` 与 `fingerprint-current.txt` 均为 1800 B、18 行、sha256 `b8df55b5afc164919530fd95aaed61586c8088db52b1f5f1d4477a36514e7b9c`；本次会话早期在改动前源码树上直接跑出的副本亦逐行相同。**裁定：既有格式产物逐字节不变，`THUMBNAIL_PROCESSOR_ID`/`PREVIEW_PROCESSOR_ID` 不递增**（新增格式不需要新版本身份，伪产物风险为零） |
| D-6 迁移与契约面零改动 | `git diff --stat 2b8a751 -- src/photo_search/catalog/migrations docs/decisions src/photo_search/taken_time/contract.py`；`uv lock --check` | diff 为空；`0001`～`0015` 逐个 sha256 与基线一致（如 `0015_taken_time.sql` = `4e2cfa09cc4f85f7c94011930d910481edac20584050d10d8679521828137d89`）；`p4-time-contracts.v1.json` = `80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`（与 P4-00A 冻结值一致）；`uv lock --check` = Resolved 96 packages |

## 9. 开发过程中的真实失败与修复

1. **网络/代理**：本机 `HTTP(S)_PROXY` 指向死端口 `127.0.0.1:9`，`uv add` 首轮在 `pypi.org` 上超时失败。
   清空代理变量后正常取到 wheel（仍是一次性授权联网）。
2. **方向双重应用风险（本卡最关键的一次返工）**：初版实现只在「容器无 irot/imir」时恢复方向；随后实测
   发现 pillow-heif 在能看到方向时会写 `irot`，此时 libheif 已经旋转过像素而 `original_orientation` 仍报 6。
   于是加入容器判别器，并用两种夹具形态各跑一遍，确认既不漏旋转也不双重旋转。
3. **锁定版本写法**：`uv add` 首次写入 `>=1.8.0`，与任务卡「固定版本」不符；改为 `==1.8.0` 后重新锁定，
   `uv lock --check` 通过。
4. **夹具签名缺陷**：`write_exif_heic/_jpeg` 起初把 `container_orientation`/`image` 透传给了 `build_exif`，
   触发 `TypeError`；修好签名并复跑。
5. **断言过早**：D-1 起初把一张 HEIC 写成了 `.jpg`（内容去重成同一 asset），D-3 起初期待重建两个产物——
   两处都是测试预期写错，按真实语义修正（不改生产代码）。
6. **环境事实（非产品缺陷）**：受限令牌下 pytest 的 basetemp / `tmp_path` 目录枚举会报
   `PermissionError [WinError 5]`（`.pytest-tmp-*`、`.pytest-basetemp-*` 既有现象）。本卡全部测试改经
   **正常 Windows 权限路由**执行，未修改任何 ACL。

## 10. 未验证项（不得当 PASS）

1. 30 万容量下的 HEIC 解码吞吐、缓存体量与峰值内存；本卡只验证像素上限未被放宽，未做规模测量。
2. 真实设备 HEIC 样本：本机没有可用的真实 iPhone/相机 HEIC（P0-06 语料里那 1 张 HEIC 不在库内），
   全部结论来自合成夹具。**容器同时带 `irot` 且 EXIF 方向非 1** 的非规范文件未实测（按 MIAF 约定
   EXIF 方向应为 1，此时不会双重旋转）。
3. HEIC 变体：10/12-bit、网格（tiled）、多帧/序列（`msf1`/`hevs`）、带 alpha/深度/辅助图的文件未逐一实测。
4. HEIC 上的 XMP 方向标签、EXIF 与 XMP 同时存在的组合未实测（pillow-heif 的 `set_orientation` 会同时处理）。
5. 大 HEIC（如 48MP 手机原图）的真实解码耗时与内存曲线未测量；HEIC 无 JPEG 的 DCT 降采样捷径。
6. 多进程/并发下的 `pillow-heif` 线程与 DLL 加载行为未测量。
7. C 级（PyInstaller onedir、libheif/DLL 收集、打包 HEIC 端到端、降级反例、包内资源审计）**属 P4-00C-B**，
   本卡未执行，也不得据此声称任何打包结论。
8. GTX 1660 实机与 CUDA 侧未涉及（本卡全是 CPU 路径）。

## 11. 真实 Git 状态与下一步

- 起点：`2b8a751`（工作树干净）。本卡改动在本阶段结束时未提交，随后**已随 P4-00C 收口提交 `9b1ff20` 入库**。
  相对基线的改动集为
  **9 modified**（`pyproject.toml`、`uv.lock` + 6 个生产文件 + `tests/unit/test_media_decode.py`）
  **+ 5 untracked**（`src/photo_search/media/heif.py`、`tests/unit/test_p4_heic_media.py`、
  `tests/integration/test_p4_heic_pipeline.py`、`tests/p4_heic_fixtures.py`、
  `tools/diagnostics/media_artifact_fingerprint.py`），另有本报告与状态文档两处文档更新。
- 状态写入 `docs/development-status.md`：**P4-00C-A PASS / P4-00C-B（C1 环境段）PENDING**。
- **停止点**：本卡不生成 P4-01，也不做任何打包工作。请用户在 Codex 中手动切换到
  **C1 / GPT-5.6 Terra / medium** 的新会话执行 [P4-00C-B](P4-00C-B.md)；两段都完成后另开**独立 DS-H 会话**
  做 A+B 全量工程主审，通过后才标 **P4-00C PASS**。
- 未命中 V3.1 §2.2 六类条件，本卡不升 DeepSeek max。

## 12. 修正轮（P4-00C-B 降级审核发现，2026-09-23）

P4-00C-B（环境段）在**去掉冻结包内 libheif DLL** 的降级反例里发现：HEIC 得到的是 `DECODE_FAILED`
（失败记录：`cannot decode the image (ImportError): DLL load failed while importing _pillow_heif`），
而不是本卡 §4 冻结的 `FORMAT_UNSUPPORTED`。按 P4-00C-B 卡的规定该缺陷交回 A 层修复，已在本轮关闭。

- **根因**：`pillow_heif.register_heif_opener()` 先 `Image.register_open("HEIF", …)`，之后才调用
  `_pillow_heif.get_lib_info()`。运行库缺失时后者抛 `ImportError`，本卡原实现虽然把
  `heif_available()` 正确置为 False（能力清单也确实不再宣传 HEIC），**但 Pillow 里已经留下一个注册好的
  HEIF 打开器**；`Image.open()` 因此进入该插件并在其中抛 ImportError → 媒体层把它当成坏文件
  → `DECODE_FAILED`。本卡 §4 的 fail-closed 结论当时只覆盖了「插件整体不可导入」这一子情形。
- **修复**：`src/photo_search/media/heif.py` 的注册改为**原子**：调用前快照 Pillow 的
  `OPEN/ID/SAVE/SAVE_ALL/MIME/EXTENSION`，在同一个 try 内强制读一次 `libheif_version()`，任一步失败即
  **原地回滚**快照再返回不可用。回滚后 Pillow 回到「不认识 HEIF」的干净状态，
  `Image.open()` 抛 `UnidentifiedImageError` → 嗅探 `HEIF` → 拒绝闭集 → **结构化 `FORMAT_UNSUPPORTED`**。
- **对照证据**（同一失败注入，两种模式）：回滚关闭时
  `available=False HEIF_in_Pillow=True code=DECODE_FAILED format=None`（与审核给出的失败记录一致）；
  回滚开启时 `available=False HEIF_in_Pillow=False code=FORMAT_UNSUPPORTED format=HEIF`。
- **回归**：`tests/unit/test_p4_heic_media.py::test_a_registration_that_fails_after_installing_the_opener_is_rolled_back`
  把 `pillow_heif.heif/_pillow_heif` 与 `as_plugin._pillow_heif` 替换为 `DeferredError` 并复现真实注册顺序；
  临时禁用回滚时该用例确实失败，说明它是一例有效的回归门。
- **未变**：格式闭集、解码/方向策略、错误码集合、GPS/拍摄时间判定、upload 白名单与 envelope 均未改动；
  正常包（扩展与运行库齐备）的行为与本报告 §7 的证据不变。
- 完整环境段证据见 [P4-00C-B 报告](P4-00C-B-heic-packaging.md)。
