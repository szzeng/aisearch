# P4-02Y-L-IDENTITY-A — OCR 版本化身份与 GPU 优先、CPU 兜底

日期：2026-10-09。状态：**IMPLEMENTED / ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED**。基线 `14567c2`；实现证据见[实现报告](../reports/P4-02Y-L-IDENTITY-A.md)，独立闭环见[复核报告文末](../reports/P4-02Y-L-review-dsh.md)。独立定向 60/60、Ruff/diff-check 通过；真实 weights/session/GPU 仍 PENDING，不标 OCR 总 PASS。无需为 DS-H 模型名称重复整轮。
推荐 **DeepSeek V4.1 Flash / 高推理（DS-H）**，用户手动选择。上一阶段为
[identity 裁决](../reports/P4-02Y-L-artifact-identity-sol.md)，已完成，OCR 总任务仍 PENDING。

## 用户场景与唯一目标

用户明确选择 GPU 优先、GPU 故障后自动用 CPU 补做，每张图只展示一个当前 OCR 结果。
同时修复 CPU/GPU force 重识别在相同 artifact ID 下发生 checksum 冲突的问题。
按 [ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)实现两个版本化单设备配置，
保留原始 confidence、旧产物读取及严格一致性拒绝；这是一张最小修复卡，不扩展性能/模型功能。

## 读取顺序与直接范围

AGENTS → development-status → 本卡 → ADR-0018、identity 报告与主审报告的高风险发现。
主架构只读 §1.3/§4.1–4.4/§6.2–6.3/§7.2/§8.2；策略 §2.2–2.4/§3.2/§9.4/§11–12；
ADR-0003 §2–6；2026-09-30 冻结报告决定 4/5（身份/兜底新增条款以 ADR-0018 为准）。

直接实现：`src/photo_search/ocr/{contract,engine,processor,service,cli}.py`，
`scheduler/{engine,projection,eligibility}.py` 的 OCR 直接调用，`refresh/pipeline.py` 的 OCR stage，
`artifacts/store.py` 的既有拒绝链与直接 OCR reader。先用实际调用定位 reader，不猜文件名。
允许修改这些 OCR 受影响调用及直接测试/诊断、报告/状态；共享通用代码只做必要 OCR 接入。
不放宽全局 parser/device 矩阵、non_model、artifact 唯一键，不改向量/profile 或其他任务语义。

## 实现要求

1. 发布 `ocr-text-cpu-fp32@2`、`ocr-text-cuda-fp32@2` 与 ADR 指定 spec/policy，有效配置 hash 按
   `ocr-execution-config.v2` 生成。模型/权重/识别阈值/长边/切片/批量不变；payload/ref/job/result/
   projection 一致绑定实际配置。runtime 与兜底原因不进入不可变 payload。
2. OCR 默认 auto 先排 GPU；缺 GPU 资格/能力或明确 GPU 初始化/执行故障才由协调层另排 CPU。
   CPU 健康/资格必须独立验证。显式 gpu 严格失败，显式 cpu 仍可用；worker 不改设备或假报成功。
   故障分类须依据实际阶段及结构化代码，禁止宽泛捕获全部异常转 CPU。现有通用错误不足以区分时，
   只新增 OCR 局部稳定故障码及定向反例，不修改 JobResult 外壳。
3. GPU 失败事实/原因与 CPU 幂等入队在短事务中交接；模型构造/推理在事务外。
   终止该次 GPU 可自动恢复分支并保留故障原因，重启继续已排 CPU，避免 GPU 恢复后反向覆盖。
   同资产/输入的不同 OCR profile 串行协调，live lease 不换配置；pending 竞争分支合法取消。
   利用既有 jobs 状态/字段持久追溯两分支；如必须新增迁移或全局协调协议，记录具体缺口并停止另立卡。
4. GPU/CPU 成功都进入原 scheduler OCR transaction hook：artifact、唯一 current text、regions、
   text_revision 和持久 FTS 待投影标记同事务。失败/取消/冲突保留此前 current；人工备注/人物/地点不动。
