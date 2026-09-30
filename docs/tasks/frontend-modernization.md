# 现代相册界面

用户于 2026-10-01 授权梳理页面与交互、迁移现代框架，要求简洁易懂。

采用 Vue 3 / TypeScript / Vite / Naive UI，产物由原 FastAPI 同源提供，运行时不需要 Node 或外网。保留后端、数据和安全协议；删除被替代的手写页面。

| 入口 | 初始展示 | 操作后展示 |
|---|---|---|
| 照片 | 搜索框、图片网格、筛选入口 | 描述/上传搜索结果、可移除条件、更多结果；点图打开详情 |
| 详情 | 大图、拍摄时间、文件位置、命中依据 | 地点指定/取消/恢复，原图入口；请求失败有明确提示 |
| 人物 | 头像、名称、照片数；未命名/待整理 | 人物照片、人脸选择、命名/合并/拆分/纠错；历史与撤销 |
| 地点 | 按名称和层级找地点、新建/改名 | 该地照片、待确认建议、批量归类；历史与撤销 |
| 图库管理 | 来源、各阶段进度、运行状态 | 添加目录、开始建库/增量刷新、移除来源、失败信息 |

图片网格有界分页；人物/地点选择分页加载。空库、加载、会话失效、无匹配、部分就绪、离线原图、写入冲突分别显示。未实现的 OCR 原文/备注编辑/备份界面不造入口；已有 OCR 检索继续保留。

必读：主架构 §4.5、§8.3、§9～10、§15；策略 §2.4、§3、§5。本次当前卡变更前已核对两份完整文档。验证为类型检查、生产构建、受影响 API/静态资源 pytest（JUnit）和真实浏览器核心交互。按最新用户要求不重复模型/规模实验。独立工程审查另行记录，不能用实现者自检代替；不改人工知识/事务语义，若审查发现协议变化需扩大范围。

## 实现与验证（2026-10-01）

- 四个导航入口和详情弹窗已接入真实 API；按页面拆分 Vue 组件，Naive UI 统一表单、选择器、弹窗、反馈与深浅主题。小屏改为顶部导航。
- 分页与请求状态防止旧响应覆盖新查询；筛选条件可移除。时间按本地日历生成结束日的次日边界；图片上传保留格式支持。人物/地点写入携带所见 revision，重试保留幂等键，撤销与原操作状态对应。
- 新增受原会话保护的 `GET /places/{place_id}`，用于分页选择器和地点目录的祖先路径消歧；未改数据结构或人工整理语义。
- 旧 HTML/JS/CSS 与仅断言其源码字符串的测试已移除；FastAPI 服务编译产物，路由仍兼容 `/search`、`/sources`。npm 锁文件入库，构建目录不入库；wheel 强制包含构建资源，Windows 打包入口检查缺失资源。

执行记录：

```sh
# 在 frontend/ 执行；build 包括 TypeScript 检查
npm run build
npm test

# 仓库根目录
.venv/bin/python -m pytest -q -o addopts='' tests/unit/test_api_frontend.py tests/integration/test_api_frontend_routes.py tests/integration/test_p2_place_page.py tests/integration/test_p3_persons_page.py tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_library_refresh.py --basetemp=.pytest-tmp/frontend-api --junitxml=test-results/frontend-api.xml
.venv/bin/ruff check src/photo_search/api/web.py src/photo_search/api/routes.py tests/unit/test_api_frontend.py tests/integration/test_api_frontend_routes.py tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_library_refresh.py tools/packaging/build_onedir.py
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.python" uv build --wheel --out-dir .pytest-tmp/frontend-package
git diff --check
```

结果：生产构建、类型检查通过；前端 **10 项通过（2.04 秒）**，JUnit `test-results/frontend.xml`；后端相关 **95 项通过（11.05 秒）**，JUnit `test-results/frontend-api.xml`；Ruff、差异检查通过。wheel 中 index.html 和 7 个 JS/CSS 文件与最终构建逐字节一致，未包含旧 app.js。

真实浏览器：公开 2,000 图测试库的浏览、中文描述搜索、以图搜图（原图首位）、大图详情、图库进度及宽/窄布局正常。另用 `.pytest-tmp/` 的两个合成人工样本走通人物命名与撤销、新建地点、照片地点批量归类与撤销；写入测试没有碰公开测试库或私人图库。浏览器写入流程无 console error。这些样本验证接口交互，不代表人脸识别质量。

浏览器检查发现并修复：已完成索引被采样上限误判为处理中、scheduler 暂停状态字段误读、地点撤销的 REVERSED 状态显示不正确；对应关键状态已有组件回归。

未验：Windows 冻结安装包、十万图真实规模、所有人物合并/拆分/重识别路径的真实浏览器覆盖、独立工程审查；不把本次实现者自检记作正式验收 PASS。下一步见 [前端独立审查](frontend-review.md)，按仓库规则由用户手动切换 DS-H 后进行，不自动切换模型。
