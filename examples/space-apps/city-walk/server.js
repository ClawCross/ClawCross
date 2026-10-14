#!/usr/bin/env node
/* 城市漫步 · City Walk — pick a starting point, how long you have and what you like; every agent
 * plans a walking route of a few stops. Routes are drawn in each agent's colour; the app works out
 * the walking distance and time and flags routes that will not fit. People vote, and open the
 * winner as directions in Google Maps. The map runs in the page (OpenStreetMap / Nominatim).
 * 简介：定好起点、时长和兴趣，每个 agent 规划一条步行路线，画在地图上。
 *
 *   node examples/space-apps/city-walk/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, MAP_HEAD } = require('../../../sdk/js/space-app.js');

const DURATIONS = { '2 小时': 120, '半天': 240, '一整天': 480 };
const INTERESTS = ['美食', '咖啡', '建筑', '书店', '公园', '博物馆', '夜景', '小众'];
const STOPS = 6;
const AGENTS = 6;
const WALK_KMH = 4.5;

function km(a, b) {
  const rad = Math.PI / 180, dLat = (b.lat - a.lat) * rad, dLng = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

const PAGE = page({
  title: '城市漫步',
  head: MAP_HEAD,
  style: `#map{height:44vh;min-height:260px;border-radius:14px;background:var(--card)}#found button{margin:3px 4px 0 0}
.route h3{margin:0;font-size:15px}.stop{font-size:14px;padding:2px 0}.stop .why{color:var(--muted);font-size:13px}.over{color:var(--bad)}
.interests label{font-size:13px}`,
  body: `<h1>城市漫步</h1><p class="muted">定好起点、时长和兴趣，每个 agent 规划一条步行路线，画在地图上，大家投票。地点由 agent 提供，出发前请核实。</p>
<div id="map"></div>
<div class="card"><div class="row"><input id="q" class="grow" placeholder="起点，例如：上海 外滩"><button id="find" class="ghost">搜索</button></div><div id="found"></div>
<div id="start" class="muted" style="margin-top:6px">还没有起点：搜索一个地点，或在地图上点一下。</div>
<div class="row interests" id="interests" style="margin-top:6px"></div>
<div class="row" style="margin-top:8px"><select id="duration"></select><input id="extra" class="grow" placeholder="补充要求（可选），例如：带小孩，避开人多的地方"><button id="plan">让 agent 规划</button></div></div>
<div id="routes"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
const COLORS=['#0071e3','#e8590c','#2b8a3e','#ae3ec9','#c92a2a','#1098ad'];
let map=null,layer=null,start=null,ready=false,last=null;
if(window.L){
  // Points stay WGS-84; SpaceMap places them on the map (高德 in China) and reads clicks back.
  map=SpaceMap.tiles(L.map('map').setView(SpaceMap.show(31.2304,121.4737),13));
  layer=L.layerGroup().addTo(map);
  map.on('click',e=>{const [lat,lng]=SpaceMap.data(e.latlng.lat,e.latlng.lng);setStart(lat,lng,'地图上选的点');});
  map.on('spacemapchange',()=>{if(last)render(last);});
}else $('map').textContent='地图加载失败，仍可以搜索和查看结果。';
function setStart(lat,lng,label){start={lat:+lat,lng:+lng,label};$('start').textContent='起点：'+label;if(map)map.setView(SpaceMap.show(start.lat,start.lng),15);}
$('find').onclick=()=>act(async()=>{
  const q=$('q').value.trim();if(!q)return;
  const rows=await SpaceMap.search(q);
  fill($('found'),...rows.map(r=>el('button',{class:'ghost',onclick:()=>{setStart(r.lat,r.lon,r.display_name.split(',').slice(0,2).join(','));fill($('found'));}},r.display_name.split(',').slice(0,3).join(','))),rows.length?null:'没有找到。');
});
$('plan').onclick=()=>act(()=>{
  if(!start)throw new Error('先选一个起点');
  return api('plan',{start,duration:$('duration').value,extra:$('extra').value,interests:[...document.querySelectorAll('#interests input:checked')].map(x=>x.value)});
});
/** Directions through every stop, opened in Google Maps. */
const directions=(s,r)=>'https://www.google.com/maps/dir/'+[s,...r.stops].map(p=>p.lat.toFixed(5)+','+p.lng.toFixed(5)).join('/')+'/data=!4m2!4m1!3e2';
function render(s){
  last=s;
  const w=s.state,agents=s.members.filter(m=>m.agent);
  const color=id=>COLORS[Math.max(0,agents.findIndex(a=>a.id===id))%COLORS.length];
  if(!ready){ready=true;fill($('interests'),...w.interests.map(x=>el('label',{class:'chip'},el('input',{type:'checkbox',value:x}),' '+x)));fill($('duration'),...w.durations.map(d=>el('option',{},d)));}
  const plan=w.plan;
  fill($('routes'),plan?el('div',{class:'muted'},plan.by+' 发起 · 起点 '+plan.start.label+' · '+plan.duration+(plan.interests.length?' · '+plan.interests.join('、'):'')):null,
    ...(plan?plan.routes:[]).map(r=>el('div',{class:'card route'},el('div',{class:'row'},el('h3',{class:'grow',style:'color:'+color(r.agent)},r.name+(r.title?' · '+r.title:'')),
      r.status==='done'?el('button',{class:r.voted?'':'ghost',onclick:()=>act(()=>api('vote',{agent:r.agent}))},'👍 '+r.votes):null),
      r.status==='asking'?el('div',{class:'muted'},'正在规划…'):r.status==='failed'?el('div',{class:'error'},'没规划成：'+r.error):[
        el('div',{class:r.over?'over':'muted'},'步行约 '+r.km+' 公里 · 走路 '+r.walk+' 分钟 + 停留 '+r.stay+' 分钟'+(r.over?' · 可能超时':'')),
        ...r.stops.map((p,i)=>el('div',{class:'stop'},(i+1)+'. '+p.name+(p.minutes?'（'+p.minutes+' 分钟）':''),p.why?el('div',{class:'why'},p.why):null)),
        el('a',{href:directions(plan.start,r),target:'_blank',rel:'noreferrer',class:'chip'},'在谷歌地图里导航')])));
  if(!layer||!plan)return;
  layer.clearLayers();const origin=SpaceMap.show(plan.start.lat,plan.start.lng),bounds=[origin];
  L.circleMarker(origin,{radius:8,color:'#111',fillOpacity:1}).bindTooltip(el('span',{},'起点')).addTo(layer);
  for(const r of plan.routes.filter(r=>r.status==='done')){
    const path=[origin,...r.stops.map(p=>SpaceMap.show(p.lat,p.lng))];bounds.push(...path);
    L.polyline(path,{color:color(r.agent),weight:4,opacity:.8}).addTo(layer);
    r.stops.forEach((p,i)=>L.circleMarker(SpaceMap.show(p.lat,p.lng),{radius:6,color:color(r.agent),fillOpacity:.9}).bindTooltip(el('span',{},(i+1)+'. '+p.name)).addTo(layer));
  }
  map.fitBounds(bounds,{padding:[30,30],maxZoom:16});
}
watch(render);`
});

function setup(app) {
  // plan: {by, start, duration, interests, extra, routes: [{agent, name, status, error, title, stops, km, walk, stay, over, votes: Set}]}
  let plan = null;
  app.page(PAGE);

  app.state((member) => ({
    durations: Object.keys(DURATIONS), interests: INTERESTS,
    plan: plan && { ...plan, routes: plan.routes.map((r) => ({ ...r, votes: r.votes.size, voted: r.votes.has(member.id) })) },
  }));

  app.route('POST', '/plan', (member, body) => {
    const start = { lat: Number(body.start?.lat), lng: Number(body.start?.lng), label: String(body.start?.label || '起点').slice(0, 60) };
    if (!(Math.abs(start.lat) <= 90 && Math.abs(start.lng) <= 180)) throw refuse('先选一个起点');
    if (plan?.routes.some((r) => r.status === 'asking')) throw refuse('上一次还在规划');
    const duration = DURATIONS[body.duration] ? body.duration : '半天';
    const interests = (Array.isArray(body.interests) ? body.interests : []).filter((x) => INTERESTS.includes(x));
    const extra = String(body.extra || '').trim().slice(0, 120);
    const agents = app.agents().filter((a) => a.online !== false).slice(0, AGENTS);
    if (!agents.length) throw refuse('空间里没有在线的 agent');
    plan = { by: member.name, start, duration, interests, extra,
      routes: agents.map((a) => ({ agent: a.id, name: a.name, status: 'asking', error: '', title: '', stops: [], km: '0', walk: 0, stay: 0, over: false, votes: new Set() })) };
    app.changed();
    const current = plan, minutes = DURATIONS[duration];
    for (const route of plan.routes) {
      app.askJSON(route.agent, `你在空间「${app.title}」帮大家规划一条城市步行路线。起点：${start.label}（${start.lat.toFixed(5)}, ${start.lng.toFixed(5)}）。` +
        `时长：${duration}（约 ${minutes} 分钟，含步行和停留）。兴趣：${interests.join('、') || '不限'}。${extra ? '补充：' + extra + '。' : ''}\n` +
        `安排 3 到 ${STOPS} 个真实存在的地点，按顺序步行可达（相邻两站最好在 2 公里内）；能联网或查地图就先查，坐标要准。\n` +
        '格式：{"title":"路线名","stops":[{"name":"地点","lat":纬度,"lng":经度,"minutes":停留分钟,"why":"一句话"}]}')
        .then((reply) => {
          let previous = start, total = 0;
          const stops = [];
          for (const x of (Array.isArray(reply?.stops) ? reply.stops : []).slice(0, STOPS)) {
            const at = { lat: Number(x?.lat), lng: Number(x?.lng) };
            if (!Number.isFinite(at.lat) || !Number.isFinite(at.lng) || km(previous, at) > 10) continue;   // a stop out of walking range
            total += km(previous, at);
            stops.push({ name: String(x.name || '').slice(0, 60) || '无名地点', ...at, minutes: Math.max(0, Math.min(240, Math.round(Number(x.minutes) || 0))), why: String(x.why || '').slice(0, 120) });
            previous = at;
          }
          if (!stops.length) throw new Error('没有给出步行范围内的地点');
          const walk = Math.round(total / WALK_KMH * 60), stay = stops.reduce((t, p) => t + p.minutes, 0);
          Object.assign(route, { status: 'done', title: String(reply?.title || '').slice(0, 40), stops, km: total.toFixed(1), walk, stay, over: walk + stay > minutes * 1.15 });
        })
        .catch((error) => Object.assign(route, { status: 'failed', error: error.message }))
        .finally(() => { if (plan === current) app.changed(); });
    }
  });

  app.route('POST', '/vote', (member, body) => {
    const route = plan?.routes.find((r) => r.agent === body.agent && r.status === 'done');
    if (!route) throw refuse('没有这条路线');
    route.votes.has(member.id) ? route.votes.delete(member.id) : route.votes.add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '城市漫步' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '城市漫步' };
