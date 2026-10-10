# P4-TEST-BASELINE-UI-A — 前端静态与人物计数测试轻量维护

日期：2026-10-11。状态：**READY / 未实施**。执行序号 3/4；推荐 **GPT-6 Medium**，由[连续总控](P4-TEST-BASELINE-CONTINUE-A.md)编排。

## 目标

核对旧报告中**12 项前端静态/DOM failures、2 项人物列表性能计数 failures**，以及 [P4-PERSON-COLLECTION-COUNT-A 报告](../reports/P4-PERSON-COLLECTION-COUNT-A.md) 指出的旧 UI 测试人物卡 `children[0]` 为空异常。均为历史/邻近报告现象，**先复现后判断现状**。

优先文件：
- `tests/unit/test_api_frontend.py`；
- `tests/integration/test_p4_person_album_perf.py`；
- `tests/unit/test_p4_person_collection_ui.py`；
- 现有头像复用、人物分页、合集计数直接相关 UI 定向测试。

## 原则

1. 先挑 3～5 个代表失败单独复跑。区分“静态字符串和旧 DOM 结构已变化”与“页面实际渲染中断”。
2. **不能**因为界面已改就删除 XSS / 外部凭据 / CSRF / 安全 DOM sink 等保护断言；应将确已改动的 UI 标识改为能验证**相同安全行为**的测试。
3. 已有 `HTMLCollection.find()` 真实渲染问题在之前头像复用报告里曾修复；需要确认当前 `src/photo_search/web/persons-albums.js` 及测试桩状态。出现新的真实渲染错误，留最小反例，单列生产热修，不在本卡为了变绿而改脚本。
4. 人物列表计数和排序要以当前权威 active canonical / distinct asset / 人工排除逻辑为准；不可通过硬编码为旧数量让性能测试通过。
5. 改动范围仅 `tests/` 与必要测试 helper；不改 `src/`、CSS 或业务计数 SQL。

## 最小验证

- 只跑修改的 UI 测试文件及其直接复用测试桩/DOM 的测试，至少保留 1 个初始渲染、1 个列表/计数、1 个安全 DOM/CSRF 保护反例。
- 改 JS **测试桩**时执行 `node --check`；改 Python 测试时执行 Ruff/py_compile；`git diff --check`。
- JUnit `test-results/P4-TEST-BASELINE-UI-A.xml`、退出码；不做完整浏览器矩阵、30 万性能、真实图库或全仓回归。

## 交付

`docs/reports/P4-TEST-BASELINE-UI-A.md` 中标明已修旧 oracle 与仍需产品热修的问题、定向验证和受影响面；独立提交并更新 status。实际产品回归不能以测试口径修复，也不算本卡 PASS。继续 CORE 卡。
