# ADR-0003 — P2 处理任务、运行时与搜索配置冻结契约

- 日期：2026-09-13
- 状态：**ACCEPTED / FROZEN FOR P2 IMPLEMENTATION**
- 适用范围：P2-01 及后续 P2 调度、产物、索引与搜索实现
- 机器可读伴随物：`contracts/p2-contracts.v1.json`
- 冻结基线：ADR-0002、`schema_version = 1`

## 1. 决策摘要与优先级

本 ADR 冻结 P2 的最小稳定协议：`JobRequest`、`JobResult`、`ProcessorSpec`、
`RuntimeCapabilities`、适配器接口、`SearchProfile`、向量空间隔离与
`CatalogFilterRevision`。它不落地新表、调度器、outbox、LanceDB 或搜索 API。

ADR-0002 的身份、五字段 job 唯一键、状态机、lease、`attempts`、迟到提交和迁移规则保持
不变。本 ADR 只能在这些语义之上增加执行与结果信封。伴随 JSON Schema 是本 ADR 的派生物；
冲突时以本 ADR 为准，并必须在同一次变更中修正两者。

冻结枚举（契约测试逐项核对）：

```text
device_policy: GPU_REQUIRED | CPU_ALLOWED | CPU_PREFERRED
requested_device: auto | cpu | gpu
resolved_device: cpu | gpu
job_state: QUEUED | RUNNING | SUCCEEDED | RETRY_WAIT | BLOCKED | FAILED | CANCELLED
job_result_state: SUCCEEDED | RETRY_WAIT | BLOCKED | FAILED | CANCELLED
```

上述大写 device policy 是稳定外部协议值；现有 runtime/device.py 的小写 Python Enum 仅作一对一内部
映射。稳定协议的 resolved_device=gpu 对应现有运行时代码的 device=cuda，并必须同时记录实际
CUDAExecutionProvider；实现不得把两个拼写当成两个逻辑设备或两个 space。CPU_ALLOWED 在派发前按
健康能力快照选 CPU 属于明确的 auto 解析，不是执行失败后的隐式 fallback。

所有跨进程对象都携带 `contract_version = "p2-contracts.v1"`，拒绝未知字段。时间为 UTC epoch
毫秒；SHA-256 为 64 位小写十六进制；字节大小为非负整数。

## 2. JobRequest：身份、快照与设备解析

### 2.1 身份只有 ADR-0002 的五字段

逻辑任务身份仍且仅为：

```text
(subject_type, subject_id, input_revision, task_type, processor_id)
```

`job_id` 是持久行标识，`lease_token` 是本次执行资格；`requested_device`、
`resolved_device`、`runtime_provider`、`precision`、priority、重试次数和 worker ID 都不构成第六个
身份字段。需要因 device/precision/runtime 变化而产生语义不同的输出时，必须发布新的
`processor_id`，向量输出还必须发布新的 `space_id`，不能把设备塞进唯一键绕过版本管理。

### 2.2 写入责任与 `auto` 唯一解析规则

- 请求创建者写五字段身份、`requested_device` 与输入 revision；`requested_device` 只允许
  `auto|cpu|gpu`。
- scheduler 在领取后、派发前，根据冻结的 `ProcessorSpec` 和一份健康的
  `RuntimeCapabilities` 写 `resolved_device`、`runtime_provider`、`precision` 和
  `runtime_capability_revision`。worker 不得自行改选设备。
- worker 必须核对实际 provider/device/precision 与派发值一致，并把实测值原样写回
  `JobResult`；不一致为 `RUNTIME_RESOLUTION_MISMATCH`，结果不得提交。
- `auto` 的解析唯一：`GPU_REQUIRED` 选择 GPU；`CPU_ALLOWED` 在 GPU 已通过该 processor 的专项
  验收且当前健康时选择 GPU，否则选择 CPU；`CPU_PREFERRED` 选择 CPU。显式 `cpu` 在
  `GPU_REQUIRED` 下为 `DEVICE_POLICY_CONFLICT`；显式 `gpu` 只有专项验收且当前健康时才可派发。

`JobRequest.input_snapshot` 固定这次执行看到的 subject revision，并可携带已知 asset SHA-256、
location、size/mtime。空值表示该类 subject 不适用该字段，不表示跳过提交时复核。需要读取原文件的
worker 仍须遵守架构 §6.3 的读前/读后检查；严格快照任务使用受控临时副本并核对完整 hash。

## 3. JobResult、产物与错误分类

