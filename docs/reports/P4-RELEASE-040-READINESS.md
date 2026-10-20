# P4-RELEASE-040-READINESS — v0.5.0 个人源码测试版就绪审查

日期：2026-10-20
状态：**REVIEW COMPLETE — HOLD**（完成审查不等于可发布）
审查基于提交 `e64c06420cc06d18e3f8c8bb43c5e68788ebc6b3`；用户将本次候选 Tag 改为 `0.5.0`。本卡不创建 Tag/Release、不改版本或产品代码。

## 候选快照

- `HEAD` 与本地 `origin/master` 一致（`e64c064`，2026-10-20）。tracked 工作树有本次新增 `docs/development-status.md` 摘要；报告文件本身未跟踪；另有多组未跟踪 `.pytest-tmp-*` 目录，均保留原样。结论仅评估 `HEAD`，工作树并非冻结候选。
- 当前 `pyproject.toml` 与 `uv.lock` 项目版本均为 `0.3.0`；API 路径仍为 v1，OpenAPI 元数据取 `APP_VERSION`；`SUPPORTED_SCHEMA_VERSION = 42`，迁移 `0042_retention_cleanup_run_fences.sql`。冻结任务需统一应用/`APP_VERSION`/OpenAPI 元数据、`uv.lock` 项目版本、CHANGELOG 版本标题和发布名称到 0.5.0；除非另有 API 协议变更，不改 API 主路径 v1，也不改数据库 Schema 版本 42。
- 拟用 Tag 按用户要求改为 `0.5.0`，并将本次发布元数据/展示名称按 0.5.0 统一规划；展示名称暂定 `Photo Search 0.5.0 — Personal Beta`，在冻结任务中最终核定。源码当前版本仍是实际观察值 `0.3.0`，本次只读审查未修改它。`0.5.0` Tag 尚不存在于本地/远端。远端已有 `0.4.0` Tag，指向 `4690bc8df42df4182040e0d15da9169d34fe4686`；因此原卡写的 `v0.4.0` 不是当前远端 Tag 名。GitHub Releases API 返回 404，不能据此确认发布列表或断言没有已发布 Release；该状态须在冻结任务中通过有权限的账号核实。
- 目标是 Windows 本机源码运行的个人测试版。PyInstaller 正式包、干净 Windows 离线部署、最低硬件和 30 万容量 SLA 不在声明范围。不要把源码个人测试版表述为正式 Windows 发行。
- `CHANGELOG.md` 仍是 `0.3.0` 历史草案，包含 Schema 32 且把 UI 2.0 写作计划，不能直接作为新发布说明。自该草案后的代表性已实现范围包括收藏、普通/智能合集、搜索状态/保存、OCR 展示、CR2 处理、模型管理、retention 维护与地点搜索改进；冻结时需按本矩阵的证据与限制重写版本标题、展示名称和用户可感知变更。其安全说明继续适用：Catalog 备份不含原图与模型权重；升级后的数据库不保证只检出旧 Git 版本即可降级。

## 六领域证据表

| 领域 | 已证行为与证据 | 缺口、风险及处置 |
|---|---|---|
| A. 搜索与保存 | `P4-SEARCH-STATE-UX-FIX`：搜索 token/profile/generation、分页、快照与失效合同有定向 JUnit；`P4-PHOTO-COLLECTION-A`：合成浏览器曾完成 TEXT 搜索→保存普通合集→详情/viewer/返回。HEAD 最新命名跟进又在隔离浏览器验证关键字预填。 | 搜索状态修复后的完整“搜索→立即保存→翻页/切换→再查看”尚无同版本浏览器证据；EXIF v2 高度匹配只有合成身份统计验证，真实模型/图库未验证。**PARTIAL**；未发现可复现 G1 缺陷，合并入核心交互补验。性能报告不宣称真实图库提速。 |
| B. Qwen 与模型管理 | `P4-EMBEDDING-OFFLINE-SELECTION-FIX` 与管理报告验证合成 profile/coverage/切换拒绝及 API/CLI 合同；搜索保存 API 有 profile/generation 变更拒绝覆盖。 | 当前活动 Provider/Profile/Generation、真实 Qwen 搜索及离线检查→安排切换未在本候选实际模型/图库上走通；Qwen 真实 `1+2+20` 热测待用户授权。**PARTIAL**；无已复现身份串用。真实搜索延迟仅作为后置观察，不宣称加速。 |
| C. 普通/智能合集 | `P4-PHOTO-SMART-COLLECTION-A` 验证 lease/CAS、旧缓存保留/重试、启动恢复，含真实 Chinese-CLIP COMBINED 刷新与隔离浏览器；`P4-PHOTO-SMART-COLLECTION-UX-FIX` 验证 FILTER 浏览/排除和 UI 刷新状态。 | 当前 TEXT/COMBINED 的整条 UI 成功/失败刷新流程与完整跨进程规则替换压力仍未闭环；已有活 worker 异常退出/OS 锁下启动恢复定向覆盖，不能把这些特定证据概括为所有中断路径都未测。P4 卡要求的 **DS-H 全受影响面独立工程审查**仍未完成；Luna H 复核不等价于该 DS-H 门。**PARTIAL**；安排一张智能合集验收/复核卡，不重复已通过的组合证据。 |
| D. 数据安全与维护 | Schema 42 有迁移链和 retention 对 schema 41 活动 run 的恢复覆盖；`P4-LIBRARY-RETENTION-C2-RETRY-FIX` 受影响 JUnit 40/40 验证人工保护撤销、索引修复和重试；合集/搜索相关报告覆盖若干迁移拒绝、失败回滚、备份恢复后人工成员保留。 | 关键缺口：备份/空目录恢复基础报告的主验收仍在 schema 16；后续数据报告提供 schema 36/37 范围证据，但未找到与当前 schema 42 对齐的恢复后人工人物/地点/收藏/合集核验。Retention 浏览器 T10（取消/确认、跨批进度、刷新返回、再次点击）因工具焦点超时未完成。**G0（证据不足，非已确认数据损坏）**：在隔离库按 `schema 41 → 42 migration → schema 42 backup → 空数据根 restore` 顺序验证，并核对人工事实；restore 本身应拒绝不匹配 schema，不替代/触发 migration。另完成 retention 合成 T10 取消和确认/恢复路径；通过前 HOLD。 |
| E. 查看器、OCR、CR2 | `P4-PHOTO-OCR-DISPLAY-A`：同源只读 API、大小限制及两个合成查看入口的历史 OCR 显示；`P4-MEDIA-CR2-A`：5 个既授权样片的只读 RAW 解码结果、合成历史错误恢复用例。 | OCR 异常元数据/指针竞态/长文布局尚有矩阵未验；CR2 真实人脸/OCR/语义建库、重复建库、历史任务恢复和完整 RAW 包链未闭环。**PARTIAL**；未证实 CR2 主路径故障，因此不记已发现 G1，但在声称完整 CR2 能力前需完成最小真样片验收，或经用户接受将该能力明确列为实验/不承诺。 |
| F. 常用 UI、启动退出 | `P4-UI2-CLOSEOUT-FLOW-A` 和 `P4-UI2-VISUAL-FINAL-A` 记录本机隔离数据的首页、搜索、人物/地点、地图、查看器、返回和窄视口浏览器证据；`P4-PHOTO-SMART-COLLECTION-UX-FIX` 有最新 FILTER 流程。 | 200% 原生缩放、部分浏览器异步竞态、所有视图重启/返回并未全部检查；这些不单独阻断个人源码版。**PARTIAL/G2**；仅随相关补验覆盖，不要求整套 UI 重测。 |

