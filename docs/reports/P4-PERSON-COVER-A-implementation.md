# P4-PERSON-COVER-A — 人物相册封面选择

日期：2026-10-04。状态：IMPLEMENTED / 简单定向验证完成；独立人物工程审查仍PENDING。与整理修复、照片导航一并本地提交；保留首页改名/排队等并行修改，未推送。

用户授权：“增加一个能修改人物相册的封面照片的功能吧，复杂吗”。实现小型个人流程，复用已有avatar_face_id，无新表/迁移/依赖/模型。

## 使用流程与变更

进入人物相册 → “更换封面” → 点照片看大图 → 选择当前人物的脸“设为封面”。选择模式只显示本相册的人脸；取消选择返回普通照片浏览。普通预览也为当前人物提供“设为封面”，其他人只有更正按钮。封面是所选照片中当前人物的人脸裁剪，避免合照混淆人物；不新增自由裁剪。保存后相册标题头像与人物卡片同步更新，成功提示3秒后消失，复用之前的修复。选择的是保存到SQLite的封面，下次读取沿用；若该脸随后失去有效归属，现有只读补选选择仍属于此人的另一张脸。

新增PUT `/api/v1/persons/{person_id}/cover`，严格请求为`face_id`（64位小写hex）、`expected_revision`（正整数），响应`changed`及现有安全相册人物投影。复用全局session/CSRF保护、canonical重定向和MEMBER_PREDICATE；同一catalog.write事务验证当前人物revision及有效成员后，只更新persons.avatar_face_id/revision/updated_at。具名和未命名人物均可使用；他人、SUGGESTED、MANUAL_UNASSIGNED、active排除或缺失人脸均拒绝。选当前存储头像且revision当前时零写入；过期请求409，拒绝覆盖后来修改。

这是独立展示元数据PUT，不把封面记录伪装成RENAME、不扩展冻结person_operations类型或补偿语义。可通过重新选择更换封面，未添加封面专用撤销。现有人工整理撤销读到新的person revision/avatar会拒绝覆盖后来的封面；此边界有直接反例。封面不改变人物成员或搜索过滤事实，因此不移动catalog_filter_revision，也不排队图片/人脸索引投影。命名/归属/人工控制/排除/原图/检测版本不变。

前端防重复提交；失败留在照片预览并显示错误，不乐观替换头像。新封面资源URL按新face_id读取；更新persons-albums.js页面版本。保存时捕获preview序号，晚到结果不关闭另一张后来打开的照片或把错误写进新预览。

## 简单验证

最终JUnit：`test-results/p4-person-cover.xml` **23 tests / 0 failures / 0 errors / 0 skipped**。新增封面API9项和实际JS最小DOM1项；连同已有相册/整理API及提示计时13项回归。

覆盖：未认证/缺CSRF拒绝、有效选择持久写入与独立列表/相册读取、数据库dump差异只含一个persons行、当前选择no-op、他人/缺失/建议/人工移出/排除/过期全部零写入、合并旧ID指向canonical、失效后补选、旧撤销拒绝覆盖新封面；实际JS运行header→照片预览→只显示自己→提交（防重复、CSRF/expected_revision）→新头像同步相册/卡片→普通预览仅本人可设封面→保存失败保持旧封面和重试入口。仅小型合成照片/catalog和模拟DOM/HTTP，不依赖人脸模型。

命令（Python 3.12.10，项目内临时目录）：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_cover.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_cleanup.py tests/unit/test_person_cover_ui.py tests/unit/test_person_cleanup_ui.py --basetemp=.pytest-tmp/person-cover --junitxml=test-results/p4-person-cover.xml
.venv/Scripts/ruff.exe check src/photo_search/persons/albums.py src/photo_search/api/models.py src/photo_search/api/routes.py tests/integration/test_p4_person_cover.py tests/unit/test_person_cover_ui.py
& 'D:/Program Files/nodejs/node.exe' --check src/photo_search/web/persons-albums.js
git diff --check
```

pytest通过正常Windows权限执行，规避已复现的沙箱测试临时目录WinError5；未改ACL。Ruff、Node语法、diff-check通过。

## 生效与限制

重启应用并刷新人物页生效；不需要重新识别或建库。本轮未访问真实库/PhotoSearchData、未重启用户服务，无真实浏览器/真实模型/全量/性能/打包验证。沿用[P4-PERSON-UX-A-review](../tasks/P4-PERSON-UX-A-review.md)补审全部新增接口/UI/元数据与撤销冲突面，不标独立PASS。
