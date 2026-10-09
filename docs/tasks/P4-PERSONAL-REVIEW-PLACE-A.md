# P4-PERSONAL-REVIEW-PLACE-A — 地点、地图与地名包统一基础审查

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED**。按用户要求在当前会话执行，没有自动切换模型；本结论是本组工程审查，不追认七张原任务卡全部PASS。按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)，共享包验证/成员/恢复证据复用，仅对直接缺口补测。

入口：AGENTS → status → 本卡 → 原报告与真实Git。主架构§4.5、9.3～9.5、11.2及ADR-0005/0015/0016直接条款；保留MAP已授权在线瓦片/坐标展示例外，不重审隐私专项或全架构。

当前交接：人物组已完成 **ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED**，见[人物组报告](../reports/P4-PERSONAL-REVIEW-PERSON-A.md)；仅AVATAR正常更新服务后的现场确认仍单项PENDING。开审核对HEAD 8d4c2c2；index干净，tracked工作区已有status/task-card编辑，且已有.tmp/与docs/reports/tmp-*保留。本次只增加恢复测试的直接正例并替换过时缺前驱断言；没有修改生产代码。完整结论与原任务保留事项见[地点组审查报告](../reports/P4-PERSONAL-REVIEW-PLACE-A.md)。

## 审查证据摘录

- test-results/p4-personal-review-place-final.xml：12/12通过；包含UNIFIED人工/来源节点、schema21复开节点、恢复测试文件。
- test-results/p4-personal-review-place-map.xml：7/7通过；地图API与review UI直接节点。
- 修改测试文件Ruff通过。首轮Ruff提示import顺序，已整理后复跑。
- 隔离10张合成照片的浏览器流程：杭州地点批量归属；“杭州的 sunset”10个文本结果；取消地点后保留sunset并显示10个结果；地图10张→详情→返回10张。Fake encoder的向量未确认，不作为语义搜索证据。
- RECOVERY-B缺失前驱恢复用独立小包覆盖：空registry唯一包恢复后registry predecessor NULL；manifest/包文件不变；普通stage仍受外键拒绝；重新打开幂等。

本卡原覆盖表、审查边界、最小验证规则及启动指令保留在下方，供追溯。原任务现场/正式数据事项未完成与非DS-H会话边界详见审查报告。

---


## 1. 覆盖表

| 原任务/报告 | 必留检查 | 已有证据/变化 |
|---|---|---|
| [CN](../reports/P4-PLACE-CN.md) | 来源/许可/完整性、稳定行政ID/层级、无GPS人工知识、stage/activate/切换/迟到结果、批次暂停恢复 | 复核既有75项定向结果及公开四地区/东京/100几何与30万JSON证据；未重提取PBF或重建夹具；正式数据导入和交付签核仍PENDING |
| [UNIFIED](../reports/P4-PLACE-UNIFIED-A.md) | OSM优先/GeoNames兜底、同名父级、来源证明、单张/批量ASSIGN拒绝零写入、取消/排除/撤销、历史custom可读可撤销 | `.tmp/p4-place-unified-final.junit.xml`116项；源身份门禁不撤销，旧USERfixture先核对预期 |
| [SCHEMA-COMPAT](../reports/P4-PLACE-SCHEMA-COMPAT.md) | 当前审核上限21、floor16桥、未知未来拒绝、包字节不变 | 30项与小包重开证据；CN旧schema20/未来21断言为历史，不重复按旧要求拒绝当前21 |
| [PACKAGE-RECOVERY](../reports/P4-PLACE-PACKAGE-RECOVERY.md)、[RECOVERY-B](../reports/P4-PLACE-RECOVERY-B.md) | 空registry/唯一完整包、缺前驱新行NULL、正常导入约束保留、manifest不变、非空/多包/坏包拒绝、原子回滚/幂等 | 恢复测试文件12项合并结果中覆盖唯一包/坏包/多包/回滚；新增缺前驱正例验证窄恢复、普通导入FK拒绝、manifest/文件不变及重开幂等 |
| [MAP](../reports/P4-PLACE-MAP-A.md)、[短审](../reports/P4-PLACE-MAP-A-review.md) | 当前有效GPS/去重、points/网格/计数版本一致、同坐标完整成员、旧响应/Back、地图失败列表回退、资源入Git | 5项已修＋6项定向、原API及30万JSON；不重测速度或重复短审已关闭项，查看之后增量 |
| [SEARCH-PLACE-NL](../reports/P4-SEARCH-PLACE-NL-A-implementation.md) | 已验证映射key一致、空成员不换来源、解除条件保留内容、地点范围在检索前生效 | 58项；共用UNIFIED成员/选择证据，不另做一套地点解析 |

