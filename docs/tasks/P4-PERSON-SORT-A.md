# P4-PERSON-SORT-A — 人物相册按照片数量降序

日期：2026-10-09。状态：**READY / 未实施**。基线：`b325b0e24e8a097fe7a9ba5bcdd819ac6289c631`。个人图库的独立最小功能卡；**只调整人物卡片排序**，不重新建库、不重新识别人脸。提交本卡不代表功能已实现或测试通过。

## 1. 起点与目标

- 上一任务 [P4-PERSON-ALBUM-PERF-A](P4-PERSON-ALBUM-PERF-A.md) 已按个人基础范围 PASS：`persons/albums.py::people_page` 先以 `person_id` 选最多40人，再由 `_album_summaries` 对**当前页**做批量去重照片统计和头像投影；前端首屏头像优先加载。该优化不能被本卡无意回退。
- 当前 `GET /api/v1/persons/browse` 按 `person_id ASC` 分页，前端 `loadPeople()` 显示每人 `asset_count`，没有按照片数全局排序。`loadChoices()`（合并/指定人物选择）也使用同一 API。
- 用户目标：在「人物」及「整理一下」的人物分组列表中，**已有有效归属照片越多的人排得越前**；同数量以 `person_id ASC` 稳定打破平局。卡片显示数量与排序数量必须一致。
- 人物内部的照片仍按原 `capture_desc`，整理收件箱、合并/指定对象列表、名字搜索与人工操作原有语义不变；不增加排序开关。

## 2. 最小实现

### A. 仅为浏览列表增加可选排序

修改 `src/photo_search/persons/albums.py`、`src/photo_search/api/routes.py`，为 `/persons/browse` 增加受限 `sort` 参数：
- `id_asc`：原默认行为。**未传参仍按原 ID 顺序、旧游标继续有效**，保障其他调用者。
- `photo_count_desc`：新增全局照片数量降序，`person_id ASC` 为第二关键字。
- 未知取值明确拒绝（422），不要隐式接受或猜测排序值。

原 `kind=all/named/unnamed`、`q` 名字及别名的字面匹配、合并身份排除、空未命名人物过滤保持原语义；已命名且无照片的人仍保留、排到最后。

### B. 在服务器先全局排序，再 LIMIT

**不得**先取40个人物后仅对40人排序，也不能在浏览器加载全量人物。

- 对满足原 `kind/q` 的候选人物，基于共享 `MEMBER_PREDICATE` 聚合 `COUNT(DISTINCT f.asset_id)`，完成全局 `(asset_count DESC, person_id ASC)` 排序及 SQL 限页，包含零照片的已命名人物。
- 照片计数必须沿用当前 `persons/members.py::member_counts` 和 `_album_summaries` 的**既有口径**：同图多脸仅算一张、SUGGESTED/排除/手动移除/旧脸替代按权威谓词处理。不要在排序 SQL 另加与现有计数不一致的过滤条件，也不修改人物识别/归属。
- 取当前页后，**仅当前页**执行头像候选读取；尽量复用排序 SQL 已得的 `asset_count`，避免 `_album_summaries` 再次对本页重复计算照片数。其他单人摘要调用保持兼容。
- 先核对相关索引和 `EXPLAIN QUERY PLAN`；不新增 schema、持久统计表、背景刷新或全量照片物化。全局计数必然比原 ID 排序多做工作，不能把“仍保持4条SELECT”作为性能保证。

### C. 新排序使用独立 keyset 游标

原 `people1` / ID 游标路径保持不变；新模式独立前缀（如 `people-count1`），绑定 `sort`、`kind`、规范化 `q` 及当前已有适用的版本条件。

新游标携带最后一项的 `(asset_count, person_id)`；续页严格采用：
`asset_count < last_count OR (asset_count = last_count AND person_id > last_id)`。
必须覆盖分页边界、同数量、零照片及查询/排序混用拒绝；数据不变时不得重号、漏号。

