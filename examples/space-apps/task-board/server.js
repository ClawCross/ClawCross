#!/usr/bin/env node
/* 协作看板 · Task board — people share tasks; their own agents propose small, actionable plans.
 * 简介：共享待办、认领与进度；让自己的 Agent 拆解任务，确认后再加入看板。
 */
'use strict';
const {run,page,refuse}=require('../../../sdk/js/space-app.js');
const PAGE=page({
  appId: 'task-board',title:'协作看板',
  style:`.lanes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.task{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px;margin:8px 0;overflow-wrap:anywhere}.lane{min-width:0}.lane h2{margin:8px 0}.task .row{margin-top:10px}input.grow{width:100%}@media(max-width:620px){.lanes{grid-template-columns:1fr}}`,
  body:`<h1>协作看板</h1><p class="muted">把想做的事放上来，一起认领、推进。Agent 可以帮你拆成小任务。</p>
<div class="card"><div class="row"><input id="title" class="grow" maxlength="120" placeholder="写下一件要做的事"><button id="add">添加任务</button></div>
<textarea id="detail" rows="2" maxlength="800" placeholder="说明或验收标准（选填）" style="margin-top:8px"></textarea></div>
<div class="card"><div class="row"><input id="goal" class="grow" maxlength="500" placeholder="想让 Agent 帮你规划什么？"><select id="agent" aria-label="我的 Agent"></select><button id="plan">帮我拆解</button></div><div id="plans"></div></div>
<div id="board" class="lanes"></div>`,
  script:String.raw`
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);let roster='';
$('add').onclick=()=>act(()=>{const title=$('title').value,detail=$('detail').value;if(!title.trim())return api('task',{title,detail});$('title').value='';$('detail').value='';return api('task',{title,detail});});
$('plan').onclick=()=>act(()=>api('plan',{goal:$('goal').value,agent:$('agent').value}));
function render(s){
  const b=s.state,key=JSON.stringify(b.mine);
  if(key!==roster){const chosen=$('agent').value;fill($('agent'),...b.mine.map(a=>el('option',{value:a.id},a.name)));if(b.mine.some(a=>a.id===chosen))$('agent').value=chosen;roster=key;}
  $('plan').disabled=!!b.planning||!b.mine.length;$('plan').textContent=b.planning?'拆解中…':'帮我拆解';
  fill($('plans'),b.error?el('p',{class:'error'},b.error):null,...b.suggestions.map((p,i)=>el('div',{class:'task'},
    el('strong',{},p.title),el('p',{class:'muted pre'},p.detail),el('button',{class:'ghost',onclick:()=>act(()=>api('accept',{index:i}))},'加入看板'))));
  const labels={todo:'待办',doing:'进行中',done:'已完成'};
  fill($('board'),...Object.keys(labels).map(status=>el('section',{class:'lane'},el('h2',{},labels[status]+' · '+b.tasks.filter(t=>t.status===status).length),
    ...b.tasks.filter(t=>t.status===status).map(t=>el('article',{class:'task'},el('strong',{},t.title),t.detail?el('p',{class:'muted pre'},t.detail):null,
      el('p',{class:'muted'},'创建：'+t.creatorName+' · '+(t.assigneeName?'负责人：'+t.assigneeName:'待认领')),
      el('div',{class:'row'},!t.assignee?el('button',{class:'ghost',onclick:()=>act(()=>api('claim',{id:t.id}))},'我来做'):null,
        t.canEdit?el('button',{onclick:()=>act(()=>api('move',{id:t.id,status:status==='todo'?'doing':status==='doing'?'done':'todo'}))},status==='todo'?'开始':status==='doing'?'完成':'重新打开'):null,
        t.canDelete?el('button',{class:'ghost',onclick:()=>act(()=>api('remove',{id:t.id}))},'删除'):null))))));
}
watch(render);`
});
function setup(app){
  const tasks=[],jobs=new Map();let next=1;
  const job=id=>{if(!jobs.has(id))jobs.set(id,{planning:false,error:'',suggestions:[]});return jobs.get(id);};
  const add=(member,title,detail)=>{
    title=String(title||'').trim().slice(0,120);detail=String(detail||'').trim().slice(0,800);
    if(!title)throw refuse('写下任务名称');
    if(tasks.length>=100)throw refuse('看板最多保留 100 个任务，请先清理已完成的任务');
    tasks.push({id:String(next++),title,detail,status:'todo',creator:member.id,creatorName:member.name,assignee:'',assigneeName:''});
  };
  const find=id=>{const task=tasks.find(t=>t.id===id);if(!task)throw refuse('任务不存在',404);return task;};
  const edit=(member,t)=>{if(!member.owner&&t.creator!==member.id&&t.assignee!==member.id)throw refuse('只有创建者、负责人或群主可以修改',403);};
  app.page(PAGE);
  app.state(member=>({...job(member.id),mine:app.agentsOf(member.id).map(({id,name})=>({id,name})),
    tasks:tasks.map(t=>({...t,canEdit:member.owner||t.creator===member.id||t.assignee===member.id,canDelete:member.owner||t.creator===member.id}))}));
  app.route('POST','/task',(m,b)=>{add(m,b.title,b.detail);app.changed();});
  app.route('POST','/claim',(m,b)=>{const t=find(b.id);if(t.assignee&&t.assignee!==m.id)throw refuse('已经有人认领了',409);t.assignee=m.id;t.assigneeName=m.name;app.changed();});
  app.route('POST','/move',(m,b)=>{const t=find(b.id);edit(m,t);if(!['todo','doing','done'].includes(b.status))throw refuse('进度无效');t.status=b.status;app.changed();});
  app.route('POST','/remove',(m,b)=>{const t=find(b.id);if(!m.owner&&t.creator!==m.id)throw refuse('只有创建者或群主可以删除',403);tasks.splice(tasks.indexOf(t),1);app.changed();});
  app.route('POST','/plan',(m,b)=>{
    const agent=app.agentsOf(m.id).find(a=>a.id===b.agent),j=job(m.id),goal=String(b.goal||'').trim().slice(0,500);
    if(!agent)throw refuse('请选择自己的 Agent',403);if(!goal)throw refuse('写下需要规划的目标');if(j.planning)throw refuse('Agent 正在拆解');
    Object.assign(j,{planning:true,error:'',suggestions:[]});app.changed();
    app.askJSON(agent.id,'你是空间「'+app.title+'」的任务规划助手。'+m.name+' 希望：'+goal+
      '\n只提出计划，不执行任务。拆成最多 8 个具体小任务，每项写清验收标准。\n格式：{"tasks":[{"title":"任务名称","detail":"步骤和验收标准"}]}')
      .then(reply=>{j.suggestions=(Array.isArray(reply?.tasks)?reply.tasks:[]).slice(0,8).map(t=>({title:String(t?.title||'').trim().slice(0,120),detail:String(t?.detail||'').slice(0,800)})).filter(t=>t.title);if(!j.suggestions.length)throw new Error('Agent 没有返回有效任务');})
      .catch(error=>{j.error=error.message;}).finally(()=>{j.planning=false;app.changed();});
  });
  app.route('POST','/accept',(m,b)=>{const j=job(m.id),index=Number(b.index),p=Number.isInteger(index)?j.suggestions[index]:null;if(!p)throw refuse('建议不存在',404);add(m,p.title,p.detail);j.suggestions.splice(index,1);app.changed();});
}
if(require.main===module)run({name:'协作看板'},setup).catch(e=>{console.error(e.message);process.exit(1);});
module.exports={name:'协作看板',setup};
