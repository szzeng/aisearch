# P4-UI-DIALOG-ESC-A 执行报告

日期：2026-10-12。结论：**SCOPED PASS（任务卡合成 UI 范围）**。

## 实际修改

- `persons-albums.js`：新增页面级 Esc 退出管理处理。只响应未被消费、非重复、非输入控件的 Esc；原生打开的 `<dialog>` 优先；优先结束人物相册“整理照片”，否则退出人物/待整理脸批量管理。退出清除选择、snapshot 与对应范围锚点，不发送业务请求、不重新加载列表；保留“清空选择”只取消勾选的行为。
- `persons.js`：`hideFaces()` 仅在可退出时完成整理并返回处理结果；写入或 pending write 时仍拒绝退出。
- `persons.html`：移除冗余“取消选择”按钮，保留唯一的批量模式切换/退出按钮，并更新两项脚本缓存版本。
- 合成 harness 增加 Esc 场景；共享查看器静态缓存断言更新。

## 验证

- `uv run pytest -q tests/unit/test_person_ux_simplify_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py --junitxml=test-results/P4-UI-DIALOG-ESC-A.xml`：**19 passed, 0 failed, 0 errors, 0 skipped**。
- `node --check src/photo_search/web/persons.js`：通过。
- `node --check src/photo_search/web/persons-albums.js`：通过。
- `node --check tests/unit/person_ux_simplify_harness.cjs`：通过。
- `git diff --check`：通过。
- JUnit：`test-results/P4-UI-DIALOG-ESC-A.xml`。

## 未验证

未运行真实浏览器手测；合成事件确认打开原生 dialog 时不把 Esc 传给底层批量状态，但原生浏览器的 cancel/backdrop 与内部空白区域实际命中仍待用户手工检查。搜索、共享照片、人物/合集及地点查看器沿用其已有原生 Esc 与遮罩关闭/close 清理实现，本卡没有改动这些路径。未访问真实图库或 PhotoSearchData，未跑全仓、GPU 或性能测试。
