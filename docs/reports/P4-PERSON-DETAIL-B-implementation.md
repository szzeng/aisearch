# P4-PERSON-DETAIL-B — 实现与定向验证

日期：2026-10-05。状态：**IMPLEMENTED / 34项定向及公开样片真实模型对照通过 / 独立审查待做**。基线 `856f26e` 加共享工作区 Luna 已完成的 DETAIL-A/B；未提交或推送。用户授权接续、简查修复并完成，沿用个人项目的小型定向验证例外。本报告更新部分实现的旧结论，不追认前置独立审查完成。

## 交付

- 人物页“整理一下”提供“一键原图补识别”，可选全部或单图库、预览候选照片数量。选择已有机器待归类、建议或有原图像素收益的小脸，按照片去重；未命名有效人物不因没名字就重跑，纯模糊／低检测分数／没有脸记录不混作默认增强候选。
- SQLite 保存批次范围、创建时资产上界、游标、任务及计数，每页最多50张、串行持续执行；暂停在当前照片结束后生效，支持继续、失败重试和进程重启恢复。单张失败继续后续照片；推理成功不冒充派生步骤完成。
- 新建／增量请求默认原图裁脸，可显式选轻量；来源集合已选策略持久保存，重试和重启沿用。首页默认“沿用已选模式”，网页刷新不把轻量默默改回原图。启动或打开页面不会自动补跑旧库。
- 原图完整解码一次，仍640检测、112识别，合照按脸局部裁剪；复用模型会话，与建库共享资源锁，补识别闲置释放缓存。保留60MP、每图200脸、120秒边界；同步解码／单次ONNX调用期间不能立即暂停。
- 合格source-detail产物进入候选索引确认发布及自动归人，人物视图／成员／计数同步排除旧机器重复。有歧义、OPEN或人工锚点的结果留待确认；名字、人工指定／移出、排除、封面和操作／撤销记录保留。单张手动增强按钮仍可用。

## 简查修正与关键规则

1. scheduler增加task_type与本轮job_id过滤，普通、手动详情和自动详情互不偷领，暂停批次不会被其他建库入口消费。
2. 新增版本化 `source-detail-auto@1` 逐资产授权、source独立association队列和派生完成门禁。已发布手动详情只恢复索引／归人，不重复推理；executor也校验并复用可用缓存产物。
3. 精确跨profile桥允许字面v1/v2/source白名单，核对已验证权重及两种配置哈希，generation确定性身份／READY／space／processor／512维／cosine／L2、当前artifact、CONFIRMED entry和向量校验。未知或任一配置不符拒绝source自动比较；source generation不激活全库新世代。v1沿用同识别家族兼容路径，本轮真实模型实测为v2↔source，未重跑历史v1推理。
4. 旧机器脸不再挡住同物理脸新版归属或显示重复，旧行保留历史；人工链接／锁定／有效排除／OPEN仍参与保护及同图占位，合照冲突规则未放松，不后台合并或重建person_id。
5. JPEG draft旧帧可能比原图小。schema20保存校验后的artifact坐标帧，事务外用checksum／face_id／bbox／input核对旧产物后按两轴换算映射，旧ID和阈值不变。人工旧帧不可验证时保守hold新脸，避免人工移出被新版本绕过。source working_size为原图时scale修为1，识别输入／配置／产物身份不变，已发布字节不改写。
6. 自动写事务重读迟到人工控制、排除、canonical及OPEN；映射确认／撤销按实际新脸spec重排各自队列，candidate repair沿用真实face spec，不用普通asset单键队列覆盖source工作。
7. 保留Luna原型0019迁移，通过0020增量升级schema20，沿用备份／恢复流程；允许多个历史批次引用同一job，job逻辑唯一身份仍避免重复推理。合成schema19升级验证人工事实及批次游标保留，未打开真实catalog。
8. UI范围切换、过期响应、轮询失败恢复、暂停／继续／失败重试补齐；首页省略profile的重复请求复用正在运行的同范围任务。

当前决策：[ADR-0017完成方案](../decisions/ADR-0017-person-source-detail-auto.md)。并行产生的[原型ADR](../decisions/ADR-0017-person-source-detail-batches.md)已注明历史被取代，不再作为“跨profile仍关闭／schema19”的当前规则。独立审查：[完整受影响面任务卡](../tasks/P4-PERSON-DETAIL-B-review.md)。

