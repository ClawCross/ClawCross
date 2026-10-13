#!/usr/bin/env node
/* 地图侦察 · Map Scout — set a place on the map and send every agent in the space on a mission
 * ("find three quiet cafés near here"). Each agent answers with places, drawn in its own colour;
 * people vote. The map, search (OpenStreetMap / Nominatim) and drawing all run in the page.
 * 简介：在地图上选个地方，派 agent 去找地点，结果标在地图上。
 *
 *   node examples/space-apps/map-scout/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '地图侦察',
  head: '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">' +
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>',
  style: `#map{height:46vh;min-height:260px;border-radius:14px;background:var(--card)}#search-results button{margin:3px 4px 0 0}
.place{display:flex;gap:8px;align-items:flex-start}.dot{width:10px;height:10px;border-radius:50%;margin-top:7px;flex:none}
.place .why{font-size:13px;color:var(--muted)}.vote{padding:3px 10px;font-size:13px}`,
  body: `<h1>地图侦察</h1><p class="muted">选一个地方，给空间里的 agent 下达侦察任务；它们找到的地点会标在地图上。位置由 agent 提供，出发前请核实。</p>
<div id="map"></div>
<div class="row" style="margin-top:10px"><input id="q" class="grow" placeholder="搜索地点，例如：上海 武康路"><button id="find" class="ghost">搜索</button></div>
<div id="search-results"></div>
<div class="card"><div class="muted" id="center">还没有设定中心：搜索一个地点，或在地图上点一下。</div>
<textarea id="task" rows="2" placeholder="侦察任务，例如：步行 15 分钟内，找 3 家适合安静办公的咖啡馆" style="margin-top:8px"></textarea>
<div class="row" id="pick"></div><div class="row" style="margin-top:8px"><button id="send">派出 agent</button><span class="muted">半径</span>
<select id="radius"><option value="1">1 公里</option><option value="3" selected>3 公里</option><option value="10">10 公里</option></select></div></div>
<div id="missions"></div>`,
  script: `
const {api,watch,act,el}=App,$=id=>document.getElementById(id);
const COLORS=['#0071e3','#e8590c','#2b8a3e','#ae3ec9','#c92a2a','#1098ad','#f08c00','#5c7cfa'];
let center=null,snapshot=null,map=null,layer=null,chosen=new Set(),pinned='';
if(window.L){
  map=L.map('map').setView([31.2304,121.4737],12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
  layer=L.layerGroup().addTo(map);
  map.on('click',e=>setCenter(e.latlng.lat,e.latlng.lng,'地图上选的点'));
}else $('map').textContent='地图加载失败，仍可以搜索和查看结果。';
function setCenter(lat,lng,label){center={lat:+lat,lng:+lng,label};pinned='';$('center').textContent='中心：'+label+'（'+center.lat.toFixed(4)+', '+center.lng.toFixed(4)+'）';
  if(map)map.setView([center.lat,center.lng],Math.max(map.getZoom(),14));}
$('find').onclick=()=>act(async()=>{
  const q=$('q').value.trim();if(!q)return;
  const rows=await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=5&q='+encodeURIComponent(q)).then(r=>r.json());
  $('search-results').replaceChildren(...rows.map(r=>el('button',{class:'ghost',onclick:()=>setCenter(r.lat,r.lon,r.display_name.split(',').slice(0,2).join(','))},r.display_name.split(',').slice(0,3).join(','))));
  if(!rows.length)$('search-results').textContent='没有找到。';
});
$('send').onclick=()=>act(async()=>{
  if(!center)throw new Error('先选一个中心点');
  await api('mission',{task:$('task').value,center,radius:+$('radius').value,agents:[...chosen]});$('task').value='';
});
const color=id=>COLORS[(snapshot.members.filter(m=>m.agent).findIndex(m=>m.id===id)+COLORS.length)%COLORS.length];
function render(s){
  snapshot=s;const agents=s.members.filter(m=>m.agent);
  if(!chosen.size)agents.forEach(a=>chosen.add(a.id));
  $('pick').replaceChildren(...agents.map(a=>el('label',{class:'chip'},el('input',{type:'checkbox',checked:chosen.has(a.id),onchange:e=>e.target.checked?chosen.add(a.id):chosen.delete(a.id)}),' ',a.name,a.online?'':'（离线）')));
  if(!center&&s.state.center)setCenter(s.state.center.lat,s.state.center.lng,s.state.center.label);
  if(layer)layer.clearLayers();
  const bounds=[];
  App.fill($('missions'),...s.state.missions.slice().reverse().map((m,index)=>el('div',{class:'card'},
    el('div',{class:'row'},el('strong',{class:'grow'},m.task),el('span',{class:'muted'},m.by+' · '+m.center.label)),
    ...Object.entries(m.results).map(([agent,r])=>el('div',{},el('div',{class:'agent',style:'color:'+color(agent)},r.name,' ',r.status==='asking'?'正在侦察…':r.status==='failed'?'没有完成：'+r.error:''),
      r.note?el('div',{class:'muted pre'},r.note):null,
      ...(r.places||[]).map(p=>{
        const key=m.id+':'+agent+':'+p.name,votes=s.state.votes[key]||[];
        if(layer&&index===0&&isFinite(p.lat)&&isFinite(p.lng)){L.circleMarker([p.lat,p.lng],{radius:8,color:color(agent),fillOpacity:.8}).bindPopup(el('div',{},el('strong',{},r.name+'：'+p.name),el('div',{},p.why))).addTo(layer);bounds.push([p.lat,p.lng]);}
        return el('div',{class:'place'},el('span',{class:'dot',style:'background:'+color(agent)}),el('div',{class:'grow'},el('div',{},p.name),el('div',{class:'why'},p.why)),
          el('button',{class:'ghost vote',onclick:()=>act(()=>api('vote',{key}))},'👍 '+votes.length));
      }))))));
  if(map&&bounds.length&&pinned!==s.state.missions.at(-1)?.id){pinned=s.state.missions.at(-1).id;map.fitBounds(bounds,{padding:[30,30],maxZoom:16});}
}
watch(render);`
});

/** The mission an agent gets, and the JSON it must answer with. */
function brief(app, agent, mission) {
  return `你是空间「${app.title}」里「地图侦察」小程序的 agent「${agent.name}」。\n` +
    `侦察任务（来自 ${mission.by}）：${mission.task}\n` +
    `地图中心：${mission.center.label}（纬度 ${mission.center.lat}，经度 ${mission.center.lng}），范围约 ${mission.radius} 公里。\n` +
    '如果你能联网搜索，请先查证地点是否真实存在、位置是否准确。找出最多 5 个最符合任务的地点，坐标尽量准确。\n' +
    '格式：{"places":[{"name":"地点名","lat":纬度数字,"lng":经度数字,"why":"一句话理由"}],"note":"可选：整体建议或提醒"}';
}

