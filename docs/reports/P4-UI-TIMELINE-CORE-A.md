# P4-UI-TIMELINE-CORE-A 实施与验证报告

日期：2026-10-13
结论：**IMPLEMENTATION PARTIAL / VALIDATION PENDING**。核心读查询、路由与共享前端组件已落地；本轮用户要求取消新建的 20 万夹具并复用既有 30 万夹具。30 万检查发现明显排序/聚合退化，且旧夹具缺少拍摄时间与人物/合集样本。LUNA High 对 CORE-A 与后续 Revision-A 的复核均已完成；Revision-A 已关闭“新资产入库未推进共享 revision”阻断。真实浏览器证据及 20 万规模验证仍未完成，不得标记整卡 PASS，也不得开始 B。

## 用户测试范围调整

本轮最初新建的 20 万元数据库已停止使用并删除。之后只复用了旧的合成 30 万 catalog：母版 `tmp/p402f-阶段0/scale-300000-wave/data/catalog/catalog.db` 以只读方式检查，再复制到 `.tmp/P4-UI-TIMELINE-CORE-A/scale-300k` 隔离运行当前迁移与查询。母版前后均为 926,244,864 bytes、时间戳 2026-09-25 02:36:30；没有对母版做迁移或写入。隔离副本迁移到 schema 29。

旧夹具含 300,000 个 ACTIVE asset 和 300,000 个 PRESENT location，但 `asset_metadata=0`、`persons=0`、`face_person_links=0`。因此只覆盖入库时间、待就绪、全库年份/月定位与 keyset 页，不代表可信 EXIF 排序或人物/合集大成员查询。

## 实施内容

- 新增只读时间页和 period 查询：`GET /api/v1/timeline/assets`、`GET /api/v1/timeline/periods`；支持 all/source/person/collection，地点保持由 E 卡承接。
- 复用 `photo_asset_sql`、人物 canonical 条件、合集有效成员条件及 `catalog_filter_revision`；游标绑定时间范围、筛选和成员修订，变化时返回明确冲突。
- 新增 `photo-timeline.js` / 局部 CSS / 隔离测试页；组件包含年月摘要、时间依据切换、游标续页、锚点、失败重试及有限页/详情/锚点缓存。
- 30 万查询首次运行发现 library keyset seek 引用未初始化变量；已初始化入库时间排序键，并新增分页/年月定位回归测试。
- 未增加 schema、索引或入库写事务。
- 根据 LUNA High 复核修复：摘要请求使用独立 generation，初始化与切 scope 不再因照片页 reset 丢弃结果；revision stale 时清空游标、暂停自动续页并保留已显示照片，显式重定位一次时以旧页 anchor 重新定位，服务端只对 anchor 允许跨 revision（cursor 仍严格失效）；照片页与摘要 revision 不一致时不显示合并计数；跨页相同分组不重复标题；dispose 移除 host scroll listener。

## 功能与检查

定向 JUnit：7 passed / 0 failed / 0 skipped。

