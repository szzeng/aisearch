# Development status

更新：2026-10-11。当前规则：主架构需求修订 10、执行策略修订 12 / §9.4个人待审去重。较早逐项记录归档于[本次执行前快照](development-status-archive-2026-10-10-pre-P4-STORAGE-GC-A.md)、[2026-10-10 快照](development-status-archive-2026-10-10.md)、[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## P4-STORAGE-UI-A — SCOPED PASS；门闩已按用户确认开放

[任务卡](tasks/P4-STORAGE-UI-A.md)、[执行报告](reports/P4-STORAGE-UI-A.md)。已实现存储诊断 UI、用户点击触发的后台有界扫描、维护预览、可配置备份目录、手动 SQLite catalog 快照及一次性多选维护。用户确认报告通过并授权开闸后，`MAINTENANCE_REVIEWED=True`；启用后 API→启动消费合成闭环及原关闭拒绝路径均通过。P2 缓存版本/逐项实际结果展示已修复；任务定向 JUnit **28/0/0/0**（含 GC-A/GC-B 定向复用和 JS 渲染桩），Ruff、Python 编译/导入、Node 语法、diff-check 通过。状态为 **SCOPED PASS**；不代表真实图库、真实浏览器或全仓 PASS。未访问真实 PhotoSearchData/图库。

后续复核发现并修复两个 P2 UI 交付问题：更新脚本缓存版本，并在批次结果中显示每项实际删除数/释放量。用户确认报告通过并明确授权开放维护门闩；现 `MAINTENANCE_REVIEWED=True`，启用后的合成 API/启动消费闭环定向通过。复核报告：[P4-STORAGE-UI-A-REVIEW](reports/P4-STORAGE-UI-A-REVIEW.md)。真实浏览器、真实图库和全仓回归仍未验证。

## P4-TEST-BASELINE-TAIL-A — SCOPED PASS / 地点测试基线收尾完成

[任务卡](tasks/P4-TEST-BASELINE-TAIL-A.md)、[执行报告](reports/P4-TEST-BASELINE-TAIL-A.md)。修正北京→中国合成层级并用东京→日本保留跨国反例；同名地点两个范围经 `intent_plan` 分别选择后返回独立 GPS 成员；历史 v4 USER/custom 归属升级后可读且退休受保护；当前来源拒绝保持零写入。定向 JUnit **6/0/0/0**，Ruff、py_compile、diff-check 通过。仅改测试；没有产品缺陷扩大项。旧 `needs_place_choice` fallback 无现成直接测试，本卡未宣称覆盖该旧分支。全仓回归未运行，GC-A 的全量数仍是历史结果，OCR 原生运行时 `RUNTIME_UNCONFIRMED`。

## P4-TEST-BASELINE-CONTINUE-A — COMPLETE WITH PARTIALS / 四阶段完成

[总控入口](tasks/P4-TEST-BASELINE-CONTINUE-A.md)；四阶段报告：[LITE-B](reports/P4-TEST-BASELINE-LITE-B.md) **SCOPED PASS**（JUnit 43/0/0/0），[PLACE-RESIDUAL-A](reports/P4-TEST-BASELINE-PLACE-RESIDUAL-A.md) **PARTIAL**（54/0/0/0），[UI-A](reports/P4-TEST-BASELINE-UI-A.md) **SCOPED PASS**（9/0/0/0），[CORE-A](reports/P4-TEST-BASELINE-CORE-A.md) **PARTIAL**（108/0/0/0）。本卡后续报告收口地点同名选择及历史人工归属证据；OCR 原生运行时 `RUNTIME_UNCONFIRMED`，其余历史全量失败未复跑。GC-A 全量历史 2,338 项（70 failures/43 errors/49 skipped）未复跑；LITE-A 的 50 项定向 48/2 仍是历史阶段结果，不推断当前全量余项。全仓门禁没有被标 PASS。

## P4-PERSON-COLLECTION-COUNT-A — PASS / 合集照片数展示

[任务卡](tasks/P4-PERSON-COLLECTION-COUNT-A.md)、[执行报告](reports/P4-PERSON-COLLECTION-COUNT-A.md)。合集列表与照片页显示有效 canonical 成员关联的活动照片总数，按 asset 去重；改名后保留计数。定向 JUnit **10/0/0/0**，Ruff、Node 语法及 diff-check 通过；一次相邻旧 UI 测试在其人物卡初始渲染断言失败，详见报告。未做真实图库/独立浏览器验证；本任务 diff 未提交。

## P4-STORAGE-GC-B — IMPLEMENTED / 任务卡定向验收通过

[任务卡](tasks/P4-STORAGE-GC-B.md)、[执行报告](reports/P4-STORAGE-GC-B.md)。新增停机 LanceDB 活跃图像 generation 诊断/按需标量索引修复/fragment compaction/14 天原生版本清理，以及独立可选 SQLite VACUUM 与 `storage optimize-vectors`、`storage vacuum` CLI。四组 synthetic JUnit 定向测试 **4/0/0/0**；Ruff、`uv lock --check`、Python 编译、CLI help 和 `git diff --check` 通过。合成 fixture 中压缩 3→1 fragments、当前检索等价；版本清理实际释放 0 bytes（保留期内）；VACUUM fixture 实际缩小 9,850,880 bytes。未访问真实 PhotoSearchData/图库/模型/GPU，未跑全量回归。为锁定 LanceDB 0.38.0 的 compaction/cleanup API 启用 `pylance` extra。GC-A 的 GPT-6 Luna/High 路由例外维持原记录，**不是 DS-H PASS**；GC-A 全量历史失败单独保留，不作为 GC-B 验收，也未重跑。

## P4-PERSON-AVATAR-REUSE-A — A+B 定向验收通过（2026-10-11 修复列表渲染回归）；C 后置

[任务卡](tasks/P4-PERSON-AVATAR-REUSE-A.md)、[执行报告](reports/P4-PERSON-AVATAR-REUSE-A.md)。用户指定从 `f140e82` 执行此卡；60 秒内无变化返回复用列表 DOM，人物卡片刷新复用稳定头像 `<img>`，同模式重复点击不重载。2026-10-11 修复真实 DOM `HTMLCollection` 不支持 `.find()` 导致人物卡片渲染中断的问题，并更新脚本版本 URL 以避开旧缓存；Node 测试桩覆盖 DOM 差异并检查版本 URL。任务卡 Node 语法、两份定向 UI pytest（JUnit 2/0/0/0）和 `git diff --check` 通过。真实浏览器图片缓存/用户图库观察未做，C 后置；工作区变更未提交。原 `P4-TEST-BASELINE-LITE-A` 状态和其后续入口保持不变。

## P4-TEST-BASELINE-LITE-A — PARTIAL / 地点 fixture 修复与轻量分类

[任务卡](tasks/P4-TEST-BASELINE-LITE-A.md)、[执行报告](reports/P4-TEST-BASELINE-LITE-A.md)。GC-A 全量历史结果仍为 2,338 项 / 70 failures / 43 errors / 49 skipped，未重跑。地点 ASSIGN fixture 改为使用正式激活的微型合成来源包；搜索权威、人物/地点搜索、备份恢复及来源拒绝零写入共 50 项定向结果为 **48/2/0/0**，两项为冻结哈希与未命名人物列表断言，需后续确认。Ruff、py_compile、diff-check 通过。52 个全量 failure 签名差异尚未规范化；本卡为 **PARTIAL**，不代表全仓通过。此前七阶段完整基线计划仍为 DEFERRED。

## P4-STORAGE-GC-A — IMPLEMENTED / 定向验收通过；GC-B 前置条件按路由例外满足

[任务卡](tasks/P4-STORAGE-GC-A.md)、[执行报告](reports/P4-STORAGE-GC-A.md)。GC-A 相关定向 JUnit 现存结果 **76/0/0/0**，未见直接受影响面新增回归。用户确认独立 GPT-6 Luna / High 复核通过；原 V3.1 路由要求 DS-H 全受影响面主审，本次按用户明确指示记录并接受路由例外。该结论**不是 DS-H PASS**。全量 unit/integration 既有 **2,338 项：2,176 passed、70 failures、43 errors、49 skipped** 原样保留，仍未通过，作为独立历史基线问题跟踪；本次未重跑或修改测试结果。未访问真实 PhotoSearchData、真实图库或用户服务；未改 Schema/依赖。

## 下一步

P4-STORAGE-UI-A 已按用户确认开放门闩并完成合成启用路径验证。后续若用户图库执行维护，需由用户明确操作并先按 UI 提示手动备份；真实图库/浏览器验收仍是独立未验证项。GC-B 已完成状态不变；OCR 原生运行时确认及新的全仓回归仍是独立后续工作。
