# P3-05C — P3-05 人物拆分与通用撤销独立 C2 审查报告

- 日期：2026-09-20
- 审查模型：C2 / GPT-5.6 Sol / high
- 审查起点：`cbf943714d80a3b21e1385df4c9799052582756c`
- 被审实现基线：`12a14a1`（P3-05B；其后文档/测试收尾见 P3-05B 报告）
- 结论：**PASS（发现缺陷后完成最小修复，并重新通过 A + D + C）**
- 数据库：`schema_version = 13`；未新增或修改 migration

## 1. 范围与总评

本次独立复核 ADR-0008、机器可读 companion、0013 迁移、拆分/共享撤销实现、API、派生发布与
P3-05B 测试证据。审查发现 4 类确凿实现缺陷，另清理 1 个错误详情的非契约字段；修复均使实现回到
已冻结语义，没有修改 ADR-0008、companion、`0001`～`0013`、依赖、打包配置或前端。

修复后，80 个 P3 人物拆分定向/交叉回归用例全部通过；完整 unit/integration 共收集 1185 项，
0 failure/error、48 项按环境显式跳过。真实 onedir 重建、新 D 盘中文/空格目录中的打包 API smoke、
bundle migration 连续性与 0013 hash 也通过。
因此 P3-05 人物拆分闭环可以关闭。

## 2. 发现与最小修复

### F1：拆分覆盖人工 link 时可能丢失其 revision floor

`_move_face()` 先闭合旧 `MANUAL/CONFIRMED` link，再查询同 key 最大 revision；被闭合行因此已不可见。
如果该行 revision 很高、新人物此前又没有该 face 的历史 link，新 link 会错误地从 revision 1 开始。

修复：闭合有效 link 前先捕获全部被替换行的最大 revision，再与目标 key 的历史 floor 合并；新增
revision 41 的人工 link 拆分后必须得到 revision 42 的断言。这只落实 ADR-0008 已冻结的严格向前要求。

### F2：撤销恢复 link 时没有把当前有效替代 link 纳入 floor

`restore_control_bundle()` 只比较快照目标与幸存的同 key 行，未必包含拆分后当前有效的新人物 link。
补偿恢复 source link 时，其 revision 可能与仍然存在的替代 link 相等，破坏跨控制变更的严格向前性。

修复：恢复 floor 同时包含快照 revision、当前有效 assignment revision 及该 face 全部幸存 link 的最大
revision；既有拆分撤销断言由 5 校正为 6，并继续验证 control/link revision 都只向前。

### F3：撤销 item 顺序校验允许非连续 index 绕过

`_validate_split_items()` 只排序并检查 family/角色/face 顺序，没有要求 `item_index` 必须恰好为
`0..n-1`。把一个 index 从 2 改为 3 后，旧实现仍会执行撤销，违背固定 item 数量/顺序的防篡改边界。

修复：在解释任何 item 前先要求 index 非负、唯一且连续；gap 以安全 reason `item_order` 整批拒绝。
新增“篡改 index 后零写”的集成测试。

### F4：拆分响应 OpenAPI 没有闭合额外字段

`PersonSplitOperationResponse` 及其嵌套 `SplitGroupItemResponse`、`FaceOperationItemResponse`、
`FaceExclusionChanges` 未统一声明 `extra="forbid"`，生成的四个 schema 缺少
`additionalProperties: false`，与 companion 的闭合响应不一致。

修复：仅给这四个 response model 增加 Pydantic `ConfigDict(extra="forbid")`，并增加 OpenAPI 断言；
没有增加、删除或改名任何响应字段。

### F5：撤销拒绝详情多回显一个未冻结字段

拆分 undo 的 `SPLIT_OPERATION_CONFLICT` 详情额外携带 `status`。它不是敏感值，但不在 companion 的
允许详情集合中。

修复：删除该字段并增加负向断言，错误码、HTTP 状态和具名 reason 不变。

## 3. 十项审查结论

| # | 审查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 迁移 0013 | PASS | 0013 只重建两张 operation 表；真实 v12→v13 含 FACE_CONTROL、UNDO 与 alias 引用逐列保留；错误形状 CHECK、PERSON_BUNDLE revision、备份、单事务、损坏回滚、缺件与过新拒写均由专项覆盖。源码/bundle 0013 SHA-256 同为 `9DEA2D67726A61A958AF57CCE7DFBEA6A433DC7679C406320F2E5DD2E1CCBDE4`。 |
| 2 | 资格矩阵 | PASS | canonical source、旧 ID、cycle、empty/duplicate/501、source/control/assignment stale、非成员及最后成员拒绝均有明确 code/reason 与零写断言；request_id 门早于实体/revision。 |
| 3 | 人工事实与不变量 | PASS（修复 F1） | 所选脸恰好 MANUAL_ASSIGNED + 单一 MANUAL/CONFIRMED，AUTO 仅降级为 suggestion，source exclusion 激活，siblings/其他人物/图片不变；高 revision 被替换行也进入 floor。 |
| 4 | 新人物身份 | PASS | 新人物无 name/name_key/alias/redirect，revision 1；头像只从有效成员选且不额外递增；注入失败时 person ID 随事务回滚。 |
| 5 | 幂等与 payload | PASS | digest 使用冻结字段与排序 face 三元组；同 key 同 payload 在 source 前进/face 删除后仍 replay；异 payload、无效选择及被拒请求均零写且不泄露 payload/hash。 |
| 6 | 撤销冲突矩阵 | PASS（修复 F3/F5） | 六个具名 reason、rename/merge/face edit/second split/effective-member 冲突、suggestion-only 与不相关改动、禁止 undo an UNDO 均有行为断言；item family/角色/数量/连续顺序不可绕过。 |
| 7 | 补偿语义 | PASS（修复 F2） | control/link/person revision 严格向前；source 成员/头像恢复；created 不删除而清头像并 redirect；旧 created ID 可解析到 source；exclusion 只切 active。 |
| 8 | 权威→派生 | PASS | changed 操作最多 +1 filter revision，复用 PERSON_CHANGED 并标记 face repair；replay/拒绝零副作用；不刷新图片 projection，不写 image index_outbox，不改 embedding/space/generation。 |
| 9 | API/错误/隐私 | PASS（修复 F4/F5） | 唯一 split 与共享 undo envelope、HTTP/错误闭集、session/Host/Origin/CSRF 均有 API 测试；四层 split response schema 闭合；响应/错误不含路径、姓名、几何、向量、相似度、幂等原文或内部快照。 |
| 10 | 测试质量 | PASS（补强） | 用例比较数据库行、revision、operation/item、queue、候选、outbox 与回滚快照；没有吞异常或只断言“未报错”。本次增加高 revision、item gap、OpenAPI closure 与错误详情反例。 |

