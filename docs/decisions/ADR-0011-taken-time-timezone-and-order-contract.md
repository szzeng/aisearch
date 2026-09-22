# ADR-0011: 拍摄时间的来源、时区、回退与 `capture_desc` 排序契约

- 状态：Accepted（2026-09-22，P4-00A：DS-H 契约草拟/修复，C2 / GPT-5.6 Sol / high 聚焦终审冻结）
- 日期：2026-09-22
- 适用实现：P4-00B
- 上游：ADR-0002 §2.1/§3.4/§3.5/§5/§7、ADR-0003 §9、ADR-0005 §3/§10/§11、ADR-0009 §3.5/§5.1/§5.4/§6、
  主架构 §4.2/§4.5/§9.1/§9.3/§9.5/§10.1/§11.2/§12/§14、V3.1 §2.3/§3.2/§9/§11
- 机器可读伴随物：`docs/decisions/contracts/p4-time-contracts.v1.json`（SHA-256 见 §13）
- 契约测试：`tests/unit/test_p4_time_contracts.py`

## 1. 决策范围与阶段边界

本 ADR 冻结“拍摄时间”进入产品所需的唯一契约：源事实与列、墙上时间与时区、排序键、回退来源与
「文件时间」诚实性、新的拍摄时间 order 及其独立游标、时间范围条件与既有 AND 集合／单一 revision／
会话摘要的关系、失效与投影边界、API/错误/隐私闭集、以及为 P4-00B 提供可实现性证明的迁移契约。

P4-00A **只冻结规则**：不实现任何拍摄时间行为、不新增或修改 migration、不改 `schema_version`、不改
依赖/`uv.lock`/`PhotoSearch.spec`/`web/**`，不修改任何 `src/photo_search/**` 生产代码，不读真实照片库，
也不声称数据库或 API 已经执行本 ADR 的拒绝。P4-00B 才能按本 ADR 新增 `0015`、抽取任务、API 与 UI。

术语在本 ADR 内固定为：

- **拍摄时间**：文件自身声明的、关于图像被拍摄／被数字化的时间（`EXIF_ORIGINAL`／`EXIF_DIGITIZED`）。
- **文件时间**：文件系统记录的时间，或 EXIF 根 IFD 记录的“文件写入”类时间（`FILE_MTIME`／`EXIF_ROOT`）。
- **墙上时间（wall clock）**：文件里逐字段写下的本地日历/时刻，不含任何时区推断。
- **civil key**：把墙上时间按“若该日历字段就是 UTC”折算出的毫秒整数。它是**排序/比较键**，
  **不是** instant，也**不是**时刻的声明（详见 §3）。

本 ADR 不改变 ADR-0002 的内容身份、ADR-0005 的 GPS 事实与人工地点、ADR-0009 的人物条件与 canonical
解析，也不改变 `order=time_desc`（加入时间）的语义与游标。

## 2. 表与列形态：`asset_metadata`

主架构 §4.2 已把该表列为目标行；本 ADR 把它冻结为**主键 + 六列**，与 §4.2 逐列一致，不增不减：

| 列 | 类型/可空 | 契约 |
|---|---|---|
| `asset_id` | `INTEGER PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT` | 一个资产至多一行；身份仍是 `assets` 的稳定内容身份，本表不引入第二套身份 |
| `taken_at_raw` | `TEXT NULL` | 取自文件的**原始**时间文本（如 `2022:07:05 18:31:07`）；`FILE_MTIME` 情形下存 min `mtime_ns` 的 UTC ISO 证据串。**内部证据，永不进入任何响应、错误、日志或 URL** |
| `taken_at_sort` | `INTEGER NULL` | civil key（§3）。`NULL` 当且仅当该行没有可解析的 EXIF 日期字段 |
| `time_source` | `TEXT NOT NULL CHECK(time_source IN ('EXIF_ORIGINAL','EXIF_DIGITIZED','EXIF_ROOT','FILE_MTIME'))` | 闭集来源（§4） |
| `time_confidence` | `TEXT NOT NULL CHECK(time_confidence IN ('HIGH','MEDIUM','LOW'))` | 与来源成对出现，取值由 §4 的映射表冻结 |
| `timezone_offset` | `INTEGER NULL CHECK(timezone_offset IS NULL OR timezone_offset BETWEEN -720 AND 840)` | 文件声明的时区偏移，单位**分钟、东为正**（`+08:00` → `480`）；无/非法为 `NULL` |
| `camera` | `TEXT NULL CHECK(camera IS NULL OR length(camera) BETWEEN 1 AND 128)` | 规范化后的 `Make Model`（§8），仅供详情页显示 |

附加的表级约束：

1. `CHECK` 只允许 §4 映射表里的五种 `(time_source, time_confidence)` 组合；
2. `taken_at_sort` 与来源一致：`FILE_MTIME` **必须**为 `NULL`，
   `EXIF_ORIGINAL`／`EXIF_DIGITIZED`／`EXIF_ROOT` **必须**非 `NULL`；
