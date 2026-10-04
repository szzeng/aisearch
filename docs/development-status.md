# Development status

更新：2026-10-04。当前规则：主架构**需求修订 9**、执行策略**修订 11**。本文件只保留最新交接，目标 ≤100 行；旧记录见[原样归档](development-status-archive-2026-09-27.md)，默认不读全文。

## P4-PLACE-PACKAGE-RECOVERY — 地名包 catalog 重建后恢复（**IMPLEMENTED / 定向验证通过 / DS-H 审查待做**）

报告：[恢复与实现（第二轮）](reports/P4-PLACE-PACKAGE-RECOVERY.md)；审查卡：[DS-H](tasks/P4-PLACE-PACKAGE-RECOVERY-review.md)；[ADR-0015](decisions/ADR-0015-place-package-catalog-recovery.md)。应用打开空地名 registry 时自动校验/激活唯一健康现存包，删除 catalog/vectors 后重启可恢复；保留包字节/mtime、已有选择及多包拒绝边界。PhotoSearchData 已自动恢复 ACTIVE（45,217条）；定向JUnit **29/0/0/0**，最终恢复/并发 **10/0/0/0**，Ruff/diff-check通过。最终全量 **1968/6/0/48**，禁用新增钩子后6项失败仍复现（旧契约哈希/高度匹配测试），详见报告；人工历史不复原，独立DS-H及决策冻结待做，未标PASS。

## P4-SEARCH-RETURN-A — 搜索结果返回恢复（**IMPLEMENTED / 定向验证通过 / 浏览器验收阻塞**）

报告：[实现与验收记录](reports/P4-SEARCH-RETURN-A-implementation.md)。`search.js` 以 `sessionStorage` 保存/恢复筛选、已加载结果、分页 session/cursor、地点状态与离开滚动位置；详情页「返回浏览」会回到搜索页；恢复复用既有渲染且不发起搜索。新搜索/重置清除现场。JUnit **95/0/0/0**、Node 语法与 diff-check 通过。24 张合成夹具已建立于项目 `.tmp`；Windows 回环端口绑定 `WinError 10013`，提权重试仍失败，浏览器往返验收未执行，因此任务尚不能标 PASS。

## P4-SEARCH-PREVIEW-A — 搜索照片查看、人物头像入口与相册直达（**人物关联定向验证通过 / 搜索查看器完整交互待验**）

[执行卡](tasks/P4-SEARCH-PREVIEW-A.md)、[实现记录](reports/P4-SEARCH-PREVIEW-A-implementation.md)。查看器新增已归属人物头像及直达其人物相册的入口；人物去重、封面缺失头像回退、无人归属状态、人物页定向打开/返回与既有人物 UI 回归 JUnit **10/0/0/0**，Ruff/Node/diff-check通过。原搜索查看器点击、续页导航、错误重试及关闭竞态仍待动态验收；未访问真实图库/服务/模型。

## P4-02Y-W-Q — Qwen 高度匹配恢复（**IMPLEMENTED / 定向验证通过 / 个人网页确认待完成**）

报告：[P4-02Y-W-Q](reports/P4-02Y-W-Q-qwen-strong-match.md)。Qwen 门限为 cosine ≥0.25（距离 ≤1.5）；CLIP 仍为 1.3，未知空间仍隐藏。核查确认门限只在候选冻结后计算显示统计；新回归断言覆盖候选数/顺序与分页元数据不变。指定 JUnit **27/0/0/0**、Ruff、Node 语法、diff-check 通过。个人网页确认尚未完成：检查时无打开浏览器标签，8000/8001/8080 无监听服务；未启动可能写入 PhotoSearchData 的服务。恢复现有网页后需完成三条只读搜索，故本卡暂不标 PASS。

## 并行待验收 P4-HOME-UX-A — 首页快速可用、图库来源辨识与照片预览适配（**IMPLEMENTED / VALIDATION PENDING**）

