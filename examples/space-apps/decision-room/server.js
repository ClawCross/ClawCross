#!/usr/bin/env node
/* 决策会 · Decision Room — a question, a few options and the criteria that matter. Every agent
 * scores each option on each criterion with a reason; each person weighs the criteria on their own
 * page and sees the ranking change live. Scoring is the agents'; weighing is yours.
 *
 *   node examples/space-apps/decision-room/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '决策会',
  style: `.bar{height:22px;border-radius:8px;background:var(--accent);color:#fff;font-size:12px;padding:2px 8px;white-space:nowrap;min-width:fit-content}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:5px;text-align:left}input[type=range]{width:120px}`,
  body: `<h1>决策会</h1><p class="muted">各个 agent 按标准给选项打分，你自己调权重，看排名怎么变。</p>
<div class="card"><input id="question" style="width:100%" placeholder="要决定什么？例如：团队下个季度先做哪个功能">
<input id="options" style="width:100%;margin-top:6px" placeholder="选项，用逗号分开：离线模式, 团队协作, 插件市场">
<input id="criteria" style="width:100%;margin-top:6px" placeholder="标准，用逗号分开：用户价值, 开发成本, 风险" value="价值, 成本, 风险">
<button id="go" style="margin-top:8px">请 agent 打分</button></div><div id="room"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
const weights={};let last=null;
const split=v=>v.split(/[,，、]/).map(x=>x.trim()).filter(Boolean);
$('go').onclick=()=>act(()=>api('ask',{question:$('question').value,options:split($('options').value),criteria:split($('criteria').value)}));
function render(s){
  last=s;const d=s.state.decision;
  if(!d){fill($('room'));return;}
  d.criteria.forEach(c=>{if(!(c in weights))weights[c]=c.includes('成本')||c.includes('风险')?-1:1;});
  const scored=d.scores.filter(x=>x.matrix);
  // Average each option on each criterion across agents, then weigh. Costs and risks weigh negative.
  const mean=(o,c)=>{const v=scored.map(x=>x.matrix[o]?.[c]).filter(n=>typeof n==='number');return v.length?v.reduce((a,n)=>a+n,0)/v.length:0;};
  const total=o=>d.criteria.reduce((sum,c)=>sum+weights[c]*mean(o,c),0);
  const ranked=d.options.map(o=>({o,v:total(o)})).sort((a,b)=>b.v-a.v),max=Math.max(...ranked.map(r=>Math.abs(r.v)),1);
  fill($('room'),el('h2',{},d.question),el('p',{class:'muted'},d.scores.map(x=>x.name+(x.matrix?' ✓':x.error?' ✗':' 打分中…')).join('　')),
    el('div',{class:'card'},el('strong',{},'你的权重（负数表示越低越好）'),...d.criteria.map(c=>el('div',{class:'row'},el('span',{style:'width:90px'},c),
      keep('w-'+c,()=>el('input',{id:'w-'+c,type:'range',min:-3,max:3,step:1,value:weights[c],oninput:e=>{weights[c]=+e.target.value;render(last);}})),el('span',{},String(weights[c]))))),
    el('div',{class:'card'},el('strong',{},'排名'),...ranked.map(r=>el('div',{class:'row',style:'margin:4px 0;flex-wrap:nowrap'},el('span',{style:'width:96px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'},r.o),
      el('div',{style:'flex:1;min-width:0'},el('div',{class:'bar',style:'width:'+Math.max(12,Math.abs(r.v)/max*100)+'%;background:'+(r.v<0?'var(--bad)':'var(--accent)')},r.v.toFixed(1)))))),
    ...scored.map(x=>el('div',{class:'card'},el('strong',{class:'agent'},x.name),el('table',{},el('tr',{},el('th',{},''),...d.criteria.map(c=>el('th',{},c)),el('th',{},'理由')),
      ...d.options.map(o=>el('tr',{},el('td',{},o),...d.criteria.map(c=>el('td',{},String(x.matrix[o]?.[c]??'-'))),el('td',{},x.reasons[o]||'')))))));
}
watch(render);`
});

function setup(app) {
  let decision = null;
  app.page(PAGE);
  app.state(() => ({ decision }));

  app.route('POST', '/ask', (member, body) => {
    const question = String(body.question || '').trim().slice(0, 200);
    const options = [...new Set((body.options || []).map((o) => String(o).trim().slice(0, 40)).filter(Boolean))].slice(0, 6);
    const criteria = [...new Set((body.criteria || []).map((c) => String(c).trim().slice(0, 20)).filter(Boolean))].slice(0, 6);
    if (!question || options.length < 2 || !criteria.length) throw refuse('写下问题、至少两个选项和一个标准');
    if (decision?.scores.some((x) => !x.matrix && !x.error)) throw refuse('上一轮还在打分');
    const agents = app.agents();
    if (!agents.length) throw refuse('空间里还没有 agent');
    decision = { question, options, criteria, by: member.name, scores: agents.map((a) => ({ agent: a.id, name: a.name, matrix: null, reasons: {} })) };
    app.changed();
    for (const x of decision.scores) {
      app.askJSON(x.agent, `你是空间「${app.title}」决策会的顾问「${x.name}」。${member.name} 要决定：${question}\n` +
        `选项：${options.join('、')}\n标准：${criteria.join('、')}\n给每个选项在每个标准上打 1–10 分（成本、风险类标准分越高代表越贵、越危险），每个选项一句理由。\n` +
        `格式：{"scores":{"选项":{"标准":分数}},"reasons":{"选项":"理由"}}`).then((reply) => {
        const score = (value) => (Number.isFinite(Number(value)) && value !== null ? Math.max(1, Math.min(10, Number(value))) : null);   // left out stays empty
        x.matrix = Object.fromEntries(options.map((o) => [o, Object.fromEntries(criteria.map((c) => [c, score(reply?.scores?.[o]?.[c])]))]));
        x.reasons = Object.fromEntries(options.map((o) => [o, String(reply?.reasons?.[o] || '').slice(0, 160)]));
      }).catch((error) => { x.error = error.message; }).finally(() => app.changed());
    }
  });
}

if (require.main === module) run({ name: '决策会' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '决策会' };
