# P4-02T 实现报告 — 上传查询格式口径收口（MPO 首帧 + HEIF 族方向归一）

**状态：IMPLEMENTED / INDEPENDENT REVIEW PENDING**（本报告是执行者自检，不是独立审查，也不代表 PASS）。
来源：[P4-02N/O/Q/S 独立 DS-H 审查](P4-02N-O-Q-S-dsh-affected-surface-review.md) 的 **F2**；用户裁决「先 1 后 2」。
基线 `549075f` + 未提交在制品；权重 `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`（已登记）。

## 1. 交付（改动面）

| 文件 | 改动 |
|---|---|
| `src/photo_search/search/upload.py` | `UPLOAD_INPUT_FORMATS` 显式扩为 `{JPEG,PNG,WEBP,BMP,GIF,MPO,HEIC,HEIF}`；`_decode_upload` 先调 `heif.ensure_heif_support()`（仓库唯一注册点）再 `Image.open`；在 `exif_transpose` 之前调用 `restore_recorded_orientation`；`upload_refused_families()` 改为正好等于 `refused_input_formats()`（不再把 HEIF 族当"已识别但拒绝"） |
| `src/photo_search/media/decode.py` | 内部 `_restore_recorded_orientation` 提升为公开 `restore_recorded_orientation`（仅改名 + 更新唯一调用点，语义不变），上传路径复用同一函数 |
| `tests/unit/test_p4_02t_upload_formats.py`（新增） | 9 个用例：白名单字面断言与拒绝集关系、MPO 首帧、HEIC 方向 6/8 × 容器 irot 两种形状、EXIF-only 控制例、坏 HEIC → `DECODE_FAILED`、TIFF 仍拒 |
| `tests/unit/test_p4_heic_media.py` | 原 `test_the_upload_boundary_keeps_its_own_frozen_whitelist` 改为 `test_the_upload_boundary_now_accepts_the_heif_family`（P2-07 断言随本卡显式修订） |
| `tools/diagnostics/p4_02t_upload_formats.py`（新增） | 合成库 + 真实模型 + 产品 CLI build + 真实上传三例（§4） |
| `docs/tasks/P4-02T.md` | 本卡 |

**未改**：schema/迁移/依赖/`uv.lock`；API 信封/游标/错误码/`total`/RRF/BM25/距离全序/权威补查/发布资格/outbox
写入与提交协议/调度与锁策略；媒体契约的受支持集（RAW/AVIF/JXL/PSD/ICO 仍拒绝上传）；§12.2；上传仍不产资产、不写索引。

## 2. 为什么改的是白名单，而不是只改文档

`UPLOAD_INPUT_FORMATS` 是 P2-07 的冻结面，它的本意是"上传从不进 catalog，所以边界要显式"。但 P4-02S/P4-00C-A
之后，`MPO` 与 `HEIC/HEIF` 已经是**库内**受支持格式：网格能显示、语义索引能排到，只有"拿同一张照片当查询"会被 409。
真实库自检索里被拒的 3 个样本后缀全是 `.jpg`（MPO），而本库 MPO 约 343 张（占 `.jpg` 约 11%）——这是首屏级功能缺口，
不是文档措辞问题。所以本卡把**集合**显式扩到"库里已验证可解码的全部格式（RAW 除外）"，同时保留"必须显式变更"的机制。

## 3. 方向归一：为什么不能只加白名单

上传路径原来只做 `ImageOps.exif_transpose(source)`，而媒体路径在此之前还有
`restore_recorded_orientation(source)`：pillow-heif 打开 HEIF 容器时会把方向标签清掉，容器带 `irot` 时 libheif
已经转好、只有 **EXIF-only** 那种形状需要把标签补回来。如果只加白名单，旋转过的 HEIC 上传会被**静默**按错误方向
编码（不报错、只是永远排不到自己）。本卡让上传路径复用媒体侧同一个函数，顺序与 `load_normalized` 一致。

另外补了一个真实缺陷：`upload.py` 此前从不调用 `heif.ensure_heif_support()`——那份 HEIC 上传测试只是"碰巧"通过，
因为它同文件里更早的用例已经注册过插件。现在解码前显式注册（失败即 `False`，仍按不可解码处理）。

