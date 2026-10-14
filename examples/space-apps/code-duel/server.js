#!/usr/bin/env node
/* 代码擂台 · Code Duel — the owner picks a small programming problem; every agent writes a
 * JavaScript solution and people may race them with their own. Code stays hidden until the owner
 * ends the round; then every page runs all solutions against the tests itself — in a Web Worker
 * inside a sandbox with the network shut off — and ranks them by tests passed, then by time.
 * The server never runs submitted code.
 * 简介：agent 和人比写同一道题，结束后在每个人的浏览器里跑测试排名。
 *
 *   node examples/space-apps/code-duel/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const PROBLEMS = [
  { id: 'palindrome', title: '回文判断', fn: 'isPalindrome', sig: 'function isPalindrome(s)', desc: '忽略大小写和所有非字母数字字符，判断字符串是不是回文，返回 true/false。',
    tests: [[['A man, a plan, a canal: Panama'], true], [['race a car'], false], [[''], true], [['0P'], false], [["No 'x' in Nixon"], true], [['ab_a'], true]] },
  { id: 'roman', title: '罗马数字', fn: 'toRoman', sig: 'function toRoman(n)', desc: '把 1 到 3999 的整数转成罗马数字字符串（IV、IX、XL、XC、CD、CM 用减法写法）。',
    tests: [[[3], 'III'], [[4], 'IV'], [[9], 'IX'], [[58], 'LVIII'], [[1994], 'MCMXCIV'], [[3999], 'MMMCMXCIX']] },
  { id: 'brackets', title: '括号匹配', fn: 'balanced', sig: 'function balanced(s)', desc: '字符串只含 ()[]{}，判断括号是否正确配对和嵌套，返回 true/false。',
    tests: [[['()[]{}'], true], [['(]'], false], [['([)]'], false], [['{[]}'], true], [[''], true], [['(('], false]] },
  { id: 'unique', title: '最长无重复子串', fn: 'longestUnique', sig: 'function longestUnique(s)', desc: '返回字符串中不含重复字符的最长连续子串的长度。',
    tests: [[['abcabcbb'], 3], [['bbbbb'], 1], [['pwwkew'], 3], [[''], 0], [['dvdf'], 3], [['abba'], 2]] },
  { id: 'intervals', title: '合并区间', fn: 'mergeIntervals', sig: 'function mergeIntervals(list)', desc: '给定若干 [起, 止] 区间（顺序任意），合并所有重叠或相接的区间，按起点升序返回。',
    tests: [[[[[1, 3], [2, 6], [8, 10], [15, 18]]], [[1, 6], [8, 10], [15, 18]]], [[[[1, 4], [4, 5]]], [[1, 5]]], [[[]], []], [[[[5, 6], [1, 2]]], [[1, 2], [5, 6]]], [[[[1, 10], [2, 3], [4, 5]]], [[1, 10]]]] },
  { id: 'thousands', title: '千分位', fn: 'thousands', sig: 'function thousands(n)', desc: '把数字格式化成带千分位逗号的字符串，保留原有小数部分和负号。',
    tests: [[[1234567], '1,234,567'], [[0], '0'], [[-1234.5], '-1,234.5'], [[999], '999'], [[1000], '1,000'], [[1234.567], '1,234.567']] },
];
const SAMPLES = 2;
const AGENTS = 4;
const MAX_CODE = 8000;

const PAGE = page({
  appId: 'code-duel',
  title: '代码擂台',
  style: `#runner{display:none}code,textarea.code{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
.solution pre{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px;overflow:auto;max-height:260px;font-size:12px}
.pass{color:var(--good);font-weight:700}.fail{color:var(--bad);font-weight:700}.rank{font-size:20px;font-weight:700;width:1.6em}`,
  body: `<h1>代码擂台</h1><p class="muted">agent 和人一起写同一道题。代码在结束前保密；结束后每个人的浏览器自己跑测试（断网的隔离环境），按通过数和用时排名。</p>
<div id="setup" class="card" hidden></div><div id="problem"></div>
<div id="mine" class="card" hidden><textarea id="code" class="code" rows="8" spellcheck="false"></textarea>
<div class="row" style="margin-top:8px"><button id="try" class="ghost">试运行样例</button><button id="submit">提交我的</button><span id="tried" class="muted"></span></div></div>
<div id="solutions"></div><iframe id="runner" sandbox="allow-scripts" title="测试"></iframe>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
// Solutions run in a Worker inside a network-less sandbox frame, so a bad loop never blocks this page.
const RUNNER='<meta http-equiv="Content-Security-Policy" content="default-src \\'none\\'; script-src \\'unsafe-inline\\'; worker-src blob:"><script>'+
  'onmessage=e=>{const {id,code,fn,tests}=e.data;'+
  'const src=code+"\\\\n;onmessage=m=>postMessage(m.data.map(([args,want])=>{try{const got=JSON.stringify("+fn+"(...args));return {ok:got===JSON.stringify(want),got:String(got).slice(0,80)};}catch(x){return {ok:false,got:String(x&&x.message||x).slice(0,80)};}}));";'+
  'let w;try{w=new Worker(URL.createObjectURL(new Blob([src],{type:"text/javascript"})));}catch(x){parent.postMessage({id,error:String(x)},"*");return;}'+
  'const t=setTimeout(()=>{w.terminate();parent.postMessage({id,error:"超时"},"*");},3000);'+
  'w.onmessage=m=>{clearTimeout(t);w.terminate();parent.postMessage({id,results:m.data},"*");};'+
  'w.onerror=m=>{m.preventDefault();clearTimeout(t);w.terminate();parent.postMessage({id,error:m.message||"出错了"},"*");};'+
  'w.postMessage(tests);};</script>';
const ready=new Promise(r=>{$('runner').onload=r;$('runner').srcdoc=RUNNER;});
const waiting=new Map();let seq=0;
addEventListener('message',e=>{if(e.source!==$('runner').contentWindow)return;const done=waiting.get(e.data.id);if(done){waiting.delete(e.data.id);done(e.data);}});
async function test(code,fn,tests){await ready;const id=++seq;return new Promise(r=>{waiting.set(id,r);$('runner').contentWindow.postMessage({id,code,fn,tests},'*');});}
const results=new Map();   // solution key -> result, so each page runs each solution once
let last=null,round=0;
$('try').onclick=()=>act(async()=>{const p=last.state.problem,r=await test($('code').value,p.fn,p.samples);
  $('tried').textContent=r.error?'出错：'+r.error:'样例 '+r.results.filter(x=>x.ok).length+'/'+r.results.length+' 通过';});
$('submit').onclick=()=>act(()=>api('submit',{code:$('code').value}));
function render(s){
  last=s;const g=s.state;
  $('setup').hidden=!(s.owner&&g.status!=='coding');
  if(!$('setup').hidden&&!$('setup').childElementCount)fill($('setup'),el('div',{class:'row'},el('select',{id:'pick',class:'grow'},...g.problems.map(p=>el('option',{value:p.id},p.title))),
    el('button',{onclick:()=>act(()=>api('start',{problem:$('pick').value}))},'开题')));
  const p=g.problem;
  fill($('problem'),p?el('div',{class:'card'},el('h2',{style:'margin-top:0'},'第 '+g.round+' 题 · '+p.title),el('div',{},p.desc),el('code',{},p.sig),
    el('div',{class:'muted'},'样例：',...p.samples.map(([a,w])=>el('div',{},el('code',{},p.fn+'('+a.map(x=>JSON.stringify(x)).join(', ')+') → '+JSON.stringify(w))))),
    g.status==='coding'&&s.owner?el('button',{style:'margin-top:8px',onclick:()=>act(()=>api('finish',{}))},'结束，揭晓代码'):null):el('p',{class:'muted'},s.owner?'选一道题开题。':'等群主开题。'));
  $('mine').hidden=g.status!=='coding';
  if(g.round!==round){round=g.round;if(p)$('code').value=p.sig+' {\\n  \\n}';$('tried').textContent='';}
  if(g.status==='coding'){
    fill($('solutions'),...g.solutions.map(x=>el('div',{class:'card row'},el('span',{class:x.kind==='agent'?'agent':''},x.name),el('span',{class:'grow muted'},
      x.status==='writing'?'正在写…':x.status==='failed'?'没交：'+x.error:'已提交 · 用时 '+x.seconds+' 秒'))));
    return;
  }
  const scored=g.solutions.map(x=>({...x,result:results.get(x.key)}));
  for(const x of scored)if(x.code&&!results.has(x.key)){results.set(x.key,null);test(x.code,p.fn,p.tests).then(r=>{results.set(x.key,r);last&&render(last);});}
  const passed=x=>x.result&&!x.result.error?x.result.results.filter(t=>t.ok).length:-1;
  scored.sort((a,b)=>passed(b)-passed(a)||(a.seconds??1e9)-(b.seconds??1e9));
  fill($('solutions'),...scored.map((x,i)=>el('div',{class:'card solution'},el('div',{class:'row'},el('span',{class:'rank'},passed(x)>=0?i+1:'–'),
    el('strong',{class:x.kind==='agent'?'agent':''},x.name),el('span',{class:'muted grow'},x.seconds!=null?'用时 '+x.seconds+' 秒':''),
    !x.code?el('span',{class:'muted'},x.status==='failed'?'没交：'+x.error:'没交'):!x.result?el('span',{class:'muted'},'测试中…')
      :x.result.error?el('span',{class:'fail'},x.result.error):el('span',{class:passed(x)===p.tests.length?'pass':'fail'},passed(x)+'/'+p.tests.length)),
    x.code?el('pre',{},x.code):null)));
}
watch(render);`
});

/** The code from a reply: the first ```js block, or the whole reply if it has none. */
function code(reply) {
  const text = String(reply || '');
  return ((/```(?:js|javascript)?[ \t]*\n([\s\S]*?)```/i.exec(text) || [])[1] ?? text).trim().slice(0, MAX_CODE);
}

