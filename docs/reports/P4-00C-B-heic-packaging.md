# P4-00C-B 报告 — HEIC 环境与正式包兼容（C1 环境段 + 修复轮）

本卡只处理 Windows / PyInstaller / DLL / 真实打包环境：证明 P4-00C-A 的 HEIC 能力在**冻结包**里真实可用、
可降级、可审计。业务语义（格式闭集、解码策略、错误码集合、GPS/拍摄时间判定、upload 白名单）属 A 段。

**本卡执行过程中确实发现了一个业务缺陷**：降级包里 HEIC 得到的是 `DECODE_FAILED` 而不是任务卡要求的
`FORMAT_UNSUPPORTED`。按本卡「发现业务缺陷只交回最小反例」的规定，C1 段留下了复现证据（
`test-results/p4-00c-b-degraded-*.json`，其中失败记录写着
`cannot decode the image (ImportError): DLL load failed while importing _pillow_heif`），
**由 A 层修复轮关闭**（见 §3），随后本卡全部 C 级证据重跑至绿。

观察起点：A 段全部未提交交付物 + C1 段的环境改动；本卡结束时的真实改动集见 §8。本卡结束时未提交，
随后**已随 P4-00C 收口提交 `9b1ff20` 入库**。

## 1. 改动文件与最小差异

C1 段（环境面）：

| 文件 | 最小差异 | 根因 |
|---|---|---|
| `tools/packaging/build_onedir.py` | 只加一行 `--collect-all pillow_heif` | PyInstaller 看不到「先探测能力再动态 import」的 `pillow_heif`；该 wheel 同时带顶层扩展（`.pyd`）与**同级** libheif 运行库（delvewheel 布局，由 `os.add_dll_directory(site-packages)` 加载），只收数据或只收 binary 都会漏掉另一半 |
| `tools/diagnostics/packaged_api_smoke.py` | 新增 HEIC 组件清单/哈希断言、打包 HEIC 缩略图/预览/坏文件断言、P4-00B 主审建议 **R1**（包内 `photo_search` **全部**数据资源与源码逐字节一致）、`app_stderr_tail`；本修复轮再补降级模式、JPEG 孪生对照、§2.3 的 taken_time/GPS 语义断言与「空目录不 IndexError」诊断 | 原有 smoke 只审 migration 脚本；P4-00B 曾因此漏掉陈旧 `web/app.js` |
| `ruff.toml`、`.gitignore` | 把 `.packaged-*` 降级副本排除在 lint/版本控制之外 | 这些副本内含完整 `_internal` Python 树，误 lint 会重写被审字节 |

修复轮（业务面，属 A 层；本卡只交回反例）：

| 文件 | 最小差异 |
|---|---|
| `src/photo_search/media/heif.py` | 注册改为**原子**：注册前快照 Pillow 的 `OPEN/ID/SAVE/SAVE_ALL/MIME/EXTENSION`，`register_heif_opener()` 之后强制读一次运行库版本；任一步失败即**原地回滚**并把 `heif_available()` 记为 False（详见 §3） |
| `tests/unit/test_p4_heic_media.py` | 新增 1 例忠实复现降级形态的回归（C 模块替换为 `DeferredError`），断言回滚后 `HEIF` 不在 Pillow 注册表、HEIC 得到 `FORMAT_UNSUPPORTED` |
| `tools/diagnostics/heif_degraded_bundle.py` | **新增**：可复现地准备「独立可丢弃副本」（`--remove libheif\|extension\|both`），打印前后组件清单与校验 |

`PhotoSearch.spec` **未改动**：本项目的构建入口是 `tools/packaging/build_onedir.py`（CLI 方式），
spec 由 PyInstaller 生成且被 `.gitignore` 忽略，改动点只有上面那一行收集参数。

## 2. 包内组件清单与一致性（C 级 §1）

真实重建：`uv run --no-sync python tools/packaging/build_onedir.py`（输出 `dist/PhotoSearch`，
21 个 NVIDIA 运行库 DLL 进 `nvidia_dlls/`）。

