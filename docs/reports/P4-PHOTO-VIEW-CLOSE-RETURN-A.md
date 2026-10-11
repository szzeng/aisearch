# P4-PHOTO-VIEW-CLOSE-RETURN-A 执行报告

日期：2026-10-11。结论：**SCOPED PASS — 合成 UI 行为验证通过**。

## 变更

- 搜索、人物、共享照片查看器和地点降级预览均支持点击 `<dialog>` 遮罩关闭；对话框内部点击不触发关闭。关闭按钮、Escape 和关闭清理保持可用。
- 共享照片信息面板与搜索页预览的人物头像跳转附带 `from_photo=photo`。人物页识别该上下文，将主返回按钮显示为“返回查看照片”，点击后通过浏览器历史回到来源查看器；普通人物入口继续显示“所有人物”。
- 为相关页面的共享查看器与前端脚本更新静态资源版本，避免浏览器继续使用旧缓存。
- 未改 API、人物/照片数据、身份/人工归属协议或数据库；没有访问真实图库。

## 验证

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py --junitxml=test-results/P4-PHOTO-VIEW-CLOSE-RETURN-A.xml --basetemp=.pytest-tmp
.\.venv\Scripts\python.exe -m ruff check tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py
.\.venv\Scripts\python.exe -m py_compile tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py
node --check src/photo_search/web/photo-info.js
node --check src/photo_search/web/search.js
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/places-albums.js
git diff --check
```

- JUnit：**9 passed / 0 failed / 0 errors / 0 skipped**。
- Ruff、Python 编译、4 个相关 JS 语法检查及 `git diff --check`：通过。
- 合成检查覆盖共享查看器/搜索查看器/人物相册查看器的遮罩和内部点击、共享头像跳转、搜索来源人物跳转、人物上下文返回以及普通人物入口；地点 fallback 和页面缓存版本做直接静态断言。

## 未验证

- 没有运行真实浏览器交互或真实照片库；验证结论仅覆盖合成 DOM/API 与静态资源接线，不代表完整产品/全仓 PASS。
- 没有运行全仓、30 万规模或打包测试；本卡无相关改动和验收要求。