# P4-TEST-BASELINE — 全量测试基线修复设计

日期：2026-10-09。状态：**DEFERRED — 用户改为个人相册基础验收**。

用户本轮明确“个人相册，不需要详尽测试，做基础测试”。当前入口回到 [P4-PERSON-ALBUM-PERF-A](../tasks/P4-PERSON-ALBUM-PERF-A.md) 卡首的个人验收范围：直接相关定向测试＋小型合成交互＋静态检查；不先修绿全仓，不自动执行下面七组计划或追加独立审查。原任务既有高风险审查状态保留。下面为此前全量设计及失败诊断的历史记录，只有用户以后明确要求全量基线修复时才恢复执行；历史全量失败不改成PASS。

## 1. 当前证据与目标

起点 HEAD：`65386805f5365ad14a879a626f6b4a2613950c87`。工作区已有人物相册性能实现及交接改动，不能把 HEAD 当作本次测试对应的完整代码快照。执行各卡时须记录 HEAD、index/worktree 差异及实际测试代码版本，保留其他任务修改。

当前证据来自 [人物相册实现报告](../reports/P4-PERSON-ALBUM-PERF-A-implementation.md)、`test-results/p4-person-album-perf-full.xml` 和五项独立复跑 XML。本轮直接解析全量 XML：**2152 total = 2010 passed + 50 failures + 43 errors + 49 skipped**；失败/错误涉及 25 个测试文件。五项代表复跑不能证明其余失败均同根因，更不能仅凭本卡未改这些文件证明所有问题早于本卡存在。

逐例清单见 [失败清单](../reports/P4-TEST-BASELINE-inventory.json)。下面的分组是修复入口，并非已经完成根因验证：

| 修复组 | failures | errors | 必须解决的行为/证据 |
|---|---:|---:|---|
| A 地点来源夹具及包恢复预期 | 36 | 43 | 78 项疑似新人工指定的来源资格/级联失败；另 1 项前驱缺失恢复预期 |
| B schema、历史迁移与人物成员 fixture | 8 | 0 | 3 项缺 detail 表；5 项旧 schema/表集合/迁移终点断言 |
| C 已批准契约扩展的冻结 oracle | 3 | 0 | `p2-contracts.v1.json` 原哈希与 OCR 加性扩展后的哈希冲突 |
| D 搜索高度匹配测试接口与 provider 预期 | 2 | 0 | 旧单门限 monkeypatch；旧 Qwen 不可用断言 |
| E 未知人脸 spec 的候选资格 | 1 | 0 | 旧测试期待未知 spec 仍读旧活动代表，现实现返回空 |
| 合计 | 50 | 43 | 93 个非通过结果逐例有去向 |

ONNX Runtime `0xc0000139` 为报告记载的额外运行时异常，**不是上表任一 SQLite/断言失败的已证实原因**。本轮在现有全量 XML 中未找到该异常的原生调用栈；必须另收集进程、测试节点、stderr 和退出状态，不能猜 DLL 根因。

目标是恢复可靠测试基线：有效输入走真实业务入口，拒绝/零写入/恢复等保护仍有反例，历史协议演进有可审查依据，最终完整 B 级套件执行通过。禁止用删测试、批量 skip/xfail、弱化断言或产品测试特判来修绿。

## 2. 顺序、粒度和模型路由

建议执行顺序为 **A → B → C → D → E → F → G**。先消除覆盖最广的地点 setup 级联问题，再收口小组；F 的原生异常若阻止任何前序定向验证，立即提前诊断。按一个可验收任务一张卡、同卡实现/调试/复测保持同一会话；本次仅生成下一张完整执行卡，后续卡按实际新失败和证据生成，不预设实施结果。

