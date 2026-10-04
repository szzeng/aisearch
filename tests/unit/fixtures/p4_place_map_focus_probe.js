// Narrow regression probe: reuse the review DOM harness and run real init().
const fsFocus = require('fs');
let focusHarness = fsFocus.readFileSync('tests/unit/fixtures/p4_place_map_review_probe.js', 'utf8').split('(async()=>{')[0];
focusHarness = focusHarness.replace('review={refresh,', 'review={init,refresh,');
focusHarness += `
async function scenario(late) {
  let releaseFocus;
  const body = (x) => ({ok:true,json:()=>Promise.resolve(x)});
  const fixture = setup(true, url => {
    if (url.endsWith('/summary')) return Promise.resolve(body({total:2,member_version:7,bounds:{west:0,east:120,south:0,north:30}}));
    if (url.includes('asset_id=17')) {
      const data=body({member_version:7,total:1,assets:[{asset_id:17,longitude:120,latitude:30}],next_cursor:null});
      return late ? new Promise(resolve => {releaseFocus=()=>resolve(data);}) : Promise.resolve(data);
    }
    return Promise.resolve(body(url.includes('/points') ? {mode:'photos',member_version:7,total:2,features:[]} : {member_version:7,total:2,assets:[],next_cursor:null}));
  });
  const handlers = {}, jumps = [];
  fixture.map.addControl=()=>{};
  fixture.map.on=(name,callback)=>{handlers[name]=callback;};
  fixture.map.jumpTo=(args)=>{jumps.push(args);};
  fixture.context.window.scrollTo=()=>{};
  fixture.context.window.AISEARCH_MAP_CONFIG={tile_url:'synthetic://tiles',attribution:'fixture'};
  fixture.context.location.search='?asset_id=17';
  fixture.context.maplibregl.Map=class{constructor(){return fixture.map;}};
  fixture.context.maplibregl.NavigationControl=class{};
  fixture.context.review.init();await flush();
  if(late){handlers.load();await flush();releaseFocus();await flush();}
  else {handlers.load();await flush();}
  return {jump_count:jumps.length,version:fixture.context.review.state().version,requests:fixture.calls};
}
(async()=>{process.stdout.write(JSON.stringify({early:await scenario(false),late:await scenario(true)}));})();
`;
eval(focusHarness);
