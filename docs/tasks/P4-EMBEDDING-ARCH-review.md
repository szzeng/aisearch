# P4-EMBEDDING-ARCH — 模型切换个人基础审查

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS（用户指定的个人项目短审，当前GPT-6会话，非DS-H）/ SOL_NOT_REQUIRED**。Qwen阈值变更后的真实页面效果仍PENDING。

当前交接：人物、地点、搜索与查看、OCR四组工程面已完成。最新[OCR复核报告](../reports/P4-02Y-L-review-dsh.md)为ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED，独立定向60/60；真实weights/session/GPU仍PENDING，不是OCR总PASS。此次模型审查开始时HEAD为`14e4de2`；已有`.tmp/`及`docs/reports/tmp-*`材料保留。按各原报告与提交历史重建了模型接入未审面，未把后续无关相册改动归入本任务。

按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)执行。输入：[主卡](P4-EMBEDDING-ARCH.md)、[实现报告](../reports/P4-EMBEDDING-ARCH-implementation.md)、相关计划、ADR-0003/0013与当前Git；只读直接相关空间/device/激活条款。

读取顺序：AGENTS→development-status→本卡→[接入计划](../plans/P4-EMBEDDING-ARCH.md)与原报告→当前代码/证据。主架构只读主卡§2列出的空间/profile/runtime条款；策略§2.2/2.4、§3.2/3.4、§9.2/9.4及§11/12直接相关部分。OCR后续已批准[ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)，以其版本化身份和最新工程复核为准，不沿用历史OCR CPU/GPU共用身份假设；本卡不重做其裁决。


## 本会话短审结果（非DS-H结论）

详见[审查进度记录](../reports/P4-EMBEDDING-ARCH-review-dsh.md)。当前会话核对到`949ae5f`后的W-Q门限已允许Qwen统计，但遗留flow测试oracle仍要求隐藏；已将oracle对齐至Qwen可用，并补两条CLI `--stopped` / 受管lease拒绝的直接合成测试。3项定向pytest、Ruff和diff-check通过。生产选择门禁、profile指针原子性及既有身份/候选证据未发现当前差异。

**本次按用户指示在当前会话完成个人项目短审，结论 ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED，不声称DS-H。** ADR-0013 §7的`RUN_SUCCEEDED`约束UpgradeRun驱动的激活；无UpgradeRun的停机选择复用ADR-0003 §8原子激活及本任务完整性门禁。外部未受管writer不能由OS lease证明已停止，继续依赖`--stopped`维护者声明；真实图库、模型、服务和私人profile均未触碰。

## 完整未审面与必要行为

- registry/spec/factory/adapter：旧CLIP身份稳定，Qwen独立空间；维度/MRL、预处理、artifact/config/generation与本地权重身份不能混用。
- worker/runtime：记录实际GPU/provider/precision；失败保护与ORT/torch导入隔离真实有效，Qwen使用torch不等于全局禁用隔离。
- jobs/artifact/outbox/profile：错encoder/空间零副作用，candidate不直接成为active，日常任务使用目标空间。
- 维护选择：ACTIVE＋PRESENT、当前内容hash/确认、artifact/readiness/outbox/runtime共同门禁；指针失败原子回滚、重启可读、切回有效。
- API/CLI、HEIF和retry-failed后续增量纳入；搜索门限按W-Q后续授权CLIP1.3/Qwen1.5/未知空间不可用，统计不改排序部分复用搜索组，不要求早期“隐藏Qwen”。
- 切换不得破坏人工人物/地点/备注；依赖pin、uv.lock、导入顺序与OCR/Face共存也纳入实际改动面。原报告记录的依赖yank、ORT提示及冻结hash漂移仍需判断其当前影响，不能因简化卡面当作已修复；hash预期由OCR冻结证据决定，不随意重生成。

复用OCR已审的ORT preload/torch隔离和普通终态保护，只补模型调用的实际新增影响。ADR-0018授权的GPU优先/CPU兜底、OCR专用handoff CAS仅属OCR；不得让embedding worker自动降级、普通FAILED/CANCELLED任务复活，或把CPU运行报告为GPU成功。记录CPU/CUDA独立OCR身份的后续变化，不将旧OCR真实实验当作新配置的实测。

维护选择还须核对CLI维护互斥、停机前提与服务启动按持久化指针解析；集成FlowEnv的`activate_candidate`只验证产品selection gate，明确绕过CLI的lease/process处理，不能凭该helper通过就宣称真实停机/互斥已验证。缺少必要证明时用隔离合成目录与模拟writer/故障补最小反例，不停止用户服务或切换私人profile。

首次独立审查查看全部未审新增文件和直接调用链。已有203项（197 passed/6 skipped）、双provider24/24真实smoke、CUDA/受控选择记录先核对代码与环境对应；不重复建库、GPU矩阵、30万或私人根目录操作。

