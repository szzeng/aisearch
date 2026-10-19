# P4-PHOTO-COLLECTION-A 执行报告

日期：2026-10-19
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**。照片合集核心与搜索保存功能已落地，但任务卡要求的若干故障恢复、容量边界和浏览器流程仍未验证；不标 PASS。

## 实施内容

- schema 0036 增加普通照片合集、稳定实例令牌、成员顺序/计数/revision，以及 retention 和存储维护识别。
- 新增合集 CRUD、分页/封面、批量增删 API；创建和追加支持从搜索保存，服务端区分排名候选快照与纯筛选结果，候选保存上限 5000，手选上限 500。
- 排名来源在保存时复用原搜索会话校验；地点、人物合集、照片合集、收藏和目录版本在结果分页与提交事务处校验。地点建议/包版本在最终写事务中再次核验。
- 搜索范围和时间轴接入独立 `photo_collection` scope；前端新增导航、详情、时间轴/限定搜索、搜索结果保存与追加、viewer 加入合集入口。
- 合集目录封面查询每个显示合集至多返回一个首张合格照片；合成 5000 个离线成员的查询计划走成员位置索引，未出现 `SCAN m2` 或 `SCAN assets`。无合格封面仍会按成员数遍历有序索引，不据此宣称大规模/延迟保证。

## 验证结果

