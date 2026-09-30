# P4-02Y-L 最小契约裁决（DS-H）— GPU OCR 在 `p2-contracts.v1` 中的最小表达

- 任务卡：[docs/tasks/P4-02Y-L.md](../tasks/P4-02Y-L.md)
- 阶段：**DS-H 契约裁决（契约草拟 + 可实现性证明 + 工程预审）**
- 状态：**`CONTRACT RULING PASS / SOL FREEZE REQUIRED`**
- 基线：HEAD `d256b93`，工作树干净（2026-09-30 实测）
- 日期：2026-09-30
- 下一步：**C2 / GPT-5.6 Sol / high** 新会话聚焦终审并冻结 §4 的契约形态（V3.1 §11 必选门禁）
- 停点：**本裁决不解锁 Luna 实现**；Sol 冻结契约形态后才允许实现卡按冻结形态开工

---

## 0. 真实 Git 基线（开工第一步实测）

```text
$ git log -1 --format='%H %s'
d256b93feat(search): raise semantic result cap to 5000
$ git status --short --untracked-files=all
(空)
```

本裁决**只读**：未改 `src/**`、未改 `docs/decisions/**`（含 ADR-0003 与 companion）、未改任何
migration / `SUPPORTED_SCHEMA_VERSION` / `pyproject.toml` / `uv.lock` / `PhotoSearch.spec`，未跑任何
真实图库扫描。所有改动仅为本报告与 `docs/tasks/P4-02Y-L.md`、`docs/development-status.md` 的交接文字。

---

## 1. 裁决（先给结论）

1. **冻结契约无法通过“局部兼容扩展”表达 GPU OCR。** 阻塞点只有一处，但它落在共享面上：companion 的
   `NonModelProcessorSpec` 用 `const` 把 `non_model` 锁成 `device_policy = CPU_PREFERRED` +
   `supported_devices = ["cpu"]`。任何解开它的做法，要么**放宽全部 `non_model`**（卡面明令禁止，
   且会让 GPS / place / taken-time 的设备限制一起松动），要么**换一个 kind**。
2. **回退到 `embedding_model` / `reranker_model` 被否决。** OCR 不产出任何向量；`embedding_model` 在冻结
   companion 里是 `output_type const "embedding"` + 必填 `space_id`/`dimension`/`metric`/`weights`，向量路径
   还把它们直接当索引条目（`artifacts/store.py`、`search/embedding.py` 按该 kind 放行）。把 OCR 塞进去就是
   **伪造向量/space**，卡面明令禁止。
3. **最小诚实的表达 = 给 `p2-contracts.v1` 加性新增第 4 个 `processor_kind`：`structured_model`。**
   语义 = “**产生 structured artifact 的模型处理器**”：会跑模型（必须声明模型与权重），但发布的产物是
   版本化结构化 JSON，**不携带任何向量/space/dimension**。`contract_version` 保持
   `p2-contracts.v1` 不变；**`non_model` 的 `const` 逐字节不动**，GPS / place / taken-time 的声明与行为
   零变化。
4. **OCR 迁移到 `structured_model` 后取 `CPU_ALLOWED` + `supported_devices = ["gpu","cpu"]`
   + `FP32`。** `auto` 因此按 ADR-0003 §5 矩阵“合格健康 GPU 优先、否则 CPU”，显式 `cpu` 仍可跑，
   显式 `gpu` 缺资格时 `BLOCKED`——**不需要任何 scheduler 特判**，`scheduler/eligibility.py` 已经实现
   这套解析。
5. **路由结论：这不是 Luna 可以自行落地的变更。** 本变更直接修改 V3.1 §11 点名的
   “`ProcessorSpec.device_policy`、`requested/resolved device`、`RuntimeCapabilities` 与禁止隐式 fallback
   的设备资格契约”，并修改 ADR-0003 §4.2 的冻结类型分支与 companion 的 `ProcessorKind` 词表。
   按 §11「DS-H 负责契约草拟、可实现性证明和工程预审；命中本节清单时 Sol high 负责最终裁决」，
   **必须先由 Sol 冻结 §4 的形态，Luna 才可实现**。因此本卡当前状态为
   `CONTRACT RULING PASS / SOL FREEZE REQUIRED`，**不是** `SOL_NOT_REQUIRED`。

