# P4-PERSON-MERGE-DEDUPE-A — 人物合并后共同照片重复定位与最小修复

日期：2026-10-09。状态：**PASS — 合成回归与用户现场验收通过**。实际开工基线：`3fda522`（任务卡原基线已过期）。报告：[P4-PERSON-MERGE-DEDUPE-A](../reports/P4-PERSON-MERGE-DEDUPE-A.md)。正式 API 合并隔离用例中三页共 3 个唯一 `asset_id`，旧 ID 解析一致，撤销恢复 A/B 各自两张照片，共同合照的两张 `face_id` 均保留。用户随后在个人相册实际复测并反馈“没问题”。未改产品代码，新增合并分页/撤销回归测试。

## 1. 目标与已有事实

人物 A 有「合照 X + 单人照 A」，人物 B 有「合照 X + 单人照 B」，合并 B → A 后，在 A 相册应为 **3 张唯一照片**（X、A、B），不是 4 张。同一张合照中的多张脸仍然保存；它也可合理地出现在不同人物的相册。

源码已存在去重基础，**不预设根因为“后端缺 DISTINCT”**：
- `src/photo_search/catalog/migrations/0001_initial.sql`：`assets.sha256` 唯一，相同内容的多个位置归属同一资产；
- `src/photo_search/api/queries.py::asset_page`：`assets` 为查询主表，人物成员集合谓词来自 `search/retrieval.py::person_condition`；
- `src/photo_search/persons/albums.py::photo_page`：按 canonical 人物查询资产、以 `asset_id` 分页，并在 `_photo_page_facts` 给同一照片附多张成员脸；
- `src/photo_search/web/persons-albums.js::renderPhotos`：现有 `state.photoIds` 已跳过同一 `asset_id` 的重复卡片，但也会将同一张照片的全部 `person_faces` 小头像显示在卡片下方；
- `tests/integration/test_p4_person_albums.py` 已覆盖同一人物一张照片两张脸的计数、照片页及游标，但缺少「**两个人物有共同合照 → 真实 merge → 逐页读取 → 页面呈现**」的完整反例。

**先核对：用户说的“重复出现”是照片主卡重复、卡片下方脸头像重复，还是外观一样的不同资产。**

## 2. 唯一最小定位流程

复用隔离合成 fixture 建 A、B 与三张资产：X 中不同 `face_id` 分别归 A、B；另有 A/B 各一张单人照。经 **正式 `POST /api/v1/persons/merge`** 合并 B → A，不直接伪造 `merged_into` 充当端到端证明。

对比：
1. 合并前 A/B 照片列表、合并后目标 A 和旧 ID B 的 `GET /api/v1/persons/{id}/photos`，记录 `asset_count`、分页 `assets[].asset_id`、同一资产 `person_faces[].face_id`（仅隔离测试输出）；
2. `limit=1` 连续翻页，确认游标、`has_more`、全部资产唯一性；仅看第一页不算覆盖；
3. 仅在需要时用最小 DOM/手动页面检查，区分「两个大照片卡片」与「一张照片卡片下有两个脸头像」。若合成环境未复现用户视觉问题，保留“待现场确认”，不要用合成通过声称真实问题已解决。

## 3. 按诊断结果收紧修改

**A — 后端返回相同 `asset_id` 多次：** 只修 `persons/albums.py::photo_page` 或 `api/queries.py` 实际造成重复的语句/分页边界。照片唯一性必须在 LIMIT、排序及游标截断**之前**成立，不能取完页再丢弃重复导致漏页/错计数。复用既有 `MEMBER_PREDICATE`；不要改变人脸成员权威语义。

**B — 后端唯一、前端同一 `asset_id` 生成多个大卡片：** 只修 `persons-albums.js` 的请求过期、刷新、列表 ID 缓存或渲染位置；不要在数据库端进行“重复清理”。分页、详情前后切换、切换人物后状态不能串用。

**C — 仅是同一照片下有两个该人物的小脸头像：** 视为合并后两张不同有效脸的正常数据。若确为用户报告的视觉重复，最小 UX 调整为**主照片卡片默认只展示一张代表脸**，其余仍保留在原 API/照片详情供查看和更正。代表选择复用既有顺序，勿将第二张脸解除归属、勿删向量/历史，也勿暗示合并错误。

**D — 外观一样但 `asset_id` 不同：** 核对资产 SHA 与文件版本。如果是不同压缩/裁剪/编辑照片，本卡**不做**感知相似去重、资产合并或删除；明确记录现象及另立任务的必要性。如果 SHA 相同而资产 ID 不同则另行查数据一致性，不做危险的静默数据迁移。

允许改动只限直接命中根因的 `persons/albums.py`、`api/queries.py`、`web/persons-albums.js`、极少量对应页面样式/相关测试。**禁止**改 `persons/operations.py::_rewrite_links`、原始 `faces`、`face_person_links`、schema、历史迁移、人脸模型/门限、自动匹配、合并撤销协议，除非新证据明确证明错误源于该层，届时先记录升级边界，不私自扩大。

## 4. 最简单验收（2 项自动 + 1 项现场）

1. **正式合并反例**：共同 X + A/B 单照，A/B 合并后目标和旧 ID 均返回 3 个不同 `asset_id`、`person.asset_count=3`；X 中原两张有效人脸仍存在。若诊断为情况 C，再用一个小渲染检查确认每张照片只有一张主卡片、代表脸 UI 不多重突出。
2. **分页与撤销**：`limit=1` 翻完无重号/漏号、游标行为正常；正式撤销合并后 A/B 各自照片列表恢复，照片/人脸没有被删除。回归沿用 `tests/integration/test_p4_person_albums.py`，可扩展最少量断言。
3. **人工验收**：用户在个人相册重复原操作，确认所报告的视觉重复消失；如果真实现象无法复现，交付诊断证据并保持「现场待验证」，**不得称修复 PASS**。

执行相关定向 `pytest`、修改面的 Ruff、改 JS 才执行 Node `--check`、`git diff --check`。不做全仓、GPU、模型、30 万照片、重建库、打包或完整浏览器矩阵；不读取私人 `PhotoSearchData` 做自动测试。

## 5. 实施、审查与交付

推荐 **GPT-6 High**：同一会话完成定向复现、最小修复和两项小测试；只复核实际受影响的 API/SQL/前端及合并接口用例，非全仓工程主审。不与 [P4-PERSON-HIDE-A](P4-PERSON-HIDE-A.md) 批量忽略卡耦合实施。

交付内容：**重复类型和证据 → 改动点（若有）→ 两项定向测试结果 → 现场待验证事项**。不覆盖其他任务未提交修改。

**PASS** 必须同时满足：合并后照片卡片按唯一 `asset_id` 展示、数量和分页一致；同一合照的人脸数据保留；实际反馈的重复现象已确认解决。
