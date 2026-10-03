# P4-PERSON-UX：人物体验与漏识别调查

日期：2026-10-03；Git HEAD：`fe46753`。状态：**DESIGN COMPLETE / DEFECT REPRODUCED / PRODUCT FIX PENDING**。用户请求设计个人使用的简单人物功能，并调查是否阈值过高。

## 1. 调查范围与边界

读取 AGENTS、接手指令、development-status、当前 P4-HOME-UX-A 卡、直接人物/人脸实现及 P3-02报告、ADR-0006/0007/0010相关条款。架构按 §8.3～8.3.5、§9.1/9.3、§10.1与§11.3.1；策略按 §2.4、§3.2、§8/11/12核对。未改变架构协议或冻结新契约。

既有首页/API/测试未提交改动保持原样。本轮新增方案、本报告、下一卡以及独立诊断脚本；status只插入简短交接。未连接实际服务、读取 PhotoSearchData、catalog 或照片目录，未加载模型或下载权重。

## 2. 最优先的已复现缺陷：长图产物无效

代码链：`face/engine.py::working_image` 返回工作栅格与逆缩放；`analyze_image` 写 FaceDocument；`face/processor.py::build_payload` 原样写 scale；`extract_for_asset` 调用 validate_payload 后才返回，persist_face_document 也再次校验。

- working_image 对长边 >1280 的图片返回 `inverse_scale = 原图长边/1280`。
- analyze_image 以 `working_scale = 1/inverse_scale` 将工作帧 bbox/landmarks 乘回原图，这使它再次缩小，应该转换回原图却得到更小坐标。
- FaceDocument.scale 实际保存 inverse_scale，validate_payload 要求 `0 <= scale <= 1`，导致超出1280的正常缩小输入报 `ARTIFACT_INVALID: scale is outside [0.0, 1.0]`。
- 即使检测返回0张脸，scale 校验仍执行；“成功处理但没检测到脸”与“保存失败”不能混淆。

无模型反例：创建纯色 PIL 图片，用固定合成人脸替换 detector decode，识别返回512维固定单位向量；真实 working_image、analyze_image、build_payload、validate_payload 仍执行。不是准确率评测，但证明了几何和产物校验错误，不依赖模型推测。

| 原图 | 工作图 | scale实际 / 应存正向比例 | 工作框 | 原图框应为 | 实际写框 | 校验 |
|---|---|---|---|---|---|---|
| 1280×800 | 1280×800 | 1 / 1 | 200,200,300,300 | 200,200,300,300 | 200,200,300,300 | 接受 |
| 2560×1600 | 1280×800 | 2 / 0.5 | 同上 | 400,400,600,600 | 100,100,150,150 | ARTIFACT_INVALID |
| 4000×2500 | 1280×800 | 3.125 / 0.32 | 同上 | 625,625,937.5,937.5 | 64,64,96,96 | ARTIFACT_INVALID |

原始 JSON：`test-results/p4-person-ux-geometry/{1280,2560,4000}.json`；持久汇总：[P4-PERSON-UX-geometry.json](P4-PERSON-UX-geometry.json)；JUnit：`test-results/p4-person-ux-geometry.xml`。诊断源：[person_face_resize_probe.py](../../tools/diagnostics/person_face_resize_probe.py)。断言的是正确行为，所以当前代码有2个失败，这是缺陷证据，不是修复通过。

实际执行（项目 .venv Python 3.12.10，无依赖安装）：

```powershell
.venv/Scripts/python.exe -m pytest -q tools/diagnostics/person_face_resize_probe.py --junitxml=test-results/p4-person-ux-geometry.xml --basetemp=.pytest-tmp/p4-person-ux-geometry-final --tb=no
```

退出码1；JUnit核对：3 tests / 1 passed / 2 failures / 0 errors / 0 skipped。临时初稿也执行一次且结论相同，最终证据对应 tools 下格式化后的脚本。故障不是WinError5或环境失败。

修复不能只放宽校验到大于1：那会保留错误坐标，并改变既有产物比例含义。先统一正向缩放与原图坐标逆变换；非整倍数尺寸还要核对整数取整后的x/y坐标误差。

