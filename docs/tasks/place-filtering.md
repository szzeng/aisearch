# 地点层级与筛选补强

用户于 2026-10-01 要求解释地点包含关系、核对专用库与类似边界问题，并直接补齐必要机制。此卡是当前接受任务；前端独立审查保留待办。

读取：主架构 §4.5、§9～10、§15；策略 §2.4、§3、§5；ADR-0005 §2～4、§9～11；地点目录/筛选、离线地名包解析、对应前端和测试。沿用已有 ID、层级类型、人工锁、候选建议及 revision 语义，不开放任意子树移动。

实现范围：页面提供“包含下级/仅当前”并正确绑定查询；同名消歧、父类型约束和缺少地名包提示；SQLite 子树筛选避免展开大量 SQL 绑定参数；创建时拒绝超深层级；SciPy KDTree 对城市球面坐标建立可重建内存索引，精确距离复核与稳定排序保持现有结果语义。SciPy 已随人脸依赖安装，升为直接依赖并更新 uv.lock。没有边界多边形数据，不引入空转的 GIS 框架或在线反查服务。

验证：必要的地理边界/查询组合单元与集成测试、前端组件测试和构建、临时样本浏览器检查、wheel 资源一致性；保留 JUnit。不下载地名/模型数据，不扫描或修改真实图库，不重复模型或大型规模实验。独立完整受影响面审查仍待后续人工切换 DS-H 进行。

## 实施结果

- 照片页和地点页可以选择包含下级或仅当前地点；条件进入普通浏览、文字搜索、图片搜索与分页快照。带地点链接打开照片页时直接显示筛选条件。
- 同名地点保留完整父链及稳定编号；新增下级地点预填父级，选择器禁用不合法的父类型。层级超过 64 个祖先时在创建阶段拒绝，防止写入后读取失败。
- 地点筛选通过 SQLite 递归子查询匹配子树，仅绑定选中的根 ID，解决大量下级地点触发 SQL 参数上限的问题；重叠父子条件仍按资产去重。
- GeoNames 城市点包每个版本惰性构建 SciPy KDTree，索引球面三维坐标；近邻候选仍用原大圆距离复核并按距离/稳定 ID 排序。日期变更线、极区、同距离排序和半径边界都有覆盖。此索引不改归属，也不是另一个向量数据库；不需要 GPU。
- 地名包缺失/不可用有独立提示；照片详情区分无 GPS、尚未提取、基准不支持、人工归属与附近城市建议，并显示候选距离。未下载任何地名包或模型、未扫描私人图库。

## 验证

```sh
.venv/bin/python -m pytest -q -o addopts='' tests/unit/test_place_spatial_index.py tests/unit/test_p2_dataset_manifest.py tests/unit/test_p2_place_directory.py tests/integration/test_p2_place_page.py tests/integration/test_p2_search_authority.py tests/integration/test_p2_acceptance_search.py --basetemp=.pytest-tmp/place-filtering --junitxml=test-results/place-filtering.xml
.venv/bin/python -m pytest -q -o addopts='' tests/integration/test_p2_place_resolver.py --basetemp=.pytest-tmp/place-resolver --junitxml=test-results/place-resolver.xml
npm --prefix frontend run build
npm --prefix frontend test
.venv/bin/ruff check src/photo_search/geo/dataset.py src/photo_search/places/directory.py src/photo_search/places/filter.py tests/unit/test_place_spatial_index.py tests/unit/test_p2_place_directory.py
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.python" uv lock --check --offline
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.python" uv build --wheel --out-dir .pytest-tmp/place-package
git diff --check
```

后端 **63 项（5.26 秒）+ 15 项（0.98 秒）**通过；前端 **14 项（2.02 秒）**通过，JUnit 分别在 `test-results/place-filtering.xml`、`place-resolver.xml`、`frontend.xml`。类型检查、生产构建、Ruff 与差异检查通过；最终 wheel 静态资源逐字节匹配，并声明 SciPy 直接依赖。离线更新锁文件首次因注册表缓存不全失败，随后正常联网解析 102 个包成功；后续离线 lock check 通过。没有安装新模型或新增数据服务。

浏览器用三个独立合成样本确认：杭州含西湖时 2 张、仅当前 1 张、惠州同名西湖始终不混入；新建下级地点预填父级；带地点链接的筛选和条件标签可见；缺少地名包提示正常。临时验证服务结束后关闭，公开测试库预览已重启到新代码。

局限：目录层级不是行政边界几何。仍未实现完整省/区枚举、跨区域景区的多重包含或基于边界多边形的自动归属；Shapely 之类的库还需要合格边界数据才能解决这些问题，本次没有加入无数据支撑的功能。十万张真实 GPS 吞吐、Windows 冻结包和独立工程审查未验；不以本次自检代替正式验收。

下一步沿用 [独立审查卡](frontend-review.md)，已追加本次完整受影响范围；按仓库规则由用户手动切换 DS-H 后执行。