---

## 2. 只读事实复核（卡面假设 vs 实测）

| 卡面 / 交接假设 | 本轮实测位置 | 结论 |
|---|---|---|
| OCR 是 `non_model + CPU_PREFERRED + cpu-only` | `ocr/contract.py:36-42`（`OCR_PROCESSOR_KIND/DEVICE_POLICY/SUPPORTED_DEVICES`）；`ocr/processor.py:110-128`（`ocr_processor_spec`） | ✅ 完全一致 |
| 冻结契约把 `non_model` 钉成 `CPU_PREFERRED + ["cpu"]` | companion `docs/decisions/contracts/p2-contracts.v1.json:493-541`（`device_policy const CPU_PREFERRED`、`supported_devices const ["cpu"]`）；代码镜像 `scheduler/contracts.py:563-570` | ✅ 钉在 **companion + 代码**（**不在** ADR-0003 正文，见 §3 备注） |
| `processor_kind` 是闭集，且只有三种 | ADR-0003 §4.2（`docs/decisions/ADR-0003-*.md:113-124`）；companion `$defs/ProcessorKind`（同文件 `:49-51`）；`scheduler/contracts.py:26` | ✅ 闭集 3 值，`ProcessorSpec.oneOf` 三分支 |
| RapidOCR 正式路径未启用 CUDA | `ocr/engine.py`：`PinnedOcrEngine.__init__` 只传权重/阈值，**不传 providers**；`OcrEngineCache.engine()` 缓存键只有权重签名 | ✅ CPU-only 是默认路径，且缓存键**不含设备**（见 §6 H1） |
| OCR job 是 `auto` 请求 | `jobs/queue.py:171`（`requested_device: str = "auto"`）；`ocr/service.py::enqueue_targets` 未覆盖该参数 | ✅ 建库 OCR 走 `auto`，所以“默认走 CUDA”只能靠 **policy** 实现 |
| 设备资格矩阵已具备 | `scheduler/eligibility.py:96`（`device not in spec.supported_devices` → 不合格）、`:201-217`（`CPU_ALLOWED` auto = GPU 优先、否则 CPU）、`:110-137`（要求 capability 健康 provider + **processor 专项 evidence**） | ✅ 零 scheduler 改动 |
| 派发/回写设备真值 | ADR-0003 §2.2；`scheduler/engine.py::_validate_result_identity`（`dispatched != reported` → `RUNTIME_RESOLUTION_MISMATCH`） | ⚠️ 只校验“worker **报告**的值”，见 §6 H2 |
| 错误码够用 | `catalog/errors.py:56-58`（`DEVICE_POLICY_CONFLICT`/`RUNTIME_CAPABILITY_UNAVAILABLE`/`RUNTIME_RESOLUTION_MISMATCH`）、`:25`（`PROCESSOR_MISMATCH`） | ✅ **零新增错误码** |
| ADR-0003 把 `non_model` 描述为“无模型” | ADR-0003 §4.2（`:120`「`non_model` 只有公共字段…禁止伪造 model、weights、dimension、metric 或 `space_id`」）；主架构 §477（GPS/地名 = “无模型 CPU_PREFERRED”）；主架构 `:83`（“OCR 根据实际收益选择 CPU/GPU”）与 `:160`（RapidOCR 设备按实测选择） | ✅ 架构把 OCR 当模型、把 GPS 当地名级无模型任务，`non_model` 对 OCR 在语义上就是错的名字 |
| 已有模型处理器的先例 | `face/contract.py:66-70`：`FACE_PROCESSOR_KIND = "embedding_model"`、`FACE_DEVICE_POLICY = "CPU_ALLOWED"`、`FACE_SUPPORTED_DEVICES = ("gpu","cpu")` | ✅ Face 靠 **kind=embedding_model** 合法拿到 GPU；OCR 因不产向量，没有等价出路 |
| 契约测试已把当前形态钉死 | `tests/unit/test_p3_ocr_contract.py:126-147`（kind/`CPU_PREFERRED`/`["cpu"]`，且断言 `GPU_REQUIRED` 被拒）；`tests/unit/test_p2_contracts.py:159-171`（`NON_MODEL_SPEC`） | ✅ 改 kind 必须**同批**修正 OCR 契约用例；`NON_MODEL_SPEC` 用例**必须保持不动** |

