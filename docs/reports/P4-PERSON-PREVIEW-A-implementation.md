# P4-PERSON-PREVIEW-A — 人物照片上一张/下一张

日期：2026-10-04。状态：IMPLEMENTED / 简单定向验证完成；现有人物独立审查仍PENDING。与封面/整理修复一并本地提交；保留首页等并行修改，未推送。

## 使用与实现

人物相册点照片打开“查看照片”，大图下新增“← 上一张”“下一张 →”和当前“第N张”。按页面照片的现有顺序切换，首张上一张禁用，最终末张下一张禁用，不循环。已加载末尾尚有续页时，下一张复用相册60张有界分页，追加页面后继续展示；人物内搜索则复用同一文本、person_ids和search_session/cursor。不会为了查看提前获取整个相册。不在当前相册列表里的待整理人脸预览隐藏导航。

JS保留本次照片列表的asset_id顺序（分页重复ID去重），每次新相册/搜索/重新加载清空并递增列表序号。导航切换仍走同一个preview，更新大图、人脸裁剪/当前人物标记、封面与更正入口；模态窗口持续打开，不重复showModal。翻页期间防重复请求；分页失败保留当前照片和游标，给出重试提示；关闭窗口或更新照片列表后，旧分页结果不能自动打开下一张。既有preview请求序号继续屏蔽旧人脸返回。封面保存期间导航禁用，避免保存中切换选中脸。

只改变人物JS/HTML/CSS，更新两项静态资源版本；未修改API、排序、归属/索引/数据库，也未增加模型调用。

## 简单测试

JUnit `test-results/p4-person-preview.xml` **7 tests / 0 failures / 0 errors / 0 skipped**。5个新增场景执行实际JS、最小DOM及合成HTTP，另含封面选择和成功提示2项已有回归：

- 非ID排序30→10→70、首尾禁用、跨页重复10去重、上一张回退、分页连点只发一请求、模态只打开一次、列表外预览隐藏导航，大图和人脸同步。
- 分页失败保持当前10，重试成功到70。
- 人物内搜索80→10→50，后续POST保留文本/人物scope/session/cursor，切回全部照片重置列表。
- 分页中关闭预览后结果不重新打开；新列表加载后旧分页不得切图。
- 旧照片人脸请求晚于新照片返回时不覆盖新大图、人脸和位置。

命令：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_person_preview_navigation_ui.py tests/unit/test_person_cover_ui.py tests/unit/test_person_cleanup_ui.py --basetemp=.pytest-tmp/person-preview --junitxml=test-results/p4-person-preview.xml
.venv/Scripts/ruff.exe check tests/unit/test_person_preview_navigation_ui.py
& 'D:/Program Files/nodejs/node.exe' --check src/photo_search/web/persons-albums.js
git diff --check
```

本次测试在沙箱正常完成，退出码0；JUnit数量实际核对。Ruff/Node/diff-check通过。仅实际JS模拟交互，未做真实浏览器、真实照片、服务重启、模型/全量/性能/打包验证。

刷新人物页加载新版资源即可；若服务仍使用旧安装包，需使用包含本改动的新版服务。不需建库。后续沿用[P4-PERSON-UX-A-review](../tasks/P4-PERSON-UX-A-review.md)补审本展示与分页竞态面。
