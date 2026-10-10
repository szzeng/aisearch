# P4-PERSON-COLLECTION-COUNT-A — 合集列表与照片页显示照片数

日期：2026-10-11。开始基线：`a83ffac`。状态：**PASS — 合集照片数展示与定向验证**。期间仓库提交 `387162a` 收口 GC-B；本任务改动保留为该 HEAD 上的未提交 diff。推荐 GPT-6 High；个人相册只做这一项小闭环和直接验收。

## 目标

人物合集列表在现有成员人数之外显示当前合集的照片总数；进入合集照片页时也显示同一照片总数。按合集照片流实际范围计数：有效 canonical 人物的有效 `CONFIRMED` 照片关系、非 `MANUAL_UNASSIGNED`、非活动排除、资产 `ACTIVE`；照片按 `asset_id` 去重，合照只算一次。人物单人相册标题已显示 `asset_count`，不改变其语义。

## 实施范围

- `src/photo_search/persons/collections.py`：合集列表为当前返回页批量投影 `photo_count`，避免每个合集单独查询；合集照片页响应的 `collection` 增加一致的 `photo_count`。
- 优先复用 `persons/collection_sql.py::collection_condition` 或从相同的 `MEMBER_PREDICATE`、canonical 关系构造页级批量计数；计数与 `GET /persons/collections/{id}/photos` 的资产集合完全一致。列表计数按页内 ID 有界批量查询，不扫描原图，不全量载入 asset ID 到 Python。
- `src/photo_search/web/persons-albums.js`、`src/photo_search/web/persons.html`：合集卡和合集下拉项保留成员人数并显示照片数；合集照片页标题/说明显示总照片数。空合集明确显示 0，不与已加载张数或分页数混淆；合集改名后保留已知照片数；更新脚本 cache-busting query。
- 扩展直接的合集集成测试并新增窄范围 UI 单元测试，验证跨成员合照去重、无效建议脸排除、空合集、分页总数、新确认照片加入后的计数更新及改名后显示保持。
- 更新本卡、实施报告和 `docs/development-status.md`。不改 migration、写操作、身份/合集成员协议、存储 GC 文件；不提交/推送。

## 必须保持

- canonical 合并来源照常归入当前人物；多成员共享一张照片时只计 1。
- 计数只投影到当前返回的合集页，不能按每个合集发起 N+1 查询。
- 照片流和展示计数使用相同有效成员、活动资产定义；当前页 `assets` 与 `photo_count` 的概念分别是分页内容和完整总数。
- 保持 collection instance token、collection revision、catalog filter revision 和现有游标拒绝行为不变。
- 不接触真实 PhotoSearchData、原图、模型/GPU 或未提交的 P4-STORAGE-GC-B 修改。

## 阅读与验收

已检查完整 V3.0/V3.1（本卡生成于原任务卡缺失时）、合集原卡 §1/§3/§4/§5、现有合集 SQL/API/UI 和相关测试。验收按 V3.1 §3.2 A 级：直接相关 pytest + Ruff + `node --check` + `git diff --check`，pytest 使用项目 `.pytest-tmp` 并生成 JUnit XML。定向文件：`tests/integration/test_p4_person_collection_manage.py`、`tests/integration/test_p4_person_collections.py`、`tests/unit/test_p4_person_collection_count_ui.py`。不得跑全仓回归。

JUnit：`test-results/P4-PERSON-COLLECTION-COUNT-A.xml`，10 passed / 0 failed / 0 skipped；Ruff、Node 语法、diff-check 通过。有环境错误时按仓库规则记录并走正常权限路径，不改 ACL。

## 交付及停止点

报告记录计数 SQL/边界、JUnit 实际统计、检查结果、一次未通过的相邻旧 UI 测试及未验证范围。未访问真实图库、未独立浏览器验收。本任务后下一任务仍为 `P4-TEST-BASELINE-LITE-A`；不自动扩展计数排序、筛选或其他相册页面。