---

## 3. 被否决的候选

**候选 A（保留 `non_model`，放宽它的设备钉）— 否决。**
把 `NonModelProcessorSpec` 的 `device_policy`/`supported_devices` 从 `const` 放开，是最省事的一条路，
但：

- 卡面明文禁止（“不得…放宽 GPS / place / taken-time 等所有 `non_model` 的设备限制”）；
- 它把三类**确实无模型**任务的设备限制一起松动，等于用 OCR 的需求改共享语义；
- 名字本身是错的：`non_model` 在 ADR-0003 §4.2 与主架构 §477 的语境里就是“无模型”。

**候选 B（OCR 改挂 `embedding_model`）— 否决。**
卡面明文禁止。且技术上也站不住：companion 要求 `output_type const "embedding"` 与
`space_id`/`dimension`/`metric`/`weights`，`artifacts/store.py` 与 `search/embedding.py` 按该 kind 放行
向量路径。OCR 没有向量，硬塞会造出**假 space**，直接违反 ADR-0003 §8 的同维不同 space 隔离。

**候选 C（scheduler / executor 特判“OCR 允许 GPU”）— 否决。**
卡面明文禁止。而且会把“设备资格由冻结契约 + capability + 专项 evidence 决定”这条 ADR-0003 §5 的核心
规则换成代码里的白名单，正是 §11 要防的那种隐式降级温床。

**候选 D（改 `non_model` 解释为“不产向量”）— 否决。**
会与 ADR-0003 §4.2 的“只允许公共字段…禁止伪造 model/weights”措辞冲突，也等于候选 A 的换皮。

> 备注（对 Sol 有用的事实）：`non_model ⇒ CPU_PREFERRED + ["cpu"]` 这条**只存在于 companion 与
> `scheduler/contracts.py`**，ADR-0003 正文并未如此规定（§4.2 只约束“只有公共字段”，§5 的表是按
> **policy** 而非按 kind 写的）。也就是说 companion 比 ADR 更严。即便如此，本裁决**不去动它**——
> 理由是卡面禁止放宽全部 `non_model`；给模型处理器另开一个诚实 kind 比松动共享约束更保守。

---

## 4. 裁定的最小表达（Sol 冻结对象，精确形态）

### 4.1 必须保持“零改动”的东西

- `contract_version` 仍是 `"p2-contracts.v1"`（**不新增 contract version**）；
- `$defs/NonModelProcessorSpec` **逐字节不动**（`device_policy const CPU_PREFERRED`、
  `supported_devices const ["cpu"]` 原样保留）；
- `scheduler/contracts.py` 里 `non_model` 的那段校验（`:563-570`）**逐字节不动**；
- `JobRequest` / `JobResult` / `ArtifactRef` / `RuntimeCapabilities` 的字段与闭集**不动**；
- `DEVICE_POLICIES`、`REQUESTED_DEVICES`、`RESOLVED_DEVICES`、`ARTIFACT_TYPES` **不动**；
- ADR-0003 §2（身份/五字段/auto 解析）、§3（JobResult/产物/错误分类）、§5（设备资格矩阵）、
  §6（RuntimeCapabilities）、§8（space 隔离）**不动**；
- OCR 的 artifact 形状与语义**不动**：`PAYLOAD_KEYS`、`validate_payload`、原文/区域/置信度/
  `search_text`/FTS、`config_hash`（= 钉死 `config.yaml` 的 sha256）、`model_fingerprint`、
  `MAX_LONG_EDGE`/切片/阈值、`max_batch_size = 1`、单 worker。

### 4.2 加性新增：`ProcessorKind` 增加 `structured_model`

```json
"ProcessorKind": {
  "enum": ["embedding_model", "reranker_model", "non_model", "structured_model"]
}
```

```python
# src/photo_search/scheduler/contracts.py
PROCESSOR_KINDS = ("embedding_model", "reranker_model", "non_model", "structured_model")
```

`ProcessorSpec.oneOf` 增加一支 `{ "$ref": "#/$defs/StructuredModelProcessorSpec" }`。
**加性**：既有三个分支与全部分支内 `const` 不变，因此仓里所有既有 envelope 逐字节仍然合法。

### 4.3 `StructuredModelProcessorSpec` 形态

