/* A space app end to end: an isolated group server, the creature battle's app server connected with
 * the space's app key, two members' pages through the tunnel, and two agents on different devices
 * (a ClawCross device and a browser member) answering the app's requests with fixture replies.
 *
 *   node test/test_space_app_flow.js
 */
const {chromium,expect}=require('@playwright/test');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const Rooms=require('../examples/space-apps/creature-battle/room-sdk.js');
const {connect}=require('../sdk/js/clawcross-space.js');
const {battle}=require('../examples/space-apps/creature-battle/server.js');

const root=path.resolve(__dirname,'..');
const port=Number(process.env.CLAWCROSS_SPACE_TEST_PORT || 51236), base=`http://127.0.0.1:${port}`;
const python=process.env.CLAWCROSS_TEST_PYTHON || path.join(os.homedir(),'.clawcross/venv/bin/python');
const svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="orange"/></svg>';

async function call(method,route,{token,key,body}={}){
  const response=await fetch(base+route,{method,headers:{'Content-Type':'application/json',
    ...(token?{Authorization:'Bearer '+token}:{}),...(key?{'X-Group-Service-Key':key}:{})},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(route+': '+response.status+' '+JSON.stringify(data));
  return data;
}
/** A fixture agent: answers each command the app sends it with the packet the battle expects. */
function reply(content,name){
  const command=Rooms.parseEvent(content);
  if(!command)return `${name} 收到，准备战斗！`;   // a summoner-chat mention
  return Rooms.encodeEvent(command.kind==='avatar'
    ?{type:'avatar',room:command.room,id:command.id,name,elements:['normal'],moves:['喷射火焰','撞击','守住','自我再生'],svg}
    :{type:'move',room:command.room,id:command.id,turn:command.turn,move:'喷射火焰',narration:'火焰命中！'});
}

(async()=>{
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'clawcross-space-'));
  const env={...process.env,CLAWCROSS_HOME:home,PORT_GROUPS:String(port)};
  const server=spawn(python,[path.join(root,'src/backend/groups/server.py'),'--relay-only','--port',String(port),'--data-dir',path.join(home,'groups')],
    {cwd:root,env,stdio:['ignore','ignore','inherit']});
  let browser,space,stopped=false;
  const asked=[];
  try{
    await expect.poll(async()=>{try{return(await fetch(base+'/relay/health')).status;}catch{return 0;}},{timeout:20000}).toBe(200);
    const serviceKey=fs.readFileSync(path.join(home,'data','group-service.key'),'utf8').trim();
    const host=await call('POST','/relay/create',{key:serviceKey,body:{title:'精灵空间',node_id:'host',user_id:'alice',display_name:'Alice'}});
    const {invite}=await call('POST','/relay/guest-invites',{token:host.token,body:{}});
    const bob=await call('POST','/relay/guest/join',{body:{invite,name:'Bob',password:'bob-password'}});
    await call('POST','/relay/agents',{token:host.token,body:{agent_id:'alpha',name:'Alpha',platform:'webot'}});
    await call('POST','/relay/agents',{token:bob.token,body:{agent_id:'browser',name:'Beta',platform:'browser'}});
    const {key}=await call('POST','/relay/app/key',{token:host.token});

    // The owner's app server, connected out to the group server with the app key.
    space=await connect({server:base,key,name:'精灵对战'});
    const app=battle(space);await app.refreshRoster();await space.serve(app.handle);
    const card=await call('GET','/relay/group',{token:host.token});
    if(!card.app?.online)throw new Error('the space should show its app online: '+JSON.stringify(card.app));

    // Alpha runs on Alice's ClawCross: it receives app requests in its device's event stream.
    // Beta runs in Bob's browser: it receives them in the browser member's state.
    (async()=>{
      let alpha=0,beta=-1;
      while(!stopped){
        const packet=await call('POST','/relay/poll',{token:host.token,body:{cursor:alpha}});
        for(const event of packet.events){alpha=event.id;if(event.kind==='app_request'){asked.push('Alpha');
          await call('POST','/relay/messages',{token:host.token,body:{content:reply(event.content,'Amber'),agent_id:'alpha'}});}}
        const state=await call('GET','/relay/guest/state?after_id='+beta,{token:bob.token});beta=state.cursor;
        for(const request of state.requests||[]){asked.push('Beta');
          await call('POST','/relay/messages',{token:bob.token,body:{content:reply(request.content,'Ripple'),agent_id:'browser'}});}
        await new Promise(resolve=>setTimeout(resolve,300));
      }
    })().catch(error=>{if(!stopped)console.error('fixture agents stopped:',error.message);});

    browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'});
    const errors=[],pages=[];
    for(const [token,route,width] of [[host.token,'/relay/app/session',1100],[bob.token,'/relay/guest/app_session',390]]){
      const {path:site}=await call('POST',route,{token});
      const page=await browser.newPage({viewport:{width,height:844}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/'+site);pages.push(page);
    }
    const [alice,bobPage]=pages;
    // Opened directly, the site still runs at an opaque origin.
    expect(await alice.evaluate(()=>self.origin)).toBe('null');
    await expect(alice.locator('.cc-room-setup select').first()).toContainText('Alpha');
    await expect(bobPage.locator('.cc-room-setup')).toBeHidden();
    await alice.locator('.cc-room-setup select').first().selectOption({label:'Alpha · Alice'});
    await alice.locator('.cc-room-setup select').nth(1).selectOption({label:'Beta · Bob'});
    await alice.locator('[data-prepare]').click();
    await expect(alice.locator('.cc-room-image')).toHaveCount(2,{timeout:15000});
    await expect(bobPage.locator('.cc-room-image')).toHaveCount(2,{timeout:10000});
    await expect(bobPage.locator('[data-start]')).toBeHidden();
    await bobPage.locator('#text').fill('准备好了');
    await bobPage.locator('[data-chat]').click();
    await expect(alice.locator('#chat')).toContainText('准备好了',{timeout:10000});
    // The summoner chat: @Alpha brings Alice's agent into the conversation; plain chat asks nobody.
    const before=asked.length;
    await bobPage.locator('#summon button',{hasText:'@Alpha'}).click();
    await bobPage.locator('#text').pressSequentially('你的精灵怕不怕火？');
    await bobPage.locator('[data-chat]').click();
    await expect(alice.locator('#chat p.agent',{hasText:'Amber 收到，准备战斗！'})).toBeVisible({timeout:10000});
    await expect(bobPage.locator('#chat p.agent',{hasText:'Amber 收到'})).toBeVisible({timeout:10000});
    expect(asked.slice(before)).toEqual(['Alpha']);
    await alice.locator('[data-start]').click();
    await expect(alice.locator('[data-start]')).toBeHidden();
    await bobPage.locator('#text').fill('抢先出招');
    await expect(bobPage.locator('[data-command]')).toBeDisabled();
    await alice.locator('#text').fill('用喷射火焰进攻');
    await alice.locator('[data-command]').click();
    await expect(alice.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:15000});
    await expect(bobPage.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:10000});
    await bobPage.locator('[data-command]').click();
    await expect(alice.locator('.cc-room-hp').first()).toHaveText('76 / 100 HP',{timeout:15000});
    // The app enforces turns: Bob cannot act for Alice's agent through the app's own API.
    const forged=await bobPage.evaluate(()=>fetch('send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({content:'[ClawCrossRoom:v1]{"protocol":"clawcross.room/1","type":"start","room":"x","id":"y"}',mentions:[]})}).then(r=>r.status));
    if(forged!==409&&forged!==403)throw new Error('a member should not start the match: '+forged);
    expect(errors).toEqual([]);
    console.log(JSON.stringify({app_server:'connected',agents_asked:asked,shared_hp:76}));
  }finally{
    stopped=true;space?.close();
    if(browser)await browser.close();
    server.kill('SIGTERM');await new Promise(resolve=>server.once('exit',resolve));
    fs.rmSync(home,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
