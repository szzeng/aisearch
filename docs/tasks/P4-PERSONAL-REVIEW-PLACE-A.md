# P4-PERSONAL-REVIEW-PLACE-A — 地点、地图与地名包统一基础审查

日期：2026-10-09。状态：**READY / 未执行审查**。独立DS-H/高推理，用户手动选择。按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)，一次审完未审的完整受影响面，包验证/成员/恢复共用证据，不分别重跑七张卡。

入口：AGENTS → status → 本卡 → 原报告与真实Git。主架构§4.5、9.3～9.5、11.2及ADR-0005/0015/0016直接条款；保留MAP已授权在线瓦片/坐标展示例外，不重审隐私专项或全架构。

## 1. 覆盖表

| 原任务/报告 | 必留检查 | 已有证据/变化 |
|---|---|---|
| [CN](../reports/P4-PLACE-CN.md) | 来源/许可/完整性、稳定行政ID/层级、无GPS人工知识、stage/activate/切换/迟到结果、批次暂停恢复 | 75/82项、公开四地区/东京/100几何与30万JSON；只核对，不重提取PBF或重建夹具 |
| [UNIFIED](../reports/P4-PLACE-UNIFIED-A.md) | OSM优先/GeoNames兜底、同名父级、来源证明、单张/批量ASSIGN拒绝零写入、取消/排除/撤销、历史custom可读可撤销 | `.tmp/p4-place-unified-final.junit.xml`116项；源身份门禁不撤销，旧USERfixture先核对预期 |
| [SCHEMA-COMPAT](../reports/P4-PLACE-SCHEMA-COMPAT.md) | 当前审核上限21、floor16桥、未知未来拒绝、包字节不变 | 30项与小包重开证据；CN旧schema20/未来21断言为历史，不重复按旧要求拒绝当前21 |
| [PACKAGE-RECOVERY](../reports/P4-PLACE-PACKAGE-RECOVERY.md)、[RECOVERY-B](../reports/P4-PLACE-RECOVERY-B.md) | 空registry/唯一完整包、缺前驱新行NULL、正常导入约束保留、manifest不变、非空/多包/坏包拒绝、原子回滚/幂等 | 前者旧测试可复用，B原实现只做静态检查，缺前驱正例及拒绝/回滚是否已有证据要执行时核对，不能凭报告PASS |
| [MAP](../reports/P4-PLACE-MAP-A.md)、[短审](../reports/P4-PLACE-MAP-A-review.md) | 当前有效GPS/去重、points/网格/计数版本一致、同坐标完整成员、旧响应/Back、地图失败列表回退、资源入Git | 5项已修＋6项定向、原API及30万JSON；不重测速度或重复短审已关闭项，查看之后增量 |
| [SEARCH-PLACE-NL](../reports/P4-SEARCH-PLACE-NL-A-implementation.md) | 已验证映射key一致、空成员不换来源、解除条件保留内容、地点范围在检索前生效 | 58项；共用UNIFIED成员/选择证据，不另做一套地点解析 |

本组查看所有尚未独立审过的改动文件和直接调用链：places/geo/CN工具、0018/0021与MAP成员版本迁移、queries/search的地点scope、API/地图/地点/搜索页面、相关依赖与测试。人物0019/0020本身引用DETAIL-B，不重审；当前schema21不误写20。

## 2. 最小验证

只对缺口/变化补现有节点：`tests/integration/test_p4_place_unified.py`（正常来源＋ASSIGN/取消/撤销/伪造拒绝）、`test_place_dataset_schema17.py`（当前/未来）、`test_place_package_startup_recovery.py`（唯一包/多包/回滚）、`test_p4_place_map_api.py`及`tests/unit/test_p4_place_map_review_ui.py`（成员/旧响应/回退）。不把所有文件无条件一起跑；当前missing-predecessor旧断言须对ADR-0015窄例外核对后再修，不能skip掩盖必要恢复验证。

一次约10～20张合成闭环：找一个可信地点→指定一张照片→相册/搜索一致→取消或撤销恢复；有GPS地图点→详情→返回，已有在线/离线证据能覆盖就复用。多地区精度、特殊边界和全部浏览器组合不强制重跑；层级/来源/事务关键拒绝有直接证据即可。

RECOVERY-B的个人运行时证明用隔离小型完整包完成一次缺前驱恢复和重开，或核对已存在相同证据；不要求用户清空私人catalog或重新建库。真实安装/现场效果仍待用户自行决定，不作为获取基本证据的手段。

## 3. 输出

报告 `docs/reports/P4-PERSONAL-REVIEW-PLACE-A.md`，每个原任务分别列覆盖/待验；原包恢复/兼容/地图报告保留，不能整组批量追认PASS。个人工程收口默认SOL_NOT_REQUIRED；正式改变来源/人工/恢复冻结规则或未决安全矛盾才短裁决，不固定追加一轮Sol。

范围变化仅限本组具体缺陷的最小修复与直接回归，记录JUnit/退出码；正常时不运行全量、30万、GPU、PBF、包构建，不访问PhotoSearchData。完成本组后交接搜索组，不自动执行。
