# P4-UI2-SEARCH-TOOLS-PLACEMENT-A 执行报告

日期：2026-10-19
状态：IMPLEMENTED / ACCEPTANCE PARTIAL

## 修改

- `src/photo_search/web/search.html`：固定搜索栏加入「以图搜图」，页面标题右侧加入「索引状态」；上传与覆盖率移入原生对话框，移除结果区底部旧面板，并同步上传格式列表。
- `src/photo_search/web/search.css`：新增搜索页局部入口、窄屏布局及对话框样式。
- `src/photo_search/web/search.js`：复用现有搜索和覆盖率逻辑；图片请求成功且仍有效后才切换结果，会话文件供自动续页使用；失败/取消保留原结果，世代保护避免过期图片响应覆盖文字搜索。
- `tests/unit/test_api_frontend.py`：更新 UI 契约与格式/响应布局断言。

## 验证

命令：`uv run --no-sync python -m pytest -q tests/unit/test_api_frontend.py tests/unit/test_search_text_mode_ui.py tests/unit/test_p4_ui_search_entry.py --junitxml=test-results/P4-UI2-SEARCH-TOOLS-PLACEMENT-A/frontend-final2.xml --basetemp=.pytest-tmp/P4-UI2-SEARCH-TOOLS-PLACEMENT-A-final2`：103/103 通过。`node --check src/photo_search/web/search.js` 与 `git diff --check` 通过。

内置浏览器使用隔离合成图库。验证固定页头及右上角入口、466px 窄屏无横向溢出、覆盖率点击读取/刷新/关闭重开保留、Escape 关闭与焦点返回、未选文件提示。以确定性假图像编码器验证上传成功、自动进入图片结果及下一页加载；空白模型实例验证上传失败后原结果和选择保留。上传中关闭对话框并提交文字搜索，迟到图片响应未覆盖文字结果。收藏范围下上传被拒绝且未扩大搜索范围；普通搜索、viewer、多选与收藏控件可用。

## 未验证

真实图像模型及检索质量、真实图库、固定合集/人物/拍摄时间范围在浏览器中的逐项操作、实际合集保存操作、全仓回归、30 万规模/延迟及发行检查未执行。没有导出本地截图。故保持 ACCEPTANCE PARTIAL；确定性假编码器只证明 UI/API 流程，不作为真实模型验收。

## 2026-10-19 跟进：索引状态遮罩关闭

「索引状态」打开时绑定现有 `closeOnBackdrop()`，点击原生 dialog 遮罩可关闭；弹窗内容区域不触发关闭。受影响 `test_api_frontend.py` 99/99 通过，Node 语法与 `git diff --check` 通过。当前没有运行中的本地应用/内置浏览器标签，本次未重跑浏览器验证；前序弹窗的 Escape/关闭按钮行为保持不变。
