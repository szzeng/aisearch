# P4-PERSON-FAST-ORGANIZE-B — 验收闭环报告

- 日期：2026-10-16
- 状态：**部分验收完成（SOURCE_CROP_PASS、SEARCH_TIMELINE_PASS、BROWSER_PENDING）**。
- 关联任务：[任务卡](../tasks/P4-PERSON-FAST-ORGANIZE-B.md)；A 实施报告：[P4-PERSON-FAST-ORGANIZE-A](P4-PERSON-FAST-ORGANIZE-A.md)。
- Git 基线：`53e4e3d`（工作区原本干净；仅新增/修改本报告点名的测试和交接文件；未提交、未推送）。

## 已完成

### Source-crop 来源 A→B

新增 `tests/integration/test_p4_person_match_b.py::test_source_crop_worker_publishes_indexes_and_reconciles_into_dynamic_collection`。A 中的合成人脸以手工 `CONFIRMED` 关系归入人物 P，P 加入动态合集；B 是隔离图库中的另一张真实合成 JPEG。测试沿当前 source-detail worker 边界实际读取并校验原图、使用 `preserve_source_detail=True` 解码原图、执行真实 `warp_aligned_source_crop()`，再生成新版 FaceDocument、写入 ArtifactStore 并调用 `persist_face_document()`。

随后使用 `FaceIndexCoordinator` 发布新版候选并运行 `reconcile_asset()`。结果确认 B 的新版脸归到 P，动态合集照片数从 1 扩展到 2；只静态收录 A 脸的另一合集仍为 1。测试显式检查 source-detail 解码和源图裁脸函数各执行一次，裁脸输入尺寸为 320×240。

检测输出与 512 维向量由确定性合成替身提供；本机未加载或下载真实人脸模型，也未运行真实模型/GPU 推理。因此本证据证明当前 source-crop 图片解码、处理、持久化、索引、reconcile 和合集扩展链路，不代表真实模型识别精度或硬件验收。已有同文件用例同时复核 `TEMPORARY` 可作为参考、`EXCLUDED` 不作为普通候选、仅静态 face member 不扩展 B。

### 合集搜索与时间轴

扩展 `tests/integration/test_p4_search_collection.py::test_collection_scope_is_applied_by_fts_and_vector_candidate_walk`：同一合集含一个动态人物成员与一个没有人物关联、仅静态收录的人脸。集合限定把两张照片都传给向量候选入口和语义搜索 service，合集时间轴返回相同的去重资产集合。语义测试使用 `FakeEncoder` 与 `ServiceRecordingIndex`，证明的是 collection scope plumbing，不验证真实语义相关性或排序；原有 FTS 断言继续验证动态成员过滤。本次没有改动产品查询实现。

## 浏览器状态

- 使用新建的 `.tmp/P4-PERSON-FAST-ORGANIZE-A/browser/fixture3` 合成库、本机 `127.0.0.1:18932` 服务和新的一次性会话链接；未访问真实图库。链接仅打开一次，报告不保存链接或 credential。
- 会话交换返回 303，首页及认证后的 People 页面/API 均返回 200。浏览器中看到了 2 张待确认脸的候选组，展开后明确选择本组并确认选择提示；选择器中选中了既有测试合集。
- 点击“加入所选人脸到合集”时，内置浏览器被 JavaScript 确认框中断；随后读取状态重现 `Emulation.setFocusEmulationEnabled` 超时。按任务卡停止浏览器操作，没有重开链接或重试。只读检查隔离数据库确认 `person_collection_face_members` 行数为 0、合集 revision 仍为 1；**浏览器写入和成功回显未发生**。
- 因超时，未完成合集内单脸区查看/折叠、打开原图和对应 crop、移出后计数与忽略状态核对；未完成浏览器内合集搜索/时间轴路由。没有可导出的真实浏览器截图。

状态分别为：浏览器交互 **PENDING**（入口、认证和选择已观察，最终写入未完成）；截图 **PENDING/未导出**；视觉 **PENDING**；API/合成链路见上文 **PASS**。

## 验收命令与结果

定向 JUnit：`docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-targeted.xml`，**4 passed、0 failed、0 errors、0 skipped**。包含 source A→B worker、静态收录不扩展及 TEMPORARY/EXCLUDED 边界、合集限定搜索和时间轴成员范围。

```powershell
$env:PYTHONPATH='.;src;tests'; $env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m pytest -q `
  tests/integration/test_p4_person_match_b.py::test_collection_person_match_extends_across_synthetic_sources_but_static_face_does_not `
  tests/integration/test_p4_person_match_b.py::test_source_crop_worker_publishes_indexes_and_reconciles_into_dynamic_collection `
  tests/integration/test_p4_search_collection.py::test_collection_scope_is_applied_by_fts_and_vector_candidate_walk `
  tests/unit/test_p4_ui_timeline_core.py::test_person_and_collection_scopes_use_effective_unique_photo_members `
  --junitxml=docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-B-targeted.xml `
  --basetemp=.pytest-tmp/P4-PERSON-FAST-ORGANIZE-B-final2
.venv\Scripts\python.exe -m ruff check tests/integration/test_p4_person_match_b.py tests/integration/test_p4_search_collection.py
git diff --check
```

Pytest、Ruff 和 diff-check 最终均通过。没有运行全仓、GPU、容量、性能、打包或发行验证；没有迁移真实 catalog。

## 独立复核与后续

按用户指定由 GPT-6 Luna / high 子代理独立复核新增测试范围，**无 finding**。复核确认 source-crop 用例确实经过真实解码、源图裁脸、ArtifactStore、文档持久化、候选索引和 reconcile；检测输出/embedding 是合成替身。合集范围覆盖动态和静态成员，搜索测试只证明 scope plumbing，不证明语义相关性或排序。该结果记录为 **LUNA High review**，不是 DS-H review。

本闭环尚未满足整卡 PASS。仅当浏览器内最终写入/回显及卡片要求的合集查看、移出、计数和忽略状态路径有新证据后，才能关闭 B；不要重用本次一次性链接。A 与 B 全部闭环前，Fast Organize 整体保持未通过状态。
