# P4-02Y-T — 当前 HEAD 打包 + 最小 smoke + 开始个人试用

**结论：PERSONAL TRIAL STARTED（2026-09-29，HEAD `b17ded6`）。** 本卡只做「当前 HEAD 重新打包 →
最小 packaged smoke → 真实个人试用 smoke」；不是 RC1 正式验收，不含性能专项，不启动 P4-02Z。
任务卡：[P4-02Y-T](../tasks/P4-02Y-T.md)。

## 1. 基线与环境

| 项 | 值 |
|---|---|
| HEAD / 分支 | `b17ded6`（`[P4-02Y 收口]`）/ `master`；打包前 `git status` 干净，本轮改动见 §8 |
| Python / uv | 3.12.10 / uv 0.11.0（`.uv-cache`） |
| schema | 16（`0016_jobs_claim_order.sql`；`0017` 未实施）；包内迁移 `0001–0016` 与源码逐字节一致 |
| 数据根 | `D:\SZ\project\PhotoSearchData`（真实库：4 个来源、试用前 3658 资产 / 3682 位置；试用后 3666 / 3690） |
| 模型目录 | `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`（产品默认按数据根解析，无需 `--model-dir`） |
| GPU / provider | RTX 4060 Laptop 8 GiB（驱动 616.92）；`onnxruntime-gpu@1.30.0`，包内 `nvidia_dlls/` 21 个 DLL |
| 授权来源 | `D:\SZ\朋友`、`D:\SZ\pic`、`D:\SZ\吉他`、`D:\SZ\vsco`（原图只读） |

## 2. 构建（onedir）

```
uv run --no-sync python tools/packaging/build_onedir.py
```

exit 0、`bundling 21 NVIDIA runtime DLLs into nvidia_dlls/`；产物 `dist/PhotoSearch/PhotoSearch.exe`
**36 308 235 B / sha256 `2da907e25b477a2c16a832beaf02d89ef6458f9c5922e14f102384035d1012d5`**，
包内含 16 个迁移脚本与 `web/` 静态前端资源。未新做 installer/MSI，未改安装体系。

## 3. 最小 packaged smoke（复用 `packaged_api_smoke.py`）

```
uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist\PhotoSearch \
  --work-dir "D:\SZ\project\aisearch\tmp\p4-02y-t\packaged-smoke-e" \
  --output test-results\p4-02y-t-packaged-smoke.json
```

`status=ok`、`problems=[]`（证据 `test-results/p4-02y-t-packaged-smoke.json`）：

- 启动 1.2 s；中文/空格安装路径与中文/空格来源根；单次会话凭据、HttpOnly+CSRF cookie、退出后重启凭据轮换全部通过；
- schema 16 + `asset_metadata` 闭集来源 + 捕获索引 + `taken_time_rules_version=1`；
- 包内迁移、`photo_search` 全部非 Python 数据资源（迁移 + web 静态文件）、pillow-heif 扩展与 libheif DLL 与源码逐字节一致；HEIC 缩略图/预览 200、损坏 HEIC 409 `DECODE_FAILED`；
- 无权重夹具下的诚实拒绝：文字搜索 409 `RUNTIME_CAPABILITY_UNAVAILABLE/QUERY_ENCODER_UNAVAILABLE`，上传 409 `IMAGE_ENCODER_UNAVAILABLE`；
- **本卡为该 smoke 补的最小检查**（`tools/diagnostics/packaged_api_smoke.py`）：`POST /api/v1/library/refresh`
  202 + `accepted=true` + `refresh_id`；`GET /api/v1/library/refresh` 同 id 走到终态且 `stages/counters` 形状完整。
  无模型夹具下 `search_ready=false`、semantic `SKIPPED_UNAVAILABLE/MANIFEST_UNCONFIGURED`、终态 `FAILED`
  （`enumerated=5`、`hash_targets=0`）——**不宣称 SEARCH_READY**；真实 SEARCH_READY 由 §4 证明。
- 环境说明：沙箱内冻结进程无法在 `%TEMP%` 建上传临时文件（`PermissionError`，普通 Python 同样复现）⇒
  按仓库规则走**正常权限路由**重跑才通过，属环境限制不是产品缺陷。

