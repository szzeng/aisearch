# P4-PHOTO-RANDOM-BROWSE-TIME 执行报告

状态：**IMPLEMENTED / VALIDATION PENDING**（2026-11-26）。随机页优先显示可信拍摄时间，否则显示可安全访问文件的创建时间，再否则显示时间未知；普通列表保留加入时间。

随机摘要一次批量查本页最多 50 个资产的可信拍摄时间，规则版本不匹配时不信任该数据。仅缺少可信时间的资产沿现有受控位置顺序查文件元数据，Windows 才以 ctime 作为 birthtime 的回退；不读取照片内容。随机摘要新增 `display_time`，移除入库时间字段；抽样、排序和分页未改。未引入 migration、全库扫描或额外 HTTP 请求。

## 验证

- 定向 JUnit：22 passed，0 failed，0 skipped；覆盖优先级、失效规则、POSIX ctime、Windows 回退、位置失败后续接、顺序及随机/普通文案。`test-results/P4-PHOTO-RANDOM-BROWSE-TIME-final.xml`。
- 初次组合运行 1 项失败：测试用例修改全局 `os.name` 导致 Windows 的 pathlib 不能构造 PosixPath；改为只模拟模块平台标识后，整组复跑通过。原始 JUnit `test-results/P4-PHOTO-RANDOM-BROWSE-TIME.xml` 保留。
- Ruff、Python 编译、Node 语法和 diff-check：见最终执行结果。
- 浏览器：当前内置浏览器无已打开的隔离应用，仓库 `.tmp` 未找到现成随机浏览夹具；未打开真实图库。隔离合成照片真实页面检查仍 **PENDING**。
- 本次变更的独立只读复核仍 **PENDING**；实现者已检查差异，不能将自检作为独立复核。必需验收尚未完成，不标 PASS。

## 复现及边界

```powershell
$env:UV_CACHE_DIR='.uv-cache'; $env:TMP='.pytest-tmp'; $env:TEMP='.pytest-tmp'
.venv\Scripts\python.exe -m pytest -q tests/unit/test_p4_random_browse.py tests/integration/test_p4_random_browse_api.py tests/unit/test_p4_ui_photos.py --junitxml=test-results/P4-PHOTO-RANDOM-BROWSE-TIME-final.xml
.venv\Scripts\ruff.exe check src/photo_search/api/queries.py tests/unit/test_p4_random_browse.py
.venv\Scripts\python.exe -m py_compile src/photo_search/api/queries.py tests/unit/test_p4_random_browse.py
node --check src/photo_search/web/app.js
git diff --check
```

未运行全仓回归、30 万规模测试、真实图库或发布验收；不作容量或延迟承诺。未提交、推送或发布。
