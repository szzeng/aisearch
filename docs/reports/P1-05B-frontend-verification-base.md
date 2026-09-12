# P1-05B — 前端静态契约断言集中化与浏览器 smoke 固化

状态：**PASS**（A 级验收真实完成，另按任务卡要求真实运行了一次夹具脚本，并额外验证了 `--too-new`
与 `--offline` 两条分支）。
执行模型（记录用）：**DS-L / DeepSeek V4.1 Flash / 低推理**（与任务卡推荐一致）。
模型切换始终由用户在 Codex 中手动完成，本任务未尝试、也未声称自动切换。

本任务性质：**P1-05 的非阻塞后续项**。任务卡：`docs/tasks/P1-05B.md`。
基线：**P1-05 收口提交 `e2654caa9daba8f86dadf31fe7afd5fc8fe621e8`**
（`git log -1 --format=%H -- docs/reports/P1-05-*.md` 复核一致；其后 `c892176` 只是新增
P1-05A/P1-05B 任务卡的 docs-only 提交，不改变基线语义）。

本任务**未修改任何生产代码**：`src/photo_search/**` 一行未动（含 `web/**`），`pyproject.toml` /
`uv.lock` / 迁移 / `schema_version` / ADR-0002 均未改动；除 `tests/unit/test_api_frontend.py` 追加断言外，
没有触碰任何既有测试文件。

---

## 1. 交付物清单

| 交付物 | 位置 |
|---|---|
| 静态契约断言（追加 4 个用例，42 → 46） | `tests/unit/test_api_frontend.py` |
| `%TEMP%` 夹具生成脚本 | `tools/diagnostics/frontend_smoke_fixture.py`（新建，约 250 行） |
| 浏览器 smoke 核对清单 | `docs/diagnostics/frontend-smoke-checklist.md`（新建，新建 `docs/diagnostics/` 目录） |
| 本报告、状态更新 | `docs/reports/P1-05B-frontend-verification-base.md`、`docs/development-status.md` |

`docs/tasks/P1-06.md` 未改动；本卡**不生成**新的下一任务卡。

## 2. 开始前必须先检查（任务卡第 1-5 项）的实测结论

1. **已有断言清点**：`tests/unit/test_api_frontend.py` 原有 42 个用例，其中
   `test_the_shipped_shell_has_no_external_reference_or_credential` 已覆盖「无 `http://`/`https://`、
   `lang="zh-CN"`、引用 `/web/app.js` 与 `/web/styles.css`、`ps_csrf`、`X-CSRF-Token`、
   `credentials: "same-origin"`、无 `localStorage`/`sessionStorage`/`console.`、路径守卫与
   「错误详情包含本机路径」、`加入时间`、`time_desc`」。本次补的是**未覆盖的契约**（见 §3），不是搬运。
2. **契约确实成立**（读源码确认，未改动）：`app.js` 只有两处 `fetch(`，都写作 `fetch(API + …)`；
   「打开原图」是 `link.href = API + "/assets/" … + "/original"`；`app.js` 里
   `token=`/`csrf_token`/`document.cookie =` 全部不出现（只有注释里的 “CSRF token” 与
   `X-CSRF-Token`）；来源列表渲染只用 `display_name`/`status`/`counts`/`last_successful_scan`，
   从读 `source.root_path`；`root_path` 仅作为 `POST /sources` 的请求字段出现一次。
3. **脚本风格参考**：`tools/diagnostics/packaged_api_smoke.py`（argparse + `%TEMP%` 工作目录 + 打印
   JSON 报告）；本次沿用同一风格，未引入任何新依赖（只用 stdlib + 已装 Pillow）。
4. **核对清单来源**：P1-05 报告 §6（10 步实测观察）与 §8（限制清单）。
5. **无 Node 工具链已确认**：仓库内不存在任何 `package.json`，`pyproject.toml` 只有 Python 依赖，
   因此自动化 DOM 断言不在本卡范围（需单独 ADR 与依赖决策）。

