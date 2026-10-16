# P4-PERSON-AVATAR-CLICK-A — 待整理/已忽略及合集独立人脸保留头像打开

- 日期：2026-10-16；状态：IMPLEMENTED / BASIC VALIDATED（已补齐未识别人脸下的已忽略人脸）；浏览器按用户要求未执行。
- 用户要求：简单任务卡；Luna High 子代理实际修复；简单定向测试；不做浏览器测试。
- 开工：AGENTS.md → development-status.md → 本卡。架构仅读 V3.0 §10.1、V3.1 §2.4/§3 相关测试边界；直接参考现有人物批量 UX、头像复用、查看器返回代码与测试。不重读全文。

## 交互与范围

- 用户最终纠正：**待整理 → 未识别人脸（包含该入口下已忽略的人脸）**和**合集 → 单独收录的人脸**需要头像打开照片、底框选择；普通人物、未命名人物、已忽略人物及合集人物成员保持原来的点击模式与 checkbox 位置。
- 待整理/已忽略人脸（`renderInboxFace` 的两种 `hiddenFace`）管理中头像打开对应照片，底框、说明和空白切换选择；checkbox 独立选择且移出头像；参考头像和业务按钮不成为选择目标。两个列表仍使用独立选集与范围锚点。
- 合集独立人脸（`collectionFaceCard`）保留头像开对应照片、底框单项切换、Shift 范围加选，checkbox 移出头像；忙时/过期/500 上限保护保持。
- 定点恢复上轮越界改动：普通人物/合集人物成员头像打开分流、额外键盘入口、合集返回选集快照与对应 CSS；保留此前头像 hover 和共享照片详情并行修改。
- 上述人脸区域的 Shift 只在选择区域沿用已有范围选择；头像打开不改变选集或范围锚点，关闭照片保留底层批量模式与选择。
- 上述人脸区域的 checkbox 不遮挡头像范围；保留可见焦点与键盘操作，避免一次点击既打开又选择。
- 沿用 busy、pendingWrite、stale、500 上限、独立 person_id/face_id Sets 与返回上下文；不改后端、身份/归属、写入/撤销、数据库、真实图库或公共 viewer 状态机。
- 主要改 persons-albums.js、persons.css、persons.html 资源版本与直接相关定向测试；persons.js 仅存在直接相关问题时修改。保留全部现有并行变更，尤其 persons.html 的 photo-info.js 版本。

## 简单验收与交付

- 复用 Node DOM harness，验证待整理/已忽略/合集独立人脸头像打开零选择变化、底框/checkbox 与 Shift；另确认普通人物、合集人物成员仍按原行为选择。仅运行直接受影响的少量 pytest 文件，输出 JUnit XML。
- 改动 JS 做 node --check，执行 git diff --check；不运行全仓、GPU、性能、打包或真实图库测试。
- 浏览器按用户要求不运行，明确 BROWSER_NOT_RUN_USER_REQUEST，不能标浏览器 PASS。
- 主代理检查子代理完整 diff、调用链与测试结果；这是实现后限定复核，不冒充独立 DS-H 审查，不标正式整体 PASS。
- 交付报告 docs/reports/P4-PERSON-AVATAR-CLICK-A.md，更新本卡和 development-status.md；下一步为用户日常试用反馈，不自动扩展新任务。按用户追加要求本地范围提交，不推送。

## 完成记录

Luna High 已补齐已忽略人脸，待整理/已忽略共用头像浏览与底框选择逻辑，选集和锚点仍独立。主代理再次确认五个人物/导航相关函数与 HEAD 完全一致。最新定向 JUnit **4/0/0/0**，Node、Ruff、diff-check 通过，资源版本为 `p4-person-avatar-click-a-3`。此前 16 项结果仅为上一版本历史证据。不运行浏览器、不标整体 PASS。按用户追加要求本地范围提交，不推送，保留共享会话 `7330bfd` 的照片详情修改。下一步用户日常试用反馈，命令与边界见[报告](../reports/P4-PERSON-AVATAR-CLICK-A.md)。
