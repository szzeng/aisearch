# P4-SEARCH-RETURN-A 实现与验收记录

日期：2026-10-03
状态：**IMPLEMENTED / TARGETED VALIDATION PASS / BROWSER ACCEPTANCE BLOCKED**

## 变更

- `src/photo_search/web/search.js` 使用版本化的 `sessionStorage` 键保存搜索页临时现场：表单条件、已渲染 hit、搜索 session/cursor、结果数量、候选总数、地点感知状态、会话条件指纹和滚动位置。
- 成功显示结果后更新快照；点击「查看详情」前同步更新滚动位置。
- 返回 `/search` 后，在来源选项加载完成时恢复筛选条件及人物选择，并通过现有 `renderPage()` / `buildHit()` 重建结果。恢复路径不调用搜索 API；恢复滚动使用 `requestAnimationFrame`。
- 搜索结果进入首页详情时，用标签页内的来源标记记录入口为 `/search`。详情页「返回浏览」据此回到 `/search`；从首页图库打开的详情仍留在原来的图库返回流程。浏览器后退路径也会清除这个一次性来源标记。
- 用户开始新搜索、上传搜图或点「重置」时清除旧快照。重置也清空当前结果现场。
- 旧会话/游标过期时保留结果并显示已有重搜提示，同时隐藏失效的「加载更多」按钮。
- `tests/unit/test_api_frontend.py` 增加静态契约：确认保存/恢复、详情点击快照、恢复路径不调用搜索，以及新搜索/重置清理快照。搜索页契约允许仅保存现场的 `sessionStorage`；首页仍禁止存储。

## 自动验证

| 检查 | 结果 |
|---|---|
| `uv run --no-sync pytest -q tests/unit/test_api_frontend.py --junitxml=test-results/p4-search-return-a.xml` | **95 passed / 0 failed / 0 errors / 0 skipped** |
| `node --check src/photo_search/web/app.js`、`node --check src/photo_search/web/search.js` | 通过 |
| `git diff --check` | 通过 |

JUnit XML：`test-results/p4-search-return-a.xml`。

## 浏览器验收

创建了 24 张合成照片和独立数据根：`.tmp/P4-SEARCH-RETURN-A-smoke/`，数据根位于该目录下的 `PhotoSearchData 数据`。夹具使用确定性合成向量，不访问真实照片库、不下载模型。

浏览器步骤未能执行。服务进程完成应用启动后，Windows 拒绝绑定 `127.0.0.1:8765`（`WinError 10013`）；通过常规提权执行重试仍失败。服务随后退出，没有浏览器页面、搜索请求或详情返回可观察。因此卡片要求的滚动恢复、详情页「返回浏览」回到原搜索、返回不重搜及「加载更多」浏览器确认仍为 **BLOCKED / UNVERIFIED**，整体不标 PASS。

合成夹具报告指出此夹具的语义查询编码器不可用；若恢复本机服务权限后继续浏览器核对，应使用纯筛选查询检查返回/分页交互，或换用具备确定性查询编码器的专用夹具。不得把纯筛选结果表述为关键词搜索验收。
