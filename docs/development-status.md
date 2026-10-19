# Development status

## Current: P4-PHOTO-COLLECTION-A — 普通照片合集与搜索结果保存

2026-10-19，按用户指令实施并续验。schema 0036、合集 CRUD/成员 API、搜索结果新建/追加保存、照片合集限定搜索与时间轴、导航/详情/viewer 入口及 retention/恢复识别已落地。Luna High 复核修复了排名来源 freshness、combined intent plan、追加 API、地点 scope 最终事务校验、封面物化及准确到期判断；追加事务有持久幂等回执。最终剩余关键验收组 **40/40**（`test-results/P4-PHOTO-COLLECTION-A-remainder-final-r7.xml`）通过，涵盖 FILTER/TEXT/COMBINED、捕获时间范围、source-token 身份/篡改/过期、profile/revision 拒绝、0036 故障回滚/旧版拒绝、备份恢复后成员读回、retention、500/501、创建与追加 5000/5001、追加锁失败同键恢复和封面 EXPLAIN。完整 unit/integration 原始基线 3235 项为 3179 passed、5 failed、51 skipped、0 errors；其中本任务 timeline 资源版本断言已同步更新并通过，另 4 个既有资源版本/digest 失败单独复跑仍在，组合证据不称单次最终全量 PASS。隔离内置浏览器验证导航、空合集创建和详情；搜索结果保存受 `prompt()` 工具限制未完成。UI 刷新/真实进程重启恢复、同 profile 身份下 generation 变化、超大无界稀疏合集容量仍待验；封面 EXPLAIN 仅证明索引查询而无 catalog 全表扫描，不作规模承诺。状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。智能合集前置后端来源/范围/revision/追加接口具备可复用合同；本任务收口，不自动启动下一卡。未访问真实图库。详见[任务卡](tasks/P4-PHOTO-COLLECTION-A.md)与[执行报告](reports/P4-PHOTO-COLLECTION-A.md)。

## Earlier status record: P4-FAVORITES-SEARCH-A — 收藏范围搜索（组合过滤与独立版本保护）

2026-10-19，按用户指定实施收藏范围搜索。服务端以 `asset_favorites` 在 FTS/向量 TopK 前限定，并以独立 favorites revision 保护排名会话、FILTER/地点游标与候选时间浏览；搜索 UI 提供收藏页一次性交接、checkbox/chip 和过期结果清理。受影响组合 160/160 通过；Ruff、py_compile、Node 语法和 diff-check 通过。用户指定 Luna High 子代理首轮 P2 已修复，跟进无新增 finding。内置浏览器覆盖收藏页交接、FILTER_ONLY/文字搜索、viewer 取消收藏后的旧结果失效及预览返回；高级地点筛选折叠区受浏览器交互工具限制未能展开，收藏与人物/地点控件组合闭环仍 PENDING，故状态为 **IMPLEMENTED / ACCEPTANCE PARTIAL**，不标 PASS。SQL 计划仅合成验证，未测 30 万容量/延迟；语义模型未就绪，未访问真实图库。详见[任务卡](tasks/P4-FAVORITES-SEARCH-A.md)与[执行报告](reports/P4-FAVORITES-SEARCH-A.md)。

## Earlier status record: P4-MEDIA-CR2-A — 历史 CR2 人脸/OCR 补建查漏

2026-10-19，按用户要求续做更新后的 [P4-MEDIA-CR2-A](tasks/P4-MEDIA-CR2-A.md)，报告：[执行报告](reports/P4-MEDIA-CR2-A.md)。新增显式 build 限定的一次性旧 `FORMAT_UNSUPPORTED` 人脸/OCR job 恢复、schema 0035 持久 marker、当前 SHA/来源/profile/CR2 头校验、有效发布产物跳过与 OCR active profile 页范围 drain。`DECODE_FAILED` 因缺乏旧故障证据暂保守保留。Luna High 首轮复核指出 payload profile 和 CAS 当前 SHA 两项 P2，均已修复并经跟进复核，无新增 finding；CR2 定向合成测试 2/2 通过。完整 unit/integration 原始运行 3212 项为 3152 passed、9 failed、51 skipped。迁移恢复 fixture 漏列新表，已补后定向复跑 1 passed；其余 8 项含并行 UI/favorites 的失败，原完整运行不记 PASS。Ruff、相关 `py_compile`、本任务涉及文件 diff-check 通过。D4 状态矩阵、内置浏览器、真实模型端到端及独立 DS-H 受影响面审查仍 PENDING；因此不标 `CR2 RECOVERY VERIFIED` / `SCOPED PASS`。未访问真实 `PhotoSearchData`。

