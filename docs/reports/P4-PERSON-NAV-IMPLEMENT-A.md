# P4-PERSON-NAV-IMPLEMENT-A 实施与基本验证

日期：2026-10-16。状态：**IMPLEMENTED / BASIC VALIDATED**；[任务卡](../tasks/P4-PERSON-NAV-IMPLEMENT-A.md)，[后续审查卡](../tasks/P4-PERSON-NAV-IMPLEMENT-A-review.md)。用户认可分类后授权实际接入，最新要求包含浏览器验证和图片关闭返回修复。

## 实施结果

生产 persons.html/css 接入人物／合集／待整理一级入口，待整理下未命名人物／未识别人脸为同级标签，没有概览中转页。保留原卡片、筛选、忽略、批量管理、合集及身份操作；质量开关和历史等辅助控件在待整理两类中均可访问。原有 195 个静态 ID 保留，增加导航和区域 ID。生产页面不加载样板 navigation.js/css，不含合成卡片。

persons-albums.js 原生管理 person_view、当前列表读取、旧请求序列失效、选择清理及分别恢复筛选/位置。同分类重复点击不清选择；未解决写入阻止导航；已提交的快速确认待刷新标记不等于在途写入，不阻塞切换。恢复使用新 cursor，最多重放 3 页，超出范围返回顶部。返回按钮按来源分类显示，保留 person_id/from_photo 兼容。

用户发现图片关闭后退到列表：共享 photo-info.js 打开图片会创建同 URL 历史项，关闭会 history.back；分类 popstate 原先无条件退出详情，误处理图片历史。现仅 person_view 真正变化时执行分类回退，同分类历史由弹框处理，保留底层人物/合集、时间轴、筛选和焦点。没有改共享弹框、认证或 API；JS 缓存版本为 p4-person-nav-implement-a-4。

改动面包含生产 HTML/CSS/JS、导航/分页/确认/共享照片相关测试，以及隔离服务的 --production 开关。旧测试组节点索引改为稳定选择器；静态缓存期望与现行资源同步，保留业务断言。共享 UI 文件还有其他会话的合集脸卡渲染/布局修改，本卡不将其归为导航工作。后端、依赖、CHANGELOG、发行草案等并行修改保持原样。按用户要求作本地范围提交，不推送。

## 自动检查

最终 PowerShell 命令，在仓库根目录运行：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q tests/unit/test_person_ux_simplify_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_p4_ui_people_d.py tests/unit/test_photo_view_info_ui.py tests/unit/test_search_person_album_link_ui.py --basetemp=.pytest-tmp/person-nav-close-a --junitxml=docs/reports/artifacts/P4-PERSON-NAV-IMPLEMENT-A-focused.xml
```

退出码 0；读取 [JUnit](artifacts/P4-PERSON-NAV-IMPLEMENT-A-focused.xml)：**28 tests / 0 failures / 0 errors / 0 skipped**。此前 19 项结果由最终 28 项取代。

导航场景覆盖独立读取、选择清理、筛选恢复、写入阻止切换、已提交确认不阻塞、旧请求竞态和零业务写入。图片历史新增反例在未修复版本失败（详情被关闭），修复后人物／未命名人物／合集底层对象及 DOM 保持，不触发重载请求；真正分类变化仍退出详情并导航。

Node --check 检查 persons-albums.js 和 person_ux_simplify_harness.cjs；Ruff 检查变更的 Python 测试与 serve_project.py；git diff --check 通过。早期旧夹具历史 mock、组节点索引和缓存期望失败已修正，最终无失败。

## 浏览器记录

使用既有 ApiEnv/seed_library 助手在项目 .tmp/P4-PERSON-NAV-PROJECT/本次目录生成 6 张绘制合成 JPEG、3 名已命名人物、4 组未命名人物、5 张待整理脸和 2 个合集；无真实图库、模型或 ACL 操作。服务命令：

```powershell
.venv/Scripts/python.exe docs/prototypes/people-navigation-20261016/project/serve_project.py --port 18782 --production
```

只绑定 127.0.0.1，无 open-browser；新服务的一次性链接在同一内置浏览器打开一次，真实 API 渲染即为认证证据。凭据不写报告。早期 18781 会话出现 401，重启自建合成服务、用新链接恢复；稍后旧服务再次 401，因此追加图片复测使用新 18782 会话。未绕过认证，401 原因未确认，不声称修复或推断为导航问题。

| 流程 | 实际观察 |
| --- | --- |
| 分类筛选 | 人物筛选妈妈得 1 项；待整理未命名显示 4 组且不继承人物词；未识别只显示 5 张脸及原控件 |
| 选择隔离 | 未识别筛选无可靠匹配得 3 张，批量选择 1 张；切走再返回退出批量、清选择、保留筛选 |
| 合集与历史 | 2 个合集；家人详情原时间轴 1 张去重照片，返回仍为合集；切回人物恢复妈妈筛选；浏览器前进/后退恢复对应分类 |
| 人物图片 | 18782 妈妈相册打开 sample-01.jpg，关闭按钮后仍为妈妈相册并聚焦原照片；再次打开后浏览器返回只关闭弹框 |
| 合集图片 | 家人合集打开 sample-01.jpg，关闭后仍显示家人时间轴及返回合集按钮，焦点回原照片 |
| 未命名图片 | 待整理 → 未命名相册 → sample-02.jpg，Esc 后仍为该未命名相册，焦点回原照片 |

真实截图：[分类页面](artifacts/P4-PERSON-NAV-IMPLEMENT-A-browser.jpg)、[图片关闭保留原相册](artifacts/P4-PERSON-NAV-IMPLEMENT-A-viewer-return.jpg)。交互、截图已完成；视觉观察限当前面板。短列表不能证明长页滚动和大图库性能。

## 审查与局限

用户要求简单提交后，按 hunk 分离共享文件：仅提交导航 JS/CSS、分类 HTML、相关测试和本任务文档/样板/截图；合集脸卡、错误诊断、后端、依赖与发行改动继续留在工作区。用 git checkout-index 将暂存的 web 资源、相关测试及 mockup 依赖导出到项目 .tmp/person-nav-commit/snapshot，在该目录使用项目 Python 对上述同一组 8 个测试文件复测，增加 --confcutdir=.，basetemp 为项目 .pytest-tmp/person-nav-staged。退出码 0，[暂存版本 JUnit](artifacts/P4-PERSON-NAV-IMPLEMENT-A-staged.xml) 为 **28/0/0/0**；暂存 diff-check 通过。样板源文件统一 LF 和末尾空白；浏览器记录对应此前完整工作区，暂存快照复测单独记录。

Luna High 参与 HTML/CSS 实现，并对主代理 JS 作限定只读复核，无阻塞 finding；不是全改动独立 DS-H 工程审查。两次 backToLibrary 用于先退出合集内人物，再退出合集，保留代码说明。

完整独立工程审查、长列表多页浏览器恢复、200% 缩放、完整写入/撤销/失败恢复、GPU/大图库性能及打包未执行。不标整体/发行 PASS，不追认 Fast Organize B 未完成项。