function setup(app) {
  // solutions: {key, id, name, kind, status: writing|done|failed, code, seconds, error}
  const game = { round: 0, status: 'idle', problem: null, started: 0, solutions: [] };
  app.page(PAGE);

  app.state(() => ({
    round: game.round, status: game.status, problems: PROBLEMS.map(({ id, title }) => ({ id, title })),
    problem: game.problem && { title: game.problem.title, fn: game.problem.fn, sig: game.problem.sig, desc: game.problem.desc,
      samples: game.problem.tests.slice(0, SAMPLES), tests: game.status === 'done' ? game.problem.tests : [] },
    // Code is secret until the round ends.
    solutions: game.solutions.map(({ id, ...x }) => ({ ...x, code: game.status === 'done' ? x.code : '' })),
  }));

  app.route('POST', '/start', (member, body) => {
    if (!member.owner) throw refuse('只有群主可以开题', 403);
    if (game.status === 'coding') throw refuse('这一题还没结束');
    const problem = PROBLEMS.find((p) => p.id === body.problem) || PROBLEMS[Math.floor(Math.random() * PROBLEMS.length)];
    const agents = app.agents().filter((a) => a.online !== false).slice(0, AGENTS);
    Object.assign(game, { round: game.round + 1, status: 'coding', problem, started: Date.now(),
      solutions: agents.map((a) => ({ key: '', id: a.id, name: a.name, kind: 'agent', status: 'writing', code: '', seconds: null, error: '' })) });
    app.changed();
    const round = game.round;
    for (const solution of game.solutions) {
      app.ask(solution.id, `你在空间「${app.title}」的代码擂台上和其他 agent、人比赛写代码。题目「${problem.title}」：${problem.desc}\n` +
        `实现 ${problem.sig}，用纯 JavaScript（不能用网络、DOM 或外部库），先保证正确，再求简洁。样例：\n` +
        problem.tests.slice(0, SAMPLES).map(([args, want]) => `${problem.fn}(${args.map((x) => JSON.stringify(x)).join(', ')}) → ${JSON.stringify(want)}`).join('\n') +
        `\n只回复一个 \`\`\`js 代码块，里面是 ${problem.fn} 的完整实现。`)
        .then((reply) => {
          if (game.round !== round || game.status !== 'coding') return;
          Object.assign(solution, { status: 'done', code: code(reply), seconds: Math.round((Date.now() - game.started) / 1000), key: round + ':' + solution.id });
        })
        .catch((error) => { if (game.round === round) Object.assign(solution, { status: 'failed', error: error.message }); })
        .finally(() => app.changed());
    }
  });

  app.route('POST', '/submit', (member, body) => {
    if (game.status !== 'coding') throw refuse('现在不能提交');
    const text = String(body.code || '').trim().slice(0, MAX_CODE);
    if (!text.includes(game.problem.fn)) throw refuse('代码里要有 ' + game.problem.fn);
    let solution = game.solutions.find((x) => x.id === member.id);
    if (!solution) game.solutions.push(solution = { id: member.id, name: member.name, kind: 'human' });
    Object.assign(solution, { status: 'done', code: text, seconds: Math.round((Date.now() - game.started) / 1000), error: '',
      key: game.round + ':' + member.id + ':' + Date.now() });
    app.changed();
  });

  app.route('POST', '/finish', (member) => {
    if (!member.owner || game.status !== 'coding') throw refuse('现在不能结束', 403);
    for (const x of game.solutions) if (x.status === 'writing') Object.assign(x, { status: 'failed', error: '没在时间内交' });
    game.status = 'done';
    app.changed();
  });
}

if (require.main === module) run({ name: '代码擂台' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '代码擂台' };
