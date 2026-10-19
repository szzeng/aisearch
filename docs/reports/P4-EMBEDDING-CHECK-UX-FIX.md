# P4-EMBEDDING-CHECK-UX-FIX 执行报告

**结果：PASS（限定在显式检查反馈和 UI 状态一致性）**。基线：`f7b3d1f`。不包含完整索引正确性、真实模型或实际切换重启验收。

## 改动

- `src/photo_search/web/app.js`：模型卡增加可访问状态行；点击后同步显示检查中并阻止重复请求；只按显式响应展示真实覆盖与资格。检查结论绑定 provider、processor、space、generation、profile 和活动状态。候选任务/身份变化、离开后重进或新页面加载使结论失效。
- 过期中的请求保留 busy 锁并标记作废。结果回来后丢弃旧响应、释放锁并刷新状态，避免在途重复请求或旧响应污染新卡。仅当前身份匹配、服务端 `switch_eligible=true` 且覆盖 `required>0` 才显示切换按钮；活动模型保持「正在使用」且不显示重复切换。
- `NO_GENERATION` 显示建库提示；不可切换显示安全原因和覆盖缺口；请求失败只显示安全错误码，不输出原始对象/异常消息/本机路径。`required=0` 显示 `0/0`，不算百分比、不允许安排切换。没有改后端门禁、切换流程或轻量状态 API。
- `tests/unit/test_p4_embedding_check_ux.py`：Node DOM/fetch 交互测试，覆盖即时反馈、重绘忙碌、重复点击、成功、资格拒绝、`NO_GENERATION`、零覆盖、脱敏错误与重试、活动模型、页面重入、任务状态变化在途/完成后失效、generation 变化和迟到响应。

## 验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
$env:PYTHONUTF8 = '1'
uv run --python 3.12.10 -- pytest -q tests/unit/test_p4_embedding_check_ux.py tests/unit/test_embedding_management_status.py tests/unit/test_p4_embedding_management_fix.py --junitxml=test-results/p4-embedding-check-ux-final2.xml --basetemp=.pytest-tmp/p4-embedding-check-ux-final2
node --check src/photo_search/web/app.js
git diff --check
```

- pytest：**12 passed, 1 skipped, 0 failed**（JUnit 13 项）；唯一 skip 为已有原生 symlink 用例创建链接时遇到 Windows `WinError 1314`，与本次 UI 改动无关。
- JS 语法与 diff-check：通过。
- 内置浏览器以单次本机会话进入隔离图库管理页，先确认 status API，再点击合成 EXIF v2 provider 的「检查」。真实产品页面/API 返回「尚无可检查的索引，请先建库」，卡内保留重试且没有「安排切换」按钮；status 与 check 请求均为 HTTP 200。合成夹具位于 `.tmp/P4-EMBEDDING-CHECK-UX-FIX/browser-a060399a54ff4e5e9eeed1a886aecd3c`，5 个占位模型文件合计 0 字节，无图片来源、generation 或 Lance 表；本次不加载模型。夹具保留供复查，无清理。
- Luna High 独立复核首轮发现候选任务状态变化时在途检查会失去 UI 锁；修复为保留 busy 锁并丢弃过期结果。跟进复核确认关闭且无新 finding；复核未额外运行测试/浏览器。

## 未验范围

未加载 Qwen/CUDA，未创建真实向量 generation，未验证实际覆盖完整性/模型质量、切换前端到后端完整闭环或退出重启生效；未访问 `D:\SZ\project\PhotoSearchData`，未做 30 万图库或全仓测试。上述不属于本卡 UI 修复验收，不据此宣称完整模型切换或索引正确性通过。
