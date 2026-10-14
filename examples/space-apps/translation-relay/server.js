#!/usr/bin/env node
/* 翻译接力 · Translation Relay — a sentence travels through the agents, each translating it into
 * the next language of the chain, until the last one brings it back into Chinese. See how far the
 * meaning drifted, and let an agent explain where it went wrong.
 * 简介：一句话经过一串语言的翻译再回到中文，看意思跑偏了多少。
 *
 *   node examples/space-apps/translation-relay/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const LANGS = ['英语', '日语', '法语', '德语', '俄语', '韩语', '西班牙语', '阿拉伯语', '文言文', '粤语'];

const PAGE = page({
  appId: 'translation-relay',
  title: '翻译接力',
  style: `.hop{border-left:3px solid var(--accent);padding:4px 10px;margin:8px 0}.final{font-size:18px;font-weight:600}`,
  body: `<h1>翻译接力</h1><p class="muted">一句话在 agent 之间按语言接力翻译，最后译回中文，看意思还剩多少。</p>
<div class="card"><textarea id="text" rows="2" placeholder="起点句子，例如：塞翁失马，焉知非福"></textarea><div id="langs" class="row" style="margin:6px 0"></div>
<button id="go">开始接力</button></div><div id="relay"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
const LANGS=${JSON.stringify(LANGS)},chosen=new Set(['英语','日语','法语']);
fill($('langs'),...LANGS.map(l=>el('label',{class:'chip'},el('input',{type:'checkbox',checked:chosen.has(l),onchange:e=>e.target.checked?chosen.add(l):chosen.delete(l)}),' '+l)));
$('go').onclick=()=>act(()=>api('relay',{text:$('text').value,langs:LANGS.filter(l=>chosen.has(l))}));
function render(s){
  fill($('relay'),...s.state.relays.slice().reverse().map(r=>el('div',{class:'card'},el('div',{class:'muted'},r.by+' 发起'),el('div',{class:'hop'},'中文（原句）：'+r.text),
    ...r.hops.map(h=>el('div',{class:'hop'},el('span',{class:'agent'},h.agent),' → '+h.lang+'：',h.text||el('span',{class:'muted'},'翻译中…'))),
    r.verdict?el('div',{},el('div',{class:'final'},'回到中文：'+r.hops.at(-1).text),el('div',{class:'muted pre'},r.verdict)):null)));
}
watch(render);`
});

function setup(app) {
  const relays = [];
  app.page(PAGE);
  app.state(() => ({ relays }));

  app.route('POST', '/relay', (member, body) => {
    const text = String(body.text || '').trim().slice(0, 200);
    const langs = (Array.isArray(body.langs) ? body.langs : []).filter((l) => LANGS.includes(l)).slice(0, 8);
    if (!text) throw refuse('写下起点句子');
    if (!langs.length) throw refuse('至少选一种语言');
    const agents = app.agents();
    if (!agents.length) throw refuse('空间里还没有 agent');
    if (relays.some((r) => !r.verdict)) throw refuse('上一棒还在跑');
    const relay = { by: member.name, text, verdict: '',
      hops: [...langs, '中文'].map((lang, i) => ({ lang, agentId: agents[i % agents.length].id, agent: agents[i % agents.length].name, text: '' })) };
    relays.push(relay);
    if (relays.length > 8) relays.shift();
    app.changed();
    void pass(relay);
  });

  async function pass(r) {
    let current = r.text, from = '中文';
    try {
      for (const hop of r.hops) {
        current = hop.text = String(await app.ask(hop.agentId, `你在空间「${app.title}」玩翻译接力。把下面这段${from}译成${hop.lang}，` +
          '只看这一段本身，不要猜原文，只输出译文：\n' + current)).trim().slice(0, 600);
        from = hop.lang;
        app.changed();
      }
      r.verdict = String(await app.ask(r.hops[0].agentId, `翻译接力结束。原句：「${r.text}」\n最后译回的中文：「${current}」\n` +
        '用两三句话点评意思保留了多少、在哪一步跑偏，再给一个 0–100 的保真分。')).slice(0, 500);
    } catch (error) { r.verdict = '接力中断：' + error.message; }
    app.changed();
  }
}

if (require.main === module) run({ name: '翻译接力' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '翻译接力' };