## 4. 真实个人试用 smoke（冻结 exe + 真实数据根 + 真实模型）

启动命令（试用期间实际使用的形式）：

```
dist\PhotoSearch\PhotoSearch.exe --data-root D:\SZ\project\PhotoSearchData
```

证据：`test-results/p4-02y-t-real-trial.json`、`test-results/p4-02y-t-query-evidence.json`。

| 步骤 | 观测 | 结果 |
|---|---|---|
| A 启动 / Web / 来源 | 启动 **1515 ms**；`GET /` 200（含「刷新图库」按钮、浏览视图与搜索入口）、`/web/app.js` 200；会话 + CSRF 换取正常；`GET /api/v1/sources` 4 个来源全 `ONLINE`（3658 资产 / 3682 位置，`locations_missing=0`） | PASS |
| A 真实模型在包内 | stdout `semantic channel READY (model_dir=…\vit-b-16, manifest=…\_internal\tools\prepare_models\manifests\…)`；`query_encoder_available=true`、`active_profile_id=semantic-image-gen-5bcba729…`、dimension 512、`onnxruntime-gpu@1.30.0`；首次 `capabilities` **2096.8 ms**（真实 worker 冷启动） | PASS |
| B 刷新图库 | `POST /api/v1/library/refresh` **202**（2.1 ms）；`SCANNING→INDEXING→SEARCH_READY→COMPLETE`；`search_ready_ms=825`、`complete_ms=831`；`enumerated=3682`、`unchanged_reused=3682`、**`hash_targets=0` / `hash_bytes=0`**、`new_assets=0`、`content_delta=0`；semantic `SKIPPED_NO_TARGETS`（basis `semantic-up-to-date`） | PASS |
| C 真实中文搜索 | 8 条（日落/猫/海边/全家福/吉他/城市夜景/花朵/食物）：**8/8 200**、`state=OK`、每条 20 命中、`vector engaged`（`engine_blocks=1`、候选 200）、每个 top1 缩略图 200 `image/jpeg`；冷启动首查 1635 ms，暖机 59–111 ms（重启后首查 1141/1018 ms） | PASS |
| D 小增量 | `D:\SZ\pic\P4-02Y-T 试用新增\` 新增 8 张**重编码**副本（新 sha256；字节级副本会被内容去重合并，故用重编码产生真实「新内容」）；刷新：`enumerated=3690`、`unchanged_reused=3682`、`hash_targets=8`、`new_assets=8`、`locations_added=8`、`content_delta=8`、semantic `encoded=8/confirmed=8`、projection `written=8`、thumbnail `generated=8`、taken-time/GPS 各 8 作业；`search_ready_ms=2615`、`complete_ms=2923`；`GET /assets?text=试用新增` 返回 8 个新 id（3659–3666），`POST /search`「试用新增」top1–2 即新资产（`TEXT+SEMANTIC`，缩略图 200） | PASS |
| E 重启 | `Ctrl+Break` 请求退出：日志 `Shutting down → Waiting for application shutdown → Application shutdown complete → Finished server process`（lifespan `api.close()` 正常执行，stderr 无异常栈）；第二次启动 **1016 ms**，`query_encoder_available=true`、generation/profile 与重启前一致、`GET /library/refresh` 回到 `IDLE`、3 条中文查询 3/3 200 且有命中与缩略图、`试用新增` 的 8 个 id 重启后仍可检索 | PASS |
| 原图安全 | 4 个来源 3682 个文件（排除本次新增目录）的 `path|size|mtime_ns` 摘要前后**逐字节相同**（`e3c8ca4c…f1222`）；无原图写入/删除，无数据损坏迹象 | PASS |

## 5. 发现的问题（均非阻断，本卡不扩展修复）

- **F1** 沙箱内冻结进程无法在 `%TEMP%` 创建上传临时文件（`PermissionError`；普通 Python 复现）⇒ 环境限制，
  正常权限路由下 packaged smoke 全绿。
- **F2** `Ctrl+Break` 退出时进程退出码为 **3**（非零），但 uvicorn lifespan shutdown 与 `ApiContext.close()`
  日志正常、无异常栈；本卡只记录该观察，不追查来源（不属 blocker）。
- **F3** 无权重夹具下「刷新图库」终态是 `FAILED` + `MANIFEST_UNCONFIGURED`——「不宣称 SEARCH_READY」是正确行为，
  但用户可见文案只显示「刷新失败」，未区分「本机缺少模型」；记为后续体验改进候选。
- **F4** 本次增量仅 8 张，未覆盖 >50 000 delta 溢出、face/OCR 真实权重、place 数据集（沿用 P4-02Y 的未验证边界）。

## 6. 结论与停止点

**PERSONAL TRIAL STARTED**：当前 HEAD 的 onedir 包可启动；Web 入口与真实模型在包内可用；刷新可到
`SEARCH_READY`（no-op 825 ms / 增量 2615 ms）；8 条真实中文查询全部有结果且有可显示缩略图；新增 8 张后即可搜到；
退出重启后库/索引/搜索仍可用；原图零改动。按卡面 §6 直接进入真实日常使用。

**停止点**：本卡到此停止——不启动 P4-02Z，不重跑 P4-02W A/B/C/D，不做 30 万/40 万、GTX 1660、ANN/A3、
installer/MSI。真实使用中遇到问题一事一卡。

## 7. 未验证 / 边界

- 未做真实浏览器渲染与首屏计时（沿用 `BROWSER_TIMING BLOCKED` 口径）；Web 可用性由真实 HTTP 会话
  （shell + 静态资源 + 全部接口）证明，不含浏览器绘制证据。
- face/OCR 真实权重、place resolver 数据集未安装 ⇒ 对应阶段 `SKIPPED_UNAVAILABLE`，不阻塞搜索与可搜索性。
- 379 个历史 `FORMAT_UNSUPPORTED` 资产（MPO/RAW 预览）与增量内容的语义相关性不在本卡结论内（无人工标注保留集）。
- 新增目录处置：`D:\SZ\pic\P4-02Y-T 试用新增\`（8 张）刻意保留以便复核；删除该目录后再点「刷新图库」会把它们
  标记为 `missing`（图库行保留，不会自动删除资产）。

## 8. 产物

- `dist/PhotoSearch`（当前 HEAD 的 onedir 包，sha256 见 §2）。
- 证据：`test-results/p4-02y-t-packaged-smoke.json`、`test-results/p4-02y-t-real-trial.json`、
  `test-results/p4-02y-t-query-evidence.json`。
- 诊断侧最小改动：`tools/diagnostics/packaged_api_smoke.py`（新增刷新入口最小检查）。
- 一次性试用驱动（`tmp/` 内、不入库）：`tmp/p4-02y-t/{trial,probe,shutdown_probe}.py`。

## 9. 本轮之后 HEAD 的变化（提交时核实）

本轮打包/试用/证据全部对应 `b17ded6`。提交前核对 Git 发现 HEAD 已由**并行会话**推进到
`2235ab3`（`[P4-02Y-I 主审复审]`，另含 `7387915`、`73c5f37`；来源建库 build 档 + 持久来源进度 +
删除来源），因此 **`dist/PhotoSearch` 与本次 packaged smoke / 真实试用证据早于当前 HEAD 一步**，
"当前 HEAD 的包"这一说法只在 `b17ded6` 成立。

P4-02Y-I 触及 `refresh/{pipeline,progress,state}.py`、`api/{refresher,models,routes,context}.py`、
`indexing/semantic.py` 与 `web/*`。本次按新代码逐行核对过本卡 smoke 补的那条检查仍然成立（每日档
`manifest_path is None → semantic SKIPPED_UNAVAILABLE/MANIFEST_UNCONFIGURED`；`_search_ready` 返回
`(False, "MANIFEST_UNCONFIGURED")`；无 failures 且非 search-ready ⇒ 终态仍为 `FAILED`），但**未**在
`2235ab3` 上重新 build/smoke/试用；若要让打包与试用证据对齐当前 HEAD，需要在 P4-02Y-I 之上重跑本卡 §2–§4。