- 合集单元/集成及时间轴定向组合：**9 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-core-r7.xml`；追加回执提前重放后的合集/前端组合：**105 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-append-replay.xml`。
- 迁移、数据集、备份恢复、前端缓存及共享消费者相关组合：**141 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-related-rerun.xml`。
- 全量 `tests/unit tests/integration` 基线（最终 receipt-before-token 路由修复前）：**3235 项，3179 passed、5 failed、51 skipped、0 errors**，JUnit `test-results/P4-PHOTO-COLLECTION-A-unit-integration-final-r2.xml`。本轮修正了 `test_p4_ui_people_d` 中随本任务更新的 timeline JS 和既有 favorites CSS 资源版本断言；它已在最终定向组通过。其余四个历史失败已单独复跑并仍失败：人物页面两条旧 `persons-albums.js` 版本断言、地图脚本旧版本断言，以及 `test_p4_time_contracts` 的 digest 参考实现漏掉生产摘要中的 `favorites_only: false` 字段。`query_hash` 未由本任务修改；本任务新增真实 capture-range 保存用例通过，故该历史测试失败不能作为本任务拍摄范围合同失败的证据。没有改写这些无关断言。完整组结果是基线加增量证据，不等于单次最终全量 PASS。
- Ruff、相关 Python `py_compile`、`app.js`/`search.js`/`photo-info.js`/`photo-timeline.js` 的 `node --check` 与 `git diff --check` 通过。
- Codex 内置浏览器使用 `.tmp/P4-FAVORITES-UX-B/PhotoSearchData` 合成库、`127.0.0.1:18849` 和新的一次性会话。确认首页/API 已认证；实际浏览合集导航、创建空合集、打开详情与返回入口。尝试搜索结果选择 3 张后保存时，浏览器工具报 `prompt() is not supported`，因此没有完成该关键保存流程；未把它记为浏览器 PASS。浏览器截图曾在会话内显示，但没有可导出的截图文件。

## 独立复核与剩余问题

用户指定的 **Luna High** 子代理完成多轮只读复核。排名来源过期检查、缺少追加 API、地点版本最终写事务校验、封面结果物化及追加回执必须早于临时 token 检查等发现均已修复。追加回执在成员事务内提交，集成测试清空内存来源注册表后以原目标 revision 和同一请求键重试，确认无需临时来源 token 缓存即可回放；未执行独立 OS 进程重启测试。UI pending request ID 仅保存在当前页面内，刷新或关闭后没有 UI 恢复入口。

### 2026-10-19 剩余关键验收

- 最终定向命令：`.venv\Scripts\python.exe -m pytest -q tests\integration\test_p4_photo_collections.py tests\unit\test_photo_collections.py tests\unit\test_catalog_migrations.py tests\unit\test_p4_ui_people_d.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-remainder-final-r7.xml --basetemp=.pytest-tmp\p4-photo-collection-remainder-final-r7`，**40 passed / 0 failed / 0 errors / 0 skipped**。该组覆盖 FILTER/TEXT/COMBINED 保存及顺序、捕获时间范围、source token 会话绑定/篡改/到期、profile 切换和目录 revision 变化、0036 故障回滚及 schema-too-new 拒绝、备份恢复后 catalog 重开与人工成员保留、retention 保护、500/501 和创建/追加 5000/5001 边界、创建与追加写锁冲突后的同键重试/幂等、稀疏离线封面 EXPLAIN。
- 本轮 `ruff check` 首次发现测试导入排序问题，已按 Ruff 规范整理；之后 Ruff、相关 Python `py_compile`、`search.js`/`persons-albums.js` `node --check`、`git diff --check` 均通过。Luna High 独立最终跟进对最新恢复/限额/锁测试未发现具体问题。
- 0036 失败迁移后 schema 仍为 35，新表/metadata 均不存在且外键检查干净；恢复正版迁移成功。恢复测试用新数据根重新打开 SQLite 并读回合集成员。retention 对缺失位置但仍由人工合集引用的资产保留保护。
- 5000 离线成员封面计划使用成员顺序索引搜索，无资产表或成员表全扫描；它仍可能访问该合集的所有成员索引项，当前只证明不扫整个 catalog 表，不构成对无上限合集成员数的规模保证。
- 追加写入在同一 Catalog 进程中经清空 source-token 内存注册表后可由同请求键的持久回执恢复；写锁失败的追加事务没有部分成员或回执，释放锁后同键重试成功且重复调用复用回执。UI 刷新后 request ID 恢复及真实 OS 进程重启验证仍未完成。

仍待验收：内置浏览器中的搜索选择→保存真实交互（当前 CUA 被原生 `prompt()` 阻断）、viewer 加入/移除/改名/删除关键闭环、刷新后的 pending request ID 恢复、真实进程重启后的回执重放、同一 profile 身份下 generation 变化专测、时间轴跨页完整合同、超大稀疏合集的规模表现及 append receipt 元数据长期清理策略。其余四个旧基线测试失败如上所述；不把它们混同为本任务缺陷。未执行 GPU、真实图库或 30 万照片性能测试；未访问真实 `PhotoSearchData`。

**智能合集前置接口判断：后端普通合集成员真值、FILTER/TEXT/COMBINED 的搜索来源 token、候选顺序/范围、scope 与 revision 复核、显式追加及事务回执已具备可复用稳定合同；可作为智能合集设计的后端基础。整卡仍是 ACCEPTANCE PARTIAL：浏览器交互和重启/刷新恢复边界未闭合，封面无界成员量也没有容量承诺。智能合集尚未启动。**

## 可复现命令

```powershell
$env:PYTHONPATH='src;tests;.'
.venv\Scripts\python.exe -m pytest -q tests\unit\test_photo_collections.py tests\integration\test_p4_photo_collections.py tests\integration\test_p4_ui_timeline_integration.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-core-r7.xml --basetemp=.pytest-tmp\p4-photo-collection-core-r7
.venv\Scripts\python.exe -m pytest -q tests\integration\test_p4_photo_collections.py tests\unit\test_photo_collections.py tests\unit\test_api_frontend.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-append-replay.xml --basetemp=.pytest-tmp\p4-photo-collection-append-replay
.venv\Scripts\python.exe -m pytest -q tests\unit tests\integration --junitxml=test-results\P4-PHOTO-COLLECTION-A-unit-integration-final-r2.xml --basetemp=.pytest-tmp\p4-photo-collection-full-final-r2
.venv\Scripts\python.exe -m pytest -q tests\integration\test_p4_photo_collections.py tests\unit\test_photo_collections.py tests\unit\test_catalog_migrations.py tests\unit\test_p4_ui_people_d.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-remainder-final-r7.xml --basetemp=.pytest-tmp\p4-photo-collection-remainder-final-r7
```

## 2026-10-19 性能收口：保存筛选、合集时间轴与封面

本轮基于当前本地 `master` / `origin/master` `bd7f1af`（性能前测快照 `a63f0d4`；中间仅合入迁移保障文档及无关收藏图标提交，受测后端链路未改），只使用 `.tmp/p4-photo-perf-z71ua719/PhotoSearchData` 的隔离 300,000 资产合成 catalog，未访问真实图库。没有新增完整 30 万容量或产品延迟承诺。

| 场景 | 数据/设备与口径 | 修改前 → 修改后 | 结论与边界 |
|---|---|---|---|
| 保存全部 FILTER_ONLY 结果 | 300k；保存 5,000 条、页 50，端到端旧实现实测 | 100 次全范围 COUNT + 99 次 offset COUNT；COUNT 分别累计 366.8/195.3 ms，保存 1.281 s → 定向路由测试确认新建和追加各只做 1 次首屏精确 COUNT、后续页 0 次全范围/offset COUNT | 去掉每续页重复统计；来源 token、revision、资格、顺序、累计 5000 上限及拒绝行为保留。新计数工作量由 120 条合成路由测试断言；未重复跑 5000 条端到端计时。 |
| capture 时间轴首/续页 | 同一 300k DB；串行 5 次中位数，页 50 | 5,000 成员首/续页 11.15/8.93 ms → 7.74/8.06 ms；150,000 成员 371.20/2.25 ms → 1.82/1.53 ms | 首/续页分别捕获两个 EXIF 来源 SQL 的实际计划；大合集均由 `idx_asset_metadata_capture` 做 keyset，并通过反向成员索引验证成员，无 `TEMP B-TREE FOR ORDER BY`。不是对其它设备的延迟保证。 |
| ultra-sparse capture 时间轴 | 同一 300k DB；100/1,000 成员，无/极少合格 capture；串行 5 次中位数 | 强制全库 capture 索引路径 189.50/40.73 ms → 成员有界路径 0.89/2.61 ms | 小合集阈值设为 1,000，驱动端为 `UNIQUE(collection_id,position)`；最多检查该小合集成员并排序，不扫全库时间索引。100 成员样例只有 34 张符合照片资格；续页无数据时低于 0.001 ms。阈值仅依据当前合成样本。 |
| 大合集时间统计 | 同一 300k DB；精确 capture period 统计，中位数 3 次 | 稀疏：8.11 → 7.54 ms；150k：268.84 → 247.62 ms | 这是精确范围统计，仍随有效数据工作；本轮未重写统计、不宣称消除全局聚合成本。 |
| 合集封面目录页 | 同一 300k DB；catalog 共 31 个合集，页面返回 20 个、首屏 92,200 个离线成员、5 次整页中位数 | 原快照：20 合集/100,000 离线成员约 22.33 ms；本轮：20 合集/92,200 离线成员约 17.71 ms，SQLite VM 步骤约 1,384,000 | 实际计划显示 `SCAN c` 遍历覆盖索引（夹具共 31 个合集），随后按成员位置索引检查候选及 asset/location 索引校验；没有 catalog 资产全表扫描。无封面时线性检查成员，成本随页合集及其成员数增长。当前实测没有显示需要改写的瓶颈，极大无界离线合集仍无延迟/容量保证。 |

时间轴自适应是本轮唯一运行时修复：`_scope` 已读取的 `member_count` 仅作内部访问路径提示，不进入游标 fingerprint 或响应；小合集用成员驱动并显式固定 SQLite 连接顺序，大合集仍用全局时间序索引。成员内联 join 已替代重复的相关成员 EXISTS 探测。两路 EXIF capture 查询在 Python 中按 `(taken_at_sort, asset_id)` 合并，跨 `EXIF_ORIGINAL` / `EXIF_DIGITIZED` 同毫秒测试及续页结果为 `[3, 2, 1]`；成员集合、排序、scope/revision 游标与现有资格 SQL 未改变。FILTER_ONLY 保存改动只关闭非首屏统计计算。

### 失败基线与捕获时间合同

原 unit/integration JUnit 中 5 个失败已逐项对照：其中 `test_p4_ui_people_d::test_person_and_collection_albums_load_the_shared_timeline` 是本任务共用 timeline 静态资源版本随变更更新后已通过的断言；其余 4 个为既有人物页资源版本断言 2 项、地图资源版本断言 1 项，以及 `test_p4_time_contracts::test_the_continuation_digest_binds_normalized_capture_bounds_and_retrieval_mode` 的摘要参考值未包含既存 `favorites_only: false` 默认字段。未改这些无关断言。与本任务相关的 `test_capture_range_filter_save_keeps_the_frozen_capture_scope` 在本轮定向组通过，覆盖捕获范围保存；capture 时间轴补充了跨来源平局与 cursor 续页测试。

### 本轮验证与未验边界

- 本轮最终受影响 JUnit：`test-results/P4-PHOTO-COLLECTION-A-perf-final-r4.xml`，25 passed、0 failed、0 errors、0 skipped；覆盖搜索保存、捕获范围、revision、备份/retention/人工成员、写锁重试、限额边界和时间轴。0036 migration/refusal 的既有验收由 `test-results/P4-PHOTO-COLLECTION-A-remainder-final-r7.xml` 40/40 记录，本轮没有重跑。`ruff check`、相关文件 `py_compile`、`git diff --check` 通过。
- 300k 合成前后原始摘要及真实 EXPLAIN/计时：`test-results/P4-PHOTO-COLLECTION-A-perf-baseline.json`、`test-results/P4-PHOTO-COLLECTION-A-perf-after.json`；复测命令为 `.venv\Scripts\python.exe .tmp\p4-photo-perf-z71ua719\measure_after.py`。只表示该隔离数据库和本机执行样本。封面时间为真实 `list_collections(limit=20)` 完整调用，不是单独 SQL 计时；SQL VM 工作步数按 1000 指令回调近似。
- 该轮没有浏览器验证；本任务搜索结果保存的浏览器闭环仍受 `prompt()` 工具限制，按原报告保持 PENDING。未验证真实进程重启、刷新后的幂等恢复、超大无界稀疏合集容量、不同设备性能或 GPU/真实图库。
- 智能合集前置接口仍可复用且本轮没有改变搜索/成员数据合同；本报告不把整卡升级为 PASS（仍 `IMPLEMENTED / ACCEPTANCE PARTIAL`），也不启动智能合集。

## 2026-10-19 普通/智能照片合集联合复核

### 目的与结果

按联合复核要求，检查普通合集保存、分页、时间轴、封面与智能 FILTER/排名缓存路径；只使用 `.tmp` 项目内隔离合成 catalog，不读取或修改真实图库。没有发现普通合集重复 COUNT 回归或需要扩大优化范围的查询问题。本轮仅修复了智能合集 COMBINED 空 active-profile 状态漏报，详情见智能合集报告。

### 验证

- 联合功能组：`.venv\Scripts\python.exe -m pytest -q tests\unit\test_photo_smart_collections.py tests\integration\test_p4_photo_smart_collections.py tests\integration\test_p4_photo_collections.py tests\unit\test_photo_collections.py tests\integration\test_p4_storage_ui.py tests\unit\test_photo_view_info_ui.py --junitxml=test-results\joint-smart-combined-20261019.xml --basetemp=.pytest-tmp\joint-smart-combined`：**59 passed, 0 failed, 0 errors, 0 skipped**。
- 迁移、备份恢复、普通合集 COUNT/范围搜索与时间轴组 `test-results/joint-data-20261019-rerun.xml`：**52 passed, 0 failed, 0 errors, 0 skipped**。覆盖 0036/0037 migration 路径、reopen/restore、保留成员、FILTER 保存统计、合集范围搜索和时间轴消费者；不访问生产数据库。
- Ruff、相关 Python `py_compile`、`node --check src/photo_search/web/app.js`、`git diff --check` 均 exit 0。并行首轮因本地 Lance/SQLite 临时根目录没有预建报 IO 错误；预建项目内目录后串行重跑通过，未发现产品断言失败。
- 内置浏览器实际流程：新鲜一次性 session 登录本机 `127.0.0.1:18849`，搜索合成文件名 `beach`（TEXT），保存 1 张为普通合集，详情查看并打开/关闭照片 viewer，切换到拍摄时间轴，移出测试照片并返回目录。每个页面状态符合预期；服务随后停止。该流程由当前实现实测，替代旧报告中 `prompt()` 工具限制下的搜索保存 pending。智能 stale 刷新、另存、排除/恢复沿用同一实现版本的既有隔离浏览器证据，避免重复检查共享 UI。
- 普通/智能状态隔离新增集成断言在联合 JUnit 通过：新建普通成员与收藏后创建 smart cache、排除一张，验证普通成员、收藏、smart rule/cache 和 exclusion 行分别保持独立。

### 状态

普通合集已通过本轮要求的保存/浏览/移出/返回代表性流程，可在已验证功能范围内进入日常使用。整卡仍 `IMPLEMENTED / ACCEPTANCE PARTIAL`：UI pending-request 跨进程恢复、同 profile 下 generation 变化、极大无界稀疏成员容量和独立 DS-H 全受影响面审查仍未验。既有 300k 合成证据仍是本项目唯一大库对照，不构成其它设备或无界容量承诺。