## 受影响表面

| 表面 | 文件／直接调用链 |
|---|---|
| profile、解码、产物、缓存 | `face/{contract,engine,processor,service}.py`、`media/{decode,face_crop}.py`，含复用DETAIL-A改动 |
| 自动发布及持久批次 | `face/index.py`、`face/detail_policy.py`、`face/source_detail_batches.py` |
| schema与领取边界 | `catalog/schema.py`、migrations0019／0020、`jobs/queue.py`、`scheduler/engine.py` |
| 人工与有效成员 | `persons/{mapping,members,albums}.py`；复用`operations._repair_candidates`、face correction及undo派生链 |
| API／worker／资源串行 | `api/{context,models,routes,refresher}.py`、`api/face_detail_runner.py` |
| 默认模式与界面 | `refresh/{pipeline,state}.py`、`web/{app.js,index.html,persons-albums.js,persons.html}` |
| 证据 | 下列六个测试文件、`tools/diagnostics/person_source_detail_compatibility.py`、真实模型JSON |

API创建批次→后台自有Catalog线程→有界页／保护检查→指定job推理或恢复产物→对应generation outbox确认→受保护自动归人→批次计数；建库保存profile→直接走选定face stage→同发布链。未改Qwen/OCR模型、地点实现或依赖；保留CN／地图／依赖等其他共享工作区编辑。

## 定向验证

