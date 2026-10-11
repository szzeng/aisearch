# P4-PERSON-EMPTY-VISIBILITY-A 执行报告

**日期：** 2026-10-11 至 2026-10-12
**状态：** SCOPED PASS / 用户指定 LUNA High 审查路由例外（不是 DS-H）
**实现基线：** 执行前 HEAD `a66b0902379a31987805914de699fd746438464f`；复核时 HEAD 为 `0940fbcd8fea0010f60967f6063d065832aaec1d`
**任务卡：** [P4-PERSON-EMPTY-VISIBILITY-A](../tasks/P4-PERSON-EMPTY-VISIBILITY-A.md)

## 实施

- `persons/albums.py::people_page` 对 `all`、`named`、`unnamed` 共用活动照片资产及 `MEMBER_PREDICATE` 的 SQL `EXISTS` 过滤，沿用 `photo_asset_sql` 视频资格。它位于总数、排序和游标分页之前；未命名 `clean` 视图保留原过滤条件。
- `persons/read.py::person_page` 的三个 kind 都在 SQL 分页前要求至少一个活动、符合照片资格的有效成员。详情/相册 ID 读取和合并 canonical 解析未更改。
- 前端已有 `notifyPeopleChanged` / `refreshPeopleViews` 刷新；未改 JS 或引入新状态。
- 增补 0 照片合并到有照片人物的 API 回归，检查目标照片、旧 ID canonical 解析和两个人物列表入口。未改 Schema、合并实现、人工排除或撤销语义。
- 实现前工作区 `src/photo_search/geo/dataset.py` 的改动现已包含在 HEAD `0940fbcd`，不属于本任务 diff。

## 验证

最终命令：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --locked -- python -m pytest -q `
  tests/integration/test_p4_person_cleanup.py `
  tests/integration/test_p3_person_search_filter.py `
  tests/integration/test_p4_person_albums.py `
  tests/integration/test_p4_library_video_exclude.py `
  --junitxml=.pytest-tmp/P4-PERSON-EMPTY-VISIBILITY-A.xml
uv run --locked ruff check src/photo_search/persons/albums.py src/photo_search/persons/read.py tests/integration/test_p4_person_cleanup.py tests/integration/test_p3_person_search_filter.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_library_video_exclude.py
git diff --check
```

- pytest：JUnit **70 tests / 0 failures / 0 errors / 0 skipped**，退出码 0；XML：`.pytest-tmp/P4-PERSON-EMPTY-VISIBILITY-A.xml`。新增视频专属 ACTIVE 资产隐藏、图片+视频共用资产仍显示的检查。
- Ruff：通过，退出码 0。
- `git diff --check`：通过，退出码 0。
- 扩大测试范围时，旧断言发现并确认了命名空人物、仅人工移出人物和无有效照片人物仍被列表计数/排序的问题；按新契约更新了相应断言。首次合并夹具尝试重复绑定同一 face，修正为独立合成照片后最终全套通过。
- 没有改 JS，因此未运行 `node --check`。

## 未验证与审查门

- 未运行全仓测试、真实浏览器、真实 PhotoSearchData/图库、GPU/模型、30 万照片或打包验收。
- 用户指定 LUNA / High 子代理审查（路由例外，不是 DS-H）。首轮 1 个 P2 finding（未应用 `photo_asset_sql` 导致视频专属人物可能显示）已修复。修复轮确认原 finding **CLOSED**，新 diff 无直接回归；详见[审查报告](P4-PERSON-EMPTY-VISIBILITY-REVIEW-A.md)。
- 审查确认成员谓词、分页位置、clean、查询/合集过滤、排序游标、旧 ID 解析及空源合并到有照片目标无额外 finding；无身份/协议/数据安全未决，不需要 DS-MAX 或 Sol。该路由例外结论不代表 DS-H PASS。
- 当前会话模型和推理档未从执行环境暴露，故不声称实际切换；任务卡推荐 GPT-6 Luna / Medium。

## 下一步

本卡按任务范围收口。未验项仍包括全仓回归、真实图库/浏览器、GPU/模型、容量与打包。
