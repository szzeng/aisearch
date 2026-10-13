# P4-PERSON-IGNORE-STATE-A LUNA High 审查记录

**审查范围：** 忽略状态迁移、API、查询投影、人物管理 UI、自动人脸候选及直接回归测试。时间线相关工作区改动排除在范围外。

## 首轮结果

- **P2：** 明确排除成功后只刷新人物列表，已加载的待整理 inbox 仍可能显示旧候选建议。
- 其余审查面无其他状态、迁移回滚、API 或候选可达性问题。迁移为追加列并设置 `TEMPORARY` 默认值，失败注入测试检查 schema 与原数据回滚；`EXCLUDED` 在 suggestion/read 投影及自动候选排序前过滤，旧链接保留。

## 修复与复核

- `submitVisibility()` 在整理视图激活时与人物列表并行重载 inbox。`loadInbox(false)` 沿用已加载页数并保留展开候选组，然后重新加载展开组详情。
- 新增合成 UI 交互断言：选择 `EXCLUDED` 并提交后，确认触发 inbox 查询。
- LUNA High 修复复核确认该 P2 **CLOSED**，无剩余发现。
- 此为用户指定的 LUNA High 路由，不是 DS-H 审查。

## 验证

修复后定向 pytest：**34/0/0/0**；Ruff、`node --check`、`py_compile`、`git diff --check` 均通过。JUnit 见 `test-results/P4-PERSON-IGNORE-STATE-A.junit.xml`。