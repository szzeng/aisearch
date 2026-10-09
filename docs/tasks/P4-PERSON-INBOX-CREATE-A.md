# P4-PERSON-INBOX-CREATE-A — 待整理人脸一键创建未命名人物（不支持撤销）

日期：2026-10-09；实施更新：2026-10-10。状态：**IMPLEMENTED / 任务卡定向验证与当前会话工程复核通过；用户个人试用待确认**。立卡基线：`master` / `4440f746dcccaad4506887537711499dd20905c7`；实施前实际 HEAD：`b7e90614c74b34af8680b6fda2eb95b565e1ab8a`。schema 27 推进到 28。实施报告：[P4-PERSON-INBOX-CREATE-A-implementation.md](../reports/P4-PERSON-INBOX-CREATE-A-implementation.md)；独立复核卡：[P4-PERSON-INBOX-CREATE-A-review.md](P4-PERSON-INBOX-CREATE-A-review.md)。**个人相册，一张小卡，两组定向测试**。

推荐执行：**GPT-6 High**，一次实现 + 简短聚焦核查。本卡不提交功能代码，不操作真实 `PhotoSearchData`、不运行识别/建库。

## 1. 用户目标与取舍

在人物相册「整理一下 → 还没认出是谁」的单张人脸操作窗口「这是谁？」增加：

**「新建未命名人物（仅这张脸）」**

用户已经认定这是一个尚不存在的人，不应被迫先找一个已有的人物或等待自动聚类。点击后**只将当前 `face_id`** 放入新创建的未命名人物；同照片其他人脸、同候选组其他脸、同名候选及自动建议均不受影响。新人物之后正常支持命名、加入合集、合并和人脸更正；自动识别仍只按既有规则产生建议，不因创建而即时强制吸入相似脸。

**明确产品决策：这次“创建人物”操作本身不支持撤销，不显示快捷撤销条，不提供历史页撤销入口。** 错误归属可用现有「移出人物」「重新指定人物」更正，**但这些更正不等价于删除新建人物或恢复完整的创建前状态**。原有命名、归属更正、合并、拆分等撤销机制一律不改。

“不能撤销”只是产品操作语义：**请求失败仍必须 SQLite 整个事务回滚**，不得留下空人物、半条链接或孤立历史。

## 2. UI 最小改动

- 复用 `src/photo_search/web/persons-albums.js` 的 `editFace(face)` / `editBase({mode:"face"})` 对话框；在现有搜索/选择人物区旁新增明显但不过度抢眼的单独按钮 **「新建未命名人物」**，提示「只用这张脸建立人物」。
- 无候选时的「这是谁？」与已有候选时的「选其他人」**进入同一对话框**，均可看到新按钮；保留「确认是 XX」「选择已有人物」「不是这个人，移出人物」等原入口。
- 只在**非批量模式、非已忽略、当前未有效归属且可整理**的人脸显示/启用新建按钮。`MANUAL_UNASSIGNED` 但仍处于合法待整理队列的人脸允许显式创建。低质量脸若用户在现有质量入口明确打开并选中，也允许人工创建；不得自动忽略用户的显式决定。
- 点击无需第二次确认或输入名字，防连续点击，显示保存状态；成功关闭对话框并刷新人物列表与待整理列表，反馈「已新建未命名人物（1 张脸）」，有条件时可引导进入新人物相册。只刷新相关视图，不强制折叠无关批量选择/候选列表。
- **成功创建时清除之前遗留的 `state.operation` 和 `undoTimer`，隐藏快捷撤销条；不得把新建返回对象传给现有 `rememberOperation` 当成可撤销事件。** 其他操作的 10 秒快捷撤销照旧。
- 网络状态不明时，针对**同一次用户意图**复用同一个 `request_id` 重试，不重新生成以致重复新建。明确失败后再次人工发起可生成新 ID，服务端仍以状态/revision 阻止重复。
- 新建失败或 409 冲突时保留可理解的错误提示，刷新后允许重新选择；不得假报成功或自动改归已有候选人物。

## 3. 最小后端接口与原子行为

建议新入口：

`POST /api/v1/faces/{face_id}/create-person`