## 4. 七组独立复现覆盖

任务卡要求的 7 组均由本次 80 项定向批中的迁移、生命周期、撤销、API 与交叉回归独立执行：

1. 含 undo/alias/FACE_CONTROL 引用的真实 v12→v13 逐列保留，备份拒绝 SPLIT；
2. request_id 同 payload 在 source revision 前进、face 删除后 replay；异 payload 零写冲突；
3. source/control/assignment 三种 stale 分别零写并只回显安全 ID/revision；
4. 成功拆分后的两组成员、头像、排除、filter revision、queue/candidate 精确断言；
5. rename/merge/face edit/second split/新增 CONFIRMED 成员阻止旧 undo，suggestion-only 与不相关改动不阻止；
6. 撤销后 created 保留并 redirect，control/link revision 向前，index_outbox/embedding/space/generation 不变；
7. 人物/头像阶段与派生阶段故障注入均整事务回滚，同一 request_id 后续仍可成功。

此外，本次用临时数据库探针先独立复现了 F3：旧代码在 item index 为 `0,1,3` 时仍返回
`undo_succeeded=true`；修复后相同篡改整批拒绝且零写。临时探针未留在仓库。

## 5. 验证记录

| 验证 | 结果 |
|---|---|
| P3 拆分定向/交叉回归 | exit 0；JUnit `tests=80 failures=0 errors=0 skipped=0`，3.827 s；`test-results/p3-05c-focused.xml` |
| 完整 unit/integration | exit 0；JUnit `tests=1185 failures=0 errors=0 skipped=48`，185.861 s；`test-results/p3-05c-full.xml` |
| 完整套件跳过项 | 23 项缺 buffalo_l 权重、19 项缺 PP-OCRv4 权重、5 项缺 P0-03 模型 artifact、1 项未启用 `PHOTO_SEARCH_GPU_TESTS`；均为显式环境 skip，P3-05C 定向批 0 skip |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0 |
| `uv run --no-sync uv lock --check` | exit 0，resolved 95 packages；依赖未变 |
| C 级 onedir | `uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` exit 0；Python 3.12.10、PyInstaller 6.22.2；真实重建完成 |
| 打包 API smoke | 新目录 `D:\PhotoSearchSmoke\P3-05C C2 中文 空格`；exit 0；`status=ok`、`problems=[]`、startup 1.41 s；session/credential rotation、split route、schema 13 与本地路径脱敏通过；`test-results/p3-05c-packaged-smoke.json` |
| bundle migration | `0001`～`0013` 共 13 个连续文件；源码/bundle 0013 SHA-256 一致 |

第一次受限环境中的 pytest 因既有 `.pytest-tmp` ACL 触发 `WinError 5`；没有修改 ACL。随后在已授权的
正常 Windows 权限路由中，仍使用项目内 `.pytest-tmp/P3-05C-*` 完成上表 focused 与 full 运行。

PyInstaller 保留既有 TensorRT DLL、tzdata 与 scipy hidden-import 警告；真实打包 smoke 成功，本任务没有
把这些非阻塞警告解释为 GPU 或缺失运行库已经可用。

## 6. 变更边界

生产最小修复：

- `src/photo_search/persons/split.py`
- `src/photo_search/persons/face_correction.py`
- `src/photo_search/api/models.py`

测试补强：

- `tests/integration/test_p3_person_split_lifecycle.py`
- `tests/integration/test_p3_person_split_undo.py`
- `tests/integration/test_p3_person_split_api.py`

未改：ADR-0001～0008、contract JSON、`0001`～`0013`、`schema_version`、`pyproject.toml`、`uv.lock`、
`PhotoSearch.spec`、前端、模型与真实照片数据。

## 7. 未验证与后续边界

- 当前 data root 没有 buffalo_l / PP-OCRv4 权重，本次没有下载大模型，也没有重跑 P3-05B 的真实 CPU
  检测→合并→拆分夹具；真实模型链路沿用 P3-05B 的既有证据，不冒充本次新证据。
- 未做真实多进程并发压力、500 face 性能基准或大规模人物库浏览；事务/revision 冲突由确定性集成测试覆盖。
- 未使用真实私人照片库；所有验证使用合成、迁移夹具和全新打包 smoke 数据目录。
- 新旧检测 `face_id` 的可审查映射仍属于 P3-08；人物页、脸框与真实浏览器交互属于 P3-06。
