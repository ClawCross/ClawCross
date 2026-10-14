#!/usr/bin/env node
/* 协作写作 · Writing room — people and their agents propose revisions; the owner chooses what to publish.
 * 简介：共写一份文稿，让各自的 Agent 提改写建议；群主确认采纳，保留版本记录。
 */
'use strict';
const {run,page,refuse}=require('../../../sdk/js/space-app.js');
const PAGE=page({
  appId: 'writing-room',title:'协作写作',
  style:`.document{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.proposal{border:1px solid var(--line);border-radius:14px;background:var(--bg);padding:14px;margin:10px 0}.proposal .row{margin-top:10px}textarea{line-height:1.6}input.grow{width:100%}.version{font-variant-numeric:tabular-nums}`,
  body:`<h1>协作写作</h1><p class="muted">一起打磨文章、公告或产品介绍。每次改写先成为建议，由群主采纳。</p>
<div id="editor" class="card" hidden><input id="title" style="width:100%" maxlength="120" placeholder="文稿标题">
<textarea id="text" rows="6" maxlength="6000" style="margin-top:8px" placeholder="写下初稿"></textarea>
<div class="row"><button id="save">发布当前稿</button><button id="reload" class="ghost">载入最新稿</button><span id="draft-version" class="muted"></span></div></div>
<div id="document"></div>
<div class="card"><div class="row"><input id="goal" class="grow" maxlength="500" placeholder="公开改写目标，例如：更简洁，适合第一次了解的读者"><select id="agent" aria-label="我的 Agent"></select><button id="rewrite">请 Agent 改写</button></div>
<details style="margin-top:12px"><summary class="muted">自己提交改写建议</summary><textarea id="manual" rows="4" maxlength="6000" placeholder="填写完整的建议稿"></textarea><button id="propose">提交建议</button></details><div id="working"></div></div>
<div id="suggestions"></div><div id="history"></div>`,
  script:String.raw`
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);let roster='',current=null,draftVersion=null,dirty=false;
$('text').oninput=$('title').oninput=()=>{dirty=true;};
function load(){if(!current)return;$('title').value=current.title;$('text').value=current.text;draftVersion=current.version;dirty=false;$('draft-version').textContent='草稿基于 v'+draftVersion;}
$('reload').onclick=load;
$('save').onclick=()=>act(async()=>{const result=await api('save',{title:$('title').value,text:$('text').value,base:draftVersion});draftVersion=result.version;dirty=false;$('draft-version').textContent='草稿基于 v'+draftVersion;});
$('rewrite').onclick=()=>act(()=>api('rewrite',{agent:$('agent').value,goal:$('goal').value}));
$('propose').onclick=()=>act(()=>{const text=$('manual').value;if(!text.trim())return api('propose',{text,base:current?.version});$('manual').value='';return api('propose',{text,base:current?.version});});
function render(s){
  const w=s.state;current=w.document;$('editor').hidden=!s.owner;
  if(s.owner&&(!dirty)&&(draftVersion!==current.version))load();
  $('draft-version').textContent='草稿基于 v'+draftVersion+(draftVersion!==current.version?' · 有新版本，请载入最新稿后修改':'');
  const key=JSON.stringify(w.mine);if(key!==roster){const selected=$('agent').value;fill($('agent'),...w.mine.map(a=>el('option',{value:a.id},a.name)));if(w.mine.some(a=>a.id===selected))$('agent').value=selected;roster=key;}
  $('rewrite').disabled=!!w.thinking||!w.mine.length||!current.text;$('rewrite').textContent=w.thinking?'改写中…':'请 Agent 改写';
  fill($('document'),el('article',{class:'card'},el('div',{class:'row'},el('h2',{class:'grow'},current.title||'还没有文稿'),el('span',{class:'chip version'},'v'+current.version)),el('p',{class:'document'},current.text||'等群主发布初稿。')));
  fill($('working'),w.error?el('p',{class:'error'},w.error):null);
  fill($('suggestions'),w.proposals.length?el('h2',{},'改写建议'):null,...w.proposals.map(p=>el('article',{class:'proposal'},el('strong',{},p.by+' · 基于 v'+p.base),p.goal?el('p',{class:'muted'},p.goal):null,
    el('p',{class:'document'},p.text),el('div',{class:'row'},s.owner?el('button',{disabled:p.base!==current.version,onclick:()=>act(()=>api('accept',{id:p.id}))},p.base===current.version?'采纳建议':'旧稿建议，请重新提交'):null,
      s.owner||p.person===s.me.id?el('button',{class:'ghost',onclick:()=>act(()=>api('remove',{id:p.id}))},s.owner?'移除':'撤回'):null))));
  fill($('history'),w.history.length?el('details',{},el('summary',{class:'muted'},'版本记录 · '+w.history.length),...w.history.map(h=>el('div',{class:'card'},el('strong',{},'v'+h.version+' · '+h.by),el('p',{class:'document'},h.text)))):null);
}
watch(render);`
});
function setup(app){
  let document={title:'',text:'',version:0,by:''};const proposals=[],history=[],jobs=new Map();let next=1;
  const job=id=>{if(!jobs.has(id))jobs.set(id,{thinking:false,error:''});return jobs.get(id);};
  const commit=(title,text,by)=>{if(document.version)history.unshift({...document});history.splice(10);document={title,text,by,version:document.version+1};app.changed();};
  const propose=(member,text,base,by,goal)=>{
    text=String(text||'').trim().slice(0,6000);if(!text)throw refuse('写下建议稿');
    if(proposals.length>=20)throw refuse('建议列表已满，请先清理');if(!Number.isInteger(base)||base<1||base>document.version)throw refuse('文稿版本无效');
    proposals.unshift({id:String(next++),person:member.id,by:by||member.name,text,base,goal:String(goal||'').slice(0,500)});app.changed();
  };
  app.page(PAGE);app.state(m=>({...job(m.id),document,proposals,history,mine:app.agentsOf(m.id).map(({id,name})=>({id,name}))}));
  app.route('POST','/save',(m,b)=>{
    if(!m.owner)throw refuse('只有群主可以发布文稿',403);
    const text=String(b.text||'').trim().slice(0,6000),title=String(b.title||'').trim().slice(0,120);
    if(!text||!title)throw refuse('填写标题和正文');if(b.base!==document.version)throw refuse('文稿已更新，请载入最新稿后再保存',409);commit(title,text,m.name);return {version:document.version};
  });
  app.route('POST','/propose',(m,b)=>propose(m,b.text,b.base));
  app.route('POST','/accept',(m,b)=>{
    if(!m.owner)throw refuse('只有群主可以采纳建议',403);const p=proposals.find(x=>x.id===b.id);
    if(!p)throw refuse('建议不存在',404);if(p.base!==document.version)throw refuse('这是旧版本的建议，请重新提交',409);
    commit(document.title,p.text,p.by);proposals.splice(proposals.indexOf(p),1);app.changed();
  });
  app.route('POST','/remove',(m,b)=>{const p=proposals.find(x=>x.id===b.id);if(!p)throw refuse('建议不存在',404);if(!m.owner&&p.person!==m.id)throw refuse('只能撤回自己的建议',403);proposals.splice(proposals.indexOf(p),1);app.changed();});
  app.route('POST','/rewrite',(m,b)=>{
    const agent=app.agentsOf(m.id).find(a=>a.id===b.agent),j=job(m.id),base=document.version,goal=String(b.goal||'').trim().slice(0,500);
    if(!agent)throw refuse('请选择自己的 Agent',403);if(!document.text)throw refuse('先发布初稿');if(!goal)throw refuse('写下改写目标');if(j.thinking)throw refuse('Agent 正在改写');
    j.thinking=true;j.error='';app.changed();
    app.askJSON(agent.id,'你在空间「'+app.title+'」替「'+m.name+'」改写共同文稿。标题：'+document.title+
      '\n初稿（v'+base+'）：\n'+document.text+'\n目标：'+goal+'\n保留有效事实，只输出完整的建议稿，不操作文件。\n格式：{"text":"完整建议稿"}')
      .then(reply=>{if(typeof reply?.text!=='string'||!reply.text.trim())throw new Error('Agent 没有返回有效建议稿');propose(m,reply.text,base,agent.name+'（替 '+m.name+'）',goal);})
      .catch(e=>{j.error=e.message;}).finally(()=>{j.thinking=false;app.changed();});
  });
}
if(require.main===module)run({name:'协作写作'},setup).catch(e=>{console.error(e.message);process.exit(1);});
module.exports={name:'协作写作',setup};