| ID | 独立闭环 | 初始验收 | 推荐模型（依据仓库策略，手动选择） |
|---|---|---|---|
| P4-TEST-BASELINE-PLACE-A | 地点测试输入及恢复 oracle 与已授权行为对齐 | A：定向测试；出现产品状态/恢复改动另立 D 卡 | DS-H / DeepSeek V4.1 Flash / 高推理 |
| P4-TEST-BASELINE-SCHEMA-A | 当前成员夹具完整、历史迁移固定目标、当前升级覆盖 | A：定向测试；真实迁移产品缺陷另立 D 卡 | DS-H / 高推理 |
| P4-TEST-BASELINE-CONTRACT-A | 保留旧冻结快照，并核验已批准 OCR 扩展 | A：完整相关可执行契约集及解析器反例 | DS-H / 高推理；需新冻结决定再交 Sol high |
| P4-TEST-BASELINE-SEARCH-A | 双 provider 门限、统计与检索独立性的 API 回归 | A：相关搜索/provider 定向集；改业务代码则至少 B | DS-H / 高推理 |
| P4-TEST-BASELINE-FACE-A | 明确已知兼容与未知 spec 的资格/人工保护 | A：相关人脸升级/成员定向集；改身份/索引状态则 D | DS-H 全受影响面；新身份裁决或未决问题交 Sol high |
| P4-TEST-BASELINE-RUNTIME-A | 定位 ORT 异常并证明隔离/失败报告可靠 | 先项目内合成诊断；运行库/worker/依赖变更按 C | C1 / GPT-5.6 Terra / medium |
| P4-TEST-BASELINE-CLOSE-A | 所有修复合流后完整 B 门禁及原相册任务收口 | B；继承产品修复触发的 D/C 附加项 | DS-H / 高推理 |

测试夹具/可执行 oracle 变更都需要完整相关验证，不能引用纯说明文档豁免。独立 DS-H 复核完整改动面、预期依据及相关证据；不把夹具修复自动归为展示专项。产品高风险改动独立全受影响面主审；Sol 只审新冻结决定、身份/人工知识风险或未关闭发现，不重复完整 diff。DeepSeek max 只在 V3.1 §2.2 命名条件有实际证据时使用。

各 A 级修复卡可以以本组定向验证和所需审查完成**单卡范围**验收；整个基线与原相册任务仍为 VALIDATION PENDING，直到 G。若发现产品缺陷需要 B/D/C，本卡不扩大修改：先持久化最小反例及产品修复卡，按该卡必需门禁验收；其他未修基线失败仍须明示，不能追认为 PASS。

## 3. 各组修复方法

### A. 地点：先修输入资格，再保持历史行为

已核对 `places/assignments.py` 的新增 ASSIGN 来源门禁、[P4-PLACE-UNIFIED-A](../tasks/P4-PLACE-UNIFIED-A.md) §3.3 和 [ADR-0015](../decisions/ADR-0015-place-package-catalog-recovery.md)。它们要求新指定来自活动且已验证来源目录，历史 USER/custom 归属和撤销保留。旧 P2 测试直接创建 USER 地点再 ASSIGN，因而许多测试在到达真正待测的幂等/批量/检索逻辑之前失败。

1. 复用现有 `tests/p2_dataset_fixtures.py`、`tests/p4_cn_fixtures.py` 和包测试流程（执行时确认其接口），建立最小离线来源包；**正式 stage → validate → activate**，通过目录映射/choices 取得可指定 ID。不 monkeypatch `is_source_backed_place`，不把任意 USER 行伪装成真实来源。
2. 对仅验证 assignment、batch、undo、人物/地点 AND、FTS、分页及备份的测试换成合法来源目标；保持资产数、祖先链、文本、排除、revision 和原命中集合的判据。
3. 将“无包仍可新增任意 custom 指定”的旧场景按已授权新入口行为拆开：新指定拒绝且零写入；历史已有 custom 关系继续可读、可筛选、可取消/恢复/撤销。历史数据应使用**明确标注的旧版本合成 fixture**在当前服务启动前生成，不走当前 API 造出已禁止的新关系，也不由测试直接调用待测查询来生成预期结果。
4. 给 assign/batch/后续编辑的 HTTP 成功前置断言补上 status/body 检查，再读取 `operation_id`。两个 KeyError 暂属级联候选，修复后必须独立复跑；不得只用 `.get()` 吞掉错误信封。
5. `test_place_package_startup_recovery.py` 的 missing-predecessor 用例与 RECOVERY-B 窄例外冲突。改为成功恢复对照：仅空 registry＋唯一完整合法包时 catalog 前驱字段 NULL、包 manifest/字节/mtime 不变；正常导入缺前驱仍拒绝；多包、非空 registry、篡改、缺件、schema 不兼容仍不自动激活；故障回滚和重启幂等保留。
6. 若历史 undo 或合法来源指定本身失败，记录为产品缺陷，另立人工状态/包恢复 D 卡，不通过增加测试专用开关解决。旧 ADR 与当前入口要求存在冲突时，在报告列出明确的历史/新增边界及授权依据；不能仅凭“实现现在如此”改 oracle。