`JobResult` 必须回显 `job_id`、`lease_token`、`input_revision`、`task_type`、`processor_id`，以及
实际 `resolved_device/runtime_provider/precision`。主进程在同一个写事务中先按 ADR-0002 §6.5
验证当前 `RUNNING`、未过期 lease、revision、processor，再核对派发设备语义，之后才允许写
artifact、业务结果、终态或 progress 终值。任何迟到、过期、revision 不符、processor 不符、
provider/device 不符的结果都不得造成持久写入。

`artifacts` 是零个或多个引用：

- `embedding`：必须携带 `space_id` 与 `dimension`，且与 processor/profile/index generation 一致；
- `structured`：保存版本化 JSON 结果，可用于 GPS、地名候选等非向量任务，不写向量表；
- `binary`：保存版本化二进制产物引用；
- checksum 校验通过后才可发布；正式的临时文件、SQLite artifact、outbox 与索引确认顺序留 P2-03；
- `SUCCEEDED` 允许 `artifacts=[]`。没有 GPS、没有脸、没有文字或没有匹配是成功空结果，不是失败。

进度只能在有效 lease 下单调增加到 `[0,1]`。取消在安全批次边界检查；当前批次可以被丢弃并重算，
未通过提交资格的部分结果不得发布。`CANCELLED` 不等定为“零计算浪费”。

错误分类：

| 分类 | JobResult 状态 | 重试预算 | 例子 |
|---|---|---|---|
| 临时执行故障 | `RETRY_WAIT` | 消耗一次 | 短暂 I/O、已验证可恢复的 worker 异常 |
| 能力/外部条件阻塞 | `BLOCKED` | 不消耗 | GPU/模型/来源不可用、运行时包不匹配、减小已支持 batch/卸载闲置模型后仍 OOM |
| 确定永久错误或耗尽 | `FAILED` | 可直接失败或已耗尽 | 损坏输入、协议不兼容、重复同一失败配置达到上限 |
| 用户取消 | `CANCELLED` | 不再执行 | 在批次边界观察到取消 |

错误信封必须有稳定 `code`、脱敏 `message`、`retryable` 和可空 `blocking_reason`。模型加载失败先按
原因分类：文件暂时缺失为 `BLOCKED`，checksum/协议确定不符为 `FAILED`；OOM 不允许通过未经验证的
precision、batch 或 CPU 隐式降级继续。

## 4. ProcessorSpec

### 4.1 公共字段

所有 processor 都有：`contract_version`、`spec_revision`、版本化 `processor_id`、
`processor_kind`、`task_type`、`config_hash`、`input_type`、`output_type`、`device_policy`、
`runtime_backend`、`runtime_package_id`、`supported_devices`、`supported_precisions`、
`max_batch_size`、`cancellation_supported`、`resource_measurements`。

`config_hash` 覆盖所有影响输出或资格的规范化配置。`resource_measurements` 只记录有 evidence ID、
设备、precision、batch、时间的实测值；未知值保持缺失/空集合，不能把估算写成已测量事实。

### 4.2 明确类型分支

- `embedding_model` 额外必需：模型 ID、resolved revision、一个或多个权重 SHA-256、图像与查询
  预处理版本、tokenizer、指令模板、归一化版本、dimension、metric、`space_id`、像素和 token
  上限。首版一个输入只产出一个向量。
- `reranker_model` 额外必需：模型/权重版本、查询与候选预处理、tokenizer、指令模板、最大 token；
  输出是候选分数，不伪造 embedding dimension/metric/space。
- `non_model` 只有公共字段。GPS 提取、地名解析等使用版本化 processor ID/task type/config hash，
  但禁止为它们伪造 model、weights、dimension、metric 或 `space_id`。
- `structured_model` 表示运行模型并发布版本化 structured JSON artifact、但不产出向量的处理器；除公共字段外，
  必须声明 `model_id`、`model_revision` 和非空 `weights`。它不得声明 embedding 专属字段，也不得产生
  `embedding` artifact，`output_type` 也不得为 `embedding`。其设备策略和支持设备由 processor 自身声明，
  并仍完整遵循 §5 的资格矩阵与专项 evidence。
  该分支为 `p2-contracts.v1` 的加性扩展；既有三种 kind、尤其 `non_model` 的 CPU 限制不变。
  旧版解析器会拒绝新 kind；发布时写入者和读取者须一同升级，不得让旧版读取者处理新 spec。

已冻结基础 embedding processor 使用：

```text
processor_id = chinese-clip-vit-b-16@36e679e6-fp32-v1
space_id = chinese-clip-vit-b-16:c8ac42d3907c57b0
dimension = 512
metric = cosine
normalization = L2
```

