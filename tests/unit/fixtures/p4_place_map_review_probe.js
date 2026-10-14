// Review only: execute the current map.js against a tiny deterministic browser stub.
const fs = require('fs');
const vm = require('vm');
function setup(withMapLibre = true, fetchImpl = null) {
  const elements = new Map();
  function element() { return {textContent:'',hidden:false,disabled:false,children:[],appendChild(n){this.children.push(n);},setAttribute(){},addEventListener(name,fn){this[name]=fn;},remove(){}}; }
  const calls=[];
  const nodes=[];
  let west=119;
  const map={getCenter(){return {toArray:()=>[120,30]};},getBounds(){return {getWest:()=>west,getEast:()=>122,getSouth:()=>29,getNorth:()=>32};},getZoom(){return 16;},getMaxZoom(){return 19;},getSource(){return {setData(){},getClusterExpansionZoom(){return Promise.resolve(17);},getClusterLeaves(){calls.push('leaves');return Promise.resolve([{properties:{asset_id:17}},{properties:{asset_id:18}}]);}};},getLayer(){return {};},removeLayer(){},removeSource(){},addSource(){},addLayer(){},once(){},queryRenderedFeatures(){return [{properties:{asset_count:2,cluster_id:1},geometry:{coordinates:[120,30]}}];},easeTo(args){calls.push({easeTo:args});}};
  const context={URLSearchParams,Promise,Math,JSON,Error,Array,String,document:{readyState:'loading',addEventListener(){},getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement(){const n=element();nodes.push(n);return n;},querySelectorAll(){return [];}}
    ,window:{scrollY:0},sessionStorage:{getItem(){return null;},setItem(){}},location:{search:''},setTimeout(){},clearTimeout(){},fetch(url){calls.push(url);if(fetchImpl)return fetchImpl(url);const x=url.includes('/points')?{mode:'photos',member_version:1,total:1,features:[]}:{member_version:2,total:0,assets:[],next_cursor:'next'};return Promise.resolve({ok:true,json:()=>Promise.resolve(x)});}};
  if(withMapLibre)context.window.maplibregl=context.maplibregl={Marker:class{setLngLat(){return this;}addTo(){return this;}}};
  context.globalThis=context;
  vm.createContext(context);
  let src=fs.readFileSync('src/photo_search/web/map.js','utf8');
  const end=src.lastIndexOf('})();');
  src=src.slice(0,end)+';globalThis.review={refresh,startMap,labels,schedule,loadSettingsUi,setMap(m){map=m;},setRuntimeConfig(value){runtimeConfig=value;},state(){return {version,gridAssets,runtimeConfig};}};'+src.slice(end);
  vm.runInContext(src,context);
  context.review.setMap(map);
  return {context,calls,elements,nodes,map,setWest(value){west=value;}};
}
async function flush(){for(let i=0;i<10;i++)await Promise.resolve();}
(async()=>{
  const result={};
  const mixed=setup();mixed.context.review.refresh();await flush();
  result.mixed={title:mixed.elements.get('grid-title').textContent,grid:mixed.context.review.state().gridAssets.length,accepted_version:mixed.context.review.state().version,requests:mixed.calls.filter(x=>typeof x==='string').length};
  const missing=setup(false);missing.context.review.startMap();await flush();
  const beforeMore=missing.calls.length;missing.elements.get('more').onclick();await flush();
  result.missing={requests:beforeMore,more_requests:missing.calls.length-beforeMore,notice:missing.elements.get('notice').textContent};
  let runtimeCalls=0;
  const recover=setup(true,url=>{
    if(url.endsWith('/provider-settings'))return Promise.resolve({ok:true,json:()=>Promise.resolve({status:'CONFIGURED',configured:true,default_layer:'vector'})});
    if(url.endsWith('/provider-runtime')){runtimeCalls++;return Promise.resolve({ok:true,json:()=>Promise.resolve({status:'CONFIGURED',configured:true,key:'saved-runtime-key',default_layer:'vector'})});}
    return Promise.resolve({ok:true,json:()=>Promise.resolve({})});
  });
  recover.context.review.setMap(null);recover.context.review.setRuntimeConfig({status:'RUNTIME_UNAVAILABLE',default_layer:'vector'});
  await recover.context.review.loadSettingsUi();
  result.runtimeRecovery={runtimeCalls,status:recover.context.review.state().runtimeConfig.status,key:recover.context.review.state().runtimeConfig.key};
  const identical=setup();identical.context.review.labels({mode:'photos'});identical.nodes[0].click({stopPropagation(){}});await flush();
  const memberButtons=identical.elements.get('grid').children;const memberCount=memberButtons.length;const detailHrefs=[];for(const tile of memberButtons){tile.onclick();detailHrefs.push(identical.context.location.href)}
  result.identical={leaves_calls:identical.calls.filter(x=>x==='leaves').length,members:memberCount,detailHrefs};
  const pending=[];const race=setup(true,url=>new Promise(resolve=>pending.push({url,resolve})));
  race.context.review.refresh();race.setWest(120);race.context.review.schedule();race.context.review.refresh();
  const body=(url,v)=>url.includes('/points')?{mode:'photos',member_version:v,total:v,features:[]}:{member_version:v,total:v,assets:[],next_cursor:null};
  for(const x of pending.filter(x=>x.url.includes('west=120')))x.resolve({ok:true,json:()=>Promise.resolve(body(x.url,2))});await flush();
  for(const x of pending.filter(x=>x.url.includes('west=119')))x.resolve({ok:true,json:()=>Promise.resolve(body(x.url,1))});await flush();
  result.race={version:race.context.review.state().version,title:race.elements.get('grid-title').textContent,requests:pending.length};
  process.stdout.write(JSON.stringify(result));
})();