下一卡：[P4-TEST-BASELINE-PLACE-A](../tasks/P4-TEST-BASELINE-PLACE-A.md)。A 预计消除最大级联组，但不承诺 79 项都只有一个根因。

### B. schema/fixture：区分历史数据库与当前查询

三个人物成员用例手写的 SQLite 表不包含当前 `MEMBER_PREDICATE` 经 `detail_policy` 读取的 `face_detail_auto_assets` 等表。优先从真实迁移构建当前查询 fixture，再填最小合成数据；若仍保留手写表，必须枚举直接 SQL 依赖并验证结构，不能只加一张空表消除异常。

用独立预期资产 ID 集合覆盖：旧机器脸未替代、原图 detail 已发布替代、人工锁/排除、OPEN 映射保留、同图多脸去重；证明成员计数与检索 scope 同集。不能把两个都调用同一错误 helper 的结果相等当作充分证明。

五项 schema 失败主要位于 `test_p4_02e_migration.py`、`test_p4_upgrade_contracts.py` 和 `test_p2_scheduler_recovery.py`：

- 历史 0015→0016 测试将迁移目录/支持上限限定到16，继续检查备份、无损、索引和旧程序拒写；恢复 monkeypatch 后另测15→当前支持版本（本轮为21）。不要把所有16机械替换成21。
- 当前迁移链检查从真实目录读取连续编号；历史已发布 migration 字节哈希不改。ADR-0013 中“0017仍空闲”的断言按**当时冻结快照**验证，不能对2026-10-09工作目录继续要求没有0017。
- scheduler v1→当前测试保留旧行/metadata/备份 oracle；当前结构与明确新增表核对。不要把实际表集合赋值给 expected，或者放弃缺表/额外表检查。
- 任何涉及真实升级丢行/回滚失败的发现另立 D 级产品修复卡，不改历史 migration 抹掉失败。

### C. 冻结哈希：演进证据优先于重新计算

本轮 `git log/show` 确认 `c1d0a34` 增加 `ProcessorKind=structured_model` 及对应 spec 分支；[Sol 冻结报告](../reports/P4-02Y-L-sol-contract-freeze.md) 明确批准同版本内的加性扩展。三个旧测试仍钉旧哈希 `ec7de5f0…`，当前文件为 `4270997c…`。这是已知演进与旧 byte oracle 的冲突，不能为哈希转绿撤销 GPU OCR 支持。

修复要同时保留两层证据：

1. 从批准的历史 Git 版本抽取只读快照，校验旧完整哈希；旧冻结事实仍成立。
2. 当前 companion 钉到**经核对、来源固定的已批准版本**；旧三类定义保持原语义，第四类具备完整 model/weights 身份，禁止向量字段/embedding 产物，`non_model` CPU 限制不变。对未经批准扩展有失败反例。

不在测试运行时将当前文件 hash 当 expected，不泛化为“接受任意合法 JSON”。同步各测试对“历史冻结”与“当前可执行契约”的说明；不修改批准后的 companion 字节。若还存在审批之外的 diff，停止 oracle 更新，交契约复核；新契约决定才需 Sol 冻结。

### D. 搜索：改旧注入点，保留排序不变量

本轮确认当前接口为 `strong_match_max_distance(space_id)`，CLIP常量1.3、Qwen常量1.5。后续 [P4-02Y-W-Q](../tasks/P4-02Y-W-Q.md) 和[实现报告](../reports/P4-02Y-W-Q-qwen-strong-match.md)取代 EMBEDDING-ARCH 中“Qwen隐藏”的早期要求。W-Q 卡示例中仍有一处 Qwen→1.0，与其§2/常量/报告冲突：执行时同步纠正该示例，保留历史说明并标出后续任务覆盖的范围。

- 排序等价测试改到当前空间策略/常量注入；门限两侧候选 ID/距离/融合顺序/分页/count/scan_truncated 完全一致，只有统计字段变化。
- provider flow 测 Qwen `strong_match_available=true` 和真实统计预期；未知空间依旧 false，UI 隐藏而非展示假0。
- 边界用固定距离：CLIP1.29/1.30/1.300001、Qwen1.49/1.50/1.500001，并保留Top5000/第5000条 more_possible 规则。
- 不改门限、RRF、模型空间或Top-K来迁就旧测试；不用真实模型/真实图库做此基线修复。

### E. 人脸：未知版本绝不默认兼容