`space_id` 中的短 hash 与当前 image tower manifest SHA-256 前缀一致，但规则不是“模型昵称 +
维度”。完整空间指纹必须覆盖两塔权重 revision/checksum、图像/查询预处理、tokenizer/指令模板、
归一化和任何改变数值语义的配置。

## 5. device_policy 与设备资格

本项目**禁止隐式 fallback**。CPU provider 的存在不构成 fallback 授权，显式 GPU 失败也不得静默
转 CPU。

| policy / 请求 | `auto` | `cpu` | `gpu` |
|---|---|---|---|
| `GPU_REQUIRED` | 健康且专项验收通过的 GPU，否则 `BLOCKED` | 拒绝 | 健康且专项验收通过的 GPU，否则 `BLOCKED` |
| `CPU_ALLOWED` | 合格健康 GPU 优先，否则 CPU | CPU | 合格健康 GPU，否则 `BLOCKED` |
| `CPU_PREFERRED` | CPU | CPU | 只有该 processor 专项 GPU 验收通过且健康才允许，否则 `BLOCKED` |

资格判断同时需要：processor 声明支持 device/precision、RuntimeCapabilities 声明 provider 可用、
该 provider 最近 health check 为 healthy、且存在对应 processor/device/precision 的专项验收 evidence。
列出 provider 但未健康检查、只有别的 processor 的验证、或只有估算资源都不合格。

`GPU_REQUIRED` 因 CUDA 不可用/不健康进入 `BLOCKED`，不消耗普通错误重试次数。GPU 恢复、health
check 通过且专项 evidence 仍适用于当前 revision 后，scheduler 可把该阻塞任务重新置为可调度状态；
恢复动作不得绕过 ADR-0002 的合法状态迁移。OOM 处理只能尝试 spec 已声明并验证的更小 batch、卸载
闲置模型和有限重试；仍失败则 `BLOCKED`。

device/precision/runtime 变化是否仍属于同一 space 不能靠名称猜测：只有固定输入对照证明数值兼容、
记录 tolerance 与 evidence 后才可复用 `processor_id/space_id`；否则发布新 ID。

## 6. RuntimeCapabilities

RuntimeCapabilities 是带 revision 的一次运行时事实快照，至少包含 runtime package ID/backend/name/
version、Python/platform/driver、provider 清单、每个 provider 的 device、precision、available、health、
health-check 时间和 evidence ID，以及整份快照的 health 与 evidence IDs。

- `available=true` 只表示可枚举/可构造，不等于健康或对任一 processor 合格；
- `healthy` 必须来自当前包/驱动/provider 的实测健康检查；失效后生成新 capability revision，受影响
  任务阻塞；恢复也生成新 revision，不能改写旧运行记录；
- scheduler 将派发使用的 revision 绑定到 JobRequest；JobResult 回显实际 provider/device/precision；
- 现有 P0-06 仅证明当前 RTX 4060、ONNX Runtime 1.30.0、FP32、batch=1 的 Chinese-CLIP 路径；
  FP16、动态 batch、TensorRT 和 GTX 1660 实机仍未验证，不得声明合格；
- GPS/地名等 `non_model` 可使用 CPU + Python/库 provider，也必须有健康事实，但不需要 CUDA evidence。

## 7. Adapter 边界

稳定方法语义如下；具体 Python 签名由实现卡固定：

```python
class EmbeddingAdapter:
    def spec(self): ...
    def load(self, runtime_config): ...
    def encode_queries(self, texts, cancellation): ...
    def encode_images(self, asset_inputs, cancellation): ...
    def health(self): ...
    def close(self): ...

class RerankerAdapter:
    def spec(self): ...
    def score(self, query, candidates, cancellation): ...
```

`spec()` 返回不可变 ProcessorSpec；`load()` 必须核对 runtime package、provider/device/precision 和
权重 checksum；encode/score 在安全批次边界响应 cancellation；`health()` 返回结构化、可关联
capability revision/evidence 的结果；`close()` 幂等释放资源。所有 encoding 结果携带
`processor_id/space_id`，提交和检索两侧都核对。reranker 分数携带 processor ID，不携带伪造 space。

业务 API/服务层禁止顶层 import ONNX、PyTorch 等重型框架。后端和模型生命周期留在受管理 worker；
跨进程只传普通 JSON 元数据、受控 artifact 引用和数值结果。

## 8. SearchProfile 与 space 隔离