3. `timezone_offset` 只可能在 EXIF 家族来源上非 `NULL`（`FILE_MTIME` 行恒为 `NULL`）；
4. 至少一条支持 `capture_desc` 与时间范围过滤的索引（建议 `(time_source, taken_at_sort, asset_id)`；
   P4-00B 可用 30 万规模的实测决定最终列序，但不得取消该索引）。

权威边界：

- 本表**不含**坐标、坐标基准或原始 GPS 块，也不复制 `asset_geo_facts` 的任何字段：拍摄时间不构成
  §4.5 的 GPS 第二权威，`/assets/{id}/geo` 的语义完全不变；
- 文件路径、可用性与文件时间的**观察值**仍以 `file_locations`（`mtime_ns`/`size`/`availability`）为准；
  本表的 `FILE_MTIME` 只是“该资产没有可用拍摄时间”的诚实标记，不新建第二份文件事实；
- `assets.sha256/byte_size/created_at` 仍是内容身份与**加入时间**；本表不回写 `assets`。

行的生命周期：

- 一个资产**至多一行**；行存在 ⟺ 该资产已经被拍摄时间抽取处理过。“没有行”与“有行但没有拍摄时间”
  是两种不同状态，必须在 API 上可区分（§8）；
- 行只由拍摄时间抽取路径在自己的写事务内写入，**不得**在 `catalog scan` 的身份事务里、也不得在任何
  读路径（`GET`）里隐式写入；
- `metadata` 表新增库级键 `taken_time_rules_version`（本 ADR 冻结为 `1`）。任何解析/优先级规则变化都必须
  递增它；库里的值与程序当前值不一致时，既有行**不得**被信任或参与过滤，必须先重抽。为使 §8.1 的状态
  真正只是三个计数的纯函数，失配时计数按 §8.1 的规范化口径报 `0`（行仍在库里，只是不参与状态与过滤）。
  这个键是本 ADR 相对 §4.2 的唯一增补；它记在 `metadata` 而不是本表，因此 §4.2 的列保持逐字不变。

## 3. 墙上时间、时区与排序键

### 3.1 civil key 的定义

`taken_at_sort` 定义为：把该行的墙上日历字段（年/月/日/时/分/秒 + 毫秒）按其字面顺序折算成一个**非负**
整数毫秒值。civil 原点固定为 `0001-01-01T00:00:00.000`（它是契约约定，**不是** instant 的原点）：

```text
taken_at_sort = (ordinal(y-m-d) - ordinal(0001-01-01)) * 86400000
              + (hh * 3600 + mm * 60 + ss) * 1000 + millis
```

取值范围 `0 .. 315537897599999`（`0001-01-01T00:00:00.000` ~ `9999-12-31T23:59:59.999`）。
非负是刻意的：三个游标空间共用同一套“非负整数元素”的编码规则，键不得为负。冻结规则：

- `taken_at_sort` **不是** instant。它不得被命名为 UTC 时间、不得被转换成 instant、不得与调用者给的
  instant 比较；v1 不产生、不存储、不公开任何 instant 字段；
- `timezone_offset` **永不参与** `taken_at_sort` 的计算。同一墙上时间的行，无论是否记录偏移，键完全相等；
- 排序键与展示值一致：展示值为 `YYYY-MM-DDTHH:MM:SS.mmm`（仅当 `timezone_offset` 非 `NULL` 时追加
  `±HH:MM`）。因此“展示相等 ⇒ 键相等”“展示升序 ⇒ 键升序”等价成立；
- 定宽规范化形式下字典序等于时间序，这一点被时间范围比较（§6）直接依赖。

### 3.2 时区

- 只接受文件里明确写下的偏移（EXIF `OffsetTimeOriginal`(0x9011)、`OffsetTimeDigitized`(0x9012)、
  `OffsetTime`(0x9010)），解析为分钟、东为正，合法范围 `-720 .. +840`；
- 缺失、非整数、带秒、超出范围或无法解析 ⇒ `timezone_offset = NULL`，**不猜、不补、不按本机时区推断**；
  `time_source = EXIF_ORIGINAL` 且偏移不可用时 `time_confidence` 由 `HIGH` 降为 `MEDIUM`；
- 偏移只用于展示（界面显示 `±HH:MM`）与将来的显式功能；v1 不做任何时区换算，也不提供 instant。

### 3.3 夏令时与歧义本地时间

- 系统**不应用**任何时区数据库或夏令时规则。EXIF 写下的墙上时间就是产品语义；
- 夏令时跳变造成的“不存在”或“重复”本地时间按文件原样保留，绝不移动、绝不加减一小时、绝不挑选某一侧；
- 文件记录的偏移（如 `+08:00`）按其字面记录，即使与当前时区规则不一致也不修正。

### 3.4 亚秒与极值