核对人脸**自动**归属、补识别、新照片入库是否都会推进 `catalog_filter_revision`。**未经证实不能声称该 revision 覆盖所有排序数量变化**；若覆盖不足，不在本卡私加持久版本体系或复杂快照。发生并发建库/自动归组时允许刷新重排，文档如实说明滚动分页一致性边界；原已知 revision 失效继续按现有 409 处理。

### D. 前端只改人物主列表

`src/photo_search/web/persons-albums.js::loadPeople()` 请求显式增加 `sort=photo_count_desc`，包括「人物」和「整理一下」人物分组；保留40人/页、更多、名字搜索、刷新、首屏头像优先级及旧请求丢弃机制。

`loadChoices()` 不传新排序参数；不改变 API 无参数默认、人物选择弹窗、相册内照片排序、人脸整理收件箱，或同图人脸的展示行为。只有必要时修改前端资源版本。

## 3. 性能保护与停止条件

对照已通过的 [人物相册性能实现证据](../reports/P4-PERSON-ALBUM-PERF-A-implementation.md)，核对新旧排序 API 的 SQLite 计划、简单合成耗时和首屏不必要额外请求。

- 不恢复逐人 N+1 统计，不为排序获取全部头像、整张照片或执行预览生成。
- 若全局聚合在有限合成样本中已明显慢于可接受的交互、查询计划出现可避免的多次扫描，优先只修直接相关 SQL；无法在现有索引/无 schema 前提下合理处理时，记录数据与建议，**停止扩张**，不静默退回 ID 顺序冒充数量排序。
- 小样本测量仅作回归信号，不能宣称30万真实照片性能或用户实际冷/热头像速度。

## 4. 简单定向验收

仅用合成/隔离数据；不访问用户的 `PhotoSearchData` 或真实图片，不做全仓、GPU/模型、打包、30万规模压测。

1. **排序**：照片数10、5、5、0，确认降序/同数ID；已命名0保留、未命名空组仍隐藏；输出 `asset_count` 与排序值一致。
2. **全局与分页**：制造至少42人（跨40人首屏），把最大计数放在大 ID，首屏仍排首位；小页与40人分页无重复/漏人、跨页同数边界正确。
3. **计数**：同照片多脸、合照、仅建议、人工排除/移除、旧脸替代按原权威规则；名字/别名查询组合正确。
4. **隔离**：无 `sort` 的 API 与旧游标结果保持原语义；旧/新排序或不同 `q/kind` 的游标不能混用；`loadChoices` 顺序不变；打开人物相册/查看更多不受影响。
5. **性能**：复用 `test_p4_person_albums.py`、`test_p4_person_album_perf.py`，增加最小数量排序定向测试；查看实际查询计划及新旧 API 合成耗时，不编造达标阈值。

运行相关 pytest（JUnit XML）、修改面 Ruff、Node `--check`、`git diff --check`；一次小型 DOM 或合成 API 测试即可，不要求完整浏览器矩阵。此前全仓失败基线不借本卡修绿；测试报告分别记录定向验证与未验证项，不把个人范围通过写成全仓 PASS。

## 5. 边界、审查与交付

允许改动：`persons/albums.py`、`api/routes.py`、`web/persons-albums.js` 及相关测试/简短报告。不要改 `persons/members.py` 权威成员谓词、共享人物默认排序、face 模型/阈值、人工事务/撤销、图片缓存、schema/索引和照片页 `capture_desc`。

模型建议：**GPT-6 High**（或 DS-H／高推理），同会话实施和简单定向验证。由于新排序引入**独立分页游标协议**，收口至少由独立 DS-H 核对全部直接受影响的 SQL、API、前端调用及游标/权限/回归证据；“完整受影响面”不是全仓重审。若实际改到身份、事务、Schema 或高风险可执行契约，按 V3.1 §2.4/§11 升级审查，不在本卡自行扩大。

交付：简述变更文件、排序/游标实现、测试与查询计划证据、性能回退与未验证边界；必要时更新 `docs/development-status.md`。保留其他任务未提交的改动。**成功标准**：人物主列表按照片数全局降序，分页正确，原 API/选择器行为不变，不破坏刚完成的浏览加载优化。
