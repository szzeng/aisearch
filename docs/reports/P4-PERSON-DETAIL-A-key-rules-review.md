# P4-PERSON-DETAIL-A — 新旧兼容、版本身份与人工保护聚焦核对

日期：2026-10-04。结论：**任务卡关键规则已补齐；实现与真实模型兼容证据仍待完成**。用户授权核对任务卡三个关键边界；本次是卡片设计核对，不是完整独立DS-H工程验收或正式协议冻结。基线HEAD `b84d4e3`；工作区同时存在原图裁脸草稿及地图/清理编辑，以下代码观察只对应检查时快照，不覆盖或修改并行实现。

## 1. 新旧特征兼容

原卡把“同权重不自动兼容”和“没有依据就隔离”写对了，但缺少允许条件及隔离后的可用闭环。核对ADR-0014：现有v1/v2桥接依据是识别输入、权重及归一化未变，只修几何；原图裁脸改变识别像素，不能沿用该例外。处理版本不同也不必然意味着潜在识别空间不同，space身份与允许自动比较的profile组合应分别记录。

已补：精确结构/版本白名单和小型真实模型对照；只读取READY/CONFIRMED且无人工禁用的有效代表，按canonical人物共享32/8上限；未知版本不参与，不复制旧向量冒充新版，不切换全库活动世代。未取得跨profile证据时，仍展示增强结果并支持显式指定已有人物/映射确认，不因空候选制造大量重复人物。小样本只支持个人试用结论。

当前`face/index.py`候选与`persons/albums.py`可见性仍面向既有v1/v2；同图占位读取资产全部CONFIRMED链接。添加新版本时需要同时处理新版可见性与机器旧行替代，否则旧脸可能挡住同一物理脸的新归属。人工锚点与OPEN映射不能通过放宽同图冲突绕开。

## 2. 版本身份与恢复

并行草稿`face/processor.py`的`preserve_source_detail`改变解码/识别输入，但`build_payload`、`face_id_for`及`artifact_ref`仍引用v2全局配置。直接执行现有构建/校验函数的合成诊断：同一个sha256输入、同框/关键点，仅把合法512D单位向量从`[1,0,…]`换成`[0.8,0.6,…]`，两份payload均被接受；face_id、artifact_id、artifact_revision和config_hash相同，checksum不同。退出码0；没有加载模型、读取真实照片或写数据库。这证明现有身份不能表达增强版本，不是新旧实际模型兼容测试。

`artifacts/store.py`同时检查artifact ID和processor/input/type/artifact revision逻辑唯一键；上述不同内容正式发布会报`ARTIFACT_CONFLICT`，不能把它表述为仓库已允许覆盖旧不可变字节。绕过该仓库直接写派生脸/向量也不是合法解决方式。

已补：增强使用独立preprocess/spec/artifact/config及脸/世代身份；普通v2不变，artifact逻辑键也隔开。完整配置记录解码、源裁剪、质量与坐标帧。当前增强草稿以原图raster尺寸写working_size、scale却来自检测工作图比例，需定义一致的新版帧语义，不能继续套用旧v2字段解释。

现有job唯一键不含spec_revision、association队列按asset唯一，单纯加布尔开关或新spec不能保证重启/重试仍执行同一路径。卡中要求持久化处理身份并证明普通/增强不互相覆盖；独立增强processor注册只是优先评估方案，具体ID与队列方案由实现ADR决定，未在本次冻结。校验/发布/重放均须按保存的profile解析。

## 3. 人工记录保护

原卡保留名字/人工记录与OPEN保护的方向正确，但没有覆盖实际版本解析。`persons/mapping.py`的映射/hold/requeue依赖当前全局spec；`persons/operations.py::_repair_candidates`虽读取face.spec_revision，仍以全局space/processor构造generation。新增增强身份后，直接复用会漏hold或预约错误世代。

已补：目标脸实际profile贯穿映射、hold、确认/拒绝/撤销、修复与重放；旧事件保留旧身份。依据ADR-0007/0010，唯一高匹配也不自动继承，单一明确继承/排除并集/人工未归类矩阵不变；冲突或陈旧revision整笔拒绝，自动提交事务内重读控制与OPEN状态，迟到结果不覆盖人工编辑。零脸仍保留旧人工锚点与NO_CANDIDATE组；撤销重新hold，旧记录和操作审计保留。

## 4. 交接与验证范围

已更新[任务卡](../tasks/P4-PERSON-DETAIL-A.md)§4及最小验证清单，允许必要的index/mapping/operations/图片读取改动，避免实现者只改engine却漏掉发布、人工修复和显示。正式决定跨模块身份/协议时仍按AGENTS例外完整架构对齐。

本次验证仅代码/ADR定向核对、上述合成身份反例、文档链接存在性及`git diff --check`；未运行pytest、真实模型、真实图库、服务或全量回归。实现阶段保留小型定向验证，不重跑历史216项：身份/候选组合，人工OPEN与迟到编辑，确认/撤销及一次发布恢复可复用同一小型夹具。现有独立人物审查仍PENDING，本报告不声明功能完成或正式PASS。未提交/推送。
