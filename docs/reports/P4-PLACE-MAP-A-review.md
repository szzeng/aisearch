# P4-PLACE-MAP-A — 简短实现审核

日期：2026-10-04。最新结论：**5 项问题均已修复，本次短审未发现新增问题；6 个定向回归通过。** 本次为用户要求的简审，不代替卡内独立 DS-H 全受影响面审查，不标整卡 PASS。以下保留历史发现，最新结果见文末。

## 发现

1. **[P1] 固定底图组件会被普通提交遗漏。** `.gitignore:23` 的 `dist/` 同时忽略 `web/vendor/maplibre-4.7.1/dist` 内 JS、CSS、worker，当前 `git status`/`rg --files` 只能看到 license。页面引用这些文件，新检出或后续部署可能没有地图组件。补该目录精确例外或迁出被忽略目录，确认所需资源均进入 Git，保留项目构建产物的忽略规则。
2. **[P2] 同坐标聚合无法打开完整成员。** `map.js:7,13` 的展开判断只有 `expansionZoom <= currentZoom` 才取 leaves；source 设置 `clusterMaxZoom=16`，同坐标组返回展开级别 17 时会直接放大到 17，聚合随后消失，只剩互相覆盖的单点。当前 JS 定向反例：target_zoom=17，leaves_calls=0。到不能继续拆分的级别时改为精确成员分页，验证至少两张完全同坐标照片均可进入详情。
3. **[P2] 成员版本和分页请求没有完整防护。** `map.js:8` 不比较 points 与 assets 的 member_version，定向反例已发布版本 1 的“1 张”标题和版本 2 的空网格。`map.js:11,13` 的 cell/加载更多回调也没有 sequence/范围/版本校验，旧范围晚到响应可覆盖或追加到新网格。比对版本再发布，给全部成员请求绑定视野与版本；过期游标应保留视野重载，不能一直禁用加载更多。
4. **[P2] 地图不可用时承诺的网格回退未实现。** `map.js:13` 的组件缺失分支只显示提示且直接退出，定向反例 API 请求数为 0；`new Map()` 的 WebGL 异常也落入外层 catch，无独立列表加载。地图组件失败仍应请求有效 GPS 成员分页，并让查看照片/加载更多不依赖 map 对象。

## 核对与证据

- 阅读新增 map API、迁移、页面/脚本、详情调用链及两份测试；SQL 成员去重、读快照、keyset 分页与加性版本触发器方向合理。
- 核对已有 `test-results/p4-place-map-junit.xml`：2 tests / 0 failures / 0 errors / 0 skipped；与实现报告一致。30 万耗时是已有合成 ASGI 证据，本次没有重复测量或证明真实图库性能。
- `git check-ignore -v` 确认 JS/CSS/worker 命中 `.gitignore:23:dist/`。没有改资源或 Git 忽略规则。
- 用当前 map.js 加最小 DOM/MapLibre 桩执行三个反例，夹具在 `.tmp/p4-map-review/`：

```text
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_review.py --basetemp=.pytest-tmp/p4-map-review --junitxml=test-results/p4-map-review-junit.xml
3 failed, 0 errors, 0 skipped (约 0.9 s)
```

失败对应上述版本混用、组件回退、同坐标展开三项；是明确反例，不是环境错误。桩只验证实际 JS 的分支/请求行为，不作为真实浏览器/WebGL 复验。未运行全量、未启动真实库服务、未访问真实照片；未修改功能代码。

后续只修这四项及相关直接回归，复验同坐标 → 列表 → 详情、缺组件列表、跨版本刷新/旧分页晚到、资源纳入 Git；无需机械重复成功的 30 万测量。修复改变 SQL/数据形状时再补相应计时。实现报告原浏览器记录保留，不能覆盖本次反例。

## 修复后的短审

