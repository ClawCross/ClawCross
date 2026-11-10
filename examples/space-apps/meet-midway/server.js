#!/usr/bin/env node
/* 约饭地图 · Meet Midway — everyone drops their own pin; the others only see it blurred to about a
 * kilometre. Say what you feel like ("hotpot, about 100 a head") and every agent proposes places
 * near the middle; the app works out how far each one is for the farthest person and on average,
 * and everyone votes. Map, search and drawing run in the page (OpenStreetMap / Nominatim).
 * 简介：各自标出大概位置，agent 在大家中间推荐聚会的地方。
 *
 *   node examples/space-apps/meet-midway/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, MAP_HEAD } = require('../../../sdk/js/space-app.js');

const AGENTS = 6;
const RADIUS_KM = 30;
const blur = (x) => Math.round(x * 100) / 100;

function km(a, b) {
  const rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

const PAGE = page({
  appId: 'meet-midway',
  title: '约饭地图',
  head: MAP_HEAD,
  style: `#map{height:44vh;min-height:260px;border-radius:14px;background:var(--card)}#found button{margin:3px 4px 0 0}
.place{display:flex;gap:8px;align-items:flex-start;padding:6px 0;border-bottom:1px solid var(--line)}.dot{width:10px;height:10px;border-radius:50%;margin-top:7px;flex:none}
.top{color:var(--good);font-size:12px;font-weight:600}`,
  body: `<h1>约饭地图</h1><p class="muted">每人标出自己大概在哪（别人只看到约 1 公里的模糊范围），agent 在大家中间找地方，一起投票。地点由 agent 提供，出发前请核实。</p>
<div id="map"></div>
<div class="card"><div id="me" class="muted">先标出你的位置：搜索一个地方，或在地图上点一下。</div>
<div class="row" style="margin-top:8px"><input id="q" class="grow" placeholder="搜索你附近的地点，例如：静安寺"><button id="find" class="ghost">搜索</button></div><div id="found"></div></div>
<div id="people" class="row"></div>
<div class="card"><div class="row"><input id="want" class="grow" placeholder="想去哪，例如：火锅，人均 100，能坐 6 个人"><button id="ask">让 agent 找</button></div><div id="middle" class="muted"></div></div>
<div id="plans"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
const COLORS=['#0071e3','#e8590c','#2b8a3e','#ae3ec9','#c92a2a','#1098ad'];
let map=null,layer=null,snapshot=null,middleKey='';
if(window.L){
  // Points stay WGS-84; SpaceMap places them on the map (高德 in China) and reads clicks back.
  map=SpaceMap.tiles(L.map('map').setView(SpaceMap.show(31.2304,121.4737),11));
  layer=L.layerGroup().addTo(map);
  map.on('click',e=>{const [lat,lng]=SpaceMap.data(e.latlng.lat,e.latlng.lng);act(()=>api('where',{lat,lng,label:'地图上选的点'}));});
  map.on('spacemapchange',()=>{if(snapshot)render(snapshot);});
}else $('map').textContent='地图加载失败，仍可以搜索和查看结果。';
$('find').onclick=()=>act(async()=>{
  const q=$('q').value.trim();if(!q)return;
  const rows=await SpaceMap.search(q);
  fill($('found'),...rows.map(r=>el('button',{class:'ghost',onclick:()=>act(async()=>{await api('where',{lat:+r.lat,lng:+r.lon,label:r.display_name.split(',')[0]});fill($('found'));})},r.display_name.split(',').slice(0,3).join(','))),
    rows.length?null:'没有找到。');
});
$('ask').onclick=()=>act(()=>{const v=$('want').value;$('want').value='';return api('plan',{want:v});});
/** Names the area around the middle, from the page (Nominatim reverse geocoding). */
async function nameMiddle(m){
  const key=m.lat.toFixed(3)+','+m.lng.toFixed(3);if(key===middleKey)return;middleKey=key;
  $('middle').textContent='大家的中点：'+key;
  try{const r=await SpaceMap.reverse(m.lat,m.lng);
    if(r.display_name&&key===middleKey)$('middle').textContent='大家的中点：'+r.display_name.split(',').slice(0,2).join(',');}catch{}
}
function render(s){
  snapshot=s;const w=s.state,agents=s.members.filter(m=>m.agent);
  const color=id=>COLORS[Math.max(0,agents.findIndex(a=>a.id===id))%COLORS.length];
  $('me').textContent=w.mine?'你的位置：'+w.mine.label+'（只有你自己看得到精确位置）':'先标出你的位置：搜索一个地方，或在地图上点一下。';
  fill($('people'),...w.people.map(p=>el('span',{class:'chip'},(p.me?'📍 ':'◌ ')+p.name)),w.people.length?null:el('span',{class:'muted'},'还没有人标位置'));
  if(w.middle)nameMiddle(w.middle);else $('middle').textContent='至少一个人标了位置，才有中点。';
  const plans=w.plans.slice().reverse();
  fill($('plans'),...plans.map((p,i)=>el('div',{class:'card'},el('div',{class:'row'},el('strong',{class:'grow'},p.want),el('span',{class:'muted'},p.by)),
    ...p.asked.filter(a=>a.status!=='done').map(a=>el('div',{class:'muted'},a.name+(a.status==='asking'?' 正在找…':' 没找到：'+a.error))),
    ...p.places.map(x=>el('div',{class:'place'},el('span',{class:'dot',style:'background:'+color(x.agent)}),
      el('div',{class:'grow'},el('div',{},el('strong',{},x.name),' ',el('span',{class:'agent',style:'color:'+color(x.agent)},x.agentName),x.top?el('span',{class:'top'},' 最受欢迎'):null),
        el('div',{class:'muted'},'最远 '+x.far+' 公里 · 平均 '+x.avg+' 公里'+(x.why?' · '+x.why:''))),
      el('button',{class:x.voted?'':'ghost',onclick:()=>act(()=>api('vote',{plan:p.id,place:x.id}))},'👍 '+x.votes))))));
  if(!layer)return;
  layer.clearLayers();const bounds=[];
  for(const p of w.people){const at=SpaceMap.show(p.lat,p.lng);bounds.push(at);
    (p.me?L.circleMarker(at,{radius:7,color:'#0071e3',fillOpacity:.9}):L.circle(at,{radius:900,color:'#888',fillOpacity:.15})).bindTooltip(el('span',{},p.name)).addTo(layer);}
  if(w.middle){const at=SpaceMap.show(w.middle.lat,w.middle.lng);L.circleMarker(at,{radius:10,color:'#2b8a3e',weight:3,fillColor:'#fff',fillOpacity:1}).bindTooltip(el('span',{},'中点')).addTo(layer);bounds.push(at);}
  for(const x of plans[0]?.places||[]){L.circleMarker(SpaceMap.show(x.lat,x.lng),{radius:8,color:color(x.agent),fillOpacity:.8}).bindPopup(el('div',{},el('strong',{},x.name),el('div',{},x.agentName))).addTo(layer);bounds.push(SpaceMap.show(x.lat,x.lng));}
  if(bounds.length)map.fitBounds(bounds,{padding:[30,30],maxZoom:15});
}
watch(render);`
});

function setup(app) {
  const where = new Map();   // person -> {lat, lng, label}; exact, never sent to anyone else
  const plans = [];          // {id, want, by, asked: [{id, name, status, error}], places: [{id, agent, agentName, name, lat, lng, why, far, avg, votes:Set}]}
  let next = 1;
  app.page(PAGE);

  const middle = () => {
    const points = [...where.values()];
    return points.length ? { lat: points.reduce((t, p) => t + p.lat, 0) / points.length, lng: points.reduce((t, p) => t + p.lng, 0) / points.length } : null;
  };

  app.state((member) => ({
    mine: where.get(member.id) || null,
    people: [...where].map(([id, p]) => id === member.id
      ? { name: app.member(id)?.name || '我', me: true, lat: p.lat, lng: p.lng }
      : { name: app.member(id)?.name || '有人', me: false, lat: blur(p.lat), lng: blur(p.lng) }),
    middle: middle(),
    plans: plans.map((p) => {
      const best = Math.max(0, ...p.places.map((x) => x.votes.size));
      return { ...p, places: p.places.map((x) => ({ ...x, votes: x.votes.size, voted: x.votes.has(member.id), top: best > 0 && x.votes.size === best })) };
    }),
  }));

  app.route('POST', '/where', (member, body) => {
    const lat = Number(body.lat), lng = Number(body.lng);
    if (!(Math.abs(lat) <= 90 && Math.abs(lng) <= 180)) throw refuse('位置不对');
    where.set(member.id, { lat, lng, label: String(body.label || '').slice(0, 60) || '已定位' });
    app.changed();
  });

  app.route('POST', '/plan', (member, body) => {
    const want = String(body.want || '').trim().slice(0, 120);
    const center = middle();
    if (!want) throw refuse('说说想去哪');
    if (!center) throw refuse('先标出位置');
    if (plans.some((p) => p.asked.some((a) => a.status === 'asking'))) throw refuse('上一次还在找');
    const agents = app.agents().filter((a) => a.online !== false).slice(0, AGENTS);
    if (!agents.length) throw refuse('空间里没有在线的 agent');
    const plan = { id: String(next++), want, by: member.name, asked: agents.map((a) => ({ id: a.id, name: a.name, status: 'asking', error: '' })), places: [] };
    plans.push(plan);
    if (plans.length > 5) plans.shift();
    app.changed();
    const people = [...where].map(([id, p]) => `${app.member(id)?.name || '有人'}（约 ${blur(p.lat)}, ${blur(p.lng)}）`).join('、');
    const points = [...where.values()];
    for (const asked of plan.asked) {
      app.askJSON(asked.id, `你在空间「${app.title}」帮大家找聚会的地方。要求：${want}\n大家的大概位置：${people}\n中点：${center.lat.toFixed(4)}, ${center.lng.toFixed(4)}\n` +
        `在中点附近（${RADIUS_KM} 公里内，越公平越好）推荐最多 3 个真实存在的地方；能联网或查地图就先查一下，坐标要尽量准。\n` +
        '格式：{"places":[{"name":"店名","lat":纬度,"lng":经度,"why":"一句话理由"}]}')
        .then((reply) => {
          for (const x of (Array.isArray(reply?.places) ? reply.places : []).slice(0, 3)) {
            const at = { lat: Number(x?.lat), lng: Number(x?.lng) };
            if (!Number.isFinite(at.lat) || !Number.isFinite(at.lng) || km(at, center) > RADIUS_KM) continue;
            const far = Math.max(...points.map((p) => km(p, at))), avg = points.reduce((t, p) => t + km(p, at), 0) / points.length;
            plan.places.push({ id: String(plan.places.length + 1), agent: asked.id, agentName: asked.name, name: String(x.name || '').slice(0, 60) || '无名小店',
              lat: at.lat, lng: at.lng, why: String(x.why || '').slice(0, 120), far: far.toFixed(1), avg: avg.toFixed(1), votes: new Set() });
          }
          asked.status = plan.places.some((x) => x.agent === asked.id) ? 'done' : 'failed';
          asked.error = asked.status === 'failed' ? '没有给出附近的地方' : '';
        })
        .catch((error) => { Object.assign(asked, { status: 'failed', error: error.message }); })
        .finally(() => app.changed());
    }
  });

  app.route('POST', '/vote', (member, body) => {
    const place = plans.find((p) => p.id === body.plan)?.places.find((x) => x.id === body.place);
    if (!place) throw refuse('没有这个地方');
    place.votes.has(member.id) ? place.votes.delete(member.id) : place.votes.add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '约饭地图' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '约饭地图' };
