# P4-PERSON-INBOX-GROUP-SORT-A 执行报告

日期：2026-10-09
状态：**PASS / PERSONAL-SCOPED REVIEW（用户指定本会话简审）**
任务基线：`master / 11cc1378982ff714359d08328f44af647b086059`
执行工作区 HEAD：`bce3a28da9ef79ed03930289d0b57ef34df71798`（含任务基线）

## 实施结果

- 在 `persons/albums.py` 增加只读全局收件箱候选分组页与组内分页。复用 `_INBOX_WHERE`、`_INBOX_KINDS` 和 `_ORGANIZATION_REASON` 的可见性/人工状态门禁；仅选未合并人物的有效 SUGGESTED 建议，排除活跃 exclusion，按 `confidence DESC, person_id ASC` 给每张脸选唯一候选。
- 候选需至少两张不同照片才形成临时组；组内数为 `COUNT(DISTINCT asset_id)`。较小候选、无候选脸保持单脸卡。全局顺序为组照片数降序、稳定键升序。新列表/组内游标分别绑定过滤、候选和当前 `catalog_filter_revision`；新 API 限制每页最多 30 卡/脸。
- 新增受 session 保护的 `GET /faces/inbox/groups` 与 `/groups/{group_key}`。旧 `/faces/inbox`、summary、`faceinbox2` 游标与响应未改。查询只读，不写人物、人脸关系、排除或归属。
- 人物收件箱“全部待整理”和“有相似人物建议”改用服务器全局排序的分组卡；按需展开后仍逐脸显示预览与“这是谁？”。其它原因筛选继续使用原单脸接口。刷新只重读人物和 inbox。
- 新增显式“从头重新检查旧照片”补匹配入口；新一轮有独立续批位置，保留原批次 `rematchAfter`，不会覆盖“继续补匹配”的位置。页面加载和刷新不发起 rematch。

## 验证

- 定向回归：人物相册、人物候选匹配、rematch 和前端行为测试共 **23 passed，0 failures/errors/skips**。JUnit：`docs/reports/P4-PERSON-INBOX-GROUP-SORT-A-related.xml`。
- 新增合成验收覆盖：10/5/2 照片候选组降序、同图多脸去重、多候选唯一归组、活跃 exclusion 与单照片候选降级为单脸、按组展开分页、跨组/跨过滤游标拒绝、动态重读、只读数据库快照、旧接口兼容与 30 卡硬上限。单测模拟展开调用，服务端使用隔离合成照片，无真实图库访问。
- 合成 31 脸页面请求：**23.205 ms**（该次本机运行值，不是容量承诺）。`EXPLAIN QUERY PLAN` 确认候选关联按 `face_person_links` 索引检索。
- `uv run ruff check ...`：通过。`node --check src/photo_search/web/persons-albums.js`：通过。`git diff --check`：通过。
- 用户随后明确指定“个人项目、在当前会话审查、可以简单点”。据此将本任务验收限定为合成/定向个人功能闭环，并在当前会话逐项复核 SQL 资格门禁、唯一候选、照片去重、分页/过滤游标、只读性、旧接口、展开和 rematch 双续批；未发现本任务阻断缺陷。此为用户授权的本会话简审，**不是独立 DS-H 审查结论**。
- B 级命令首次收集失败：`uv run pytest -q tests/unit tests/integration --junitxml=.pytest-tmp/person-inbox-full.xml` 缺少 `PYTHONPATH=.`，并发现已跟踪的 `tests/integration/test_p4_02y_l_identity.py:824` 含字面量 `\n`，触发语法错误。
- 随后使用 `$env:PYTHONPATH='.'; uv run pytest -q tests/unit tests/integration --ignore=tests/integration/test_p4_02y_l_identity.py --junitxml=.pytest-tmp/person-inbox-full-except-preexisting-syntax-error.xml`，完整执行其余单元/集成套件，退出码 1：**2168 tests, 48 failures, 43 errors, 49 skipped**。新增收件箱用例通过；失败/错误集中于现有地点目录、旧 schema/伴随契约、OCR/GPU DLL 及其它非本任务范围。测试还记录一次 ONNX Runtime CUDA DLL Windows fatal exception，pytest 继续至结束。JUnit XML 在下一次 pytest 使用同一 `.pytest-tmp` basetemp 时被清理；聚合值由当轮终端输出记录。全量 B 门槛未通过，不据此标 PASS。

## 未验证与边界

- 尚无真实浏览器手工验收；前端展开通过 Node 行为夹具和 API 合成用例验证。
- `catalog_filter_revision` 不一定因所有机器建议变化而递增，因此不同页面间并发自动归组变化不构成快照保证；刷新会重读并重排，报告不宣称并发滚动快照。
- 未访问或修改真实 `PhotoSearchData`、照片库、用户服务或模型；未新增 schema、索引、模型任务或持久分组。
- 本会话简审未覆盖真实浏览器、真实图库或生产规模；本任务不声称这些范围通过。用户指定的个人验收不要求额外 DS-H/Sol 轮次；本功能只读且未改人工身份/事务/撤销语义。

## 下一步

本任务按用户指定个人范围收口。全仓既有失败和语法问题已记录，不扩大为本次修复范围；只有用户提出具体反馈时再开新任务。不提交、不推送；保留工作区内先前无关的未跟踪清理材料。