2026-10-19 CR2 artifact identity 复核：修复恢复 SQL 将候选 job_id 误作 artifact 逻辑唯一身份的问题。手动/自动 source-detail 可共享同一 `(processor,input SHA,type,revision)` published artifact；当前页批量候选按该逻辑键查找，并由既有复合唯一索引 SEARCH。隔离回归 2/2 通过，真实图库/模型、完整恢复矩阵及独立 DS-H 仍待验；整卡状态仍 IMPLEMENTATION PARTIAL / VALIDATION PENDING，详见[执行报告](reports/P4-MEDIA-CR2-A.md#2026-10-19-artifact-identity-recovery-sql-follow-up)。

2026-10-19 第一阶段继续：修复 face/OCR 目标选择在传入 `asset_ids` 时扫描全库，以及 face job identity 逐资产查询；受影响组合 44 passed、14 skipped（均缺 pinned face/OCR 权重）、0 failed。合成零脸 build 重复执行不增加 executor 调用。独立只读审查指出真实人物 CR2 从显式 build 到实际结果及同一 CR2 第二次零推理仍未验证；本机模型权重缺失，且未获真实 `PhotoSearchData` 授权，缓存/job/profile 实库对照未进行。Ruff、py_compile、diff-check 通过；阶段结果和范围限制见原[任务卡](tasks/P4-MEDIA-CR2-A.md)与[报告](reports/P4-MEDIA-CR2-A.md)。仍为 IMPLEMENTATION PARTIAL / VALIDATION PENDING，不标阶段恢复验证或整卡 PASS。

2026-10-19 第二阶段继续：显式 build 的来源分页按页批量识别 CR2 并补建缺失 `catalog-preview@1`，复用已有 thumbnail/preview 内容寻址缓存；旧 `FORMAT_UNSUPPORTED` 缓存仅对真 CR2 且无产物进行一次重试，失败留下可审计 marker 并跳过后续重复重试。隔离 synthetic CR2 header + 假 decoder 的受影响组合 19/19 通过：首次 build stage 生成两种缓存、第二次零生成/零新增解码，首次 preview GET 无 RAW 解码。未验真实 CR2/LibRaw 解码、完整 build API 链和独立 DS-H；未访问 `PhotoSearchData`。详细报告见[任务卡](tasks/P4-MEDIA-CR2-A.md)及[执行报告](reports/P4-MEDIA-CR2-A.md)。整卡继续 IMPLEMENTATION PARTIAL / VALIDATION PENDING。

2026-10-19 专项性能审查：修复 CR2 thumbnail/preview 缺失时两次完整 RAW decode，当前配对生成冷缓存解码 1 次、暖跑 0 次；缓存输出与既有 renderer 字节相同。历史恢复 SQL 从 profile/state job 扫描改为当前页 subject 索引探测，artifact lookup 走 job_id 索引。隔离 3k asset/15k job 对照中 100×8-ID 查询 230.26ms→4.14ms；synthetic 6000×4000 冷生成 253.62ms→217.03–227.70ms，非容量承诺。受影响组合 52 passed；OCR 权重缺件使重复 drain 测试 skipped。Luna High 首轮发现配对 fallback 的来源越界 P2，已修复并跟进复核关闭。真实 RAW/模型、浏览器和完整恢复面 DS-H 仍待验，整卡仍 IMPLEMENTATION PARTIAL / VALIDATION PENDING。详见[专项执行报告](reports/P4-MEDIA-CR2-A.md#2026-10-19-专项性能审查与最小优化)。

## Earlier status record: P4-FAVORITES-UX-B — 收藏星标、批量收藏与拍摄时间排序

2026-10-19，本任务状态：PASS（受影响面验收），详见[执行报告](reports/P4-FAVORITES-UX-B.md)与[任务卡](tasks/P4-FAVORITES-UX-B.md)。最终受影响组合 JUnit 40/40 通过（0 failures、0 errors、0 skipped）；Ruff、Node 语法、py_compile、diff-check 通过。Luna High 对完整变更面及最新探针独立复核无剩余 finding。隔离内置浏览器实测首页、搜索、收藏排序、viewer 回写、地图、人物照片页和地点相册星标；注入一次性 revision 写故障确认浏览器保留选择，并验证 Escape 优先关闭 viewer、否则退出选择。Node 行为探针确认选择集精确保留 500 项并拒绝第 501 项、DOM 回收后迟到响应不污染缓存、201 个 ID 按 100/100/1 无重无漏分批且全部按钮状态正确。未访问真实图库、未跑全仓/30万性能测试；并行 CR2 保持当前状态。

## Earlier status record: P4-UI2-VIEWER-PAN-AVATAR-FIX — 查看器平移与人物头像修复

2026-10-19，交接前状态：[P4-UI2-VIEWER-PAN-AVATAR-FIX](tasks/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)，报告：[执行报告](reports/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)。状态：IMPLEMENTED / ACCEPTANCE PARTIAL。定向单测最终 28/28 通过；此前完整受影响组合 64/64 通过。最后重跑遇并行工作树新增 migration 0035 与 schema 支持版本 34 不一致，38 个 integration 在 fixture setup 报错；不把该次运行称为产品失败或 PASS。JS 语法、Ruff、diff-check 通过。按用户要求跳过浏览器，V01/V04 实际页面与图像边缘验证仍 PENDING。新增 V08 后确认 pointerdown 立即捕获舞台会重定向浏览器 click/dblclick target；现改为超过 4px 拖动阈值后才捕获，dblclick 仍限定图片目标，并抑制实际拖动形成的双击。原生事件序列模拟定向单测 1/1 通过。按用户要求不跑浏览器，真实 V08 由用户手测，仍 PENDING。前一轮提交为 `2df5fe8`；本轮 V08 修正尚未提交。

上一任务 P4-FAVORITES-CORE-A 的组合证据及原报告保留如下；不改变其既有状态。
## Earlier status record: P4-FAVORITES-CORE-A — 照片收藏核心

2026-10-19，当前卡：[P4-FAVORITES-CORE-A](tasks/P4-FAVORITES-CORE-A.md)，报告：[执行报告](reports/P4-FAVORITES-CORE-A.md)。状态：PASS（组合证据）。已新增 schema 0034、收藏服务/API、共享 viewer 操作及 `/?view=favorites` 收藏网格；收藏状态与 revision 幂等事务更新，游标 revision 失效、retention 保护和备份恢复均有覆盖。完整 unit/integration 执行 3204 项，3151 passed、51 skipped、2 failed；两条人物相册深链 Node harness 用例未改代码单独复跑 2 passed，因此如实标记组合验证，不将原整套运行写作全量 PASS。Ruff、JS 语法、diff-check 通过；内置浏览器隔离合成照片覆盖收藏、详情往返和取消后空态。无截图导出；未访问真实图库，未做收藏规模延迟结论。Luna High 独立复核无阻塞项。

后续任务卡 `P4-FAVORITES-UX-B` 在本记录形成后开始实施；当时的验收进度以其最新报告和上方状态摘要为准。本记录保留 A 完成时的历史状态。

## Previous: 性能设计规范落地

2026-10-18，当前卡为 [PERF-DESIGN-GUIDANCE](tasks/PERF-DESIGN-GUIDANCE.md)，报告见[性能设计规范落地](reports/PERF-DESIGN-GUIDANCE.md)。状态：PASS — 仅文档规范及一致性核对，无产品性能实测。新增[性能规范](performance.md)及[任务模板](tasks/TASK-TEMPLATE.md)，接入 AGENTS、V3.0 §12.5、V3.1 §4.2 与测试指南。个人库默认由执行者补三行：链路与风险、做法与边界、验证与停止；用户直接安排需求，无需手动改任务卡。八项仅按需展开，改查询不自动触发30万，未承诺的容量/发行资格不阻塞局部功能。用户已授权当前目录并修正 AGENTS 旧路径。

验证：9份文档本地链接、28项入口/字段、旧路径、变更范围与diff-check通过，实际exit0；仅文档不运行pytest/浏览器/性能基准。原始命令、JSON和文件身份留在忽略的 `test-results/perf-design-guidance/`。下一产品任务未选；旧卡下次激活时补齐性能段，不自动启动性能专项。用户已授权本次本地提交，不推送、不创建PR。

引用对话最后回答未能读取（仅占位符），本次依据仓库实际优化报告，不声称已逐条核对原建议；补充正文时在同一规范/报告内对照修订。

2026-10-19 补充复核：已修正任务卡生成器旧版 B/D 全量测试规则，接入三行性能模板；高频 SQL 在编码前检查工作量和索引，并补充 `808c4f5` 时间轴、向量及统计/并发经验。仅文档收口，无产品性能新结论。详见同一[报告](reports/PERF-DESIGN-GUIDANCE.md)。

## 产品基线与未关闭边界

当前产品HEAD为 `4690bc8`（PR #7合入），源码仍为已验证的搜索质量与本地产品完善成果；既有实现、组合验证、模型实验、30万局部对照及未验边界详见[搜索质量报告](reports/P4-SEARCH-QUALITY.md)和[原任务卡](tasks/P4-SEARCH-QUALITY.md)。不把历史组合证据称为本轮全仓回归，不追认正式发行或真实图库资格。

[人物整理卡](tasks/P4-PERSON-ORGANIZE-UX-CLEANUP.md)、[合流余项卡](tasks/P4-UPSTREAM-REVIEW-REMAINDER.md)中的既有双页面版本冲突及未单独浏览器检查的合集管理等范围仍PENDING，不因本次规范落地关闭。未访问真实图库，未启动这些任务。
