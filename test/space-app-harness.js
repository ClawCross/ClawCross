/* A test space for space apps: an isolated group server — ClawCross's, or a built Hub when
 * CLAWCROSS_SPACE_TEST_HUB=<hub checkout> — with two members, each bringing one agent:
 * Alice (the owner, on a ClawCross device) with agent Alpha, and Bob (a browser member) with
 * agent Beta. Fixture agents answer the app's requests with a reply function per agent.
 */
const {expect}=require('@playwright/test');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');

const root=path.resolve(__dirname,'..');
const python=process.env.CLAWCROSS_TEST_PYTHON || path.join(os.homedir(),'.clawcross/venv/bin/python');

async function request(base,method,route,{token,key,invite,guest,body}={}){
  const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json',
    ...(token?{Authorization:'Bearer '+token}:{}),...(key?{'X-Group-Service-Key':key}:{}),
    ...(invite?{'X-Group-Invite':invite}:{}),...(guest?{'X-Guest-Token':guest}:{})},
    body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(route+': '+response.status+' '+JSON.stringify(data));
  return data;
}

/** ClawCross's group server: the relay alone, as `groups/server.py --relay-only` runs it. */
async function clawcross(port,home){
  const origin=`http://127.0.0.1:${port}`;
  const child=spawn(python,[path.join(root,'src/backend/groups/server.py'),'--relay-only','--port',String(port),'--data-dir',path.join(home,'groups')],
    {cwd:root,env:{...process.env,CLAWCROSS_HOME:home,PORT_GROUPS:String(port)},stdio:['ignore','ignore','inherit']});
  await expect.poll(async()=>{try{return(await fetch(origin+'/relay/health')).status;}catch{return 0;}},{timeout:20000}).toBe(200);
  const call=(method,route,options)=>request(origin,method,route,options);
  const serviceKey=fs.readFileSync(path.join(home,'data','group-service.key'),'utf8').trim();
  process.env.CLAWCROSS_SPACE_LOCAL_TOKEN=serviceKey;
  const alice=await call('POST','/relay/create',{key:serviceKey,body:{title:'测试空间',node_id:'host',user_id:'alice',display_name:'Alice'}});
  const {invite}=await call('POST','/relay/guest-invites',{token:alice.token,body:{}});
  const bob=await call('POST','/relay/guest/join',{body:{invite,name:'Bob',password:'bob-password'}});
  return {child,server:origin,relay:call,alice:alice.token,bob:bob.token,
    state:(token,after)=>call('GET','/relay/guest/state?after_id='+after,{token}),
    page:(entrance)=>origin+'/'+entrance,groupPage:null};
}

/** A built Hub (`next build` into .next-space-apps) serving /groups. */
async function hub(port,home){
  const dir=process.env.CLAWCROSS_SPACE_TEST_HUB,origin=`http://127.0.0.1:${port}`;
  const child=spawn(process.execPath,[path.join(dir,'node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port',String(port)],
    {cwd:dir,env:{...process.env,CLAWCROSSHUB_BUILD_DIR:process.env.CLAWCROSS_SPACE_TEST_HUB_BUILD || '.next-space-apps',
      CLAWCROSSHUB_DATA_DIR:home,NEXT_TELEMETRY_DISABLED:'1'},stdio:['ignore','ignore','inherit']});
  await expect.poll(async()=>{try{return(await fetch(origin+'/groups')).status;}catch{return 0;}},{timeout:30000}).toBe(200);
  const call=(method,route,options)=>request(origin+'/groups',method,route,options);
  process.env.CLAWCROSS_SPACE_LOCAL_TOKEN=fs.readFileSync(path.join(home,'space-app-local.key'),'utf8').trim();
  const alice=await call('POST','/group-guest-api/create',{body:{title:'测试空间'}});
  await call('POST','/group-guest-api/rename',{guest:alice.token,body:{name:'Alice'}});
  const bob=await call('POST','/group-guest-api/join',{invite:alice.invite,body:{name:'Bob',password:'bob-password'}});
  return {child,server:origin+'/groups',relay:call,alice:alice.token,bob:bob.token,
    state:(token,after)=>call('GET','/group-guest-api/state?after_id='+after,{guest:token}),
    page:(entrance)=>origin+'/groups/'+entrance,
    // The Hub's own group page, signed in as Bob through the identity a browser keeps.
    groupPage:{url:origin+'/groups/group-guest#'+alice.invite,storage:['clawcross-group-link:'+alice.invite,JSON.stringify({token:bob.token,name:'Bob'})]}};
}

/**
 * Starts the space. `replies.alpha` / `replies.beta` turn an app request's text into the agent's
 * reply (or null for no reply). Returns the app key, each member's page URL and the requests asked.
 */
async function startSpace({port=51236,replies}){
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'clawcross-space-'));
  const space=await (process.env.CLAWCROSS_SPACE_TEST_HUB?hub:clawcross)(port,home);
  const {relay,alice,bob}=space;
  await relay('POST','/relay/agents',{token:alice,body:{agent_id:'alpha',name:'Alpha',platform:'webot'}});
  await relay('POST','/relay/agents',{token:bob,body:{agent_id:'browser',name:'Beta',platform:'browser'}});
  const {key}=await relay('POST','/relay/app/key',{token:alice});
  const asked=[];let stopped=false;
  // Alpha runs on Alice's device: app requests arrive in its event stream. Beta runs in Bob's
  // browser: they arrive in the browser member's state. Both answer with send_to_group.
  const agents=(async()=>{
    let alpha=0,beta=-1;
    while(!stopped){
      const packet=await relay('POST','/relay/poll',{token:alice,body:{cursor:alpha}});
      for(const event of packet.events){alpha=event.id;if(event.kind==='app_request'){asked.push({agent:'Alpha',content:event.content});
        const text=await replies.alpha?.(event.content);
        if(text)await relay('POST','/relay/messages',{token:alice,body:{content:text,agent_id:'alpha'}});}}
      const state=await space.state(bob,beta);beta=state.cursor;
      for(const ask of state.requests||[]){asked.push({agent:'Beta',content:ask.content});
        const text=await replies.beta?.(ask.content);
        if(text)await relay('POST','/relay/messages',{token:bob,body:{content:text,agent_id:'browser'}});}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
  })().catch(error=>{if(!stopped)console.error('fixture agents stopped:',error.message);});
  return {
    server:space.server,key,asked,groupPage:space.groupPage,dataDir:path.join(home,'app-data'),
    card:async()=>(await space.state(alice,-1)).app,
    /** The page URL each member opens: their own signed entrance. */
    entrance:async(member)=>space.page((await relay('POST','/relay/app/session',{token:member==='bob'?bob:alice})).path),
    async stop(){stopped=true;await agents;space.child.kill('SIGTERM');await new Promise(resolve=>space.child.once('exit',resolve));
      fs.rmSync(home,{recursive:true,force:true});}
  };
}

module.exports={startSpace};