- `SubSecTimeOriginal`(0x9291)/`SubSecTimeDigitized`(0x9292)/`SubSecTime`(0x9290) 取**前 3 位**有效数字
  作为毫秒，多余位忽略，绝不四舍五入；缺失则为 `.000`；非数字一律忽略；
- 非法日历（如 `2022-02-30`、月份 13、时 24）**不产生** civil key，按 §4 继续回退；
- 极端但有意义的年份（`0001`/`9999`）必须正确解析并落在上面的取值范围内。

## 4. 来源闭集、优先级与「文件时间」诚实性

### 4.1 闭集与优先级（冻结映射表）

| 序 | `time_source` | 读取点（Pillow） | 标签 | `time_confidence` | 参与时间过滤 | 排序桶 |
|---:|---|---|---|---|---|---|
| 1 | `EXIF_ORIGINAL` | Exif IFD `0x9003` + `0x9011`/`0x9291` | `capture` | 有偏移 `HIGH`，无偏移 `MEDIUM` | 是 | `ordered` |
| 2 | `EXIF_DIGITIZED` | Exif IFD `0x9004` + `0x9012`/`0x9292` | `capture` | `MEDIUM` | 是 | `ordered` |
| 3 | `EXIF_ROOT` | IFD0 `0x0132` + `0x9010` | `file_time` | `LOW` | 否 | `ordered` |
| 4 | `FILE_MTIME` | `file_locations.mtime_ns` 的最小值 | `file_time` | `LOW` | 否 | `tail` |

回退是**逐级**的：某一级缺字段、字段为空、字段不可解析、日历非法或亚秒坏值，就继续下一级；
坏值只被丢弃，不升级成错误、不中断其他资产。`FILE_MTIME` 是链尾，一定可得（`mtime_ns` 是
`NOT NULL` 列），因此抽取对任何资产都能产出一行。

**禁止**的兜底来源：扫描时间、进程当前时间、`assets.created_at`（加入时间）、任何“最近一次成功扫描”
时间。时间来源不可靠时界面必须显示「文件时间」，不得伪装成拍摄时间。

### 4.2 文件位置选择（确定性）

- EXIF 证据来源：按 `location_id` **升序**遍历该资产的 `PRESENT` 位置，取第一个能解析出上述 EXIF 日期
  字段的位置；解析不到就继续下一个 `PRESENT` 位置；
- `FILE_MTIME` 回退值：该资产**全部** `file_locations` 行的 `mtime_ns` **最小值**（含当前 `MISSING` 的行）。
  这样拔掉一块来源硬盘不会改变该资产的文件时间；
- 一个位点都没有的资产（冻结模型下不应出现）不写行，按“未取值”处理。

### 4.3 文件内容变化守卫

沿用 P1-03/ADR-0002 §5.4 的观察守卫：读取某个位置前，若观察到的 `(size, mtime_ns)` 与
`file_locations` 记录不一致（处理期间文件被替换/修改），**不采纳**该位置的 EXIF 结果，继续下一位置或
退回 `FILE_MTIME`，并把该资产留给下一次抽取。读取 EXIF 只允许打开文件头，不得解码像素。

### 4.4 诚实标签

- `label = capture` 当且仅当 `time_source ∈ {EXIF_ORIGINAL, EXIF_DIGITIZED}`；
- `label = file_time` 时界面必须显示「文件时间」，即使该行的 `taken_at` 有值（`EXIF_ROOT`）；
- 只有 `label = capture` 的行参与时间范围过滤（§6）；`EXIF_ROOT`/`FILE_MTIME`/无行**永不**因时间条件命中。
  这不是实现细节，而是“时间范围条件 = 拍摄时间条件”的定义。

## 5. 新 order：`capture_desc` 与独立游标空间

### 5.1 `order` 闭集

`order` 的闭集扩为 `{asset_id, time_desc, capture_desc}`：

- `asset_id`（默认）与 `time_desc`（**加入时间**）语义、SQL、行集与游标空间**逐字不变**：
  前缀 `v1` 一元（`asset_id`）、前缀 `t1` 二元（`created_at, asset_id`）；
- `capture_desc` 是**拍摄时间**浏览：先按 civil key 倒序，再把没有 civil key 的行放到尾部按
  `asset_id` 倒序：

```sql
ORDER BY (m.taken_at_sort IS NULL), m.taken_at_sort DESC, a.asset_id DESC
```

（`m` 为 `asset_metadata`；无行的资产等价于 `taken_at_sort IS NULL`。）

### 5.2 游标

- 前缀 `c1`，arity 4，**只有三个字段** `prefix`/`arity`/`values`，`values` 恰好是
  `[known, sort_key_or_0, asset_id, catalog_filter_revision]`：
  - `known ∈ {0,1}` 区分“有序桶/尾部桶”；有序桶 `known = 1` 且 `sort_key_or_0` 是 civil 键；
  - 尾部桶 `known = 0` 时 `sort_key_or_0` **必须**恒为 `0` 且不解释（不是“可写任意值”）；
  - `asset_id >= 1`；
  - **第 4 个元素就是铸造时绑定的 `catalog_filter_revision` 本身**：游标里**没有**第二个
    `catalog_filter_revision` 字段，因此“绑定的 revision”与“游标元素”不可能互相矛盾；