2026-10-04首页改名：[功能与简单验证](reports/P4-HOME-UX-A-source-rename.md)。每张图库卡片新增“修改名称”→保存/取消；持久更新显示名，卡片/筛选同步，离线及后台建库时可用，不改ID/目录/原图/任务。定向JUnit **147/0/0/0**、Ruff/Node/diff-check通过；重启应用刷新首页生效，无需重建图库。仅合成API及实际JS最小DOM验证，未访问真实库/重启服务/做真实浏览器验收；本补充功能完成，其他状态不变。

2026-10-03新增图库排队小修：[实现与简单验证](reports/P4-HOME-UX-A-build-queue.md)。开始/继续建库可在其他任务运行时立即排队，后台FIFO串行执行、相同scope去重，按钮显示“已排队/正在建库”。定向JUnit **139/0/0/0**（含实际JS最小DOM行为）、Ruff/Node/diff-check通过。重启应用刷新首页生效；等待队列为进程内，重启后需重新点击，持久进度保留。未访问真实库/重启服务/运行全量，本小修完成，其他验收状态不变。

执行卡：[P4-HOME-UX-A](tasks/P4-HOME-UX-A.md)；调查：[代码调查](reports/P4-HOME-UX-A-investigation.md)；实现与证据：[实现报告](reports/P4-HOME-UX-A-implementation.md)。后续修正首页请求顺序、显式展开完整来源路径、预览缩放/适合窗口/实际大小；修复空查询纯筛选搜索将空文本传到接口造成的422。116个相关/直接回归测试 **0失败/0错误/0跳过**、Ruff、Node语法、diff-check通过；合成浏览器确认6张纯筛选搜索结果可见。普通文本搜索 API 在确定性合成编码器下返回命中。2026-10-03只读发现本机8000服务健康；新开的未认证页面取来源摘要返回`SESSION_REQUIRED`，未发起搜索，因此真实会话下搜索结果仍待核。用户需在尝试搜索的浏览器打开启动器打印的一次性链接；不得分享链接/令牌。只运行隔离小型合成夹具；**未访问或重建30万夹具**、真实库或PhotoSearchData，未加载模型。其他待审任务不变。

2026-10-03搜索页进入加速：[修复与证据](reports/P4-HOME-UX-A-search-entry.md)。首页/搜索使用不初始化模型的轻量页面能力接口，来源筛选读取摘要，覆盖率改为按点击读取；定向JUnit **116/0/0/0**、Ruff/Node及合成浏览器通过。需重启应用并刷新页面生效；首次实际搜索仍可能等待模型准备，真实搜索待核状态不变。 本次暂存整理保留搜索返回功能，补交取消启动时自动读取覆盖率的遗漏修正；前端JUnit **95/0/0/0**、Ruff/Node/diff-check通过，详细范围见同报告“暂存区整理与遗漏修正”。

## 当前人物任务 P4-PERSON-PREVIEW-A — 大图上一张/下一张（**IMPLEMENTED / 简单验证通过 / 独立审查待做**）

[执行卡](tasks/P4-PERSON-PREVIEW-A.md)、[实现与简单验证](reports/P4-PERSON-PREVIEW-A-implementation.md)。查看照片新增上一张/下一张和当前位置，按当前相册/搜索结果顺序切换；首尾禁用，跨页复用有界分页，失败可重试，图与人脸同步；关闭/新列表后旧响应不切图。定向JUnit **7/0/0/0**、Ruff/Node/diff-check通过，仅实际JS最小DOM合成HTTP验证；刷新人物页生效，无需建库。未访问真实库/重启服务/做真实浏览器验证，沿用[P4-PERSON-UX-A-review](tasks/P4-PERSON-UX-A-review.md)加审。

## 人物前一补修 P4-PERSON-COVER-A — 手动选择人物封面（**IMPLEMENTED / 简单验证通过 / 独立审查待做**）

[执行卡](tasks/P4-PERSON-COVER-A.md)、[实现与简单验证](reports/P4-PERSON-COVER-A-implementation.md)。人物相册“更换封面”→照片→本人脸“设为封面”，相册/人物卡片同步，复用avatar_face_id持久保存；有效成员/revision/session/CSRF保护，封面失效只读补选。只更新persons显示元数据，不改schema/归属/索引；旧撤销拒绝覆盖新封面。定向JUnit **23/0/0/0**、Ruff/Node/diff-check通过，仅合成API与实际JS最小DOM。重启刷新生效，无需建库；未访问真实库/重启服务/做真实浏览器验证，沿用[P4-PERSON-UX-A-review](tasks/P4-PERSON-UX-A-review.md)加审。