Python3.12.10，`UV_CACHE_DIR=.uv-cache`，现有`.venv`。最终命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync --python 3.12.10 pytest -q tests/integration/test_p4_person_detail_b.py tests/integration/test_p4_person_source_detail.py tests/unit/test_p4_person_face_geometry.py tests/unit/test_person_source_detail_ui.py tests/unit/test_person_detail_batch_ui.py tests/unit/test_api_refresher_queue.py --basetemp .pytest-tmp/detail-b-final-check --junitxml test-results/P4-PERSON-DETAIL-B-final34.xml
```

退出码**0**；最终独立JUnit：[final34](../../test-results/P4-PERSON-DETAIL-B-final34.xml)，**34 tests / 0 failures / 0 errors / 0 skipped**。此前同名targeted.xml被另一次10项局部运行覆盖，因此本轮完整证据另存，不能拿局部10项作为完整验收。

覆盖选择／去重／混合人工状态／晚到保护、缓存发布中断及重启不重复推理、实际face stage到profile、零脸、暂停任务隔离、策略重开、新旧配置／未知身份门禁、旧机器占位替代、映射确认→撤销→实际source队列、单图失败继续与只重试失败、session／CSRF／范围、schema19升级、JPEG跨帧人工保护、实际JS状态操作和首页FIFO。假模型仅证明控制流。

静态检查（实际退出码0）：

```powershell
uv run --no-sync --python 3.12.10 ruff check src/photo_search/face src/photo_search/api/face_detail_runner.py src/photo_search/api/context.py src/photo_search/api/models.py src/photo_search/api/routes.py src/photo_search/api/refresher.py src/photo_search/refresh src/photo_search/jobs/queue.py src/photo_search/scheduler/engine.py src/photo_search/persons/albums.py src/photo_search/persons/mapping.py src/photo_search/persons/members.py src/photo_search/catalog/schema.py tests/integration/test_p4_person_detail_b.py tests/integration/test_p4_person_source_detail.py tests/unit/test_person_detail_batch_ui.py tests/unit/test_person_source_detail_ui.py tests/unit/test_api_refresher_queue.py tests/unit/test_p4_person_face_geometry.py tools/diagnostics/person_source_detail_compatibility.py --output-format concise
node --check src/photo_search/web/app.js
node --check src/photo_search/web/persons-albums.js
git diff --check -- docs/development-status.md src/photo_search/api src/photo_search/catalog/schema.py src/photo_search/face src/photo_search/jobs/queue.py src/photo_search/media src/photo_search/persons src/photo_search/refresh src/photo_search/scheduler/engine.py src/photo_search/web/app.js src/photo_search/web/index.html src/photo_search/web/persons-albums.js src/photo_search/web/persons.html tests/unit/test_api_refresher_queue.py tests/unit/test_p4_person_face_geometry.py
```

新文件另作Python AST／空白／文档链接检查。未跑全部unit/integration、历史216项、30万容量、打包或跨GPU矩阵。

过程记录：初次uv同步构建hatchling联网失败，改用现有环境`--no-sync`；沙箱pytest临时目录WinError5，正常审批后通过，未改ACL。一次审批服务用量限制令复验未执行，用户继续后正常重试成功。新增缓存复用夹具首次job_id=NULL违反产物约束，补齐实际job；另一个会话随后新增API断言把已登录缺CSRF请求期望写成401，已移到认证前检查401，认证后检查403。用户确认停止另一实现会话后，本轮完整34项再次通过。报告／审查卡恢复与实际代码及真实证据一致，不使用并行旧稿代替当前结论。

## 公开样片真实模型对照

只读使用已安装且SHA校验的buffalo_l权重，未下载模型、未读私人照片或真实catalog：

```powershell
.venv\Scripts\python.exe tools/diagnostics/person_source_detail_compatibility.py --data-root D:\SZ\project\PhotoSearchData --output docs/reports/P4-PERSON-DETAIL-B-compatibility.json
```

[真实对照JSON](P4-PERSON-DETAIL-B-compatibility.json)：scikit-image astronaut与insightface包内t1.jpg两个公开身份、三个缩放／亮度夹具，实际两条解码→640检测→裁剪→112 ArcFace路径，512维L2；实际provider **CPUExecutionProvider**。最初另一个包内照片没检测出脸，改用包内多人夹具并选最大脸，没有将未检测项计作对照通过。

9个同人／异人双向组合通过；同人cosine **0.994685–0.999458**，异人 **0.005534–0.012190**，.50/.29/.08规则未改。固定权重fingerprint `62b5343d…1e74d`、普通config `955550ce…947b`、source config `49ac9be9…b004`与代码资格门禁一致。

| 样片 | 轻量含解码 | 原图含解码 |
|---|---:|---:|
| astronaut | 502ms | 1454ms |
| astronaut亮度变体 | 1048ms | 1113ms |
| 包内多人照片 | 1832ms | 1832ms |

这仅是公开夹具／放大变体的处理兼容证据，三张均OK；不证明原生高清困难小脸改善、私人图库错归率、GPU速度或大库性能。局部高频几何夹具证明源裁剪保留细节。

## 未验与停止

独立DS-H完整受影响面审查（含复用A）、随后Sol high聚焦身份／人工记录终审待做，整卡不标正式PASS。真实浏览器、应用重启、私人样片改善／错归、真实catalog升级和长期性能未验。退出重启应用并刷新页面才能加载当前代码；本轮未以验证为由启动真实库任务。

旧库一键＋新库默认原图＋合格结果自动归人的实现与限定自检完成，停止于独立审查交接；未扩展漏脸分块检测、阈值调整或全库自动升级。

## 2026-10-08 本地提交整理

用户授权简单处理提交。CN及依赖已由父提交`73bca7e`保存（schema18）；A与B的共用代码、0019/0020迁移、相关测试及文档共48个文件合并为完整人脸本地提交，避免拆出不完整A。提交不推送；`.tmp/`及临时清理/保留报告不纳入。本次只整理Git范围、规范LF和交接元数据，未改功能、未运行真实库或重复模型对照。

在当前父提交和工作区复验：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync --python 3.12.10 pytest -q tests/integration/test_p4_person_detail_b.py tests/integration/test_p4_person_source_detail.py tests/unit/test_p4_person_face_geometry.py tests/unit/test_person_source_detail_ui.py tests/unit/test_person_detail_batch_ui.py tests/unit/test_api_refresher_queue.py --basetemp .pytest-tmp/person-detail-commit --junitxml test-results/P4-PERSON-DETAIL-commit.xml
```

退出码0，JUnit **34/0/0/0**，详见[提交复验XML](../../test-results/P4-PERSON-DETAIL-commit.xml)。Ruff同上述直接相关范围、两个修改JS的Node语法和scoped diff-check均退出0；已有真实CPU对照及历史final34保留。检查迁移0001到0020连续、暂存区起始为空、48个明确路径无CN/依赖/临时文件混入。独立审查和私人效果未验状态保持；本地提交仅保存成果，不标正式PASS。
