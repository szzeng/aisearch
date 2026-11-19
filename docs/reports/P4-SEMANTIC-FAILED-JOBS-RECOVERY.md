# P4-SEMANTIC-FAILED-JOBS-RECOVERY 执行报告

2026-11-19 · 基线 `7186dee` · Windows / Python 3.12.10

## 根因

- v0.6.0 现场提供的候选任务停在 `FAILED / FAILED_JOBS`。处理器历史失败 6975 条，含 6054 条 `SOURCE_FILE_MISSING`、920 条 `RUNTIME_CAPABILITY_UNAVAILABLE` 和 1 条 `RUNTIME_RESOLUTION_MISMATCH`。最后一批没有目标，但候选管理器仍把该处理器全部失败当作当前阻塞。
- 删除来源按原契约移除登记和位置，保留资产、任务、人工记录及向量。旧搜索候选规则允许零位置资产进入结果。来源进度也可能把同资产旧版本的任务算入分母。
- 旧适配器已被替换时，资源代理会返回明确的 `retryable=True`。执行器曾把对应错误写成终态 `FAILED`，丢失调度器退避机会。

## 修复

- 候选建库结束时按目标处理器、当前 SHA-256、已登记的 PRESENT 位置和本代向量确认状态核对任务。当前未确认的失败会保留错误原因并阻止完成，退避或待确认工作显示可继续，旧版本、无 PRESENT 位置和已有当前向量的失败进入历史计数。普通状态页可重读旧任务的分类，快速轮询仍只读运行记录。
- 候选开始或继续时，每批最多检查 100 项、领取当批任务。按资产 ID 向前，持续错误在同一次执行中最多编码一次。写入前复查当前版本、来源与已确认向量，读取源文件前后校验文件大小和修改时间。来源范围中途变化时可继续核对。现有待确认向量事件可由 outbox 继续确认。
- 暂时的适配器错误进入 `RETRY_WAIT`，沿用原调度退避和重试上限。来源同步或补建遇到已经可读的缺文件记录，或消息明确为旧适配器被替换的失败时，复用同一任务重试。真实编码异常和规格错误保留可见性。仍找不到文件的旧失败不会重复领取。
- 来源进度只统计当前版本和当前代的有效资产。搜索在候选与结果复核时要求保有合格的来源位置。仍登记的 MISSING 位置继续遵循现有离线照片筛选。候选完成后仍保留当前活动模型，向量、原图和人工记录沿用原有保留规则。

## 升级后的恢复操作

1. 正常退出旧版。将带本修复的 Windows 版本解压到程序目录，用原来的 `PhotoSearchData` 路径启动。已有数据和索引可以直接续用。
2. 在「图库来源」核对需要管理的目录。要找回误移除的图库时，重新注册原目录并点「开始建库」。仍在管理的目录若修复了文件或重新接上磁盘，先点「同步变化」让目录位置和文件版本更新。
3. 在「搜索模型管理」打开原 Qwen3-VL-Embedding-2B 512D EXIF v2 候选，点「继续建库」。候选从当前缺向量的照片分批补齐，并保留已确认向量。
4. 完成后核对候选结果和当前搜索模型。「历史失效任务」可能继续显示。还有「当前有效照片编码失败」时，按提示检查原文件、模型运行时或向量规格，解决后再继续建库。切换搜索模型仍通过现有的独立检查和切换操作。

## 验证口径

隔离测试使用真实 SQLite、任务、outbox、LanceDB、扫描和来源删除入口，模型输出由确定性替身提供。三类错误共存、历史失效、跨批继续、保留向量/人工收藏/活动模型和来源重加均纳入验证。候选的查询计划使用 PRESENT 索引、任务逻辑键和向量代主键，未筛选搜索计数使用位置索引。本轮未做规模或延迟验收。

从仓库根目录按 [日常验证](../testing.md) 设置隔离环境变量。测试清单、JUnit 和过程日志保存在忽略目录 `test-results/semantic-recovery-*`。最终执行：

```powershell
$affected = Get-Content test-results/semantic-recovery-r4-final-files.txt
.venv/Scripts/python.exe -m pytest -q @affected --junitxml=test-results/semantic-recovery-recheck.xml --basetemp=.pytest-tmp/semantic-recovery-recheck
.venv/Scripts/python.exe -m pytest -q tests/unit/test_p4_02o_semantic_build.py tests/unit/test_p4_embedding_check_ux.py tests/integration/test_p4_embedding_provider_flow.py::test_candidate_recovers_current_failures_across_batches_and_keeps_history --junitxml=test-results/semantic-recovery-incident.xml --basetemp=.pytest-tmp/semantic-recovery-incident
.venv/Scripts/python.exe -m ruff check src tests tools/evaluation/search_quality.py
node --check src/photo_search/web/app.js
git diff --check
```

| 运行 | 通过 | 跳过 | 失败 | 退出 |
|---|---:|---:|---:|---:|
| R1 初次定向 | 128 | 0 | 30 | 1 |
| R2 核心复验 | 57 | 0 | 4 | 1 |
| R3 扩展回归 | 1266 | 1 | 8 | 1 |
| R4 受影响回归 | 450 | 1 | 0 | 0 |
| R5 最终增量首轮 | 68 | 0 | 1 | 1 |
| R6 最终增量复验 | 69 | 0 | 0 | 0 |

R1/R2 的受影响问题由后续回归闭合，包含真实缺文件、历史位点排除、拒绝内容和搜索计数。R3 的 5 项涉及仍按旧规则构造的零位置合成夹具，补齐位置元数据后在 R4 通过，搜索和分页断言保留。其余 3 项是 A2 完整回归已记录的首次建库模型不可用时提前登记任务，原 JUnit 保留。R5 新增故障断言将待确认状态预期为无记录，改为准确的 `PENDING_REPAIR` 后，R6 验证从现有 outbox 确认且只编码一次。R4 和 R6 是受影响回归与最终增量的组合证据。Ruff、18 个变更 Python 文件编译、Node 语法及 diff-check 通过。

## 独立 Sol 审核

按用户指定完成只读审查。初次冒烟 3 项通过。审查发现普通同步可能跳过待确认向量、手动继续可绕过退避、普通流程可重置耗尽预算、同一内容复扫后无法解开 `INPUT_UNSTABLE`，以及并发扫描恢复旧位置时候选可能提前报告完成。修复还补齐了编码结束时的位置核对、暂时退避的待处理统计，以及每组最多 100 个调度任务的边界。

独立 Sol 复审确认发现的问题已关闭，无阻塞项。修复后 9 项定向测试通过，0 失败、0 跳过，退出 0，JUnit 见 `test-results/semantic-recovery-sol-recheck-144740238.xml`。Ruff 与 diff-check 通过。候选用 SQLite 数据版本保守检查并发写入，遇到其它连接的无关写入也可能暂停，继续时复核现有范围。已回收产物沿用现有索引维护入口。

内置浏览器两次连接超时，隔离应用已确认在回环地址启动。实际页面、窄窗口和交互验收待完成。真实 v0.6.0 数据、Qwen 权重、GPU 和正式 Windows 包升级未运行。本次无数据库迁移、依赖更新或发行打包，未操作用户图库。
