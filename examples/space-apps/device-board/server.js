#!/usr/bin/env node
/* 设备看板 · Device Board — every device in the space reports its own health through the agent
 * running on it: disk, memory, load, uptime and anything worth a look (a nearly full disk, a dirty
 * work tree). Agents use read-only commands and share summaries only. Each person checks their own
 * device; the owner can check every device at once. One agent per device answers.
 * 简介：各设备上的 agent 用只读命令汇报本机状态。
 *
 *   node examples/space-apps/device-board/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse } = require('../../../sdk/js/space-app.js');

const text = (x, max) => String(x || '').trim().slice(0, max);

const PAGE = page({
  title: '设备看板',
  style: `.device h2{margin:0;font-size:15px}.metric{display:flex;gap:8px;font-size:14px;padding:1px 0}.metric span:first-child{color:var(--muted);width:3.5em;flex:none}
.alert{color:var(--bad);font-size:14px}.ok{color:var(--good)}`,
  body: `<h1>设备看板</h1><p class="muted">每台设备上的 agent 用只读命令看看自己的设备，只汇报摘要。你可以巡检自己的设备，群主可以一次巡检全部。</p>
<div class="row"><button id="mine">巡检我的设备</button><button id="all" class="ghost" hidden>全部巡检</button><span id="summary" class="muted grow"></span></div>
<div id="devices" class="grid" style="margin-top:10px"></div>`,
  script: `
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);
$('mine').onclick=()=>act(()=>api('check',{}));
$('all').onclick=()=>act(()=>api('check',{all:true}));
const METRICS=[['os','系统'],['disk','磁盘'],['memory','内存'],['load','负载'],['uptime','运行']];
function render(s){
  const d=s.state;
  $('all').hidden=!s.owner;$('mine').hidden=!d.mine;
  $('summary').textContent=d.devices.length+' 台设备 · '+d.devices.filter(x=>x.report&&x.report.alerts.length).length+' 台需要注意';
  fill($('devices'),...d.devices.map(x=>el('div',{class:'card device'},el('div',{class:'row'},el('h2',{class:'grow'},(x.report?(x.report.alerts.length?'⚠ ':'✓ '):'')+x.person),el('span',{class:'agent'},x.agent)),
    x.status==='checking'?el('p',{class:'muted'},'正在巡检…'):x.status==='failed'?el('p',{class:'error'},'没有汇报：'+x.error):!x.report?el('p',{class:'muted'},'还没有巡检'):[
      ...METRICS.filter(([k])=>x.report[k]).map(([k,t])=>el('div',{class:'metric'},el('span',{},t),el('span',{},x.report[k]))),
      ...x.report.alerts.map(a=>el('div',{class:'alert'},'⚠ '+a)),x.report.alerts.length?null:el('div',{class:'ok'},'一切正常'),
      x.report.notes?el('div',{class:'muted'},x.report.notes):null,el('div',{class:'muted'},'巡检于 '+x.at)])));
}
watch(render);`
});

function setup(app) {
  const boards = new Map();   // device -> {agent, agentName, status, error, report, at}
  app.page(PAGE);

  /** One agent per device: the space's devices and who sits at each. */
  const devices = () => {
    const seen = new Map();
    for (const agent of app.agents()) if (!seen.has(agent.device)) seen.set(agent.device, agent);
    return [...seen].map(([device, agent]) => ({ device, agent, person: app.ownerOf(agent.id) }));
  };

  app.state((member) => ({
    mine: app.agentsOf(member.id).length > 0,
    devices: devices().map(({ device, agent, person }) => {
      const b = boards.get(device) || {};
      return { person: person?.name || agent.name + ' 的设备', agent: agent.name, status: b.status || '', error: b.error || '', report: b.report || null, at: b.at || '' };
    }),
  }));

  function check({ device, agent }) {
    const board = boards.get(device) || {};
    if (board.status === 'checking') return;
    boards.set(device, { ...board, status: 'checking', error: '' });
    app.askJSON(agent.id, `你在空间「${app.title}」的设备看板上汇报你所在设备的状态。只运行只读命令（例如 df -h、free -m 或 vm_stat、uptime、` +
      `你工作目录里的 git status --short），不要修改、安装或删除任何东西；不要汇报文件内容、密钥、IP、用户名或路径细节。\n` +
      '格式：{"os":"系统和版本","disk":"磁盘剩余，例如 120G/500G","memory":"内存","load":"负载或 CPU","uptime":"已运行多久","alerts":["值得注意的问题，没有就空"],"notes":"一句话"}')
      .then((r) => {
        if (!r) throw new Error('回复不是看板格式');
        const report = { os: text(r.os, 60), disk: text(r.disk, 60), memory: text(r.memory, 60), load: text(r.load, 60), uptime: text(r.uptime, 60),
          alerts: (Array.isArray(r.alerts) ? r.alerts : []).map((a) => text(a, 120)).filter(Boolean).slice(0, 5), notes: text(r.notes, 200) };
        boards.set(device, { status: 'done', error: '', report, at: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) });
      })
      .catch((error) => boards.set(device, { ...boards.get(device), status: 'failed', error: error.message }))
      .finally(() => app.changed());
  }

  app.route('POST', '/check', (member, body) => {
    if (body.all && !member.owner) throw refuse('只有群主可以全部巡检', 403);
    const mine = new Set(app.agentsOf(member.id).map((a) => a.device));
    const targets = devices().filter((d) => d.agent.online !== false && (body.all || mine.has(d.device)));
    if (!targets.length) throw refuse(body.all ? '没有在线的设备' : '你的设备上没有在线的 agent');
    targets.forEach(check);
    app.changed();
  });
}

if (require.main === module) run({ name: '设备看板' }, setup).catch((error) => { console.error(error.message); process.exit(1); });
module.exports = { setup, name: '设备看板' };
