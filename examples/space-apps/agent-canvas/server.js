#!/usr/bin/env node
/* 共享画板 · Shared Canvas — one canvas for the whole space. Ask any agent in a sentence to add
 * something ("a red kite top left"); it answers with an SVG layer that is stacked onto the canvas.
 * People can undo their own requests. The canvas is composed in the page.
 *
 *   node examples/space-apps/agent-canvas/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, safeSvg } = require('../../../sdk/js/space-app.js');

const LAYERS = 40;

const PAGE = page({
  title: '共享画板',
  style: `#canvas{position:relative;width:min(100%,600px);aspect-ratio:3/2;margin:8px auto;background:#fffdf7;border-radius:16px;overflow:hidden;border:1px solid var(--line)}
#canvas img{position:absolute;inset:0;width:100%;height:100%}.layer{display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0}`,
  body: `<h1>共享画板</h1><p class="muted">用一句话请 agent 往同一张画布上添东西。</p><div id="canvas"></div>
<div class="card"><div class="row"><select id="painter"></select><input id="ask" class="grow" placeholder="例如：右上角一轮淡黄的月亮"><button id="paint">请它画</button></div></div>
<div id="layers"></div>`,
  script: `
const {api,watch,act,el,fill,svg}=App,$=id=>document.getElementById(id);
$('paint').onclick=()=>act(()=>{const v=$('ask').value;$('ask').value='';return api('paint',{agent:$('painter').value,ask:v});});
function render(s){
  const agents=s.members.filter(m=>m.agent);
  if($('painter').options.length!==agents.length)fill($('painter'),...agents.map(a=>el('option',{value:a.id},a.name)));
  fill($('canvas'),...s.state.layers.filter(l=>l.svg).map(l=>svg(l.svg,{alt:l.ask})));
  fill($('layers'),...s.state.layers.slice().reverse().map(l=>el('div',{class:'layer'},el('span',{class:'agent'},l.agent),el('span',{class:'grow'},'「'+l.ask+'」 · '+l.by+(l.svg?'':l.error?' · 没画成：'+l.error:' · 正在画…')),
    l.byId===s.me.id||s.owner?el('button',{class:'ghost',onclick:()=>act(()=>api('undo',{id:l.id}))},'撤掉'):null)));
}
watch(render);`
});

function setup(app) {
  const layers = [];
  let next = 1;
  app.page(PAGE);
  app.state(() => ({ layers }));

  app.route('POST', '/paint', (member, body) => {
    const agent = app.member(body.agent);
    const ask = String(body.ask || '').trim().slice(0, 120);
    if (!agent?.agent) throw refuse('选一个 agent');
    if (!ask) throw refuse('说说要画什么');
    if (layers.filter((l) => l.byId === member.id && !l.svg && !l.error).length >= 2) throw refuse('你的前两个请求还在画');
    const layer = { id: next++, agent: agent.name, ask, by: member.name, byId: member.id, svg: '', error: '' };
    layers.push(layer);
    while (layers.length > LAYERS) layers.shift();
    app.changed();
    const others = layers.filter((l) => l.svg).map((l) => `- ${l.agent} 画了「${l.ask}」`).join('\n') || '（画布还是空的）';
    app.askJSON(agent.id, `你在空间「${app.title}」的共享画板上作画。画布 viewBox="0 0 600 400"，背景米白。已经有：\n${others}\n` +
      `${member.name} 请你添上：${ask}\n只画这一样东西，放在合适的位置、和已有内容协调；画布其余部分保持透明（不要画背景），不要文字、脚本、图片或外部资源，6000 字以内。\n` +
      '格式：{"svg":"<svg xmlns=\\"http://www.w3.org/2000/svg\\" viewBox=\\"0 0 600 400\\">…</svg>"}').then((reply) => {
      layer.svg = safeSvg(reply?.svg);
      if (!layer.svg) layer.error = '画出来的不是安全的 SVG';
    }).catch((error) => { layer.error = error.message; }).finally(() => app.changed());
  });

  app.route('POST', '/undo', (member, body) => {
    const index = layers.findIndex((l) => l.id === body.id);
    if (index < 0) throw refuse('没有这一层');
    if (layers[index].byId !== member.id && !member.owner) throw refuse('只能撤掉自己请求的', 403);
    layers.splice(index, 1);
    app.changed();
  });
}

if (require.main === module) run({ name: '共享画板' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '共享画板' };