- 三个游标空间**互不解释**：任一空间铸造的游标被另外两个空间或旧版本使用时必须被拒绝
  （`INVALID_CURSOR`，422），绝不静默按位置续页；
- `capture_desc` 的游标额外绑定铸造时的 `catalog_filter_revision`（第 4 个元素）。续页时与当前值不同 ⇒
  409 `CATALOG_FILTER_REVISION_STALE`，details **只用既有键**：
  `{"reason": "CATALOG_FILTER_REVISION_CHANGED", "current_catalog_filter_revision": <n>}`。
  这里没有会话，所以不带 `search_session_id`/`session_catalog_filter_revision`，也不新增任何 details 键；
  客户端丢弃游标重新打开列表。
- 为什么绑定 revision：`capture_desc` 的排序键是**可变**的权威事实（尚未抽取的资产在抽取后才获得
  civil key）。ADR-0009 §3.5/§6.1 只允许一套 revision，因此这里不能在旁边新造“时间 revision”。
  代价是无关的人工编辑也会让 `capture_desc` 的续页失效——这是**有意**的保守成本，替代方案（静默跳过或
  重复行）被否决。

### 5.3 分页稳定性

在 revision 不变的前提下，`capture_desc` 分页不得重复、不得跳行，`asset_id` 是唯一的 tie-break；
权威事实一变就明确拒绝续页，而不是发布一个混合版本的页。

## 6. 时间范围条件、单一 AND 集合与单一 revision

### 6.1 条件在两个信封上的形态

同一条件在两个面上有各自的传输拼写，语义完全同一：

| 面 | 字段 |
|---|---|
| `GET /api/v1/assets` | 查询参数 `capture_from`、`capture_to_exclusive` |
| `POST /api/v1/search` | 请求信封可选字段 `capture_from`、`capture_to_exclusive` |

主架构 §9.1 的 `time_range {from, to_exclusive}` 是**解析器内部结构**；它落到这里定义的同一对字段，
不得出现第二套条件表示。两个字段都可单边省略；都不给即“无时间条件”。

### 6.2 取值语法（闭集）

- `YYYY-MM-DD`：该日 `00:00:00.000`（这是**墙上日历**语义，不做任何时区换算）；
- `YYYY-MM-DDTHH:MM:SS` 与 `YYYY-MM-DDTHH:MM:SS.f{1,3}`；
- 拒绝一切带时区的写法（尾随 `Z` 或 `±HH:MM`）——拍摄时间没有唯一时区，接受偏移会让调用方以为
  服务端做了换算；`reason = capture_bound_has_offset`；
- 拒绝宽松/歧义写法（`2022/01/01`、`20220101`、`2022-1-1`、空串、首尾空白、小数位超过 3 位）与非法
  日历（`2022-02-30`、月份 13、时 24）：`reason = capture_bound_malformed`；语法合法但年份为 `0000`：
  `reason = capture_bound_out_of_range`；
- 两端都给且 `capture_from >= capture_to_exclusive`：`reason = capture_range_not_ascending`（空区间是
  显式拒绝，而不是静默空结果）。

### 6.3 规范化与回显

回显一律是规范化形式 `YYYY-MM-DDTHH:MM:SS.mmm`（日期简写展开）。这样 `2022-01-01` 与
`2022-01-01T00:00:00.000` 是同一个条件、同一个摘要；定宽形式下字典序等于时间序，比较与摘要都稳定。

### 6.4 语义

- 左闭右开：`capture_from <= taken_at_sort` 且 `taken_at_sort < capture_to_exclusive`；
- 比较在**同一个 civil 空间**内进行（条件与键都是墙上时间折算值），因此不需要、也不允许任何时区换算；
- 只对 `label = capture` 的行成立（§4.4）；`EXIF_ROOT`/`FILE_MTIME`/无行的资产永不命中，
  其存在感由 §8 的 `taken_time` 块解释。

### 6.5 与既有条件的关系

- 与 `source_id`/`availability`/`place_ids`/`place_state`/`person_ids`/`person_match`/文本一律 **AND**；
- 绑定**同一个** `catalog_filter_revision`（读取一次、回显一次），不新增第二个计数器：不存在
  `capture_revision`/`time_revision` 之类的字段或表；`place_suggestion_revision` 仍是建议面的独立
  revision，与时间条件无关；
- 会话摘要（`query_hash`）的冻结载荷 = ADR-0009 §6.2 的字段集 **加上**两个规范化 capture 字段；
  两个不同的时间条件必须得到不同摘要，两个写法等价的时间条件必须得到相同摘要。

