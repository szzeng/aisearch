# P4-FAVORITES-CORE-A — 照片收藏核心、导航与收藏相册

- **状态**：READY / 任务卡已立项，尚未实施
- **优先级**：P1；**推荐模型**：GPT-6 High
- **编卡基准**：`szzeng/aisearch` `master` [`2e0e5509c329cddd36c01bee2e50fcb19b83895a`](https://github.com/szzeng/aisearch/commit/2e0e5509c329cddd36c01bee2e50fcb19b83895a)，2026-10-17；当前 `SUPPORTED_SCHEMA_VERSION=33`，已有 `0033_scale_read_indexes.sql`。**实际开工以届时 HEAD、工作树、migration 序号为准**，不得覆盖并行改动。
- **前置阅读**：`AGENTS.md` → `docs/development-status.md` → `docs/testing.md` → 本卡；针对直接调用链读 `P4-PHOTO-FACE-MANAGE-A`、`P4-UI2-VIEWER-UNIFY-A`、`P4-UI2-BATCH-SELECT-UNIFY-A` 的报告，以及现有图库清理/备份策略。
- **交付原则**：个人使用、一次完成核心闭环；测试按 `docs/testing.md` 的迁移/人工状态变更规则选取或复用可信证据；不强制真实图库、GPU、模型重跑或 30 万图库完整压测。

## 1. 需求与排除项

新增**针对完整照片 `asset_id` 的持久收藏布尔标记**。任意现有共享照片查看器都可以一键加入/取消收藏；导航栏「照片」下新增「收藏」，进入收藏照片网格，默认按**最近收藏**排列，并可自动续页。点收藏照片仍打开原共享查看器，关闭后保留列表范围和合理滚动位置。

收藏不等于人脸、人物、人物合集、待整理状态或未来「保存搜索结果」照片合集。多路径对应同一资产共享收藏；**路径相同但内容变化产生新 `asset_id` 时不继承**；离线、扫描更新、重新识别不清除原收藏。**不修改图片、EXIF、OCR、向量或人脸结果。**

本卡**不做**：缩略图上的全站星标、普通/搜索列表批量收藏、收藏的拍摄时间排序/时间轴、收藏范围语义搜索、AI 自动收藏、评分、导出。上述适用部分由 `P4-FAVORITES-UX-B` 承接，但 A 可独立使用且不等待 B。

## 2. 已确认代码事实与落点

- `src/photo_search/web/index.html`、`search.html`、`persons.html`、`places.html`、`map.html` 各自维护左侧导航；主应用是 `/`，图库管理已有 `/?view=sources`；`app.js::handleHash/showView` 管主页视图及 `data-shell-page` 高亮。
- `web/photo-info.js::AIPhotoInfo.open()` 已被首页/搜索/人物/地点/地图复用，最新版本刚加入**照片内人脸管理**；其 `photo-viewer-heading-actions`、`setAsset()`/`generation`、`beforeClose`、Back、完整详情返回均为现有合同。收藏仅加独立标题栏操作，不重写 viewer。
- `web/app.js` 照片浏览默认每页 50，已有 `buildTile`、`PhotoScrollPager`、缩略图懒加载、列表回收/返回锚点。优先复用**同一个照片浏览视图和网格**，不要另建一套复制的相册框架。
- `catalog/db.py::Catalog.write/read_snapshot` 管短事务，`api/queries.py::summarize_asset/locations_by_asset`、`catalog/image_policy.py::photo_asset_sql` 已是读侧构件。`0033_scale_read_indexes.sql` 已提供规模索引，不能回退。
- `library_retention.py` 有「未知资产引用类型」「人工信息保护」检查，新增收藏表后必须同时补齐白名单与人工引用判定。

## 3. SQLite schema：单条连续迁移、独立修订

当前基线建议新增 `src/photo_search/catalog/migrations/0034_asset_favorites.sql` 并升级 `SUPPORTED_SCHEMA_VERSION`；若开工出现后续迁移，**顺延至下一连续号**，绝不修改旧 migration。

```sql
CREATE TABLE asset_favorites (
  asset_id INTEGER PRIMARY KEY
    REFERENCES assets(asset_id) ON DELETE RESTRICT,
  added_at_ms INTEGER NOT NULL CHECK (added_at_ms >= 0)
);
CREATE INDEX idx_asset_favorites_recent
  ON asset_favorites(added_at_ms DESC, asset_id DESC);
INSERT INTO metadata(key, value) VALUES ('favorites_revision', '0');
```

- `favorites_revision` 使用**独立的 `metadata` 键**：单调非负整数；与收藏插入/删除**同一写事务**，整次真实状态变化仅递增一次；幂等 no-op 不递增。不创建第二套「收藏状态真值」或多余状态表。
- **禁止提升** `catalog_filter_revision`、地点/人物修订、搜索会话/时间轴 revision，也不能因为收藏写入调用搜索投影刷新、向量 outbox 或模型任务。
- schema 迁移必须保持既有 33 版资产、人物、地点、合集、索引记录不变：无全表回填、无重建原表。已有新版库启动不重做迁移；旧版程序遇到新版库延续 `SCHEMA_TOO_NEW`，不承诺回退兼容。
- 收藏是一项人工长期记录：`library_retention.py` 将 `asset_favorites` 列入已知表，并以 `asset_id` 有记录为「人工收藏」保护理由。离线及零位置资产不自动删除收藏；真正删除资产的策略留给单独清理任务。已有 Catalog 备份/恢复应完整带上表与 metadata 修订键。

## 4. 轻量收藏服务与 API（具体契约）

新增小型 `favorites.py`（或 `favorites/`），不得把业务 SQL 散放在 `routes.py`。沿用统一 session、Origin、CSRF 与结构化错误处理、现有 Catalog writer。

1. `GET /api/v1/favorites?limit=50&cursor=...`
   - 固定按 `(added_at_ms DESC, asset_id DESC)`，从 `idx_asset_favorites_recent` **驱动** Keyset 分页；仅列 ACTIVE 且满足现有 `photo_asset_sql` 的图片，保留原图缺失/离线的表现；去重基于 `asset_id`。
   - 使用已存在的资产摘要、批量文件位置读取、缩略图协议；明确声明 `FavoriteListResponse`（`assets` / `next_cursor` / `has_more` / `favorites_revision`），每条项可附 `favorited_at_ms`，不要破坏已有 `/assets` response model。`limit` 有界，默认 50。
   - Cursor 带专用前缀、排序位置、`favorites_revision`。有效成员/顺序发生变化后旧游标返回明确 409；前端提示刷新，**不拼接不同版本**。不为了每页渲染做全量收藏数统计。
2. `GET /api/v1/favorites/states?asset_ids=1&asset_ids=2`：1～100 个**正整数、去重** ID 的批量状态查询；固定响应 `{favorite_asset_ids: [...], favorites_revision: N}`（返回的 ID 必须是输入 ID 子集），未在集合中的请求 ID 即为未收藏。严格拒绝非法输入。A 的单 viewer 也用该接口，B 按页复用；**不许每图一个 SQL/HTTP 请求**。
3. `PUT /api/v1/assets/{asset_id}/favorite` 与 `DELETE /api/v1/assets/{asset_id}/favorite`：显式设定目标状态，**不提供非幂等 toggle**。同一请求重复执行返回 `changed=false`，保留原 `added_at_ms` 与修订；有效增删一次短事务完成，响应含 `asset_id/is_favorite/changed/favorites_revision`。未知或非 ACTIVE 图片返回确定的 4xx；允许已登记但暂时离线的图片收藏。数据库错误不得假报成功。
4. 写超时/连接中断导致结果不明时，客户端须重新获取该 ID 权威收藏状态后再恢复按钮；不使用无保护的「自动反向 toggle」。前端请求不能绕过 session、Origin/CSRF。

B 卡将新增批量写入接口；A **不为未使用的批量操作预写复杂协议**。

## 5. 页面与共享查看器（实现路线收紧）

**导航与视图**：五个现有 HTML 的主导航在「照片」后面新增 `☆ 收藏`，使用 `/?view=favorites` 与唯一 `data-shell-page="favorites"` 高亮；地图仍有独立布局。**推荐在 `index.html` + `app.js` 的现有 `view-browse` 上扩展 `scope=all|favorites`，复用同一 `#asset-grid`/`buildTile`/分页器与返回机制**，而非新建第六份 `favorites.html` 或复制两份 DOM。`handleHash` 解析 `?view=favorites`，`showView` 及导航高亮同步按 scope 更新；从收藏返回「照片」时恢复正常模式。收藏页显示「我的收藏」、空态、最近收藏序，**隐藏普通照片的时间轴/图库/高级日期控件**，不能继承其筛选和 cursor。进入收藏范围才发送收藏查询，离开后停止续页；禁止收藏页触发全局“搜索此图库”按钮的旧 source 范围。

**共享弹窗**：在 `photo-viewer-heading-actions` 与「完整详情」「关闭」并列加星标：未收藏/已收藏/保存中/失败、`aria-pressed`、中文 accessible name。状态加载或写入时按钮状态明确；点击只写当前 `asset_id`，不影响图片缩放、详情、人脸整理、前后切图、焦点与 Esc/Back。`setAsset()` 切图**使用 generation 与当前 ID 双重验证**迟到状态及写响应，防止 A 图响应改 B 图按钮。写成功后在当前页面更新可见收藏状态；在收藏视图取消收藏时，可以重新加载当前收藏结果或原位移除，但必须重置失效游标并保持合理滚动锚点，绝不能继续使用旧修订号翻页。

**详情返回**：从 `/?view=favorites` 打开 viewer → 「完整详情」→ 关闭/Back，应返回原收藏范围及合理位置。`photo-info.js::DETAIL_RETURN_PATHS.home` 已允许同源 `/` 的 query；复用其 existing handoff，不要另建一套历史协议；保存并恢复收藏 scope，不能让 `handleHash` 把它误判为普通照片。`/#/asset/{id}` 旧深链及搜索/人物/地点/地图返回路径保持不变。

视觉仅复用现有变量、局部 CSS 和真实引用静态资源的 cache-bust，不更改 UI 2.0 全局排版。

## 6. 性能与一致性底线

收藏写入仅触及新增小表 + 独立修订元数据；没有模型/GPU/缩略图重建或搜索重排。读收藏列表必须**先按收藏索引限定候选，再 JOIN 资产**，不能扫描 30 万 `assets` 后筛选或把所有收藏读进 Python 排序；单页约 50，使用有界批量位置读。通过合成 SQL `EXPLAIN QUERY PLAN`、调用计数证明「索引驱动、无每图 N+1」即可，不以此冒称 30 万规模压测通过。

## 7. 验收、交付与停止

按 `docs/testing.md` 判定测试范围：迁移和人工状态读写属于高风险边界，覆盖升级/拒绝/事务故障；如已具可信完整基线可用基线＋增量，否则按文档触发相应完整 unit/integration，不为追求“简单”而虚报全仓通过。**不要求真实图库、GPU、模型或 30 万数据压测。**

重点用例：① v33→新版、重启、旧版拒绝、新增空表不动既有数据；② PUT/重复 PUT/DELETE/重复 DELETE、离线、多路径、新内容新 ID、非图片/未知 ID；③ 相同毫秒时间、分页/409/刷新、索引路径、批量状态读取；④ session/CSRF、失败/超时权威重读；⑤ viewer 快速切图/人脸操作/Back/完整详情，收藏 scope 与滚动恢复；⑥ retention 保护、备份恢复；⑦ 隔离合成图库浏览器抽查「收藏→左侧收藏→取消→返回」，不可用明确 `BROWSER_PENDING`。脚本/JS 语法、受影响 Ruff、diff-check 按规则执行。证据归 `test-results/` / `.tmp/`，报告写实测结果与未验项。

**仅提交本任务实际源码、连续迁移、受影响测试、`docs/reports/P4-FAVORITES-CORE-A.md` 与必要开发状态更新。实现并验收后停止，不自动执行 B。**
