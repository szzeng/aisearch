# P2-01A — 地点事实、人工控制、数据包与搜索契约报告

日期：2026-09-13。状态：**PASS（A 级文档／契约闭环）**。

本任务只冻结地点详细协议，没有实现数据库表、migration、GPS 读取、地名包、API、前端或调度器。
开始时 HEAD 为 `2222450`，工作树干净；该提交是 P2-01 的收口提交，P2-01 报告、ADR-0003 和
`p2-contracts.v1.json` 与状态文件一致。执行模型为 GPT-5.6 Sol / high；没有自动切换模型或启动
子 Agent。

## 1. 交付物与边界

| 交付物 | 路径 |
|---|---|
| C2 冻结决策 | `docs/decisions/ADR-0005-place-facts-assignment-and-search-contract.md` |
| JSON Schema draft 2020-12 伴随物 | `docs/decisions/contracts/p2-place-contracts.v1.json` |
| 契约结构、正反例和跨行语义测试 | `tests/unit/test_p2_place_contracts.py` |
| 下一任务卡 | `docs/tasks/P2-02.md` |
| 本报告与状态 | `docs/reports/P2-01A-place-contract.md`、`docs/development-status.md` |

没有修改 `src/photo_search/**`、任何 migration、`0001_initial.sql`、ADR-0001～0004、P2-01 伴随物、
既有测试、`pyproject.toml`、`uv.lock` 或模型 manifest；`schema_version` 仍为 1。没有读取或扫描真实
照片，没有访问网络、下载模型／地名包或运行真实推理。

## 2. 来源与冻结决定映射

| 冻结面 | 来源 | ADR-0005 / schema / 测试 |
|---|---|---|
| GPS 事实、机器候选、人工归属三层分离 | ADR-0004 §决策 1～4；架构 §4.5.1～4.5.3 | ADR §3～§5；GeoFact/PlaceResolution/PlaceAssignment；正例与人工覆盖反例 |
| stable place_id、同名、层级、无合并 | 架构 §4.5.1；路线图 §2/§5 | ADR §2/§7；Place closed object、四类枚举、自父级语义拒绝 |
| 首版地点四类 | 用户确认、ADR-0004、P2-01A 卡 | `country/city/attraction/custom`；早期 `POI→attraction`，`ADMIN` 后置新版本 |
| GPS/WGS84/无 GPS/隐私 | 架构 §4.5.2/4.5.4、§12.4；V3.1 §7.2 | ADR §3；GeoFact/Wgs84Coordinate/CoordinatePrivacy；有限数、范围和泄露反例 |
| 最近城市只作建议 | ADR-0004 §4；架构 §4.5.2 | ADR §4；candidate 固定 NEARBY_CITY_POINT + SUGGESTION_ONLY；自动确定反例 |
| 人工锁、排除、批量、幂等、撤销 | 架构 §4.5.3、§10.2、§12.4；V3.1 §11 | ADR §5～§6；PlaceOperation max 500；重复 ACTIVE 和 501 反例 |
| 增量 DDL、RESTRICT、软删除、单例版本 | ADR-0002 §2.2/§3.7/§7/§8/§10 | ADR §7 文档级冻结；测试确认现有 0001 无地点表 |
| 地名包 manifest、激活/回退/引用保护 | ADR-0004；架构 §4.5.2、§11.1/11.4、§12.4 | ADR §8；PlaceDatasetManifest 与正例；WGS84、相对文件、checksum、许可字段 |
| `/api/v1`、session/CSRF/错误信封 | 架构 §10.2；现有 api/errors/session/security | ADR §9；PlaceApiError closed details 和安全字段测试 |
| SQLite 权威筛选、祖先、补查、旧会话 | ADR-0003 §8/§9；架构 §7.1/7.2、§9.1/9.3/9.5 | ADR §10～§11；PlaceFilter；旧 CatalogFilterRevision 语义反例 |
| 数据包/profile/space 解耦 | ADR-0003 §8/§9；V3.1 §7.1/7.2 | ADR §8/§11；使用真实 P2 core schema 拒绝 place_dataset_version 进入 SearchProfile |

主架构 §4.5 的早期地点类型写有 `ADMIN/POI`，而用户确认、ADR-0004 和任务卡固定首版为国家、城市、
景点、自定义地点。ADR-0005 将 `POI` 收敛为协议 `attraction`，不把 `ADMIN` 放进 v1；未来增加独立
行政区类型必须新契约版本和 migration。该收敛没有修改旧规划文档或暗中扩张首版。