公共字段与 `NonModelProcessorSpec` 完全一致（`contract_version`、`spec_revision`、`processor_id`、
`processor_kind`、`task_type`、`config_hash`、`input_type`、`output_type`、`device_policy`、
`runtime_backend`、`runtime_package_id`、`supported_devices`、`supported_precisions`、
`max_batch_size`、`cancellation_supported`、`resource_measurements`），另加**最小模型身份三件套**：

| 字段 | 约束 | 理由 |
|---|---|---|
| `model_id` | `NonEmptyString` | 与 `embedding_model`/`reranker_model` 对称：“是模型”就必须报出模型 |
| `model_revision` | `NonEmptyString` | 同上，且必须来自 P0-04 钉死 manifest，不得写成昵称 |
| `weights` | `WeightArtifact[]`，`minItems: 1` | 复用既有 `$defs/WeightArtifact`（`name`/`revision`/`sha256`） |

**不**加入任何向量字段：`space_id`、`dimension`、`metric`、`normalization_version`、`tokenizer_version`、
`max_pixels`、`max_tokens` **一律禁止出现**（`additionalProperties: false` 天然拒绝）。

设备字段取值域**从 `const` 变成正式词表**：

| 字段 | 取值 |
|---|---|
| `processor_kind` | `const "structured_model"` |
| `output_type` | `NonEmptyString`（OCR 仍为 `ocr_document.v1`） |
| `device_policy` | `$ref DevicePolicy`（`GPU_REQUIRED` / `CPU_ALLOWED` / `CPU_PREFERRED`） |
| `supported_devices` | `ResolvedDevice[]`，`minItems: 1`，`uniqueItems`（⟶ 允许 `["gpu","cpu"]`） |
| `supported_precisions` | `NonEmptyString[]`，`minItems: 1`，`uniqueItems` |
| `max_batch_size` | `integer ≥ 1`（OCR 仍为 1） |

不变量（写进 ADR-0003 §4.2 新增条目）：**`structured_model` 的处理结果不得产生 `embedding` artifact**
（`ArtifactRef` 层的既有校验已使 embedding 必带 `space_id`/`dimension`，因此该不变量无需新增 schema 约束，
只需在 ADR 里写明这条 kind 的语义）。

> 这组“模型身份三件套”是本裁决**唯一**留给 Sol 收紧/放宽的自由度：若 Sol 判定要取绝对最小，
> 可以让 `StructuredModelProcessorSpec` 与 `NonModelProcessorSpec` 同为“只有公共字段”。但**无论哪种取法，
> 本质裁决不变**：`non_model` 零改动 + 模型侧另开 kind + OCR 取 `CPU_ALLOWED`。
> 本裁决**推荐**要求三件套，因为它能防止 `structured_model` 变成“绕过 CPU 限制”的后门——
> 无模型任务无法在不伪造权重的前提下挂到该 kind 上。

### 4.4 OCR 的目标 spec（Luna 实现目标值）

```python
# src/photo_search/ocr/contract.py
OCR_PROCESSOR_KIND = "structured_model"        # 原 non_model
OCR_DEVICE_POLICY = "CPU_ALLOWED"               # 原 CPU_PREFERRED
OCR_SUPPORTED_DEVICES = ("gpu", "cpu")          # 原 ("cpu",)
OCR_SUPPORTED_PRECISIONS = ("FP32",)            # 不变（无 FP16 / TensorRT / 新依赖）
OCR_MODEL_ID = "rapidocr-ppocrv4-mobile"        # 新增
OCR_MODEL_REVISION = "<P0-04 钉死 pack revision>"  # 新增，必须取自 tools/prepare_models/manifests/face-ocr.json
```

`weights` = P0-04 manifest 里的三个图（`detection` / `classification` / `recognition`），
`name` 取 role、`sha256` 取 manifest 的 sha256、`revision` 取同一 pack revision；`config.yaml` 不是权重，
继续只通过 `config_hash` 表达。

> 唯一开放小项（请 Sol 一并冻结）：`OCR_MODEL_ID` / `OCR_MODEL_REVISION` 的**确切字符串**。本裁决只冻结
> “必须存在、必须来自 manifest、不得是昵称”这条规则，不冻结具体字面量。

### 4.5 设备资格：复用 ADR-0003 §5 矩阵，无新增机制

