# P4-UI-TIMELINE-REVISION-A 实施与验证报告

日期：2026-10-13
结论：**IMPLEMENTED / 定向验证通过 / LUNA HIGH 最终修复复核无剩余发现**。

用户于 2026-10-13 选择“保持每文件事务各递增一次”。任务卡已同步此语义：当前 scanner 每个文件分别提交绑定，故每个真正新增的 asset 各增加一次 revision；一个事务内批量写入则只增加一次。该选择保留图库写入与 cursor 失效的同事务原子性。

## 范围与实现

沿用唯一权威版本 `catalog_filter_revision`，没有新增表、schema、API 或 cursor 协议。已接入：

- 新 asset 在 `bind_location_content` 确认内容的写事务内递增版本，原因 `ASSET_ADDED`；已存在 asset 首次绑定到新 source 时也在绑定事务内递增，原因 `ASSET_LOCATION_BOUND`。普通复扫未改变绑定时不递增。
- 文件 stat 变化导致位置解绑时，在 `observe_location_detailed` 清空 `asset_id` 的同一事务递增版本，原因 `ASSET_CONTENT_DETACHED`；稍后的哈希绑定是另一事务，按真实绑定变化再次递增。
- 删除 source 时，同一事务先取出受影响的图片 asset 集合，再删除 source 和位置并递增一次版本，原因 `SOURCE_PHOTOS_REMOVED`。
- 合集成员真实变化和合集删除在各自事务内递增一次，原因 `PERSON_COLLECTION_CHANGED`；重复 add/remove 不递增。
- 人物隐藏/取消隐藏有真实变化时在原事务递增一次，沿用 `PERSON_CHANGED`；no-op 不递增。人物合并及人物照片关系变更继续使用既有 `publish_derived` 共享入口。
- 地点归属、拍摄时间变化继续使用原有共享 revision 机制。

## 验证

任务卡定向测试：**14 passed / 0 failed / 0 skipped / 0 errors**。JUnit：[test-results/P4-UI-TIMELINE-REVISION-A.xml](../../test-results/P4-UI-TIMELINE-REVISION-A.xml)

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location).Path '.uv-cache')
uv run --no-sync pytest -q tests/unit/test_p4_ui_timeline_core.py --basetemp=.pytest-tmp/P4-UI-TIMELINE-REVISION-A --junitxml=test-results/P4-UI-TIMELINE-REVISION-A.xml
uv run --no-sync ruff check src/photo_search/catalog/store.py src/photo_search/ingestion/service.py src/photo_search/ingestion/scanner.py src/photo_search/persons/collections.py src/photo_search/persons/service.py src/photo_search/places/revision.py tests/unit/test_p4_ui_timeline_core.py
.\.venv\Scripts\python.exe -m py_compile src/photo_search/catalog/store.py src/photo_search/ingestion/service.py src/photo_search/ingestion/scanner.py src/photo_search/persons/collections.py src/photo_search/persons/service.py src/photo_search/places/revision.py tests/unit/test_p4_ui_timeline_core.py
git diff --check
```

Ruff、`py_compile` 和 `git diff --check` 均通过。定向回归覆盖扫描新增多张照片和每文件事务递增、旧 cursor stale、单位置变更导致解绑递增、重复位置解绑不递增、已存在 SHA 首次绑定到新 source、同一 source 重复内容不递增、合集成员变更/删除、人物合并、人物隐藏和 no-op、source 删除。没有运行全仓、浏览器或 30 万图库测试，符合本卡测试边界。

## LUNA High 复核

用户指定由子代理 LUNA High 做独立复核。首轮发现变更文件先解绑、后绑定的 revision 漏洞；后续复核分别发现同一 source 重复 SHA 绑定、以及同一 asset 仍有 source 内另一图片位置时变更一条位置会无意义递增。现已补上原子解绑、首次 source 可见时递增、同 source 重复绑定和重复位置解绑不递增，并新增反例回归。最终修复轮确认无剩余发现：解绑只在旧路径为图片且同一 source 内没有该 asset 的其他图片位置时递增；同源重复绑定保持 no-op。该复核按用户路由记录为 LUNA High，不称为 DS-H；复核者未运行测试。

Timeline CORE-A 的浏览器证据与 `PERF_200K` 仍 pending；本卡不替代 CORE-A 的关闭条件。

## 工作区说明

实现触及 catalog store、ingestion service、person service/collections、revision 常量和 Timeline 定向测试。开始时 `src/photo_search/web/photo-timeline.js` 已有用户未提交修改，本任务没有编辑该文件，也没有提交任何变更。
