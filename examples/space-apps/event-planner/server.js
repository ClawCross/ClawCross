#!/usr/bin/env node
/* 活动排期 · Event planner — people pick a slot; their agents help them compare the options.
 * 简介：一起选时间、报名和统计人数；自己的 Agent 帮忙比较候选安排。
 */
'use strict';
const {run,page,refuse}=require('../../../sdk/js/space-app.js');
const PAGE=page({title:'活动排期',
  style:`.slot{border:1px solid var(--line);background:var(--bg);border-radius:14px;padding:14px;margin:8px 0;overflow-wrap:anywhere}.slot.chosen{border-color:var(--accent)}.slot .row{margin-top:8px}input.grow{width:100%}.meter{height:5px;border-radius:99px;background:var(--line);overflow:hidden}.meter span{display:block;height:100%;background:var(--accent)}`,
  body:`<h1>活动排期</h1><p class="muted">周末聚会、读书会或线上讨论，看看大家哪段时间方便。</p>
<div id="setup" class="card" hidden><input id="title" style="width:100%" maxlength="120" placeholder="活动名称">
<textarea id="slots" rows="3" maxlength="800" style="margin-top:8px" placeholder="每行一个候选时间，请注明时区。例如：周六 14:00（北京时间）"></textarea>
<div class="row"><label>每个时间段最多人数 <input id="capacity" type="number" min="0" max="100" value="0" style="width:90px"></label><span class="muted">0 表示不限</span><button id="create">创建安排</button></div></div>
<div id="head"></div><div id="options"></div>
<div id="helper" class="card" hidden><p class="muted">让自己的 Agent 比较安排。补充信息和建议仅自己可见。</p>
<textarea id="note" rows="2" maxlength="500" placeholder="例如：我下午才有空，希望留出路上的时间"></textarea>
<div class="row" style="margin-top:8px"><select id="agent" aria-label="我的 Agent"></select><button id="ask">帮我选时间</button></div><div id="advice"></div></div>`,
  script:String.raw`
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);let roster='';
$('create').onclick=()=>act(()=>api('create',{title:$('title').value,slots:$('slots').value.split('\n'),capacity:Number($('capacity').value)}));
$('ask').onclick=()=>act(()=>api('advice',{agent:$('agent').value,note:$('note').value}));
function render(s){
  const w=s.state,event=w.event,key=JSON.stringify(w.mine);$('setup').hidden=!s.owner;$('helper').hidden=!event;
  if(key!==roster){const chosen=$('agent').value;fill($('agent'),...w.mine.map(a=>el('option',{value:a.id},a.name)));if(w.mine.some(a=>a.id===chosen))$('agent').value=chosen;roster=key;}
  $('ask').disabled=!!w.thinking||!w.mine.length;$('ask').textContent=w.thinking?'比较中…':'帮我选时间';
  fill($('head'),event?el('div',{class:'row'},el('h2',{class:'grow'},event.title),event.final?el('span',{class:'chip'},'已确定：'+event.slots.find(x=>x.id===event.final)?.label):el('span',{class:'chip'},'报名中')):el('p',{class:'muted'},'等群主创建一个活动。'));
  fill($('options'),...(event?event.slots:[]).map(slot=>el('article',{class:'slot'+(slot.mine?' chosen':'')},el('strong',{},slot.label),
    el('p',{class:'muted'},slot.names.length+' 人'+(event.capacity?' / '+event.capacity:'')+' · '+(slot.names.join('、')||'还没有人报名')),
    el('div',{class:'meter'},el('span',{style:'width:'+Math.min(100,slot.names.length/Math.max(event.capacity||s.members.filter(m=>!m.agent).length,1)*100)+'%'})),
    el('div',{class:'row'},el('button',{disabled:!!event.final||(!slot.mine&&!!event.capacity&&slot.names.length>=event.capacity),onclick:()=>act(()=>api('join',{slot:slot.mine?'':slot.id}))},slot.mine?'取消报名':'这个时间可以'),
      s.owner&&!event.final?el('button',{class:'ghost',onclick:()=>act(()=>api('finalize',{slot:slot.id}))},'确定这个时间'):null))));
  fill($('advice'),w.error?el('p',{class:'error'},w.error):null,w.advice?el('p',{class:'pre'},w.advice):null);
}
watch(render);`
});
function setup(app){
  let event=null,sequence=0;const choices=new Map(),advice=new Map();
  const mine=id=>{if(!advice.has(id))advice.set(id,{thinking:false,error:'',advice:''});return advice.get(id);};
  app.page(PAGE);
  app.state(member=>({...mine(member.id),mine:app.agentsOf(member.id).map(({id,name})=>({id,name})),event:event?{
    ...event,slots:event.slots.map(slot=>({...slot,mine:choices.get(member.id)===slot.id,names:app.humans().filter(m=>choices.get(m.id)===slot.id).map(m=>m.name)}))}:null}));
  app.route('POST','/create',(m,b)=>{
    if(!m.owner)throw refuse('只有群主可以创建安排',403);
    const title=String(b.title||'').trim().slice(0,120),labels=[...new Set((Array.isArray(b.slots)?b.slots:[]).map(x=>String(x).trim().slice(0,80)).filter(Boolean))].slice(0,8);
    const capacity=Number(b.capacity||0);if(!title||!labels.length)throw refuse('填写活动名称和候选时间');
    if(!Number.isInteger(capacity)||capacity<0||capacity>100)throw refuse('人数上限需要是 0–100 的整数');
    event={title,capacity,version:++sequence,final:'',slots:labels.map((label,i)=>({id:String(i+1),label}))};choices.clear();advice.clear();app.changed();
  });
  app.route('POST','/join',(m,b)=>{
    if(!event)throw refuse('还没有活动');if(event.final)throw refuse('时间已经确定',409);
    if(!b.slot){choices.delete(m.id);app.changed();return;}
    if(!event.slots.some(s=>s.id===b.slot))throw refuse('没有这个候选时间',404);
    const total=app.humans().filter(person=>person.id!==m.id&&choices.get(person.id)===b.slot).length;
    if(event.capacity&&total>=event.capacity)throw refuse('这个时间段已经报满',409);
    choices.set(m.id,b.slot);app.changed();
  });
  app.route('POST','/finalize',(m,b)=>{if(!m.owner)throw refuse('只有群主可以确定时间',403);if(!event?.slots.some(s=>s.id===b.slot))throw refuse('没有这个候选时间');event.final=b.slot;app.changed();});
  app.route('POST','/advice',(m,b)=>{
    if(!event)throw refuse('还没有活动');const agent=app.agentsOf(m.id).find(a=>a.id===b.agent),result=mine(m.id);
    if(!agent)throw refuse('请选择自己的 Agent',403);if(result.thinking)throw refuse('Agent 正在比较');
    const current=event.version,note=String(b.note||'').slice(0,500);
    Object.assign(result,{thinking:true,error:'',advice:''});app.changed();
    app.askJSON(agent.id,'你在空间「'+app.title+'」为「'+m.name+'」比较活动时间。活动：'+event.title+
      '\n候选：'+event.slots.map(s=>s.label).join('；')+'\n本人补充（私有）：'+note+
      '\n只根据提供的信息比较，不假定知道用户日历。一段话推荐一个时间并说明原因。\n格式：{"advice":"建议"}')
      .then(reply=>{if(event?.version!==current)return;const text=String(reply?.advice||'').trim().slice(0,1200);if(!text)throw new Error('没有收到有效建议');result.advice=text;})
      .catch(error=>{result.error=error.message;}).finally(()=>{result.thinking=false;app.changed();});
  });
}
if(require.main===module)run({name:'活动排期'},setup).catch(e=>{console.error(e.message);process.exit(1);});
module.exports={name:'活动排期',setup};