### 6.6 时间事实变化与 cause

- 写入/更新 `asset_metadata` 行与递增 `catalog_filter_revision` 必须发生在**同一个**受控事务内；
  cause 固定为 `CAPTURE_TIME_CHANGED`，`changed_asset_ids` 为本次受影响的资产；
- 一个事务至多递增一次（批量抽取也是每事务一次）；`catalog_filter_revision_events` 的既有形状原样复用；
- `catalog_filter_revision.cause` 是无 CHECK 的自由文本列，新增 cause 值**不需要**重建表；
- 后台/手动抽取必须收敛到“已抽取资产数 == 资产总数”，否则 §8 的 `taken_time.state` 只能报
  `PARTIAL`/`PENDING`，界面不得把不完整当成完整。

## 7. 失效、投影与重启

- **不新增** `search_projection_queue` 的 cause，不修改 `search_documents`/FTS5：时间不是文本，时间条件
  是权威 SQLite 判定（沿用 ADR-0009 §5.1 的模式），检索候选页直接读 `asset_metadata`；
- 冻结的 `search_projection` 块（ADR-0009 §5.4 的 8 键）**不新增任何字段**；§8 的 `taken_time` 是它旁边的
  **兄弟块**，不得并入、不得改名；
- 会话/游标失效只复用既有码与既有 details 键：`CATALOG_FILTER_REVISION_STALE`、
  `SEARCH_SESSION_EXPIRED`、`INVALID_CURSOR`；
- 抽取任务必须幂等、可续跑：重启后已完成的行不重写、未完成的资产继续；重启不改变任何已抽取事实。

## 8. API、错误闭集与隐私

### 8.1 三个面的形状

1. `GET /api/v1/assets`：新增回显键 `capture_from`、`capture_to_exclusive`（规范化串或 `null`，恒定存在），
   以及**条件性**的 `taken_time` 块（当且仅当给了至少一个 capture 边界时出现）；
2. `POST /api/v1/search`：请求信封新增同一对可选字段；`filters` 回显升级为伴随物的
   `SearchFiltersEchoV2`（ADR-0009 §7.1 的 **11** 键 + 这 2 键 = 13 键，恒定存在），并同样条件性带
   `taken_time` 块；
3. `GET /api/v1/assets/{id}`：新增 `taken_time` 对象（没有行时为 `null`），字段为
   `taken_at`、`taken_at_sort`、`time_source`、`time_confidence`、`timezone_offset_minutes`、`label`、
   `camera`，全部闭合、`additionalProperties: false`。

`taken_time` 块（列表与搜索）字段：`state`、`assets_total`、`extracted_assets`、`capture_time_assets`
（**只有这四个键**，没有隐藏输入）。计数口径：

- `assets_total`：`assets` 的资产数；
- `extracted_assets`：**按当前 `taken_time_rules_version` 可信**的已抽取资产数。库级版本与程序不一致时
  该计数**规范化为 `0`**（行仍在库里，只是不可信），因此下表的 `state` 真的只是这三个数的纯函数；
- `capture_time_assets`：上述可信行里 `label = capture` 的资产数（版本失配时同为 `0`）。

`state` 是这三个数的**纯函数**：

| 条件 | `state` |
|---|---|
| `extracted_assets == 0` 且 `assets_total > 0`（含规则版本失配被规范化成 `0` 的情形） | `PENDING` |
| `0 < extracted_assets < assets_total` | `PARTIAL` |
| `extracted_assets == assets_total` 且 `assets_total > 0` 且 `capture_time_assets > 0` | `READY` |
| `extracted_assets == assets_total` 且 `assets_total > 0` 且 `capture_time_assets == 0` | `NO_CAPTURE_TIME` |
| `assets_total == 0` | `READY` |

作用：当时间条件返回空页时，客户端必须能区分「确实没有命中」与「还没抽取拍摄时间」，不得把后者
当成前者展示。

### 8.2 `filters` 回显的加性取代（本 ADR 唯一声明的取代）

ADR-0009 §7.1 把 `filters` 冻结为一个闭合对象，其 `required`（以及 `properties`）实测为 **11** 键：
`source_id`、`availability`、`place_ids`、`include_descendants`、`place_state`、`expanded_place_ids`、
`catalog_filter_revision`、`suggestion_revision`、`binding`、`person_ids`、`person_match`。时间条件按
§3.5 进入同一 AND 集合后，回显必须是一个对象，因此本 ADR **加性取代**该块：伴随物的
`SearchFiltersEchoV2` 是 live 形状，且是这 11 键的**严格超集**（11 + 2 = **13** 键，只多
`capture_from`/`capture_to_exclusive`，不删不改任何键）。约束：

- `docs/decisions/contracts/p3-person-search-contracts.v1.json` **不修改**（它仍是 ADR-0009 时代的历史冻结，
  由 §13 的哈希锁定）；
