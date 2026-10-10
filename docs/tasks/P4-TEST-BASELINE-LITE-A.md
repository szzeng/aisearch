# P4-TEST-BASELINE-LITE-A — 全量测试基线轻量整理

日期：2026-10-10。状态：**READY / 待实施**。立卡基线：`8e7258b`（开工时重核 master、index、worktree）。推荐模型：**GPT-6 Medium**。**个人相册、只修测试、少量定向验证**。

关联：[GC-A 任务卡](P4-STORAGE-GC-A.md)、[GC-A 报告](../reports/P4-STORAGE-GC-A.md)、[原完整基线设计（DEFERRED）](../plans/P4-TEST-BASELINE.md)、[旧地点基线卡（DEFERRED）](P4-TEST-BASELINE-PLACE-A.md)。

## 1. 目标与口径

最新 GC-A 全量 `pytest -q tests/unit tests/integration` 已执行：**2,338 tests / 2,176 passed / 70 failures / 43 errors / 49 skipped**；GC-A 相关定向 **76/0/0/0**。较早人物语义修复报告记录 2,253 项、70 failures、43 errors、49 skipped，但**计数相同不等于 nodeid/失败签名相同**，也不能证明没有新增回归。43 个 setup errors 当前指向地点 ASSIGN 的 `PLACE_NOT_FOUND`。

本卡只做：① 找到并尽量用最小共用 fixture 修复 `PLACE_NOT_FOUND` 的主要级联原因；② 为其余非通过节点按根因建立简明可比较的基线；③ 明确本卡范围和后续功能卡怎样识别新增回归。

**不是**恢复之前的七阶段全量修绿计划；不要求本次把 70 failures 全部改成 0，不强制重跑 2,338 项；不修改任何生产业务代码。不把本卡定向通过宣称为全仓回归 PASS。

## 2. 开工核对与允许范围

先按 `AGENTS.md → docs/development-status.md → 本卡` 核对最新 HEAD、当前 worktree、GC-A 报告和可获得的 JUnit XML：
- `test-results/P4-STORAGE-GC-A-full.xml`（存在于工作区时使用；不默认假定 GitHub 上存在）。
- 早期 `tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-D-with-import-path.xml` 或其他现存基线 XML。
- 已有 `docs/reports/P4-TEST-BASELINE-inventory.json` 可用于参考分类；它是旧时间点清单，不直接作为当前失败事实。

允许编辑：`tests/` 中实际受影响的 fixture、直接测试用例、最少的 test helper；新增本卡报告，简短更新 `docs/development-status.md`。

**不允许**修改 `src/`、schema、migration、依赖、模型、搜索/地点/人物权威规则、历史契约与冻结哈希；不访问真实 `PhotoSearchData`、照片、模型、GPU；不提交/推送其他任务未提交改动。如果发现必须改产品逻辑，留下最小反例并停止该分支，另立产品修复卡。

## 3. A — 优先处理地点 setup errors

重点读：`src/photo_search/places/{choices,assignments}.py` 的当前来源资格要求，以及 `tests/p2_dataset_fixtures.py`、`tests/p4_cn_fixtures.py` 和报错测试的直接 fixture。

旧测试可能直接构造 USER/custom 地点后调用新 ASSIGN；当前入口要求目标属于有效来源目录，因此在待测搜索、批量、撤销逻辑运行前报 `PLACE_NOT_FOUND`。

实施：
1. 在 43 个 error 中选 3～5 个代表节点单独复现，先确认共同根因，不能把所有 error 不经验证全部归为一类。
2. 优先复用现有**离线合成** OSM/GeoNames 小包及正式 stage → validate → activate 流程，从合法目录取得实际可指定的 place_id；不从任意名称/数字伪造合法 ID。
3. 仅替换与被测行为无关的失效输入；保留原测试的资产数、过滤范围、ancestor 语义、revision、幂等、UNDO、备份和预期集合。涉及历史 USER/custom 归属的测试单独准备明确标注的历史数据，不用当前禁止的 API 创建它。
4. 保留至少一条无来源目标返回 `PLACE_NOT_FOUND` 且**零写入**的反例。合法来源 ASSIGN 正常通过。
5. 修改共用 helper 后，跑它的直接调用方定向集合，确认原有绿色用例没有变红。不做全仓字符串替换，不 monkeypatch 掉来源验证。

