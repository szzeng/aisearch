# P1-05A — 本机路径脱敏补强

状态：**PASS**。

## 问题与根因

P1-05 的真实浏览器记录已保存了原始问题：Pillow 针对损坏图片的失败记录包含
`cannot identify image file 'C:\\Users\\…\\corrupt.jpg'`。该字符串的分隔符是 repr
形式的双反斜杠；旧 `Redactor` 仅对登记根的原始字面量做不区分大小写的替换，因而不能匹配这一形态。
这会令资产详情的 `media[*].message` 泄露本机绝对路径。

## 改动

- `src/photo_search/api/errors.py`：`Redactor` 保留公开 `pairs` 的逻辑根语义，并私下为 Windows
  根建立大小写不敏感的匹配模式。模式覆盖单/双反斜杠、正/混合分隔符和 `\\?\` 扩展长度前缀；
  逻辑根仍按最长优先处理。
- 登记根替换之后，对剩余的盘符绝对路径、UNC 和扩展 UNC 路径使用稳定的 `<local_path>` 兜底。
  该路径同样通过 `details()` 的递归调用生效；没有配置任何根时，原有空值/原样返回行为不变。
- 新增 `tests/unit/test_api_redaction.py`，以及一条仅新增的媒体 API 集成用例。页面的 `PATH_LIKE`
  守卫、错误信封、状态码、调用方和其他生产模块均未改动。

## 验证证据

| 检查 | 结果 |
|---|---|
| `uv run pytest -q tests/unit/test_api_redaction.py tests/integration/test_api_files.py` | exit 0，21 passed |
| `uv run pytest -q tests/unit` | exit 0，212 passed |
| `uv run pytest -q tests/unit tests/integration` | 后台独立进程完整运行至 `[100%]`，303 passed、1 skipped；stderr 为空 |
| `uv run ruff check .` | exit 0，All checks passed |
| `git diff --check` | exit 0，无输出 |
| 真实端口 | exit 0：`python -m photo_search --host 127.0.0.1 --port 18766 --data-root <TEMP>`；一次性会话、来源注册、扫描、损坏图缩略图请求与详情请求全部完成 |

真实端口夹具仅在 `%TEMP%`：1 字节 `corrupt.jpg`、临时来源与数据目录。扫描成功后，缩略图请求返回
`409 DECODE_FAILED`；详情返回 200，`media.thumbnail.message` 为（已脱敏）：

```text
cannot identify the file as an image: cannot identify image file '<source_root>\\corrupt.jpg'
```

完整 JSON 响应断言不含来源根的单反斜杠或 repr 双反斜杠拼写，且 code 和非空可读原因都保留。

## 完整套件执行说明

交互执行通道会在既有 `test_ai_worker_lifecycle` 的 CPU worker 场景中截断约 30 秒后的前台输出。改为
后台独立进程后，同一条任务卡命令完整运行至 `[100%]`，输出为 303 passed、1 skipped，stderr 为空，且
进程正常结束；因此 B 级验收已完成。

## 不变项与限制

未新增依赖、表或迁移；`schema_version = 1`，ADR-0002 与会话/CSRF/Host/Origin/受控文件边界不变。
本次未穷尽 `\\?\Volume{GUID}\`、8.3 短名或 Unicode 归一化差异；若这些形式成为实际输入，应交 C1
继续聚焦验证。
