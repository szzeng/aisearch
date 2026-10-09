# P4-PERSON-ALBUM-PERF-A 实现与验证

日期：2026-10-09。基线：`65386805f5365ad14a879a626f6b4a2613950c87`。当前状态：**PASS — 个人相册基础功能与小样本验证**。用户本轮改为个人基础验收，见文末修订；历史B级全量未通过的证据仍保留，不追认全量PASS。

## 实现

- `persons/albums.py`：人物列表先按原 `kind/q/cursor` 选本页至多40人，再以两条批量 SQL 投影有效脸数、去重照片数和头像候选。列表响应继续使用 `MEMBER_PREDICATE`、`COUNT(DISTINCT asset_id)`、有效ACTIVE封面与原头像排序规则；共享 `person_summary` 和单人封面更新语义不变。
- 照片页对当前最多60张照片批量读取 taken-time 与该人物的脸。taken-time rule gate、字段映射、脸排序、每图8+1 `has_more` 及缺事实回退与原逐张 helper 相等。页面照片主查询仍调用原 `asset_page(order="capture_desc")`，保留权威成员过滤、revision 游标、61张前瞻和分页。
- `persons-albums.js`：普通启动不再读取完整 `/sources`、补识别批次 active 或 preview。切换到“整理一下”时才加载来源与任务状态；离开整理视图使旧批次响应失效并暂停页面轮询，不影响后台任务本身。首屏前4个人头像和相册前2张缩略图使用 eager/high priority，其余仍懒加载。
- 未改 schema、索引、DTO、身份/人工归属、共享查询调用者、媒体缓存键或图片权限路径。

## 小型合成对照

环境为当前 Windows/Python 环境，61张合成照片、42个人物，HTTP API 每项修复前（逐条投影逻辑）与修复后各3次交错测量。完整样本和执行计划见 [`p4-person-album-perf-measure.json`](../../test-results/p4-person-album-perf-measure.json)。首个列表基线样本包含一次冷启动；用三次中位数比较：

| 路径 | 基线三次 ms | 当前三次 ms | 中位数 ms |
|---|---:|---:|---:|
| 人物列表 API | 19.93, 2.52, 2.47 | 1.92, 1.77, 1.82 | 2.52 → 1.82 |
| 60张人物照片 API | 10.02, 9.55, 9.51 | 6.45, 6.62, 6.77 | 9.55 → 6.62 |

定向 SQL tracing 证明人物页请求总计4条 SELECT（revision、当前页、两条批量投影）；原逐人摘要新增120条。照片页原逐张时间/人脸投影为每页最多180条；当前批量投影执行3条（rule gate、时间、脸）。照片主SQL维持每页一次，执行计划以 `idx_face_person_links_person` 找本人物有效成员、按 asset 主键读取照片与时间，再对命中集做临时排序；61张合成样本执行约 **0.25–0.32 ms**。此小样本不证明30万照片延迟；当前未看到值得扩大为共享查询或索引改造的证据。

名单与照片批量结果分别对比旧标量 helper；覆盖40人分页、空成员、混合命名、头像、61张照片、有效 taken-time 与同图9张脸的8+1显示。原相册集成测试继续覆盖同图多脸/重复脸、下一页/跨人游标拒绝、修订失效与独立人物脸。

真实浏览器首屏头像/首张照片可见时间以及媒体 cold/hot 缓存未测量，记录为 **NOT MEASURED / UNVERIFIED**；页面桩验证只确认首屏优先级属性及启动请求行为，不能替代用户端图片解码/绘制时间。因此结论仅为合成 API 与 SQL 投影耗时降低，不宣称真实图库体感已验收。

## 验证

- 定向 pytest：`tests/integration/test_p4_person_albums.py`、`tests/integration/test_p4_person_album_perf.py`、`tests/integration/test_p4_person_album_perf_measure.py`、`tests/unit/test_person_detail_batch_ui.py`、`tests/unit/test_person_source_detail_ui.py`：**12 passed / 0 failed / 0 errors / 0 skipped**。JUnit：`test-results/p4-person-album-perf-focused.xml`；重复三次测量的 JUnit：`test-results/p4-person-album-perf-measure.xml`。
- Ruff（修改的 Python 文件）、Node `--check`、`git diff --check`：通过。
- 按 V3.1 §3.2 执行 `PYTHONPATH=. uv run pytest -q tests/unit tests/integration --junitxml=test-results/p4-person-album-perf-full.xml`：**2152 tests / 50 failures / 43 errors / 49 skipped**，未通过。首次收集时因 pytest 未把仓库根加入导入路径找不到命名空间 `tools`；加 `PYTHONPATH=.` 后全套执行完成。失败主要落在既有地点 fixture 要求 active source-backed assignment、冻结 p2 companion 哈希/旧迁移版本预期，以及缺少 `face_detail_auto_assets` 的旧 fixture；未触及本任务改动面。执行中还出现 ONNX Runtime 原生 `0xc0000139` 异常，pytest 进程随后继续并完成。完整失败诊断留在 JUnit XML。