如果还剩多个独立原因，记录具体节点、签名与原因；能确认的小修则修，涉及产品 bug 的停手另记。

## 4. B — 剩余 failures 简明分类，不强制修绿

解析现存 JUnit（XML 不存在则明确记录缺失，不编造）；按 `nodeid + error/failure signature` 与可用旧 XML 比较。分类：
- 地点来源 fixture 级联；
- 人物/搜索 fixture 结构过期（如 `face_detail_auto_assets`）；
- schema / migration / API 旧断言；
- 前端静态断言；
- 历史契约/冻结哈希；
- 环境/原生运行时；
- 疑似真实产品缺陷；
- UNKNOWN / 证据不足。

在报告中给出各类**数量、代表 nodeid、是否复跑、处置状态**。允许顺手修复少数明确是测试旧断言且有已批准契约证据的情况，必须保持断言力度；不要为了“70→0”跨模块重写测试。实际修改涉及历史兼容、冻结契约或人工知识的预期时停止并另立专项。

禁止通过 `skip`、`xfail`、`deselect`、放宽断言、把测试失败吞成成功、改全局 pytest 配置或绕过来源资格来做绿色结果。

基线对比结论只可为：
- **已证实既有**：有旧 XML 的同一 nodeid + 兼容失败签名；
- **已确认新回归**：新出现或签名实质变化，并能复现；
- **未核实**：缺证据。不能仅因总数恰好相同就全部标为既有失败。

## 5. 最小验证（不强制全量）

**T1 地点代表节点（3～5 项）**：合法来源指定成功、无来源拒绝且零写入、人物＋地点组合搜索、地点批量或撤销、地点相关备份/恢复（按实际存在的节点选择）。

**T2 修改影响面**：运行全部实际修改的测试文件和共用 fixture 的直接调用方；保留原有关键拒绝/恢复反例。先确认 pytest 可以正常收集；必要时采用仓库已有的 `PYTHONPATH=src;tests;.`（Windows PowerShell 用分号），不可改产品代码绕过导入问题。

**T3 最小静态**：对修改的 Python 文件执行 `ruff check`、`python -m py_compile`、`git diff --check`。解析 JUnit 的 tests/failures/errors/skipped 并记录退出码。

全部使用隔离合成 data_root；不访问用户运行服务、不重建真实库、不跑 GPU/OCR/模型、30 万规模、浏览器矩阵或打包；**不强制再跑完整 unit/integration**。若主动运行全量，必须原样报告真实结果，不能宣称通过。

## 6. 验收与后续边界

本卡可以标记 **SCOPED PASS** 的条件：
1. 地点 setup errors 的主要来源已确认，已修的共享夹具及直接影响集通过。
2. 保留合法指定成功和非法来源拒绝的正反例。
3. 其余失败有真实 nodeid/签名级清单，未知明确写 UNKNOWN。
4. 无跳过、降低断言、产品逻辑修改或真实数据访问。
5. 定向命令、退出码、JUnit 和静态检查有证据。

如仍存在未解决失败，报告写清剩余数量和是否复跑，不能把历史全量结果改写成新全量结果。若无法满足上述条件，记 `PARTIAL / BLOCKED`，不强制补丁。

**独立边界**：本卡 SCOPED PASS **不代表**全仓 B/D 门禁通过，不自动解除 P4-STORAGE-GC-A 报告中的 `VALIDATION PENDING` 和独立 DS-H 数据删除安全复核要求，也不自动启动 P4-STORAGE-GC-B；原冻结门禁如需调整，必须独立按项目执行策略明确记录决定，不能静默豁免。

## 7. 交付与停止

生成 `docs/reports/P4-TEST-BASELINE-LITE-A.md`（基线来源、失败分类、修改清单、最小测试、剩余风险、后续门禁）。在 `docs/development-status.md` 更新一段简短状态。保留旧七阶段计划 `DEFERRED`，不自动执行下一卡。不自动提交/推送、不动其他任务变更。