请求仅包含 `contract_version`（新功能专用版本）、`request_id`、`expected_control_revision`；**不接受 name、group_key、asset_id、其他 `face_id` 或候选人物作为建人依据**。

在 `persons/service.py` 内通过现有 `Catalog.write()` 的单一 SQLite 写事务：

1. 先校验 request 形状和全局 `request_id` 幂等：相同 ID/相同规范化 payload 返回原 `operation_id` 和**同一个** `new_person_id`、`replayed=true`（即使后来已更正或合并）；同 ID 不同 payload 零写拒绝。payload hash 覆盖接口族、`face_id`、期望控制 revision 和协议版本。
2. 新请求在事务内复核人脸存在，照片仍有效，`face_id` 符合与页面一致的 **待整理资格**：可见且未被替代、无有效 `CONFIRMED` 成员、不在重检测 `OPEN hold`、未人工忽略。使用现有 `persons/albums.py` 的成员/可见性语义，必要时抽出共享判定，避免复制出分叉规则；**不把 `clean` 质量显示阈值当作创建禁令**。
3. 精确校验 `expected_control_revision`；控制 revision 已变、已被另一请求归属或队列资格消失都必须 409/明确拒绝并零写，不允许客户端基于过时建议覆写归属。
4. 复用 `persons/split.py` 已有 `_create_person` 的安全创建语义：新 `person_id`，`name/name_key=NULL`、无别名、无合并指向、初始 revision=1，封面设为当前 `face_id`；**不得调用 `split_person`**，因为 split 必须有已经归属的 source。
5. 复用 `face_correction.apply_one(action="assign")` 的人工归属矩阵：当前这一张脸设置 `MANUAL_ASSIGNED` 并产生唯一有效 `MANUAL/CONFIRMED` 链接；保留对旧人物的排除语义与建议事实，不误改其他脸、图片或人工锁。必须在写事务内共享同一 `operation_id`。
6. 复用既有 `publish_derived`/`PERSON_CHANGED` 派生追赶，使搜索、人物列表和待整理数及时更新；不重算人脸 embedding、不触发即时全体重匹配。
7. 操作日志、新人物、人脸控制/链接和投影队列均成功才提交；中途失败全部回滚。提交成功仅产生一个人物及一张人工确认脸。

响应至少携带 `new_person_id`、`face_id`、`operation_id`、`operation_revision`、`status=COMMITTED`、`changed=true`、`replayed`、`catalog_filter_revision`；新旧重复请求响应遵守同一明确契约。

## 4. 操作历史：记录但绝不提供本操作的撤销

当前 `person_operations.type` 是 SQL 闭合 `CHECK`，不能假冒 `FACE_ASSIGN` 或 `SPLIT` 绕过类型限制，否则可能被旧撤销入口错误接收。

- 新增**唯一**类型 `CREATE_PERSON_FROM_FACE`，使用实施时连续的新 migration 扩展操作类型白名单；立卡时 `SUPPORTED_SCHEMA_VERSION=26`，若另一张 `P4-PERSON-OPLOG-GC-A` 已先实施升级，则选最新后继版本。旧操作及 `UNDO`/alias 引用升级必须完整保留，不改旧 migration。
- **仅记录必要的 `FACE_CONTROL` before/after item** 和 `person_operations.target_person_id=new_person_id`，用于历史摘要、`changed`、新操作的准确幂等重放及后续历史清理；**不新增 `SPLIT_GROUP` / `PERSON_BUNDLE` 的新建人物补偿快照**，因为该操作不需要撤销。
- `operations.operation_family` / `operation_result` 必须显式识别新类型并还原其响应，不能默认为可撤销的普通人物操作，不能因为只保存 `FACE_CONTROL` 而误走 person bundle 分支；历史列表应显示「新建未命名人物 · 不支持撤销」。
- 后端统一 `POST /person-operations/{operation_id}/undo` **针对该类型明确拒绝、零写入**；前端 `persons.js` 的历史操作页面不为该类型呈现「撤销此操作」。不修改其他类型的撤销行为。
- 创建人物的稳定 `person_id` 不得因后来移走唯一的脸而直接删除/复用。现有 `kind=unnamed` 已过滤空组；核对 `kind=all` 的选择列表，若会显示无成员、无名字的空人物，做最小只读过滤，不能出现无照片的幽灵选项。人物历史身份仍保留。
- 与历史清理卡 `P4-PERSON-OPLOG-GC-A` 兼容：本类型从创建起永久不可撤销，但仍遵守其 `request_id`/历史回执保留要求；不得用清理历史绕过幂等。

