# P4-EMBEDDING-ARCH — 模型切换个人基础审查

日期：2026-10-09。状态：**独立工程审查待执行；维护选择协议适用性仍有一项聚焦裁决待核对**。工程推荐DS-H/高推理，裁决推荐Sol/high，用户手动选择。

按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)执行。输入：[主卡](P4-EMBEDDING-ARCH.md)、[实现报告](../reports/P4-EMBEDDING-ARCH-implementation.md)、相关计划、ADR-0003/0013与当前Git；只读直接相关空间/device/激活条款。

## 完整未审面与必要行为

- registry/spec/factory/adapter：旧CLIP身份稳定，Qwen独立空间；维度/MRL、预处理、artifact/config/generation与本地权重身份不能混用。
- worker/runtime：记录实际GPU/provider/precision；失败保护与ORT/torch导入隔离真实有效，Qwen使用torch不等于全局禁用隔离。
- jobs/artifact/outbox/profile：错encoder/空间零副作用，candidate不直接成为active，日常任务使用目标空间。
- 维护选择：ACTIVE＋PRESENT、当前内容hash/确认、artifact/readiness/outbox/runtime共同门禁；指针失败原子回滚、重启可读、切回有效。
- API/CLI、HEIF和retry-failed后续增量纳入；搜索门限按W-Q后续授权CLIP1.3/Qwen1.5/未知空间不可用，统计不改排序部分复用搜索组，不要求早期“隐藏Qwen”。
- 切换不得破坏人工人物/地点/备注；依赖pin、uv.lock、导入顺序与OCR/Face共存也纳入实际改动面。原报告记录的依赖yank、ORT提示及冻结hash漂移仍需判断其当前影响，不能因简化卡面当作已修复；hash预期由OCR冻结证据决定，不随意重生成。

首次独立审查查看全部未审新增文件和直接调用链。已有203项（197 passed/6 skipped）、双provider24/24真实smoke、CUDA/受控选择记录先核对代码与环境对应；不重复建库、GPU矩阵、30万或私人根目录操作。

优先证据：`test-results/p4-embedding-arch.xml`、`test-results/p4-embedding-arch-smoke.json`及实现报告§4/5/8。完整新增文件、精确符号与调用面从主卡/报告和实际Git重建，不把历史未提交工作区说明当当前基线。证据无法对应的必要行为继续待验，不强行加载模型补齐。

## 最小补测与唯一未决项

只有缺失或改变的行为才选择 `tests/unit/test_p4_embedding_provider.py`、`tests/integration/test_p4_embedding_provider_flow.py`、`tests/unit/test_runtime_device.py`的直接节点，补测生成JUnit。fake可证明边界，不能证明实际模型效果。

已记录的未决点是：停机维护select-profile无UpgradeRun，ADR-0003维护规则与ADR-0013的RUN_SUCCEEDED激活门禁如何适用。先查后续是否已有正式裁决；有则引用并核对实现。没有则在工程面收敛后保留**一次Sol聚焦短裁决**，只看该边界与反例，不重复全diff/模型测试。不能为通过伪造成功run或绕过门禁。

输出既有约定的 `docs/reports/P4-EMBEDDING-ARCH-review-dsh.md` 与必要时 `docs/reports/P4-EMBEDDING-ARCH-review-sol.md`。工程结果和裁决分别记录；必要安全证据或上述适用性未决则对应PENDING，不能整卡PASS。其余已裁决部分不再次冻结。

禁私人图库/服务重启、下载模型、全量/容量/打包、新增服务或提交/推送。只完成本组。
