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