5. 同配置 force 复用 job；跨设备 force 是不同 processor 的 job，不能 UPDATE 旧 processor_id。
   同配置同字节重放复用 artifact，不同字节继续 ARTIFACT_CONFLICT，不以兜底或 nonce 绕过。
6. 旧 @1 reader、artifact 与 current projection 保留，不重算历史 checksum、不猜原设备、不自动全库升级。
   legacy pending/live 的处理须显式、安全并在报告留证；新默认写入不用共享 @1。
7. 更新 CLI/library 摘要，使实际设备和 CPU 兜底原因可见；只读展示既有事实，不增加新设置页面。

## 必须覆盖的基本正反例

使用确定性 fake engine 与隔离 synthetic catalog，不加载模型。新增用例不得因无 weights 跳过。

- GPU 成功/成功空文档不排 CPU；GPU 不可用、初始化失败、明确执行故障→CPU 成功。
- GPU→CPU 交接重复/崩溃重启仅一个 CPU job；CPU 失败、取消、过期提交保留旧结果。
- 输入/权重/lease/processor/提交 provider/checksum 错误、未知异常和低质量结果不触发兜底。
- CPU→GPU 与 GPU→CPU force；同配置相同字节 replay；同配置不同字节拒绝，零 current/FTS 写入。
- 有 live lease 时换配置拒绝；旧 pending/迟到任务不能覆盖新选择。
- legacy @1 读取与新配置发布并存；事务提交前故障无半套 current text/regions/投影标记。
- confidence `0.6`/`0.600001` 保持 1/0 的低置信度计数；raw 分数不量化。

若历史 compare JSON 仍在，复用 dense region 13 的真实 score 对照；同时在版本化测试中保留最小合成数据，
不能让 fresh checkout 依赖 ignored 本机文件。真实 dense 的 CPU/GPU confidence 为
`0.7899004022280375` / `0.7836258014043173`，text=`2-4`，box=`[1227,167,1279,210]`。

## 验收与报告

本卡为既有 OCR finding 的局部修复，按 §9.4 执行受影响基本验证，保留 D 级身份/原子性/恢复直接反例。
不是新增全局协议/迁移/运行库或正式发行，不默认全量/30万/GPU测速/打包；一旦越界停止重新立卡。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = (Get-Location).Path
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-identity-a --junitxml=test-results/p4-02y-l-identity-a.xml tests/unit/test_p2_contracts.py tests/unit/test_p2_scheduler.py tests/unit/test_p2_artifacts.py tests/unit/test_p3_ocr_contract.py tests/unit/test_p3_ocr_geometry.py tests/integration/test_p2_index_submission.py tests/integration/test_p3_ocr_cli.py tests/integration/test_p3_ocr_lifecycle.py tests/integration/test_p3_ocr_end_to_end.py tests/integration/test_p3_ocr_migration.py
# 把新增 identity/fallback 专用测试的实际路径加入同一 required 命令；受影响 Python 文件执行 Ruff。
git diff --check
```

记录退出码及 JUnit passed/failed/errors/skipped；新关键正反例须实际执行、零 skip/xfail。
已有真实 weights 节点缺资源仍如实 PENDING，不下载模型、不访问真实图库/用户服务、不提交或推送。
报告：`docs/reports/P4-02Y-L-IDENTITY-A.md`；更新 development-status 和 OCR 主审入口，保留已有并行改动。

## 独立复核与停止点

实现完成后交独立 DS-H 全受影响面工程复核：核对本卡完整新增 diff、入口/reader/资格/force/事务/恢复链与证据。
复用已审共享面；无语义偏离和高风险未决时可按 §9.4 记录 SOL_NOT_REQUIRED，不再机械重复全仓/多轮 Sol。
若发现冻结不符或高风险未决，只交回相关边界的 Sol 短裁决。DeepSeek max 仅按 §2.2 六类实际证据升级；
Windows/ORT 环境问题推荐 C1，不改 ACL/依赖猜测修复。

只完成本卡实现/验证并生成工程复核交接，不自行标 OCR 总 PASS，不启动模型切换主审。
