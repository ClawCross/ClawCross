#!/usr/bin/env node
/* 小游戏工坊 · Game Workshop — everyone's agents build one little web game together. Anyone asks an
 * agent for a change ("add a score", "make it two-player"); the agents take turns, each rewriting
 * the game on top of the last version, and the new version runs right away in every page — in a
 * nested sandbox with the network shut off. The owner can roll back; anyone can save the HTML.
 * 简介：大家的 agent 轮流改同一个网页小游戏，改完马上能玩。
 *
 *   node examples/space-apps/game-workshop/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const MAX_HTML = 60000;
const VERSIONS = 12;
const QUEUE = 8;

const PAGE = page({
  title: '小游戏工坊',
  style: `#preview{width:100%;height:min(62vh,440px);border:1px solid var(--line);border-radius:14px;background:#fff;display:block}
.change{display:flex;gap:8px;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--line);font-size:14px}.change .grow{overflow-wrap:anywhere}`,
  body: `<h1>小游戏工坊</h1><p class="muted">大家的 agent 轮流改同一个网页小游戏，改完马上能玩。游戏在隔离环境里运行，不能联网。</p>
<iframe id="preview" sandbox="allow-scripts" title="游戏"></iframe>
<div class="row" style="margin-top:6px"><span id="version" class="muted grow"></span><button id="replay" class="ghost">重新开始</button><a id="save" class="chip" download="game.html">保存 HTML</a></div>
<div class="card"><div class="row"><select id="builder"></select><input id="ask" class="grow" placeholder="让它加点什么，例如：加一个倒计时和最高分"><button id="send">请它改</button></div></div>
<div id="changes"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
// The game may not reach the network: a CSP goes into its <head>, on top of the frame's sandbox.
const GUARD='<meta http-equiv="Content-Security-Policy" content="default-src \\'none\\'; script-src \\'unsafe-inline\\'; style-src \\'unsafe-inline\\'; img-src data: blob:; media-src data: blob:; font-src data:">';
const guard=h=>/<head[^>]*>/i.test(h)?h.replace(/<head[^>]*>/i,m=>m+GUARD):h.replace(/^(\\s*<!doctype[^>]*>)?/i,m=>m+GUARD);
let shown=-1,html='';
function play(){$('preview').srcdoc=html?guard(html):'<p style="font:15px sans-serif;color:#888;text-align:center;margin-top:30%">还没有游戏，请一个 agent 先做出第一版。</p>';}
async function load(n){
  shown=n;const code=await api('code?n='+n);html=code.html;play();
  $('save').href='data:text/html;charset=utf-8,'+encodeURIComponent(html);$('save').hidden=!html;
}
$('replay').onclick=play;
$('send').onclick=()=>act(()=>{const v=$('ask').value;$('ask').value='';return api('change',{agent:$('builder').value,ask:v});});
const LABEL={queued:'排队中',working:'正在改…',done:'',failed:'没改成'};
function render(s){
  const w=s.state,agents=s.members.filter(m=>m.agent);
  if($('builder').options.length!==agents.length){
    const mine=agents.filter(a=>a.device&&a.device===s.me.device);
    fill($('builder'),...[...mine,...agents.filter(a=>!mine.includes(a))].map(a=>el('option',{value:a.id},a.name+(a.online?'':'（离线）'))));
  }
  if(w.version!==shown)act(()=>load(w.version));
  $('version').textContent=w.version?'第 '+w.version+' 版':'';
  fill($('changes'),...w.changes.slice().reverse().map(c=>el('div',{class:'change'},el('span',{class:'agent'},c.agent),
    el('span',{class:'grow'},'「'+c.ask+'」 · '+c.by+(c.status==='done'?' → 第 '+c.n+' 版：'+c.summary:' · '+LABEL[c.status]+(c.error?'：'+c.error:''))),
    s.owner&&c.status==='done'&&c.n!==w.version?el('button',{class:'ghost',onclick:()=>act(()=>api('restore',{n:c.n}))},'回到这一版'):null)));
}
watch(render);`
});

/** The summary line and the HTML from a reply: a sentence, then the page in a ```html block. */
function parse(reply) {
  const text = String(reply || '');
  const fence = /```(?:html)?[ \t]*\n([\s\S]*?)(?:```|$)/i.exec(text);
  const html = (fence ? fence[1] : /<!doctype html[\s\S]*<\/html>/i.exec(text)?.[0] || '').trim();
  const summary = (fence ? text.slice(0, fence.index) : '').split('\n').map((line) => line.replace(/[#*`>]/g, '').trim()).find(Boolean) || '';
  return { html, summary: summary.slice(0, 120) };
}

function setup(app) {
  const versions = [];   // {n, html}
  const changes = [];    // {id, agent, agentId, ask, by, byId, status, summary, error, n}
  let nextChange = 1;
  let nextVersion = 1;
  let busy = false;
  app.page(PAGE);
  app.state(() => ({ version: versions.at(-1)?.n || 0,
    changes: changes.map(({ agentId, byId, ...c }) => c) }));

  app.route('GET', '/code', (member, query) => {
    const version = versions.find((v) => v.n === Number(query.n)) || versions.at(-1);
    return { n: version?.n || 0, html: version?.html || '' };
  });

  app.route('POST', '/change', (member, body) => {
    const agent = app.member(body.agent);
    const ask = String(body.ask || '').trim().slice(0, 200);
    if (!agent?.agent) throw refuse('选一个 agent');
    if (!ask) throw refuse('说说要改什么');
    const waiting = changes.filter((c) => ['queued', 'working'].includes(c.status));
    if (waiting.some((c) => c.byId === member.id)) throw refuse('你的上一个请求还没改完');
    if (waiting.length >= QUEUE) throw refuse('排队的请求太多了，等等再来');
    changes.push({ id: nextChange++, agent: agent.name, agentId: agent.id, ask, by: member.name, byId: member.id, status: 'queued', summary: '', error: '', n: 0 });
    while (changes.length > 30 && !['queued', 'working'].includes(changes[0].status)) changes.shift();
    app.changed();
    void pump();
  });

  app.route('POST', '/restore', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以回滚', 403);
    const old = versions.find((v) => v.n === Number(body.n));
    if (!old) throw refuse('这一版已经不在了');
    versions.push({ n: nextVersion++, html: old.html });
    changes.push({ id: nextChange++, agent: member.name, agentId: '', ask: '回到第 ' + old.n + ' 版', by: member.name, byId: member.id, status: 'done', summary: '回滚', error: '', n: versions.at(-1).n });
    while (versions.length > VERSIONS) versions.shift();
    app.changed();
  });

  /** One change at a time: each agent builds on the version before it. */
  async function pump() {
    if (busy) return;
    busy = true;
    for (let change; (change = changes.find((c) => c.status === 'queued'));) {
      change.status = 'working';
      app.changed();
      const current = versions.at(-1)?.html || '';
      const recent = changes.filter((c) => c.status === 'done').slice(-5).map((c) => `- ${c.agent}：${c.ask} → ${c.summary}`).join('\n');
      try {
        const reply = await app.ask(change.agentId, `你在空间「${app.title}」的小游戏工坊里，和其他 agent 轮流完善同一个单文件网页小游戏。\n` +
          (current ? `之前的改动：\n${recent || '（无）'}\n当前版本：\n\`\`\`html\n${current}\n\`\`\`\n${change.by} 请你：${change.ask}\n在当前版本上修改，保留已有玩法，除非要求改掉。\n`
            : `还没有代码。${change.by} 请你做出第一版：${change.ask}\n`) +
          `要求：单个 HTML 文件，CSS 和 JS 都内联；在大约 640×440 的框里运行，鼠标、触屏和键盘都能玩；不能加载任何外部资源（网络被禁止），` +
          `不要用 localStorage、cookie、alert；总长 ${MAX_HTML} 字符以内。\n格式：第一行用一句话说明你改了什么，然后把完整的新 HTML 放在一个 \`\`\`html 代码块里。`);
        const { html, summary } = parse(reply);
        if (!html.includes('<')) throw new Error('回复里没有 HTML');
        if (html.length > MAX_HTML) throw new Error('游戏太大了');
        versions.push({ n: nextVersion++, html });
        while (versions.length > VERSIONS) versions.shift();
        Object.assign(change, { status: 'done', summary: summary || '改好了', n: versions.at(-1).n });
      } catch (error) {
        Object.assign(change, { status: 'failed', error: error.message });
      }
      app.changed();
    }
    busy = false;
  }
}

if (require.main === module) run({ name: '小游戏工坊' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '小游戏工坊' };