| 包内相对路径 | 字节数 | SHA-256 | 与 `.venv` 来源 |
|---|---|---|---|
| `_internal/_pillow_heif.cp312-win_amd64.pyd` | 59904 | `58ccecb79a7e5265e3e25154683635ed801a34a347dab81024c0e9b089de10b4` | **同源哈希一致** |
| `_internal/libheif-30fc1b3ceee9088934615cbf4948eba3.dll` | 2201088 | `6223de1181ea35f394b380f2142be64406659ebf7026a2cb61d181c16e42ae9b` | **同源哈希一致** |

`packaged_api_smoke` 的 `bundled_heif_components` 直接比较「包内清单 == `.venv` 来源清单（名称/字节/哈希）」，
结论 `matches_source = true`，`present_kinds = ["extension","libheif"]`。

反向清单（R1）：包内 `photo_search` 的**全部**非 Python 数据资源与源码逐字节一致 ——
`bundled_photo_search_data_resources.matches_source = true`，**27 个路径**
（15 个 `catalog/migrations/0001`～`0015` + 12 个 `web/*`：`app.js`、`index.html`、`styles.css`、
`places.{html,css,js}`、`search.{html,css,js}`、`persons.{html,css,js}`），本卡未发现任何陈旧资源。
另 `bundled_migrations.matches_source = true`（15 个脚本名称与哈希齐全）。

## 3. 降级 fail closed：审核失败 → 根因 → 修复 → 复现

**审核给出的最小反例**（C1 段，`.packaged-heif-degraded-p400cb` 去掉 `libheif-*.dll` 后）：

```
packaged_heic_thumbnail_status = 409, packaged_heic_thumbnail_code = "DECODE_FAILED"
cache/media/.../<sha>.json → {"code": "DECODE_FAILED", "format": null,
  "message": "cannot decode the image (ImportError): DLL load failed while importing _pillow_heif: 找不到指定的模块。"}
problems = ["packaged pillow-heif components", "packaged HEIC decode isolation"]
```

**根因（读 pillow-heif 1.8.0 源码 + 实测确认）**：`pillow_heif.register_heif_opener()` 先
`Image.register_open("HEIF", …)`，**之后**才调用 `_pillow_heif.get_lib_info()` 询问编解码能力。
libheif DLL 缺失时后者抛 `ImportError("DLL load failed …")`，被 `heif.ensure_heif_support()` 捕获后
`heif_available()` 正确变为 False（能力清单也不再宣传 HEIC），**但 Pillow 里已经留下一个注册好的
HEIF 打开器**。于是 `Image.open()` 会进入该插件并在其中抛 ImportError，媒体层把它当成「这个文件坏了」
→ `DECODE_FAILED`。这违反了 A 段冻结的降级契约（必须是结构化 `FORMAT_UNSUPPORTED`）。

**修复**（`media/heif.py`，原子注册）：注册前后快照/回滚 Pillow 的六个注册表，并在 try 内强制读一次
`libheif_version()`；失败即回滚，使 HEIC 回到「本构建不认识这个格式」的干净状态：

```
rollback=off available=False HEIF_in_Pillow=True   code=DECODE_FAILED   format=None
rollback=on  available=False HEIF_in_Pillow=False  code=FORMAT_UNSUPPORTED format=HEIF
```

（上表由 `test-results/p4-00c-b/heif-degraded-repro.py` 的等价脚本产出，两种模式各跑一次。
「rollback=off」一行与 C1 的失败记录逐字段一致；「rollback=on」是修复后的行为。）

回归测试：`tests/unit/test_p4_heic_media.py::test_a_registration_that_fails_after_installing_the_opener_is_rolled_back`
把 `pillow_heif.heif/_pillow_heif` 与 `as_plugin._pillow_heif` 替换为 `DeferredError`，按真实顺序
「先注册打开器、再读运行库」，断言回滚后 `HEIF` 不在 `Image.OPEN`/`Image.ID` 且 HEIC 得到
`FORMAT_UNSUPPORTED`；**临时禁用回滚时该用例确实失败**（`AssertionError: assert 'HEIF' not in {…}`），
因此它是一例真实回归门。