## 4. 真实模型端到端证据（合成库，不读真实图库）

```powershell
uv run --no-sync python tools/diagnostics/p4_02t_upload_formats.py `
  --model-dir D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16 --device cuda `
  --keep --output test-results/p4-02t-upload-formats.json
```

夹具：两帧 MPO（首帧红、次帧蓝）+ 首帧 JPEG 孪生 + EXIF orientation=6 且无容器变换的 HEIC + TIFF，全部经生产扫描器入库，
再用**产品命令** `python -m photo_search.indexing.cli build` 建库（真实模型）。

| 观测 | 值 |
|---|---|
| 建库 | exit 0；3 个可解码目标全部 `CONFIRMED`（TIFF 进 `out_of_scope`，不是失败） |
| 上传 MPO | **200**、`vector engaged`、top1 是 MPO 本体或首帧孪生、两者占据 top2 |
| 上传旋转 HEIC | **200**、top1 = 自身资产 |
| 上传 TIFF | **409 `FORMAT_UNSUPPORTED`**，`details.supported_formats` = 新白名单（8 项） |
| 9/9 检查 | `status: pass`（本卡范围） |

单测侧还带一个**变异对照**：把 `restore_recorded_orientation` 那行临时去掉后，
`test_a_rotated_heic_upload_is_decoded_upright[False-6]` 与 `[False-8]` 确实失败（容器 irot 的两例仍通过，因为 libheif
已经转好）——证明这条测试能抓住"漏掉方向归一"。文件随后按 SHA256 校验复原。

## 5. 测试与静态验证

| 命令 | 结果 |
|---|---|
| `pytest -q tests/unit/test_p4_02t_upload_formats.py tests/unit/test_p4_heic_media.py` | **29 passed / 0 failed**（新 9 + HEIC 20，`test-results/p402-t-unit.xml`） |
| 六文件收集计数 | media_decode 21、N 13、O/Q 9、S 4、T 9、HEIC 20（无既有用例丢失） |
| **B 级**：`pytest -q tests/unit tests/integration` | **1783 / 0 failed / 0 errors / 48 skipped**（`test-results/p402-t-full.xml`）＝ 修复轮 1774 + 新增 9 |
| `ruff check .` / `uv lock --check` / `git diff --check` | exit 0 / exit 0（96 packages）/ exit 0 |

## 6. 未验证 / 边界

- **独立 DS-H 完整受影响面主审未做** ⇒ 本卡不得记 PASS（改的是冻结 P2-07 断言，审查是必须项）。
- 没有用**真实手机 MPO/HEIC** 验证（没有新的图库授权）；证据是合成夹具 + 真实模型。若要真实样本，授权后可用
  现有库里的 MPO 资产做一次上传自检索（一条命令）。
- 未重建打包产物、未跑 `packaged_api_smoke.py`：C 级不在本卡 ⇒ GTX 1660/§12.2/打包仍未验证。
- 前端文案不需要改（`search.js` 的 `FORMAT_UNSUPPORTED` 文案不枚举格式）；`/capabilities` 的
  `supported_input_formats` 本来就来自媒体契约，未受影响。
- 已知遗留：`indexing.cli reexecute` 仍走 adapter 的 path 直读（HEIF 注册与方向归一未接），属另一张卡。

## 7. 审查路由

产品改动 ⇒ **独立 DS-H 完整受影响面主审（必须）**：至少独立复跑 §4 驱动、9 个新单测与 B 级全量，
并核对 `git diff` 只含 `search/upload.py`、`media/decode.py`、两个测试文件与一个诊断工具。
本卡判断 **SOL_NOT_REQUIRED**（未改信封/错误码/高风险状态；P2-07 的修订是"接受更多格式"这一条附加变更，
由主审复核）；若主审认为放行 HEIF 族构成 §11 风险，再升级 Sol 聚焦终审。

## 8. 下一任务

**不预生成**。本卡闭合后建议按用户既有排序继续：P4-02M §3 性能单点 → 30 万扩容；质量集仍需人工标注。