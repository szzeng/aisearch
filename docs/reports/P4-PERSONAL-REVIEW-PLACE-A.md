# P4-PERSONAL-REVIEW-PLACE-A — 地点组工程审查报告

日期：2026-10-09
基线：HEAD 8d4c2c2（开审时）；审查过程中未提交。
结论：**ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED**。按用户要求在当前会话执行，没有自动切换模型；因此不声称本次是 DS-H 会话。结论只覆盖本报告的工程审查，不会把七张原任务卡整体追认为 PASS。

## 范围与方法

依照 AGENTS.md → development-status → 本卡，复核 CN、UNIFIED、SCHEMA-COMPAT、PACKAGE-RECOVERY、RECOVERY-B、MAP 与 SEARCH-PLACE-NL 的实现报告、相关 ADR、当前代码和直接调用链。共享的来源身份、人工归属、撤销、包版本/成员版本和恢复证据按任务卡复用；未重跑 30 万数据、完整套件、PBF、GPU 或安装包。

直接检查了地点来源选择和来源引用校验、单条/批量归属及撤销事务、中国行政成员/人工与 GPS 边界、地点相册成员版本、schema 迁移至 21 的兼容桥、地名包启动恢复、地图查询及成员快照/旧响应保护、地点词解析和统一搜索 scope。没有发现需要新建正式语义决策的矛盾，因此不需要额外 Sol 聚焦裁决。

## 逐原任务结论

| 原任务 | 本次审查结论 | 已核对证据 | 未覆盖事项 |
|---|---|---|---|
| CN：P4-PLACE-CN | 工程审查通过；原任务现场交付仍未闭环 | 复核既有 CN 报告中的 75 项定向结果与公开四地区、东京、100 几何和 30 万 JSON 证据；检查来源/许可字段、稳定行政 ID/层级、包切换与成员调用边界。新合成流程确认地点建议可批量转成人工归属。 | 本次未重新提取 PBF、构建或导入正式数据包；实际 catalog 中的包导入和原卡要求的正式交付/签核保持 PENDING。 |
| UNIFIED：P4-PLACE-UNIFIED-A | 工程审查通过 | 复用原报告 116/0/0/0 JUnit；复核 OSM/GeoNames 来源身份、同名父级解析、活动包来源证明、人工归属与撤销调用链。当前合成 UI 对一张及余下九张分别提交 BATCH_ASSIGN，日志显示 rev 1、2 COMMITTED，最终地点相册为 10 张。 | 本次浏览器闭环没有另外覆盖单条撤销 UI；事务/撤销及来源拒绝复用现有定向测试和历史报告。 |
| SCHEMA-COMPAT：P4-PLACE-SCHEMA-COMPAT | 工程审查通过 | 复用既有 30/0/0/0 schema 兼容小包证据；当前直接节点重新激活并在 schema 21 重开成功。复核 floor 16 与显式版本范围桥接；不会把历史 schema 20 断言当作当前拒绝 21 的要求。 | 未对真实用户 catalog 执行迁移或重开。 |
| PACKAGE-RECOVERY：P4-PLACE-PACKAGE-RECOVERY | 工程审查通过 | 当前 test_place_package_startup_recovery.py 全文件直接运行；唯一包、多包、坏包、回滚和幂等路径均处于同一结果集中。 | 不清空或触碰用户 catalog；历史全量结果中的既有失败不在本次重跑范围。 |
| RECOVERY-B：P4-PLACE-RECOVERY-B | 工程审查通过，新增缺前驱正例 | 新增隔离小包测试：空 registry 的唯一完整包恢复后，新行的缺失 predecessor 为 NULL；manifest 与包文件快照不变；正常 stage/import 仍因 FK 拒绝缺失 predecessor 且不写 registry；重开幂等，单一 registry 行、pointer revision 1、地点物化数正确。 | 该窄例外只在空 registry 启动恢复路径验证，未改变正常导入约束或 manifest。 |
| MAP：P4-PLACE-MAP-A | 工程审查通过 | 当前地图 API 与修复后的 review UI 定向节点 7/7 通过。合成浏览器载入 10 张带 GPS 照片，聚合标记和网格均显示 10；从照片卡进入详情再返回，地图网格恢复 10 张。复核当前有效 GPS 与资产位置状态过滤、地图成员版本绑定、旧响应保护及详情返回链接。 | 仅合成坐标和小图库；未据此主张真实地图性能或实图库地理准确度。 |
| SEARCH-PLACE-NL：P4-SEARCH-PLACE-NL-A | 工程审查通过（合成 UI 只覆盖文本通道） | 复用原报告 28/0/0/0 JUnit；检查地点范围在 FTS/向量候选/分页前进入同一 SearchScope。真实浏览器合成查询“杭州的 sunset”解析出“杭州（GeoNames）”条件并返回 10/10 文本命中；取消地点条件后输入框保留 sunset，仍返回 10/10。 | 浏览器明确显示 fake encoder 下 10 个向量条目尚未确认；这只证明文本地点范围与条件取消保留，不证明语义通道可用或向量结果。 |

