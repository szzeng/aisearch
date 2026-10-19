# P4-PHOTO-COLLECTION-A 执行报告

日期：2026-10-19
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**。照片合集核心与搜索保存功能已落地，但任务卡要求的若干故障恢复、容量边界和浏览器流程仍未验证；不标 PASS。

## 实施内容

- schema 0036 增加普通照片合集、稳定实例令牌、成员顺序/计数/revision，以及 retention 和存储维护识别。
- 新增合集 CRUD、分页/封面、批量增删 API；创建和追加支持从搜索保存，服务端区分排名候选快照与纯筛选结果，候选保存上限 5000，手选上限 500。
- 排名来源在保存时复用原搜索会话校验；地点、人物合集、照片合集、收藏和目录版本在结果分页与提交事务处校验。地点建议/包版本在最终写事务中再次核验。
- 搜索范围和时间轴接入独立 `photo_collection` scope；前端新增导航、详情、时间轴/限定搜索、搜索结果保存与追加、viewer 加入合集入口。
- 合集目录封面查询每个显示合集至多返回一个首张合格照片；极端离线成员导致的内部扫描工作量仍未通过查询计划验证。

## 验证结果

- 合集单元/集成及时间轴定向组合：**9 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-core-r7.xml`；追加回执提前重放后的合集/前端组合：**105 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-append-replay.xml`。
- 迁移、数据集、备份恢复、前端缓存及共享消费者相关组合：**141 passed**，JUnit `test-results/P4-PHOTO-COLLECTION-A-related-rerun.xml`。
- 全量 `tests/unit tests/integration` 基线（最终 receipt-before-token 路由修复前）：**3235 项，3179 passed、5 failed、51 skipped、0 errors**，JUnit `test-results/P4-PHOTO-COLLECTION-A-unit-integration-final-r2.xml`。失败为 `test_p4_person_collection_count_ui`、`test_p4_time_contracts`、`test_p4_ui_people_d`、`test_p4_ui_shell`、`test_person_loaded_pages_ui`；分别涉及人物合集/人物页面旧资源版本断言、既有 capture digest 期望、地图脚本旧版本期望。它们不在照片合集变更范围内，本次不重写断言掩盖原失败。之后对最终追加回执/重放变更运行 105 项受影响组合，全部通过。以上是基线加增量的组合证据，不等于单次最终全量 PASS。
- Ruff、相关 Python `py_compile`、`app.js`/`search.js`/`photo-info.js`/`photo-timeline.js` 的 `node --check` 与 `git diff --check` 通过。
- Codex 内置浏览器使用 `.tmp/P4-FAVORITES-UX-B/PhotoSearchData` 合成库、`127.0.0.1:18849` 和新的一次性会话。确认首页/API 已认证；实际浏览合集导航、创建空合集、打开详情与返回入口。尝试搜索结果选择 3 张后保存时，浏览器工具报 `prompt() is not supported`，因此没有完成该关键保存流程；未把它记为浏览器 PASS。浏览器截图曾在会话内显示，但没有可导出的截图文件。

## 独立复核与剩余问题

用户指定的 **Luna High** 子代理完成多轮只读复核。排名来源过期检查、缺少追加 API、地点版本最终写事务校验、封面结果物化及追加回执必须早于临时 token 检查等发现均已修复。追加回执在成员事务内提交，集成测试清空内存来源注册表后以原目标 revision 和同一请求键重试，确认无需临时来源 token 缓存即可回放；未执行独立 OS 进程重启测试。UI pending request ID 仅保存在当前页面内，刷新或关闭后没有 UI 恢复入口。

剩余两项性能/恢复证据：

1. 封面查询至多返回每合集一行，但若成员大量离线/不合格，SQLite 仍可能扫描该合集的有序成员直到找到封面或耗尽；尚无稀疏/离线封面测试或 `EXPLAIN QUERY PLAN` 工作量证据。
2. UI 刷新后没有保留 pending request ID 的恢复界面；服务重启后的实际进程级回执回放也未单独验证。

以下任务卡验收仍待完成：0036 迁移中途故障回滚与旧版拒绝、合集备份/恢复和 retention 人工引用验证、source token 篡改/跨会话/过期、ranked profile/generation 与文字搜索实际保存、500/501 与 5000/5001 边界及写锁、时间轴跨页/捕获时间范围、viewer 加入/移除与改名/删除浏览器流程。受影响面的 5 个既有失败需由可信基线单独对照。未执行 30 万照片性能测试，不据合成小库推断容量或延迟；未访问真实 `PhotoSearchData`。

## 可复现命令

```powershell
$env:PYTHONPATH='src;tests;.'
.venv\Scripts\python.exe -m pytest -q tests\unit\test_photo_collections.py tests\integration\test_p4_photo_collections.py tests\integration\test_p4_ui_timeline_integration.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-core-r7.xml --basetemp=.pytest-tmp\p4-photo-collection-core-r7
.venv\Scripts\python.exe -m pytest -q tests\integration\test_p4_photo_collections.py tests\unit\test_photo_collections.py tests\unit\test_api_frontend.py --junitxml=test-results\P4-PHOTO-COLLECTION-A-append-replay.xml --basetemp=.pytest-tmp\p4-photo-collection-append-replay
.venv\Scripts\python.exe -m pytest -q tests\unit tests\integration --junitxml=test-results\P4-PHOTO-COLLECTION-A-unit-integration-final-r2.xml --basetemp=.pytest-tmp\p4-photo-collection-full-final-r2
```
