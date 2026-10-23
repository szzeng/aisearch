from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_daily_copy_uses_current_entries_and_truthful_photo_recycling() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    search = (WEB / "search.js").read_text(encoding="utf-8")
    timeline = (WEB / "photo-timeline.js").read_text(encoding="utf-8")

    assert "图库管理与分析明细" not in app
    assert "来源与扫描" not in app
    assert "打开“图库管理”，展开“添加图库来源”，填写照片文件夹路径后开始建库。" in app
    assert "当前条件下没有找到照片" in app
    assert "较早的 " in app and "原照片没有删除" in app
    assert "照片节点已回收" not in timeline
    assert "较早的照片已从当前页面暂时移除，原照片没有删除" in timeline
    assert "原照片没有删除" in search
    assert 'photo-timeline.js?v=p4-ui2-copy-daily-ux-1' in (WEB / "index.html").read_text(encoding="utf-8")
    assert 'photo-timeline.js?v=p4-ui2-copy-daily-ux-1' in (WEB / "persons.html").read_text(encoding="utf-8")


def test_search_copy_preserves_person_and_capture_filter_meaning() -> None:
    search = (WEB / "search.js").read_text(encoding="utf-8")
    html = (WEB / "search.html").read_text(encoding="utf-8")

    assert "未选择人物，当前普通搜索不限制人物。" in search
    assert "以图搜图暂不支持人物筛选。" in search
    assert "以图搜图暂不支持拍摄时间筛选。" in search
    assert "实际生效的拍摄时间范围：" in search
    assert "（含开始，不含结束）。" in search
    assert "本次没有填写搜索词，结果按筛选条件列出，未按内容相关性排序。" in search
    assert "文字搜索索引仍在更新：还有 " in search
    assert "候选照片 " in search
    assert "包含开始日期，不包含结束日期。" in html
    assert "search.js?v=p4-ui2-copy-daily-ux-1" in (WEB / "search.html").read_text(encoding="utf-8")
    assert "person_ids=[]" not in search
    assert "capture_to_exclusive=" not in search


def test_map_and_collection_copy_explain_what_the_count_means() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    map_source = (WEB / "map.js").read_text(encoding="utf-8")

    assert "规则候选照片：" in app
    assert "规则候选照片经排除项复核后，实际可浏览数量可能不同。" in app
    assert map_source.count("地图底图暂不可用，仍可通过照片列表浏览。") >= 5
    assert "app.js?v=p4-ui2-copy-daily-ux-2" in (WEB / "index.html").read_text(encoding="utf-8")
    assert "places.js?v=p4-ui2-copy-daily-ux-3" in (WEB / "places.html").read_text(encoding="utf-8")
    assert "map.js?v=p4-ui2-copy-daily-ux-1" in (WEB / "map.html").read_text(encoding="utf-8")


def test_place_management_copy_uses_natural_actions_and_keeps_safety_boundaries() -> None:
    page = (WEB / "places.html").read_text(encoding="utf-8")
    script = (WEB / "places.js").read_text(encoding="utf-8")

    assert "只有经你确认后才会成为人工地点归属" in page
    assert "待确认建议只包含当前地名包生成、且未被排除的识别结果" in page
    assert "确认所选照片的地点" in page
    assert "取消所选照片的地点归属" in page
    assert "恢复所选照片的自动判断" in page
    assert "某一批失败时会停止后续批次" in page
    assert "单条确认会保留“来自识别建议”的记录来源" in page
    assert "origin=" not in page
    assert "操作记录 #" in script
    assert "请求编号 " in script
    assert 'setError(els.editError, "未找到编号为 " + target + " 的地点，请重新确认编号。");' in script
    assert 'var message = "读取地点失败，请重新加载地点列表后重试。";' in script
    assert "places.js?v=p4-ui2-copy-daily-ux-3" in page


def test_known_place_errors_use_plain_language_and_keep_request_ids() -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node is not None
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('src/photo_search/web/places.js','utf8');
const start=source.indexOf('  function errorText(code)');
const end=source.indexOf('\n\n  function showNotice',start);
assert(start>=0 && end>start);
const context={};
const pathStart=source.indexOf('var PATH_LIKE =');
const pathEnd=source.indexOf(';',pathStart)+1;
assert(pathStart>=0 && pathEnd>pathStart);
vm.createContext(context);
vm.runInContext(source.slice(pathStart,pathEnd)+"\nvar PATH_NOTE='错误详情包含本机路径，已省略。';",context);
vm.runInContext(source.slice(start,end)+'\nglobalThis.describe=describe;',context);
assert.equal(context.describe({code:'PLACE_ASSIGNMENT_CONFLICT',message:'raw internal details',request_id:'req-123'}),
 '这张照片的地点归属已变化，请重新加载后再试（请求编号 req-123）');
assert.match(context.describe({code:'UNMAPPED',message:'unexpected server response',request_id:'req-456'}),
 /\[UNMAPPED\] unexpected server response（请求编号 req-456）/);
assert.equal(context.describe({code:'UNMAPPED',message:'C:\\\\Users\\\\Alice\\\\Photos',request_id:'req-789'}),
 '[UNMAPPED] 错误详情包含本机路径，已省略。（请求编号 req-789）');
assert.equal(context.describe({code:'UNMAPPED',message:'\\\\server\\\\share\\\\Photos',request_id:'req-790'}),
 '[UNMAPPED] 错误详情包含本机路径，已省略。（请求编号 req-790）');
const fetchStart=source.indexOf('  function fetchPlaceById(placeId)');
const fetchEnd=source.indexOf('\n  // ----------------------------------------------------------------------- create',fetchStart);
assert(fetchStart>=0 && fetchEnd>fetchStart);
context.state={placeIndex:{}};
context.URLSearchParams=URLSearchParams;
vm.runInContext(source.slice(fetchStart,fetchEnd)+'\nglobalThis.fetchPlaceById=fetchPlaceById;',context);
(async()=>{
 context.request=()=>Promise.reject({code:'NETWORK_ERROR',message:'offline',request_id:'req-read'});
 let rejected=false;
 try { await context.fetchPlaceById(123); } catch (failure) { rejected=failure.code==='NETWORK_ERROR'; }
 assert.equal(rejected,true,'a read failure must not be converted into a missing place');
 context.request=async()=>({body:{places:[]}});
 assert.equal(await context.fetchPlaceById(123),null,'an empty successful read means not found');
})().catch(error=>{console.error(error);process.exitCode=1;});
""";
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_copy_guidance_is_part_of_the_existing_visual_standard() -> None:
    standard = (ROOT / "docs" / "plans" / "P4-UI-VISUAL-TOKENS.md").read_text(encoding="utf-8")
    assert "## 用户文案与提醒" in standard
    assert "提示引用的按钮、入口和恢复操作必须实际存在" in standard
