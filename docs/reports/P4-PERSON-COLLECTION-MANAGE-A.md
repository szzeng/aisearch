# P4-PERSON-COLLECTION-MANAGE-A 实施报告

日期：2026-10-10。基线：`3b43b1c`。状态：**IMPLEMENTED / 任务卡定向验证通过；独立 DS-H 审查、现场试用待完成**。

## 实现

- `GET /api/v1/persons/browse` 新增 `collection_state=all|none`，默认 `all`。`all` 响应为每个当前页人物增加 `collections: [{collection_id, name}]`；项目查询限定到当前页 canonical 人物及其任意深度合并来源，按 `collection_id` 去重排序，不依赖当前前端只加载的合集首屏。
- `none` 在 SQL total 和两种列表排序/分页前排除任意现存合集中的 canonical 人物。过滤与 `kind/q/visibility/quality_view` 共同执行。专用游标含排序/人物过滤 binding、现有 filter revision 和 SHA-256 合集版本摘要；摘要源为按 ID 排序的 `(collection_id, instance_token, revision)`。合集版本与 identity/filter revision 均在读取前后复核，避免同一请求混合两种成员或身份状态。默认 `all` 游标 binding 未变。
- 人物页搜索框下增加“只看未加入合集”。命名、未命名、已忽略人物列表共用开关；待整理无 person_id 的脸没有进入人物查询。切换筛选后只重新读第一页、清空跨页人物选择并提示，保留批量模式；详情返回维持复选框值。人物卡只读显示当前合集标签，长名自动换行并提供 title/aria-label，无合集不增加标签区域。
- 成功批量加入后仍使用现有 `loadPeople` 页回放刷新，筛选开启时由服务端过滤已加入项；失败保留原列表和选择。没有修改 schema、migration、成员写 API、识别、人脸人工状态或 undo 协议。

## 验证

使用隔离合成 catalog，无真实图库或运行中服务访问。执行：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync python -m compileall -q src/photo_search/persons/albums.py src/photo_search/persons/collections.py src/photo_search/api/routes.py
uv run --no-sync ruff check src/photo_search/persons/albums.py src/photo_search/persons/collections.py src/photo_search/api/routes.py tests/integration/test_p4_person_collection_manage.py
node --check src/photo_search/web/persons-albums.js
uv run --no-sync pytest -q tests/integration/test_p4_person_collection_manage.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_small_fixes.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_sort_ui.py --basetemp=.pytest-tmp/P4-PERSON-COLLECTION-MANAGE-A --junitxml=test-results/P4-PERSON-COLLECTION-MANAGE-A.xml
git diff --check
```

结果：**27 passed, 0 failures, 0 errors, 0 skipped**；Python 编译、Ruff、Node 语法和 diff-check 均通过。JUnit：`test-results/P4-PERSON-COLLECTION-MANAGE-A.xml`。

新增集成用例覆盖 85 人筛选及 40 人分页、标签空/非空与改名投影、名字/可见性/质量筛选组合、canonical 合并/撤销、加入/移出/删除后的 stale cursor 和未命名人物筛选；DOM 用例覆盖标签展示、批量加入后离开未加入列表、筛选请求、清选择、保留批量模式、关闭筛选显示标签及详情返回保留状态。旧 `all` cursor 兼容及既有写入/撤销基本语义由已运行的合集/人物回归覆盖。

## 审查与现场确认

- 用户要求在实施会话内完成审查。受影响面代码审查通过，审查中补齐加入、改名、删除合集后的当前页刷新与跨页面通知；详见[审查报告](P4-PERSON-COLLECTION-MANAGE-A-review.md)。本次未切换模型，不声称独立 DS-H。
- 用户报告已在浏览器测试并确认功能 OK。该结论来自用户现场反馈，本会话未连接正在运行的服务独立复测。
- 未运行全仓测试、浏览器矩阵、容量/GPU/模型/打包验证；未检查真实 PhotoSearchData、当前服务或真实照片；未提交/推送。
