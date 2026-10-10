# P4-PERSON-COLLECTION-COUNT-A 实施报告

日期：2026-10-11。状态：**PASS — 合集照片数展示与定向验证**。开始基线 `a83ffac`；实现期间 GC-B 已由提交 `387162a` 收口，最终本任务 diff 基于该提交。

## 实现

- 合集列表每页最多 100 个结果；在一个页级 SQL 投影中按 canonical 成员、共用 `MEMBER_PREDICATE` 和 `assets.state='ACTIVE'` 统计 `COUNT(DISTINCT asset_id)`，没有逐合集请求或展开照片 ID。
- 合集照片接口在 `collection` 投影中返回完整 `photo_count`，计数复用 `collection_condition`，与照片流的活动资产、有效成员和去重规则一致。
- 合集卡、加入合集下拉选择项同时显示成员数和照片数；合集照片页标题说明显示完整照片总数。合集改名响应不含照片数，前端合并改名结果时保留当前照片数。静态脚本 query 版本已更新，避免浏览器复用旧 JS。
- 人物单人相册原已显示人物总照片数，本任务保持其字段和显示语义不变。

## 验证

JUnit：`test-results/P4-PERSON-COLLECTION-COUNT-A.xml`，**10 passed / 0 failed / 0 errors / 0 skipped**。用例覆盖共用合照只计一次、建议脸不计入、空合集为 0、合集列表与每页照片响应总数一致、成员新增正式确认照片后计数增加，以及合集改名后 UI 继续显示照片数。

- Ruff：通过。
- `node --check src/photo_search/web/persons-albums.js`：通过。
- `git diff --check`：通过。

首次附带运行 `tests/unit/test_p4_person_collection_ui.py` 时，在该测试已有的人物卡初始渲染断言处发生 `TypeError`（`pa-cards.children[0]` 为空），尚未执行到本次数量展示断言。本任务随后使用新增的窄范围 `test_p4_person_collection_count_ui.py` 单独验证数量 UI，并通过；未修复或掩盖前述相邻问题，需按其归属另行处理。

未做真实浏览器/真实图库验证；没有 schema、写接口、人物身份语义或 GC 变更。没有提交或推送。