OCR 迁到 `CPU_ALLOWED` 后：

| 请求 | 结果（唯一解析） |
|---|---|
| `auto`（建库默认） | capability 有**健康** GPU provider **且**该 processor 的 `(gpu, provider, FP32, package_id)` **专项 evidence** 存在且 `gpu ∈ supported_devices` → GPU；否则 CPU（这是 §5 明确允许的 **auto 解析**，不是失败后 fallback） |
| 显式 `cpu` | CPU（`CPU_ALLOWED` 接受，作为显式请求，不是降级） |
| 显式 `gpu` | 资格齐备 → GPU；否则 `BLOCKED`（`RUNTIME_CAPABILITY_UNAVAILABLE`，不消耗重试） |

### 4.6 artifact / payload 零改动；真值只进 JobResult

**裁定**：运行时设备/provider/precision 的真值**只**记录在 `JobResult`（`resolved_device` /
`runtime_provider` / `precision`）与 `jobs` 行、qualification evidence 上，**不得**写进 OCR 的
content-addressed 文档 payload。

理由：OCR 文档是**内容寻址且确定性的**（“相同像素产生相同字节”，见 `ocr/processor.py` 模块文档与
`artifact_id_for`）。把随运行变化的 provider 写进 payload 会破坏该确定性，并让
`artifact_ref()` 的 `checksum_sha256` 在 CPU/GPU 之间漂移。

已知一处**非阻断**的措辞事实：`ocr/contract.py::ENGINE_CONFIG["providers"] = ["CPUExecutionProvider"]`
会随文档 payload 一起落库。它是 P0-04 钉死基线的**引擎配置声明**，不是本次运行的事实。本裁决要求
Luna **不得**把它改成随运行取值（那属于 artifact 语义变更，本卡“明确排除”）；实现报告只需记一条
“运行时 provider 真值见 JobResult/job 行”的说明。

---

## 5. 可实现性证明（逐条映射到现有机制，无需新机制）

| 目标 | 现有机制 | 结论 |
|---|---|---|
| `auto` 默认 GPU | `eligibility.resolve` 的 `CPU_ALLOWED` 分支（`eligibility.py:201-217`）+ `_qualified` | ✅ 只靠 spec 取值即可 |
| `gpu` 必须真存在 `CUDAExecutionProvider` | `runtime/device.py`：`resolve_device`（CUDA 不在 provider 列表即 `DeviceUnavailableError`）、`session_providers`、`preload_runtime_dlls`、`build_session_options` | ✅ 复用；不需要 CUDA Toolkit / TensorRT / 新依赖 |
| GPU 请求不得静默跑 CPU | `scheduler/engine.py::_validate_result_identity`（`dispatched != reported` → `RUNTIME_RESOLUTION_MISMATCH`） | ✅ 已有校验；**前提是** worker 报告的是**实测**值（见 §6 H2） |
| Job/result 如实记录 | 既有 `JobRequest`/`JobResult` 的 `requested_device`/`resolved_device`/`runtime_provider`/`precision`/`runtime_capability_revision` | ✅ 零字段变更 |
| capability + 专项 evidence | `scheduler/capability.py`（`snapshot_from_runtime`、`record_capability`、`record_qualification`） | ✅ 复用；OCR 侧需补 GPU 分支（见 §6 H3） |
| 失败分类 | `RUNTIME_CAPABILITY_UNAVAILABLE`（能力/资格阻塞）、`RUNTIME_RESOLUTION_MISMATCH`（派发/实测不符）、`DEVICE_POLICY_CONFLICT`（显式 cpu 撞 GPU_REQUIRED） | ✅ **零新增错误码** |
| 输出语义不变 | `validate_payload` + `PAYLOAD_KEYS` 不动 | ✅ CPU/GPU 交付同一 payload 形状 |

**结论：契约形态之外，Luna 不需要新的调度、新的表、新的迁移、新的错误码或新依赖。**

---

## 6. 实现必须遵守的最小边界（Sol 冻结后交 Luna，逐条当验收项）

- **H1（缓存键必须含设备）** `OcrEngineCache.engine()` 目前只按权重签名缓存。加了设备支持后，
  缓存键必须包含 `resolved_device`/`runtime_provider`/`precision`（及 provider options），否则
  **一次 CPU 构造的 session 会被 GPU job 复用**，正是本卡要消灭的静默 CPU 场景。
