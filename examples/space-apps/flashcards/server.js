#!/usr/bin/env node
/* 闪卡学堂 · Flashcards — a shared deck, private learning progress, and explanations from your own agent.
 * 简介：共建知识卡片，翻面复习；各自记录学习进度，让自己的 Agent 讲解难点。
 */
'use strict';
const {run,page,refuse}=require('../../../sdk/js/space-app.js');
const PAGE=page({
  appId: 'flashcards',title:'闪卡学堂',
  style:`.study{text-align:center;padding:28px 18px;min-height:210px}.question{font-size:22px;font-weight:600;overflow-wrap:anywhere}.answer{margin:18px 0;white-space:pre-wrap;overflow-wrap:anywhere}.study .row{justify-content:center}.progress{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}.progress .chip{padding:5px 10px}input.grow{width:100%}`,
  body:`<h1>闪卡学堂</h1><p class="muted">共享一副卡片，各自按自己的节奏复习。翻面后再记录是否记住。</p>
<div id="builder" class="card" hidden><div class="row"><input id="topic" class="grow" maxlength="120" placeholder="主题，例如：前端基础"><select id="maker" aria-label="制卡 Agent"></select><button id="generate">Agent 制卡</button></div>
<details style="margin-top:10px"><summary class="muted">自己填写卡片</summary><textarea id="manual" rows="4" maxlength="10000" placeholder="每行：问题 | 答案"></textarea><button id="save">保存卡片</button></details></div>
<div id="heading"></div><div id="card"></div>
<div class="card"><div class="row"><select id="helper" aria-label="讲解 Agent"></select><button id="explain">帮我讲解</button></div><div id="explanation"></div></div><div id="progress" class="progress"></div>`,
  script:String.raw`
const {api,watch,act,el,fill}=App,$=id=>document.getElementById(id);let roster='';
$('generate').onclick=()=>act(()=>api('generate',{topic:$('topic').value,agent:$('maker').value}));
$('save').onclick=()=>act(()=>api('deck',{topic:$('topic').value,text:$('manual').value}));
$('explain').onclick=()=>act(()=>api('explain',{agent:$('helper').value}));
function render(s){
  const d=s.state; $('builder').hidden=!s.owner;const key=JSON.stringify(d.mine);
  if(key!==roster){for(const id of ['maker','helper']){const selected=$(id).value;fill($(id),...d.mine.map(a=>el('option',{value:a.id},a.name)));if(d.mine.some(a=>a.id===selected))$(id).value=selected;}roster=key;}
  $('generate').disabled=!!d.generating||!d.mine.length;$('generate').textContent=d.generating?'制卡中…':'Agent 制卡';
  $('explain').disabled=!!d.explaining||!d.mine.length||!d.card;$('explain').textContent=d.explaining?'讲解中…':'帮我讲解';
  fill($('heading'),d.topic?el('h2',{},d.topic+' · '+d.count+' 张'):el('p',{class:'muted'},'等群主创建一副卡片。'),d.error?el('p',{class:'error'},d.error):null);
  fill($('card'),d.card?el('section',{class:'card study'},el('div',{class:'muted'},'第 '+(d.index+1)+' 张 · 已记住 '+d.known+'/'+d.count),
    el('p',{class:'question'},d.card.question),d.revealed?el('p',{class:'answer'},d.card.answer):null,
    el('div',{class:'row'},d.revealed?[el('button',{onclick:()=>act(()=>api('rate',{rating:'known'}))},'记住了'),
      el('button',{class:'ghost',onclick:()=>act(()=>api('rate',{rating:'again'}))},'再练一次')]:el('button',{onclick:()=>act(()=>api('reveal',{}))},'翻面看答案'))):
      d.count?el('section',{class:'card study'},el('h2',{},'这一轮都记住了'),el('button',{onclick:()=>act(()=>api('reset',{}))},'重新复习')):null);
  fill($('explanation'),d.explanation?el('p',{class:'pre'},d.explanation):null,d.explainError?el('p',{class:'error'},d.explainError):null);
  fill($('progress'),...d.progress.map(p=>el('span',{class:'chip'},p.name+' · '+p.known+'/'+d.count)));
}
watch(render);`
});
function setup(app){
  let cards=[],topic='',version=0,generating=false,error='';const learners=new Map();
  const learner=id=>{let p=learners.get(id);if(!p||p.version!==version){p={version,index:0,marks:new Map(),revealed:false,explanation:'',explainError:'',explaining:false};learners.set(id,p);}return p;};
  const tidy=rows=>(Array.isArray(rows)?rows:[]).slice(0,40).map(c=>({question:String(c?.question||'').trim().slice(0,200),answer:String(c?.answer||'').trim().slice(0,800)})).filter(c=>c.question&&c.answer);
  const replace=(name,rows)=>{const next=tidy(rows);if(!next.length)throw refuse('至少填写一张有问题和答案的卡片');cards=next;topic=name||'知识卡片';version++;learners.clear();app.changed();};
  app.page(PAGE);
  app.state(m=>{
    const p=learner(m.id),known=[...p.marks.values()].filter(Boolean).length,current=known<cards.length?cards[p.index]:null;
    return {topic,count:cards.length,generating,error,index:p.index,known,revealed:p.revealed,
      card:current?{question:current.question,...(p.revealed?{answer:current.answer}:{})}:null,
      explanation:p.explanation,explaining:p.explaining,explainError:p.explainError,
      mine:app.agentsOf(m.id).map(({id,name})=>({id,name})),progress:app.humans().map(person=>({name:person.name,known:[...learner(person.id).marks.values()].filter(Boolean).length}))};
  });
  app.route('POST','/deck',(m,b)=>{if(!m.owner)throw refuse('只有群主可以创建卡片',403);if(generating)throw refuse('Agent 正在制卡');
    const rows=String(b.text||'').slice(0,10000).split('\n').map(line=>{const at=line.indexOf('|');return at<0?null:{question:line.slice(0,at),answer:line.slice(at+1)};});
    replace(String(b.topic||'').trim().slice(0,120),rows);
  });
  app.route('POST','/generate',(m,b)=>{
    if(!m.owner)throw refuse('只有群主可以制卡',403);const agent=app.agentsOf(m.id).find(a=>a.id===b.agent),name=String(b.topic||'').trim().slice(0,120);
    if(!agent)throw refuse('请选择自己的 Agent',403);if(!name)throw refuse('填写主题');if(generating)throw refuse('Agent 正在制卡');
    generating=true;error='';app.changed();
    app.askJSON(agent.id,'你在空间「'+app.title+'」替「'+m.name+'」制作主题为「'+name+'」的学习卡片。生成最多 8 张，问题具体，答案简洁准确，避开含糊和重复问题。\n格式：{"cards":[{"question":"问题","answer":"答案"}]}')
      .then(reply=>replace(name,(Array.isArray(reply?.cards)?reply.cards:[]).slice(0,8)))
      .catch(e=>{error=e.message;}).finally(()=>{generating=false;app.changed();});
  });
  app.route('POST','/reveal',(m)=>{if(!cards.length)throw refuse('还没有卡片');learner(m.id).revealed=true;app.changed();});
  app.route('POST','/rate',(m,b)=>{
    const p=learner(m.id);if(!cards.length||!p.revealed)throw refuse('先翻面看答案');
    if(!['known','again'].includes(b.rating))throw refuse('学习记录无效');
    p.marks.set(p.index,b.rating==='known');for(let step=1;step<=cards.length;step++){const at=(p.index+step)%cards.length;if(!p.marks.get(at)){p.index=at;break;}}
    p.revealed=false;p.explanation='';p.explainError='';app.changed();
  });
  app.route('POST','/reset',(m)=>{learners.delete(m.id);app.changed();});
  app.route('POST','/explain',(m,b)=>{
    const p=learner(m.id),card=cards[p.index],agent=app.agentsOf(m.id).find(a=>a.id===b.agent);
    if(!agent)throw refuse('请选择自己的 Agent',403);if(!card)throw refuse('还没有卡片');if(p.explaining)throw refuse('Agent 正在讲解');
    p.explaining=true;p.explanation='';p.explainError='';const revision=version,index=p.index;app.changed();
    app.askJSON(agent.id,'你是空间「'+app.title+'」里「'+m.name+'」的学习伙伴。请讲解这张卡片，给一个简单例子帮助记忆。\n问题：'+card.question+'\n答案：'+card.answer+'\n格式：{"explanation":"讲解和例子"}')
      .then(reply=>{if(version!==revision||learners.get(m.id)!==p||p.index!==index)return;const text=String(reply?.explanation||'').trim().slice(0,1600);if(!text)throw new Error('没有收到有效讲解');p.explanation=text;})
      .catch(e=>{p.explainError=e.message;}).finally(()=>{p.explaining=false;app.changed();});
  });
}
if(require.main===module)run({name:'闪卡学堂'},setup).catch(e=>{console.error(e.message);process.exit(1);});
module.exports={name:'闪卡学堂',setup};
