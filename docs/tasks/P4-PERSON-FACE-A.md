# P4-PERSON-FACE-A — 修复长图人脸产物比例与原图坐标

建卡：2026-10-03；状态：**SUPERSEDED — 合并到P4-PERSON-UX-A**。用户后续授权直接优化个人相册并简单测试，执行范围与验证以[P4-PERSON-UX-A](P4-PERSON-UX-A.md)为准。本卡保留原缺陷与版本边界供核对。基线 HEAD `fe46753`；工作区有 P4-HOME-UX-A 并行未提交改动，禁止覆盖。

用户新增人物反馈的唯一下一实现闭环。推荐 **DS-H / DeepSeek V4.1 Flash / 高推理** 实现；独立 DS-H 完整受影响面工程审查，Sol high 聚焦身份版本、人工知识与ADR-0010对齐。模型由用户手动切换，不启动额外Agent。P4-HOME-UX-A验收仍未收口，本卡不虚报其PASS。

## 1. 输入与目标

- [调查与反例](../reports/P4-PERSON-UX-investigation.md)，[人物体验方案](../plans/P4-PERSON-UX.md)。
- 长边>1280的图片即使检测出脸，也因scale>1被validate_payload拒绝；bbox/landmarks逆变换同时错误。
- 本卡只恢复长图的合法人脸产物与准确原图坐标。阈值不变，不实施整个人物UI，不扫描真实图库。

## 2. 开工读取

AGENTS → development-status → 本卡 → 调查 → Git实际状态。

主架构§5.4、§7.1/7.2、§8.3～8.3.5、§11.3.1/11.3.3；执行策略§2.2～2.4、§3.2/3.4、§8/11/12。ADR-0006/0007中人工知识与成员规则、ADR-0010 §2/3的版本与新旧脸映射，以及对应冻结contract中实际受影响条款。

直接实现：face/engine.py、contract.py、processor.py、index.py、service.py；persons/mapping.py；media/face_crop.py；refresh中人脸processor注册/调度。直接测试：test_p3_face_geometry、contract、redetection相关、face_thumbnail、face_lifecycle。只扩读新版本受影响的注册、进度、恢复与兼容断言。

## 3. 最小修复与不变量

1. 先跑独立诊断，确认1通过/2失败基线，记录JUnit与JSON；不得把诊断失败改成断言现有错误行为来制造绿色。
2. 明确scale的既有[0,1]正向比例含义，原图→工作帧以及工作帧→原图的两个方向分别命名。bbox/五个landmarks逆变换；FaceDocument.scale与validate_payload一致。保留face_pixels/blur的工作帧口径，不用原图大像素绕过质量门槛。
3. 至少覆盖1280边界、2560整倍、非整倍长图、竖图与EXIF转正；量化整数resize造成的x/y舍入误差。超过60MP仍拒绝；MAX_FACES、deadline与CPU/GPU策略不变。
4. **先写最小版本影响判断**：face_id含bbox，ADR-0010不允许同spec同内容出现另一份合法几何。修复改变几何必须采用正确的新检测/预处理spec版本，核对注册/config/产物身份及旧记录兼容，不直接改冻结旧身份后强写。复用现有跨世代mapping流程，保留旧人工知识，未确认映射的新脸继续hold。
5. 不能依据“旧大图理应失败”假定所有旧库无人工数据。用合成旧世代脸验证人工指定、取消、排除保留；不要修改真实数据，也不要开发批量迁移/全库重建工具。
6. 正确比例必须使build_payload→validate_payload→persist_face_document链成功；有脸与0脸两种结果都覆盖。允许注入确定性 detector/recognizer完成合成catalog闭环，报告明确其不是准确率证明。
7. 若既有版本兼容机制不足，先形成最小契约草案及反例，交独立DS-H与Sol裁决；保留未实现/未验证状态，不自行扩大迁移、回退或人工知识语义。

禁止：真实库/PhotoSearchData访问、模型下载、自动全库重检、检测画布或0.5/0.65/48/24/0.29/0.56等门限调整、换模型、人脸/图像embedding混用、人物UI重写、schema新增、人工锁/排除/undo算法改动、依赖安装/锁文件修改。原图零修改。

## 4. 验证

开发阶段先做真实缺口的定向回归，不反复跑全量。最终因为几何身份版本和映射边界，按V3.1 **D级**：B级完整回归＋直接相关新旧映射/人工知识/重跑/恢复反例，不扩展30万性能专项或打包（除非实际改变打包面）。这是本卡风险分级，不把首页卡的个人简化测试例外套用到身份变更。

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tools/diagnostics/person_face_resize_probe.py --junitxml=test-results/p4-person-face-a-probe.xml --basetemp=.pytest-tmp/p4-person-face-a-probe
uv run --no-sync pytest -q tests/unit tests/integration --junitxml=test-results/p4-person-face-a.xml --basetemp=.pytest-tmp/p4-person-face-a
uv run --no-sync ruff check src tests tools
git diff --check
```

用小型项目内合成图库验证一次保存/重跑和头像裁剪，不加载真实模型；后续真实样本与模型测试需明确授权再执行。本卡不以真实识别率作为无授权情况下的PASS证据。若全量有其他待审卡的既有失败，证明来源并记为未通过/受阻，不虚报零失败；不得顺手修别的卡。

## 5. 审查、交接与停止点

实现报告保存命令/退出码/JUnit/原始JSON、版本取舍、已验证与未验证项。独立DS-H审所有改动、调用链、冻结契约差异、必需证据；max仅在§2.2命名条件有实际证据时升级。Sol聚焦spec/face_id、旧人工知识与映射hold；两阶段结束前不标总任务PASS。

成功后更新status，人物UX按方案继续建唯一必要卡；不要启动阈值或模型专项。无用户真实样本核对时，结论只能是“合成长图产物/映射修复通过”，不能说用户现有照片已经恢复，也不能自动重跑现有图库。
