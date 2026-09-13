# P2-01 — 处理任务、运行时与搜索配置契约报告

日期：2026-09-13。状态：**PASS（A 级文档/契约闭环）**。

本任务只冻结协议，没有实现 P2 调度、产物、索引或搜索能力。开始时 HEAD 为 d8d8514，工作树干净；
P1-06 报告的实际提交为 e42c5448d1d19d575bff97de93695c427d6532bb，与任务卡依赖一致。
执行模型由用户设为 GPT-5.6 Sol / high，本任务未自动切换模型或启动子 Agent。

## 1. 交付物

| 交付物 | 路径 |
|---|---|
| C2 冻结决策 | docs/decisions/ADR-0003-p2-processing-runtime-and-search-profile-contract.md |
| JSON Schema draft 2020-12 伴随物 | docs/decisions/contracts/p2-contracts.v1.json |
| 契约漂移与正反例测试 | tests/unit/test_p2_contracts.py |
| 下一任务卡 | docs/tasks/P2-01A.md |
| 本报告与状态 | docs/reports/P2-01-processing-runtime-search-profile-contract.md、docs/development-status.md |

没有修改 src/photo_search/**、任何 migration、0001_initial.sql、ADR-0001/0002、既有测试、
pyproject.toml、uv.lock 或模型 manifest；schema_version 仍为 1。

## 2. 仓库现状核对

- ADR-0002、jobs migration 与 queue.py 一致使用五字段逻辑唯一键
  (subject_type, subject_id, input_revision, task_type, processor_id)，并冻结七态状态机、lease、
  attempts 和迟到提交资格。ADR-0003 没有增加第六个身份字段。
- 现有 runtime/device.py 已有三种小写内部 DevicePolicy、设备解析和 provider 事实；worker 协议只
  提供有界 JSON 信封。P2-01 冻结的是后续稳定跨进程协议，不声称现有 worker 已经实现新 schema。
- 当前 schema 只有 metadata、scan_runs、source_roots、assets、file_locations、jobs；没有
  processors、artifacts、outbox、index_generations、search_profiles 或 LanceDB 写入。
- Chinese-CLIP manifest 证明当前基础空间 512 维、cosine、L2；space ID 的 c8ac42d3907c57b0
  与 image tower SHA-256 前缀一致。
- P0-06 实测边界是当前 RTX 4060、ONNX Runtime 1.30.0、FP32、batch=1；CPU/GPU 两条路径均有
  provider 证据，CUDA 路径禁用 CPU fallback。FP16、动态 batch、TensorRT 与 GTX 1660 实机未验证。

## 3. 冻结规则与来源/测试映射

| 冻结规则 | 来源 | ADR-0003 | Schema / 测试 |
|---|---|---|---|
| JobRequest/Result 五字段身份、lease/revision/provider 回显与零副作用拒绝 | ADR-0002 §3.6/§6；架构 §6.2/6.3 | §2～§3 | JobRequest/JobResult；枚举与 0001 jobs CHECK 对齐 |
| ProcessorSpec 公共字段与三类型分支 | 架构 §11.3.4、§13.1 | §4 | 三个分支 schema；non-model 与 embedding 正反例 |
| 三种 device policy、auto 唯一解析、禁止隐式 fallback | V3.1 §7、§11 | §2.2、§5 | DevicePolicy/RequestedDevice；ADR 文本逐项对齐 |
| RuntimeCapabilities 必须有健康检查与实测 evidence | 架构 §6.4、§11.3.4；P0-06 | §6 | RuntimeCapabilities/RuntimeProvider 正例 |
| Adapter 方法、取消、ID 回显、重型框架隔离 | 架构 §11.3.4 | §7 | 文档级冻结；运行时实现留 P2-02 |
| SearchProfile 不可变、完整原子切换、缓存/会话绑定 | 架构 §11.3.5～11.3.7 | §8 | SearchProfile closed object；禁止额外目录 revision |
| 同维不同 space 也拒绝 | 架构 §11.3.4/11.3.5；V3.1 §11 | §4.2、§8 | space/manifest 对齐；embedding 缺 space 反例 |
| 非模型 CPU processor、非向量 artifact、成功空结果 | 架构 §6.1/6.4/11.3.4 | §3～§4 | non-model、structured artifact、空 artifacts 正例 |
| CatalogFilterRevision 独立、会话失效与权威补查 | 架构 §4.5、§7.2、§9.5；V3.1 §7.1/7.2 | §9 | CatalogFilterRevision；SearchProfile 额外字段反例 |
| schema_version=1 与后续增量迁移边界 | ADR-0002 §7；任务卡 | §11 | 只读仓库核对；无 migration/production 差异 |

JSON Schema 的六个具名入口为 JobRequest、JobResult、ProcessorSpec、RuntimeCapabilities、
SearchProfile、CatalogFilterRevision，另含 device/status 枚举、artifact、provider 与三种
processor 分支。对象使用 required 与 additionalProperties=false。

## 4. 正常、异常与边界结论

- auto + GPU_REQUIRED 必须是合格健康 GPU，否则 BLOCKED；CPU_ALLOWED 在合格健康 GPU 可用时优先
  GPU，否则 CPU；CPU_PREFERRED auto 固定 CPU。显式 GPU 失败从不转 CPU。
- scheduler 写派发解析，worker 回显实际值；provider/device/precision 不符返回
  RUNTIME_RESOLUTION_MISMATCH，不得成功提交。
- BLOCKED 不消耗普通重试；GPU 恢复并通过健康检查后，只能通过 ADR-0002 合法状态迁移恢复。
- 模型加载缺失与 checksum/协议不符分开分类；OOM 只能用 spec 已验证的小 batch/卸载路径有限处理，
  不允许未经验证的 precision 或 CPU fallback。
- 同维不同 space、旧 lease/revision、错 processor、profile 半切换全部结构化拒绝。
- structured artifact 和 artifacts=[] 均可 SUCCEEDED；没有 GPS/脸/文字/匹配不是失败。
- CatalogFilterRevision 不进入 SearchProfile。人工有效筛选变化使旧会话过期；最终 SQLite 复核能
  排除旧结果但不能补回新命中，因此冻结权威候选补查/重搜/明确更新中边界。

## 5. 验证

环境没有 jsonschema、fastjsonschema 或 referencing 包。为遵守“不新增依赖”，新测试实现并说明了
本伴随物实际使用的 draft 2020-12 关键字子集验证器：本地 ref、type、required、
additionalProperties、enum/const、oneOf/anyOf/allOf/if/then/not、数组、数值与 pattern。它真实验证
正反例，但不是完整通用 JSON Schema validator，也不是运行时拒绝实现。

| 命令 | 结果 |
|---|---|
| uv --cache-dir 任务专用 TEMP 缓存 run --no-sync pytest -q tests/unit/test_p2_contracts.py（唯一 TEMP basetemp，禁用 cacheprovider） | exit 0，11 passed |
| uv --cache-dir 任务专用 TEMP 缓存 run --no-sync ruff check . | exit 0，All checks passed |
| git diff --check（含提交前已跟踪全部新文件） | exit 0，clean |

第一次 focused run 为 10 passed / 1 failed：测试正则先匹配到 scan_runs 的 state CHECK，导致与 jobs
七态集合比较失败。修正为从 CREATE TABLE jobs 段提取后，契约本身无需变更，focused test 全部通过。

一次收口组合命令把测试路径误写为 tests/unit/testDu_p2_contracts.py：JSON 语法检查已完成，但 pytest
因文件不存在退出 1、没有执行测试。随后使用表中正确路径和新的 TEMP basetemp 重跑为 11 passed。

所有 pytest 临时文件和 uv 任务缓存均位于 %TEMP%；没有读取或扫描真实照片库，没有下载模型或数据包，
没有运行真实推理。

## 6. 未验证与后续

P2-01 只证明 ADR、伴随 schema 和实例样例内部一致。尚未验证实际 scheduler/worker 资格解析、
lease 竞争、取消/OOM、artifact/outbox/LanceDB、数据库约束、profile 激活/回退、搜索 session、
CatalogFilterRevision 并发补查或地点实现；这些分别留 P2-02～P2-08。

下一任务为 P2-01A：地点事实、人工控制、数据包与搜索详细契约，仍推荐
**C2 / GPT-5.6 Sol / high**。任务卡是 docs/tasks/P2-01A.md；完成后才生成 P2-02。