命令：

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location).Path '.uv-cache')
uv run --no-sync pytest -q tests/unit/test_p4_ui_timeline_core.py --basetemp=.pytest-tmp/P4-UI-TIMELINE-CORE-A --junitxml=test-results/P4-UI-TIMELINE-CORE-A.xml
uv run --no-sync ruff check src/photo_search/api/timeline.py src/photo_search/api/routes.py tests/unit/test_p4_ui_timeline_core.py tools/benchmarks/p4_ui_timeline_scale.py
.\.venv\Scripts\python.exe -m py_compile src/photo_search/api/timeline.py tools/benchmarks/p4_ui_timeline_scale.py
node --check src/photo_search/web/photo-timeline.js
git diff --check
```

Ruff、py_compile、Node 语法和 diff-check 通过。测试报告：[JUnit XML](../../test-results/P4-UI-TIMELINE-CORE-A.xml)。没有运行全仓测试、GPU/OCR/模型、打包或真实图库。

## 30 万重点性能复核

命令使用 `--reuse --count 300000 --samples 3`，测当前 schema 上的查询核心，不是 HTTP 端到端耗时。年份/月跳转取旧夹具实际 `MAX(created_at)` 对应的本地年月。原始 JSON：[timeline-benchmark.json](../../.tmp/P4-UI-TIMELINE-CORE-A/scale-300k/timeline-benchmark.json)。每场景 3 次，表中 P95 即 3 个样本的最大值，仅作重点退化诊断，不当作正式 20 万门槛或稳定 P95。

| 场景 | 行数 | P50 | P95 / max |
|---|---:|---:|---:|
| 入库时间首屏 50 条 | 50 | 2116 ms | 2173 ms |
| 入库时间第二页 keyset | 50 | 1815 ms | 1905 ms |
| 年份直达（夹具实际年份） | 50 | 1780 ms | 1804 ms |
| 月份直达（夹具实际年月） | 50 | 1793 ms | 1799 ms |
| 待就绪页 50 条 | 50 | 1805 ms | 1808 ms |
| 全库年份摘要 | — | 3424 ms | 3487 ms |

EXPLAIN 显示资产全表扫描、每项照片资格相关子查询，以及 `USE TEMP B-TREE FOR ORDER BY`；年份摘要出现 `USE TEMP B-TREE FOR GROUP BY`。因此 30 万结果是明显瓶颈，不能声称大图库性能通过。任务卡 §3.1/§6.1 禁止未经单独升级就暗加 schema/索引；本轮保留该限制并记录瓶颈。20 万基准未运行，按用户指示不重建新夹具，`PERF_200K` 仍未验证。

## LUNA High 独立复核及修复

用户指定由子代理 LUNA High 复核。首轮只读复核发现 3 项 P1（摘要 generation 竞态、新资产插入未递增共享 revision、stale cursor 前端仍重试）及 2 项 P2（页/摘要 revision 未比对、跨页组头重复）；另发现 dispose 未移除 scroll listener。首轮复核确认服务端资格/排序/游标大部分契约以及 30 万证据口径正确，没有修改文件或另跑测试。

本轮已修复摘要竞态、stale 暂停与显式 anchor 重定位、跨 revision anchor 语义、摘要/页版本不一致提示、跨页组头、scroll listener 清理。LUNA 修复复核又指出虚拟化可能回收唯一组头，以及重定位未刷新摘要；现已在释放标题页时向紧邻同分组保留页转移其实际持有的组头，且显式重定位同时刷新 periods 和 assets。对多分组跨页的最后补丁也已重跑 7 项定向 JUnit、Ruff、py_compile、Node 语法和 diff-check，结果通过。LUNA High 最终只读复核未发现剩余实现缺陷；复核覆盖了修改面和证据，但按用户路由不称为 DS-H。新资产 revision 阻断随后由 Revision-A 修复并独立复核；真实浏览器交互仍未验证。

## 后续修订：P4-UI-TIMELINE-REVISION-A

**CORE-A 的新资产 revision 阻断已关闭。** Revision-A 在 `bind_location_content` 确认新内容并绑定位置的同一写事务内推进现有 `catalog_filter_revision`（cause=`ASSET_ADDED`）；scanner 保留用户选择的每文件提交粒度，因此扫描 3 张新照片对应 revision +3。旧 Timeline cursor 随后返回 `CATALOG_FILTER_REVISION_STALE`。证据为提交 `fc8f399`、[Revision-A 报告](P4-UI-TIMELINE-REVISION-A.md)及 [JUnit XML](../../test-results/P4-UI-TIMELINE-REVISION-A.xml)：14 passed / 0 failed / 0 skipped / 0 errors。Revision-A 的 LUNA High 最终修复复核无剩余发现。实现沿用既有 revision 表和 cursor 协议，没有新增 schema/API。

## 未通过 / 未验证项

- **BROWSER_PENDING**：隔离测试页和 30 页模拟器已准备，但 Computer Use 因无法确认当前 Chrome 标签 URL 而安全停止；未把模拟器或 API 成绩冒充浏览器证据，也未记录第 1/10/20/30 页 DOM/资源窗口。
- **LUNA HIGH REVIEW COMPLETE**：首轮指出的问题及两轮修复复核发现的 P2 均已修复；最终复核未发现剩余实现缺陷。新资产 revision 阻断已由 Revision-A 关闭；浏览器交互和 20 万性能仍未验证。两次用户指定复核均不称为 DS-H。
- `CHECK_300K` 已运行，但只覆盖旧夹具可代表的入库日期数据；无可信拍摄元数据、人物或合集样本。
- 本轮没有连线首页/人物/地点正式布局；这是 CORE 公共组件卡的范围，B/D/E 负责页面接入。

## 后续

新资产 revision 阻断已关闭。CORE 后续需补真实浏览器证据，并按用户认可的规模范围决定性能验收/记录；再决定是否另立索引性能任务。CORE 关闭前不要开始 B。F1 原有 200% 和已保存搜索命中截图仍 pending，本轮按用户明确要求启动 CORE，不代表 F1 已完成。