## 阻断与后置

### G0 — 必须先补

1. **当前 Schema 备份/恢复与人工事实保留**：在隔离合成目录准备 schema 41 库并升级到 42，创建备份并恢复到空数据根；核对人物/地点/收藏/普通及智能合集等人工状态、catalog/vector/artifact 一致性和原图源文件 hash 不变。至少注入一次恢复校验/发布失败，确认目标没有半成品。应复用 `P4-05`、`P4-PHOTO-COLLECTION-A` 与 migration tests，产出当前代码身份的 JUnit/报告。
2. **Retention T10**：只对隔离合成库完成取消无写入、确认后跨批进度、刷新/返回、可重试终态；结束后只读核对 run/barrier/item/vector 与人工引用，确认没有误删合成库中被保护或仍 PRESENT 的照片。不得对真实图库演练。
3. **智能合集独立审查**：按项目既有门禁完成该状态/持久化改动的 DS-H 全受影响面工程审查；Luna High 可用于本次 readiness 文档复核，但不替代此实现审查。

完成以上后，才重评 G0 与发布候选。当前证据未显示已发生数据破坏；HOLD 原因是风险判断所需证据/独立门禁未齐。

### G1 — 已知产品缺陷

当前未复现可确认的 G1 缺陷。核心搜索保存、模型身份和 CR2 若补验发现严重缺陷，先按最小反例修复并重新审查。

### G2 / 不在本版承诺

最新真实图库 Qwen 性能、30 万容量、GTX 1660、PyInstaller/全新 Windows 离线安装、200% 原生缩放和 OCR 边缘异常均未证实；不作为本机个人源码版的性能/正式发行承诺。CR2 若未完成最小真样片闭环，不得描述为已完整验证。

## 发布判断

**`HOLD`**。审查工作完成，但数据备份/恢复与破坏性清理路径尚不足以判断安全；智能合集状态持久化改动的独立 DS-H 门也待完成。当前没有证据支持 `READY_FOR_RELEASE_TASK`。`SCOPED_PREVIEW_ONLY` 也不能绕过 G0，且是否缩小 CR2 声明需用户明确接受。

必要顺序：先 `P4-DATA-SAFETY-SMOKE`（合并当前 schema 42 备份恢复与 retention T10）；再 `P4-SMART-COLLECTION-ACCEPTANCE`（合并搜索即刻保存/当前 TEXT/COMBINED 交互及 DS-H 审查）；按 CR2 声明决定是否补 `P4-CR2-REAL-ACCEPTANCE`。三者后只重评缺口，再决定是否开独立 `P4-RELEASE-050` 冻结任务。该冻结任务需将 `pyproject.toml`、`APP_VERSION`/OpenAPI 元数据、`uv.lock` 项目版本、CHANGELOG 版本标题/内容与展示名称统一到 `0.5.0`，保持 API 路径 v1 与 Schema 42，核验 `0.5.0` 目标 tag、候选 SHA 和有权限账号可见的 Release 状态。**本审查未改产品、版本、Tag、Release；未运行 pytest/浏览器/模型/GPU，未访问真实图库。**

## 独立文档复核

Luna High 对候选快照、证据引用、风险分级、Tag 与 Release 可见性作只读复核。本报告已修正工作树状态、Schema 42 恢复序列、CHANGELOG 过时描述及智能合集剩余验收范围。本次复核只针对就绪报告，不替代智能合集卡的 DS-H 全受影响面工程审查。