- P4-00B 必须把 `tests/unit/test_p3_person_search_contracts.py` 中比较 live echo 键集的那条断言
  （`test_the_frozen_echo_keys_are_the_implemented_echo_keys`）扩展为 V2 键集，并且只允许**加键**；
  该文件里用自带 fixture 与 p3 companion 对照的用例不得被删除或削弱；
- 任何其它端点（`/places`、`/persons`、`/faces`、`/face-mappings`、`SESSION_REQUIRED`）的信封逐字节不变。

### 8.3 错误闭集

| code | HTTP | 语义 | details |
|---|---:|---|---|
| `INVALID_TIME_RANGE` | 422 | 边界语法/日历/顺序不合法 | `reason ∈ {capture_bound_malformed, capture_bound_has_offset, capture_bound_out_of_range, capture_range_not_ascending}`，附 `field ∈ {capture_from, capture_to_exclusive}` |
| `CATALOG_FILTER_REVISION_STALE` | 409 | `capture_desc` 续页的权威 revision 已变 | 既有键子集（§5.2） |
| `SEARCH_SESSION_EXPIRED` / `INVALID_CURSOR` / `SCHEMA_TOO_NEW` / `SCHEMA_INCONSISTENT` / `VALIDATION_ERROR` | 既有 | 既有语义 | 既有键，**不得新增键** |

`INVALID_TIME_RANGE` 是新增 code（与 P3-07B 新增 `INVALID_PERSON_FILTER` 同类）；其 details 使用的
`reason`/`field` 都是既有 details 键名，伴随物里的 `TimeErrorDetails` 属性集被契约测试断言为
ADR-0009 details 词汇的子集。

### 8.4 隐私闭集

公开面**允许**出现的拍摄时间形态：规范化边界回显、`time_source` 枚举、`time_confidence` 枚举、
`timezone_offset_minutes`、`taken_at`（规范化墙上时间）、`taken_at_sort`（civil key）与 `label`；
`camera` 只允许出现在 `GET /api/v1/assets/{id}`，且只允许规范化后的 `Make Model`（各自 `strip`、
折叠空白、去控制字符、合并为一个空格、截断 128；两者都空则为 `null`）。

**不得**在任何响应、错误、日志、URL、反馈中出现：

- `taken_at_raw`（原始时间文本）与任何原始 EXIF 值/blob/缩略图；
- 任何 instant 形式的拍摄时间（本 ADR 根本不产生 instant），也不得把 civil key 标注成 UTC；
- 绝对或相对文件路径、`source_roots.root_path`、文件名以外的路径片段；
- 坐标、`raw_gps`、坐标基准，或任何只有 `/geo` 才允许的内容；
- `BodySerialNumber`、`LensSerialNumber`、`OwnerName`、`Artist`、`Copyright`、`Software`、
  `ImageDescription`、`UserComment` 等设备/身份类标签（本 ADR 只读取时间与 `Make`/`Model`）。

## 9. 迁移与兼容契约（P4-00B 可实现性证明）

- 新 migration：`0015_taken_time.sql`（仓库当前最后一个是 `0014`，下一空号即 `0015`）；
  `SUPPORTED_SCHEMA_VERSION` 由 14 递增为 15；
- `0001`～`0014` **逐字节不变**；本 ADR 不授权也不建议改动任何既有迁移；
- 迁移内容只有 `CREATE TABLE asset_metadata` + 表级 `CHECK` + 索引（+ `metadata` 的
  `taken_time_rules_version` 键），**不含**数据回填：抽取是 P4-00B 的独立、可续跑、幂等任务；
- 事务与回滚沿用 ADR-0002 §7.3：建表与版本推进在同一受控事务内，任一步失败即回滚到 14；
  重启后从 14 重放必须幂等；
- 旧程序遇到 15 必须 `SCHEMA_TOO_NEW` 且不得写库（ADR-0002 §7.4 不变）；schema 一致性检查由
  `catalog/schema.py` 从迁移文件推导，P4-00B 无需手工维护对象清单；
- 依赖与 `uv.lock` 无变化（EXIF 仍由已存在的 Pillow 读取），但
  **`0015_taken_time.sql` 本身是打包数据资源**：`PhotoSearch.spec` 用
  `collect_data_files('photo_search')` 收包，而 `catalog/schema.py::migration_files()` 在运行时从包的
  `catalog/migrations/*.sql` 读取并要求**连续覆盖** `0001`～`SUPPORTED_SCHEMA_VERSION`。因此
  新增 `0015` 会改变冻结包的数据资源集合，**P4-00B 必须把 C 级真实打包 smoke 纳入验收**
  （构建后核对 bundle 内 `0001`～`0015` 连续且与源码逐字节一致，并确认打包后的 data root 实测
  `schema_version = 15`）；只以 `PhotoSearch.spec` 文本未改推断“资源未变”被否决。最小反例：程序声明支持
  15、冻结包漏带 `0015` 时，`migration_files()` 得到 `0001`～`0014`，与期望序列不等并抛
  `SCHEMA_INCONSISTENT`。
