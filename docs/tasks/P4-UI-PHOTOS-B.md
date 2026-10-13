# P4-UI-PHOTOS-B — 照片浏览与图库管理层级

> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [01-photos](../mockups/ui2/01-photos.svg)、[11-management](../mockups/ui2/11-management.svg)、[12-narrow](../mockups/ui2/12-narrow.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-10；2026-10-13 UI 2.0 修订。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`；实施前要求 [A](P4-UI-SHELL-A.md) 与 [CORE](P4-UI-TIMELINE-CORE-A.md) 已验收，执行时检查实际 Git/报告。

推荐 GPT-6 / High；A 级前端展示小改，独立 DS-H 核对全部受影响 UI 与原管理动作接线。

## 视觉参考（执行前必须看图）

打开[照片页模拟图](../mockups/P4-UI-PERSONAL-photos.png)与[图库管理模拟图](../mockups/P4-UI-PERSONAL-management.png)，再查看[HTML草图](../mockups/P4-UI-PERSONAL.html#photos)的照片/管理区域及[参考规则](../mockups/README.md)。实际看图后实施网格和主次操作；示例数字/图形不照搬。报告保存对应产品合成截图，说明重要偏离；不为靠近画面改变任务状态/业务接口。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4照片/§6/§8；架构§5.2、§6.1、§10.1～10.3；策略§2.2～2.4、§3.2～3.4。直接读 `web/index.html/app.js/styles.css`、来源/浏览 API 参数及原测试，不重读全仓。

允许上述三个前端文件、CORE 共享组件接线与定向 UI 测试；业务只读日期接口由 CORE 交付，B 不更改扫描/模型/写协议。

## 唯一闭环（新版时间轴优先）

- 全部照片与单来源照片使用 CORE 的**同一时间轴**：默认可信拍摄时间+按日、可切换入库时间和按月/年分组；年/月筛选与直接跳转是不同控件，侧边年月导航窄屏可收纳。
- 服务端权威日期标题，跨页同日合并、稳定 cursor/时间成员修订；滚动接近底部自动续页、失败时“加载更多/重试”，切时间/来源/年月必须抛弃过期响应。跳到旧年月不能逐页遍历。
- 无可靠拍摄时间/只有文件时间/未抽取分桶且不伪称拍摄；旧 `time_desc/capture_desc/asset_id` 的 API 合同不改，旧高级条件仍可找回；照片日期直接在分页结果中获取，避免逐图 `GET /assets/{id}`。
- 管理 UI 延续“看照片、建库/刷新”优先，改名/移除/完整扫描次级；所有原权限/CSRF/任务状态、查看器原图能力和返回滚动位置保留。无来源/未建库/筛选空/请求失败明确区分，不虚构照片总数。
- 旧静态 PNG 不含新时间轴，只作配色与外壳参考；不因此重造 CORE API、人工整理或第二套网格组件。

## 必须验证

相关原测试：`tests/unit/test_photo_view_info_ui.py`、`tests/integration/test_api_sources_assets.py`、`tests/unit/test_api_source_rename_ui.py`；必要新增 `tests/unit/test_p4_ui_photos.py` 用行为覆盖三种空状态、按日/月/年和年月定位、拍摄/入库切换、连续两页跨日/同日标题、过期响应、日期返回与管理按钮请求不变。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_photos.py tests/unit/test_photo_view_info_ui.py tests/unit/test_api_source_rename_ui.py tests/integration/test_api_sources_assets.py --basetemp=.pytest-tmp/P4-UI-PHOTOS-B --junitxml=test-results/P4-UI-PHOTOS-B.xml
node --check src/photo_search/web/app.js
uv run --no-sync ruff check tests/unit/test_p4_ui_photos.py
git diff --check
```

隔离小型合成浏览器验来源切换、两页照片、详情/返回、空筛选、改名错误及删除提示；管理写操作只针对合成来源，核对与旧按钮相同 payload/CSRF。不能只截图替代事件验证。不运行全仓/真实图库/模型/打包；若出现业务变化按总计划§8升 B/D 另卡，不能用本 A 门槛通过。

独立 DS-H 完整覆盖新增 DOM/事件到原 API 的直接链路，核对原请求/任务状态映射与授权，无新高风险未决则 SOL_NOT_REQUIRED。报告 `docs/reports/P4-UI-PHOTOS-B.md`，记录未验范围；完成本卡停止，下一候选 [D](P4-UI-PEOPLE-D.md)，随后 E/C/SEARCH-MAP/F。