## 人物前一补修 P4-PERSON-CLEANUP-A — 更正后空分组与成功提示（**IMPLEMENTED / 简单验证通过 / 独立审查待做**）

[执行卡](tasks/P4-PERSON-CLEANUP-A.md)、[实现与简单测试](reports/P4-PERSON-CLEANUP-A-implementation.md)。更正后没有有效人脸的未命名分组在分页前隐藏，保留人物身份与撤销；合照其他未命名人脸仍正常显示。“已归到/已并入/已保存”成功状态3秒消失，撤销条保留10秒，旧计时不清除后续错误。定向JUnit **13/0/0/0**、Ruff/Node/diff-check通过，仅小型合成API与实际JS最小DOM验证。重启刷新生效，无需建库；未访问真实库/重启用户服务/做真实浏览器验证，沿用[P4-PERSON-UX-A-review](tasks/P4-PERSON-UX-A-review.md)加审，未标独立PASS。

## 人物前一补修 P4-PERSON-IMAGE-A — 相册错图与人脸裁剪偏移（**IMPLEMENTED / 简单验证通过 / 独立审查待做**）

[补充卡](tasks/P4-PERSON-IMAGE-A.md)、[实现与截图](reports/P4-PERSON-IMAGE-A-implementation.md)。ID图片URL重新验证且新版人物JS绕开已有一年旧缓存；裁剪按校验后的检测文档坐标帧处理JPEG draft和旋转，face-crop@2不复用旧错误裁剪；人脸ID/不可变产物/向量/人工归属不变。定向JUnit **29/0/0/0**、Ruff/Node/diff-check与合成旧缓存→相册→预览浏览器验证通过。重启应用并刷新人物页生效，**不用重新识别或建库**。未访问真实库/PhotoSearchData或重启8000，真实照片效果待确认；独立审查加审本面，沿用[P4-PERSON-UX-A-review](tasks/P4-PERSON-UX-A-review.md)。此前图片补修已由3bab1c3撤回，仅保留撤销提示；以此新证据为准。

## 人物基础待审 P4-PERSON-UX-A — 个人相册与简单整理（**IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**）

[实现报告](reports/P4-PERSON-UX-A-implementation.md)、[功能卡](tasks/P4-PERSON-UX-A.md)、[最小决策ADR-0014](decisions/ADR-0014-person-album-geometry-repair.md)。人物默认点头像看完整照片、按名字选人、取名/明确并入、合照选单脸更正及撤销；未命名与机器/人工待归类集中到“整理一下”，高级流程折叠保留。修复长边>1280的scale及原图坐标错误；新旧产物分版本，保留人工映射hold，明确同空间v1/v2代表兼容且不激活部分新索引。按用户“个人项目，完成后简单测试”收尾：定向JUnit **219/0/0/0**，升级边界补充子集 **3/0/0/0**、Ruff/Node/diff-check通过；合成浏览器完成命名/冲突并入/单脸更正/撤销。未访问真实库/PhotoSearchData或大夹具、未加载模型/调整阈值/自动重检，不声明真实识别率或正式PASS。唯一下一人物阶段：[独立受影响面审查](tasks/P4-PERSON-UX-A-review.md)，建议DS-H→Sol high聚焦身份/人工知识及兼容决策；由用户手动切换，不自动继续Agent或全量测试。首页/搜索/Qwen并行任务原状态保留。

2026-10-03个人反馈修复：自动未命名人物NULL头像现在从自己的有效人脸只读补选；每张相册照片显示对应人物裁剪、预览标出本相册人脸。定向JUnit **20/0/0/0**、Ruff/Node/diff-check与合成浏览器通过，两个人物可对应同一合照但展示不同人脸，见报告末节。未改识别阈值/自动合并/真实数据；真实错分仍未验证，源码需重启应用刷新生效。