- 为核实基线，对上述五类各单独复跑一个代表：地点相册 fixture 的 assignment 结果为空；person membership fixture 缺 `face_detail_auto_assets`；companion SHA-256 实值 `4270997c…` 与冻结断言 `ec7de5f0…` 不符；migration test 仍断言 schema 16 而当前支持 21；strong-match test monkeypatch 的 `search.service.STRONG_MATCH_MAX_DISTANCE` 已不存在。五项均在独立运行中重现，JUnit：`test-results/p4-person-album-perf-regression-probes.xml`。这些根因涉及本任务未修改的地点 source package、测试 schema fixture、冻结 oracle/迁移期望和搜索测试接口；不通过改本任务代码绕过。

按此前全量门禁，B级未通过时不能标PASS。全量失败的代表用例独立复现，代码差异限于相册实现/UI/定向测试/交接文档；全仓基线修复需另划跨模块任务。本段记录当时判断，当前收口范围由下面用户修订替代；定向通过仍不等于全量通过。

审查路由：本次只读有界投影/UI加载行为，无协议、schema、身份、人工状态或事务变化；按任务卡记录 **SOL_NOT_REQUIRED**，理由如上，不申请独立高风险审查。

## 2026-10-09 用户修订：个人相册基础验收

用户明确要求个人相册只做基础测试。当前必需范围收敛为：已有直接定向JUnit及小型合成样本、一次头像→相册→照片缩略图详情→前后切换→返回的基础交互、相关Ruff/Node/diff-check；分页、命名冲突零写入、单脸更正与撤销仍保留直接检查。不再要求先修绿全仓、重新测速、30万/GPU/模型/打包或新增多轮审查。已有其他高风险任务审查状态不因此关闭。

全量93项保留为后置清单，不新增skip/xfail、删除测试或篡改其结果。基础闭环中实际遇到错误才修对应路径与直接回归。用户自己操作私人相册不等于授权本任务扫描/修改PhotoSearchData。

### 一次隔离合成浏览器基础交互（2026-10-09）

- 用 `tools/diagnostics/person_album_browser_smoke.py --port 8874` 在 `127.0.0.1` 启动三张程序绘制的合成照片；未启动或打开指向 `PhotoSearchData` 的现有服务。流程为人物列表 → “爸爸”相册 → 点照片缩略图打开详情 → 下一张（显示第 2 张及另一张合成照片）→ 上一张（回第 1 张）→ 关闭详情返回相册。
- 可见验收：列表上“爸爸/妈妈”卡片及头像正常；相册两张缩略图都实际显示；详情图切换时标题在 `IMG_0001.JPG` 与 `beach sunset.jpg` 间改变，序号为第 1/2 张且首尾按钮禁用状态正确；关闭详情后回到“爸爸”相册。截图与AX页面状态均确认。浏览器夹具总共仅三张照片，因此不够验证浏览器下一页；分页/无重复由既有定向回归覆盖。
- 观察项：详情中的“文件位置”显示了本次合成夹具位于仓库 `tmp` 下的绝对路径。它不是私人照片路径；本次仅做基本浏览验收，未修改此展示行为。真实图片首屏可见速度、冷/热缓存仍未测量。
- 隔离服务已停止，临时合成夹具保留在仓库 `tmp/p4-person-ux-a-browser/20261009-022500`，供本地复查；没有访问、扫描或修改真实图库。

本次基础导航交互完成，标记 **PASS — 个人相册基础功能与小样本验证**；不表示全仓转绿、浏览器分页动态验收、真实图库提速、模型质量或正式发行通过。已有定向证据12/0/0/0及静态检查通过；本次未新增JUnit或重复性能测量。