降级副本的准备是脚本化的（不再手工删文件）：

```powershell
uv run --no-sync python tools/diagnostics/heif_degraded_bundle.py `
  --source dist/PhotoSearch --dest .packaged-heif-degraded-libheif --remove libheif
uv run --no-sync python tools/diagnostics/packaged_api_smoke.py `
  --bundle .packaged-heif-degraded-libheif --heif-degraded libheif `
  --work-dir .packaged-heif-degraded-smoke-libheif `
  --output test-results/p4-00c-b-degraded-libheif-smoke.json
```

三种变体（`libheif` / `extension` / `both`）各跑一次，结论一致：

| 变体 | 包内剩余组件 | 程序启动 | HEIC 缩略图/预览/坏文件 | 能力清单 | JPEG 与缓存 |
|---|---|---|---|---|---|
| 去 `libheif` | `extension` | 正常（`health=200`） | **409 `FORMAT_UNSUPPORTED`** ×3 | 不含 HEIC | 缩略图 200 `image/jpeg`，既有缓存字节不变 |
| 去 `extension` | `libheif` | 正常 | **409 `FORMAT_UNSUPPORTED`** ×3 | 不含 HEIC | 同上 |
| 两者都去 | 空 | 正常 | **409 `FORMAT_UNSUPPORTED`** ×3 | 不含 HEIC | 同上 |

（三份 JSON 均为 `status=ok` / `problems=[]`；每份都同时给出 `present_kinds == expected_kinds`、
`scan_state=SUCCEEDED`、`asset_count=5`、`offline_thumbnail_bytes_unchanged=true`，
即「其它格式与既有缓存不受影响」。反例副本按卡要求只用于丢弃式验证。）

## 4. 打包后的 HEIC 端到端闭环（C 级 §2）

`test-results/p4-00c-b-packaged-smoke.json`：`status=ok`、`problems=[]`、`exit=0`。夹具在中文/空格目录，
一个来源内 5 个文件：`照片 一.jpg`（离线主体）、`beach sunset.jpg`、`竖拍 HEIC.heic`、
`竖拍 JPEG.jpg`（**同像素、同 EXIF 方向 6 的对照**）、`损坏 HEIC.heic`（截断）。

| 步骤 | 证据 |
|---|---|
| 1 注册来源 + 扫描入库 | `create_source=201`、`scan_request=202`、`scan_state=SUCCEEDED`、`asset_count=5` |
| 2 缩略图/预览 READY 且与 JPEG 一致 | HEIC `thumbnail=200 image/jpeg`、`preview=200`，尺寸 **[320,240]**；JPEG 孪生 **[320,240]**（同一 480×640 竖拍像素 + EXIF 方向 6 ⇒ 两者都必须先归一化成 640×480 再缩到 320×240） |
| 3 taken_time 与 GPS 事实按 A 段语义发布 | 同一墙钟 `2022:07:05 18:31:07` 的对照对：HEIC `EXIF_ORIGINAL/HIGH/offset=480min`、JPEG 孪生 `EXIF_ORIGINAL/MEDIUM/offset=null`，两者 `taken_at = 2022-07-05T18:31:07.000`、`taken_at_sort = 63792642667000` **完全相同**，`label=capture`；两条 `asset_geo_facts` 均为 `VALID/WGS84` 且经纬度一致（读取打包 data root） |
| 4 坏文件同批 | `损坏 HEIC` 缩略图 `409 DECODE_FAILED`，同批好文件全部 READY，进程未崩（`health=200`，`app_stderr_tail` 无 traceback） |