2026-10-03当前人物授权补充[P4-PERSON-MATCH-A](tasks/P4-PERSON-MATCH-A.md)：自动归人相似度0.56→0.50，检测0.50/建议0.29及质量/冲突/人工保护不变；历史校准独立保留。新增`face.cli rematch`复用已存同空间v1/v2特征重匹配未归人的脸，保持已有归属，不需要图文重建；错误目录不建空库，新零脸产物不复活旧机器脸。定向JUnit **79/0/0/0**、Ruff/CLI帮助/diff-check通过，见[报告与操作命令](reports/P4-PERSON-MATCH-A-implementation.md)。未对真实库执行/加载模型，不声称真实识别率；独立审查加审本补充面，状态仍PENDING。

## 上一任务 P4-PLACE-SEARCH-A — 普通搜索支持地点＋内容（**PASS — 本机个人地点＋内容搜索与小样本验证**）

用户新增补充：[执行卡](tasks/P4-PLACE-SEARCH-A.md)。基线`f3b04af`。报告：[实现记录](reports/P4-PLACE-SEARCH-A-implementation.md)。新增地点感知搜索入口、共用权威地点成员scope、无语义Top-K限制的纯地点分页、可取消条件chip；active省份INDEX即使无照片也可识别，同名选项附上级地名。定向JUnit 28/0/0/0（含共同向量block路径）、Ruff与diff-check通过。合成浏览器完成`济南的日落`→普通照片结果/预览→取消后保留日落查询并全范围搜索；将样片人工改至上海后重载并重新搜索，济南范围0张。仅合成资产；不代表真实模型准确率或真实图库效果。未访问真实库，不建B/C卡、不跑全量或性能测试；实现已提交c812d10；本次补交方案与交接文档，未重复运行测试，未覆盖项见实现报告。

## 待审任务 P4-EMBEDDING-ARCH — 可插拔语义 provider + Qwen3-VL 512D（**实现完成；独立审查待做**）

实现报告：[P4-EMBEDDING-ARCH 实现](reports/P4-EMBEDDING-ARCH-implementation.md)；独立审查卡：[P4-EMBEDDING-ARCH-review](tasks/P4-EMBEDDING-ARCH-review.md)；[执行卡](tasks/P4-EMBEDDING-ARCH.md)、[接入策略](plans/P4-EMBEDDING-ARCH.md)、[设计核对](reports/P4-EMBEDDING-ARCH-design.md)。状态 **IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**；下一步 **DS-H 全受影响面工程审查 → Sol high 聚焦终审**，模型由用户手动选择。本轮完成 registry/factory/Qwen adapter、`embedding list|prepare-local|build-candidate|select-profile`、候选不激活、受控停机选择的事务内覆盖/outbox/artifact/Lance/资格门禁、页面 `strong_match_available`，并把主环境锁定到 torch `2.8.0+cu128`/torchvision `0.23.0+cu128`/transformers `4.57.0`/qwen-vl-utils `0.0.14`。已按用户要求把 Qwen 装入真实数据根 `D:\SZ\project\PhotoSearchData` 并完成全库切换（3182 张图像、候选建库 17.4 分钟、受控选择覆盖 3182/3182、真实 HTTP 三查询 200）。证据：定向 JUnit **203 tests / 0 failed / 0 errors / 6 skipped**、真实双 provider CUDA smoke **24/24**（`test-results/p4-embedding-arch-smoke.json`，12 合成图、8 中文 query、2 上传自查询、Qwen 峰值显存 4.29 GB）、现有页面文字+上传各一次真实浏览器确认；Ruff/`uv lock --check`/`git diff --check` 通过。未验证项见报告 §5（OOM/GTX 1660 未注入、Lance 行数核对 ≤2 万行、CLIP 侧未做候选选择演练、未打包、未跑性能专项）。onnxruntime 的 PyTorch CUDA 主版本提示已消除：ONNX worker 进程改为 `USE_TORCH=0`（torch 不再进入该进程），见报告 §5.6。真实数据根的日常增量/切回只做机制验证；切回 CLIP 需先补建那张 HEIC。额外全量套件发现 4 个**既有**失败（`p2-contracts.v1.json` 冻结哈希与 `c1d0a34` 不一致，非本卡改动），建议路由 P4-02Y-L 审查，见实现报告 §7。

