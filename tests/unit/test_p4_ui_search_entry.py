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


def test_scope_entry_carries_query_and_executes_once_without_fallback() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the search-scope handoff check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
class Element { constructor(){this.value="";this.textContent="";this.hidden=false;this.children=[];this.handlers={};this.disabled=false;}
  addEventListener(name,fn){this.handlers[name]=fn;} appendChild(child){this.children.push(child);return child;}
  removeChild(child){this.children.splice(this.children.indexOf(child),1);return child;} get firstChild(){return this.children[0]||null;}
  focus(){this.focused=true;} fire(name){if(this.handlers[name])this.handlers[name]({preventDefault(){}});}
}
const stored=new Map(),els=new Map(),get=id=>{if(!els.has(id))els.set(id,new Element());return els.get(id);};
const location={href:"",search:"",pathname:"/search",hash:""};
const context={URLSearchParams,encodeURIComponent,isFinite,console,URL:{revokeObjectURL(){}},
 document:{readyState:"loading",cookie:"",addEventListener(){},getElementById:get,createElement:()=>new Element()},
 window:{crypto:{randomUUID:()=>"12345678-1234-1234-1234-123456789012"},location,
  history:{state:null,replaceState(_state,_title,url){location.search=url.includes("?")?url.slice(url.indexOf("?"),url.indexOf("#")<0?undefined:url.indexOf("#")):"";}},
  sessionStorage:{setItem(k,v){stored.set(k,String(v));},getItem(k){return stored.get(k)||null;},removeItem(k){stored.delete(k);}},
  SearchScopeEntry:{validScope(scope){return Boolean(scope&&scope.kind==="person"&&Number(scope.person_id)>0);}}}};
let helper=fs.readFileSync("src/photo_search/web/search-scope-entry.js","utf8");
vm.createContext(context);vm.runInContext(helper,context);
const button=new Element(),input=new Element();input.value="  海边  ";let current={kind:"person",person_id:8,name:"阿姨"};
context.window.SearchScopeEntry.configure({button,input,getScope:()=>current})(current,"相册");button.fire("click");
assert.equal(location.href,"/search?scope_entry=12345678-1234-1234-1234-123456789012");
const key="aisearch.search-scope.v1:12345678-1234-1234-1234-123456789012";
assert.deepEqual(JSON.parse(stored.get(key)),{version:2,scope:current,query:"海边",execute:true});
current=null;button.fire("click");assert.equal(button.hidden,true,"a stale scope is hidden when its click-time identity is no longer valid");

const code=fs.readFileSync("src/photo_search/web/search.js","utf8").replace(
 '  if (document.readyState === "loading")',
 '  globalThis.ui={restoreOrStartSearch,renderIntentChips,setEls(value){els=value;},setSubmit(fn){submitSearch=fn;},getState(){return state;}};\n  if (document.readyState === "loading")');
vm.runInContext(code,context);const ui=context.ui;
const searchEls={queryText:new Element(),querySource:new Element(),searchSubmit:new Element(),resultIntentScope:new Element(),queryStatus:new Element()};
ui.setEls(searchEls);let calls=0;ui.setSubmit(()=>{calls++;});
stored.set(key,JSON.stringify({version:2,scope:{kind:"person",person_id:8,name:"阿姨"},query:"海边",execute:true}));
location.search="?scope_entry=12345678-1234-1234-1234-123456789012";ui.restoreOrStartSearch();
assert.equal(calls,1,"a nonempty scope handoff executes exactly once");assert.equal(searchEls.queryText.value,"海边");
stored.set(key,JSON.stringify({version:2,scope:{kind:"person",person_id:8,name:"阿姨"},query:"",execute:true}));
location.search="?scope_entry=12345678-1234-1234-1234-123456789012";ui.restoreOrStartSearch();
assert.equal(calls,2,"an explicit empty-query handoff still executes exactly once");assert.equal(searchEls.queryText.value,"");
location.search="?scope_entry=12345678-1234-1234-1234-123456789012&q=海边";ui.restoreOrStartSearch();
assert.equal(calls,2,"a missing token never falls back to the query as a full-library search");
assert.match(searchEls.queryStatus.textContent,/未执行全库搜索/);

const cancelEls={queryText:new Element(),querySource:new Element(),searchSubmit:new Element(),queryStatus:new Element(),
 resultIntentScope:new Element(),resultPlaceScope:new Element(),resultGrid:new Element(),loadMore:new Element(),
 queryActions:new Element(),candidateTimeToggle:new Element(),resultNotices:new Element()};
ui.setEls(cancelEls);let cancelSearches=0;ui.setSubmit(()=>cancelSearches++);
const search=ui.getState().search;search.selectedScope={kind:"place",place_kind:"city",place_key:"geo:tokyo",name:"东京"};
search.placeAware={place_kind:"city",place_key:"geo:tokyo",content_query:""};
search.intentPlan={place_kind:"city",place_key:"geo:tokyo",original_query:"",content_query:""};
search.hits=[{asset_id:1}];search.requestGeneration=10;search.loading=false;search.sessionId="old";search.cursor="old";
ui.renderIntentChips(null);assert.equal(cancelEls.resultIntentScope.children.length,2);
cancelEls.resultIntentScope.children[1].fire("click");
assert.equal(search.selectedScope,null);assert.equal(search.placeAware,null,"cancel removes the echoed duplicate place condition");
assert.equal(search.intentPlan.place_kind,null,"cancel removes the same place echoed in the intent plan");
assert.equal(cancelEls.resultIntentScope.hidden,true,"no place chip remains after cancelling its matching fixed scope");
assert.equal(cancelEls.searchSubmit.textContent,"全库搜索");assert.equal(search.hits.length,0);assert.equal(search.sessionId,null);
assert.equal(search.requestGeneration,11);assert.equal(cancelSearches,0,"cancelling scope does not trigger an unscoped search");
assert.match(cancelEls.queryStatus.textContent,/点击全库搜索重新查询/);

search.selectedScope={kind:"person",person_id:8,name:"阿姨"};
search.intentPlan={person_ids:[4,8,9],person_names:["叔叔","阿姨","表姐"],person_match:"all",
 person_match_before_fixed_scope:"any",content_query:"海边"};
ui.renderIntentChips(search.intentPlan);cancelEls.resultIntentScope.children[1].fire("click");
assert.deepEqual(search.intentPlan.person_ids,[4,9],"cancelling person scope preserves other explicit person IDs");
assert.deepEqual(search.intentPlan.person_names,["叔叔","表姐"]);
assert.equal(search.intentPlan.person_match,"any","cancelling scope restores the parser's original match mode");
assert.equal(search.intentPlan.person_match_before_fixed_scope,undefined);

search.selectedScope={kind:"collection",collection_id:3,instance_token:"a".repeat(32),revision:2,name:"旅行"};
search.intentPlan={collection_scope:{collection_id:3,instance_token:"a".repeat(32),revision:2,name:"旅行"},content_query:""};
ui.renderIntentChips(search.intentPlan);cancelEls.resultIntentScope.children[1].fire("click");
assert.equal(search.intentPlan.collection_scope,null,"cancelling only clears the matching collection instance revision");

search.selectedScope={kind:"source",source_id:5,name:"SSD"};search.intentPlan={source_id:5,source_name:"SSD",content_query:""};
ui.renderIntentChips(search.intentPlan);cancelEls.resultIntentScope.children[1].fire("click");
assert.equal(search.intentPlan.source_id,null,"cancelling source scope removes its echoed source filter");
assert.equal(cancelEls.querySource.value,"");
"""
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10
    )
    assert result.returncode == 0, result.stderr