- **H2（报告值必须来自实测 session，不得回显请求）** `ocr/processor.py::ocr_executor` 现在把
  `request["resolved_device"/"runtime_provider"/"precision"]` 原样回显。GPU 路径建立后，必须从**实际构造
  的 session 的 provider 列表**推导报告值；与派发值不符即结构化失败（`RUNTIME_RESOLUTION_MISMATCH`），
  不得继续建库。
- **H3（qualification evidence 必须在真实 session 建好之后才写）** `ocr/service.py::record_runtime_evidence`
  现在只写 CPU provider 健康 + `device="cpu"` 的专项 evidence。GPU 分支必须在**真实 CUDA det/cls/rec
  session 成功构造之后**才写 `device="gpu"`、`provider_name="CUDAExecutionProvider"`、`precision="FP32"`
  的 evidence；探针失败就只能继续 CPU/`BLOCKED`，不得预写。
- **H4（CPU 显式诊断路径保留）** 显式 `cpu` 必须仍可跑通并如实报告（`CPU_ALLOWED` 语义），
  这是最小验收第 1 条。
- **H5（不改质量策略）** `MAX_LONG_EDGE=2000`、切片尺寸/重叠、`MIN_TEXT_SCORE`、`LOW_CONFIDENCE_THRESHOLD`、
  `MAX_REGIONS`、`max_batch_size=1`、单 worker 全部不动。
- **H6（payload 不变）** 见 §4.6；`ENGINE_CONFIG` 不得变成随运行取值。
- **H7（`non_model` 三处零改动）** companion `NonModelProcessorSpec`、`scheduler/contracts.py` 的
  `non_model` 校验段、`tests/unit/test_p2_contracts.py::NON_MODEL_SPEC` 用例，全部逐字节不动；
  GPS / place / taken-time 三个 processor 的 spec 与行为零变化（应有回归断言）。

---

## 7. 最小反例（冻结后必须能拒绝）

| # | 反例 | 期望 | 归属 |
|---|---|---|---|
| 1 | 用 `processor_kind = "non_model"` 申报 `device_policy = "CPU_ALLOWED"` 或 `supported_devices = ["gpu","cpu"]` | 拒绝（companion `const` + `scheduler/contracts.py` 都拒） | 契约层 |
| 2 | `structured_model` 携带 `space_id` / `dimension` / `metric` | 拒绝（`additionalProperties: false`） | 契约层 |
| 3 | `structured_model` 缺 `model_id` / `model_revision` / `weights` | 拒绝 | 契约层 |
| 4 | OCR spec 报 `supported_devices = ["gpu","cpu"]` 但**无** GPU 专项 evidence，请求 `auto` | 解析为 CPU（合法 auto），**不得**报 GPU | 资格层 |
| 5 | 同上但请求显式 `gpu` | `BLOCKED`（`RUNTIME_CAPABILITY_UNAVAILABLE`），零持久写入 | 资格层 |
| 6 | 派发 `gpu`/`CUDAExecutionProvider`/`FP32`，实际 session 只有 `CPUExecutionProvider` | 结构化失败（`RUNTIME_RESOLUTION_MISMATCH`），结果不得提交、不得留文档 | worker + 提交层 |
| 7 | OCR 文档 payload 出现第四类顶层键或 provider 字段 | 拒绝（`PAYLOAD_KEYS` 闭集） | artifact 层 |
| 8 | GPS / place / taken-time spec 试图申报 GPU | 仍被拒（`non_model` 钉未动） | 回归 |

---

## 8. 变更面清单

**Sol 冻结对象（契约面）**

| 文件 | 改动 |
|---|---|
| `docs/decisions/ADR-0003-*.md` | §4.2 增加一条 `structured_model` 类型分支（语义 + 必需字段 + “不得产 embedding artifact”）；§1/§5/§6 不动 |
| `docs/decisions/contracts/p2-contracts.v1.json` | `$defs/ProcessorKind` 加一个枚举值；新增 `$defs/StructuredModelProcessorSpec`；`$defs/ProcessorSpec.oneOf` 加一支。**`NonModelProcessorSpec` 逐字节不动** |
| `src/photo_search/scheduler/contracts.py` | `PROCESSOR_KINDS` 加值；`ProcessorSpec.from_envelope` 加 `structured_model` 分支。**`non_model` 校验段逐字节不动** |
| `tests/unit/test_p2_contracts.py` | 新增 `structured_model` 正/反例；**`NON_MODEL_SPEC` 用例不动** |

