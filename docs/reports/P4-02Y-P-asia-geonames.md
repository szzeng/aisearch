# P4-02Y-P — 亚洲 GeoNames 真实地名包准备

**结论：PASS（2026-09-29）——亚洲 GeoNames 包生成成功、`validate` + `activate` 通过，杭州/上海/东京/新加坡
坐标查询与真实 GPS 照片都能得到合理附近城市建议。** 本卡只做数据准备与导入，不改地点数据结构、解析协议、
schema、依赖或 ADR。任务卡：[P4-02Y-P](../tasks/P4-02Y-P.md)。

## 1. 基线与环境

| 项 | 值 |
|---|---|
| HEAD / 分支 | `eb0e5be`（`master`，工作树开工前干净） |
| 数据根 | `D:\SZ\project\PhotoSearchData`（个人试用数据根） |
| 包安装位置 | `D:\SZ\project\PhotoSearchData\places\datasets\geonames-asia-cities500-v1` |
| Python / uv | 3.12.10 / uv（`.uv-cache`） |
| schema | 16（未改；`0017` 未实施） |

## 2. 数据来源、快照与获取过程

**使用快照：GeoNames dump `2026-09-15`**，三份文件同快照、同源：

| 文件 | 字节数 | 用途 |
|---|---|---|
| `cities500.zip` | 13 640 338 | 城市条目（人口 ≥ 500 或首都） |
| `alternateNamesV2.zip` | 204 392 884 | 语言标记别名（中文/英文名来源） |
| `countryInfo.txt` | 31 678 | 国家代码、英文名、国家 geonameid |

获取过程（如实记录，因为它影响可复现性）：

1. 首选官方 `https://download.geonames.org/export/dump/`。实测该站到本机只有 **20–43 KB/s**：
   `cities500.zip` 反复断开重传才下完（约 11 分钟）；`alternateNamesV2.zip` 下载约 40 分钟只到
   **100 MB / 195 MB**（按当时速率预计还需 60–90 分钟）。
2. 改用 **Hugging Face 上的官方 dump 镜像 `DataDock/geonames`**（同一天发布的 dump 导出副本），
   快照目录 `2026-09-15` 同时具备上述三份文件；实测 **8.7 MB/s**，三份文件 **29.4 秒**下完。
3. 一致性核对（镜像 vs 官方）：镜像 `cities500` 中杭州(1808926)、上海(1796236)、东京(1850147) 三行与
   官方 `2026-09-29` 快照逐字段一致（名称、经纬度、国家、人口）；两个 zip 解压 CRC 通过；
   解出的 dump 通过产品既有 `photo_search.geo.dataset.load_content` 校验。

> 说明：官方 `2026-09-29` 的 `cities500`/`countryInfo` 与官方慢速链路下已下载的 100 MB 分片不足，未参与建包
> （分片已删除、官方文件单独归档在 `tmp/p4-02y-p/raw-official-2026-09-29\`）。若日后要用纯官方链路重做，
> 工具支持直接换 `--raw-dir`，命令见 §3。

## 3. 新增准备工具

`tools/places/prepare_geonames.py`（只读 dump、只写 spec 与可选包目录；**不联网、不碰照片/来源/catalog**，
不改变包格式）：读 `cities500` + `alternateNamesV2` + `countryInfo` → 过滤国家 → 按命名优先级生成 spec →
交给既有 `build_package` / `manage_dataset.py`。

```
uv run --no-sync python tools/places/prepare_geonames.py \
    --raw-dir tmp/p4-02y-p/raw-2026-09-15 --spec-out tmp/p4-02y-p/asia-cities.spec.json \
    --snapshot 2026-09-15
uv run --no-sync python tools/places/manage_dataset.py build \
    --spec tmp/p4-02y-p/asia-cities.spec.json --out tmp/p4-02y-p/asia-package
uv run --no-sync python tools/places/manage_dataset.py --data-root D:\SZ\project\PhotoSearchData \
    import --source tmp/p4-02y-p/asia-package --activate
