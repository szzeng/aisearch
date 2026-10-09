# P4-02Y-L — artifact identity 聚焦裁决报告

- 日期：2026-10-09；基线 HEAD：`14567c2`，index 无已暂存变更。
- 结论：**ARTIFACT IDENTITY RULING COMPLETE / SHARED IDENTITY REJECTED / IMPLEMENTATION REQUIRED**。
- 本次由用户指定的当前 Codex 会话执行聚焦裁决；原卡推荐 GPT-5.6 Sol / high，不声称自动切换模型。
- 正式落点：[ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)。这不是 OCR 产品 PASS。

## 裁决

拒绝继续以当前 `ocr-text@1` 身份接受 CPU/GPU 不同字节。现有 quality tolerance 不足以支持
immutable structured artifact 身份复用。采用版本化 CPU/CUDA processor，保留原始 confidence 和严格 checksum。

用户随后明确授权 **GPU 优先、CPU 自动兜底、每张图一个当前结果**。此策略在 OCR 协调层通过独立任务实现，
不是 GPU worker 内的设备替换。GPU 成功即结束；合格的 GPU 故障才排 CPU，失败不能清空旧结果。
当前结果唯一与历史 artifact 并存可以同时成立，不需要放宽持久层唯一键。

冻结值、配置 hash 落点、兜底范围、严格显式 GPU 请求、force/live lease、历史读取和原子回写要求均见 ADR-0018。
它补充 2026-09-30 [Sol 冻结报告](P4-02Y-L-sol-contract-freeze.md)决定 5 的身份复用前提，
并由本次用户授权限定新增 OCR 协调层策略；其他 processor、non_model、向量空间、全局设备矩阵保持原规则。

## 直接核对与反例

核对 ADR-0003 §2.1/§3/§4.2/§5，现有 OCR contract/engine/processor/service、scheduler eligibility、
OCR 提交 hook、artifact store 与 0003 唯一键；查看 CLI 和 refresh 的入队/运行顺序。
因裁决涉及任务身份、协调和 current projection，同时完成主架构与当前策略的架构对齐，
不重复主审已覆盖的全 diff 或模型实验。重点实现位置为：

- `ocr/processor.py`：当前 artifact/document ID、payload 和 projection 都引用单一常量；不能只改 ID 生成器。
- `ocr/service.py`：force/live 检查只认一个 processor，run_queue 只注册一个 spec；新配置和兜底需共同覆盖。
- `scheduler/engine.py`：OCR persistence hook 按 `OCR_PROCESSOR_ID` 精确匹配；新配置成功必须进入同一事务 hook。
- `scheduler/projection.py`、OCR CLI、`refresh/pipeline.py`：摘要/入队/资格与实际设备须对应新配置。
- `artifacts/store.py`：按 ID 及 logical key 校验 checksum；保留这一拒绝路径。

历史输入 `test-results/p4-02y-l-cuda-compare.json` 本次读取时 SHA-256：
`9e1d4ea32743df15c9e93c530c3e74b3f0d5a379e7e8fa0f54f62efd9635f2c4`。
用其 dense regions 重建同输入 revision `a`×64、location_id=1、original/working_size=1800×1200、scale=1、JPEG payload。

| 观察 | 本次结果 |
|---|---|
| confidence 最大差 | `0.006274600823720222`，低于 `0.03` |
| artifact ID | CPU/GPU 相同 |
| CPU checksum | `13abb27aa556ea9daee01b2d14c7254c8a0da37b32cb1639991948a4f077c34f` |
| GPU checksum | `c490076d5532e103fec5549a3c25276998510be7c2b64cf3f39af9e91cc2f68c` |
| 当前 store，CPU→GPU / GPU→CPU | 两者均 `ARTIFACT_CONFLICT`，每个隔离 catalog 保持一个 artifact 行 |
| 相同完整 payload 再次 persist | 成功复用，仍一个 artifact 行 |
| 设计可实现性探针 | 两个合法单设备 spec、独立 processor/artifact ID，CPU/GPU/CPU/GPU persist 为两个 artifact 行 |

