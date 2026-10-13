"""Focused shell navigation and deep-link checks for P4-UI-SHELL-A."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src" / "photo_search" / "web"


def test_five_pages_share_the_working_transition_navigation() -> None:
    pages = ("index.html", "search.html", "persons.html", "places.html", "map.html")
    for page in pages:
        document = (WEB / page).read_text(encoding="utf-8")
        assert 'class="app-shell' in document
        assert 'class="app-sidebar"' in document
        assert 'data-shell-page="photos" href="/"' in document
        assert 'data-shell-page="places" href="/places"' in document
        assert 'data-shell-page="management" href="/?view=sources"' in document
        assert 'class="global-search-form' in document
        assert 'data-shell-page="search" href="/search"' not in document
        assert 'id="main"' in document

    assert 'id="nav-persons" href="/persons" hidden' in (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="nav-persons" href="/persons" hidden' in (WEB / "search.html").read_text(encoding="utf-8")
    assert 'id="nav-persons" href="/persons" hidden' in (WEB / "places.html").read_text(encoding="utf-8")
    assert 'id="nav-persons" href="/persons" hidden' in (WEB / "map.html").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert "#main:focus-visible {\n  outline: none;\n}" in styles
    assert ":focus-visible {\n  outline: 3px solid var(--accent);" in styles
    map_script = (WEB / "map.js").read_text(encoding="utf-8")
    map_page = (WEB / "map.html").read_text(encoding="utf-8")
    assert "map.js?v=p4-ui-shell-a-1" in map_page
    assert "map.css?v=p4-ui-shell-a-2" in map_page
    map_styles = (WEB / "map.css").read_text(encoding="utf-8")
    assert ".map-page-main > section::-webkit-scrollbar-track {\n  background: var(--bg);\n}" in map_styles
    assert ".map-page-main > section::-webkit-scrollbar-thumb {" in map_styles
    assert "scrollbar-color: var(--line) var(--bg);" in map_styles
    assert 'req("/api/v1/capabilities/page").then(revealPersonsNav)' in map_script
    assert 'version >= 13 && String(body && body.schema_status) === "ok"' in map_script
    assert 'href="/places"' in (WEB / "map.html").read_text(encoding="utf-8")
    assert 'href="/map"' in (WEB / "places.html").read_text(encoding="utf-8")
    assert 'id="search-form"' in (WEB / "search.html").read_text(encoding="utf-8")
    assert 'id="pa-search-photos"' in (WEB / "persons.html").read_text(encoding="utf-8")


def test_home_route_selection_and_detail_return_preserve_priority_without_requests() -> None:
    node = shutil.which("node")
    assert node is not None, "Node is required for the synthetic route check"
    script = r"""
const fs=require("fs"),vm=require("vm"),assert=require("assert/strict");
const attrs=()=>({values:{},setAttribute(k,v){this.values[k]=String(v);},removeAttribute(k){delete this.values[k];}});
const links=["photos","management"].map(page=>Object.assign(attrs(),{dataset:{shellPage:page}}));
const personsLink=Object.assign(attrs(),{hidden:true});
const location={pathname:"/",search:"",hash:""};
const values=new Map(),requests=[];
const window={location,sessionStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
  requestAnimationFrame:fn=>fn(),scrollTo(){}};
const document={readyState:"loading",addEventListener(){},querySelectorAll:()=>links,getElementById:id=>id==="nav-persons"?personsLink:null};
let code=fs.readFileSync("src/photo_search/web/app.js","utf8");
code=code.replace('  if (document.readyState === "loading")',
  '  globalThis.ui={state,handleHash,showView,revealPersonsNav,setElements(value){els=value;},stubDetail(fn){renderDetail=fn;}};\n  if (document.readyState === "loading")');
const context={document,window,location,history:{replaceState(){}},URLSearchParams,URL,console,fetch:(...args)=>{requests.push(args);}};
vm.createContext(context);vm.runInContext(code,context);
const focusCalls=[];
const sections={sources:{hidden:true},browse:{hidden:true},detail:{hidden:true}},main={focus(options){focusCalls.push(options);}};
const ui=context.ui;ui.setElements({views:sections,navButtons:[],main,assetGrid:{querySelector(){return null;}}});
ui.state.browse.loaded=true;
ui.handleHash();assert.equal(sections.browse.hidden,false,"plain / opens photo browsing");
assert.equal(focusCalls[focusCalls.length-1].preventScroll,true,"route focus preserves the current scroll position");
assert.equal(links[0].values["aria-current"],"page");
location.search="?view=sources";ui.handleHash();assert.equal(sections.sources.hidden,false,"explicit management view opens sources");
assert.equal(links[1].values["aria-current"],"page");
location.search="?view=unknown";ui.handleHash();assert.equal(sections.browse.hidden,false,"unknown view safely falls back to photos");
let opened="";ui.stubDetail(id=>{opened=id;});location.search="?view=sources";location.hash="#/asset/42";ui.handleHash();
assert.equal(opened,"42","a valid asset deep link takes priority over the view query");
ui.state.detailRouteActive=true;ui.state.browse.returnContext={assetId:"42"};ui.state.browse.returnPending=true;ui.state.browse.loaded=true;
location.hash="";ui.handleHash();assert.equal(sections.browse.hidden,false,"detail return restores the browse context first");
ui.revealPersonsNav({schema_version:12,schema_status:"ok"});assert.equal(personsLink.hidden,true);
ui.revealPersonsNav({schema_version:13,schema_status:"ok"});assert.equal(personsLink.hidden,false);
assert.deepEqual(requests,[],"route selection and navigation do not start API or write requests");
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
