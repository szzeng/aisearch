# P4-PLACE-CN — schema20 兼容修复与个人测试步骤

2026-10-05。状态：**修复及定向验证通过 / 用户重启与实际相册测试待做 / 独立审查待做**。未提交或推送。

## 原因与修复

并行 P4-PERSON-DETAIL-B 新增 migration0020（人脸坐标帧、自动处理授权、历史批次任务索引），当前代码 schema20；上一轮地名包的明确兼容桥只到19。这使原 GeoNames 包的 maximum16 与 CN复合包的 maximum18 都被拒绝。不是源文件下载/校验损坏。

核对0020与ADR-0017没有修改地名包、GeoFact、人工地点或CN投影后，仅允许schema20使用max16/17/18/19的包；最低版本条件继续生效，max15和未来schema21仍拒绝。不重写manifest、不重下数据、不修改历史migration或实际catalog。运行进程版本未核实；当前修复需重启源码服务加载，刷新页面不能替代重启，旧exe不包含工作区修复。

定向JUnit **51/0/0/0**、退出0：schema升级/空catalog恢复、显式兼容矩阵、schema20损坏文件拒绝、CN浏览/搜索/人工保护/切包/持久批次、包状态及原包API。Ruff/scoped diff-check通过。命令：

```text
.venv/Scripts/python.exe -m pytest -q tests/integration/test_place_dataset_schema17.py tests/integration/test_p4_place_cn.py tests/integration/test_place_package_startup_recovery.py tests/integration/test_p2_place_dataset_api.py --basetemp=.pytest-tmp/p4-cn-schema20-2 --junitxml=test-results/p4-cn-schema20.xml --tb=short
```

仅只读公开包的manifest与全部内容checksum：原GeoNames 4文件/12,189,594bytes、CN候选8文件/76,804,462bytes均在schema20通过；证据 `test-results/p4-cn-schema20-packages.json`。未打开真实catalog、扫描或更改照片，也没有导入/激活真实包；此前实际四地区几何验证沿用原证据，本次不重复提取/30万计时/模型/全量测试。

## 个人测试步骤

1. 正常退出正在运行的相册后台。若只验证旧包报错修复，重新使用仓库源码启动后，“地名包状态”应为active、解析器可用，原GeoNames包仍显示兼容5–16是正常的。
2. 要测试新增中国省/市/区县，在项目目录执行以下导入命令，再用源码启动。首次完整校验可能需要等待，包保留旧GeoNames供回退；原1.6GB PBF不用再下载或放运行目录。这些命令由用户决定执行，本轮未执行。

```powershell
Set-Location D:\SZ\project\aisearch
.venv\Scripts\python.exe tools\places\manage_dataset.py --data-root D:\SZ\project\PhotoSearchData import --source .tmp\p4-place-cn\composite-261003 --activate
.\tools\dev\start_personal_trial.ps1
```

3. 使用启动器打开的会话页面进入“地点整理”。活动dataset_id应为 `geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3`，点击“更新中国地点”；该按钮只分批处理已有有效GPS，支持暂停继续。没有GPS的照片不会凭图像内容生成行政地点。
4. 查看切到“区县”，打开你确实有GPS照片的地区，核对省/市父级、数量、下一页与照片预览；搜索同一个区县名，再搜“区县名的日落”。示例“西湖区”“西湖区的日落”只有该地区照片才应命中。地图入口仍为“地图相册”，中国行政区包不是底图瓦片。

若仍报schema不兼容，先核对页面显示的schema与是否从当前源码启动；不要编辑包的maximum字段或删除catalog。未覆盖地区可能只显示可靠上级，不宣称全国完整覆盖。独立DS-H/Sol审查要求沿用主任务，不把这次定向修复当作整卡PASS。
