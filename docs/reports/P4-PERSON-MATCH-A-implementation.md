# P4-PERSON-MATCH-A — 门限与已存人脸重匹配

2026-10-03；IMPLEMENTED / SCOPED VALIDATION COMPLETE；独立审查PENDING。[授权卡](../tasks/P4-PERSON-MATCH-A.md)。在已有未提交的人物几何/UI及首页/搜索并行改动上补充，保留其余编辑；未提交或推送Git。

## 变更与依据

用户授权自动归人余弦相似度0.56→0.50。检测0.50、建议0.29、near-tie0.08及小脸/模糊/低分/同图冲突/人工控制不变。

第三方[DeepGHS buffalo_l评测](https://huggingface.co/deepghs/insightface)最大F1门限：合并集0.2203，LFW0.2469；[源码](https://dghs-realutils.deepghs.org/main/_modules/realutils/face/insightface/extract.html)确认为归一化点积。这是成对验证，不能当多人物相册最优门限。[Immich默认配置](https://docs.immich.app/install/config-file/)buffalo_l maxDistance0.50；[查询实现](https://github.com/immich-app/immich/blob/main/server/src/repositories/search.repository.ts)用余弦距离，转换为相似度0.50。它是个人试用参考，不是本机新测量。

CALIBRATION保留2026-09-14八身份历史数据和原0.56/0.29推导；ASSOCIATION_POLICY单列用户选择、参考及revision。关联门限不进入检测payload/ENGINE_CONFIG，因此不改变spec/preprocess、face_id、不可变artifact/checksum或候选generation，不重算特征。

## 使用与限制

在项目目录关闭运行中的应用后，使用同一应用数据根：

```powershell
.venv\Scripts\python.exe -m photo_search.face.cli rematch --data-root D:\SZ\project\PhotoSearchData --json
```

目录来自当前交接中的个人配置，本轮没有访问它；若启动器用其他数据根，填写实际目录。命令预检catalog/catalog.db存在，错误目录返回CATALOG_UNREADABLE而不建空库。

--limit 100可限制一轮最多100个资产；默认100000。结果含selected_assets/processed/merged/suggested/deferred、策略revision及门限。deferred表示候选尚未确认或无活动索引，保留queue；退出0只表示本轮处理完成，不表示所有人脸已归人。

rematch_unassigned复用规范向量、既有候选发布/确认和reconcile事务门禁；不读原图、不校验/加载模型、不建检测job，不改图文/OCR索引。只重新考虑无CONFIRMED关联的机器脸，跳过人工锁、异空间、非ACTIVE/非当前内容以及新版产物已取代的旧机器脸。新增可选asset_ids限定reconcile范围，原group/detect调用不变。

人工指定、人工移除、排除及OPEN映射hold保持；已归入人物的自动关联也不拆散，人物名称/ID不替换。已经拆成多个组的人物仍在页面显式“并入”，不是全库破坏性重聚类。

仅门限变化无需重新建库。完全漏检、先前ARTIFACT_INVALID失败或要应用前轮v2几何修复时，另跑人脸检测即可，不重建图文库。既有detect --force --asset-id <id>提供有界重检测，会运行模型，新旧人工关系走映射审查，不自动确认。普通刷新/group仅处理现有queue，不能替代显式rematch。新增的是CLI，没有新网页入口。

## 验证

最终命令（直接项目Python3.12.10，无依赖变更）：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_p3_face_contract.py tests/unit/test_p3_face_association.py tests/integration/test_p4_person_rematch.py tests/integration/test_p4_person_face_upgrade.py tests/integration/test_p3_face_redetection_hold.py tests/integration/test_p3_face_correction_lifecycle.py --junitxml=test-results/p4-person-rematch.xml --basetemp=.pytest-tmp/p4-person-rematch-confirmed
.venv\Scripts\ruff.exe check src/photo_search/face tests/unit/test_p3_face_contract.py tests/unit/test_p3_face_association.py tests/integration/test_p4_person_rematch.py
.venv\Scripts\python.exe -m photo_search.face.cli rematch --help
git diff --check
```

均退出0；[JUnit](../../test-results/p4-person-rematch.xml) 79 tests / 0 failures / 0 errors / 0 skipped。覆盖0.50边界，旧0.56产生的0.53建议在v1/v2均归原人物；检测/向量/人工控制/排除和既有归属前后不变；低质量、hold、重复运行、零limit、缺失索引、错误目录/负limit、零脸新产物和异空间拒绝。

首次沙箱42例通过、4例setup因.pytest-tmp创建WinError5失败，保存test-results/p4-person-rematch-sandbox.xml，记环境失败；通过正常权限路线重跑，未改ACL。补充反例初次失败因fixture只有face facts而未真正发布零脸artifact；补齐真实ArtifactStore发布后单例及最终79例通过，未降低断言。

未访问真实库/PhotoSearchData、未加载模型、未执行实际库重匹配、未跑全量/30万/性能/打包/浏览器。合成验证不证明真实误认率/召回率；独立审查仍待做，加入[P4-PERSON-UX-A-review](../tasks/P4-PERSON-UX-A-review.md)，不标正式PASS。