关于步骤 3 的边界（已在报告中标明，避免被读成更强的声明）：冻结产品目前**没有面向用户的
taken-time 抽取入口**（`python -m photo_search.taken_time.cli` 是开发入口，onedir 可执行文件无法运行
`-m`），因此本步骤验证的是**打包进程的服务/读回语义**：诊断按 ADR-0011 §2/§4.1 与 ADR-0005 §4.5 的
精确形状（civil key 与 geo-fact id 由冻结契约本身计算）写入事实，打包构建必须按同一语义发布它们。
生产者侧语义（同一墙钟/偏移下 JPEG↔HEIC 逐字段一致）已由 A 段真实调度器 + 真实抽取器证据覆盖。

## 5. B 级（业务套件，不得回归）

```
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
  --basetemp=.pytest-basetemp-p400cb/full-final --junitxml=test-results/p4-00c-b-full.xml
```

- `test-results/p4-00c-b-full.xml`：**1633 tests / 0 failures / 0 errors / 48 skipped**（206.2 s；48 skip 全为
  未安装 pinned 权重的既有环境跳过）。
- 修复既有测试的**业务期望未被削弱**：`tests/unit/test_media_decode.py` 只更新了 A 段已改的格式闭集断言，
  本卡新增的 C 级断言全部在 `tools/diagnostics/**` 内。
- `uv run --no-sync ruff check .`：All checks passed；`git diff --check`：clean；
  `uv lock --check`：Resolved 96 packages。pytest 经正常 Windows 权限路由执行，未改 ACL。

## 6. 未验证项（不得当 PASS）

1. 真实设备 HEIC 样本（本机无）、10/12-bit/网格/多帧/带 alpha 的 HEIC 变体、HEIC 的 XMP 方向 —— 同 A 段。
2. 大 HEIC 在打包进程里的峰值内存/耗时未测量；只证明既有像素上限仍生效（A 段）。
3. 只测了「去掉 libheif DLL / 去掉扩展」两种降级形态；`pillow-heif` 版本错配、系统 codec 冲突未测。
4. 打包进程的 taken-time **生产**路径未在包内运行（见 §4 边界说明）；A 段为开发进程证据。
5. 未在真实 Windows 用户环境做安装/升级/卸载回归，只做项目内 onedir 与中文/空格路径。
6. 未测 GTX 1660 实机与 CUDA 路径（本卡全是 CPU/打包面）。

## 7. 与任务卡/Sol 的关系

- 本卡未改 upload 白名单，未改任何已冻结信封或错误码集合（修复只是把降级情形的答案从
  `DECODE_FAILED` 纠正为契约早已要求的 `FORMAT_UNSUPPORTED`），因此按任务卡记 **`SOL_NOT_REQUIRED`**。
- 该修复触及 A 段业务代码，A 段报告已追加同一条修正记录（§12）。

## 8. 真实 Git 状态与停止点

- 相对起点（`2b8a751` + A 段与 C1 段全部未提交交付物）本卡新增/修改：
  `tools/packaging/build_onedir.py`、`tools/diagnostics/packaged_api_smoke.py`、
  `tools/diagnostics/heif_degraded_bundle.py`（新）、`ruff.toml`、`.gitignore`、`src/photo_search/media/heif.py`、
  `tests/unit/test_p4_heic_media.py`、本报告与 `docs/development-status.md`。当时不加 commit，
  这些改动随后随 P4-00C 收口提交 `9b1ff20` 一并入库。
- 一次性 `.packaged-*` 降级副本与 smoke 工作目录只是丢弃式验证材料，已被 `.gitignore` 忽略。
- **停止点**：状态写为 `P4-00C-A PASS / P4-00C-B PASS / INDEPENDENT DS-H REVIEW PENDING`。请用户在 Codex 中
  **手动切换**并**另开独立 DS-H / DeepSeek V4.1 Flash / 高推理**会话做 A+B 全量工程主审（A 的业务 diff +
  B 的环境 diff、包内资源与哈希、D/C 证据真实性、ADR/任务卡一致性、V3.1 §2.2 六类条件）；该主审通过后才标
  **P4-00C PASS** 并生成下一卡（预期 P4-01）。