## 3. DDL、事务和版本边界

ADR-0005 只设计后续增量 migration：places/aliases/external refs、当前和历史 GeoFact、不可变解析及
候选、当前 assignment/history/exclusions、operation/items、geo dataset/files/active pointer、独立
suggestion revision 与 CatalogFilterRevision/event。所有稳定身份/历史使用 RESTRICT，地点退休为软
删除；SQLite 可表达的约束下沉，父环、类型矩阵、幂等载荷和跨表当前态由短单写事务保证。

人工 assign/unassign/restore_auto、排除、历史、operation/items、CatalogFilterRevision 和通用 outbox
在一个短事务提交；批量 1～500 全有或全无；undo 是递增 revision 的补偿操作。P2-03A 按届时实际连续
版本创建 migration，不能修改 0001。当前仓库依然只有 metadata、scan_runs、source_roots、assets、
file_locations、jobs。

## 4. 正反例与验证边界

正例实际覆盖：无 GPS 成功空结果、WGS84 字段结构、附近城市待确认、无坐标手工自定义地点、人工指定、
MANUAL_UNASSIGNED 取消锁、批量 500、可复现地名包 manifest、祖先地点筛选和安全结构化 API error。

反例实际覆盖：纬经度越界、NaN/Infinity、最近城市直接标自动确定、机器写人工 assignment、同资产两条
ACTIVE assignment、缺 request_id、缺 expected revision、批量 501、坐标塞入 URL/普通日志/反馈字段、
数据包版本进入 SearchProfile、旧 CatalogFilterRevision 继续翻页。JSON Schema 无法表达“数组内按
asset_id 唯一”或“两个实例 revision 相等”这类跨项关系，测试明确用 `_semantic_errors` 执行这些
附加语义；没有把它冒充完整通用 JSON Schema validator 或生产运行时拒绝。

本机环境没有引入 jsonschema/fastjsonschema/referencing。测试执行本伴随物实际使用的关键字子集：
本地 ref、type、required、additionalProperties、enum/const、oneOf/anyOf/allOf/if/then/else/not、
数组上下限/唯一、字符串长度/正则、数值范围与有限数，并读取真实 ADR、地点 schema、P2 core schema
和 0001 migration 做漂移检查。

## 5. 验证

所有 pytest 临时目录和 uv 任务缓存均位于唯一 `%TEMP%` 子目录；命令使用 `--no-sync`，没有更新依赖。

| 命令 | 结果 |
|---|---|
| `uv --cache-dir <unique-%TEMP%> run --no-sync pytest -q -p no:cacheprovider --basetemp <unique-%TEMP%> tests/unit/test_p2_place_contracts.py` | exit 0，21 passed |
| `uv --cache-dir <unique-%TEMP%> run --no-sync ruff check .` | exit 0，All checks passed |
| `git diff --cached --check`（提交前已暂存本任务全部新文件与状态更新） | exit 0，clean |

第一次直接使用 uv 默认缓存做 JSON 语法检查在执行 Python 前失败：Windows 对
`%LOCALAPPDATA%\uv\cache\sdists-v9\.git` 返回 access denied。按既有接手规则没有重复该路径，改用
唯一 `%TEMP%` uv cache 后 JSON 解析通过（38 个 `$defs`）。

第一次 focused pytest 为 20 passed / 1 failed：Schema 和语义校验均通过，失败仅是测试要求 ADR 包含
“地名数据包版本……”的精确措辞，而文档写成“数据包版本……”。ADR 补上“地名”后，以 UTF-8 控制台
重跑为 21 passed；没有弱化断言或改变契约。

## 6. 未验证与后续

P2-01A 不证明任何地点 production 能力已经存在。数据库 CHECK/FK/事务竞争、真实 API 拒绝、GPS EXIF
兼容、GeoNames 构建/许可/覆盖、包激活崩溃、人工撤销、投影补查、搜索分页、备份恢复和 UI 均未实现，
分别留 P2-03A/B/C、P2-04～P2-08。真实行政边界、景点识别、地图、围栏、地点合并不属于首版。

下一任务是 **P2-02 — 持久调度、设备资格与阻塞恢复实现**，推荐
**DS-H / DeepSeek V4.1 Flash / 高推理**，最低 D 级。任务卡为 `docs/tasks/P2-02.md`；它只用确定性
non-model CPU 假执行器证明地点任务资格，不提前读取 GPS 或创建地点表。
