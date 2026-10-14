# P4-PLACE-MAP-TIANDITU-A 实施与验收报告

日期：2026-10-14
状态：**FUNCTIONAL_SCOPED_PASS / ACCEPTANCE_PARTIAL**

## 实施内容

- 将地图页与照片详情小地图切换到天地图官方 `_w` Web Mercator XYZ 栅格图层：标准矢量/注记及卫星影像/注记；署名常驻显示。切换底图不重建照片业务图层与列表。
- 新增固定 provider 的本机设置 API 和 `settings/map-provider.json` 数据根配置：读接口只回显状态与默认图层，Key 仅在认证 runtime 响应中短暂返回；写操作沿用现有认证、Origin/CSRF 防护与 no-store 策略。Web Key 字段为密码型，不回填；清除需二次操作；测试瓦片只请求固定低缩放样例。
- 地图服务/配置/瓦片不可用时，照片定位、列表与分页仍可用；详情保留坐标与地图相册链接。
- 写入以原子替换和进程内锁保护；应用运行期间 data-root 的互斥 lease 是并发访问前提。配置目录和文件在 Windows 继承 data-root ACL。本次未检查或修改 ACL（用户明确拒绝授权 ACL 更改）；因此 Windows 本机密钥文件访问边界仍为待验项。

## 自动验证

- Ruff：通过。
- 定向 pytest：JUnit `26 tests / 0 failures / 0 errors / 1 skipped`，见 `test-results/p4-place-map-tianditu-junit.xml`。跳过项是 Windows 缺少符号链接权限的路径拒绝测试（WinError 1314），不是产品测试失败。
- Python `compileall`：通过。
- Node `--check`：`map-config.js`、`map.js`、`photo-info.js` 通过。
- `git diff --check`：通过。
- Luna High 独立 repair-only 复核完成：runtime 恢复 finding 已修复并由子代理确认关闭；无剩余代码阻断 finding。并发写入 finding 由进程内 RLock 与 data-root lease 关闭。Windows ACL 按用户要求未检查、未修改，作为控制边界待验。

## 浏览器冒烟

使用 Codex 内置浏览器访问本机 `127.0.0.1` 应用的一次性会话，并使用项目内隔离合成 GPS 数据根（复制自既有合成 fixture）；未访问 `PhotoSearchData`，未使用或记录用户真实 Key。

- A 无 Key：地图相册显示 2 张合成带位置照片，设置入口/未配置状态正常，列表可用；最后确认署名可见。
- B 假 Key：设置保存后不回显 Key；固定测试瓦片失败提示原因范围；底图失败时地图页仍显示 2 张照片及点位，切换到卫星层不丢列表与照片图层；清除 Key 后恢复无 Key 状态。
- C 可加载真实瓦片及标准/卫星实际影像对齐：未完成。假 Key 的瓦片失败不能替代在线验收。
- 浏览器截图：`NOT_EXPORTED_TOOL_LIMIT`（本次 CUA 没有可用的截图导出能力）；未在报告保存伪造截图。

## 待完成验收

- `REAL_TIANDITU_NETWORK=PENDING / USER_MANUAL_CHECK`：缺少用户个人 Key 和在大陆无 VPN 的实际验证。用户可在本机设置页输入 Key，分别核验深圳等合成/已知坐标的标准、卫星图层、署名和坐标对齐；无需向开发模型提供 Key。
- `WINDOWS_ACL=PENDING / USER_DECLINED_ACL_CHANGE`：密钥文件当前继承 data-root ACL；本轮不修改权限或 ACL，故不能声称 Key 文件权限已收紧或验证。
- CUA 截图导出待可用工具支持。

未运行全仓、30 万压测、模型/GPU、打包/发布；本任务代码与记录将按用户要求做本地提交，不推送远端。
