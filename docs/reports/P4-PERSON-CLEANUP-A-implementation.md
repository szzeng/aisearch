# P4-PERSON-CLEANUP-A — 整理残留与成功提示

日期：2026-10-04。状态：IMPLEMENTED / 简单定向验证完成；独立人物审查仍PENDING。基线：d85c63a；保留首页改名/排队等并行修改。本轮通过本地提交收口，未推送。

用户反馈：已经纠正/整合的人物仍在未命名区；追加“已归到‘有力气’”长期停留，要求短暂显示后消失。

## 原因与修改

`persons/albums.py::people_page`原先只检查name=NULL和merged_into=NULL。单脸指定到已有具名人物会保留原人物行供历史/撤销使用；最后一张脸移出后，空人物仍被投影到整理列表。新增未命名列表的有效成员EXISTS条件，在LIMIT和游标之前排除空组；复用MEMBER_PREDICATE并对应ACTIVE照片。不删除人物行、不更改人工关系、模型/门限或schema。named/all列表保留空身份，既有目标选择能力不变。合照若仍有另一张未命名人脸，仍正确显示其小图和照片；不能因为其中一人已整理就移除另一人。

`persons-albums.js`原先撤销条有10秒计时，但重复写到`pa-status`的成功文本没有计时。成功状态（命名/归并/更正/撤销）改为3秒自动清空并隐藏；每次消息取消前一计时，后来的错误不会被旧成功计时清掉。撤销条维持10秒及手动收起入口。成功后编辑对话框正常关闭；HTML默认隐藏空状态，并更新JS资源版本。

## 简单验证

新增用例修复前：JUnit `test-results/p4-person-cleanup-red.xml` **4/2/0/0**。更正最后一张脸后未命名列表仍返回原ID，成功状态3秒后仍显示；显式合并/命名原来就能离开未命名区，作为回归保护保留。

最终：`test-results/p4-person-cleanup.xml` **13/0/0/0**。覆盖真实保护API的单脸更正/显式合并/命名、只读零写入、撤销恢复、同图剩余人脸与小图、人工移出/排除/机器建议不算有效成员、空组在分页首/中/尾的过滤、具名空身份可选；既有人物相册7项一起回归。实际JS在最小DOM和模拟时钟下覆盖归属提交、关闭编辑窗、3秒提示消失、10秒撤销保留、连续成功刷新计时和后来错误保留。只使用小型合成照片与隔离catalog。

命令（项目Python 3.12.10）：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_cleanup.py tests/integration/test_p4_person_albums.py tests/unit/test_person_cleanup_ui.py --basetemp=.pytest-tmp/person-cleanup-final-2 --junitxml=test-results/p4-person-cleanup.xml
.venv/Scripts/ruff.exe check src/photo_search/persons/albums.py tests/integration/test_p4_person_cleanup.py tests/integration/test_p4_person_albums.py tests/unit/test_person_cleanup_ui.py
& 'D:/Program Files/nodejs/node.exe' --check src/photo_search/web/persons-albums.js
git diff --check
```

初次沙箱测试在项目临时目录创建触发WinError5，属于环境失败；通过正常Windows权限重跑。第一次绿色轮有1个测试夹具错误：assets.schema只允许ACTIVE，已移除不存在的DELETED假设；最终13项全部执行通过。Ruff初次两处导入格式问题已修正，最终静态检查通过。

## 使用与限制

重启应用并刷新人物页生效；已有空分组下一次加载即隐藏，无需重新建库或识别人脸。未访问真实库/PhotoSearchData、未重启用户服务；无真实浏览器/模型/全量/性能/打包验证。独立受影响面审查补充至[P4-PERSON-UX-A-review](../tasks/P4-PERSON-UX-A-review.md)，不把本轮实现者验证称为独立PASS。