## 3. 新增的静态契约断言（每条防一类回归）

| 用例 | 防的回归 |
|---|---|
| `test_frontend_reaches_data_only_through_the_protected_api` | 出现绕过 `/api/v1` 的取数路径：断言 `var API = "/api/v1";`、`script.count("fetch(") == script.count("fetch(API +")`、原图链接指向受控资产 id、页面不出现 `sqlite`/`catalog.db` |
| `test_frontend_never_puts_a_credential_in_a_url_or_in_storage` | 令牌被写进 URL 或浏览器存储：断言无 `token=`/`csrf_token`、无 `document.cookie =`（只读）、无 `localStorage`/`sessionStorage`/`console.` |
| `test_frontend_never_renders_an_absolute_local_path` | 来源根路径被渲染进 DOM：断言 `root_path` 只作为请求字段出现、页面从不读 `source.root_path`、`PATH_LIKE` 与「错误详情包含本机路径」守卫仍在 |
| `test_frontend_copy_stays_honest_about_time_and_offline_media` | 文案把入库时间说成拍摄时间 / 离线资产无提示：断言 `加入时间`、`不是 EXIF 拍摄时间`、`原文件不可用`、「请使用启动器打印的一次性链接建立会话。」、失败产物显示服务端 `result.code`（`badge-code`） |

用例数：`tests/unit/test_api_frontend.py` 收集 **46**（原 42 + 新增 4）。既有断言一条未删、未改。

## 4. 夹具脚本（`tools/diagnostics/frontend_smoke_fixture.py`）

用法与实测输出：

```text
uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt --reset

ready: 60 photos + 2000 bulk + 1 corrupt file(s) under
       …\Temp\P1-05B 前端验收\照片库 中文 空格
next:  uv run python -m photo_search --port <free-port> --data-root
       "…\Temp\P1-05B 前端验收\PhotoSearchData 数据"
checklist: docs/diagnostics/frontend-smoke-checklist.md
```

磁盘实测（PowerShell，非控制台编码伪影）：`照片库 中文 空格\` 下有 `子目录 中文/`、`nested/deep/`、
`bulk/`（2000 个 `b####.jpg`）、`corrupt.jpg`、`p001.jpg`、`p002.jpg`；按 `i%3`/`i%5` 规则
`p000.jpg`/`p003.jpg`/`p005.jpg` 位于子目录（用 `Test-Path` 逐项确认）；`--reset` 只删除本脚本创建的
库目录（上次运行的目录被正确重建，`removed` 字段如实报告）。

参数分支另行实测（独立工作目录 `%TEMP%\P1-05B 参数自检`，`--photos 4 --bulk 3 --corrupt --too-new
--offline bulk/b0001.jpg --offline p000.jpg --offline nope.jpg`）：

| 参数 | 实测结果 |
|---|---|
| `--offline bulk/b0001.jpg` | `deleted: ["bulk/b0001.jpg"]`；目录内文件数 8 → 7 |
| `--offline p000.jpg` / `--offline nope.jpg` | `not_found: ["p000.jpg", "nope.jpg"]`（`p000.jpg` 按规则位于 `子目录 中文/`，根目录确实没有该文件；`nope.jpg` 不存在） |
| `--too-new` | 生成 `PhotoSearchData 过新\catalog\catalog.db`，实测 `schema_version = 2`、表
`['future_only', 'metadata']`（与 P1-04/P1-05 的 `SCHEMA_TOO_NEW` 夹具形状一致） |
| 越界保护 | `--offline ../x` 之类会被 `_within()` 拒绝并打印 `refusing to delete …`，不触碰库外路径 |

脚本本身**不启动服务器、不启动浏览器**，只写自己工作目录下的合成图片/数据库，并打印启动命令与
10 步期望观察；输出在管道/重定向场景下强制 UTF-8（`force_utf8_when_piped()`），避免中文路径变成乱码。

## 5. 浏览器 smoke 核对清单

