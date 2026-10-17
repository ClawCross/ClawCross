/* Player-owned companions: AI creation in the backpack, human-only arena and host saves. */
const {chromium,expect}=require('@playwright/test');
const Rooms=require('../examples/space-apps/creature-battle/room-sdk');
const {art}=require('../examples/space-apps/creature-battle/garden');
const {connect}=require('../sdk/js/clawcross-space');
const {battle}=require('../examples/space-apps/creature-battle/server');
const {startSpace}=require('./space-app-harness');
const fs=require('node:fs'),path=require('node:path');
const effect='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 240"><circle r="24" fill="#ff5a1f"><animateMotion path="M110 110L490 110" dur=".8s" fill="freeze"/></circle></svg>';
function reply(content){const match=/\[ClawCrossGame:v1\]({[^\n]+})/.exec(content);if(!match)return '收到';const p=JSON.parse(match[1]);return '[ClawCrossGame:v1]'+JSON.stringify({request:p.request,creature:p.creature,screen:'bag',say:'原创精灵与技能动画已加入背包。',design:{name:'星屿灵狐',description:'由星光凝成的灵狐，喜欢探索浮岛，以温暖的光守护同伴。',elements:['normal'],svg:art('spark'),skills:[{name:'星焰',type:'normal',power:24},{name:'飞撞',type:'normal',power:14},{name:'光盾',type:'guard',power:0},{name:'暖流',type:'heal',power:22}],actions:{星焰:{motion:'jump',effect}}}});}
(async()=>{
 const test=await startSpace({port:Number(process.env.CLAWCROSS_SPACE_TEST_PORT||51236),replies:{alpha:reply,beta:reply}});
 let space,browser;
 const start=async()=>{space=await connect({server:test.server,key:test.key,name:'精灵对战',dataDir:test.dataDir});const app=battle(space);await app.refreshRoster();await space.serve(app.handle);};
 const shot=async(page,name)=>{if(process.env.SPACE_APP_SCREENSHOTS){fs.mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.SPACE_APP_SCREENSHOTS,name+'.png')});}};
 try{
  await start();browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME||'/usr/bin/google-chrome'});
  const errors=[],pages=[];
  for(const [person,width,height]of[['alice',1100,760],['bob',390,650]]){const page=await browser.newPage({viewport:{width,height}});page.on('pageerror',e=>errors.push(e.message));await page.goto(await test.entrance(person));pages.push(page);}
  const[alice,bob]=pages;
  for(const page of pages){await expect(page.getByRole('complementary',{name:'AI Agent 精灵设计师'})).toBeVisible();await expect(page.getByRole('region',{name:'游戏首页'})).toBeVisible();}
  await shot(alice,'home-ai-desktop');await shot(bob,'home-ai-mobile');
  for(const page of pages)await page.getByRole('button',{name:'收起设计师'}).click();
  expect(await alice.evaluate(()=>self.origin)).toBe('null');
  await alice.locator('.realm-nav [data-nav=bag]').click();await alice.getByRole('button',{name:'选择 萤尾',exact:true}).click();
  await alice.getByRole('button',{name:'训练',exact:true}).click();
  await alice.getByRole('button',{name:'力量训练 · 8星币',exact:true}).click();await expect(alice.locator('.garden-notice')).toContainText('经验 +35');
  await alice.getByRole('button',{name:'力量训练 · 8星币',exact:true}).click();await expect(alice.locator('.garden-hero')).toContainText('Lv.2');
  await alice.getByRole('button',{name:'伙伴',exact:true}).click();await alice.getByRole('button',{name:'新建精灵',exact:true}).click();
  await alice.locator('#text').fill('请创造新的星屿灵狐，包含介绍、四招和动画，加入背包。');await alice.locator('[data-send]').click();
  await expect(alice.locator('.garden-hero')).toContainText('星屿灵狐',{timeout:15000});await expect(alice.locator('.garden-description')).toContainText('由星光凝成');
  await expect(alice.locator('.garden-companion')).toHaveCount(2);await expect(alice.getByRole('log',{name:'设计对话'})).toContainText('已加入背包');
  const bobState=await bob.evaluate(()=>fetch('state').then(r=>r.json()));expect(bobState.snapshot.assistant.messages).toEqual([]);expect(bobState.snapshot.garden.creatures).toEqual([]);
  await alice.getByRole('button',{name:'收起设计师'}).click();await alice.locator('.garden-moves').getByRole('button',{name:'星焰',exact:true}).click();await expect(alice.locator('.garden-effect')).toHaveAttribute('data-own','1');
  await shot(alice,'backpack-ai-created');
  await bob.locator('.realm-nav [data-nav=bag]').click();await bob.getByRole('button',{name:'选择 潮团',exact:true}).click();
  for(const page of pages){await page.locator('.realm-nav [data-nav=arena]').click();await expect(page.locator('select[data-player]')).toHaveCount(0);await expect(page.locator('[data-agent-command]')).toBeHidden();}
  await alice.getByRole('combobox',{name:'选择出战精灵'}).selectOption({label:'星屿灵狐 · Lv.1'});
  await alice.getByRole('button',{name:'携带精灵参战',exact:true}).click();await bob.getByRole('button',{name:'携带精灵参战',exact:true}).click();
  await expect(alice.locator('.arena-ready-players')).toContainText('Bob · 潮团');await alice.getByRole('button',{name:'开始对战',exact:true}).click();
  await expect(alice.locator('.cc-room-hp').first()).toHaveText('100 / 100 HP');await expect(bob.locator('.cc-room-hp').nth(1)).toHaveText('100 / 100 HP');
  const forged=await bob.evaluate(()=>fetch('state').then(r=>r.json()).then(d=>{const c=ClawCrossRooms.parseConfig(d.snapshot.announcement);return fetch('send',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({content:ClawCrossRooms.encodeEvent({type:'command',kind:'skill',room:c.id,id:'forged',fighter:c.players[0].key,turn:1,move:'星焰'})})}).then(r=>r.status);}));expect(forged).toBe(403);
  await alice.getByRole('button',{name:'使用星焰',exact:true}).click();await expect(bob.locator('.cc-room-hp').nth(1)).toHaveText('64 / 100 HP');await expect(alice.locator('.cc-room-effect img')).toHaveAttribute('data-own','1');
  await bob.getByRole('button',{name:'使用水枪',exact:true}).click();await expect(alice.locator('.cc-room-hp').first()).toHaveText('70 / 100 HP');
  await alice.getByRole('button',{name:'使用星焰',exact:true}).click();await expect(bob.locator('.cc-room-hp').nth(1)).toHaveText('28 / 100 HP');
  expect(test.asked.map(x=>x.agent)).toEqual(['Alpha']);
  await alice.evaluate(()=>{window.stableCreature=document.querySelector('.cc-room-image');window.stableMove=document.querySelector('.cc-room-move');});
  await bob.evaluate(()=>fetch('send',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{"content":"准备好了","mentions":[]}'}));await expect(alice.locator('#chat')).toContainText('准备好了');
  expect(await alice.evaluate(()=>window.stableCreature===document.querySelector('.cc-room-image')&&window.stableMove===document.querySelector('.cc-room-move'))).toBe(true);
  for(const[width,height]of[[1100,760],[390,844],[390,650],[320,568],[844,390],[1024,500],[768,600]]){
   await alice.setViewportSize({width,height});expect(await alice.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   expect(await alice.evaluate(()=>{const root=document.querySelector('#room-root');return root.scrollHeight<=root.clientHeight+1;})).toBe(true);
   const buttons=alice.locator('.cc-room-move');for(let i=0;i<await buttons.count();i++)await expect.poll(()=>buttons.nth(i).evaluate(n=>{const r=n.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>=30&&r.height>=22&&r.bottom<=innerHeight+1&&(hit===n||n.contains(hit));})).toBe(true);
   await shot(alice,'arena-'+width+'x'+height);
  }
  await alice.setViewportSize({width:1100,height:760});await bob.getByRole('button',{name:'使用水枪',exact:true}).click();await expect(alice.locator('.cc-room-hp').first()).toHaveText('40 / 100 HP');
  await alice.getByRole('button',{name:'使用星焰',exact:true}).click();await expect(alice.locator('.cc-room-status')).toContainText('星屿灵狐 获胜');
  if(test.groupPage){const page=await browser.newPage({viewport:{width:390,height:844}});page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(([key,value])=>{if(self.origin!=='null')localStorage.setItem(key,value);},test.groupPage.storage);await page.route(/\/groups\/group-guest-api\//,async route=>route.fulfill({response:await route.fetch({headers:{...route.request().headers(),origin:'https://clawcross.net'}})}));await page.goto(test.groupPage.url);const frame=page.frameLocator('[data-room-surface]');await frame.locator('.realm-nav [data-nav=arena]').click();await expect(frame.locator('.cc-room-status')).toContainText('星屿灵狐 获胜');await page.getByRole('button',{name:/^(全屏|Fullscreen)$/}).click();await expect.poll(()=>page.evaluate(()=>!!document.fullscreenElement)).toBe(true);await page.getByRole('button',{name:/^(退出全屏|Exit fullscreen)$/}).click();}
  space.close();await start();await alice.reload();await expect(alice.getByRole('complementary',{name:'AI Agent 精灵设计师'})).toBeVisible();await alice.getByRole('button',{name:'收起设计师'}).click();await alice.locator('.realm-nav [data-nav=bag]').click();
  await expect(alice.locator('.garden-hero')).toContainText('星屿灵狐');await expect(alice.locator('.garden-hero')).toContainText('Lv.2');await expect(alice.locator('.garden-description')).toContainText('由星光凝成');await expect(alice.locator('.garden-companion')).toHaveCount(2);
  expect(errors).toEqual([]);console.log('PASS:',process.env.CLAWCROSS_SPACE_TEST_HUB?'Hub':'ClawCross','default AI sidebar, new companion with description/animation, private backpack, player-only arena, seven sizes, rewards and restart');
 }finally{space?.close();await browser?.close();await test.stop();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