- 兼容承诺（未抽取的旧库）：
  - `order=asset_id`/`time_desc` 的 SQL、行集、游标、既有字段逐字不变；
  - 新增键只以 `null` 出现（两个回显键），条件性块不出现；`/assets/{id}` 的 `taken_time` 为 `null`；
  - `order=capture_desc` 在未抽取库上把所有行放进尾部桶，按 `asset_id DESC` 返回（确定性、可解释）；
  - 任何带 capture 边界的查询在未抽取库上返回空页，并带 `taken_time.state = PENDING`；
  - 数据侧回滚：`asset_metadata` 整体可重算，删除其行不影响任何 P1～P3 事实。

可实现性证据（P4-00A 实测，合成 JPEG，无真实照片库）：Pillow 的 `Image.getexif()` 只给出 IFD0 标签
（`0x0132`/`0x010F`/`0x0110` 可读），**Exif 子 IFD 必须用 `getexif().get_ifd(0x8769)` 读取**——
在根对象上 `get(0x9003)` 返回 `None`。`0x9003`/`0x9004`/`0x9011`/`0x9012`/`0x9010`/`0x9291`/`0x9292`
均在该子 IFD 中，读取只需文件头、无需解码像素。契约测试
`test_pillow_exif_time_reading_paths_are_the_frozen_ones` 固定这一证据。

## 10. P4-00B 前置验收清单（D + C + 浏览器）