function setup(app) {
  const missions = [];
  const votes = {};
  let center = null;
  app.page(PAGE);
  app.state(() => ({ center, missions, votes: Object.fromEntries(Object.entries(votes).map(([k, v]) => [k, [...v]])) }));

  app.route('POST', '/mission', (member, body) => {
    const task = String(body.task || '').trim().slice(0, 500);
    const c = body.center || {};
    if (!task) throw refuse('写下侦察任务');
    if (!Number.isFinite(c.lat) || !Number.isFinite(c.lng) || Math.abs(c.lat) > 90 || Math.abs(c.lng) > 180) throw refuse('先选一个中心点');
    if (missions.filter((m) => m.byId === member.id && Object.values(m.results).some((r) => r.status === 'asking')).length) throw refuse('你上一个任务还在进行');
    const chosen = app.agents().filter((a) => !body.agents?.length || body.agents.includes(a.id));
    if (!chosen.length) throw refuse('空间里还没有 agent');
    center = { lat: c.lat, lng: c.lng, label: String(c.label || '').slice(0, 120) || '选定的位置' };
    const mission = { id: String(missions.length + 1), task, center, radius: [1, 3, 10].includes(body.radius) ? body.radius : 3,
      by: member.name, byId: member.id, results: {} };
    missions.push(mission);
    if (missions.length > 30) missions.shift();
    for (const agent of chosen) {
      mission.results[agent.id] = { name: agent.name, status: 'asking' };
      app.askJSON(agent.id, brief(app, agent, mission)).then((answer) => {
        const places = (Array.isArray(answer?.places) ? answer.places : []).slice(0, 5)
          .map((p) => ({ name: String(p.name || '').slice(0, 80), lat: Number(p.lat), lng: Number(p.lng), why: String(p.why || '').slice(0, 200) }))
          .filter((p) => p.name && Number.isFinite(p.lat) && Number.isFinite(p.lng));
        mission.results[agent.id] = places.length || answer?.note
          ? { name: agent.name, status: 'done', places, note: String(answer?.note || '').slice(0, 400) }
          : { name: agent.name, status: 'failed', error: '回复里没有可用的地点' };
      }, (error) => { mission.results[agent.id] = { name: agent.name, status: 'failed', error: error.message }; })
        .finally(() => app.changed());
    }
    app.changed();
  });

  app.route('POST', '/vote', (member, body) => {
    const key = String(body.key || '').slice(0, 200);
    if (!key) throw refuse('选一个地点');
    const set = (votes[key] ||= new Set());
    set.has(member.id) ? set.delete(member.id) : set.add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '地图侦察' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '地图侦察' };
