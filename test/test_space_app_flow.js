/* A space app end to end: an isolated group server, the creature battle's app server connected with
 * the space's app key, two members' pages through the tunnel, and two agents on different devices
 * (a ClawCross device and a browser member) answering the app's requests with fixture replies.
 *
 *   node test/test_space_app_flow.js
 */
const {chromium,expect}=require('@playwright/test');
const Rooms=require('../examples/space-apps/creature-battle/room-sdk.js');
const {connect}=require('../sdk/js/clawcross-space.js');
const {battle}=require('../examples/space-apps/creature-battle/server.js');
const {startSpace}=require('./space-app-harness.js');

const svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="orange"/></svg>';
// Amber draws its own fireball and jumps; Ripple gives no actions and gets the room's defaults.
const fireball="<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 240'><circle cx='110' cy='110' r='22' fill='#ff5a1f'><animateMotion path='M0 0 L380 0' dur='.8s' fill='freeze'/></circle></svg>";
/** A fixture agent: answers each command the app sends it with the packet the battle expects. */
function reply(content,name){
  const design=/\[ClawCrossGame:v1\]({[^\n]+})/.exec(content);
  if(design){const request=JSON.parse(design[1]);return '[ClawCrossGame:v1]'+JSON.stringify({request:request.request,creature:request.creature,screen:'bag',say:'星屿灵狐与技能动画已保存。',design:{name:'星屿灵狐',svg,skills:[{name:'星焰',type:'fire',power:24},{name:'飞撞',type:'normal',power:14},{name:'光盾',type:'guard',power:0},{name:'暖流',type:'heal',power:22}],actions:{星焰:{motion:'jump',effect:fireball}}}});}
  const command=Rooms.parseEvent(content);
  if(!command)return `${name} 收到，准备战斗！`+'我正在观察对手的动作，火焰可以照亮前路，也可以保护同伴。'.repeat(8);   // a summoner-chat mention
  return Rooms.encodeEvent(command.kind==='avatar'
    ?{type:'avatar',room:command.room,id:command.id,name,elements:['normal'],stats:{hp:100,attack:100,defense:100,speed:100},skills:[{name:'星焰',type:'fire',power:24},{name:'飞撞',type:'normal',power:14},{name:'光盾',type:'guard',power:0},{name:'暖流',type:'heal',power:22}],svg,
      ...(name==='Amber'?{actions:{'星焰':{motion:'jump',effect:fireball}}}:{})}
    :{type:'move',room:command.room,id:command.id,turn:command.turn,move:'星焰',narration:'火焰命中！'});
}