## 本次新增与运行证据

1. 首次诊断的唯一失败是旧断言把 missing-predecessor 归到“包缺陷”。这与 ADR-0015 已限定的“仅空 registry 且唯一完整包启动恢复可省略前驱”不一致；属于过时测试预期，不是产品缺陷。已用专门正例替换该参数断言，没有 skip 测试、修改生产代码或放宽常规 FK。
2. test-results/p4-personal-review-place-final.xml：12 tests，12 passed，0 failures，0 errors，0 skipped；退出码 0。包含指定的 UNIFIED 人工/来源节点、schema 21 小包重开节点和恢复测试文件。
3. test-results/p4-personal-review-place-map.xml：7 tests，7 passed，0 failures，0 errors，0 skipped；退出码 0。
4. Ruff：uv run --no-sync ruff check tests/integration/test_place_package_startup_recovery.py 通过。
5. 首次 Ruff 检查只报告新增 sqlite3 import 排序；已整理导入并复跑通过。
6. 当前浏览器用隔离 synthetic fixture（10 张生成照片、合成地点包、fake encoder、无模型加载）运行于本机 loopback 18765。可见结果包含 10 张相册成员、地点条件解析与取消、地图 10 张、详情打开和返回。进程已停止。运行期间未读取真实照片库或模型目录；fixture 保留在项目 .tmp/ 供复核。

## 可复现命令

PowerShell 环境：项目内 UV_CACHE_DIR=.uv-cache，PYTHONPATH 指向仓库根目录和 tests；每次 pytest 使用独立 basetemp，并生成 JUnit XML。

恢复、schema21 与指定 UNIFIED 节点：

~~~powershell
uv run --no-sync pytest -q tests/integration/test_p4_place_unified.py::test_manual_osm_district_assignment_uses_existing_lock_membership_undo_and_rejects_bypass tests/integration/test_place_dataset_schema17.py::test_unchanged_composite_package_activates_and_reloads_at_schema21 tests/integration/test_place_package_startup_recovery.py --basetemp=.pytest-tmp/p4-personal-review-place-final --junitxml=test-results/p4-personal-review-place-final.xml
~~~

结果：12/12通过，0失败/错误/跳过，退出码0。

地图 API 与 UI：

~~~powershell
uv run --no-sync pytest -q tests/integration/test_p4_place_map_api.py tests/unit/test_p4_place_map_review_ui.py --basetemp=.pytest-tmp/p4-personal-review-place-map --junitxml=test-results/p4-personal-review-place-map.xml
~~~

结果：7/7通过，0失败/错误/跳过，退出码0。

~~~powershell
uv run --no-sync ruff check tests/integration/test_place_package_startup_recovery.py
~~~

结果：All checks passed。首轮仅提示新增导入排序，整理后复跑通过。上述 pytest 实际运行时均设置项目本地 UV_CACHE_DIR 与 PYTHONPATH。

## 保留事项与交接

- 工程审查通过不等于 CN 正式数据导入、包许可/交付签核、用户实际安装或重启后的现场验收通过。
- 浏览器 fake encoder 显示语义索引仍在追赶，本次没有语义结果证据。
- 未访问 PhotoSearchData，未操作实际 catalog、用户服务、ACL、模型、完整套件或 30 万数据；未提交或推送。
- 开审时 index 干净；原先已存在的 status/task-card 修改、.tmp/ 与 docs/reports/tmp-* 保留。此次代码改动只限恢复测试断言与专用正例。
- 下一步只交接 P4-PERSONAL-REVIEW-SEARCH-A；本次不自动开始搜索组。
