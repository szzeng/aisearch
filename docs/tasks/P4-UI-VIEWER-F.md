# P4-UI-VIEWER-F — 查看照片外观与必要往返收口

日期：2026-10-10。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`。依赖 A 及当时已启用的页面任务；不要求为做本卡先强制完成 D/E。

推荐 GPT-6.1 Sol / High；A 级展示小改，独立 DS-H 展示专项。纯样式子项可用 Luna Medium；不默认合并查看器状态机。

## 视觉参考（执行前必须看图）

打开[查看照片模拟图](../mockups/P4-UI-PERSONAL-viewer.png)、[HTML草图查看区域](../mockups/P4-UI-PERSONAL.html#viewer)与[参考规则](../mockups/README.md)。实际看图后实施预览/信息面板/关闭按钮层级，详情完整适窗；图形和字段说明换成真实照片与原API事实。报告保存对应产品合成截图对照；各页既有打开、返回、分页和异步释放规则优先。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4查看/5/6/8；架构§10.1～10.3；策略§2.2～2.4、§3.2～3.4。读 `web/photo-info.js/photo-info.css` 与五页直接预览/详情调用；只核对已改页面的相关报告/返回证据。

允许上述信息组件及首页/搜索/人物/地点/地图的查看器 DOM/CSS，必要的键盘/焦点兼容修复；禁止改 assets API、原图处理、旧照片/搜索会话、预览分页与控制状态。

## 唯一闭环

- 统一大预览、可见关闭、信息折叠、按钮用语；竖图/长图完整适窗。缩略图裁切不能延伸到详情。
- 信息仍来自 `AIPhotoInfo.render/open`，保留来源标记、分辨率、文件位置、人物/地点入口与异步 dispose。旧请求不覆写新照片，地图关闭释放。
- 搜索和人物保留原前后导航；其他入口没有现成导航时本卡不补。详情关闭回到原网格/范围/筛选/滚动，Esc和焦点正确。
- 冲突/保存中/失败/可撤销与不可撤销短而准确；不会用统一 toast 隐藏需要用户处理的错误。
- 不增加通用状态总线、第二套 viewer、全页请求重构或原图批量读取。

## 验证与停止

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_photo_view_info_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_p4_place_map_review_ui.py --basetemp=.pytest-tmp/P4-UI-VIEWER-F --junitxml=test-results/P4-UI-VIEWER-F.xml
node --check src/photo_search/web/photo-info.js
git diff --check
```

实际改动的页面 JS 均加 node check，新增Python测试加Ruff。证据无漂移的未改面沿用旧报告；缺口/新改面才补合成浏览器。最少覆盖本卡改变的每种打开模式、一次快切照片、Esc/关闭焦点、长图/竖图、小窗口及离线原图有缩略图；所有数据在项目 `.tmp/P4-UI-VIEWER-F`。不自动重跑旧全入口组合、全仓、模型、GPU、容量或打包。

独立 DS-H 审完整外观与事件 diff、信息/API 映射和实际证据；若改变会话/分页/权限等语义按总计划§8升级另卡。无新冻结问题 SOL_NOT_REQUIRED。

报告 `docs/reports/P4-UI-VIEWER-F.md`；总计划逐卡记录完成/未启用/待验，不能六卡批量PASS。满足已启用卡的方便使用目标即转个人试用；不自动生成新的相册、时间轴或性能卡。真实照片小样本由用户日常试用反馈，工程与现场结论分开。