(async()=>{
  const test=await startSpace({port:Number(process.env.CLAWCROSS_SPACE_TEST_PORT || 51236),
    replies:{alpha:content=>reply(content,'Amber'),beta:content=>reply(content,'Ripple')}});
  let browser,space;
  try{
    // The owner's app server, connected out to the group server with the app key.
    space=await connect({server:test.server,key:test.key,name:'精灵对战',dataDir:test.dataDir});
    const app=battle(space);await app.refreshRoster();await space.serve(app.handle);
    const card=await test.card();
    if(!card?.online)throw new Error('the space should show its app online: '+JSON.stringify(card));

    browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'});
    const errors=[],pages=[];
    for(const [member,width,height] of [['alice',1100,760],['bob',390,650]]){
      const page=await browser.newPage({viewport:{width,height}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(await test.entrance(member));pages.push(page);
    }
    const [alice,bobPage]=pages;
    // Opened directly, the site still runs at an opaque origin.
    expect(await alice.evaluate(()=>self.origin)).toBe('null');
    await expect(alice.getByRole('region',{name:'游戏首页'})).toBeVisible();
    expect(await alice.locator('.realm-field-link').textContent()).toContain('星光草原');
    if(process.env.SPACE_APP_SCREENSHOTS){require('fs').mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});await alice.screenshot({path:require('path').join(process.env.SPACE_APP_SCREENSHOTS,'game-home-desktop.png')});}
    await alice.setViewportSize({width:390,height:650});
    if(process.env.SPACE_APP_SCREENSHOTS)await alice.screenshot({path:require('path').join(process.env.SPACE_APP_SCREENSHOTS,'game-home-mobile.png')});
    await alice.getByRole('button',{name:'开始冒险',exact:false}).click();
    await alice.getByRole('button',{name:'选择 萤尾',exact:true}).click();
    await expect(alice.locator('.garden-hero')).toContainText('Lv.1');
    await alice.getByRole('button',{name:'训练',exact:true}).click();
    await alice.getByRole('button',{name:'力量训练 · 8星币',exact:true}).click();
    await expect(alice.locator('.garden-notice')).toContainText('经验 +35');
    await alice.getByRole('button',{name:'力量训练 · 8星币',exact:true}).click();
    await expect(alice.locator('.garden-hero')).toContainText('Lv.2');
    if(process.env.SPACE_APP_SCREENSHOTS){require('fs').mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});await alice.screenshot({path:require('path').join(process.env.SPACE_APP_SCREENSHOTS,'garden-training.png')});}
    await alice.getByRole('button',{name:'探险',exact:true}).click();
    await alice.getByRole('button',{name:'和 萤尾 出发',exact:true}).click();
    await expect(alice.locator('.garden-opponent')).toHaveCount(2);
    await alice.getByRole('button',{name:/^火花 /}).click();
    await expect(alice.locator('.garden-battle-log')).toContainText('造成');
    await alice.getByRole('button',{name:'返回基地',exact:true}).click();
    await alice.getByRole('button',{name:'返回首页',exact:true}).click();
    await alice.locator('.realm-nav [data-nav=bag]').click();
    await alice.getByRole('button',{name:'请设计师改造',exact:true}).click();
    await alice.locator('#text').fill('设计星屿灵狐和跳起的星焰动画，打开背包。');
    await alice.locator('[data-send]').click();
    await expect(alice.locator('.garden-hero')).toContainText('星屿灵狐',{timeout:15000});
    await expect(alice.getByRole('log',{name:'设计对话'})).toContainText('星屿灵狐与技能动画已保存。');
    expect(await bobPage.evaluate(()=>fetch('state').then(r=>r.json()).then(d=>d.snapshot.assistant.messages))).toEqual([]);
    await alice.getByRole('button',{name:'收起设计师'}).click();
    await alice.locator('.garden-moves').getByRole('button',{name:'星焰',exact:true}).click();
    await expect(alice.locator('.garden-effect')).toHaveAttribute('data-own','1');
    if(process.env.SPACE_APP_SCREENSHOTS)await alice.screenshot({path:require('path').join(process.env.SPACE_APP_SCREENSHOTS,'game-bag-mobile.png')});
    await alice.locator('.realm-nav [data-nav=arena]').click();
    await bobPage.locator('.realm-nav [data-nav=arena]').click();
    await alice.setViewportSize({width:1100,height:760});
    await expect(alice.locator('.cc-room-setup select').first()).toContainText('Alpha');
    await expect(bobPage.locator('.cc-room-setup')).toBeHidden();
    await alice.locator('.cc-room-setup select').first().selectOption({label:'Alpha · Alice'});
    await alice.locator('.cc-room-setup select').nth(1).selectOption({label:'Beta · Bob'});
    await alice.locator('[data-prepare]').click();
    await expect(alice.locator('.cc-room-image')).toHaveCount(2,{timeout:15000});
    await expect(bobPage.locator('.cc-room-image')).toHaveCount(2,{timeout:10000});
    await expect(bobPage.locator('[data-start]')).toBeHidden();
    await alice.evaluate(()=>{window.stableCreature=document.querySelector('.cc-room-image');window.stableMove=document.querySelector('.cc-room-move');});
    for(const page of pages){await page.locator('.realm-designer-toggle').click();await page.getByRole('button',{name:'对战聊天',exact:true}).click();}
    await bobPage.locator('#text').fill('准备好了');
    await bobPage.locator('[data-send]').click();
    await expect(alice.locator('#chat')).toContainText('准备好了',{timeout:10000});
    expect(await alice.evaluate(()=>window.stableCreature===document.querySelector('.cc-room-image')&&window.stableMove===document.querySelector('.cc-room-move'))).toBe(true);
    // The summoner chat: @Alpha brings Alice's agent into the conversation; plain chat asks nobody.
    const before=test.asked.length;
    await expect(bobPage.locator('#bar button:visible')).toHaveCount(1);
    await bobPage.locator('#text').fill('@Alpha 你的精灵怕不怕火？');
    await bobPage.locator('[data-send]').click();
    await expect(alice.locator('#chat p.agent',{hasText:'Amber 收到，准备战斗！'})).toBeVisible({timeout:20000}).catch(async error=>{console.error('chat diagnostics',JSON.stringify({asked:test.asked.map(x=>x.agent),chat:await alice.locator('#chat').textContent(),hint:await bobPage.locator('#hint').textContent(),errors}));throw error;});
    await expect(bobPage.locator('#chat p.agent',{hasText:'Amber 收到'})).toBeVisible({timeout:10000});
    expect(test.asked.slice(before).map(x=>x.agent)).toEqual(['Alpha']);
    const bubble=alice.getByRole('complementary',{name:'Amber 的对话'});
    await expect(bubble).toBeVisible();const firstLine=await bubble.locator('p').textContent();
    await bubble.getByRole('button',{name:'下一页对话'}).click();
    await expect(bubble.locator('p')).not.toHaveText(firstLine);
    await bubble.getByRole('button',{name:'上一页对话'}).click();await expect(bubble.locator('p')).toHaveText(firstLine);
    await alice.locator('[data-start]').click();
    await expect(alice.locator('[data-start]')).toBeHidden();
    await expect(bobPage.locator('[data-agent-command]')).toBeDisabled();
    for(const page of pages)await page.getByRole('button',{name:'收起设计师'}).click();
    await alice.locator('[data-agent-command]').click();
    await alice.locator('#text').fill('用喷射火焰进攻');
    await alice.locator('[data-send]').click();
    await expect(alice.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:15000});
    // Amber's skill plays the effect and motion its agent designed with it.
    await expect(alice.locator('.cc-room-effect img')).toHaveAttribute('data-own','1');
    expect(decodeURIComponent(await alice.locator('.cc-room-effect img').getAttribute('src'))).toContain('#ff5a1f');
    await expect(alice.locator('.cc-room-image').first()).toHaveAttribute('data-motion','jump');
    expect(test.asked.find(x=>Rooms.parseEvent(x.content)?.kind==='avatar').content).toContain('motion 是精灵本体的动作');
    await expect(bobPage.locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:10000});
    await bobPage.locator('[data-agent-command]').click();
    await bobPage.locator('#text').fill('用星焰进攻');
    await bobPage.locator('[data-send]').click();
    await expect(alice.locator('.cc-room-hp').first()).toHaveText('76 / 100 HP',{timeout:15000});
    // Ripple brought no actions: the room's own effect, mirrored for the right side, and a lunge.
    await expect(alice.locator('.cc-room-effect img')).not.toHaveAttribute('data-own','1');
    await expect(alice.locator('.cc-room-image').nth(1)).toHaveAttribute('data-motion','lunge');
    // A human's skill click acts directly; no extra model call and no input-box round trip.
    const askedBeforeClick=test.asked.length;
    await alice.getByRole('button',{name:'使用星焰',exact:true}).click();
    await expect(bobPage.locator('.cc-room-hp').nth(1)).toHaveText('52 / 100 HP',{timeout:10000});
    expect(test.asked.length).toBe(askedBeforeClick);
    await expect(alice.locator('#text')).toHaveValue('');
    for(const page of pages)await page.getByRole('button',{name:'收起设计师'}).click();
    await alice.setViewportSize({width:390,height:650});
    for(const [width,height] of [[1100,760],[390,844],[390,650],[320,568],[844,390],[1024,500],[768,600]]){
      await alice.setViewportSize({width,height});
      expect(await alice.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await expect.poll(()=>alice.locator('.cc-room-bubble').evaluateAll(bubbles=>bubbles.every(bubble=>{
        const image=bubble.parentElement.querySelector('.cc-room-image');
        if(!image)return true;
        const text=bubble.getBoundingClientRect(),sprite=image.getBoundingClientRect();
        return text.bottom<=sprite.top;
      })),'speech stays clear of creatures at '+width+'x'+height).toBe(true);
      expect(await alice.evaluate(()=>{const root=document.querySelector('#room-root');return root.scrollHeight<=root.clientHeight+1;})).toBe(true);
      const moveBounds=await alice.locator('.cc-room-move').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width};}));
      expect(moveBounds[0].y).toBe(moveBounds[1].y);expect(moveBounds[2].y).toBeGreaterThan(moveBounds[0].y);
      const buttons=alice.locator('.cc-room-move,[data-agent-command]');
      if(process.env.SPACE_APP_SCREENSHOTS){const fs=require('node:fs'),path=require('node:path');fs.mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});await alice.screenshot({path:path.join(process.env.SPACE_APP_SCREENSHOTS,'battle-'+width+'x'+height+'.png')});}
      for(let i=0;i<await buttons.count();i++){
        await expect.poll(()=>buttons.nth(i).evaluate(node=>{node.scrollIntoView({block:'nearest'});const r=node.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>=30&&r.height>=22&&r.y>=0&&r.bottom<=innerHeight+1&&(node===hit||node.contains(hit));}),'button reachable at '+width+'x'+height).toBe(true);
      }
    }
    await alice.setViewportSize({width:1100,height:760});
    if(process.env.SPACE_APP_SCREENSHOTS){
      require('node:fs').mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});
      for(const [i,page] of pages.entries())await page.screenshot({path:require('node:path').join(process.env.SPACE_APP_SCREENSHOTS,'battle-played-'+i+'.png'),fullPage:true});
    }
    // The app enforces turns: Bob cannot act for Alice's agent through the app's own API.
    const forged=await bobPage.evaluate(()=>fetch('send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({content:'[ClawCrossRoom:v1]{"protocol":"clawcross.room/1","type":"start","room":"x","id":"y"}',mentions:[]})}).then(r=>r.status));
    if(forged!==409&&forged!==403)throw new Error('a member should not start the match: '+forged);
    if(test.groupPage){
      // On the Hub, Bob's own group page shows the app's site in place of the chat.
      const page=await browser.newPage({viewport:{width:390,height:844}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(([key,value])=>{if(self.origin!=='null')localStorage.setItem(key,value);},test.groupPage.storage);
      // The Hub accepts its API calls from its canonical origin only; replay them from it.
      await page.route(/\/groups\/group-guest-api\//,async route=>route.fulfill({response:await route.fetch({headers:{...route.request().headers(),origin:'https://clawcross.net'}})}));
      await page.goto(test.groupPage.url);
      await page.frameLocator('[data-room-surface]').locator('.realm-nav [data-nav=arena]').click();
      await expect(page.frameLocator('[data-room-surface]').locator('.cc-room-hp').nth(1)).toHaveText('52 / 100 HP',{timeout:15000});
      const frame=page.locator('[data-room-surface]');const beforeHeight=(await frame.boundingBox()).height;
      await page.getByRole('button',{name:/^(全屏|Fullscreen)$/}).click();
      await expect.poll(()=>page.evaluate(()=>!!document.fullscreenElement)).toBe(true);
      expect((await frame.boundingBox()).height).toBeGreaterThan(beforeHeight);
      const full=await frame.boundingBox();expect(full.x).toBe(0);expect(full.y).toBe(0);expect(full.width).toBe(page.viewportSize().width);expect(full.height).toBe(page.viewportSize().height);
      await page.getByRole('button',{name:/^(退出全屏|Exit fullscreen)$/}).click();
      await expect(page.getByRole('log')).toBeHidden();
    }
    // A new app process restores both the arena and each member's companions.
    space.close();
    space=await connect({server:test.server,key:test.key,name:'精灵对战',dataDir:test.dataDir});
    const restored=battle(space);await restored.refreshRoster();await space.serve(restored.handle);
    await alice.reload();
    await expect(alice.locator('.cc-room-hp').nth(1)).toHaveText('52 / 100 HP');
    await alice.locator('.realm-nav [data-nav=bag]').click();
    await expect(alice.locator('.garden-hero')).toContainText('Lv.2');
    await expect(alice.locator('.garden-companion')).toHaveCount(2);
    expect(errors).toEqual([]);
    console.log(JSON.stringify({group_server:process.env.CLAWCROSS_SPACE_TEST_HUB?'hub':'clawcross',app_server:'connected',
      agents_asked:test.asked.map(x=>x.agent),shared_hp:76}));
  }finally{
    space?.close();
    if(browser)await browser.close();
    await test.stop();
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
