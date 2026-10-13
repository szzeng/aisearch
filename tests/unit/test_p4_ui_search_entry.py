"""Focused checks for the single global search entry and its startup handoff."""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


class SearchEntryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.header_depth = 0
        self.header_search_forms = 0
        self.global_query_inputs = 0
        self.sidebar_search_links = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "header" and "app-header" in values.get("class", "").split():
            self.header_depth += 1
        if tag == "form" and self.header_depth and "global-search-form" in values.get("class", "").split():
            self.header_search_forms += 1
        if tag == "input" and values.get("id") in {"global-search-query", "query-text"}:
            self.global_query_inputs += 1
        if tag == "a" and values.get("data-shell-page") == "search":
            self.sidebar_search_links += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "header" and self.header_depth:
            self.header_depth -= 1


def test_all_pages_have_one_header_search_and_no_duplicate_sidebar_entry() -> None:
    for filename in ("index.html", "search.html", "persons.html", "places.html", "map.html"):
        parser = SearchEntryParser()
        parser.feed((WEB / filename).read_text(encoding="utf-8"))
        assert parser.header_search_forms == 1, filename
        assert parser.global_query_inputs == 1, filename
        assert parser.sidebar_search_links == 0, filename

    shared_css = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".sr-only" in shared_css
    search_html = (WEB / "search.html").read_text(encoding="utf-8")
    assert search_html.count('id="search-form"') == 1
    assert search_html.count('id="query-text"') == 1
    assert 'name="q"' in search_html
    assert 'id="query-apply-filters"' in search_html
    assert "应用高级筛选" in search_html
    assert '<form id="upload-form"' in search_html
    assert 'id="pa-search-photos"' in (WEB / "persons.html").read_text(encoding="utf-8")


def test_startup_query_precedence_empty_focus_and_ime_submit_guard() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-entry interaction check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element { constructor(){this.value="";this.handlers={};this.focused=0;this.hidden=false;this.disabled=false;}
  addEventListener(name,fn){this.handlers[name]=fn;}
  focus(){this.focused++;}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  fire(name,event={}){this.handlers[name](Object.assign({preventDefault(){this.prevented=true;}},event));}
}
const store=new Map([["aisearch.search-return.v1","old"]]);
const location={search:"?keep=1&q=%E5%BC%A0%E4%B8%89%E5%B9%B4%E7%85%A7%E7%89%87",pathname:"/search",hash:"#results"};
const replacements=[];
const context={URLSearchParams,encodeURIComponent,isFinite,console,URL:{revokeObjectURL(){}},
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById(){return new Element();},createElement(){return new Element();}},
 window:{location,history:{state:{k:1},replaceState(...args){replacements.push(args);}},sessionStorage:{getItem(k){return store.get(k)||null;},removeItem(k){store.delete(k);},setItem(k,v){store.set(k,String(v));}}}};
let code=fs.readFileSync("src/photo_search/web/search.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={consumeEntryQuery,restoreOrStartSearch,bindQuerySubmission,resetFilters,setEls(value){els=value;},getState(){return state;},setState(value){state=value;},setSubmit(fn){submitSearch=fn;},setRestore(fn){restoreSearchSnapshot=fn;}};\n  if (document.readyState === "loading")');
vm.createContext(context);vm.runInContext(code,context);
const ui=context.ui,query=new Element(),form=new Element(),sent=[];ui.setEls({queryText:query,searchForm:form});
ui.setSubmit(()=>sent.push(query.value));ui.setRestore(()=>sent.push("restored"));
ui.restoreOrStartSearch();
assert.equal(query.value,"张三年照片");assert.deepEqual(sent,["张三年照片"]);
assert.equal(store.has("aisearch.search-return.v1"),false,"explicit new q wins over the prior snapshot");
assert.equal(replacements.length,1);assert.equal(replacements[0][2],"/search?keep=1#results");

location.search="?q=";sent.length=0;ui.restoreOrStartSearch();
assert.deepEqual(sent,[]);assert.equal(query.focused,1,"empty global search only focuses the existing input");
location.search="";ui.restoreOrStartSearch();assert.deepEqual(sent,["restored"]);

let submits=0;ui.setSubmit(()=>submits++);ui.bindQuerySubmission();
query.fire("compositionstart");let prevented=false;query.fire("keydown",{key:"Enter",preventDefault(){prevented=true;}});
assert.equal(prevented,true);form.fire("submit",{preventDefault(){}});
assert.equal(submits,1,"form submit event is wired once");
query.fire("compositionend");prevented=false;query.fire("keydown",{key:"Enter",preventDefault(){prevented=true;}});
assert.equal(prevented,false,"Enter after composition can submit normally");

const filters={queryText:query,querySource:new Element(),queryAvailability:new Element(),queryPlaceState:new Element(),
 queryPlaceIds:new Element(),queryDescendants:new Element(),queryCaptureFrom:new Element(),queryCaptureTo:new Element(),
 queryPageSize:new Element(),queryPersonMatch:new Element(),personOptions:new Element(),personEcho:new Element(),
 personSelection:new Element(),personClear:new Element(),personReload:new Element(),personMore:new Element(),personStatus:new Element(),
 captureState:new Element(),captureEcho:new Element(),queryStatus:new Element(),resultGrid:new Element(),resultPlaceScope:new Element(),resultIntentScope:new Element(),loadMore:new Element(),queryActions:new Element(),resultNotices:new Element()};
ui.setEls(filters);query.value="保留我的搜索词";filters.querySource.value="4";filters.queryAvailability.value="missing";
let advancedClearSubmits=0;ui.setSubmit(()=>advancedClearSubmits++);ui.resetFilters();
assert.equal(query.value,"保留我的搜索词","clearing advanced filters preserves the global query");
assert.equal(filters.querySource.value,"");assert.equal(filters.queryAvailability.value,"any");
assert.equal(advancedClearSubmits,1,"the existing query is rebuilt once after clearing manual filters");
query.value="";ui.setState({person:{selected:{},enabled:false},search:{mode:"text",intentPlan:null,placeAware:null,requestGeneration:7,loading:true,hits:[{}],lastBody:{},sessionId:"old",cursor:"old",binding:"old"}});ui.resetFilters();
assert.equal(ui.getState().search.requestGeneration,8,"clearing an empty query invalidates in-flight results");
assert.equal(ui.getState().search.loading,false);
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
