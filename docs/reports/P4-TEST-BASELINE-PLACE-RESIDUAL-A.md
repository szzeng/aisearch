# P4-TEST-BASELINE-PLACE-RESIDUAL-A

日期：2026-10-11。状态：**PARTIAL / 定向夹具修复完成；同名地点选择待决**。

## 结果

- 将目录 ASSIGN、批量 ASSIGN、API 批量/UNDO、API 地点筛选和 acceptance 同名筛选中的新地点换成 `install_source_places()` 正式 stage→validate→activate 的合成包 ID；新增合成日本层级及同名杭州，保留国家祖先过滤、同名身份和跨地分配语义。
- 有活动地点包但没有 GPS 解析记录时，建议状态为 `no_resolution`。按 `P2-03B-offline-name-dataset-and-nearby-city-suggestions` 报告列出的状态契约更新两个旧断言；没有修改生产逻辑。
- `test_cn_same_name_requires_parent_choice` 仍失败：`POST /api/v1/search/place-aware` 对“西湖区”返回 `needs_scope_choice: true` 与 `scope_choices`，旧测试读取 `needs_place_choice` 导致 KeyError。已确认当前处理路径分别存在 `needs_scope_choice` 与 `needs_place_choice` 分支，但该响应究竟符合当前消歧契约、或为真实消歧问题，需后续最小协议/期望核对；本阶段不改 API 或 oracle。
- 旧人工归属/退休保护失败节点未改造：卡片要求以明确的旧版本合成状态复现，本阶段未伪造历史状态，也未更改生产语义。该分支保持 UNKNOWN。

## 验证

- 首轮 5 个历史代表节点均复现失败；前四个为不符合当前来源资格的旧 fixture，最后一个为上述响应形态差异。
- 定向最终 JUnit：`test-results/P4-TEST-BASELINE-PLACE-RESIDUAL-A.xml`，54 passed / 0 failed / 0 errors / 0 skipped；覆盖两个目录写入节点、两个 API 节点、acceptance 地点筛选，以及共享 fixture 的搜索权威、人物过滤、备份恢复直接调用方。
- Ruff、修改 Python 文件 `py_compile`、`git diff --check` 通过。
- 未运行全仓回归，未访问真实 PhotoSearchData/照片库，未执行真实地名包下载或模型/GPU。

## 后续

继续总控阶段 3 UI-A。CN 同名选择和需要历史状态构造的旧人工归属/退休保护问题列为剩余项；不把本报告视为地点全量基线 PASS。
