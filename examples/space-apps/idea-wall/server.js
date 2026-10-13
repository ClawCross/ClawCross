#!/usr/bin/env node
/* 点子墙 · Idea Wall — a brainstorm. The owner sets a topic; people post ideas and send their own
 * agents to add more (the owner can send everyone's). Each person has three dots to vote with. One
 * agent then sorts the wall into themes and sums it up. Built for team decisions.
 * 简介：人和 agent 一起出点子，每人 3 票，最后由 agent 归类总结。
 *
 *   node examples/space-apps/idea-wall/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const IDEAS = 80;
const PER_PERSON = 10;
const DOTS = 3;

const PAGE = page({
  title: '点子墙',
  style: `.idea{display:flex;gap:8px;align-items:center;padding:6px 10px;background:var(--bg);border:1px solid var(--line);border-radius:10px;margin:5px 0}
.idea .grow{overflow-wrap:anywhere}.dot{padding:3px 10px;font-size:13px}.dot.on{background:var(--accent);color:var(--on-accent)}.theme h2{margin-top:4px}`,
  body: `<h1>点子墙</h1><p class="muted">围绕一个主题，人和 agent 一起出点子。每人 3 个点投票，最后让 agent 归类总结。</p>
<div id="topic-box" class="card" hidden><div class="row"><input id="topic" class="grow" placeholder="主题，例如：周末团建去哪"><button id="set">换主题</button></div></div>
<div id="head"></div>
<div id="idea-box" class="card" hidden><div class="row"><input id="idea" class="grow" placeholder="写一个点子"><button id="post">贴上去</button><button id="agents" class="ghost">派 agent 出点子</button></div>
<div id="tools" class="row" style="margin-top:8px"></div></div>
<div id="wall"></div>`,
  script: `
const {api,watch,act,el,fill,keep}=App,$=id=>document.getElementById(id);
$('set').onclick=()=>act(()=>{const v=$('topic').value;$('topic').value='';return api('topic',{text:v});});
$('post').onclick=()=>act(()=>{const v=$('idea').value;$('idea').value='';return api('idea',{text:v});});
$('idea').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing)$('post').click();};
$('agents').onclick=()=>act(()=>api('agents',{}));
function render(s){
  const w=s.state;
  $('topic-box').hidden=!s.owner;$('idea-box').hidden=!w.topic;
  $('agents').textContent=s.owner?'派所有 agent 出点子':'派我的 agent 出点子';
  fill($('tools'),el('span',{class:'muted grow'},'你还有 '+(w.dots-w.used)+' 个点'+(w.thinking.length?' · '+w.thinking.join('、')+' 在想…':'')),
    s.owner&&w.ideas.length?[keep('sorter',()=>el('select',{id:'sorter'},...s.members.filter(m=>m.agent).map(a=>el('option',{value:a.id},a.name)))),
      el('button',{class:'ghost',onclick:()=>act(()=>api('sort',{agent:$('sorter').value}))},w.sorting?'正在归类…':'让它归类总结')]:null);
  fill($('head'),w.topic?el('h2',{},w.topic):el('p',{class:'muted'},s.owner?'先定一个主题。':'等群主定主题。'),
    w.summary?el('div',{class:'card'},el('strong',{},'总结 · '+w.sortedBy),el('div',{class:'pre'},w.summary)):null);
  const card=i=>el('div',{class:'idea'},el('span',{class:'grow'},i.text),el('span',{class:i.agent?'agent':'muted'},i.by),
    el('button',{class:'dot'+(i.mine?' on':' ghost'),onclick:()=>act(()=>api('dot',{id:i.id}))},'● '+i.dots));
  const themes=w.themes.length?w.themes.map(t=>({name:t.name,ideas:w.ideas.filter(i=>t.ideas.includes(i.id))})):[];
  const rest=w.ideas.filter(i=>!themes.some(t=>t.ideas.includes(i)));
  fill($('wall'),...themes.map(t=>el('div',{class:'theme'},el('h2',{},t.name),...t.ideas.map(card))),rest.length?el('div',{class:'theme'},themes.length?el('h2',{},'其他'):null,...rest.map(card)):null);
}
watch(render);`
});

function setup(app) {
  // ideas: {id, text, by, byId, agent, dots: Set(person)}
  const wall = { topic: '', ideas: [], themes: [], summary: '', sortedBy: '', sorting: false, thinking: new Map(), asked: new Set() };
  let next = 1;
  app.page(PAGE);

  app.state((member) => ({
    topic: wall.topic, summary: wall.summary, sortedBy: wall.sortedBy, sorting: wall.sorting, themes: wall.themes, thinking: [...wall.thinking.values()],
    dots: DOTS, used: wall.ideas.filter((i) => i.dots.has(member.id)).length,
    ideas: wall.ideas.map((i) => ({ id: i.id, text: i.text, by: i.by, agent: i.agent, dots: i.dots.size, mine: i.dots.has(member.id) }))
      .sort((a, b) => b.dots - a.dots),
  }));

  const add = (text, by, byId, agent) => {
    if (wall.ideas.length >= IDEAS) return false;
    wall.ideas.push({ id: String(next++), text, by, byId, agent, dots: new Set() });
    return true;
  };

  app.route('POST', '/topic', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以定主题', 403);
    const topic = String(body.text || '').trim().slice(0, 100);
    if (!topic) throw refuse('写一个主题');
    Object.assign(wall, { topic, ideas: [], themes: [], summary: '', sortedBy: '', asked: new Set() });
    app.changed();
  });

  app.route('POST', '/idea', (member, body) => {
    const text = String(body.text || '').trim().slice(0, 120);
    if (!wall.topic) throw refuse('还没有主题');
    if (!text) throw refuse('写一个点子');
    if (wall.ideas.filter((i) => i.byId === member.id).length >= PER_PERSON) throw refuse('你已经贴了很多了');
    if (!add(text, member.name, member.id, false)) throw refuse('墙贴满了');
    app.changed();
  });

  app.route('POST', '/agents', (member) => {
    if (!wall.topic) throw refuse('还没有主题');
    // People send their own agents; the owner may send every agent. Each agent brainstorms once per topic.
    const agents = (member.owner ? app.agents() : app.agentsOf(member.id)).filter((a) => a.online !== false && !wall.asked.has(a.id));
    if (!agents.length) throw refuse(member.owner ? '所有 agent 都出过点子了' : '你的 agent 已经出过点子了');
    const topic = wall.topic;
    for (const agent of agents) {
      wall.asked.add(agent.id);
      wall.thinking.set(agent.id, agent.name);
      const existing = wall.ideas.map((i) => '- ' + i.text).join('\n') || '（还没有）';
      app.askJSON(agent.id, `你在空间「${app.title}」的点子墙上头脑风暴。主题：${topic}\n墙上已有：\n${existing}\n再想 3 个不重复、具体、可执行的点子，每个一句话。\n格式：{"ideas":["点子"]}`)
        .then((reply) => {
          if (wall.topic !== topic) return;
          for (const idea of (Array.isArray(reply?.ideas) ? reply.ideas : []).slice(0, 3)) {
            const text = String(idea || '').trim().slice(0, 120);
            if (text) add(text, agent.name, agent.id, true);
          }
        })
        .catch(() => {})
        .finally(() => { wall.thinking.delete(agent.id); app.changed(); });
    }
    app.changed();
  });

  app.route('POST', '/dot', (member, body) => {
    const idea = wall.ideas.find((i) => i.id === body.id);
    if (!idea) throw refuse('没有这个点子');
    if (idea.dots.has(member.id)) idea.dots.delete(member.id);
    else if (wall.ideas.filter((i) => i.dots.has(member.id)).length >= DOTS) throw refuse(`每人只有 ${DOTS} 个点，先撤掉一个`);
    else idea.dots.add(member.id);
    app.changed();
  });

  app.route('POST', '/sort', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以归类', 403);
    const agent = app.member(body.agent);
    if (!agent?.agent) throw refuse('选一个 agent');
    if (wall.sorting) throw refuse('正在归类');
    if (!wall.ideas.length) throw refuse('墙上还没有点子');
    wall.sorting = true;
    app.changed();
    const topic = wall.topic;
    app.askJSON(agent.id, `你在空间「${app.title}」整理点子墙。主题：${topic}\n点子（#编号 内容 · 票数）：\n` +
      wall.ideas.map((i) => `#${i.id} ${i.text} · ${i.dots.size} 票`).join('\n') +
      '\n把它们归成 2 到 6 个主题（每个点子只归一类），再用三五句话总结大家的倾向和建议的下一步。\n格式：{"themes":[{"name":"主题名","ideas":["编号"]}],"summary":"总结"}')
      .then((reply) => {
        if (wall.topic !== topic) return;
        const ids = new Set(wall.ideas.map((i) => i.id)), used = new Set();
        wall.themes = (Array.isArray(reply?.themes) ? reply.themes : []).slice(0, 6).map((t) => ({ name: String(t?.name || '').slice(0, 30) || '未命名',
          ideas: (Array.isArray(t?.ideas) ? t.ideas : []).map((x) => String(x).replace('#', '')).filter((x) => ids.has(x) && !used.has(x) && used.add(x)) }))
          .filter((t) => t.ideas.length);
        Object.assign(wall, { summary: String(reply?.summary || '').slice(0, 800), sortedBy: agent.name });
      })
      .catch((error) => { wall.summary = '没有归类成：' + error.message; wall.sortedBy = agent.name; })
      .finally(() => { wall.sorting = false; app.changed(); });
  });
}

if (require.main === module) run({ name: '点子墙' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '点子墙' };
