# P0-04 — Face / RapidOCR weights, manifest and minimal runtime load probe

**Status:** PASS, executed 2026-09-11. Three in-task fix rounds (data-root resolution under the
packaged app's `LOCALAPPDATA` view, CRLF line endings in generated files, and a missing positive
face fixture). No Windows environment or import failure occurred, so no C1 handoff was needed.

## Task record

| Field | Value |
|---|---|
| Task | P0-04 — Face/RapidOCR fixed weights, manifest and minimal runtime load probe |
| Planned model | DS-L — DeepSeek V4.1 Flash, official API model `deepseek-flash`, thinking enabled, reasoning `low`, standard speed |
| Actual execution | The client's `config.toml` declared `model = "deepseek-flash"`, `model_provider = "deepseek"`, `model_reasoning_effort = "low"` when this task started, which matches the DS-L tier. The client exposed no `thinking.type` field and no token/cost breakdown, so DS-L is recorded without claiming the thinking flag was verified item by item. No model switch is claimed. |
| Git baseline at start | `261c26d` (`P0-03完成`). `docs/tasks/P0-04.md` and `docs/development-status.md` were still the P0-03 revision, so the card was expanded to the launcher format and the status was corrected in this task. Working tree was clean. |
| Previous task | P0-03, PASS, re-verified in this session (`docs/reports/P0-03-chinese-clip-onnx.md`) |

## Files changed

- `tools/prepare_models/face_ocr_common.py` (new) — data-root, hashing and manifest helpers.
- `tools/prepare_models/prepare_face_ocr.py` (new) — pins the OCR models, downloads and extracts
  the InsightFace pack, writes the manifest.
- `tools/prepare_models/verify_face_ocr.py` (new) — re-hashes every artifact and runs the CPU
  OCR/face probe on script-generated fixtures.
- `tools/prepare_models/manifests/face-ocr.json` (new) — model identity, hashes, measured ONNX
  I/O, preprocessing, licenses.
- `tools/prepare_models/manifests/face-ocr-validation.json` (new) — probe record.
- `tests/integration/test_face_ocr_manifest.py` (new) — manifest and probe-record contract.
- `pyproject.toml`, `uv.lock` — `rapidocr-onnxruntime>=1.4.4,<1.5` and `insightface>=2.0,<3`
  plus their CPU dependencies; 88 packages locked.
- `docs/reports/P0-04-face-ocr-load.md` (new), `docs/tasks/P0-04.md`, `docs/tasks/P0-05.md`,
  `docs/development-status.md`.

No product code changed: `src/photo_search/` is untouched, no API, worker, index or migration was
added, and `GET /api/v1/health` keeps its P0-01 behaviour.

## Observed environment

| Item | Observed value |
|---|---|
| Interpreter | CPython 3.12.10 (`uv` 0.11.0) |
| ONNX Runtime | 1.30.0; providers `['AzureExecutionProvider', 'CPUExecutionProvider']` |
| RapidOCR | `rapidocr-onnxruntime` 1.4.4 |
| InsightFace | 2.0 (pure-Python `py3-none-any` wheel; no local compiler needed) |
| Numerics / imaging | numpy 2.5.3, scipy 1.18.1, scikit-image 0.26.0, opencv-python 5.0.0.93, Pillow 11.3.0 |
| Data root | `%LOCALAPPDATA%\PhotoSearchData`; the packaged desktop app exposes the same directory as `...\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\PhotoSearchData`, and the manifest records the spelling this run resolved |

## Pinned weights

All files live under the user data root, outside the repository, and are reproduced by
`uv run python tools/prepare_models/prepare_face_ocr.py`.

| Artifact | Path (relative to `PhotoSearchData/models`) | Bytes | SHA-256 |
|---|---|---|---|
| OCR detection (PP-OCRv4 mobile) | `ocr/ppocr-v4-mobile/ch_PP-OCRv4_det_infer.onnx` | 4,745,517 | `d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9` |
| OCR direction classifier | `ocr/ppocr-v4-mobile/ch_ppocr_mobile_v2.0_cls_infer.onnx` | 585,532 | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |
| OCR recognition (PP-OCRv4 mobile) | `ocr/ppocr-v4-mobile/ch_PP-OCRv4_rec_infer.onnx` | 10,857,958 | `48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b` |
| OCR runtime config copy | `ocr/ppocr-v4-mobile/config.yaml` | 1,221 | `bf94a1da4cba828e67b1d61e27cee14d9e7da27c9f272e04048a17e41ae97332` |
| Face detection (`buffalo_l`) | `face/buffalo_l/det_10g.onnx` | 16,923,827 | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| Face recognition (`buffalo_l`) | `face/buffalo_l/w600k_r50.onnx` | 174,383,860 | `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43` |
| InsightFace pack archive (cached) | `PhotoSearchData/cache/buffalo_l.zip` | 288,621,354 | `80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f` |

Sources: the OCR models are copied out of the pinned RapidOCR wheel (so the package version pins
them) from `https://pypi.org/project/rapidocr-onnxruntime/`; the face weights come from the
official InsightFace release asset
`https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip` (the older
`v0.7` tag serves the same asset). Only `det_10g.onnx` and `w600k_r50.onnx` are extracted; the
landmark, gender/age and 3D models of the pack are not used, per architecture §8.3.

