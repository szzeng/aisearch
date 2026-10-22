## Current task: P4-RELEASE-060 — 0.6.0 版本标记更新（2026-10-22）

应用、API/OpenAPI、包、uv.lock 本地项目项、直接版本断言、README 与 CHANGELOG 已更新为 0.6.0；Catalog Schema 43、API v1 保持不变。GitHub master/tag 已推送；版本更新未新增验证（用户明确要求跳过）。GitHub Release 正在按 Personal Beta prerelease 更新。详见[任务卡](tasks/P4-RELEASE-060.md)与[更新记录](reports/P4-RELEASE-060.md)。

## Current task: P4-OCR-EVERYDAY-QUALITY (2026-10-22)

OCR @4 uses RapidOCR text_score 0.5 with existing PP-OCRv4 Mobile weights; @3 config stays frozen at 0.0. Active CPU/GPU profiles have separate hashes. Historical OCR remains readable, v4 enqueue is idempotent, offline sources are skipped without removing text, and progress counts actual active-version task states. Affected OCR JUnit 183 passed / 19 skipped / 0 failed; Ruff, py_compile, diff-check and synthetic CPU model smoke passed. Actual read-only weights check verified all pinned files. Real-library processing and GPU smoke were not run. Luna H follow-up review PASS; the default-hash finding was fixed. Locally committed on user request; not pushed. See [task card](tasks/P4-OCR-EVERYDAY-QUALITY.md) and [report](reports/P4-OCR-EVERYDAY-QUALITY.md).

## Current task: P4-UI2-SHELL-COLLECTION-NAV-A（2026-10-21）

修复人物、地点、搜索、地图等独立页面侧栏漏掉“照片合集”入口的问题；五个 shell 页面现共用同一 `/?view=photo-collections` 入口。定向 JUnit **4/4** 通过；隔离内置浏览器及用户实测均确认人物和地点页可进入合集目录。Luna H 有界独立复核 **PASS**。状态 **IMPLEMENTED / SCOPED PASS**。未访问真实图库，未提交、未推送。详见[任务卡](tasks/P4-UI2-SHELL-COLLECTION-NAV-A.md)与[报告](reports/P4-UI2-SHELL-COLLECTION-NAV-A.md)。

## Current task: P4-PHOTO-SMART-STRONG-MATCH（2026-10-21）

COMBINED 智能合集按同一搜索 Session 的向量距离与绑定阈值收录，并保留融合排名顺序；schema 0043 为缓存增加策略身份，旧策略结果在刷新完成前不浏览、不另存。打开合集目录错误已修复：显式 SQL 投影补入 `cache_policy_identity`，缺列旧投影安全进入重建状态；跟进定向 JUnit **6/6**、Ruff、py_compile、diff-check 通过，重启本地应用加载修复。原任务受影响组合 JUnit **256/256**，合成浏览器完成关键流程，Luna H 跟进复核 PASS、无 blocker 或缓存泄露风险。完整 unit/integration 初始 3493 项有 30 失败/52 跳过，复验相关模块后剩余一项工作树既有的 Qwen 页面资源版本断言不一致；不称单次全量通过。状态 **IMPLEMENTED / SCOPED PASS**。未访问真实图库或模型/GPU，未提交、未推送。详见[任务卡](tasks/P4-PHOTO-SMART-STRONG-MATCH.md)与[报告](reports/P4-PHOTO-SMART-STRONG-MATCH.md)。
## Current task: P4-QWEN-1024D-TRIAL（2026-10-21）

新增独立 Qwen 1024D EXIF v2 Provider，共享标准 512D 权重；Registry 拒绝同 ID 本地清单的 revision/hash/dimension/config 偏差，只允许自定义展示名。默认仍为 Qwen 512D；按用户要求将 1024D 高度匹配计数恢复为初始阈值 squared-L2 ≤1.50（归一化向量 cosine ≥0.25），仅计已召回候选，不改变排序/召回。回退定向 JUnit 59/59 通过，Ruff 与 diff-check 通过；该值尚未用真实图库相关性标签单独校准。真实图库建库/切换仍未验证，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。详见[任务卡](tasks/P4-QWEN-1024D-TRIAL.md)与[报告](reports/P4-QWEN-1024D-TRIAL.md)。

## Current task: P4-WEMM-QUALITY-PILOT（2026-10-20）

A 阶段合成 BF16 GPU smoke 已通过：固定 revision WeMM 在 RTX 4060 Laptop `cuda:0` 全模型驻留；中文与严格 image-only 合成 JPEG 均产生有限2048D输出，并成功派生512D归一化前缀，重复输出一致。R3 首次图像调用峰值约5.49GB allocated / 5.61GB reserved；该合成输入不代表质量或真实图库性能。任务已按用户指示暂停：A 合成 BF16 GPU smoke 已完成；B 真实图片对照未开始，C 未启动。只读 preflight 合成安全测试8/8、Ruff与此前diff-check通过；GPU smoke R3 exit 0。未运行真实图片、Qwen对照、排名或HTML报告，未启动Provider。详见[报告](reports/P4-WEMM-QUALITY-PILOT.md)与[任务卡](tasks/P4-WEMM-QUALITY-PILOT.md)。
## Current task: P4-RELEASE-050 — 0.5.0 个人源码测试版（2026-10-20）