本次重建的固定 payload 参数与主审探针不完全相同，因此 checksum 不沿用主审表；同 ID/不同 checksum 的发现一致。
设计探针仅验证当前 parser 和 store 的键隔离，未实现新配置 hash、OCR validator、force、dispatch、兜底或 projection。
它不能记为新产品验收。

低置信度反例：同一 region 的分数 `0.6` 与 `0.600001` 相差 `0.000001`，但 `<= 0.6` 的计数分别为 1 和 0。
四舍五入到两位会把两者都改为 `0.6`，从而改变第二份结果的标记。即使某组近似值恰好归入同一量化区间，
也不能证明所有容差内输入产生相同字节。因此不批准舍弃/量化 confidence、选第一份近似结果或按 tolerance 接受重放。

## 必须成功与必须拒绝

| 场景 | 冻结预期；产品实现尚待验证 |
|---|---|
| 默认 GPU 成功，包括空文档 | 只提交 GPU 当前结果，不排 CPU |
| 明确 GPU 故障、CPU 合格且成功 | GPU 失败事实保留，另排 CPU，原子发布唯一 CPU 当前结果 |
| CPU→GPU force、GPU→CPU force | 不改旧 job 身份；分配置发布、替换当前指针，历史 artifact 保留 |
| 同配置、相同字节重放 | 复用 artifact；重复终态 result 仍按 lease/状态拒绝二次提交 |
| 兜底交接后崩溃/重启 | 已排 CPU job 可恢复；旧 GPU 不因能力恢复自动回来覆盖它 |
| CPU 也失败、取消或提交故障 | 保留旧当前结果，明确报失败，不循环切回 GPU |
| 同 logical key 不同 checksum | `ARTIFACT_CONFLICT`，零 projection 更新，不兜底 |
| 过期 lease、输入/processor 或提交 provider 不符 | 拒绝持久提交，不兜底 |
| legacy @1 已有结果 | 原字节及 reader 保留；没有隐式全库重算或设备重标 |

## 本次验证

项目内 UV_CACHE_DIR；未更改依赖、ACL 或环境。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv run --no-sync python tmp/p4-02y-l/artifact-identity-sol-20261009/probe.py
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-artifact-sol-20261009 --junitxml=test-results/p4-02y-l-artifact-identity-sol-20261009.xml tests/unit/test_p3_ocr_contract.py::test_a_valid_document_payload_round_trips_and_is_deterministic tests/unit/test_p3_ocr_contract.py::test_low_confidence_regions_are_kept_and_counted tests/unit/test_p3_ocr_contract.py::test_the_artifact_id_and_document_id_are_deterministic
git diff --check
```

探针与 pytest 退出码均 0；JUnit **3 passed / 0 failed / 0 errors / 0 skipped**。
这是当前既有契约的定点证据，不证明新兜底已实现。探针脚本/隔离 catalog 保留在项目 ignored tmp 下；
JSON 为 `test-results/p4-02y-l-artifact-identity-sol-20261009.json`，不作为源代码提交。
文档链接和 diff-check 的最终结果见本报告末尾收口记录。

未加载模型/创建真实 session、未重跑 GPU/速度/全量/打包、未访问真实图库或重启用户服务。
既有主审 11 passed/1 skipped（pinned weights 缺失）与历史 119 passed/19 skipped 保持其原范围，
当前真实 DLL/session 与现场 PENDING 不追认通过。进入时已有诊断/测试/状态/tmp 等改动均保留，未提交或推送。

## 交接

唯一下一任务：[P4-02Y-L-IDENTITY-A](../tasks/P4-02Y-L-IDENTITY-A.md)，推荐 DS-H / 高推理。
完成最小修复后回到 OCR 独立工程复核，只审新增/受影响面；若偏离冻结或有高风险未决，才另做 Sol 短审。
现在不启动模型切换审查，不把 OCR 总任务标 PASS。

收口：7 份相关文档的 50 个本地链接均存在，无行尾空白；development-status 为 51 行。
已重新读取 JUnit 核对 3/0/0/0；`git diff --check` 退出码 0。原工作区产品/测试改动与未跟踪材料保留，index 未暂存。