命令模板（Windows 受限令牌下使用全新项目内 basetemp）：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p400b/<fresh> `
  --junitxml=test-results/p4-00b-full.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

D 级必须执行的故障注入：

1. `0014 → 0015` 在**有数据**的库上升级：既有行、既有 revision、既有游标行为不变；升级后
   `schema_version == 15` 且核心 schema 校验通过；
2. 迁移中途失败/进程被杀：事务回滚，库仍是 14，无半张表；
3. 抽取任务中途被杀后重启：不重复写行、不跳资产、每事务至多一次 `CAPTURE_TIME_CHANGED`；
4. 处理期间文件内容变化（`(size, mtime_ns)` 不匹配）：不采纳该位置的 EXIF，退回文件时间，资产留待重抽；
5. 来源离线/位置 `MISSING`：不因读文件失败而失败，使用 `FILE_MTIME`（min `mtime_ns`，含 `MISSING` 行）；
6. 多位置资产：EXIF 取自 `location_id` 最小的可解析 `PRESENT` 位置；删除该位置后重抽结果确定；
7. 规则版本门禁：库级 `taken_time_rules_version` 与程序不一致时状态为 `PENDING`、行不参与过滤；
8. `capture_desc` 续页在抽取写入后返回 409 stale，丢弃游标重开后不重复、不跳行；`time_desc` 与
   `asset_id` 的续页行为与 P1-05 完全一致。

C 级必须执行的打包验收（`0015` 是打包数据资源，见 §9）：

1. 真实 onedir 重建（`tools/build_onedir.py`），确认 bundle 内
   `photo_search/catalog/migrations/` 含 `0001`～`0015` 且**逐个与源码 SHA-256 一致**；
2. `tools/packaged_api_smoke.py` 在中文/空格工作目录下 `status=ok`、`problems=[]`，打包后的 data root
   实测 `schema_version = 15`；
3. 缺件最小反例：删掉 bundle 内的 `0015` 后启动必须报 `SCHEMA_INCONSISTENT`（而不是静默只跑到 14）。

记录式真实浏览器（内置浏览器，非 Playwright/Selenium PASS）：

1. 用 `capture_from`/`capture_to_exclusive` 过滤并逐页浏览，条件回显为规范化串、左闭右开（边界日
   包含、右端不含）；
2. `order=capture_desc`：无拍摄时间的资产出现在尾部，界面显示「文件时间」；
3. 有偏移的行显示 `±HH:MM`；无偏移的行**不显示**任何时区、也不显示 UTC；
4. 空结果时区分「无命中」与 `taken_time.state ∈ {PENDING, NO_CAPTURE_TIME}` 的诚实提示；
5. 第二标签页改名/改地点后旧续页明确 stale 并要求重搜，条件不丢；
6. 上传搜图路径不含任何 capture 字段；
7. 窄宽（内置浏览器实际视口）无横向溢出、键盘可达；
8. DOM/URL/访问日志隐私扫描：无 `taken_at_raw`、无路径、无坐标、无序列号。

## 11. 必须固定的反例（最小）

1. 无偏移的本地时间不得当 UTC：`EXIF_ORIGINAL` 无 `0x9011` 时 `timezone_offset = NULL`、
   `time_confidence = MEDIUM`，且 `taken_at_sort` 与“带偏移的同一墙上时间”**完全相等**；
2. 带偏移的边界被拒：`capture_from=2022-01-01T00:00:00Z` 与 `...+08:00` 均答 422
   `INVALID_TIME_RANGE`/`capture_bound_has_offset`；
3. 空区间被拒：`capture_from == capture_to_exclusive` 答 `capture_range_not_ascending`；
4. 右端不含：恰好等于 `capture_to_exclusive` 的资产不出现，等于左端的资产出现；
5. 未知来源被拒：`time_source = 'EXIF_GPS'`/`'SCAN_TIME'` 不在闭集内；
6. 坏值继续回退：`0x9003` 非法日历 + 合法 `0x9004` ⇒ `EXIF_DIGITIZED`；
   两级都坏 + 合法 `0x0132` ⇒ `EXIF_ROOT`；全坏 ⇒ `FILE_MTIME`；
7. 扫描时间/当前时间兜底被禁：闭集里不存在相应成员，`taken_at_sort` 也不会取自 `created_at`；
8. 三个游标空间互不解释：`t1`/`v1` 游标被 `capture_desc` 拒绝，`c1` 游标被前两者拒绝（均 422
   `INVALID_CURSOR`）；
9. `time_desc` 仍是加入时间：改写 `created_at` 后该顺序随之变化，而 `capture_desc` 不变；
10. `filters` 回显仍是单一对象：V2 键集 = ADR-0009 的 11 键 + 2 = 13，且只有一个
    `catalog_filter_revision`（没有第二个 revision 键）；
11. `EXIF_ROOT` 不参与过滤：只有 `0x0132` 的资产在覆盖其日期的 capture 过滤中**不出现**，
    但仍能在 `capture_desc` 的 `ordered` 桶中出现且标签为 `file_time`；
12. `capture_desc` 游标有三个必须被拒的形态（阶段 B 探针的最小反例）：
    `values = [2, 0, 5, 12]`（`known` 越界）、`values = [0, 99, 5, 12]`（尾部桶的 sort 元素非 0）、
    `values = [1, 0, 0, 12]`（`asset_id` 为 0）；另加“带一个多余 `catalog_filter_revision` 字段”的对象
    也必须被拒（该字段已按 §5.2 取消，避免与第 4 个元素双表示）；
13. 规则版本失配：库级 `taken_time_rules_version` 与程序不一致时，`extracted_assets` 与
    `capture_time_assets` 规范化为 `0`，三计数为 `(10, 0, 0)` ⇒ `state = PENDING`；绝不能出现
    “同样三计数却有两种状态”。

## 12. 后果与未验证项

- 收益：拍摄时间第一次有唯一、可实现、可拒绝的语义；排序与展示一致；不可靠来源永不伪装成拍摄时间；
  时间条件沿用一个 AND 集合与一套 revision，不引入第二套失效协议；
- 代价：`capture_desc` 的续页会因任何人工编辑而失效（§5.2 的有意保守）；抽取期间时间条件的空结果
  必须靠 `taken_time` 块解释；`capture_desc` 的排序键可变，因此不能像 `time_desc` 那样完全不绑定版本；
- 未验证（不得当成 PASS）：30 万资产下 `capture_desc`/时间过滤的延迟与最终索引形状、全库抽取耗时、
  多进程并发抽取、`0015` 在真实库上的实测耗时、内部存储格式（大小写、已知坏相机样本）的覆盖率、
  GTX 1660 实机；这些属 P4-00B/P4-01/P4-02/P4-04 与 C1 范围。C 级打包**不属于**“未验证项”，而是
  §10 对 P4-00B 的硬性要求；
- 本 ADR 未实现、未迁移、未跑真实照片库；P4-00A 阶段 A 结束时状态只能是
  `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`；修复轮后的阶段 B 短审已通过，契约状态转为
  **Accepted**，实现仍全部留给 P4-00B。

## 13. 伴随物、哈希与契约测试

- `docs/decisions/contracts/p4-time-contracts.v1.json`
  SHA-256：`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`
- 既有伴随物在本 ADR 下**逐字节不变**（由契约测试锁定哈希）：`p2-contracts.v1.json`、
  `p2-place-contracts.v1.json`、`p3-acceptance-matrix.v1.json`、`p3-face-correction-contracts.v1.json`、
  `p3-face-redetection-contracts.v1.json`、`p3-person-search-contracts.v1.json`、
  `p3-person-split-contracts.v1.json`。
- `tests/unit/test_p4_time_contracts.py` 固定：伴随物的合法性与闭合性、`time_source`/`time_confidence`
  闭集、`timezone_offset` 范围与“无偏移不等于 UTC”、`order` 闭集、三个游标空间互不解释、
  `capture_desc` 游标的三个越界/双表示负例、左闭右开与端点、回退优先级与坏值继续回退、
  单一 AND 集合/单一 revision/摘要规范化、规则版本失配计数规范化、`time_desc` 逐字不变、隐私闭集、
  Pillow EXIF 读取路径证据。