**冻结后 Luna 实现对象（产品面）**

| 文件 | 改动 |
|---|---|
| `src/photo_search/ocr/contract.py` | kind/policy/devices/model_id/model_revision/weights（§4.4） |
| `src/photo_search/ocr/processor.py` | spec 增补；executor 报告实测设备（H2） |
| `src/photo_search/ocr/engine.py` | provider 注入 + CUDA preload + 缓存键含设备（H1） |
| `src/photo_search/ocr/service.py` | GPU capability + 专项 evidence（H3） |
| `tests/unit/test_p3_ocr_contract.py` | 由 `non_model/cpu-only` 断言改到新形态（同批） |
| `tests/unit/test_p3_ocr_*` / `tests/integration/test_p3_ocr_*` | 最小验收 1–7 的定点用例与 fixture 对照 |
| `docs/reports/P4-02Y-L-ocr-cuda.md`、`docs/development-status.md` | 实现报告与交接 |

---

## 9. 未验证项与环境事实

1. 本裁决**未**在真实 RTX 4060 上复核 GPU OCR 的实际 provider/加速——那是实现卡的最小验收（本机已
   有 P0-06 的 CPU p50 ≈ 917.94 ms / CUDA p50 ≈ 75.95 ms 证据，但**未在本轮重跑**）。
2. GTX 1660 6 GB 实机、FP16、TensorRT、动态 batch、多 GPU 全部 **未验证**，本卡也明确排除。
3. 本机**未**安装/改动任何 CUDA Toolkit、TensorRT 或 Python 依赖；未触碰真实图库。
4. "局部兼容扩展不存在"这一判断的边界：本裁决把它理解为“改动必须落在 OCR 自己的契约面上、不修改共享
   词表”。若 Sol 采用更宽的解释（把“加性新增枚举值”也算兼容扩展），结论会变成“Luna 可直接实现”，
   但 §11 的 Sol 冻结门禁仍然适用于 `ProcessorSpec.device_policy`/设备资格契约。此差异请 Sol 明确裁决。
5. 沙箱内 `Get-ChildItem -Recurse` 对 `.pytest-tmp` 报 `Access denied`；本轮为**纯只读文档裁决**，
   未运行 pytest，无需正常权限路径。

---

## 10. 停止点与下一步

1. **停点**：本裁决结束，**未**修改任何冻结契约、未改 `src/**`、**未**标 P4-02Y-L PASS、
   **未**生成实现交接包以外的下一卡。
2. **下一步（必做）**：手动切到 **C2 / GPT-5.6 Sol / high** 新会话，输入 = 本报告 §4（冻结对象）、
   §7（最小反例）、§8（变更面）、§9.4（“局部”定义差异）。Sol 的产出 = 冻结 ADR-0003 §4.2 新增条目
   + companion 加性改动 + `scheduler/contracts.py` 分支，并明确 §4.3 的“模型身份三件套”取与不取、
   以及 §4.4 的两个字面量。
3. **冻结后**：才允许 Luna（实现档）按冻结形态实现 §6 的 H1–H7；完成后仍需**独立 DS-H 完整受影响面
   工程主审**（因为改的是 `ProcessorSpec.device_policy` 与设备资格契约，不是窄展示/打包面）。
4. **不得**用 scheduler 特判、伪造 processor 类型、放宽全部 `non_model` 或改 contract version 来绕过
   本裁决；若实现过程中发现必须修改 §4.1 的“零改动”清单，立即停手并回到 Sol。

> 下一阶段建议使用 **C2 / GPT-5.6 Sol / high**，请在 Codex 中手动切换模型/推理档后继续。

**V3.1 §2.2 升级判定**：本阶段未命中六类 DS-MAX 条件（无跨模块根因不明异常、无互斥状态矛盾、无
测试/ADR 互相矛盾、无连锁回归、无多状态机长推演、无事前 Sol 提问）；因此**不升 DS-MAX**，按 §11 直接
进入 Sol 冻结。