优先证据：`test-results/p4-embedding-arch.xml`、`test-results/p4-embedding-arch-smoke.json`及实现报告§4/5/8。完整新增文件、精确符号与调用面从主卡/报告和实际Git重建，不把历史未提交工作区说明当当前基线。证据无法对应的必要行为继续待验，不强行加载模型补齐。

## 最小补测与协议边界

只有缺失或改变的行为才选择 `tests/unit/test_p4_embedding_provider.py`、`tests/integration/test_p4_embedding_provider_flow.py`、`tests/unit/test_runtime_device.py`的直接节点，补测生成JUnit。fake可证明边界，不能证明实际模型效果。

直接证据定位（不是固定重跑清单）：

- `tests/unit/test_p4_embedding_provider.py::test_the_historical_clip_manifest_keeps_its_exact_identity`、`test_the_qwen_manifest_is_its_own_space_at_the_same_dimension`：旧身份与同维独立空间。
- `tests/unit/test_p4_embedding_provider.py::test_a_build_refuses_an_encoder_of_another_space_with_zero_side_effects`：错空间零副作用。
- `tests/unit/test_p4_embedding_provider.py::test_a_strict_model_cannot_be_softened_into_a_cpu_run`：embedding严格设备边界。
- `tests/integration/test_p4_embedding_provider_flow.py::test_a_candidate_build_never_moves_the_pointer_and_the_selection_does`：候选与显式选择分离。
- `tests/integration/test_p4_embedding_provider_flow.py::test_switching_back_requires_full_coverage_and_rejects_a_gap`：切回当前覆盖门禁。
- `tests/integration/test_p4_embedding_provider_flow.py::test_a_pending_outbox_or_an_offline_asset_blocks_the_selection`：待确认/离线拒绝。

当前`test_the_selected_provider_serves_search_and_hides_the_strong_match_statistic`仍要求Qwen统计隐藏，与W-Q后续授权及已通过搜索组的CLIP1.3/Qwen1.5策略冲突。需要执行该分支时只按批准预期对齐直接oracle，保留活动provider、generation、候选/排序绑定；未知空间仍不可用，不回退产品功能、不skip。搜索组统计/排序已审面引用[其报告](../reports/P4-PERSONAL-REVIEW-SEARCH-A.md)，不重新标定Qwen。

先列必要行为、已有证据、当前差异、是否补测。203项含6skip，不能当全部执行；历史24/24真实smoke仅适用于其对应代码和环境。证据缺失/变更时补无模型加载的合成/mock直接节点，必要真实能力保留待验。补测设置项目`.uv-cache`、必要`PYTHONPATH`及全新`.pytest-tmp`子目录；`uv run --no-sync pytest -q <实际选定节点> --basetemp=.pytest-tmp/p4-embedding-review-<本次标识> --junitxml=test-results/p4-embedding-review-<本次标识>.xml`，即时记录退出码及JUnit。无缺口不跑pytest；不重做CUDA/建库/质量/性能实验。

本次短审已按ADR范围解释停机维护select-profile：不创建UpgradeRun时使用ADR-0003 §8现有原子激活和任务指定覆盖/健康门禁；ADR-0013 §7的RUN_SUCCEEDED仅用于UpgradeRun驱动的升级激活。未来若将此入口改为run驱动，必须遵守ADR-0013；不得伪造run或绕过门禁。

本次短审依用户指示直接收口以上适用性解释，不另立Sol裁决卡。若后续正式实现UpgradeRun协议/迁移，需另立任务并按冻结门禁实施；本组不扩建状态机。

输出 `docs/reports/P4-EMBEDDING-ARCH-review-dsh.md`，按实际会话方式记录而不从文件名推断DS-H。工程审查PASS；ADR-0013适用性按上述既有范围解释，SOL_NOT_REQUIRED；真实能力与用户现场仍分别保留原PENDING。

禁私人图库/服务重启、下载模型、全量/容量/打包、新增服务或提交/推送。只完成本组。

## 执行范围与最后一组收口

默认只读model registry/spec/adapter、worker/runtime、候选build、profile/selection/outbox、直接API/CLI/UI及相关测试/证据；写本组报告、当前卡和development-status。直接缺陷允许在该调用面做最小修复和直接回归，同组会话复核；保留并行编辑。不得更改冻结语义、历史迁移、依赖或模型门限；发现必须扩大时输出精确冲突和最小后续任务，不自行扩展。

报告文件名`review-dsh`沿用旧约定，模型/会话方式按实际记录，不因文件名声称已经使用DS-H。工程、聚焦裁决、真实能力和用户现场事项分别结论。本次个人短审完成后，转日常使用；真实Qwen页面效果与原卡其它现场PENDING继续保留。不默认新增功能、性能、全量基线修绿或发行任务，不将其它原卡PENDING批量关闭。

任务闭环后不自动启动其他功能或审查：

```text
此任务已按本卡闭环：见 docs/reports/P4-EMBEDDING-ARCH-review-dsh.md。后续仅进入个人日常试用，真实权重/GPU、用户现场等PENDING仍由用户决定是否验证；不自动新增功能、性能、全量基线或发行任务，不提交或推送。
```
