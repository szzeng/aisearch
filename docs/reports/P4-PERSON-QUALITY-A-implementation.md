# P4-PERSON-QUALITY-A 实施报告

日期：2026-10-09。基线：`0959137e4916e2da3c8bacae0b09227f8715a7f10`。状态：**IMPLEMENTED / 定向合成验证通过；真实图库阈值试用待用户确认**。

## 实施结果

- 新增 `persons/quality_policy.py`，集中定义宽松、均衡、严格三档。页面固定均衡值 0.70 / 56 / 40；只有 `quality_state=OK` 且三项均达到门限才视为普通合格脸。缺项按保守策略留在页面，原存储字段不改。
- `/persons/browse` 和待整理单脸/分组/组内/摘要增加可选 `quality_view=all|clean`。API 省略参数仍走旧 all 视图和旧游标绑定；只在整理页未命名人物列表和待整理普通原因入口使用 clean。专门 `kind=quality` 保留全候选池找回新增门限筛出的脸。模式和固定策略版本进入新游标绑定。
- clean 只对未命名人物的有效成员与待整理非人工控制脸投影筛选；人工确认、人工移出、有效排除保护仍可见。人物已有照片数不变，用户已选封面优先；从清爽头像选择时优先合格脸。`person_hidden`、`face_inbox_hidden`、质量原因和组织原因不写入或互相混用。
- 页面整理模式默认 clean，提供“显示疑似低质量人脸”开关。切换质量模式/原因会清分页和相应选择；列表及分组的迟到响应受序号保护。摘要把当前可整理脸数与质量收起脸数分开。
- 新增只读诊断工具 `tools/diagnostics/person_face_quality.py`，只打开显式传入的 SQLite 路径并以 `mode=ro` 连接；使用现行可见性/hold/人工忽略/旧检测替代规则，不读图像或向量。输出状态/原因分布、三档抑制数和重叠、未命名分组影响、人工保护数、不完整指标数及按 `spec_revision` 分开的指标分位数。

## 定向验证

JUnit：`test-results/p4-person-quality-final.xml`，**20 passed / 0 failures / 0 errors / 0 skipped**。覆盖质量策略/只读统计、合成 API 列表及组过滤、旧调用兼容、质量找回、人工控制和忽略状态，以及 DOM 切换/选择清理/迟到响应；并回归现有人物相册与人脸忽略 UI。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_quality.py tests/integration/test_p4_person_albums.py tests/unit/test_person_quality_ui.py tests/unit/test_person_inbox_hide_ui.py --basetemp=.pytest-tmp/p4-person-quality-final5 --junitxml=test-results/p4-person-quality-final.xml
.venv/Scripts/ruff.exe check src/photo_search/persons/quality_policy.py src/photo_search/persons/albums.py src/photo_search/api/routes.py tools/diagnostics/person_face_quality.py tests/integration/test_p4_person_quality.py tests/integration/test_p4_person_albums.py tests/unit/test_person_quality_ui.py tests/unit/test_person_inbox_hide_ui.py
& 'D:/Program Files/nodejs/node.exe' --check src/photo_search/web/persons-albums.js
.venv/Scripts/python.exe -m py_compile src/photo_search/persons/quality_policy.py src/photo_search/persons/albums.py src/photo_search/api/routes.py tools/diagnostics/person_face_quality.py
git diff --check
```

以上命令退出码均为 0；Ruff 报 `All checks passed!`。合成分档示例：det_score 0.69、face_pixels 100、blur_variance 100 的 OK 脸在宽松档保留，在均衡/严格档列为疑似低质量。该单样例仅说明规则边界，不代表总体效果或最佳阈值。

## 尚未验证与边界

用户需在正常更新应用后，以少量已知清晰脸、明显模糊脸、合照小脸及人工指定人物检查实际整理列表与质量入口；这项现场结果仍 PENDING，当前不宣称真实图库质量改善或阈值已标定。没有运行真实诊断 CLI、浏览器矩阵、全仓/30万/GPU/模型/正式打包；没有访问 PhotoSearchData、用户服务或照片。本轮未改 Schema、冻结人脸推理合同、身份/人工事实，也未提交/推送。原有 `.tmp/` 与 `docs/reports/tmp-retention-*` 等未跟踪材料保持不动。

下一步转入个人试用并收集上述反馈；目前没有必要的新工程任务卡。
