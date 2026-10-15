# P4-PERSON-FAST-ORGANIZE-A — 实施报告

- 日期：2026-10-15
- 状态：**M1 实施完成；M2 实施完成 / 验收部分完成（BROWSER_PENDING、SOURCE_CROP_PENDING）**
- 实际基线：6c7c681，开工时工作区干净；Schema v31。当前工作区实现迁移至 Schema v32，未提交。
- 任务卡：[P4-PERSON-FAST-ORGANIZE-A](../tasks/P4-PERSON-FAST-ORGANIZE-A.md)

## 交付内容

- 新增 v32 迁移 0032_person_collection_face_members.sql，用软锚点存储合集脸成员，不建立对检测脸或资产的外键；旧 migrations 未改。
- 新增脸级合集成员分页及 add/remove API。写入校验合集实例、revision、脸与内容 SHA；重复操作幂等，只在实际变化时推进 revision。
- collection_condition() 统一把正式人物动态照片与脸级静态照片合并，并按资产去重；合集计数、照片页和照片脸投影使用同一口径。
- 人物合集移除不再改变 person_hidden；TEMPORARY 和 EXCLUDED 状态独立保留。
- People 页增加脸选择、候选组明确选择、脸级批量入合集，以及默认折叠的“单独收录的人脸”区域。混合批量提交分别限制最多 500 人物与 500 张脸，并分别报告部分成功。
- 同内容重检测只有在可信映射唯一、高置信且 group/mapping 已确认时才展示新脸；OPEN、AMBIGUOUS 或多条确认候选继续显示原软锚点，不误指其他脸。映射后候选按当前显示脸批量投影。
- 单脸卡打开原照片时，同步显示该归档脸的头像与状态；无有效脸缩略图时不以照片缩略图冒充人脸。

## 影响文件

- API/schema/query：src/photo_search/api/models.py、src/photo_search/api/routes.py、src/photo_search/catalog/schema.py、src/photo_search/catalog/migrations/0032_person_collection_face_members.sql、src/photo_search/persons/collection_sql.py、src/photo_search/persons/collections.py。
- UI：src/photo_search/web/persons-albums.js、src/photo_search/web/persons.html。
- 验证：tests/integration/test_p4_person_collections.py、tests/integration/test_p4_person_match_b.py、tests/unit/test_catalog_migrations.py、tests/unit/test_p4_person_collection_count_ui.py。
- 交接：docs/tasks/P4-PERSON-FAST-ORGANIZE-A.md、docs/tasks/P4-PERSON-FAST-ORGANIZE-B.md、docs/development-status.md 及其归档。

## 验证

最终定向 JUnit：**31 tests, 0 failures, 0 errors, 0 skipped**。报告：[JUnit XML](artifacts/P4-PERSON-FAST-ORGANIZE-A-targeted.xml)。

执行命令（PowerShell，pytest 输出 JUnit XML）：

    $env:PYTHONPATH='.;src;tests'; $env:PYTHONUTF8='1'; $env:UV_CACHE_DIR=(Resolve-Path .uv-cache).Path
    .venv\Scripts\python.exe -m pytest -q tests/integration/test_p4_person_collections.py tests/integration/test_p4_person_match_b.py::test_collection_person_match_extends_across_synthetic_sources_but_static_face_does_not tests/unit/test_catalog_migrations.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_p4_person_collection_count_ui.py --junitxml=docs/reports/artifacts/P4-PERSON-FAST-ORGANIZE-A-targeted.xml --basetemp=.pytest-tmp/P4-PERSON-FAST-ORGANIZE-A-final2

覆盖迁移 v31→v32、旧数据保真、重复迁移、失败回滚、隔离备份恢复、软锚点 SHA/来源变化、批量 API、去重计数、候选投影、TEMPORARY/EXCLUDED 保留，以及合成来源 A→B 下动态合集扩展和仅静态收录不扩展。

额外检查通过：

- 受影响 Python 文件 Ruff。
- node --check src/photo_search/web/persons-albums.js。
- git diff --check。

Ruff 检查命令：

    .venv\Scripts\python.exe -m ruff check src/photo_search/api/models.py src/photo_search/api/routes.py src/photo_search/catalog/schema.py src/photo_search/persons/collection_sql.py src/photo_search/persons/collections.py tests/integration/test_p4_person_collections.py tests/integration/test_p4_person_match_b.py tests/unit/test_catalog_migrations.py tests/unit/test_p4_person_collection_count_ui.py

新增独立 Luna High 全受影响面复核发现并促成修复：未使用已确认唯一映射、单脸预览未标出对应脸、混合提交误把人物和脸合算上限。Repair-only 复核又发现映射后候选 join 仍使用原脸 ID，已改为当前显示脸并加回归断言；另覆盖多个确认映射的保护。Luna 最终确认修复区域关闭，无新增 finding。该结果是 LUNA High review，不代表 DS-H review。

## 浏览器与未验证项

使用项目内 .tmp 合成库、127.0.0.1 和 Codex 内置浏览器。新的一次性会话通过认证，已观察候选组明确选择、明确 face_id 的确认提示、合集选择器及所选脸数。提交前的确认操作触发 Codex CUA/CDP Emulation.setFocusEmulationEnabled 超时；服务日志无 /faces POST，隔离数据库也确认没有写入。按连续工具限制停止重试。因此浏览器的实际写入、浏览器端成功/失败反馈、合集照片/人物导航及视觉截图仍为 **PENDING**，不能据此判为通过。

来源 A→B 回归使用 RedetectionHarness，实际调用 persist_face_document()、FaceIndexCoordinator.process_all() 和 reconcile_asset()，但输入是合成 FaceDocument，没有走真实 source-crop 解码/检测 worker。任务卡要求的新版 source-crop 路径验收仍为 **PENDING**。合集限定搜索与时间轴目前由共享 collection_condition() 调用链保证并经代码复核，但本轮未补端到端 API 浏览器证据。

另尝试的旧合集管理测试 test_collection_labels_and_unassigned_filter_are_canonical_paged_and_versioned 因其合成 identity 没有有效关联脸而返回空人物页，产生 KeyError；相关生产文件不在本任务改动内。该用例不计入上述 31 项定向结果，后续应修正 fixture 或单独确认旧测试预期。

未扫描或修改真实图片库；未运行全仓、完整 source-crop/模型识别、GPU、容量/性能、打包或发布检查。没有迁移真实 catalog。

## 后续手动试用

在隔离图库的 People 页选择“整理一下”，展开候选组并使用“明确选择本组待整理脸”，确认选中数量及“不会确认人物身份”提示，加入现有合集。然后展开该合集的“单独收录的人脸”，核对对应人脸与原照片、移出该脸，再验证忽略状态不变。最后分别确认加入 person member 后动态扩展与仅加入 face member 时不扩展。

下一闭环为 [P4-PERSON-FAST-ORGANIZE-B](../tasks/P4-PERSON-FAST-ORGANIZE-B.md)，只补浏览器写入/查看路径、搜索/时间轴受影响证据及真实 source-crop worker 的合成验收；不得接触真实图库或下载新模型。