## 5. 代码范围（尽量少改）

- 前端：`src/photo_search/web/persons-albums.js`，必要时 `persons.html`/CSS；历史页 `src/photo_search/web/persons.js` 仅加新类型标签与禁用撤销显示。
- 服务：`src/photo_search/persons/service.py`、`face_correction.py`、`operations.py`、`contracts.py`；可增加短小的 `create_from_face.py` 封装，避免复制已有 split/correction 实现。
- API：`src/photo_search/api/routes.py` / `models.py`；沿用现有认证、Origin/CSRF、错误返回习惯。
- 数据库：一份连续 migration + `schema.py` 版本调整；必要定向测试。
- 禁止改人脸识别阈值、模型、聚类算法、真实图库、全局批量管理、既有撤销协议或全部人物操作历史清理流程。

## 6. 最少验收：两组

**A. 后端一组（合成 SQLite/API）**

- 待整理单脸创建→一个未命名人物、1 个 `MANUAL/CONFIRMED`、头像为该脸；同一照片其他脸、同候选组其他脸、建议、原图与向量不变。`MANUAL_UNASSIGNED` 待整理脸及用户明确选的低质量脸可创建。
- 同一 `request_id` 重放仍是同一个人物；不同请求或 stale revision 不能给同脸重复建人；有效归属、已忽略、旧替代脸、`OPEN hold` 及未知脸拒绝且零写；注入中途错误验证整个创建事务回滚。
- 历史列表/replay 可读且显示不可撤销；直接调用旧 undo API 对本类型必须拒绝且不变更任何记录；其他现有撤销仍正常；移走该脸后的空人物不混入普通选择列表。

**B. 前端一组（轻量 Node DOM）**

- 「这是谁？」及「选其他人」打开的对话框都有新建入口；点击只传一个 `face_id`；成功隐藏原有撤销条，刷新相关列表；快速双击和不明网络失败用同一 `request_id` 重试、不重复创建；409 失败不冒充成功。
- 保留原候选确认、选择已有人物、忽略/批量选择；已有历史操作撤销按钮不被改坏。

运行上述两组定向验证、相关 Ruff、`node --check`、`git diff --check`。用户自行用少量真实人物试用后确认。**不运行**全仓、30万容量、GPU、模型、真实图库扫描、重建库、打包与浏览器矩阵。

## 7. PASS 判据与实施说明

- 只有用户明确选择的一个 `face_id` 成为新人物的初始成员，其他脸绝不自动吸入；
- 新人物可正常命名、后续纠错/合并，人工归属保护与搜索同步正确；
- 操作**不可撤销**但**失败可事务回滚**，旧人物操作仍可正常撤销；
- 同一请求无重复人物，数据与历史重放均无失效；
- 简化实现，不新增另一套通用人物创建/撤销框架；两组最小测试通过。

实施完成需报告实际改动文件、定向测试结果、未验证范围。推荐 **GPT-6 High** 一轮实现并聚焦核查身份、幂等、迁移与旧撤销入口；无需默认全仓审查或多模型循环。

## 8. 实施结果与交接

- schema 28 / migration 0028 已实现，接入单脸原子创建、新操作族、幂等回执、撤销拒绝和前端入口。
- 后端及旧撤销回归 JUnit 40/0/0/0；迁移组 JUnit 13/0/0/0；前端轻量 DOM JUnit 1/0/0/0；受影响面 Ruff、Node、py_compile、diff-check 均通过。具体命令见实施报告。
- 当前为 IMPLEMENTED 与定向验证通过；用户要求的当前会话受影响面工程复核已通过（非独立 DS-H/Sol）。复核修复 schema 27 带 RENAME→UNDO 历史时的 0028 外键升级失败，修复回归 JUnit 16/0/0/0。用户少量真实人物试用待确认。未访问真实图库/当前服务、未跑全仓/浏览器矩阵/GPU/模型/打包。
