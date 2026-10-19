# P4-UI2-VIEWER-PAN-AVATAR-FIX 执行报告

日期：2026-10-19
状态：IMPLEMENTED / ACCEPTANCE PARTIAL（用户要求不做浏览器验证）

## 交付

- 查看器舞台改为明确的绝对中心定位，平移坐标与 clamp 对齐；补齐主指针校验、pointer cancel/lost capture 清理、换图/关闭释放、resize 重算或边界夹紧。舞台内设置 `touch-action:none`。
- 人物深链以一次 `GET /persons/{id}/photos?limit=1` 获取 canonical person 和有效展示头像；不再请求原详情、不保存自动头像，单图副产物丢弃。后端接口/数据库未改。
- 更新相关 JS/CSS cache version；扩展 viewer、人物深链和集成覆盖。

## 性能与实现边界

合成只读诊断对比原详情 6 条 SELECT/CTE 与单图摘要 12 条；人物深链仍只有一次 HTTP 摘要请求，不存在逐脸/逐图 N+1。计划使用人物关联索引和资产主键查找，并出现排序临时 B-tree。该小样本仅用于查询路径/有界性判断，不代表延迟或大相册规模保证。

viewer 修复对齐 JS 的舞台中心数学基准，但没有真实浏览器 DOM 几何测量；图片实际边缘可达仍待浏览器验证。

## 验证

- 初次完整受影响命令：64 tests，0 failures、0 errors、0 skipped，exit 0；JUnit：`test-results/P4-UI2-VIEWER-PAN-AVATAR-FIX-final-20261019-095148-802.xml`。
- 最终扩展单测：28 tests，0 failures、0 errors、0 skipped，exit 0；JUnit：`test-results/P4-UI2-VIEWER-PAN-AVATAR-FIX-units-20261019-095632-053.xml`。
- 最后一次完整集成组合重跑：66 tests 中单测 28 passed；38 个 integration 在 fixture setup 报错。并行工作树在成功组合运行之后出现 migration 0035，而 `schema.py` 仍声明支持版本 34，迁移集合不连续。该环境/并行改动冲突使本次重跑无法确认集成结果；此前 38 个集成用例在同一生产代码下通过。JUnit：`test-results/P4-UI2-VIEWER-PAN-AVATAR-FIX-final-20261019-095606-341.xml`。
- 最终 JS 语法检查、Ruff（四个测试模块）、`git diff --check` 均通过。
- 浏览器验证按用户要求跳过。V01/V02 的真实边缘可达与 V04 的真实页面头像路径保持 PENDING，因此不标最终 PASS。

## V01–V07

- V01–V03：自动测试覆盖几何、边界、指针取消/切换/释放及 resize；真实 DOM 几何和视觉边缘待浏览器验证。
- V04–V06：定向自动覆盖有效头像、canonical ID、空脸、接口错误与迟到响应；此前成功的集成组合覆盖 NULL/失效头像与只读语义。当前工作树迁移冲突导致最终集成重跑 pending。
- V07：人物入口和深链消费者定向单测通过；浏览器详情返回与目录交互未验证。

未访问真实照片库。未改变其他并行任务的暂存内容；未提交。



## V08 更新：双击与 pointer capture（2026-10-19）

更新后的任务卡指出 `pointerdown` 时立即对舞台调用 `setPointerCapture` 会使原生 click/dblclick 的目标转为 stage。此前为响应用户反馈而放宽 stage target 检查，违反了新增要求；现已改为：只有指针移动超过 4 CSS px 才开始拖动并捕获；dblclick 仍要求目标是当前图片；按原生 `click.detail` 与拖动状态阻止拖动形成的伪双击，正常双击继续交由浏览器原生 dblclick 处理。pointerup/cancel/lostpointercapture、窗口外 pointerup 与 blur 都会清理状态。viewer JS cache key 升到 `p4-ui2-viewer-pan-avatar-fix-a-3`。

自动验证：`pytest -q tests/unit/test_photo_view_info_ui.py -k photo_info_renders --junitxml=test-results/P4-UI2-VIEWER-PAN-AVATAR-FIX-v08-final-20261019.xml`，1 passed、0 failed、exit 0；`node --check src/photo_search/web/photo-info.js` 与 `git diff --check` 通过。Node harness 依序模拟两组 pointerdown/up/click 后派发由点击目标决定的 dblclick，并覆盖 100% 按钮、图片目标、舞台空白目标、拖动越阈值/捕获/释放及拖动后误双击抑制。这仍是合成事件证据，不是浏览器原生事件验收。

用户明确要求不运行浏览器，由用户自行测试。V08 浏览器闭环、V01/V04真实页面路径仍 PENDING；保持 `ACCEPTANCE PARTIAL`，不标最终 PASS。
