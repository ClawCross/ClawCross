#!/usr/bin/env node
/* 研究圆桌 · Research Roundtable — ask a question; every agent researches it with its own tools
 * and brings a brief with sources, then critiques the others; one agent writes the final report
 * with the consensus, the disagreements and what is still open.
 *
 *   node examples/space-apps/research-roundtable/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PAGE = page({
  title: '研究圆桌',
  style: `.brief h3{margin:0 0 4px;font-size:15px}.brief li{margin:2px 0}.src a{font-size:12px;color:var(--accent);margin-right:8px}.critique{font-size:13px;color:var(--muted)}`,
  body: `<h1>研究圆桌</h1><p class="muted">一个问题，每个 agent 各自调研、互相点评，最后汇总成报告。</p>
<div class="card"><textarea id="question" rows="2" placeholder="研究问题，例如：2026 年个人开发者做 AI 产品，最值得押注哪个方向？"></textarea>
<div class="row" style="margin-top:8px"><label>汇总人 <select id="writer"></select></label><button id="ask">开始圆桌</button></div></div><div id="table"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('ask').onclick=()=>act(()=>api('ask',{question:$('question').value,writer:$('writer').value}));
const STAGE={researching:'调研中…',critiquing:'点评中…',done:'完成',failed:'未完成'};
function render(s){
  const agents=s.members.filter(m=>m.agent);
  if($('writer').options.length!==agents.length)fill($('writer'),...agents.map(a=>el('option',{value:a.id},a.name)));
  const r=s.state.round;
  if(!r){fill($('table'));return;}
  fill($('table'),el('h2',{},r.question),el('p',{class:'muted'},Object.values(r.agents).map(a=>a.name+'：'+(STAGE[a.status]||a.status)).join('　')),
    el('div',{class:'grid'},...Object.values(r.agents).filter(a=>a.brief).map(a=>el('div',{class:'card brief'},el('h3',{class:'agent'},a.name),
      el('div',{class:'pre'},a.brief.summary),el('ul',{},...a.brief.points.map(p=>el('li',{},p))),
      el('div',{class:'src'},...a.brief.sources.map(x=>el('a',{href:x.url,target:'_blank',rel:'noreferrer'},x.title||x.url))),
      ...a.critiques.map(c=>el('div',{class:'critique'},'→ 对 '+c.target+'：'+c.comment))))),
    r.report?el('div',{class:'card'},el('div',{class:'row'},el('h2',{class:'grow'},'报告 · '+r.writerName),
      el('button',{class:'ghost',onclick:()=>navigator.clipboard?.writeText(r.report)},'复制')),el('div',{class:'pre'},r.report)):
      r.writing?el('p',{class:'muted'},r.writerName+' 正在汇总报告…'):null);
}
watch(render);`
});

function setup(app) {
  let round = null;
  app.page(PAGE);
  app.state(() => ({ round }));

  app.route('POST', '/ask', (member, body) => {
    const question = String(body.question || '').trim().slice(0, 600);
    if (!question) throw refuse('写下研究问题');
    if (round && !round.report && !round.failed) throw refuse('上一轮圆桌还在进行');
    const agents = app.agents();
    if (!agents.length) throw refuse('空间里还没有 agent');
    const writer = app.member(body.writer)?.agent ? app.member(body.writer) : agents[0];
    round = { question, by: member.name, writerName: writer.name, writing: false, report: '', failed: false,
      agents: Object.fromEntries(agents.map((a) => [a.id, { name: a.name, status: 'researching', brief: null, critiques: [] }])) };
    app.changed();
    void hold(round, agents, writer);
  });

  async function hold(r, agents, writer) {
    // Research, each on its own.
    await Promise.all(agents.map(async (agent) => {
      try {
        const brief = await app.askJSON(agent.id, `你是空间「${app.title}」研究圆桌的研究员「${agent.name}」。${r.by} 提出的问题：${r.question}\n` +
          '如果你能联网搜索，请先查资料再回答，引用真实可访问的来源。给出你的独立结论。\n' +
          '格式：{"summary":"三句话以内的结论","points":["要点1","要点2","要点3"],"sources":[{"title":"来源标题","url":"https://..."}]}');
        r.agents[agent.id].brief = { summary: String(brief?.summary || '').slice(0, 800),
          points: (brief?.points || []).slice(0, 6).map((p) => String(p).slice(0, 300)),
          sources: (brief?.sources || []).filter((x) => /^https?:\/\//.test(x?.url)).slice(0, 6).map((x) => ({ title: String(x.title || '').slice(0, 100), url: String(x.url).slice(0, 400) })) };
        r.agents[agent.id].status = 'critiquing';
      } catch (error) { r.agents[agent.id].status = 'failed'; }
      app.changed();
    }));
    const briefs = () => Object.values(r.agents).filter((a) => a.brief)
      .map((a) => `【${a.name}】${a.brief.summary}\n- ${a.brief.points.join('\n- ')}`).join('\n\n');
    // Critique the others.
    await Promise.all(agents.filter((a) => r.agents[a.id].status === 'critiquing').map(async (agent) => {
      try {
        const reply = await app.askJSON(agent.id, `你是研究圆桌的研究员「${agent.name}」。问题：${r.question}\n大家的简报：\n${briefs()}\n` +
          '点评其他人的简报：指出最有价值的一点或最可疑的一点。格式：{"critiques":[{"target":"研究员名字","comment":"一句点评"}]}');
        r.agents[agent.id].critiques = (reply?.critiques || []).filter((c) => c?.target !== agent.name).slice(0, 4)
          .map((c) => ({ target: String(c.target || '').slice(0, 40), comment: String(c.comment || '').slice(0, 300) }));
        r.agents[agent.id].status = 'done';
      } catch { r.agents[agent.id].status = 'failed'; }
      app.changed();
    }));
    // The report.
    r.writing = true;
    app.changed();
    try {
      const critiques = Object.values(r.agents).flatMap((a) => a.critiques.map((c) => `${a.name} → ${c.target}：${c.comment}`)).join('\n');
      r.report = String(await app.ask(writer.id, `你是研究圆桌的汇总人「${writer.name}」。问题：${r.question}\n简报：\n${briefs()}\n\n互相点评：\n${critiques || '（无）'}\n\n` +
        '写一份简洁的报告（600 字以内），分成：结论、共识、分歧、还没有答案的问题。直接输出报告正文。')).slice(0, 4000);
    } catch (error) { r.failed = true; r.report = '汇总失败：' + error.message; }
    r.writing = false;
    app.changed();
  }
}

if (require.main === module) run({ name: '研究圆桌' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '研究圆桌' };