```

命名与别名口径（卡面 §1）：**简体中文（zh-Hans/zh-CN/zh-SG/zh-MY）→ 其他中文 → GeoNames 主名称 → 英文 →
ASCII**；别名 = 中文/英文标记名（去掉与显示名重复者）+ 主名称 + ASCII 名，按同一优先级排序、去重、每地点最多 10 条。

真实数据里踩到并已处理的四个坑（每个都有定点回归用例）：

| 坑 | 例子 | 处理 |
|---|---|---|
| 同一文本挂多个语言标签 | `新加坡` 同时是 `zh` 与 preferred `zh-CN` | 去重时保留**最好标签**的那份（原本会丢掉 zh-CN 而落到罗马字 `Dānmǎxī`） |
| 历史地名 | `库伦`(hist=1) vs `乌兰巴托` | 显示名降权历史条目（`isHistoric` 字段），别名仍保留 |
| 繁体写法挂在 `zh` 标签下 | `馬累`/`瑪律`/`马累` | 用受限的繁体专用字表在中文类内降权（永不降到英文之下） |
| 别名里的罗马字/长式 | `Dānmǎxī`、`雅加达` vs `巴达威亚`、`成都` vs `天府` | 中文脚本优先于拉丁转写；同脚本内先短式/基础式（`成都` 是 `成都市` 的前缀） |

## 4. 建包结果

| 项 | 值 |
|---|---|
| dataset_id / package_id | `geonames-asia-cities500-v1` / `photo-search-geonames-asia-cities500-package` |
| 国家范围 | 24 个（CN HK MO TW JP KR MN SG MY TH VN PH ID KH LA MM BN IN NP LK BD PK BT MV） |
| 记录数 | **45 217** = 45 193 城市 + 24 国家 |
| 别名 | 26 521 条，覆盖 17 057 个地点 |
| 显示名为中文的城市 | 12 367 / 45 193（**27.4%**；其余回退 GeoNames 主名称/英文名，见 §6） |
| 经纬度范围（城市） | south −10.934 / west 61.58661 / north 53.31946 / east 145.575 |
| 包内文件 | `manifest.json`、`places.jsonl`(7.38 MB)、`hierarchy.jsonl`(2.94 MB)、`aliases.jsonl`(1.87 MB)、`LICENSE.txt`（合计约 12.2 MB） |
| build / import / validate / activate | 全部 `status: ok`；`state=ACTIVE`、`places_mapped=45217`、`resolver_available=true`、`suggestion_revision=0` |

证据：`test-results/p4-02y-p-{converter,build,import,status}.json`、`p4-02y-p-spec-inspection.txt`。

**命名抽查（32 个知名城市/国家）29 个完全命中**：杭州/上海/东京/新加坡/曼谷/孟买/德里/雅加达/首尔/台北/
香港/北京/成都/广州/乌兰巴托/马尼拉/吉隆坡/达卡/卡拉奇/廷布/马累/金边/仰光/斯里巴加湾市/中国/日本 …
剩余 3 个是**源数据本身的限制**（不是排序缺陷，本卡不做简繁转换）：`河內`(Hanoi)、`加德滿都`(Kathmandu)、
`可倫坡`(Colombo)、`永珍`(Vientiane) 在这一版 dump 里只有繁体/非大陆写法；`大阪市` 是该 dump 里大阪唯一的
中文名（没有更短的 `大阪`）。

## 5. 坐标查询与端到端验证

查询用产品自身的确定性函数 `photo_search.geo.dataset.nearby_cities`（`max_candidates=3`、半径 200 km，
与 resolver 的冻结配置一致），证据 `test-results/p4-02y-p-verify.json`：

| 查询坐标 | 返回（最近 3） | 单次耗时 |
|---|---|---|
| 杭州 30.2741/120.1551 | **杭州** 2.26 km、Mishixiang 2.56 km、Gudang 3.55 km | 29.7 ms |
| 上海 31.2304/121.4737 | 黄浦 0.92 km、浦西 1.70 km、上海 1.74 km | 22.4 ms |
| 东京 35.6762/139.6503 | Eifuku 0.94 km、Sasazuka 1.47 km、Umegaoka 2.37 km | 24.0 ms |
| 新加坡 1.3521/103.8198 | Hillcrest Park 2.27 km、Sembawang Hills Estate 2.58 km、Thomson 2.67 km | 22.2 ms |

（东京/新加坡的最近点是当地小地名，源数据没有它们的中文名，因此显示为拉丁名；位置正确。）

**产品闭环（一次性工作目录，不碰真实库）**：把真实包 stage→validate→activate 进一个临时数据根，造 4 张带真实
EXIF GPS 的合成照片（就在上述 4 个坐标），走产品 GPS + resolver 调度器：`gps_steps=4`、`resolver_steps=4`、
`place_resolutions` 全部 `CANDIDATES`，各自 top1 = 黄浦 / Eifuku / Hillcrest Park / **杭州**。

**真实照片（只读）**：真实库 catalog 里有 **87 个带坐标的 GPS fact**；取前 3 个（36.19169 N / 117.12494 E）
在真实包上查询 → **泰安市 0.84 km**、Feicheng 32.8 km、莱芜市 47.7 km。该步骤只以只读方式打开 catalog，
不写任何地点建议（避免本卡触发全库重算）。

## 6. 性能观察（是否需要后续优化卡）

`nearby_cities` 目前对每个坐标线性扫描全部 45 193 个城市点：**22–30 ms / 次**（本机实测）。

* 现有 87 个 GPS fact ≈ 2 秒，日常增量（每次刷新几十张）完全可以接受；
* 但若将来整个 30 万库都要解析地点（30 万 × 25 ms）≈ **2 小时**纯 CPU，这时值得**另立性能卡**：
  候选方向是 `cities15000` 子集、按经纬度分格的空间索引，或把解析改成批量共享一次扫描（本卡按 §4 明确不做
  RTree/GIS/网格索引）。

另外：45 193 个城市里只有 27.4% 有中文名（GeoNames 对小城镇的中文别名覆盖本来就稀疏），因此边远小地方的
建议名可能是拼音/英文名。要提升只能换更大的名称源（如中文维基地名词表），不在本卡范围。

## 7. 结论与边界

卡面 §5 验收全部达成：亚洲 GeoNames 包生成成功 / `validate` PASS / `activate` PASS / 杭州-上海-东京-新加坡
附近城市查询正常 / 包确实位于 `D:\SZ\project\PhotoSearchData\places\datasets`。

激活后，日常「刷新图库 / 快速刷新此来源」会对**本次增量**的 GPS fact 自动产出附近城市建议（原图与来源目录零改动）。

未做（按卡面 §4）：全球 GeoNames、全图库地点重算、RTree/GIS/网格索引、行政区边界、POI/景点、地图、自动确认地点、
地点 UI 改造、打包与发行资源处理；也未做真实浏览器验收、30 万规模地点解析。

未验证：`>45 193` 城市尺度的解析性能（见 §6 外推）、简繁转换质量（本卡不做转换）、以及镜像与官方 `2026-09-15`
文件的逐字节一致性（只做了抽样字段比对与结构校验；官方链路当时无法在可接受时间内取得同一快照）。

## 8. 产物

* 新工具：`tools/places/prepare_geonames.py`；新用例：`tests/unit/test_p4_02y_p_geonames_prepare.py`（7 例，含四个坑的回归）。
* 证据：`test-results/p4-02y-p-{converter,build,import,status,verify}.json`、`p4-02y-p-spec-inspection.txt`、
  `test-results/p4-02y-p-focused.xml`（本卡定点 + 地点模块回归 **64 / 0 / 0 / 0**）。
* 原始数据（`tmp/`，不入库，约 **1.1 GB**，可直接删除）：`tmp/p4-02y-p/raw-2026-09-15\`（建包用快照）、
  `tmp/p4-02y-p/raw-official-2026-09-29\`（官方对照）、`asia-cities.spec.json`、`asia-package\`。
* 一次性核对脚本（`tmp/`，不入库）：`verify_asia_package.py`、`inspect_spec.py`。
* 产品代码 0 修改：本卡只新增 `tools/places/prepare_geonames.py` 与一个单测文件，外加本报告与状态行；
  `src/**`、`docs/decisions/**`、schema/迁移、依赖、`uv.lock` 均零改动（`ruff check .` 与 `git diff --check` exit 0）。
