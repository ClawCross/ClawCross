/* Real Hub HTTP APIs and two browser Agents; provider responses are local fixtures. */
const {chromium,request,expect}=require('@playwright/test');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const Rooms=require('../src/frontend/static/js/room-sdk.js');
const hub=process.env.CLAWCROSS_HUB_TEST_ROOT || path.resolve(__dirname,'../../ClawCrossHub');
const base='http://127.0.0.1:51213';
const svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="g"><stop stop-color="#fbbf24"/><stop offset="1" stop-color="#f97316"/></linearGradient></defs><ellipse cx="128" cy="223" rx="68" ry="12" fill="#000" opacity=".15"/><path d="M65 110 L55 30 L110 75 L170 65 L210 35 L196 118 Q226 190 170 216 Q100 230 65 188Z" fill="url(#g)" stroke="#75412e" stroke-width="5"/><ellipse cx="105" cy="125" rx="10" ry="14" fill="#17253e"/><ellipse cx="164" cy="125" rx="10" ry="14" fill="#17253e"/><path d="M115 156 Q136 174 157 156" fill="none" stroke="#75412e" stroke-width="5"/></svg>';
(async()=>{
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'clawcross-room-flow-'));
  const log=fs.openSync('/tmp/clawcross-room-flow-server.log','w');
  const server=spawn('/usr/bin/node',[path.join(hub,'node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port','51213'],
    {cwd:hub,env:{...process.env,CLAWCROSSHUB_BUILD_DIR:'.next-room-apps',CLAWCROSSHUB_DATA_DIR:data,NEXT_TELEMETRY_DISABLED:'1'},stdio:['ignore',log,log]});
  const api=await request.newContext({baseURL:base});
  let browser;
  try{
    await expect.poll(async()=>{try{return(await api.get('/')).status();}catch{return 0;}},{timeout:15000}).toBe(200);
    const call=async(action,body,token='',invite='')=>{
      const response=await api.post('/groups/group-guest-api/'+action,{headers:{'X-Guest-Token':token,'X-Group-Invite':invite},data:body});
      if(!response.ok())throw new Error(action+': '+await response.text());
      return response.json();
    };
    const created=await call('create',{title:'精灵对战 SDK 测试'});
    await call('rename',{name:'Host'},created.token);
    const friend=await call('join',{name:'Bob',password:'test-room-password'},'',created.invite);
    for(const [token,name] of [[created.token,'Alpha'],[friend.token,'Beta']]){
      const response=await api.post('/groups/relay/agents',{headers:{Authorization:'Bearer '+token},data:{agent_id:'browser',name,platform:'browser'}});
      if(!response.ok())throw new Error(await response.text());
    }
    const state=await(await api.get('/groups/group-guest-api/state',{headers:{'X-Guest-Token':created.token}})).json();
    await call('patch',{announcement:Rooms.encodeConfig(Rooms.createConfig('battle',{key:state.principal,name:state.name}))},created.token);
    const originProbe=await api.post('/groups/group-guest-api/info',{headers:{Origin:'https://clawcross.net','X-Group-Invite':created.invite},data:{}});
    console.log(JSON.stringify({origin_probe:originProbe.status(),body:await originProbe.json()}));
    browser=await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome'});
    const pages=[],errors=[],requestsSeen=[];
    for(const [token,name,person,width] of [[created.token,'Alpha','Host',1100],[friend.token,'Beta','Bob',390]]){
      const context=await browser.newContext({viewport:{width,height:844}});
      await context.route(base+'/groups/**',async route=>{
        // Replay canonical-origin requests through the test HTTP client. Chrome
        // restores its local Origin when continue() is used.
        const response=await route.fetch({headers:{...route.request().headers(),origin:'https://clawcross.net'}});
        await route.fulfill({response});
      });
      const page=await context.newPage();pages.push(page);
      page.on('pageerror',error=>errors.push(error.message));
      const answered=new Set();
      await page.route('https://room-model.invalid/**',async route=>{
        if(route.request().method()==='OPTIONS'){
          await route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Authorization, Content-Type'}});
          return;
        }
        const body=route.request().postDataJSON();
        const texts=(body.messages||[]).map(m=>typeof m.content==='string'?m.content:JSON.stringify(m.content)).join('\n');
        const current=await(await api.get('/groups/group-guest-api/state',{headers:{'X-Guest-Token':token}})).json();
        const config=Rooms.parseConfig(current.announcement);
        const target=config.players.find(p=>p.name===name);
        const matches=[];
        let position=texts.indexOf(Rooms.EVENT);
        while(position>=0){const event=Rooms.parseEvent(texts.slice(position));if(event?.type==='command'&&event.fighter===target?.key)matches.push(event);position=texts.indexOf(Rooms.EVENT,position+Rooms.EVENT.length);}
        const command=matches.at(-1);
        let content='NO_REPLY';
        if(command&&!answered.has(command.id)){
          answered.add(command.id);requestsSeen.push({agent:name,kind:command.kind});
          content=Rooms.encodeEvent(command.kind==='avatar'
            ?{type:'avatar',room:command.room,id:command.id,name:name==='Alpha'?'Amber':'Ripple',element:'normal',svg}
            :{type:'move',room:command.room,id:command.id,turn:command.turn,move:'喷射火焰',narration:'抓住破绽，火焰命中！'});
        }
        await route.fulfill({json:{choices:[{message:{role:'assistant',content},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:100}},
          headers:{'Access-Control-Allow-Origin':'*'}});
      });
      await page.addInitScript(({invite,group,token,name,person})=>{
        localStorage.setItem('clawcrosshub_locale','zh');
        localStorage.setItem('clawcross-group-link:'+invite,JSON.stringify({token,name:person}));
        localStorage.setItem('clawcross-agent:'+group,JSON.stringify({name,persona:'',source:'key',base_url:'https://room-model.invalid/v1',model:'room-test',api_key:'fixture-key',paused:false}));
      },{invite:created.invite,group:created.identity_key,token,name,person});
      await page.goto(base+'/groups/group-guest#'+created.invite);
    }
    const [host,friendPage]=pages;
    await expect(host.locator('.cc-room-setup select').first()).toContainText('Alpha');
    await host.locator('.cc-room-setup select').first().selectOption({label:'Alpha · Host'});
    await host.locator('.cc-room-setup select').nth(1).selectOption({label:'Beta · Bob'});
    await host.locator('[data-prepare]').click();
    try { await expect(host.locator('.cc-room-image')).toHaveCount(2,{timeout:15000}); }
    catch (error) {
      const current=await(await api.get('/groups/group-guest-api/state',{headers:{'X-Guest-Token':created.token}})).json();
      console.log(JSON.stringify({requestsSeen,errors,room_error:await host.locator('.cc-room-error').textContent(),
        messages:current.messages.map(m=>({sender:m.sender_name,packet:Rooms.parseEvent(m.content)?.type,head:m.content.slice(0,240)}))}));
      await host.getByRole('button',{name:'我的 Agent',exact:true}).click();
      console.log((await host.locator('[role=dialog]').innerText()).slice(0,1200));
      await host.screenshot({path:'/tmp/clawcross-room-battle-failure.png'});
      throw error;
    }
    await expect(friendPage.locator('.cc-room-image')).toHaveCount(2,{timeout:10000});
    await expect(friendPage.locator('.cc-room-setup')).toBeHidden();
    await expect(host.locator('.cc-room-setup')).toBeHidden();
    await host.locator('.cc-room-command textarea').fill('抓住机会，用喷射火焰进攻');
    await host.locator('[data-command]').click();
    await expect(host.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:15000});
    await expect(friendPage.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:10000});
    for(const page of pages)expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await host.screenshot({path:'/tmp/clawcross-room-battle-desktop.png'});
    await friendPage.screenshot({path:'/tmp/clawcross-room-battle-mobile.png'});
    console.log(JSON.stringify({two_browser_agents:true,svg_avatars:2,damage:24,shared_hp:76,requests:requestsSeen,page_errors:errors}));
  }finally{
    if(browser)await browser.close();
    await api.dispose();server.kill('SIGTERM');
    await new Promise(resolve=>server.once('exit',resolve));
    fs.closeSync(log);fs.rmSync(data,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
