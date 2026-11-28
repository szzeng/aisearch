# P4-PLACE-MAP-BAIDU-A 设计与坐标门禁报告

日期：2026-11-28；状态：**IMPLEMENTED / VALIDATION PENDING**。仓库 HEAD `70a7520`；提案基线 `3d6ad4f` 已过时。用户后续明确要求先实现；尚未完成地图替换，未提交、未推送。

## 核对结果

- 当前主地图是 MapLibre；`map.js` 的 viewport、照片点、网格成员、分页、深链和返回状态相互关联。`map_api.py` 已有 WGS84 bbox、`_grid_expression`、`_bounded_grid`、版本和成员分页；原卡所需强制网格模式仍需后续实现并保护默认响应。
- [百度坐标文档](https://lbsyun.baidu.com/docs/jsapi?title=jsapi4/guide/concept/coord) 写明默认输入输出为 BD-09，而全局设置描述的是**输入**坐标；不能据此证明 `getCenter()`、`getBounds()` 或点击事件输出为 WGS84。
- [PointLayer 官方类参考](https://lbs.baidu.com/jsapi/refdoc/v4/classes/BMap.PointLayer.html) 支持批量 GeoJSON 和拾取，但该页没有列出 `coordType`；不能据此证明图层坐标遵循全局设置。
- 官方说明 Convertor 支持 WGS84 → BD09，但该正向转换不能解决未证实的 WGS84 视野边界；不能自行逆算，也不能把 BD09 边界作为 WGS84 查询。

## 推荐方案与待决门禁

1. 先在隔离合成数据的本地验证页加载 JSAPI 4.0，用户自行输入有 JSAPI 权限、允许本地 Referer 的浏览器端 AK。凭据只在浏览器内存中，记录的结果和截图均不得包含 AK。SDK 成功与底图可用分别记录。
2. 在国内和海外已知 WGS84 合成位置，分别记录输入点、返回中心、视野四角、地图点击、可见标志点、PointLayer 拾取的 `asset_id`、深链定位及相对已知位置误差。覆盖移动、缩放、跨日期变更线及世界视野。需事先确定独立参考和容差；屏幕上看起来接近不能证明边界足够准确。
3. 如果 PointLayer 不遵守全局输入设置，才测官方 Convertor 的允许批量、请求次数、延迟、配额及失败处理；仅转换当前视野有界数据，不写回数据库。若仍不能可靠提供 WGS84 边界或正确显示点，则按原卡 NO-GO。测试尚未执行时为 BLOCKED，不能认定技术上不可行。
4. 只有 POC GO 且允许进入实施后，复用 API 和业务状态，增设可选网格模式；保留旧默认行为、上限、`cell_id`、`grid_zoom`、`member_version`、48 张排序分页以及失败回退。新百度配置独立存储；不读取或覆盖旧天地图配置。
5. 先确定 PointLayer 对数量文字、点击与更新清理的实际支持，再确定渲染方式；不以逐照片 DOM Marker 替代批量图层。异步请求按视野/版本序号拒绝迟到结果，地图不可用时保留列表。

## 验证和未完成项

后续使用独立验证页及用户自行输入的 AK：JSAPI 与北京、伦敦底图可见；中心输入/读回分别为北京 116.397,39.908 / 116.3969999161,39.9079990706，伦敦 -0.1276,51.5072 / -0.1276,51.5071992902。视野边界有返回但未用独立参照验证其坐标系及完整性；伦敦地图点击返回经纬度但未独立验证精度。点图层未显示；发现验证页错误使用 addNormalLayer 而非官方示例 addLayer，已修正测试页但尚未重测，因此不是 SDK 能力结论。没有转换批量或完整坐标误差证据。不能标记 POC_GO 或整任务 IMPLEMENTED / PASS。

用户要求先实现后，增加独立百度配置与受保护 API、可选 grid 参数；原天地图配置及默认点响应保持。定向 `pytest -q tests/unit/test_baidu_map_provider_config.py tests/unit/test_map_provider_config.py tests/integration/test_baidu_map_provider_api.py tests/integration/test_p4_place_map_provider_api.py tests/integration/test_p4_place_map_api.py --basetemp=.pytest-tmp/baidu-grid --junitxml=test-results/baidu-grid.xml`：23 passed / 1 skipped，退出 0；skip 是 Windows 符号链接权限缺失。网格及计数缓存 `pytest -q tests/unit/test_p4_perf_map_grid.py tests/integration/test_p4_perf_map_count_cache.py --basetemp=.pytest-tmp/baidu-grid-related --junitxml=test-results/baidu-grid-related.xml`：36 passed，退出 0。Ruff、Python compileall、git diff --check 通过。以上为组合证据，不是全量或性能验收。

共享 SDK 加载器已添加，但未接入产品页面；Node 模拟单次加载、WGS84 设置与不同 AK 拒绝，及原天地图配置 JS 回归 2 passed（`test-results/baidu-loader.xml`），Node 语法通过。模拟测试不能证明真实 SDK 行为。主地图和详情小地图仍为旧实现；点图层、设置 UI、聚合联动、故障回退、竞态、性能和浏览器实测、独立审查全部 PENDING；未访问真实图库。

后续实现验证需按原卡完整执行定向 pytest（JUnit）、Ruff、compileall、Node 语法、diff-check、合成数据性能、实际浏览器及用户真实 AK 验收；此处不把计划当成结果。

## 2026-11-28 后续联网坐标实测

使用用户自行提供的浏览器端 AK、127.0.0.1 独立验证页及合成坐标。修复验证页加层方式为官方 `map.addLayer` 后，北京 PointLayer 蓝点可见，但原始 WGS84 点显著偏离地图视野中心；伦敦原始点目视处于中心附近。北京单点经官方 `Convertor.translate(points, 1, 5, callback)` 后得到 116.4096217502,39.9157489255，显示在中心附近；此处为单次目视观察，不是精度或批量性能结论。

在北京视野的一次读取中，`getCenter()` 为 116.3964613791,39.9079975989；`getBounds()` 西南 116.3790381095,39.9019080935、东北 116.4391167820,39.9295781908，其矩形中心约 116.4090774458,39.9157431422，接近 BD09 转换点而不接近 WGS84 中心。中心点击事件亦返回约 116.4090774458,39.9160765911。由此推断本次运行的视野边界不能直接当作 WGS84；不声称这证明百度全部 API 或其他版本都无法支持。PointLayer 点击事件被地图点击事件覆盖，成员 ID 尚未确认。

原卡禁止自行逆转换、用 BD09 bbox 查询、未可靠获得 WGS84 边界时继续迁移；因此本方案 **NO-GO**。未测批量、配额、耗时、世界/日期变更线或失败处理；单次转换不能证明可用性。现有产品页面仍使用天地图，不应用不安全的 bbox。要继续需先找到并验证符合原卡约束的官方 WGS84 视野边界方案，再恢复地图替换。

## 官方视野与逆转换路径补查（2026-11-28）

再次用同一隔离验证页检查 `pixelToPoint()`：北京视野中心返回 116.4090774458,39.9157445508，与当时 `getBounds()` 的中心一致，而 `getCenter()` 为 116.3964613791,39.9079975989；像素左上和右下也分别与边界对应角一致。拖动后中心像素、边界和 `getCenter()` 仍有同类差异。由此这次测试未发现可直接取得 WGS84 视野边界的像素接口；只说明本次环境和调用，不推广到所有实现。

[JSAPI Convertor 参考](https://lbs.baidu.com/jsapi/refdoc/v4/classes/BMap.Convertor.html)总述使用“互转”措辞，但其目标参数仅列 BD09 经纬度/米制；[百度坐标转换 Web API 文档](https://lbsyun.baidu.com/docs/webapi?title=geoconv/guide/changeposition-base)明确转换到 GPS 坐标不支持。[百度 JS API FAQ](https://lbs.baidu.com/faq/details?id=2315&title=2751)也写明 JS API 不支持百度坐标转其他坐标。现有可见官方证据不支持使用 BD09→WGS84 方案。不能用地图中心反复平移推算四角代替官方合同，或扩大为整库 GPS 转换以绕开原卡限制。

结论仍为 POC NO-GO；未改变产品地图或数据、未增加真实图库访问。后续只能等待百度提供/确认支持 WGS84 视野边界的官方接口，或由用户明确调整地图方案及数据合同后重新设计。

## 用户确认使用正向转换后的进展（2026-11-28）

用户说明其他项目使用 WGS84 → BD09LL 并希望先实现、之后自行检查位置。共享 `AISEARCH_BAIDU.convertPoints` 现调用官方 `BMap.Convertor.translate(..., 1, 5, ...)`，单批最多 10、总输入最多 1000、顺序串行、超时和失败均拒绝输出；仅用于将来调用者明确提供的有界坐标，目前尚未接入产品或上传图库坐标。10 是保守工程批次，参考旧版官方上限，不宣称已完成 JSAPI 4.0 的批量/配额实测。Node 模拟验证 23 个输入分为 10/10/3、保持顺序和失败处理；新旧 loader 合计 3 passed，JUnit `test-results/baidu-forward.xml`，Node 语法及 Ruff、diff-check 通过。

只读参照本机图迹 Android 项目发现，其视野筛选使用视野边界增加固定余量，之后对候选点做正向转换和屏幕投影；这不是本卡要求的可靠 WGS84 bbox 证明，Android 地图投影行为也不能直接等同 JSAPI 4.0。用户同意事后检查位置不自动证明其允许改变视野成员合同或使用近似边界。需要明确该权衡，或取得官方 WGS84 边界证据后才能完成安全接入。

## 正向批次与拾取补测（2026-11-28）

在本地隔离验证页、真实百度 JSAPI 4.0 和用户提供的 AK 下，仅发送北京附近合成坐标：10、11、100 点各进行一次 `Convertor.translate(...,1,5)`，返回状态均为 0，返回点数分别为 10、11、100，观察耗时分别 29、28、28 ms；没有其他有效样本，非性能保证，也未验证服务配额、并发或是否发出网络请求。保留实现的每批最多 10 是保守选择，不从单次结果断言 100 是上限。转换后的合成北京 PointLayer 点击事件 `value.properties.asset_id` 成功取回测试 ID。

这些结果确认当前样本的正向转换与点击可用，但没有验证 bbox 是 WGS84，也不构成产品实现或坐标门禁 GO。未使用真实图库。

## 百度地图试用实现与验证（2026-11-28）

用户要求直接实施已说明的扩大候选范围方案。主地图使用百度 JSAPI 4.0 和 PointLayer，复用服务端可选网格、成员 API、分页和时间排序；照片详情地图共享 SDK。地图边界在百度坐标上扩展 0.1 度后作为 WGS84 候选查询，此为用户接受的试用近似，不是可靠坐标反算或严格视野筛选；列表和聚合基于候选范围，可能包含视野外照片，也不能保证边缘完全无遗漏，未进一步对成员按屏幕精确过滤。图层中心按批官方 WGS84→BD09LL 转换，最多 1000 网格，每批 10，失败不继续显示旧点。新配置单独保存，旧配置保留；坐标不写回数据库。

隔离 16 张合成图库、实际内置浏览器：百度普通、卫星可见，混合按钮切换；单点打开对应照片，详情小地图可见；两张同位置照片聚合及成员展开、深链接、详情返回地图、无 AK 和无效 AK 后照片列表回退已观察。深链接初测列表未刷新，修复并复测；无效 AK 转换失败时初测只显示局部范围，修复后复测回到 16 张。百度 SDK 本身可能弹出无效 AK 提示。缩放、窄屏、真实定位精度、详情返回后的全部状态、网络及配额故障矩阵未完整验证。最后一次代码调整后本地浏览器不可用，页面需复验。隔离环境 AK 已通过设置清除；未访问真实图库。

定向 JUnit `test-results/baidu-final.xml`：100 项，95 passed / 4 failed / 1 skipped，退出 1。四个失败在 `test-results/baidu-baseline.xml`、`test-results/baidu-baseline2.xml` 的 HEAD 基线复现：两个历史首页资源版本断言、两个首页返回筛选控件断言；保留失败，不称全量通过。此前 `test-results/baidu-affected.xml` 的 134 项有 5 failures / 1 skip，其中地图返回状态校验的新增失败已恢复原边界检查并在 final 通过；地图文案测试已改为验证实际失败回退，之后到达基线既有版本断言。初始 `baidu-integration.xml` 的悬停和详情夹具失败在适配百度后通过；`baidu-engine.xml` 初次失败为测试桩遮蔽原生 Map，`baidu-engine-r2.xml` 4/4 通过。`baidu-sizes.xml` 16/16：500、1000、5000、5001 条合成记录网格数量、总数与分页正确性；不是浏览器延迟或资源性能测试。Ruff、compileall、Node --check、git diff --check 通过。

必需项 PENDING：真实用户数据对齐验收、500/1000/5000/超 5000 的浏览器初始化及拖动/内存/泄漏性能测量、国内外与跨日界线的准确性和密集场景、后台失效矩阵、窄屏 UI 及独立 DS-H 全影响面审查。当前不能标功能或最终 PASS；不提交、不推送。

## 连续缩放转换失败续修

用户反馈连续放大后显示“地图点转换失败”，日志中的 points / assets 请求均为 200；日志未包括百度转换回调状态或异常，因此不能据此确定具体原因。代码核查发现旧视野的一次转换尚在途时，新视野会启动新的转换，可能增加百度并发和额度压力；此前任何当前视野转换失败会销毁地图、清空视野并加载全库照片。

现将同一页面所有转换批次串行，等待时重新检查视野是否过期；每批仍最多 10，单次 10 秒超时，过期视野不再启动后续批次。转换失败保留地图及当前照片列表，并提供“重试地图点”，不再自动跳到全库。若上一次请求一直无响应，新视野仍可能等待至该次超时；服务配额、真正网络/权限错误仍可能导致失败，未宣称原因已经确认。

合成测试 `pytest -q tests/unit/test_baidu_sdk_loader.py tests/unit/test_baidu_map_engine.py tests/unit/test_p4_ui2_map_photo_hover.py tests/integration/test_p4_place_map_api.py tests/integration/test_baidu_map_grid_sizes.py --basetemp=.pytest-tmp/baidu-zoom-fix --junitxml=test-results/baidu-zoom-fix.xml`：16 passed，退出 0；包括延迟回调下新旧视野并发上限、过期批次不启动及最新结果。Node 语法、Ruff、diff-check 通过。内置浏览器本会话显示不可用，未完成实际页面连续缩放、真实失败和重试检查；该修复保持 VALIDATION PENDING，需用户现场复测或恢复隔离浏览器后验收。未访问真实图库。

## 连续放大后底图回退续修

用户日志显示带视野参数的 points/assets 均返回 200，随后访问无 bbox 的 assets。当前实现中，地图初始化后的 12 秒计时器若未接到 `firsttileload`，即使地图已经显示，也会调用 `fallback` 销毁地图并请求全库列表；这与观察到的回退路径一致，但日志没有事件和时间证据，不能断言是唯一根因。

改为计时器只显示“底图状态未确认”，不再销毁地图；照片详情小地图也不再仅凭该事件超时销毁。百度授权错误和 SDK 初始化失败仍保持现有失败处理，不把 SDK 已加载当成底图成功。

`pytest -q tests/unit/test_baidu_map_tile_timeout.py tests/unit/test_baidu_sdk_loader.py tests/unit/test_baidu_map_engine.py tests/unit/test_p4_ui2_map_photo_hover.py tests/unit/test_photo_view_info_ui.py::test_photo_info_renders_paths_people_gps_and_releases_stale_maps --basetemp=.pytest-tmp/baidu-tile-timeout --junitxml=test-results/baidu-tile-timeout.xml`：11 passed，退出 0。新测试模拟初始化、缩放、未收到首瓦片事件、计时器触发，检查地图未销毁且没有无范围回退请求；Node 语法、Ruff、diff-check 通过。本地内置浏览器清单报告不可用，因此真实百度页面连续放大仍待现场验证。未访问真实图库。

## 聚合密度与转换失败分类续修

用户继续反馈转换失败及聚合不合理。核对发现此前强制网格用相对当前视野的 `2^(zoom+2)` 划分；放大同时缩小视野和指数式增密网格，使相邻照片容易分散，不能等同原 MapLibre 的像素距离聚合。改为仅对 `grid=true` 使用相对视野的 16×16 网格；默认 points 响应保持原有策略。网格仍受视野平移、矩形切分、视野扩大 0.1 度影响，邻近照片跨格可能分开，不能声称与旧聚合完全一致。分组成员通过原 cell_id、grid_zoom 查询，合成测试核对数量及成员总数。

前端原先把超时、异常、错误状态和返回数据异常都变成同一提示，无法诊断。现仅显示脱敏类别 `timeout`、`exception`、`invalid_result` 或 `status_数字`；不输出 AK、坐标或异常原文。未拿到用户真实百度回调状态，转换失败的直接原因尚未确定；本次没有声称已经修复服务端转换失败。

合成验证：`pytest -q tests/unit/test_baidu_sdk_loader.py tests/unit/test_baidu_map_engine.py tests/unit/test_baidu_map_tile_timeout.py tests/integration/test_p4_place_map_api.py tests/integration/test_baidu_map_grid_sizes.py tests/unit/test_p4_perf_map_grid.py tests/integration/test_p4_perf_map_count_cache.py tests/unit/test_p4_ui2_map_photo_hover.py --basetemp=.pytest-tmp/baidu-cluster-fix-r2 --junitxml=test-results/baidu-cluster-fix-r2.xml`，55 passed，退出 0；包括固定密度、网格成员、各数据规模、默认路径、异步视野、失败分类。Ruff、compileall、Node 检查和 diff-check 通过。当前内置浏览器不可用，真实页面与用户现场连续缩放、聚合视觉及具体错误状态仍 PENDING；未访问真实图库。

## 聚合数量与展开成员续修

用户反馈点击数量与显示照片数量不符。现有 UI 每页最多 48 张，但只显示聚合总数，没有注明已载入张数；用户现场具体数字未提供，不能断定分页就是唯一原因。百度 PointLayer 自定义 canvas 图标此前未指定图集去重标识；现按数量显式使用不同 icon ID，避免不同数字复用同一图集标识，尚待实际 SDK 验证。点击聚合时清空上一选择并显示加载状态，对同一 member_version 下的点击数字和服务端成员总数进行核对，不一致时刷新而不展示错误结果。标题显示总数及已显示数量，后续“加载更多”继续使用同一 cell/bbox/version 游标；迟到的旧点击响应不覆盖新选择。

`test-results/baidu-cluster-members.xml` 55 passed，含 500/1000/5000/5001 条合成资产下最大格完整分页、总数和去重，以及前端 50 张分 48+2 和选择交错；最终图标 ID 测试与相关 API 的 `test-results/baidu-cluster-members-r2.xml` 11 passed。Node 语法、Ruff、diff-check 通过。上述为组合证据；SDK 真实图集、实际页面和现场聚合视觉未复验，内置浏览器不可用，保持 PENDING。未访问真实图库。


## 聚合位置与稳定性续修（2026-11-28）

用户反馈数量一致，但聚合位置偏移、移动时变化，怀疑照片定位不准。此前强制网格依赖每次视野边界，平移导致同一照片重新分组；位置采用经纬度极值中点，分布不均时不代表成员平均位置。现仅对百度强制网格改为按地图缩放级别划分的世界固定网格，cell ID 使用 `w:` 前缀，成员查询识别同一网格；多照片使用成员 GPS 算术平均位置，单照片保留原 GPS。原默认网格协议不变，最多 1000 格、48 张分页不变。缩放改变分组、视野边缘成员进入或离开、网格跨界及超限降级仍可能改变聚合，且网格不等同于旧的像素距离聚合。视野扩大 0.1 度仍是近似候选范围，不能证明筛选边界准确。

代码核对：单照片从原始 WGS84 经过百度 Convertor 一次正向转换交给 PointLayer，未发现数据库坐标被重复写入或转换；其他 SDK 对象使用 WGS84 输入模式。此前仅北京一个转换点目视测试，不能证明实际照片、海外底图或详情小地图精度。本次未观察最终真实 SDK 地图或用户图库，定位精度和浏览器验收 PENDING。

定向测试首次 15 通过、1 失败：旧测试仍要求缩放级别不影响网格，已按新行为更新并重跑。最终受影响 API、转换、分页和配置 JUnit `test-results/baidu-position-final.xml`：32 passed，exit 0；包括平移不改变相同成员分组及位置、缩放细分、加权中心、单点坐标、分页和超过上限的父格合并。Ruff、`git diff --check` 通过；未运行完整套件。独立 DS-H 审查和实际位置验证仍待完成。


## invalid_result 偶发错误续修（2026-11-28）

用户确认位置对齐，反馈偶发 `invalid_result`。现有通用错误可能源于回调为空、状态缺失、点列表缺失、返回数量不符或坐标无效；现场只有合并类别，尚不能确认真正原因。对这些异常每批最多等待 250ms 自动重试一次；过期视野不发重试，仍全局串行、每批最多 10 点，且不对明确状态码、异常或超时重试。持续无效时拒绝错误坐标并显示脱敏细分代码供诊断；失败仍保留照片列表。最坏转换调用数由每批一次增加至每批两次，未声明性能达标。

合成 JUnit `test-results/baidu-invalid-retry.xml` 19 passed / 0 failures / 0 errors；覆盖偶发成功、持续无效、过期视野停止、转换串行、聚合 API 和分页。Node 语法检查、Ruff、diff-check 通过。未采集实际回调、未使用真实图库，真实服务复测和浏览器验收 PENDING；此次改善偶发恢复，不声称确认或消除了所有现场原因。


## 百度官方文档与状态缺失排查（2026-11-28）

用户反馈 `invalid_result_status`。复核 [JSAPI 4.0 Convertor](https://lbs.baidu.com/jsapi/refdoc/v4/classes/BMap.Convertor.html) 与 [TranslateResults](https://lbs.baidu.com/jsapi/refdoc/v4/interfaces/BMap.TranslateResults.html)：`translate(points, 1, 5, callback)` 是官方 WGS84→BD09 示例，回调 status 为数字，0 表示成功；旧版参考限制每批至多 10 个点，现实现保持 10。实际从百度公开入口以无效测试 AK 获取 20261118190714 版本 SDK，仅抽取 Convertor 并用合成 callback 运行：成功响应得到 status=0 和 points；`result.error` 错误分支读取拼写为 `result.errror`，导致返回的 status 为 undefined。此是该 SDK 版本的可复现问题，可解释状态缺失，但没有用户现场的实际原始响应，不能断言它就是唯一原因或知道服务错误码。没有修改或绕过 SDK，也不把无状态响应当成成功。

新增页面内精确坐标转换结果 LRU 缓存，上限 2000 条，失败不缓存；相同坐标再次出现时减少百度转换调用，不持久化或写回数据库。保留串行、每批最多 10、最多 1000 输入和无效返回一次有界重试。持续的百度服务错误仍可能导致地图点不能显示；未找到文档支持通过丢失的状态推断具体错误，实测问题与用户复测 PENDING。

首次 JUnit 19 passed / 1 failed：新增测试跨 Node VM 上下文比较数组原型，非产品失败；修正为转换测试数组后，最终 `test-results/baidu-status-sdk-rerun.xml` 20 passed / 0 failures，涵盖缓存命中、输出顺序、防外部修改、超过 2000 项淘汰及原有转换/聚合测试。Node 语法、Ruff 和 diff-check 通过。未访问真实图库；未在最终应用页面对用户场景进行浏览器验收。
