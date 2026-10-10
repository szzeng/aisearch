# P4-PERSON-NAME-CASE-A — 当前会话简要复核

2026-10-10。用户明确要求“就在这里简单复核就好了”，覆盖原独立 DS-H / Sol 交接安排。结论：**本次姓名大小写修复的当前会话复核通过，未发现需继续修复的问题**。这是同一实现会话的相关范围复核，不冒称独立审查、全仓或正式发行验收。

[实施报告](P4-PERSON-NAME-CASE-A.md)、[任务卡](../tasks/P4-PERSON-NAME-CASE-A.md)、[规则补充](../decisions/ADR-0019-person-name-case-comparison.md)。HEAD `6fa7001`，本次复核未改产品代码、测试、依赖或 schema，未提交/推送。

## 实际复核范围与结果

检查 identity.py / operations.py / contracts.py 本次完整 diff、新增姓名大小写合成测试、service / Catalog.write 事务、name / label / 显式 merge / undo API 与前端 NAME_IN_USE 确认链。核对自动关联、拆分及单脸新建仍只创建未命名人物，没有绕过同名检查的其他正式姓名写入口。没有扩展复核无关地点或前序小修全部产品 diff。

- 正式名先 NFC / 空白规范，再 Unicode casefold 比较；仅查询活动有名人物，不以别名或后台识别触发合并。持久 nfc1 / 显示名字保留大小写，原规范测试和历史快照格式有效。
- 排除当前 canonical ID 后寻找其他目标，避免历史大小写重复被自己遮住；精确拼写优先、最低 ID 次之，与 ADR-0019 一致。没有其他目标时，自己的大小写改写是 RENAME，完全相同是记录请求的 no-op。
- name 与 label 经过同一 service / BEGIN IMMEDIATE；label 碰撞在打开操作记录前返回 NAME_IN_USE，前端展示显式确认，第二次提交携带源/目标 revision。旧 name 调用原 MERGE_BY_NAME，目标名字与 ID 保留，来源转重定向。
- 幂等摘要保留显示大小写，重放先于 canonical / revision / 当前同名检查；不同大小写意图复用同一 request_id 仍被拒绝。
- 复用原合并和补偿撤销，人工 unassign / 排除不被覆盖、来源类型保留。撤销恢复旧精确名字/键，后续修改冲突时整体拒绝；关闭操作异常由 Catalog.write 全事务回滚。历史大小写重复可经撤销恢复，后续保存再提示合并，不自动批量改历史状态。

## 复用的相关证据

实际核验 `tmp/test-reports/P4-PERSON-NAME-CASE-A-focused.xml`：**109/0/0/0**，11.509 秒；覆盖新增大小写与原命名/合并/撤销/API/相册回归。报告时间晚于当前 identity.py / operations.py 修改；验证后及复核期间无产品代码或测试变化，沿用已通过结果，没有重复 pytest。

同时核验前序 `P4-PERSON-SMALL-FIX-A-page-preserve.xml` 的 `test_person_writes_refresh_candidates_choices_and_collection_returns` 成功用例，复用改名后候选/选择与合集返回刷新的 DOM 证据；同名确认分支本次检查实际代码链，没有冒称现场浏览器验证。Ruff / Node 既有通过证据沿用；文档落盘后执行 diff-check。

历史全仓尝试仅保留实施报告，未重跑或追修其他模块。未访问真实图库/当前服务，无模型/GPU/容量/打包/浏览器矩阵工作。下一步为用户重启刷新后少量试用，不再要求为本次简要复核切换模型或新开会话。