本组查看所有尚未独立审过的改动文件和直接调用链：places/geo/CN工具、0018/0021与MAP成员版本迁移、queries/search的地点scope、API/地图/地点/搜索页面、相关依赖与测试。人物0019/0020本身引用DETAIL-B，不重审；当前schema21不误写20。

## 2. 最小验证

只对缺口/变化补现有节点：`tests/integration/test_p4_place_unified.py`（正常来源＋ASSIGN/取消/撤销/伪造拒绝）、`test_place_dataset_schema17.py`（当前/未来）、`test_place_package_startup_recovery.py`（唯一包/多包/回滚）、`test_p4_place_map_api.py`及`tests/unit/test_p4_place_map_review_ui.py`（成员/旧响应/回退）。不把所有文件无条件一起跑；missing-predecessor旧断言已依据ADR-0015替换为窄恢复正例，未skip必要验证。

优先核对的直接节点包括：

- `tests/integration/test_p4_place_unified.py::test_manual_osm_district_assignment_uses_existing_lock_membership_undo_and_rejects_bypass`：指定/撤销/来源拒绝的共享证据。
- `tests/integration/test_place_dataset_schema17.py::test_unchanged_composite_package_activates_and_reloads_at_schema21`：当前兼容与重开。
- `tests/integration/test_place_package_startup_recovery.py::test_recovery_failure_rolls_back_registration_pointer_and_places`：恢复原子回滚。
- `tests/integration/test_place_package_startup_recovery.py::test_multiple_surviving_packages_require_explicit_selection`：不替用户选择活动包。

直接证据定位与实际执行结果见本卡“审查证据摘录”和审查报告。恢复缺前驱正例已覆盖正常导入拒绝、manifest/包字节不变及重开幂等。实际命令和JUnit路径见报告；collect-only不作为通过证据。

一次约10～20张合成闭环：找一个可信地点→指定一张照片→相册/搜索一致→取消或撤销恢复；有GPS地图点→详情→返回，已有在线/离线证据能覆盖就复用。多地区精度、特殊边界和全部浏览器组合不强制重跑；层级/来源/事务关键拒绝有直接证据即可。

RECOVERY-B已用隔离小型完整包完成缺前驱恢复和重开验证；未清空私人catalog或重新建库。真实安装/现场效果仍待用户自行决定。

## 3. 输出

报告 `docs/reports/P4-PERSONAL-REVIEW-PLACE-A.md`，每个原任务分别列覆盖/待验；原包恢复/兼容/地图报告保留，不能整组批量追认PASS。个人工程收口默认SOL_NOT_REQUIRED；正式改变来源/人工/恢复冻结规则或未决安全矛盾才短裁决，不固定追加一轮Sol。

范围变化仅限本组具体缺陷的最小修复与直接回归，记录JUnit/退出码；正常时不运行全量、30万、GPU、PBF、包构建，不访问PhotoSearchData。完成本组后交接搜索组，不自动执行。

## 4. 执行结论与后续交接

本组ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED。用户要求在当前会话执行；未自动切换模型，不声称本次为DS-H会话。该工程结论不代表七张原任务卡整体PASS。逐原任务覆盖、精确JUnit、浏览器观察及剩余PENDING事项见[地点组审查报告](../reports/P4-PERSONAL-REVIEW-PLACE-A.md)。

未访问PhotoSearchData，未操作真实catalog/用户服务/ACL/模型；未运行完整套件、30万、GPU、PBF或打包；未提交/推送。只交接[搜索与查看组](P4-PERSONAL-REVIEW-SEARCH-A.md)，本次不自动执行。
