# P4-PERSON-NAME-CASE-A — 姓名大小写同名判断修复

2026-10-10。[实施卡](../tasks/P4-PERSON-NAME-CASE-A.md)、[ADR-0019](../decisions/ADR-0019-person-name-case-comparison.md)。当前：实现完成 / 用户指定相关验证与当前会话简要复核通过；个人试用待确认，不标总项目 PASS。未提交/推送。

用户最新明确要求：“个人相册，简单测试相关的测试就好了”。覆盖先前按默认 D 级追加全仓回归的安排，本次以姓名/合并/撤销/API/相册的相关测试收口，不扩展地点或其他模块修复。已有全仓尝试结果仅如实保留，不当作本次新增验收门槛，不再运行全仓或额外容量/GPU/打包测试。

## 原因与结果

前序小修将只读搜索改为 casefold，保存仍沿用 ADR-0006 的大小写敏感 `nfc1:` 精确查找。用户明确覆盖旧产品规则后，`identity.active_person_id_for_name_key` 在已有写事务内比较正式持久键的 Unicode casefold；SQL 只读取活动有名人物，排除当前 canonical ID，精确拼写优先、最低 person_id 次之，Python 比较避免 SQLite NOCASE 的 ASCII 限制。开销仅随活动有名人物数增长，不读取人脸、照片或向量；未做容量测量。

`operations.rename_person` 保持幂等重放先于 revision / 同名查询，复用原 `_apply_merge`，个人 `/label` 同名返回 NAME_IN_USE 整事务零写入，既有前端展示目标并要求显式确认；旧 `/name` 使用 MERGE_BY_NAME。无其他目标时，完全相同存储键仍为 no-op，仅修改当前大小写则普通改名。显示名字、nfc1 键、请求摘要、journal 快照及旧撤销恢复均保留原格式。旧大小写重复只有用户保存/显式合并时处理，不自动改历史库。

`contracts.py` 仅澄清持久键与同名比较的区别，原大小写保留规范测试仍有效。小修搜索测试的 Alice / alice 是直接种入的历史重复，搜索仍只读，不合并；补了夹具说明。schema 仍 28，未改 API shape / 名称校验 / 别名 / redirect / merge / undo 实现 / 模型阈值 / 向量。

## 验证记录

项目本地 `.uv-cache` 与 `.pytest-tmp`，Python 3.12.10 / uv，无真实图库和当前应用服务操作。

```powershell
$env:UV_CACHE_DIR = (Join-Path $PWD '.uv-cache')
uv run pytest -q tests/integration/test_p4_person_name_case.py tests/unit/test_p3_person_contract.py tests/integration/test_p3_person_merge.py tests/integration/test_p3_person_operations.py tests/integration/test_p3_person_undo.py tests/integration/test_p3_person_undo_ordering.py tests/integration/test_p3_person_api.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_small_fixes.py --basetemp .pytest-tmp/person-name-case-focused-fixed --junitxml tmp/test-reports/P4-PERSON-NAME-CASE-A-focused.xml
uv run ruff check src/photo_search/persons/identity.py src/photo_search/persons/operations.py src/photo_search/persons/contracts.py tests/integration/test_p4_person_name_case.py
git diff --check
```

定向 JUnit：**109 / 0 / 0 / 0**（tests / failures / errors / skipped），退出码 0。Ruff、diff-check 均退出码 0。首次新增 undo 夹具误断言目标人物 revision 必须递增到 3；原 undo 对没有恢复人物字段的目标允许保留 merge revision，已改为校验不倒退，并实际重跑整组，不改旧 undo 产品规则。

新增证据覆盖：ASCII / Straße / 希腊大小写 / NFC / 空白；目标正式名字保留，来源 MANUAL 链接不升级或丢失；自身改写大小写和 exact no-op；历史重复不被自己掩盖及精确/最低 ID 稳定顺序；别名与 merged 源旧名隔离，全宽与近似名字不合并；人工 unassign 与 exclusion 降级待确认、旧精确键与来源链接补偿恢复；target 后改导致 undo 整体拒绝，stale revision 零写；重放早于后来改名和不同大小写意图冲突；关闭操作阶段注入失败后 journal / 人物 / 链接全库回滚；真实 API label 零写→显式 merge→undo→旧 name 同名合并。

全量首次命令 `uv run pytest -q tests/unit tests/integration --basetemp .pytest-tmp/person-name-case-full --junitxml tmp/test-reports/P4-PERSON-NAME-CASE-A-full.xml` 退出码 2，9 个 collection errors，未执行测试：8 个 tools 模块导入路径问题，以及 `test_p4_02y_l_identity.py:824` 的独立 `\n` 字符造成 SyntaxError。`git show HEAD:tests/integration/test_p4_02y_l_identity.py` 已验证同样存在该行，此文件没有本任务 diff。

历史重试使用 `uv run python -m pytest -q tests/unit tests/integration --continue-on-collection-errors --basetemp .pytest-tmp/person-name-case-full-retry --junitxml tmp/test-reports/P4-PERSON-NAME-CASE-A-full-retry.xml` 修正仓库根模块查找路径，执行可收集用例；日志 `tmp/test-reports/P4-PERSON-NAME-CASE-A-full-retry.log`。结果 **2228/98/44/49**，退出码 1，耗时 468.723 秒；不能宣称全量通过。其范围包括原有地点 30 万合成夹具，未读取真实图库；语法错误已确认在 HEAD，其他失败没有全部进行基线归因。用户要求收口时该进程已自然结束，随后未重跑、未扩展修复无关用例。

## 交接

用户随后明确要求“就在这里简单复核就好了”，覆盖原独立 DS-H / Sol 交接安排。当前会话简要复核通过，未发现需继续修复的问题；实际检查面与复用证据见 [复核报告](P4-PERSON-NAME-CASE-A-current-session-review.md)。未派子代理、未切换模型，不冒称独立审查。保留前序小修全工作区 diff、上游 `6fa7001` 文档提交、`.tmp/` 与清理审计文件。无依赖/迁移/模型/GPU/打包变化，无提交/推送，无现场浏览器或真实图库验收。下一步仅用户少量个人试用。

## 本会话整合提交

2026-10-10，用户明确要求将本会话修复整合成一个简单本地提交，覆盖此前任务卡的提交禁止。本卡实现、相关测试和交接文档随本次人物相册整合提交保存；既有验证证据沿用，不重复测试，不推送。无关 .tmp/ 与清理审计材料保持未暂存。