SearchProfile 创建后不可变，字段为：`profile_id`、`embedding_processor_id`、`space_id`、
`image_generation_id`、`query_preprocess_version`、`query_parser_version`、`fts_tokenizer_version`、
`fts_generation_id`、可空 `reranker_processor_id`、`fusion_config`、`runtime_package_id`、`created_at`。

单行 `active_search_profile` 只保存当前 `profile_id`。激活必须在一个 SQLite 事务内核对 profile、
processor、runtime 和所有 generation 已 READY，再同时维护生命周期标记和活动指针。禁止分别切换
“模型”和“向量表”。每次请求在开始时固定一个 profile；查询向量、候选缓存、search session 和分页
cursor 全部绑定 `profile_id` 与对应 generation。旧会话不能中途切换 profile。

embedding 写入、查询、缓存与 index generation 必须逐项核对 `space_id`。维度相同但 space 不同仍返回
结构化 `SPACE_MISMATCH`，不得计算相似度、写入或复用缓存。`RUNTIME_RESOLUTION_MISMATCH`、
`PROCESSOR_MISMATCH`、`PROFILE_GENERATION_MISMATCH` 同样拒绝成功。

人物命名、地点目录/归属/人工锁、人工纠错和当前可见性始终读取最新 SQLite，与 profile 回退解耦；
回退只恢复完整 profile、对应 runtime 与索引，不回滚人工知识。

## 9. CatalogFilterRevision

`CatalogFilterRevision` 是权威 SQLite 筛选事实的全局单调整数版本，独立于不可变 SearchProfile、
embedding `space_id`、index generation 和地名包版本。它绝不能成为 SearchProfile 字段。

- 有效地点归属、地点改名/层级、有效人物归属、可见性或人工可检索元数据改变时，在同一权威事务中
  递增 revision，并记录 cause、时间和受影响 asset ID（可为空，表示全量/未知影响集）；
- 仅机器地点候选刷新而有效归属未变，不递增地点筛选 revision；
- 搜索缓存、search session 和 cursor 同时绑定 `profile_id` 与创建时的 catalog filter revision；
- 分页/继续会话时 revision 不同，返回结构化 `CATALOG_FILTER_REVISION_STALE` 并让客户端重搜；
- 查询执行期间发生变化时，返回前以最新 SQLite 复核候选；不能用旧投影过滤已有结果后就认为完整，
  因为地点改名或有效归类可能产生新命中。实现必须用权威候选补查/重搜或显式“索引更新中”；
- 地名包更新但只改变机器建议时不使有效地点会话过期。

P2-01A 再冻结地点 cause、事务、DDL/API 和人工状态的精确枚举；本节只冻结通用外壳。

## 10. 正常、异常与边界判定

- CPU 任务必须记录 `requested/resolved/provider/precision`；符合 `CPU_PREFERRED auto` 时解析为 CPU。
- 合格 GPU 恢复后，GPU capability 阻塞任务可通过合法 `BLOCKED -> QUEUED` 恢复。
- 同一 processor 同时支持 CPU/GPU 时仍按 §5 矩阵唯一解析，不由 worker 临场挑选。
- `resolved_device` 与实际 provider 不一致、lease/revision/processor 过期、同维不同 space、profile
  半切换都必须结构化拒绝且零持久副作用。
- 换 device/precision 只有兼容性 evidence 证明数值空间未变时才能复用 space；没有证据就是新 space。
- 成功空结果与非向量结构化结果是正常完成路径。

## 11. 物理落地与迁移边界

本 ADR 不改变当前 `schema_version = 1`，不修改 ADR-0002、`0001_initial.sql` 或生产代码，也不新增
依赖。当前仓库没有 processors/artifacts/outbox/index_generations/search_profiles 的持久表和 LanceDB
写入。后续任务若新增表/列/迁移，必须从本 ADR 推导，使用新的增量 migration，并接受 C2 对唯一约束、
外键、状态与原子提交边界的复核；不得回写 `0001_initial.sql`。

## 12. 后果与未验证项

该决策以更严格的显式阻塞、版本数量和缓存失效换取可诊断、可恢复、可回退的一致性。它不证明运行时
实现已经拒绝所有坏输入；P2-01 的 JSON Schema/测试只验证文档伴随契约与样例。scheduler、worker、
artifact 提交、数据库约束、LanceDB 幂等、profile 激活竞争和故障恢复分别由 P2-02/P2-03/P2-08 实测。

未验证：GTX 1660 实机、FP16/量化/TensorRT、动态 batch、真实调度竞争、真实模型取消、OOM 恢复、
索引切换和地点筛选补查性能。不得从本 ADR 推导这些项目已经 PASS。
