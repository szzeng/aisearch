# P4-PERSON-AVATAR-FIX-A — 原图头像409回归修复

2026-10-09。状态：**IMPLEMENTED / 定向验证通过 / 用户现场重启待确认**。基线`d94279b`加并行搜索／查看器未提交工作区；保留同文件及页面其他改动，未提交或推送。任务卡：[头像修复](../tasks/P4-PERSON-AVATAR-FIX-A.md)。

## 原因与修复

DETAIL-A/B增加source-detail产物校验后，`recorded_face_frame`读取实际`face.spec_revision`，确认原图产物与人脸属于同一profile。头像路由`_face_crop_response`仍只查询face_id／asset_id／bbox／artifact／input，漏了spec_revision；dict访问触发KeyError，被现有安全校验转成409 `FACE_STATE_CONFLICT`，reason=`face_frame_invalid`。

因此完整且有效的原图人脸文档也会被拒绝；已有头像缓存可绕过生成分支，所以旧合成／缓存测试未揭示该问题。普通文档不走source专属字段判断，旧普通头像可正常加载。用户日志只给HTTP状态，未读取其真实响应或catalog；本轮已通过真实API合成反例复现相同409来源，不宣称查看过其私人照片。

修复仅在`api/routes.py::_face_crop_response`的SELECT增加`spec_revision`并传给现有校验函数，保持SQLite查询在API线程。未降低checksum／输入revision／face_id／bbox／profile校验，未改错误信封、crop缓存身份、不可变文档、数据库、人物归属或人工记录。无需删除索引、清理缓存或重新识别人脸。

## 反例与证据

`tests/integration/test_p3_face_thumbnail.py`新增普通／原图×GET／HEAD四个冷缓存用例：真实合成双色JPEG→job／ProcessorSpec→ArtifactStore发布→face持久化→真实头像HTTP路由，验证200、JPEG及正确红色区域、HEAD无body、重复请求及ETag 304。使用合成向量，不运行模型。

修复前命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync --python 3.12.10 pytest -q tests/integration/test_p3_face_thumbnail.py -k published_face_profile --basetemp .pytest-tmp/person-avatar-repro --junitxml test-results/P4-PERSON-AVATAR-FIX-A-before.xml
```

退出1：4项中普通GET/HEAD通过，原图GET/HEAD均409；GET响应明确`FACE_STATE_CONFLICT / face_frame_invalid`，HEAD无body。证据：[反例JUnit](../../test-results/P4-PERSON-AVATAR-FIX-A-before.xml)。

修复后最终命令：

```powershell
uv run --no-sync --python 3.12.10 pytest -q tests/integration/test_p3_face_thumbnail.py tests/integration/test_p4_person_image_frames.py tests/unit/test_p4_person_face_geometry.py --basetemp .pytest-tmp/person-avatar-final --junitxml test-results/P4-PERSON-AVATAR-FIX-A.xml
uv run --no-sync --python 3.12.10 ruff check src/photo_search/api/routes.py tests/integration/test_p3_face_thumbnail.py --output-format concise
git diff --check -- src/photo_search/api/routes.py tests/integration/test_p3_face_thumbnail.py docs/development-status.md
```

全部退出0；[最终JUnit](../../test-results/P4-PERSON-AVATAR-FIX-A.xml) **29 tests / 0 failures / 0 errors / 0 skipped**。包括既有EXIF／draft帧、头像缓存、session、checksum／bbox／内容拒绝和source-profile匹配门禁。新增文档链接／空白核对通过。未跑全量、GPU、模型、性能、打包、真实浏览器或真实图库。

## 交接

源码应用需正常退出并重新启动后刷新人物页；只有刷新浏览器不会更新旧服务里的SQL。若使用旧打包exe，需要包含该修复的构建。现场是否还有其他409未验证，须以实际响应错误码判断，不能一概认为所有409都来自此字段。

该遗漏来自此前本人对原图profile校验调用链适配不完整；已补真实published source文档的HTTP反例。补充进入DETAIL-B既有独立审查，覆盖query投影→recorded_face_frame→preview裁剪，不为此另开模型或全量测试任务。

## 提交整理

2026-10-09按用户“简单提交”授权，只提交头像修复及上述四份交接文档与测试，共六个文件。routes/status仅暂存头像片段，保留同文件里的搜索／查看器并行改动，不推送。提交前导出HEAD加暂存内容的独立快照，用项目Python 3.12.10执行同一组三文件定向pytest，PYTHONPATH指向快照src，basetemp在项目内；[提交快照JUnit](../../test-results/P4-PERSON-AVATAR-FIX-A-staged.xml)再次确认29/0/0/0。