`docs/diagnostics/frontend-smoke-checklist.md` 固化了 P1-05 报告 §6 的 10 步：每步给出「操作 / 期望观察 /
不满足即不得 PASS 的判据」，并包含：

- 夹具生成与真实端口启动命令（与 §4 一致）；
- 三条可直接粘贴的 DOM 断言（正文无盘符/用户目录路径、窄视口无横向溢出、浏览器存储为空）；
- 「本仓库不做自动化 DOM 断言」的理由与需要单独 ADR 才能改变这一限制；
- 已知限制：窄视口 ≠ 真实 200% 缩放、`time_desc` 只按入库时间（`taken_at` 需 C2 + 迁移）、
  详情里的产物状态是请求时刻的快照。

## 6. 真实执行的命令与退出码

| 命令 | 退出码 | 摘要 |
|---|---|---|
| `uv run pytest -q tests/unit/test_api_frontend.py` | 0 | `46 passed`（原 42 + 新增 4） |
| `uv run ruff check .` | 0 | `All checks passed!`（含新脚本与新断言） |
| `git diff --check` | 0 | 无空白错误 |
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt --reset` | 0 | 2061 个文件（60 + 2000 + 1 corrupt），打印来源根路径、启动命令与 10 步期望观察 |
| 参数自检运行（`--too-new` / `--offline`，独立 `%TEMP%` 目录） | 0 | 删除 1 个文件、报告 2 个不存在、生成 `schema_version = 2` 的库（见 §4） |

## 7. 未验证项、限制与「不可声称」

1. **没有自动化 DOM 断言**，也不能因为静态断言通过就声称前端已被自动化测试覆盖；交互闭环仍是人工核对
   （清单已固化）。真要做自动化需要单独 ADR + 依赖决策（Node/npm 或无头浏览器）。
2. **窄视口 ≠ 真实 200% 缩放**：清单用窄视口 + 无横向滚动作为代理，操作系统级缩放在 P1-05 与本次均未
   实测（P1-05 报告 §8.3 已记录）。
3. **本卡未重跑浏览器 smoke**：任务卡范围就是「固化」，不是「重跑」；P1-05 报告 §6 的 10 步仍是最近一次
   真实浏览器证据，清单是它的可重复版本。下次真正按清单执行时，结果要按 §5 的方式逐条记录。
4. **夹具脚本只在 Windows + 本仓库环境验证**（中文/空格路径、`%TEMP%`）；`%TEMP%` 在不同环境可能指向
   其他盘符，脚本默认取 `tempfile.gettempdir()`，行为已按此验证。
5. **`taken_at`（EXIF 拍摄时间）仍不可声称**：页面只按入库时间排序与展示，需要 C2 决策 + ADR-0002 §7
   加列迁移后才能引入第二个时间维度。
6. **`--offline` 的语义局限**：它只是删除原文件（不触发重扫），用于演示「实时离线提示」；图库记录的
   `availability` 仍要重扫才会变成 `missing`（清单步骤 6/7 与 P1-05 报告 §6 均按此说明）。
7. **未触碰的真实数据**：本任务所有夹具都在 `%TEMP%` 下，未读取、修改、移动或删除任何真实照片库；
   遗留夹具（`%TEMP%\P1-05B 前端验收`、`%TEMP%\P1-05B 参数自检`）可随时删除。

## 8. 交接记录

- `docs/development-status.md` 已更新：P1-05B = PASS；**「当前任务」指针回到 P1-06**
  （推荐 **DS-H / DeepSeek V4.1 Flash / 高推理**）。
- 未生成新的下一任务卡（主线仍是 `docs/tasks/P1-06.md`）。
- 建议下一步：由用户手动切换到 **DS-H / 高推理** 后执行 `docs/tasks/P1-06.md`
  （重复/移动/替换/断盘/重映射/重启验收，D 级）；P1-05A（脱敏补强）仍待 **C1 / Terra / medium**。