`test_p4_person_face_upgrade.py` 最后一条期待 `future-unknown` 候选包含旧人物，当前 coordinator 返回空。ADR-0014只允许明确v1/v2例外，ADR-0017再定义原图 detail 的独立身份和验证桥。本轮倾向这是旧预期需要收紧，但还需审查完整 active_generation/candidates 调用链与 DETAIL-B 测试，不直接把 expected 改为空就结束。

分开验证：已知v1/v2兼容、经过验证的detail兼容、未验证detail、未知spec、不同processor/space/dimension/metric/normalization；PENDING/部分世代不激活。未知spec零候选且不写人工关系，旧已知coordinator仍能检索有效旧人物；旧name/手工归属/锁/排除/OPEN映射不损失。若由此发现产物/preprocess身份缺陷，另立高风险任务，不降级兼容门禁。

### F. ORT：先证明异常发生在哪个进程

先用项目内最小合成 ONNX 模型和独立子进程诊断，保存命令、Python/ORT/torch/CUDA包版本、相关模块是否加载、原生stderr和十进制/十六进制退出码。分“单独节点”与“全量中导入顺序”核对，不运行私人权重或全库建库。

- 普通 mocked unit test 如果意外调用真实 preload/native session，修正其 mock 边界；真实运行时探测保留独立、明确的子进程测试并检查返回码/完整结果，不捕获异常后当PASS。
- 复核当前 ONNX worker 的 torch 隔离及 Qwen worker 路径。不能全局 `USE_TORCH=0`，否则会破坏 Qwen；不能用 `CUDAExecutionProvider` 列表代替成功创建/执行session证据。
- 有日志后才判断DLL/ABI/包冲突。依赖变化须更新uv.lock；worker/运行库/依赖变更按C级实际构建及包smoke验收。无法完成必需环境验证就记录BLOCKED/PENDING，不能静默退CPU或全组skip。
- 未改变产品运行环境时，普通夹具隔离修复无需自动打包；不得为尚未确定的原因开启驱动/ACL修改或下载大模型。

### G. 合流：完整执行后才宣布转绿

各组关闭后，再跑一次当前合流代码的完整B级套件；检查退出码、XML存在及计数一致、原生异常日志、意外收集减少和skip变化。原2152/49不是永久固定的总数/跳过数，但新增/删除/skip变动必须逐项解释；93个原失败节点不能失踪。

PowerShell环境示例（每张卡用自己的全新basetemp子目录，不手工删除其他会话临时文件）：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = (Get-Location).Path
uv run --no-sync python -c "import sys; print(sys.version); assert sys.version_info[:3] == (3,12,10)"
uv lock --check
uv run --no-sync pytest -q tests/unit tests/integration --basetemp=.pytest-tmp/p4-test-baseline-close --junitxml=test-results/p4-test-baseline-close.xml
# 每条命令立即记录 $LASTEXITCODE；不能仅从终端是否有输出判断成功。
uv run --no-sync ruff check src tests tools
git diff --check
```

Ruff若有独立既有失败也要单列归属，不能自动全仓格式化。PYTHONPATH为此次环境使用仓库根的明确约束；若希望消除该环境变量，另修测试启动配置并重新验证，不在规划阶段改配置。

随后回到 `P4-PERSON-ALBUM-PERF-A`：在同一合流版本复跑相册定向集/Node检查，核对必需审查及报告。已有真实首屏头像/首张照片可见、媒体cold/hot时间仍为NOT MEASURED；全量转绿不证明真实体感、GPU、30万性能或发行PASS。卡面“无需全量”与当前报告“B门禁未过”的口径差异需收口时注明采用的当前门禁，本方案明确按用户要修全量的目标执行B，不悄悄删除门禁。

## 4. 交付、授权和停止点

仅项目目录内合成数据；不访问 `D:\SZ\project\PhotoSearchData`、私人照片、真实模型，不下载模型，不重扫描/重建库、不改ACL、不提交或推送。每卡保存 scoped diff、命令/退出码/JUnit、93节点去向、未关闭项、独立审查结论和下一卡。最终报告写 `docs/reports/P4-TEST-BASELINE-CLOSE-A.md`，开发状态只保留简短结论与报告链接。

本设计不把这些问题提前裁成全部“测试过期”，也不要求重做地点/人物/搜索系统。能用合法fixture和已批准演进闭环的先做；发现产品缺陷就携带最小反例进入对应高风险任务。