上轮 4 项关闭：固定 JS/CSS/worker 已解除忽略，普通提交可纳入（当前尚未提交）；实际 JS 桩复验同坐标两位成员入口、缺组件首屏/续页、跨版本重读、旧视野迟到响应均通过。独立重跑 JUnit `test-results/p4-map-rereview-junit.xml`：**4/0/0/0**；`node --check` 与 scoped diff-check 通过。未重跑 30 万测量或真实浏览器。

**[P2] 详情进入地图不再聚焦指定照片。** `src/photo_search/web/map.js:240-244` 的 focus 回调要求初始化 id 等于当前 sequence 且响应版本等于 version。响应在地图 load 前到达时，version 仍为 null，直接丢弃；load 后到达时，refresh 已递增 sequence，仍被丢弃。`/map?asset_id=17` 因此停在默认/恢复视野，可能看不到所选照片。应把聚焦请求绑定到初始化/用户操作的独立上下文，使用 summary/当前有效成员版本核对，并在首次范围刷新中保留这个定位意图；不能让自己的 load/refresh 把它作废。

使用当前 init() 的最小 DOM/MapLibre 桩分别模拟早到/晚到，两种顺序均收到有效单资产响应但 `jumpTo` 次数为 0：

```text
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_review.py --basetemp=.pytest-tmp/p4-map-rereview --junitxml=test-results/p4-map-rereview-junit.xml
4 passed
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_focus_regression.py --basetemp=.pytest-tmp/p4-map-focus-review --junitxml=test-results/p4-map-focus-review-junit.xml
2 failed, 0 errors, 0 skipped
```

仅需修复此定位回归并复验两种响应顺序，以及详情 → 地图实际定位闭环；其他通过证据保留。未修改功能代码或访问真实图库。


## 定位回归修复与复验

新增 P2 已修复。`asset_id` 聚焦意图使用独立状态，不受地图 `load` 触发的 refresh 序号变更影响；焦点单资产响应先暂存，等 points/assets 快照建立当前成员版本后再校验并 `jumpTo`。若响应与当前版本不符，最多按当前版本重取三次；无效/不可见成员不定位。焦点结果晚到时同样按当前已发布版本检查。

修正早到反例顺序后，早到和晚到分别测试真实 `init()`、地图 load 与单资产 API 结果：

```text
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_focus_regression.py --basetemp=.pytest-tmp/p4-map-focus-review --junitxml=test-results/p4-place-map-focus-junit.xml
2 passed, 0 failed, 0 errors, 0 skipped
```

结合前四项的 JS 定向复验，本次仅执行隔离 DOM/MapLibre 桩测试，没有启动完整浏览器、真实图库服务或重跑 30 万数据计时。

## 定位修复后的独立短审

复核当前 `focusIntent`、单资产响应暂存及 `fetchSnapshot → applyFocusIntent` 调用链：聚焦不再受自身 load/refresh 序号变化影响，使用已发布成员版本核对；意图在 jumpTo 前清除，避免自身移动重复定位。早到夹具已补实际 load，早/晚均执行 jumpTo 一次。只复核本次修复和前四项直接回归，未发现新增问题。

```text
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_review.py .tmp/p4-map-review/test_focus_regression.py --basetemp=.pytest-tmp/p4-map-final-short-review --junitxml=test-results/p4-map-final-short-review-junit.xml
JUnit: 6 tests / 0 failures / 0 errors / 0 skipped
node --check src/photo_search/web/map.js
scoped git diff --check
```

上述静态检查通过，5 项问题在本次短审范围内关闭；无功能代码修改、真实浏览器复验、真实图库访问或 30 万重测。完整独立 DS-H 审查仍待做，不能将本次桩复验扩大为整卡 PASS。

提交前将现有六项回归固化到 `tests/unit/test_p4_place_map_review_ui.py`，JS 夹具位于 `tests/unit/fixtures/p4_place_map_*_probe.js`，不依赖临时目录。以该正式入口复跑 JUnit `test-results/p4-map-commit-junit.xml` 为 **6/0/0/0**，Ruff 与夹具 JS 语法检查通过；只调整测试保存路径，没有新增产品行为或重跑容量测量。
