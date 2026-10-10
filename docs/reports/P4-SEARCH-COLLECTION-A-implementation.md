# P4-SEARCH-COLLECTION-A 实施报告

日期：2026-10-10。状态：**IMPLEMENTED / 任务卡定向合成验证通过 / 个人页面试用待确认**。

## 基线与实现

- 开始时 HEAD：`6c45557b516b9cb4b359d34f840e87b5a63821b3`（`master`）。任务卡立项基线 `0736b843...` 已前进；实现以当前实际代码为准。工作树原有 `.tmp/` 和 `docs/reports/tmp-*` 未跟踪文件均保留。
- 提取共享只读合集成员谓词至 `persons/collection_sql.py`，合集相册 API 保持原 SQL 语义；`SearchScope.collection_id` 在一个参数化递归子查询中解析 canonical 成员，并由统一 scope 同时限制 FTS、Vector 和 SQLite 权威页。没有展开成员 ID，也没有改变普通 `person_ids` 的 100 人上限。
- `/search/place-aware` 按合集保存名称做 NFC、空白规范和大小写不敏感的显式上下文解析，支持最长名称优先。人物/地点同名返回对象选择；多个合集命中也要求用户选择。未明确指向合集的普通文本保留原搜索行为。
- 纯筛选、纯地点和语义搜索均应用合集与已有条件的 AND。纯 SQLite 继续用 keyset 分页；合集＋地点复用地点范围；语义通道在 FTS/向量召回前受合集 scope 限制，Top5000 没有改变。普通 `/api/v1/search` 请求与回显契约未改。
- 查询计划绑定 `collection_id + instance_token + revision`。服务端每次请求重新校验实例、名称和 revision；首屏发布后再核验；续页绑定合集版本。筛选游标使用固定长度筛选摘要，避免新增合集绑定字段后超过 512 字符游标限制。前端显示可取消合集 chip，取消后保留其它条件；合集失效错误会清除旧结果。
- 未改 schema/migration、依赖、向量索引或人物身份/归属写协议；未访问或修改 `PhotoSearchData`、真实照片库或运行服务。

## 验证

最终收口命令（`PYTHONPATH=src;tests;.`，`UV_CACHE_DIR=.uv-cache`）：

```powershell
uv run --no-sync pytest -q tests/integration/test_p4_search_collection.py tests/integration/test_p4_person_collections.py tests/integration/test_p4_01c_retrieval_blocks.py -k 'not rewritten_predicates' tests/unit/test_p2_search_sessions.py tests/unit/test_p4_search_nl_parse.py tests/unit/test_p4_search_nl_ui.py tests/unit/test_api_queries.py tests/unit/test_p2_search_contracts.py tests/unit/test_p2_contracts.py --basetemp=.pytest-tmp/p4-search-collection-final --junitxml=test-results/p4-search-collection-final.xml
uv run --no-sync ruff check src/photo_search/persons/collection_sql.py src/photo_search/api/queries.py src/photo_search/api/routes.py src/photo_search/search/natural_intent.py src/photo_search/search/retrieval.py src/photo_search/search/service.py tests/integration/test_p4_search_collection.py tests/unit/test_p4_search_nl_ui.py
node --check src/photo_search/web/search.js
git diff --check
```

- JUnit：**81 tests / 0 failures / 0 errors / 0 skipped**，见 `test-results/p4-search-collection-final.xml`。
- Ruff、Node 语法检查、`git diff --check`：退出码 0。
- 新增合成覆盖：名称/长短名/普通词不误触发/多合集选择、人物与地点同名选择、年份与合集交集、地点与合集交集、合照去重、空合集、101 成员、小页/总数、FTS 与 fake Vector 候选限制、成员修订后的旧游标、错误实例令牌、首屏发布前成员变更拒绝，以及取消 chip 保留其它条件。`EXPLAIN QUERY PLAN` 检查合集成员谓词。
- 一次更宽的受影响面尝试 `test-results/p4-search-collection-review.xml` 有 5 个既有夹具失败：`test_p4_place_search_flow.py` 的 4 个用例无法装载其兼容上限为 schema 18 的旧合成地点包；`test_p4_01c_retrieval_blocks.py::test_the_rewritten_predicates_select_the_same_photos` 缺少 `face_detail_auto_assets` 表。最终收口重跑保留其余检索块节点并得到上述 81/81；没有把这 5 个夹具问题记作产品 PASS，也没有扩大任务去改无关夹具/协议。

## 后续语义合集回归修正（2026-10-10）

- 用户现场反馈语义合集查询返回 500：`query_hash()` 收到不支持的 `collection_id` 参数。移除摘要调用中的多余参数，合集实例与成员 revision 仍由 `album_scope_revision` 绑定；fake encoder/向量索引的 `search_request()` 回归确认合集外照片不会进入 Vector 候选。
- 用户随后指出“AI的裙子”不会进入 AI 合集范围。原因是入口解析器只把“合集/的照片”等后缀识别为明确合集引用，没有识别“合集名 + 的 + 内容词”。现在识别该表达，剥离合集名后将“裙子”保留为语义词。HTTP 路由回归断言合集 ID 与 residual query 都正确传入语义搜索。
- 两个回归节点均通过：`test_collection_parse_scope_paging_empty_over_100_and_stale_cursor` 与 `test_collection_scope_is_applied_by_fts_and_vector_candidate_walk` 各 1/0/0/0。按任务卡限定的组合命令重跑：JUnit 81/0/0/0，`test-results/p4-search-collection-user-regression.xml`。Ruff、Node `--check`、`git diff --check` 均退出码 0。

## 复核与未验证项

- 当前会话对本次全部改动、直接调用链、普通 `/search` 契约、版本绑定和定向证据做了聚焦复核；未进行独立 DS-H 或 Sol 会话，也不把本报告称作独立工程审查。
- 未做真实地点包/浏览器/真实图库试用、运行中服务重启、完整 pytest、30 万容量、GPU/真实模型、打包或发行验证。地点交集 API 测试使用隔离合成地点成员列表，证明共同 scope 的交集，不证明真实地点数据质量。
- 用户可在正常刷新后的个人页面少量试用：`同事的照片` → `2024年同事` → `同事在海边` → 详情返回 → 取消合集；现场结果仍待用户确认。

## 修改文件

- `src/photo_search/persons/collection_sql.py`
- `src/photo_search/api/queries.py`
- `src/photo_search/api/routes.py`
- `src/photo_search/search/natural_intent.py`
- `src/photo_search/search/retrieval.py`
- `src/photo_search/search/service.py`
- `src/photo_search/web/search.js`
- `tests/integration/test_p4_search_collection.py`
- `tests/unit/test_p4_search_nl_ui.py`
- `docs/tasks/P4-SEARCH-COLLECTION-A.md`
- `docs/development-status.md`
