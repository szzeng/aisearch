# P4-PERSON-INBOX-COMPARE-A — 执行报告

日期：2026-10-12。状态：**SCOPED IMPLEMENTED / 定向合成验收通过；用户页面观察待完成**。

## 实施内容

- `src/photo_search/web/persons-albums.js`：候选分组摘要展示待整理代表脸和参考头像。参考人物通过 `group_key` 中的 `candidate:<person_id>` 精确匹配 `group.face.suggestions[]`，不采用顺序首项；ID 不匹配或头像为空时显示“暂无参考头像”。逐脸卡右侧只显示首选建议头像，文字和“确认是”按钮复用同一建议对象。右侧不增加选择、预览或写入事件；待整理人脸原预览入口和 checkbox 保留。已忽略脸不增加建议对照区。
- `src/photo_search/web/persons.css`：新增局部自适应双列布局，头像最大约 3.5rem，并限制组摘要宽度。
- `src/photo_search/web/persons.html`：为人物脚本和样式更新缓存版本。
- 定向 UI 测试补充错序多候选、组键 ID 不匹配、候选无头像、首选候选与按钮一致及无候选退化；同步两个过期断言（缓存版本和已存在的 `/500` 选择计数）。
- 未扩展后端接口或增加人物请求；没有改自动识别、归属、候选排序、分页、selection 写语义或数据结构。

## 验证

最终命令与结果：

- `$env:UV_CACHE_DIR='.uv-cache'; uv run --locked --no-sync pytest -q tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_loaded_pages_ui.py --junitxml=test-results/P4-PERSON-INBOX-COMPARE-A.xml` — **3 passed，JUnit 3/0/0/0**。
- `node --check src/photo_search/web/persons-albums.js` — 通过。
- `git diff --check` — 通过。

首轮验证因新增测试变量重名，以及两个旧断言过期而失败；修正测试后上述最终命令全通过。

## 未执行 / 待确认

- 尚未由用户在实际人物页手工观察头像观感、小屏布局或真实图片错误退化；因此不记录完整产品 PASS。
- 未访问真实图库或 `PhotoSearchData`，未运行全仓、GPU、性能、浏览器矩阵或打包测试（本卡未要求）。