## P4-PLACE-AUTO-A — 个人GPS地点相册（**PASS — 本机个人地点相册与小样本验证**）

实现报告：[P4-PLACE-AUTO-A](reports/P4-PLACE-AUTO-A-implementation.md)；[唯一功能卡](tasks/P4-PLACE-AUTO-A.md)、[简化方案](plans/P4-PLACE-AUTO.md)、[核对记录](reports/P4-PLACE-AUTO-design.md)。新增地点相册 API/UI、别名地名入口、省份可选包 INDEX 和有界 GPS/解析补齐；人工 assignment/取消及旧搜索语义保留。合成集成覆盖24张照片及边界/分页/控制；JUnit **40/0/0/0**，Ruff 与 diff-check 通过；浏览器验证 Hangzhou入口→杭州网格/预览→改一张到上海后重载保持上海1/杭州23；再将另一张留空取消归类，刷新仍为杭州23且首张为照片 #2，未被GPS归回。仅临时合成图库；未访问真实个人库，不声称真实省份包已安装、行政区准确或大规模性能。P2-04A维持已删除；P4-EMBEDDING-ARCH独立审查状态不变。

## P4-02Y-L — OCR CUDA 设备契约（**实现验收通过；独立 DS-H 审查待做**）

实现报告：[P4-02Y-L OCR CUDA](reports/P4-02Y-L-ocr-cuda.md)；审查卡：[P4-02Y-L-review](tasks/P4-02Y-L-review.md)。Sol 冻结通过；RTX 4060 Laptop 实测 auto 3/3 job 走 CUDA，显式 CPU 3/3 成功；CPU/GPU 输出等价，round-total 中位数 5227.72/843.33 ms（6.199×），synthetic refresh/build 3/3 CUDA。P2/OCR JUnit **119 passed / 0 failed / 0 errors / 19 skipped**；Ruff、lock、diff-check 通过。P4-02Y-L 总任务仍**待独立 DS-H 全受影响面审查**，未标 PASS。

## 待审任务 P4-02Y-W — 5000 搜索结果 + 高度匹配（实现与验收完成，独立审查待做）

实现报告：[P4-02Y-W](reports/P4-02Y-W-5000-results-strong-match.md)；独立审查卡：[P4-02Y-W-review](tasks/P4-02Y-W-review.md)。当前暂定阈值 `distance <= 1.3`（约 `cosine >= 0.35`）；边界测试/文档已同步。此前 B 级 JUnit **1878/0/0/48**、30 万 smoke 及静态检查早于此门限修正，按用户要求未重跑测试，故新门限未验证；总任务仍待独立 DS-H 复核。

## 旁路纯测量 P4-QWEN-SPEED — 完成

报告：[P4-QWEN-SPEED](reports/P4-QWEN-SPEED.md)；原始样本：`test-results/p4-qwen-speed.json`。Qwen query P50 **72.86 ms**；2048D 30 万向量 P50 **23957.95 ms**，20 blocks / 300000 scanned / fallback 0 / truncated false。8 GB RTX 4060 Laptop FP16 CUDA 路径成功；估算搜索时间：512D **3.81 s**、2048D **24.34 s**（均非 HTTP 端到端实测）。

## 上一任务 P4-02Y-V — `top_k` 性能测试（DONE）

纯测量完成（2026-09-30；[报告](reports/P4-02Y-V-top-k.md)、[任务卡](tasks/P4-02Y-V.md)、原始数据 `test-results/p4-02y-v-top-k.json`）。30 万固定夹具两轮交错共 60 样本；向量前缀检查 5/5 通过。Service P50：K200 3894 ms、K500 3946 ms、K1000 4132 ms、K2000 4162 ms、K3000 4340 ms、K5000 3733 ms；K5000 非单调偏快，结论未见可信拐点。纯性能数据不单独决定产品上限。