身份影响：face_id_for 使用 spec_revision、input_revision、ordinal、bbox。修坐标会改变ID；ADR-0010 §3规定同spec同内容只对应一份合法文档，几何发生变化需要遵守检测/预处理版本纪律及跨世代映射。不能无版本修正后假定人工锁会自然迁移。

## 3. 已检测但用户看不到的整理缺口

`persons/read.py::face_page` 只接受 person_id 或 assignment_state=manual_unassigned。

- 人物成员由 MEMBER_PREDICATE 限定为 CONFIRMED、非人工取消、非排除。
- 人工待归类列表来自 control.mode=MANUAL_UNASSIGNED。
- index.decide_for_face 对低质量或边界相似度输出 SUGGESTED/LOW_QUALITY_UNASSIGNED；这些机器未归组脸不因此变成 MANUAL_UNASSIGNED。
- _suggested_faces 仅为已选出的人物成员脸/人工待归类脸补布尔标记，不提供所有自动未归组脸的入口。

因此已检测的 LOW 或边界建议脸可能既不在人物相册，也不在“待手动归类”。这是从代码可证的可见性缺口；没有读取用户库，不能说实际漏掉多少。

## 4. 确认的当前门限与输入

| 环节 | 当前值 | 能解释什么 |
|---|---|---|
| 检测画布 | 640×640，整图缩小后填充 | 大合照小脸的检测细节变少，是否实际漏检需样本证明 |
| 检测分门限 | >=0.5 | 小于此值不输出脸；降低可能增加误检 |
| 工作栅格 | 长边1280 | 识别质量尺寸用此帧，不是原图尺寸 |
| 质量 | 短边>=48、blur>=24、det_score>=0.65 | 任一不满足为LOW，不自动建有效人物成员 |
| 建议 / 自动归组 | cosine >=0.29 / >=0.56 | 中间区间仅建议；降低0.56不会凭空产生检测框 |
| 歧义保护 | 最佳与第二人物差<0.08；同图冲突 | 即使超过0.56仍可能只建议 |

例：原图长边4000、某脸短边100，工作帧约32像素、检测画布约16像素。忽略首要产物错误后，也可能被判小脸。这个例子是比例推算，不是实际检测召回实测。

历史校准只有8个公开身份、同图合成变化，P3-02报告明确年龄变化没有真实夹具。不能据此断言个人图库的0.56“已经最优”或“必然太高”。Qwen语义搜图与独立的SCRFD/ArcFace人物路径不同，升级Qwen不能自动修复人脸结果。

## 5. 体验与落地判断

persons.html公开要求填写person_id、expected_revision等字段，persons.js主要按钮是“载入此人的脸”；当前没有点头像直达完整人物照片的使用闭环。asset_detail目前没有完整人物裁剪/归属投影。

同名保存由ADR-0006冻结为事务内合并。若新UI承诺“先确认再合并”，只做客户端同名预查无法覆盖并发竞态，需后续最小命名意图约束裁决；本轮不冻结它。

方案：[P4-PERSON-UX](../plans/P4-PERSON-UX.md)。下一卡只修长图产物，先恢复可用结果；不同时改阈值、聚类策略和人物写契约。

## 6. 验证与未验证

- 诊断脚本 Ruff check/format通过；最终 JUnit为 **1通过 / 2失败**，如上。
- 文档本地链接、条款、Node语法与diff-check通过；不沿用首页或历史P3测试为本次验证。
- 可点击示意：[persons-flow.html](../prototypes/persons-flow.html)。用独立本地预览实际走通头像→相册→合照更正→移除→撤销；同名命名先显示合并确认→确认合并→撤销；疑似脸确认后按钮禁用且演示计数变化。仅浏览器内演示数据，无产品API请求，不能称人物产品功能已经通过。
- 未做真实用户照片、真实CPU/GPU推理、人物准确率、浏览器产品验收、全量回归、打包或独立审查。交互示意只有本地演示数据，不是接入产品后的验收。
- 根因已复现，但用户现有照片是否遇到同一个ARTIFACT_INVALID尚未核对；不把可复现缺陷等同于用户所有漏识别的唯一原因。

2026-10-03后续：原缺陷已在P4-PERSON-UX-A修复，简单验证与当前限制见[实现报告](P4-PERSON-UX-A-implementation.md)。上文保留初次调查时点，不代表当前失败；原始JSON不追改。
