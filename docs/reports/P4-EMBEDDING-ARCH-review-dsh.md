# P4-EMBEDDING-ARCH — 模型切换审查进度记录

日期：2026-10-09。基线：`14e4de2`。状态：**ENGINEERING REVIEW PASS（用户指定的个人项目短审，非DS-H会话）/ SOL_NOT_REQUIRED**。

文件名沿用既有约定`review-dsh`，不表示本报告由DS-H会话产生。用户明确指定“个人项目，在这里做复核短审”，因此本报告按当前会话的个人范围短审收口，不声称DS-H审查。

## 工作区与覆盖范围

开始时HEAD为`14e4de2`；已有用户对本任务卡和开发状态的修改、`.tmp/`及`docs/reports/tmp-*`材料，全部保留。审查按`9ebcbef`实现提交、其后相关搜索门限修订（`8f6370a`、`949ae5f`）、当前代码、实现报告§2/4/5/8及原测试/真实smoke证据重建模型接入面，没有把HEAD之后其它个人相册改动误算入模型改动。

核对面包括`models/embedding`的manifest身份/registry/factory/CLIP兼容/Qwen适配/CLI，`workers/query_encoder.py`和`ai_worker.py`、`runtime/device.py`，语义建库与刷新目标profile绑定，`indexing/selection.py`与`search/profile.py`原子选择，API/search statistics及搜索页只读提示，以及相关provider/runtime/selection/worker测试、ADR-0003 §4–8、ADR-0013 §1/4/7、主架构§11.3.3–11.3.4、V3.1 §2.2/2.4/3.2/3.4/9.2/9.4/11/12。OCR专属CPU兜底、handoff CAS及ORT preload结论复用最新ADR-0018与OCR复核报告；仅检查embedding调用没有继承OCR语义。

| 必要行为 | 核对结果与证据 | 当前状态 |
|---|---|---|
| 历史CLIP身份及同维Qwen隔离 | 保留历史CLIP manifest/spec映射；Qwen space/processor独立。原`p4-embedding-arch.xml`为203 tests、0 failures、0 errors、6 skipped；identity、manifest、参数指纹和错空间零副作用节点均列在实现报告§4.3。 | 证据可复用；6项依赖本机已装CLIP或显式GPU测试开关而跳过，不扩大为通过。 |
| provider/runtime与失败边界 | Qwen用真实PyTorch provider/precision事实，GPU_REQUIRED，加载前核hash；实际设备和参数核验，不静默CPU/offload。CLIP worker的`USE_TORCH=0`及ORT加载顺序复用ADR-0018前已审的隔离结论；OCR自身变化引用最新OCR复核。 | 未发现本次可见的embedding侧回归；不据此宣称本机当前weights/GPU重新验收。 |
| 候选、覆盖门禁和原子选择 | candidate不移动active pointer；CLI要求`--stopped`并拿受管data-root lease；selection核当前可索引图像全集、hash摘要、CONFIRMED覆盖、offline、pending/repair/outbox、artifact可读和Lance/runtime readiness。最终`activate_profile`在同一`BEGIN IMMEDIATE`中复核catalog/entry/outbox，再原子移动profile与generation指针；故障回滚、重连读取、切回拒绝缺口有原集成证据。 | 已增两条合成直接测试覆盖停止声明和受管writer lease拒绝；受管锁不证明任意外部writer已停，CLI显式声明仍是维护前置条件。 |
| W-Q之后的高度匹配统计 | `949ae5f`之后CLIP 1.3、Qwen 1.5、未知空间不可用；统计在候选冻结后生成，不参与候选、排序或分页，已有单测对照。遗留flow节点还要求Qwen隐藏，与已通过搜索组及生产代码冲突。 | 最小修正该测试名称和oracle为Qwen统计可用；保留active provider/generation断言，且断言count在候选数范围内。没有改生产阈值。 |
| UpgradeRun适用性 | ADR-0013 §1将冻结范围定为一次UpgradeRun的持久语义，§7门禁②检查本次run为`SUCCEEDED`；P4个人接入范围另行定义受控停机profile选择，明确不创建run。 | 本次按该范围完成解释：手动停机选择沿用ADR-0003原子激活及本任务完整覆盖/健康门禁；未来run驱动激活仍遵守ADR-0013 §7。未改ADR或迁移，SOL_NOT_REQUIRED。 |

### 本次验证

- 首次执行遗留flow节点，证实旧“隐藏Qwen统计”断言失败；仅按W-Q已批准规则修正测试oracle，没有修改产品逻辑或skip。
- `uv run --no-sync pytest -q tests/integration/test_p4_embedding_provider_flow.py::test_select_profile_requires_stopped_attestation_before_touching_the_data_root tests/integration/test_p4_embedding_provider_flow.py::test_select_profile_refuses_a_managed_writer_lease_before_opening_catalog tests/integration/test_p4_embedding_provider_flow.py::test_the_selected_provider_serves_search_and_reports_the_strong_match_statistic --basetemp=.pytest-tmp/p4-embedding-review-e --junitxml=test-results/p4-embedding-review-e.xml`：**3 passed, 0 failures/errors/skips**。
- `uv run --no-sync ruff check tests/integration/test_p4_embedding_provider_flow.py`：通过；`git diff --check`：通过。
- 原`p4-embedding-arch.xml`核对为203/0/0/6；实现报告记载真实权重/CUDA/HTTP双provider smoke 24/24；JSON的`status=pass`、`passed=true`，23个具名检查均为true。本次未加载模型、未重跑smoke、未重启服务；真实能力继续只引用原代码/环境对应的记录，不当作本次模型或当前现场验收。
- 无全量suite、容量/GPU矩阵、真实图库/用户服务、打包、依赖变更或模型下载。

## ADR适用性短审结论

实现计划明确把本入口定义为受控停机的单profile选择，并把完整UpgradeRun/在线升级留待后续。ADR-0013 §1冻结的是“一次升级运行”的持久语义；其§7 `RUN_SUCCEEDED` 门禁约束该升级运行的激活。ADR-0003 §8则定义通用profile激活：在一个事务内核对profile、processor、runtime、generation readiness并共同移动活动profile与generation指针。

据此，本个人短审将现有`select-profile`解释为ADR-0003控制下的手动停机选择：当它不创建或声称创建UpgradeRun时，不伪造`RUN_SUCCEEDED`，也不要求虚构UpgradeRun记录；它仍须满足ADR-0003原子激活和本实现额外的完整覆盖/健康/停机门禁。未来由UpgradeRun驱动的激活必须满足ADR-0013 §7。本解释没有修改ADR文字或实现新的持久协议，因此本次`SOL_NOT_REQUIRED`。

结论：在用户指定的个人项目短审范围内，工程复核通过；本结论不是DS-H独立复核或真实图库/当前模型/GPU/打包验收。Qwen门限变更后的真实页面确认仍PENDING。未修改冻结契约、迁移、依赖或模型门限，也未执行私人profile切换。未提交/推送，不启动下一功能或全仓修绿任务。
