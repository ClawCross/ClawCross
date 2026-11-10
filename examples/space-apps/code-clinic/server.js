#!/usr/bin/env node
/* 代码会诊 · Code Clinic — paste code or a diff; every agent in the space reviews it on its own,
 * then each one cross-examines the others' findings (agree or dispute, with a reason). The page
 * merges everything into one table ranked by severity and agreement. Built for "my Claude Code and
 * your Codex review this together".
 * 简介：贴一段代码，每个 agent 独立审查后互相质证，汇总成问题清单。
 *
 *   node examples/space-apps/code-clinic/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const SEVERITY = { high: 3, medium: 2, low: 1 };

const PAGE = page({
  appId: 'code-clinic',
  title: '代码会诊',
  style: `.code{font:12px/1.55 ui-monospace,Menlo,monospace;background:var(--card);border-radius:12px;padding:10px;overflow:auto;max-height:42vh;white-space:pre}
.code .hit{background:#ffd8a8;color:#000}.sev{font-size:11px;padding:1px 8px;border-radius:99px;color:#fff}.sev.high{background:#c92a2a}.sev.medium{background:#e8590c}.sev.low{background:#868e96}
.verdict{font-size:12px;margin:2px 0}.agree{color:var(--good)}.dispute{color:var(--bad)}`,
  body: `<h1>代码会诊</h1><p class="muted">贴一段代码或 diff，空间里的 agent 各自审查，再互相质询对方的发现。</p>
<div class="card"><textarea id="code" rows="9" placeholder="粘贴代码或 diff（最多 20000 字）" style="font:12px ui-monospace,Menlo,monospace"></textarea>
<div class="row" style="margin-top:8px"><select id="focus"><option>全部</option><option>正确性</option><option>安全</option><option>性能</option><option>可读性</option></select>
<input id="note" class="grow" placeholder="补充说明（可选），例如：这是登录接口"><button id="start">开始会诊</button></div></div>
<div id="session"></div>`,
  script: `
const {api,watch,act,el}=App,$=id=>document.getElementById(id);
$('start').onclick=()=>act(()=>api('review',{code:$('code').value,focus:$('focus').value,note:$('note').value}));
function render(s){
  const r=s.state.session;
  if(!r){$('session').replaceChildren();return;}
  const lines=new Set(r.findings.map(f=>f.line).filter(Boolean));
  const code=el('div',{class:'code'},...r.code.split('\\n').map((text,i)=>el('div',{class:lines.has(i+1)?'hit':''},String(i+1).padStart(4)+'  '+text)));
  const status=Object.values(r.agents).map(a=>a.name+'：'+({reviewing:'审查中…',examining:'质询中…',done:'完成',failed:'未完成'}[a.status]||a.status)).join('　');
  App.fill($('session'),el('h2',{},'会诊 · '+r.focus+'（'+r.by+'）'),el('p',{class:'muted'},status),code,
    el('h2',{},'发现（'+r.findings.length+'）'),
    ...r.findings.map(f=>el('div',{class:'card'},
      el('div',{class:'row'},el('span',{class:'sev '+f.severity},f.severity),el('strong',{class:'grow'},f.title),el('span',{class:'muted'},(f.line?'第 '+f.line+' 行 · ':'')+f.agentName)),
      el('div',{class:'pre'},f.detail),f.fix?el('div',{class:'muted pre'},'建议：'+f.fix):null,
      ...f.verdicts.map(v=>el('div',{class:'verdict '+(v.agree?'agree':'dispute')},(v.agree?'✔ ':'✘ ')+v.name+'：'+v.reason)),
      el('div',{class:'row'},el('span',{class:'muted grow'},'认同 '+f.score),
        el('button',{class:'ghost',onclick:()=>act(()=>api('mark',{id:f.id,ok:true}))},'👍 '+f.people.up),
        el('button',{class:'ghost',onclick:()=>act(()=>api('mark',{id:f.id,ok:false}))},'👎 '+f.people.down)))));
}
watch(render);`
});

const review = (app, agent, s) => `你是空间「${app.title}」里「代码会诊」小程序的审查 agent「${agent.name}」。\n` +
  `${s.by} 请大家审查下面的代码，关注点：${s.focus}。${s.note ? '说明：' + s.note : ''}\n` +
  '独立给出你发现的问题（最多 8 个），只写真实存在、值得改的问题，不要凑数。行号指下面代码的行号（从 1 开始），说不准就写 null。\n' +
  '格式：{"findings":[{"severity":"high|medium|low","line":行号或null,"title":"一句话问题","detail":"为什么是问题","fix":"怎么改"}]}\n\n' +
  s.code.split('\n').map((line, i) => `${i + 1}| ${line}`).join('\n');

const examine = (app, agent, s, others) => `你是「代码会诊」的审查 agent「${agent.name}」。其他 agent 对同一段代码提出了下面这些问题。` +
  '逐条判断你是否认同，给出简短理由；不确定就写 agree=false 并说明。\n' +
  '格式：{"verdicts":[{"id":"问题编号","agree":true或false,"reason":"一句话理由"}]}\n\n' +
  others.map((f) => `[${f.id}] (${f.severity}${f.line ? '，第 ' + f.line + ' 行' : ''}，${f.agentName}) ${f.title}：${f.detail}`).join('\n') +
  '\n\n代码：\n' + s.code.split('\n').map((line, i) => `${i + 1}| ${line}`).join('\n');

function setup(app) {
  let session = null;
  app.page(PAGE);
  app.state(() => ({ session: session && {
    ...session,
    findings: session.findings.map((f) => ({ ...f, score: f.verdicts.filter((v) => v.agree).length - f.verdicts.filter((v) => !v.agree).length,
      people: { up: [...f.up].length, down: [...f.down].length }, up: undefined, down: undefined }))
      .sort((a, b) => SEVERITY[b.severity] - SEVERITY[a.severity] || b.score - a.score)
  } }));

  app.route('POST', '/review', (member, body) => {
    const code = String(body.code || '').replace(/\r/g, '');
    if (!code.trim()) throw refuse('先贴上代码');
    if (code.length > 20000) throw refuse('代码最多 20000 字');
    if (session && Object.values(session.agents).some((a) => !['done', 'failed'].includes(a.status))) throw refuse('上一次会诊还没结束');
    const agents = app.agents();
    if (!agents.length) throw refuse('空间里还没有 agent');
    const s = session = { code, focus: String(body.focus || '全部').slice(0, 10), note: String(body.note || '').slice(0, 300),
      by: member.name, agents: Object.fromEntries(agents.map((a) => [a.id, { name: a.name, status: 'reviewing' }])), findings: [] };
    app.changed();
    void consult(agents, s);
  });

  /** Runs in the background: the page follows along through the state. */
  async function consult(agents, s) {
    // Round one: independent reviews.
    await Promise.all(agents.map(async (agent) => {
      try {
        const answer = await app.askJSON(agent.id, review(app, agent, s));
        for (const f of (answer?.findings || []).slice(0, 8)) {
          s.findings.push({ id: 'F' + (s.findings.length + 1), agent: agent.id, agentName: agent.name,
            severity: SEVERITY[f.severity] ? f.severity : 'low', line: Number.isInteger(f.line) ? f.line : null,
            title: String(f.title || '').slice(0, 120), detail: String(f.detail || '').slice(0, 800), fix: String(f.fix || '').slice(0, 600),
            verdicts: [], up: new Set(), down: new Set() });
        }
        s.agents[agent.id].status = 'examining';
      } catch (error) { s.agents[agent.id] = { name: agent.name, status: 'failed', error: error.message }; }
      app.changed();
    }));
    // Round two: each agent cross-examines everyone else's findings.
    await Promise.all(agents.filter((a) => s.agents[a.id].status === 'examining').map(async (agent) => {
      const others = s.findings.filter((f) => f.agent !== agent.id);
      try {
        if (others.length) {
          const answer = await app.askJSON(agent.id, examine(app, agent, s, others));
          for (const v of answer?.verdicts || []) {
            const finding = others.find((f) => f.id === v.id);
            if (finding) finding.verdicts.push({ name: agent.name, agree: v.agree === true, reason: String(v.reason || '').slice(0, 300) });
          }
        }
        s.agents[agent.id].status = 'done';
      } catch (error) { s.agents[agent.id] = { name: agent.name, status: 'failed', error: error.message }; }
      app.changed();
    }));
  }

  app.route('POST', '/mark', (member, body) => {
    const finding = session?.findings.find((f) => f.id === body.id);
    if (!finding) throw refuse('没有这条发现');
    for (const set of [finding.up, finding.down]) set.delete(member.id);
    (body.ok ? finding.up : finding.down).add(member.id);
    app.changed();
  });
}

if (require.main === module) run({ name: '代码会诊' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '代码会诊' };