**已发布 [0.5.0 个人源码测试版](https://github.com/szzeng/aisearch/releases/tag/0.5.0)**（GitHub Pre-release），Tag 精确指向 `a669340c89df63d136a827f61f7b6ac9f6bc9556`；版本0.5.0 / Schema42 / API v1。当前 Schema 备份恢复、人工事实、清理保护/重试和关键合集浏览器流程通过，Luna High 完整独审无代码 blocker；修复旧地点包在40–42误拒绝。原始全仓3330通过/76失败/52跳过，增量复测关闭75项失败，剩余范围外30万场景未重跑，不称单次全仓全绿。确认取消的工具限制、容量/最低设备/安装包等仍未认证。未访问真实图库；后续进入个人试用反馈。详见[任务卡](tasks/P4-RELEASE-050.md)与[发布报告](reports/P4-RELEASE-050.md)。
## Current task: P4-PHOTO-OCR-DISPLAY-A（2026-10-20）

共享照片查看弹窗与完整详情已接入同一 OCR 原文区；新增同源只读 API，短 SQL 快照/复核与最多 2 路独立文件读取，Artifact 元数据先拒绝超过 16 MiB，正文最多返回 128 KiB。定向 API/UI JUnit **13/13** 通过；Ruff、py_compile、Node 语法及 diff-check 通过。隔离合成浏览器验证首页查看器与完整详情显示保存的历史 OCR 文本。Luna High 首轮发现的 Artifact 读取上限已修复并经跟进复核关闭。任务卡其余失效元数据矩阵、真实读取期间指针竞态、并发池隔离实测、长文本布局与完整回归闭环尚未全覆盖，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实图库或运行 OCR/GPU，未提交、未推送。详见[任务卡](tasks/P4-PHOTO-OCR-DISPLAY-A.md)与[执行报告](reports/P4-PHOTO-OCR-DISPLAY-A.md)。

## Current task: P4-SEARCH-PLACE-LEXICON-FAST-PATH（2026-10-20）

文本地点查询先在 SQLite 按活动 GeoNames 包预筛主名/别名，再构造命中地点；完整目录和来源身份/回退语义保持一致。四种合成命中形状（含完整 45,217 ID 有效 INDEX）与 814ffa1 基线结果签名一致，Python 返回行数 123,255→6,848–7,048；热 P50 约 83–88ms（五样本本机微基准）。定向 JUnit 86/86、Ruff、py_compile、diff-check 通过；Luna High 跟进无代码 blocker。SQLite 子串查询仍扫描活动包范围，诊断额外开销约 11–18ms；真实 EXIF v2 1+2+20 复测 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实图库，未提交、未推送。详见[任务卡](tasks/P4-SEARCH-PLACE-LEXICON-FAST-PATH.md)与[报告](reports/P4-SEARCH-PLACE-LEXICON-FAST-PATH.md)。

## Current task: P4-PHOTO-SMART-COLLECTION-UX-FIX（2026-10-20）

智能合集目录/详情 UI、缩略图、分页、异步身份和搜索关键词命名默认值已实现。最终补修清除标签/排序切换时不匹配的卡片并释放旧请求锁；同视图、同排序、同快照首屏失败保留原卡片及操作，已知新快照加载失败清空旧卡并提供重试。刷新状态 GET 增加 8 秒局部超时/AbortController，超时计入 60 次预算；退出、超限或终态释放观察器及按钮，不将未知状态误报为成功。最终增量定向 JUnit 6/6、Ruff、`node --check src/photo_search/web/app.js`、`git diff --check` 通过；内置隔离浏览器验证 FILTER 结果/排除切换和照片列表更新。Luna High 最终有界复核无 blocker/non-blocker finding。CR2/离线缩略图及真实 TEXT/COMBINED 浏览器刷新流程仍待验，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。只用合成数据，未访问真实图库；未运行全仓、模型/GPU 或大图库认证；已按用户要求本地提交，不推送。详见[任务卡](tasks/P4-PHOTO-SMART-COLLECTION-UX-FIX.md)与[执行报告](reports/P4-PHOTO-SMART-COLLECTION-UX-FIX.md)。

## Current task: P4-SEARCH-PLACE-CHOICES-PERF-FIX（2026-10-20）

地点关联查找由重复线性扫描改为保留首条记录的 `place_id` 映射；文本查询改为用已存正规化 GeoNames 名称/别名先筛，再构造命中候选，`text=None` 完整目录保持不变。新增 opt-in 地点子阶段计时和对象/正规化计数已接入既有诊断 JSON 与 preparation 明细。合成 3,013 OSM、45,217 GeoNames、274 links 验证完整候选及无命中结果一致；无命中构造 0 个 GeoNames 对象。FIX-1 单样本执行 1,159.912→854.046 ms；FIX-2 单样本端到端没有证明改善，SQL 保持 126 次且未进一步改写。定向 JUnit 80/80、Ruff、py_compile、diff-check 通过；7 项地点 integration 在旧 manifest schema 兼容检查阶段受阻。Luna High 跟进复核无新 blocker。真实图库 1+2+20 及性能显著性仍 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**；未访问真实图库，未提交、未推送。详见[任务卡](tasks/P4-SEARCH-PLACE-CHOICES-PERF-FIX.md)和[执行报告](reports/P4-SEARCH-PLACE-CHOICES-PERF-FIX.md)。

## Current task: P4-SEARCH-PIPELINE-PERF-A（2026-10-20）

搜索请求新增 opt-in 阶段树、Worker/SQLite/地点候选统计和未拆分耗时；地点别名及 OSM 映射查询根据合成 400+5000 候选基线做 400 项分批，查询数 5,404→18，候选构造中位数 529.861→24.265ms。受影响 JUnit 127 passed、1 skipped，Ruff、py_compile、Node 语法、diff-check 通过。合成阶段树烟测通过；真实图库原始阶段 JSON 未取得，无法归因真实 1,396ms 或宣称实机提速，需用户复测 1+2+20；DS-H 独立受影响面审查 PENDING，故状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实图库；本地提交不推送。详见[任务卡](tasks/P4-SEARCH-PIPELINE-PERF-A.md)与[报告](reports/P4-SEARCH-PIPELINE-PERF-A.md)。
## Current task: P4-SEARCH-PERF-UX-FIX2（2026-10-20）

性能诊断现通过新增只读身份端点查询活动 Profile 明确绑定的 Generation；诊断开关开启后异步确认，开始测速前再次核对，测试不触发 query encoder/capabilities 模型解析。用户搜索条件与服务端语义状态分离，编码/LanceDB 统计改为读取 `sample.server` 并保留有效样本数；跨轮样本不补数。定向诊断/UI JUnit 21 passed，相关认证断言 1 passed，Ruff、py_compile、Node 语法和 diff-check 通过。Luna H 发现的跨轮摘要/导出混样问题已修复并经跟进复核关闭；真实模型 23 轮与图库图片就绪数据 PENDING。状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。详见[任务卡](tasks/P4-SEARCH-PERF-UX-FIX.md)与[FIX2 报告](reports/P4-SEARCH-PERF-UX-FIX2.md)。

## Current task: P4-SEARCH-STATE-UX-FIX（2026-10-20）

搜索状态与结果操作区已完成：首次成功响应的保存 token、规则身份、模型/生成绑定、高度匹配统计及候选信息独立保留；分页和时间排序不再覆盖确认元数据。按当前模式生成相关度/时间状态栏和分页入口，连续时间页失败保留真实重试 cursor。智能合集独立于照片多选工具栏；每次异步保存 POST 前核验原搜索身份与条件指纹，token TTL、应用 Session 及模型绑定由服务端裁决。P1 阻断续修覆盖快照筛选搜索凭据恢复、智能依赖版本失效及搜索 Session 失效后的时间/相关度游标清理。前端 JUnit **100/100**（`test-results/p4-search-state-p1-fix-final4.xml`），保存 API 合约 **7/7**（`test-results/p4-search-state-p1-api-final2.xml`），Node、Ruff 与 diff-check 通过。Luna High 跟进确认前 4 项 P2 已关闭，并确认新增游标 P2 关闭。真实浏览器检查 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**；未访问真实图库、未做 GPU/大规模测试；按用户后续授权本地提交，不推送。详见[任务卡](tasks/P4-SEARCH-STATE-UX-FIX.md)与[报告](reports/P4-SEARCH-STATE-UX-FIX.md)。

## Previous task: P4-SEARCH-PERF-UX-FIX（2026-10-20）

搜索页新增当前标签页默认关闭的性能诊断入口；诊断请求不再依赖环境变量。实现串行 1+2+20 标准测试、请求 ID 图片回调隔离、活动 Profile/Generation 核对、第一页实际照片数、有限数值百分位和正确耗时命名。标准测试只在本轮 20 个有效 hot 样本时报告完成；跨轮样本不能补数。定向诊断/文字搜索 UI JUnit 13 passed，相关 API 前端选集修正断言后 8 passed；Ruff、py_compile、Node 语法与 diff-check 通过。隔离内置浏览器确认默认关闭、即时显示和无模型时阻止测速。Luna High 复核两项 P2 均已关闭。真实模型 23 轮、重复运行浏览器路径、图片错误矩阵及图库实测 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。初始前端失败是将既有可选参数函数签名写死为无参数的断言，已调整匹配并复跑通过。详见[任务卡](tasks/P4-SEARCH-PERF-UX-FIX.md)与[报告](reports/P4-SEARCH-PERF-UX-FIX.md)。
## Previous task: P4-LIBRARY-RETENTION-C2-FINAL-FIX（2026-10-20）

父目标 `P4-LIBRARY-RETENTION-C2` 按 C2-A 至 C2-F 分阶段执行，整体仍 **IMPLEMENTED / ACCEPTANCE PARTIAL**。C2-A 完成可信失效观察、30 天来源复核与批量异常 hold（受影响 JUnit 119/119、隔离内置浏览器和 Luna High 复核通过）；C2-B 完成 schema 39 持久屏障及 job、outbox、FTS、候选发布拦截（最终受影响 JUnit 241/241、Luna High 复核无 blocker）；C2-C 完成 DELETE_VECTOR 原子生产 helper、真实 Lance 多 space/generation 删除和崩溃重放（受影响 JUnit 56/56、Lance JUnit 2/2、Luna High 复核无 blocker）；C2-D 完成单资产清理资格复查、向量回执屏障、机器派生 SQLite 清理及恢复重试（受影响 JUnit 124 项、0 failed/errors、16 skipped，Luna H 复核无 blocker）。C2-E 已实现 schema 40 身份留存、停机受管缓存和 Artifact/Job 回收 CLI；最终受影响 JUnit 170 项、0 failed/errors、16 skipped，Luna H 跟进关闭未知 job 引用 blocker。媒体 unlink 与数据库记账间的崩溃窗口可能使累计字节保守低估，故 E 为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。C2-F 实现 schema 41 持久清理批次、默认关闭的自动调度、手动确认与进度 UI、逐资产复核和重启续做；其浏览器交互由后续 FINAL-FIX 卡完成。FINAL-FIX 修正耗尽尝试崩溃恢复和 runner 退出竞态，受影响 JUnit 42/42，Ruff、py_compile、Node 语法及 diff-check 通过，Luna H 复核无 blocker；隔离浏览器完成取消、跨批次确认、刷新/离页返回及失败后重试。详见[C2-A 报告](reports/P4-LIBRARY-RETENTION-C2-A.md)、[C2-B 报告](reports/P4-LIBRARY-RETENTION-C2-B.md)、[C2-C 报告](reports/P4-LIBRARY-RETENTION-C2-C.md)、[C2-D 报告](reports/P4-LIBRARY-RETENTION-C2-D.md)、[C2-E 报告](reports/P4-LIBRARY-RETENTION-C2-E.md)、[C2-F 报告](reports/P4-LIBRARY-RETENTION-C2-F.md)、[FINAL-FIX 报告](reports/P4-LIBRARY-RETENTION-C2-FINAL-FIX.md)；当前卡为[C2-FINAL-FIX](tasks/P4-LIBRARY-RETENTION-C2-FINAL-FIX.md)。全量 unit/integration 最新 C2-B 基线 3372 项中 31 failed、52 skipped；未将其标为全量 PASS。

## Previous task: P4-EMBEDDING-OFFLINE-SELECTION-FIX（2026-10-20）

修正离线历史资产阻止语义模型切换的问题：只要求在线可索引照片全覆盖，离线资产保留为独立诊断及风险提示；在线缺向量、PRESENT 文件不可读及原有 generation/outbox/repair/artifact/Lance 门禁仍拒绝。检查与停机 CLI 复核共享选择快照，恢复图片并重扫后仍拒绝不完整候选。API 同时核对 profile 与 provider generation 的 space/processor 绑定，错配时 `switch_eligible=false`，安排返回 `PROFILE_GENERATION_MISMATCH` 且不写 pending 请求。定向 JUnit **30 项（29 passed、0 failed、0 errors、1 skipped）**；Ruff、Python 编译、Node 语法、diff-check 通过。Luna High 跟进复核两项 finding 已关闭、无新增。内置浏览器中显式点击检查/确认安排仍 PENDING，故任务状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实图库、未加载模型、未执行真实停机切换或大库测试。详见[任务卡](tasks/P4-EMBEDDING-OFFLINE-SELECTION-FIX.md)与[执行报告](reports/P4-EMBEDDING-OFFLINE-SELECTION-FIX.md)。

## Previous task: P4-EMBEDDING-CHECK-UX-FIX（2026-10-20）

搜索模型「检查」现在在卡片内即时显示 busy/通过/阻塞/失败结果；只使用显式检查响应和匹配的 provider/profile/generation 身份决定切换按钮。任务/身份变化会使结论失效，在途请求保留 busy 锁后丢弃过期响应。受影响 pytest 12 passed、1 skipped（symlink WinError 1314），Node 语法、diff-check 通过。隔离内置浏览器真实验证 `NO_GENERATION` 提示与无切换按钮；Luna High 跟进复核无遗留 finding。未加载模型、未访问真实图库、未验真实索引或重启切换。状态 **PASS（限定 UX 范围）**；已本地提交，未推送。详见[任务卡](tasks/P4-EMBEDDING-CHECK-UX-FIX.md)与[报告](reports/P4-EMBEDDING-CHECK-UX-FIX.md)。

## Previous task: P4-EMBEDDING-DEFAULT-QWEN-A（2026-10-20）

新图库在没有活动 SearchProfile 时默认 EXIF v2 `qwen3-vl-embedding-2b-exif-v2`；既有活动 Profile 继续权威，普通版与 EXIF v2 共用 `models/qwen3_vl_embedding` 权重目录。普通首次建库在分类、入队及 source-built 标记前拒绝孤儿 Lance 表，并在同一阶段确认模型可用；缺权重/CUDA/encoder 时明确失败且无状态写入；补齐资源门禁包装器的单图编码接口。原定向组合 58 passed、Qwen/严格 CUDA 单测 1 passed；EXIF v2 默认选择/wiring 定向测试 4 passed。Luna High 指出的孤儿/模型预检顺序问题均已修复并确认闭合。真实 Qwen/CUDA 建库与真实 PhotoSearchData 均未触及；未做大图库测试。原实现提交 `176d189`，EXIF v2 默认调整现位于 HEAD 的提交 `18631f7`。详见[任务卡](tasks/P4-EMBEDDING-DEFAULT-QWEN-A.md)和[执行报告](reports/P4-EMBEDDING-DEFAULT-QWEN-A.md)。

## Previous task: P4-EMBEDDING-MANAGEMENT-UI-FIX（2026-10-19）

完成搜索模型管理最小安全与性能修复：孤儿 Lance 隔离/恢复统一身份与引用预检；深度覆盖检查改为线程内独立只读连接并合并重复请求；候选资源释放后 query encoder 可重建；能力状态区分 BUSY、缺失与加载失败；高频进度轮询不再聚合 SQLite 统计，并在离页/隐藏时停止。受影响定向 pytest 16 通过、1 项原生 symlink 测试因 WinError 1314 跳过；Ruff、Node、diff-check 通过。隔离内置浏览器确认空图库模型状态与搜索页入口。额外前端检查 100 项中有 2 个基线已有失败，报告列明。Luna High 复核的两项问题已修复并确认。真实 Qwen/CUDA 与 NTFS junction 仍 PENDING；未访问真实 PhotoSearchData。已按用户要求本地提交，不推送。详见[执行报告](reports/P4-EMBEDDING-MANAGEMENT-UI-FIX.md)。

## Previous task: P4-EMBEDDING-MANAGEMENT-UI-A（2026-10-19）

按用户指示实现搜索模型展示、候选分批建库、孤儿表隔离申请及停机切换。定向 pytest 66/66、Ruff、Node、diff-check 通过；Luna High 复核问题已修复。浏览器只验了隔离空库状态展示；模型缺失，建库/检查/切换动作、重启闭环、孤儿故障恢复矩阵和真实 Qwen/CUDA smoke 仍 PENDING。基线 7842b34；保留 api/models.py 用户修改；未触碰真实 PhotoSearchData。用户随后要求简单提交；实现提交 `885fde1e3b832f84e6bea3532ba444677d419a7e`，不推送。状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。详见[任务卡](tasks/P4-EMBEDDING-MANAGEMENT-UI-A.md)和[报告](reports/P4-EMBEDDING-MANAGEMENT-UI-A.md)。

# Development status

## P4-PHOTO-SIMILAR-SEARCH（2026-10-19）

查看器按钮桌面恢复 SVG+文字、窄屏紧凑显示；相似搜索 A→B→Back 现在验证并恢复快照，零候选使用只读会话验证，普通游标越界规则未放宽。原始功能受影响测试 138/138；本次修复定向 JUnit 10/10，Ruff、Node 与 diff-check 通过。内置隔离浏览器通过 1280×900 桌面、390×844 窄屏、相似搜索返回和普通照片返回检查；Luna High 限定复核无 blocker。状态 **PASS（限定合成验收范围）**；修复提交 `def7e3026f4a67507890e5d49388b37de1ada141`。真实模型/图库、bfcache 专项与大库容量不在已验证范围；未访问真实照片库。详见[任务卡](tasks/P4-PHOTO-SIMILAR-SEARCH.md)与[执行报告](reports/P4-PHOTO-SIMILAR-SEARCH.md)。

2026-10-19 模式切换残留续修：文字搜索提交同步清理参考栏/缩略图/文件名和相似 URL，普通结果渲染复用清理逻辑；CSS 显式尊重 `hidden` 并更新缓存版本。定向 JUnit 3/3（含请求挂起及 503 失败行为）、Node 语法、diff-check 通过；隔离内置浏览器确认普通页初始隐藏及相似→文字切换立即移除摘要。仅默认桌面视口，未做真实浏览器端 API 故障注入。实现提交 `762ce4c803475dce126acd0ca537195cbe497c63`；详情见原[执行报告](reports/P4-PHOTO-SIMILAR-SEARCH.md)。

## P4-UI2-SEARCH-TOOLS-PLACEMENT-A — 搜索辅助入口布局优化（2026-10-19）

固定搜索栏右侧提供「以图搜图」，页面标题右上角提供按需「索引状态」对话框，结果区底部的旧面板已移除。图片上传成功前不替换旧结果，上传会话支持续页并受搜索世代保护；覆盖率只在显式读取/刷新时请求。受影响前端 JUnit 103/103，Node 语法与 diff-check 通过。隔离内置浏览器验证窄屏、上传成功/失败及续页、覆盖率读取/刷新、收藏范围拒绝和过期响应保护。后续补上索引状态弹窗点击遮罩关闭（内容区不关闭）；该跟进定向前端测试 99/99 通过，本次未重跑浏览器。状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**：真实模型质量、合集/人物/拍摄时间逐项浏览器范围和实际合集保存未验；不涉及真实图库、全仓或规模测试。详见[任务卡](tasks/P4-UI2-SEARCH-TOOLS-PLACEMENT-A.md)与[执行报告](reports/P4-UI2-SEARCH-TOOLS-PLACEMENT-A.md)。
## Search result card information simplification: P4-SEARCH-RESULT-INFO-SIMPLIFY-A (2026-10-19)

结果卡片移除“语义就绪”、通道名次和独立详情按钮，保留总体排名、缩略图预览与预览详情入口；不改搜索数据映射或请求逻辑。原定向单测 99/99、收藏 UI 单测 4/4；续改后搜索/前端单测 99/99，Node 语法检查通过；按卡未执行浏览器/真实图库/规模性能。状态 PASS（限定范围），报告：[P4-SEARCH-RESULT-INFO-SIMPLIFY-A](reports/P4-SEARCH-RESULT-INFO-SIMPLIFY-A.md)。

## Current joint audit: P4-PHOTO-COLLECTION-A + P4-PHOTO-SMART-COLLECTION-A (2026-10-19)

普通合集受影响测试与浏览器保存/查看/移出闭环通过；智能合集补测模型不可用状态、热缓存免重检索和人工状态隔离。智能/普通合集核心组合 **59 项**，迁移/备份恢复/性能计数组 **52 项**通过；独立 100/1000/5000 候选合成计时未显示有界路径异常。首次并行运行的临时目录错误经项目内目录预建后串行重跑通过。两卡继续保留 **IMPLEMENTED / ACCEPTANCE PARTIAL**：COMBINED 真实模型、刷新中进程退出后的租约自动恢复、不同 smart_id 无界排队及 DS-H 完整独审未验。具体证据与 pending 见两份任务卡和[执行报告](reports/P4-PHOTO-SMART-COLLECTION-A.md)、[普通合集执行报告](reports/P4-PHOTO-COLLECTION-A.md)。只使用隔离合成数据。

## Implementation baseline: P4-PHOTO-SMART-COLLECTION-A — 动态智能照片合集与有界语义结果缓存

2026-10-19 第一轮、地点范围修复及 stale/save-static 恢复续交付已落地。FILTER/place_album 排除、可信 EXIF `capture_desc` Keyset、规则冻结及租约发布 CAS 已覆盖；过期 ranked 缓存现以 SQL 按当前冻结范围/资格/排除复核后标记“上次结果·待更新”浏览，刷新后旧 build 游标失效；save-static 通过普通合集事务另存，FILTER 超 5000 明确拒绝，缓存 stale/truncated/indexing 需要确认。最新受影响 JUnit **56/56** 通过；Ruff、py_compile、Node 语法、diff-check 通过。隔离内置浏览器实测过期→刷新、另存、排除/恢复及应用进程重启状态保持。当前整卡仍 **IMPLEMENTED / ACCEPTANCE PARTIAL**：真实 COMBINED 向量模型和 DS-H 独立全受影响面审查尚未完成。详情见[任务卡](tasks/P4-PHOTO-SMART-COLLECTION-A.md)与[执行报告](reports/P4-PHOTO-SMART-COLLECTION-A.md)。未访问真实图库。

## Earlier status record: P4-PHOTO-COLLECTION-A — 普通照片合集与搜索结果保存

2026-10-19，按用户指令实施并续验。schema 0036、合集 CRUD/成员 API、搜索结果新建/追加保存、照片合集限定搜索与时间轴、导航/详情/viewer 入口及 retention/恢复识别已落地。Luna High 复核修复了排名来源 freshness、combined intent plan、追加 API、地点 scope 最终事务校验、封面物化及准确到期判断；追加事务有持久幂等回执。最终剩余关键验收组 **40/40**（`test-results/P4-PHOTO-COLLECTION-A-remainder-final-r7.xml`）通过，涵盖 FILTER/TEXT/COMBINED、捕获时间范围、source-token 身份/篡改/过期、profile/revision 拒绝、0036 故障回滚/旧版拒绝、备份恢复后成员读回、retention、500/501、创建与追加 5000/5001、追加锁失败同键恢复和封面 EXPLAIN。完整 unit/integration 原始基线 3235 项为 3179 passed、5 failed、51 skipped、0 errors；其中本任务 timeline 资源版本断言已同步更新并通过，另 4 个既有资源版本/digest 失败单独复跑仍在，组合证据不称单次最终全量 PASS。隔离内置浏览器验证导航、空合集创建和详情；搜索结果保存受 `prompt()` 工具限制未完成。UI 刷新/真实进程重启恢复、同 profile 身份下 generation 变化、超大无界稀疏合集容量仍待验；封面 EXPLAIN 仅证明索引查询而无 catalog 全表扫描，不作规模承诺。状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。智能合集前置后端来源/范围/revision/追加接口具备可复用合同；本任务收口，不自动启动下一卡。未访问真实图库。性能续验随后确认 FILTER_ONLY 多页 COUNT 从每次保存 100/99 次降至 1/0；300k 合成 capture 时间轴对 100/1000 成员用成员索引、对 5000/150000 成员用时间索引且首续页无临时全局排序。20 合集/92,200 离线成员封面页实测约 17.71ms，仍为成员线性检查。受影响性能组合 25/25，详见同一执行报告；整卡仍 ACCEPTANCE PARTIAL，不自动启动智能合集。详见[任务卡](tasks/P4-PHOTO-COLLECTION-A.md)与[执行报告](reports/P4-PHOTO-COLLECTION-A.md)。

2026-10-20 搜索选择保存命名跟进：搜索页保存已选照片为新普通合集时，以当前已确认搜索关键字预填名称，空关键字留空，长度最多 80 个 Unicode 字符。Node 定向 JUnit 1/1，Ruff、search.js 语法和 diff-check 通过；内置隔离浏览器搜索合成文件名 `p520` 并确认命名框预填，取消未创建合集。只增加对话框初值，不改搜索/合集后端合同、数据库或选中数据。未重跑全仓、未访问真实图库。详见[普通合集任务卡](tasks/P4-PHOTO-COLLECTION-A.md)与[执行报告](reports/P4-PHOTO-COLLECTION-A.md)。

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

2026-10-19 收藏拍摄排序专项复核：确认原 `capture_desc` 在 2k/10k/50k 收藏及 100%/10% 密度均出现 `USE TEMP B-TREE FOR ORDER BY`；无 hint 也未改变，50k/500k 第二页约 59.251ms。改为两个现有 EXIF 索引流有界 keyset + merge，并按 asset_id 补未知时间尾部；不新增索引/migration。六种条件第一页/第二页/深页均与 oracle 一致，最近首屏 0.080–0.109ms，50k/500k 第二页拍摄排序 59.251ms → 0.343ms。定向 JUnit 8/8 通过；未访问真实图库或宣称 30 万保证。详细证据见 [B 专项报告](reports/P4-FAVORITES-UX-B.md#2026-10-19-专项复核收藏拍摄时间分页性能)。本轮未提交。

2026-10-19 缩略图收藏图标视觉跟进：未收藏为深色圆底白描边星；已收藏为琥珀圆底实心星并带绿色勾选角标。受影响 UI 单测 4/4、Node 行为探针、JS 语法和 diff-check 通过。内置浏览器复用 fixture 因 schema_version 36 不一致未完成视觉检查，标记 PENDING；启动器更新了隔离 fixture 的 `runtime/session.json`，未访问真实图库。详见 [B 专项报告](reports/P4-FAVORITES-UX-B.md#2026-10-19-缩略图收藏图标视觉跟进)。

## Earlier status record: P4-UI2-VIEWER-PAN-AVATAR-FIX — 查看器平移与人物头像修复

2026-10-19，当前卡：[P4-UI2-VIEWER-PAN-AVATAR-FIX](tasks/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)，报告：[执行报告](reports/P4-UI2-VIEWER-PAN-AVATAR-FIX.md)。状态：IMPLEMENTED / ACCEPTANCE PARTIAL。定向单测最终 28/28 通过；此前完整受影响组合 64/64 通过。最后重跑遇并行工作树新增 migration 0035 与 schema 支持版本 34 不一致，38 个 integration 在 fixture setup 报错；不把该次运行称为产品失败或 PASS。JS 语法、Ruff、diff-check 通过。按用户要求跳过浏览器，V01/V04 实际页面与图像边缘验证仍 PENDING。新增 V08 后发现 pointerdown 立即捕获舞台会重定向浏览器 click/dblclick target；现改为超过 4px 拖动阈值后才捕获，dblclick 仍限定图片目标，并抑制实际拖动形成的双击。原生事件序列模拟定向单测 1/1 通过，JS 语法与 diff-check 通过。按用户要求不跑浏览器，真实 V08 由用户手测，仍 PENDING。前一轮提交为 `2df5fe8`；本轮 V08 修正尚未提交。

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

## P4-UI2-MAP-MINOR-FIX-A — 地图相册悬停标记与收藏按钮修复（2026-10-19）

地图悬停 Marker 恢复绝对定位并提高局部层级；卡片操作按钮使用专用样式，避免覆盖共享收藏星标。定向悬停与收藏 UI JUnit 8/8 通过，Node 语法与 diff-check 通过。浏览器视觉与完整点击路线因一次性会话链接流程无法通过当前内置浏览器安全打开，保持 PENDING；状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。详见[任务卡](tasks/P4-UI2-MAP-MINOR-FIX-A.md)与[执行报告](reports/P4-UI2-MAP-MINOR-FIX-A.md)。未访问真实图库。

## P4-PHOTO-SMART-COLLECTION-REFRESH-CLOSEOUT — 刷新可靠性与真实 COMBINED 收口（2026-10-19）

本轮收口范围 PASS：刷新队列为 1 执行 + 2 等待，重复请求去重，队列满可重试；活动 lease 可续租，取得数据根独占锁后能恢复上一进程遗留任务；真实 Chinese-CLIP COMBINED 查询、智能合集创建/刷新/缓存命中和隔离浏览器失败重试均验证。受影响 JUnit 171/171，Ruff、py_compile、Node 语法和 diff-check 通过。模型执行 CUDA / CUDAExecutionProvider，完整身份及候选、缓存、耗时和 FTS LAGGING 限制见[执行报告](reports/P4-PHOTO-SMART-COLLECTION-A.md)。原卡历史整卡 ACCEPTANCE PARTIAL 标记保留，不冒称真实 FTS 追赶、30 万、GTX 1660 或正式 SLA 通过。仅使用独立合成数据与开发机既有模型；未创建后续任务卡。

**智能合集刷新中模型切换补测（2026-10-19）**：修复 `MODEL_REBIND_REQUIRED` 在运行任务期间改写失败状态却遗留旧 owner lease 的边界；按 owner/request 状态 CAS 清 lease，QUEUED 请求保持不变。Chinese-CLIP→模拟 Qwen→恢复、迟到 worker/新 owner、旧版分裂租约、旧缓存与排除保留及立即重试测试 49/49；Luna High 独立复核 PASS。详见同一[执行报告](reports/P4-PHOTO-SMART-COLLECTION-A.md)。

## P4-SEARCH-STRONG-MATCH-EXIF-FIX（2026-10-20）

补充 Chinese-CLIP / Qwen EXIF v2 高度匹配统计身份映射，复用现有阈值，普通版身份、候选、排名、分页及 5000 边界保持原语义。定向 pytest 39 passed；Ruff、Python 编译、Node 语法和 diff-check 通过。测试文件扩展组合中发现的既有前端静态断言与 Node harness 限制见[执行报告](reports/P4-SEARCH-STRONG-MATCH-EXIF-FIX.md)。未加载 EXIF v2 真实模型、未访问真实图库、未做真实浏览器验收；状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。任务卡：[P4-SEARCH-STRONG-MATCH-EXIF-FIX](tasks/P4-SEARCH-STRONG-MATCH-EXIF-FIX.md)。

## P4-QWEN-SEARCH-PERF-DIAG-A（2026-10-20）

只读检查定位到 8000 端口服务绑定 4727 条 assets，catalog 有 1941 条可行动 FTS projection queue（每次搜索追赶最多 800 条）、Qwen EXIF v2 512D READY generation，GPU 单次快照 0% 利用率/4306 MiB 显存；活动 profile、runtime health、搜索各阶段、20 次热搜索和浏览器 Network 均未确认。内置浏览器用当前启动凭据得到 `SESSION_INVALID`；未重启服务、未绕过认证、未发搜索请求。静态调用链显示 UI 默认走 `/api/v1/search/place-aware`，encoder/Worker/adapter 按进程复用，EXIF v2 方向修正只在图片路径。无已确认性能缺陷，暂不建修复任务；状态 **IMPLEMENTED / VALIDATION PENDING**。报告：[P4-QWEN-SEARCH-PERF-DIAG-A](reports/P4-QWEN-SEARCH-PERF-DIAG-A.md)，任务卡：[P4-QWEN-SEARCH-PERF-DIAG-A](tasks/P4-QWEN-SEARCH-PERF-DIAG-A.md)。原始脱敏材料：`test-results/p4-qwen-search-perf-diag-a.json`。本记录不替换上方仍在进行的 C2-F。

2026-10-20 续测复核：再次检查时 8000/8117 均无监听服务，内置浏览器无已认证会话；未启动指向不明数据根的服务，未发搜索请求。实测仍待目标应用按正常方式启动并生成新会话后继续。报告和 JSON 已记录本次阻塞事实；不替换仍进行中的 C2-F。

2026-10-20 最新复核（本地 09:32）：8000 无监听；8117 有服务但只读确认绑定旧空 catalog（指纹 `23BE562F5E`、0 assets），未向其发搜索请求。内置浏览器无标签页/认证会话。目标图库实测仍待正常服务与新会话；详情见同一报告及 JSON `latest_recheck`。 Luna 子代理复核确认静态结论有源码支持，并已澄清初次服务与 FTS 队列快照时间；实机验收继续 PENDING。

2026-10-20 09:47 续测：8000 无监听；8117 仍绑定空 catalog（指纹 `23BE562F5E`、0 assets），不是目标图库。未发搜索请求、未启动服务或访问照片；实机验收继续 PENDING。 Luna High 本轮只读复核无 blocker；确认材料和静态源码判断一致，未补足实机测量。

2026-10-20 诊断能力实现跟进：新增显式启用的多模型搜索性能诊断面板、搜索执行阶段与内部阶段计时与隐私受限 JSON 导出。定向诊断测试 11/11、文字分页检查 2/2；Ruff、Python 编译、Node 语法、diff-check 通过。隔离空合成库浏览器验证面板开关和搜索执行阶段计时，缺少活动模型的样本按绑定不匹配拒绝计入。额外旧回归 5 项失败（4 项地点 fixture manifest 版本不兼容，1 项 UI Node harness 缺既有 `window.addEventListener`），均保留记录。真实图库及四模型运行时、Worker/GPU、足量热样本仍待正常会话验收；当前为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。详见同一[任务卡](tasks/P4-QWEN-SEARCH-PERF-DIAG-A.md)与[报告](reports/P4-QWEN-SEARCH-PERF-DIAG-A.md)。不访问真实照片，不覆盖并行 C2-F 当前记录。


## P4-LIBRARY-RETENTION-C2-FIX（2026-10-20）

一键清理现按点击事务持久化候选身份快照，按 20 张 keyset 批次执行实时复核；旧 schema 41 活动 run 可以从既有批次恢复并重建计数。目标 DELETE_VECTOR 走 IndexCoordinator 定向协议，单资产最多 100 个事件、最多 5 次 drain；手动/自动授权冲突明确拒绝。定向 pytest 51/51、Ruff、py_compile、Node 语法与 diff-check 通过；Luna H 两轮复核关闭了候选静默漏检 blocker。内置浏览器确认失效照片面板及合成统计可见，但原生确认框无法由当前浏览器控制完成，确认后的 T10 跨批/刷新/返回仍 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。报告：[P4-LIBRARY-RETENTION-C2-FIX](reports/P4-LIBRARY-RETENTION-C2-FIX.md)，任务卡：[P4-LIBRARY-RETENTION-C2-FIX](tasks/P4-LIBRARY-RETENTION-C2-FIX.md)。未访问真实照片库、未做全仓/30万/GPU 验收。

## P4-LIBRARY-RETENTION-C2-RETRY-FIX（2026-10-20）

共享未结算谓词收口 PARTIAL 创建互斥、启动恢复、worker 与进度显示；终结 run 保留历史统计并允许新点击创建新 run。ACTIVE barrier 重试重新校验当前 MANUAL/AUTOMATIC 授权、来源、扫描、副本和人工保护；人工保护变化会按既有协议撤销 barrier、清理旧 DELETE_VECTOR 并排入 UPSERT 修复，来源/证据阻断则保留待复核 barrier。受影响 retention + storage UI JUnit **40/40** 通过（`test-results/P4-LIBRARY-RETENTION-C2-RETRY-FIX-final-r4.xml`），Ruff、Python 编译、Node 语法、查询计划与 diff-check 通过。Luna H 独立复核确认人工保护 blocker 已关闭。隔离浏览器使用新会话确认 retention 页面/API 可用，但原生确认交互触发 CUA 焦点控制超时；catalog 复核为 65 assets、0 runs、0 ACTIVE barriers，确认未执行清理。T10 操作仍 PENDING，状态 **IMPLEMENTED / ACCEPTANCE PARTIAL**。未访问真实图库，未提交。报告：[P4-LIBRARY-RETENTION-C2-RETRY-FIX](reports/P4-LIBRARY-RETENTION-C2-RETRY-FIX.md)，任务卡：[P4-LIBRARY-RETENTION-C2-RETRY-FIX](tasks/P4-LIBRARY-RETENTION-C2-RETRY-FIX.md)。
