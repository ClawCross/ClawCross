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
/** A fixture agent: answers each command the app sends it with the packet the battle expects. */
function reply(content,name){
  const command=Rooms.parseEvent(content);
  if(!command)return `${name} 收到，准备战斗！`;   // a summoner-chat mention
  return Rooms.encodeEvent(command.kind==='avatar'
    ?{type:'avatar',room:command.room,id:command.id,name,elements:['normal'],moves:['喷射火焰','撞击','守住','自我再生'],svg}
    :{type:'move',room:command.room,id:command.id,turn:command.turn,move:'喷射火焰',narration:'火焰命中！'});
}

(async()=>{
  const test=await startSpace({port:Number(process.env.CLAWCROSS_SPACE_TEST_PORT || 51236),
    replies:{alpha:content=>reply(content,'Amber'),beta:content=>reply(content,'Ripple')}});
  let browser,space;
  try{
    // The owner's app server, connected out to the group server with the app key.
    space=await connect({server:test.server,key:test.key,name:'精灵对战'});
    const app=battle(space);await app.refreshRoster();await space.serve(app.handle);
    const card=await test.card();
    if(!card?.online)throw new Error('the space should show its app online: '+JSON.stringify(card));

    browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'});
    const errors=[],pages=[];
    for(const [member,width] of [['alice',1100],['bob',390]]){
      const page=await browser.newPage({viewport:{width,height:844}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.goto(await test.entrance(member));pages.push(page);
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
    const before=test.asked.length;
    await bobPage.locator('#summon button',{hasText:'@Alpha'}).click();
    await bobPage.locator('#text').pressSequentially('你的精灵怕不怕火？');
    await bobPage.locator('[data-chat]').click();
    await expect(alice.locator('#chat p.agent',{hasText:'Amber 收到，准备战斗！'})).toBeVisible({timeout:10000});
    await expect(bobPage.locator('#chat p.agent',{hasText:'Amber 收到'})).toBeVisible({timeout:10000});
    expect(test.asked.slice(before).map(x=>x.agent)).toEqual(['Alpha']);
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
    if(test.groupPage){
      // On the Hub, Bob's own group page shows the app's site in place of the chat.
      const page=await browser.newPage({viewport:{width:390,height:844}});
      page.on('pageerror',error=>errors.push(error.message));
      await page.addInitScript(([key,value])=>{if(self.origin!=='null')localStorage.setItem(key,value);},test.groupPage.storage);
      // The Hub accepts its API calls from its canonical origin only; replay them from it.
      await page.route(/\/groups\/group-guest-api\//,async route=>route.fulfill({response:await route.fetch({headers:{...route.request().headers(),origin:'https://clawcross.net'}})}));
      await page.goto(test.groupPage.url);
      await expect(page.frameLocator('[data-room-surface]').locator('.cc-room-hp').nth(1)).toHaveText('76 / 100 HP',{timeout:15000});
      await expect(page.getByRole('log')).toBeHidden();
    }
    expect(errors).toEqual([]);
    console.log(JSON.stringify({group_server:process.env.CLAWCROSS_SPACE_TEST_HUB?'hub':'clawcross',app_server:'connected',
      agents_asked:test.asked.map(x=>x.agent),shared_hp:76}));
  }finally{
    space?.close();
    if(browser)await browser.close();
    await test.stop();
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