## Licenses recorded

- RapidOCR: `Apache-2.0` (PyPI metadata for `rapidocr-onnxruntime`, read 2026-09-11). The bundled
  PP-OCRv4 mobile weights are published by PaddleOCR; the manifest marks the weight licence as
  **not re-verified file by file**.
- InsightFace: the official `model_zoo` README states `ALL models are available for
  non-commercial research purposes only.` The manifest records `allow_redistribution: false` and
  the source link; GitHub's API reported no recognized licence file for the repository, which is
  recorded as unverified. Shipping this pack to end users therefore needs a separate decision.

## Measured ONNX interface

| Model | Inputs (measured) | Outputs (measured) |
|---|---|---|
| OCR detection | `x` `tensor(float)` `[N,3,H,W]` (dynamic) | `sigmoid_0.tmp_0` `[N,1,H,W]` |
| OCR classifier | `x` `tensor(float)` `[N,3,H,W]` | `save_infer_model/scale_0.tmp_1` `[N,2]` |
| OCR recognition | `x` `tensor(float)` `[N,3,H,W]` | `softmax_11.tmp_0` `[N,T,6625]` |
| Face detection | `input.1` `[1,3,640,640]` runtime blob | 9 tensors: 12800/3200/800 anchors × {1 score, 4 box, 10 keypoints} for strides 8/16/32 |
| Face recognition | `input.1` `[1,3,112,112]` | measured at runtime `[1,512]` |

InsightFace's own model objects report `input_mean = 127.5`, `input_std = 128.0` for detection and
`127.5`/`127.5` for recognition; those measured values are stored in the probe record, and the
manifest keeps the reference preprocessing description next to them.

## Real CPU probe

`uv run python tools/prepare_models/verify_face_ocr.py` re-hashes all six artifacts and the config
file, then runs both stacks on CPU only.

OCR (Pillow-rendered fixtures, Microsoft YaHei `C:\Windows\Fonts\msyh.ttc`):

| Fixture | Boxes | Recognized text | Score | Seconds |
|---|---|---|---|---|
| `cjk` ("照片搜索测试" / "编号 20260911") | 2 | `照片搜索测试` / `编号20260911` | 0.9991 / 0.9999 | 1.93 |
| `ascii` ("Photo Search Test" / "ID 20260911") | 2 | `Photo Search Test` / `ID 20260911` | 0.9970 / 0.9810 | 1.49 |
| `blank` (white page) | 0 | — (no crash, `None` result) | — | — |

Face:

| Fixture | Faces | Detail |
|---|---|---|
| Schematic faces `neutral` / `warm` / `cool` | 0 / 0 / 0 | Drawn shapes are not detected. Recorded as an honest negative, not a failure; these fixtures only prove the detector runs and returns an empty structure. |
| `sample-astronaut` (public-domain sample bundled with scikit-image, not from the user's library) | 1 | bbox `[180.91, 59.41, 270.49, 179.11]` (512×512 source), `det_score` 0.8304, five keypoints `[204.3,100.9] [247.2,102.9] [224.1,128.7] [202.7,139.1] [244.8,141.1]`, embedding `[512]` with L2 norm 19.66 |

Recognition graph smoke check on a deterministic zero patch: `[1,3,112,112]` → `[1,512]`, L2 norm
9.13. This only proves the graph and the 512-dimension contract; it is not a face and is labelled
as such in the record.

GPU: this ONNX Runtime build lists `AzureExecutionProvider` and `CPUExecutionProvider` — no CUDA
provider is present, so no GPU session was created and no GPU claim is made for the RTX 4060.

## Commands, exit codes, results

| Command | Exit | Result |
|---|---|---|
| `uv run python --version` | 0 | `Python 3.12.10` |
| `uv lock` | 0 | `Resolved 88 packages` |
| `uv sync --all-groups` | 0 | environment already satisfied (68 packages checked) |
| `uv run python tools/prepare_models/prepare_face_ocr.py` | 0 | `status: pass`; steps `rapidocr_copy`, `face_extract`, `manifest` |
| `uv run python tools/prepare_models/verify_face_ocr.py` | 0 | `status: pass`; steps `manifest_hashes`, `ocr_inference`, `face_inference`, `result` |
| `uv run pytest tests/integration/test_face_ocr_manifest.py` | 0 | `2 passed` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run pytest tests/unit tests/integration` | 0 | `13 passed` |
| `git add -N tools tests` + `git diff --check` | 0 | no whitespace errors |

## Fix rounds

1. **Data root under the packaged app.** `%LOCALAPPDATA%` is presented by the desktop app as
   `...\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local`, and the first run also failed
   because the OCR target directory did not exist. `face_ocr_common.default_data_root()` now
   prefers the profile path, canonicalizes with `realpath`, and the prepare script creates the
   target directory explicitly.
2. **CRLF in generated files.** `Path.write_text` translates `\n` to `\r\n` on Windows, so the
   first `git diff --check` reported trailing whitespace on every generated line.
   `write_json()` now opens with `newline="\n"`, and the two Python files were normalized to LF.
3. **No positive face case.** All three schematic fixtures returned zero faces. Rather than
   claiming success, the probe gained a documented public-domain sample photo
   (`skimage.data.astronaut`, 440,419 bytes, SHA-256
   `b8c8afe36141c45dd06f55d257d6b951b4c77a1a2c31fe09e78452cdda2dfcd4`) that must yield at least
   one face with a 512-dimension embedding or the probe fails. The negative synthetic results stay
   in the record.

## Session environment defects

The managed sandbox helper could not start on this machine (`helper_sandbox_lock_failed ...
SetNamedSecurityInfoW sandbox dir failed: 5` on `.codex/.sandbox-bin`), so every command in this
session ran with escalated permissions. The `apply_patch` tool failed for the same reason, so the
file edits were applied through escalated shell writes; nothing was written outside the repository
and the user data root.

## Not verified here

- OCR quality on real screenshots and documents: no accuracy, reading-order, box-correctness,
  high-resolution slicing or mixed-script measurement; the fixture is rendered text, not a photo.
- Face detection/recognition accuracy, threshold calibration, alignment error against
  InsightFace's own pipeline, and behaviour on side faces, children, low-light or blurred images.
- GPU execution, dynamic detection sizes, batching, FP16/int8 quantization, cancellation and
  concurrency; nothing about throughput or resource ceilings.
- The successor RapidOCR package `rapidocr` (2.x/3.x) and its model-download path; P0-04 pins the
  ONNX-only 1.4.x line.
- Whether the InsightFace weights may be redistributed to end users, and whether the OCR weights
  carry the same terms as the PaddleOCR project.
- Any product integration: no worker, adapter, `ProcessorSpec`, index write or API reads these
  weights yet.

## Usage and cost

Downloads: the InsightFace pack archive (288,621,354 bytes) and the new wheel set
(`opencv-python` 42.0 MiB, `rapidocr-onnxruntime` 14.2 MiB, `scipy` 35.0 MiB, `scikit-image`
11.4 MiB, `insightface` 1.1 MiB, `shapely` 1.6 MiB). The client exposed no token, cache or cost
breakdown for this session, and no DeepSeek API call was made by the task scripts themselves.

## Handoff record (strategy §8.4)

```text
任务 ID / 状态：P0-04 / PASS（3 个修复轮次：数据根解析、CRLF 行尾、缺少正样本夹具）
实际模型 / reasoning / 速度：客户端 config.toml 声明 deepseek-flash / provider=deepseek / reasoning_effort=low（对应 DS-L），标准速度；客户端未暴露 thinking 字段与费用明细，未做切换声明
基线提交号：261c26d（P0-03完成），任务卡与状态修正后另提交
已完成行为：固定并复制 PP-OCRv4 移动检测/方向/识别权重与 buffalo_l 检测/识别权重到用户数据目录并记录来源、字节数、SHA-256、预处理、许可证；生成 manifest 与真实 CPU 探针记录；OCR 对中文/ASCII 夹具真实识别（置信度 0.98~0.9999），空白图返回空结构；人脸检测在公共领域样例照片上检出 1 张脸（score 0.8304，5 关键点，512 维嵌入），识别图 [1,512]
修改文件与接口/迁移变化：新增 tools/prepare_models/{face_ocr_common,prepare_face_ocr,verify_face_ocr}.py、manifests/face-ocr.json、manifests/face-ocr-validation.json、tests/integration/test_face_ocr_manifest.py；改 pyproject.toml 与 uv.lock（88 包）；docs 三份；无产品代码、接口或迁移变化
实际命令和结果：见上表（全部退出码 0：uv lock、uv sync、prepare、verify、pytest 集成 2 passed、ruff、pytest 全套 13 passed、git diff --check）
报告/详细日志路径：docs/reports/P0-04-face-ocr-load.md
未验证项和具体阻塞：真实截图 OCR 质量、人脸识别精度与阈值、GPU、动态尺寸/批处理/量化、rapidocr 2.x 路径、权重再分发条款；无阻塞
失败修复轮次：3
下一任务及必须读取的文件：P0-05（AI 子进程 spawn、有界消息、退出与异常回传 + 最小 onedir 打包），任务卡 docs/tasks/P0-05.md，执行模型 C1 / GPT-5.6 Terra / medium；需读 AGENTS.md、docs/development-status.md、主架构 §3、§6、§11.1、§11.3.4、§12.4、§14、§15 与策略 §2.1、§6.1、§7.2、§8.1
可见 GPT 额度观察：未测量
DS 全任务请求用量/缓存/费用：客户端未提供用量明细；本任务脚本未直接调用 DeepSeek API
独立审查者与结果：本任务不需要独立审查
```

## Next task

**P0-05 — AI subprocess spawn, bounded messages, exit and error propagation, plus a minimal
onedir package** (`docs/tasks/P0-05.md`), planned model **C1 / GPT-5.6 Terra / medium** per
strategy §6.1. It hands the ONNX/Windows packaging work back to Terra, so it needs a client
switch before it starts.
