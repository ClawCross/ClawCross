#!/usr/bin/env node
/* 热点圆桌 · Hot Takes — the page pulls today's top stories from Hacker News or the most-read
 * articles on Chinese Wikipedia (or take any link);
 * pick one and every agent gives its stance and a take, then answers the question another agent
 * put to it. People react. The page fetches the news itself; the server only runs the table.
 * 简介：拿今天的热点（Hacker News、维基百科），每个 agent 表态并互相提问。
 *
 *   node examples/space-apps/news-roundtable/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  appId: 'news-roundtable',
  title: '热点圆桌',
  style: `.story{display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line)}.story a{color:inherit}
.stance{font-size:12px;padding:1px 8px;border-radius:99px;background:var(--card)}.stance.看好{color:var(--good)}.stance.看衰{color:var(--bad)}
.react button{padding:2px 9px;font-size:13px}`,
  body: `<h1>热点圆桌</h1><p class="muted">选一条热点，让每个 agent 表态、互相提问。</p>
<div class="card"><div class="row"><select id="source"><option value="hn">Hacker News 热门</option><option value="wiki">维基百科 · 今日热读</option></select><span class="grow"></span><button id="refresh" class="ghost">刷新</button></div><div id="stories" class="muted">加载中…</div>
<div class="row" style="margin-top:8px"><input id="title" class="grow" placeholder="或者自己贴一个话题 / 标题"><input id="url" class="grow" placeholder="链接（可选）"><button id="pick">上桌</button></div></div>
<div id="table"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
// Both sources are public CORS APIs, fetched by this page; the server never sees them.
async function hackerNews(){
  const ids=(await fetch('https://hacker-news.firebaseio.com/v0/topstories.json').then(r=>r.json())).slice(0,12);
  const items=await Promise.all(ids.map(id=>fetch('https://hacker-news.firebaseio.com/v0/item/'+id+'.json').then(r=>r.json())));
  return items.filter(Boolean).map(x=>({title:x.title,url:x.url||'https://news.ycombinator.com/item?id='+x.id,meta:'▲'+x.score}));
}
async function wikipedia(){
  const d=new Date(Date.now()-86400000),day=d.getUTCFullYear()+'/'+String(d.getUTCMonth()+1).padStart(2,'0')+'/'+String(d.getUTCDate()).padStart(2,'0');
  const feed=await fetch('https://zh.wikipedia.org/api/rest_v1/feed/featured/'+day).then(r=>r.json());
  return (feed.mostread?.articles||[]).slice(0,12).map(a=>({title:a.titles?.normalized||a.title,url:a.content_urls?.desktop?.page||'',meta:Math.round((a.views||0)/1000)+'k'}));
}
async function stories(){
  $('stories').textContent='加载中…';
  try{
    const rows=await ($('source').value==='wiki'?wikipedia():hackerNews());
    fill($('stories'),...rows.map(x=>el('div',{class:'story'},el('span',{class:'muted'},x.meta),
      el('a',{class:'grow',href:x.url,target:'_blank',rel:'noreferrer'},x.title),
      el('button',{class:'ghost',onclick:()=>act(()=>api('discuss',{title:x.title,url:x.url}))},'上桌'))));
  }catch(failure){$('stories').textContent='热门加载失败，可以自己贴一个话题。';}
}
$('refresh').onclick=stories;$('source').onchange=stories;stories();
$('pick').onclick=()=>act(()=>api('discuss',{title:$('title').value,url:$('url').value}));
function render(s){
  fill($('table'),...s.state.topics.slice().reverse().map(t=>el('div',{class:'card'},
    el('h2',{},t.url?el('a',{href:t.url,target:'_blank',rel:'noreferrer'},t.title):t.title),el('div',{class:'muted'},'由 '+t.by+' 端上桌'),
    ...t.takes.map(x=>el('div',{style:'margin:10px 0'},el('div',{class:'row'},el('span',{class:'agent'},x.name),x.stance?el('span',{class:'stance '+x.stance},x.stance):el('span',{class:'muted'},'思考中…')),
      x.take?el('div',{class:'pre'},x.take):null,
      x.question?el('div',{class:'muted'},'问 '+x.to+'：'+x.question):null,
      x.answer?el('div',{class:'pre',style:'margin-left:14px'},'↳ '+x.to+'：'+x.answer):null,
      x.take?el('div',{class:'row react'},...['👍','🤔','😂','🔥'].map(e=>el('button',{class:'ghost',onclick:()=>act(()=>api('react',{topic:t.id,agent:x.agent,emoji:e}))},e+' '+(x.reactions[e]||0)))):null)))));
}
watch(render);`
});

function setup(app) {
  const topics = [];
  app.page(PAGE);
  app.state(() => ({ topics: topics.map((t) => ({ ...t, takes: t.takes.map((x) => ({ ...x,
    reactions: Object.fromEntries(Object.entries(x.reactions).map(([e, set]) => [e, set.size])) })) })) }));

  app.route('POST', '/discuss', (member, body) => {
    const title = String(body.title || '').trim().slice(0, 200);
    if (!title) throw refuse('选一条热点或写个话题');
    const url = /^https?:\/\//.test(body.url || '') ? String(body.url).slice(0, 400) : '';
    const agents = app.agents();
    if (agents.length < 1) throw refuse('空间里还没有 agent');
    if (topics.some((t) => t.takes.some((x) => !x.stance))) throw refuse('上一条还在讨论');
    const topic = { id: String(topics.length + 1), title, url, by: member.name,
      takes: agents.map((a, i) => ({ agent: a.id, name: a.name, to: agents[(i + 1) % agents.length].name, stance: '', take: '', question: '', answer: '', reactions: {} })) };
    topics.push(topic);
    if (topics.length > 10) topics.shift();
    app.changed();
    void hold(topic, agents);
  });

  async function hold(topic, agents) {
    const about = `话题：${topic.title}${topic.url ? '（' + topic.url + '）' : ''}`;
    await Promise.all(topic.takes.map(async (x) => {
      try {
        const reply = await app.askJSON(x.agent, `你是空间「${app.title}」热点圆桌上的 agent「${x.name}」。${about}\n如果能联网就先看看原文。` +
          `给出你的立场和观点，并向「${x.to}」提一个尖锐的问题。\n格式：{"stance":"看好|看衰|中立","take":"两三句观点","question":"给 ${x.to} 的问题"}`);
        Object.assign(x, { stance: ['看好', '看衰', '中立'].includes(reply?.stance) ? reply.stance : '中立',
          take: String(reply?.take || '').slice(0, 500), question: agents.length > 1 ? String(reply?.question || '').slice(0, 200) : '' });
      } catch (error) { Object.assign(x, { stance: '中立', take: '（没有回应：' + error.message + '）', question: '' }); }
      app.changed();
    }));
    // Each agent answers the question put to it.
    await Promise.all(topic.takes.filter((x) => x.question).map(async (x) => {
      const target = topic.takes.find((y) => y.name === x.to);
      if (!target) return;
      try {
        x.answer = String(await app.ask(target.agent, `你是热点圆桌上的「${target.name}」。${about}\n你之前的观点：${target.take}\n` +
          `「${x.name}」问你：${x.question}\n用两三句话直接回答。`)).slice(0, 500);
      } catch { x.answer = '（没有回答）'; }
      app.changed();
    }));
  }

  app.route('POST', '/react', (member, body) => {
    const take = topics.find((t) => t.id === body.topic)?.takes.find((x) => x.agent === body.agent);
    if (!take || !['👍', '🤔', '😂', '🔥'].includes(body.emoji)) throw refuse('没有这条观点');
    const set = (take.reactions[body.emoji] ||= new Set());
    set.has(member.id) ? set.delete(member.id) : set.add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '热点圆桌' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '热点圆桌' };
