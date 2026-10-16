/* Fast visual smoke check of every shipped app, with an isolated in-memory member roster.
 * No live groups, model calls or service restarts. node test/test_space_app_layouts.js
 * SPACE_APP_SCREENSHOTS=/tmp/app-previews writes representative desktop/phone previews.
 */
'use strict';
const {chromium,expect}=require('@playwright/test');
const {EventEmitter}=require('node:events');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {App}=require('../sdk/js/space-app.js');
const root=path.resolve(__dirname,'../examples/space-apps');
const members=[
  {id:'alice',name:'Alice',owner:true,device:'a'},
  {id:'bob',name:'Bob',device:'b'},
  {id:'alpha',name:'我的代码与写作伙伴 Alpha',agent:true,device:'a',online:true},
  {id:'beta',name:'地图与研究伙伴 Beta',agent:true,device:'b',online:true},
];
const preview=new Set(['task-board','event-planner','flashcards','writing-room','gomoku','creature-battle','debate','city-walk','idea-wall','story-relay','code-clinic','mock-interview']);

(async()=>{
  const apps=new Map();
  for(const dir of fs.readdirSync(root).sort()){
    if(process.argv.length>2&&!process.argv.slice(2).includes(dir))continue;
    const file=path.join(root,dir,'server.js');if(!fs.existsSync(file))continue;
    const template=require(file),space=new EventEmitter();
    space.members=async()=>({title:'周末的小伙伴',members});
    space.ask=async()=>{throw new Error('Layout check must not call an Agent');};
    const app=template.battle?template.battle(space):new App(space,template.name);
    if(template.battle)await app.refreshRoster();else{await app.refresh();await template.setup(app);}
    if(app.html)app.html=app.html.replace('render(snapshot);','render(snapshot);document.documentElement.dataset.ready="yes";');
    apps.set(dir,app);
  }
  const server=http.createServer((req,res)=>{
    const url=new URL(req.url,'http://test'),[,dir,who,...rest]=url.pathname.split('/');
    const app=apps.get(dir),member=members.find(m=>m.id===who);
    if(!app||!member){res.writeHead(404).end();return;}
    req.member=member;req.url='/'+rest.join('/')+url.search;
    // The battle has a separate page shell. Mark its first rendered state for the same check.
    if(dir==='creature-battle'&&!rest.join('/')){
      const html=require(path.join(root,dir,'server.js')).page().replace('game.update(client.snapshot);','game.update(client.snapshot);document.documentElement.dataset.ready="yes";');
      res.writeHead(200,{'Content-Type':'text/html;charset=utf-8'}).end(html);return;
    }
    void app.handle(req,res);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;const errors=[],results=[];
  try{
    browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME||'/usr/bin/google-chrome'});
    const origin='http://127.0.0.1:'+server.address().port;
    if(process.env.SPACE_APP_SCREENSHOTS)fs.mkdirSync(process.env.SPACE_APP_SCREENSHOTS,{recursive:true});
    for(const [dir] of apps){
      const viewports=dir==='creature-battle'?[[1100,'light','alice',760],[390,'light','alice',844],[390,'light','alice',650],[320,'dark','alice',568],[844,'light','alice',390],[1024,'light','alice',500],[768,'light','alice',600],[320,'dark','bob',640]]:[[1100,'light','alice',850],[390,'light','alice',850],[320,'dark','bob',850]];
      for(const [width,colorScheme,who,height] of viewports){
        const page=await browser.newPage({viewport:{width,height},colorScheme,reducedMotion:'reduce'});
        page.on('pageerror',e=>errors.push(dir+' '+width+': '+e.message));
        await page.route(/https?:\/\/(?!127\.0\.0\.1)/,route=>route.fulfill({status:200,json:[]}));
        await page.goto(origin+'/'+dir+'/'+who+'/');
        await page.waitForFunction(()=>document.documentElement.dataset.ready==='yes',null,{timeout:10000})
          .catch(async error=>{throw new Error(dir+' '+width+': '+error.message+' '+await page.locator('#app-error').textContent().catch(()=>''));});
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),dir+' '+width+' overflow').toBe(true);
        if(dir==='creature-battle'){
          if(height>=600)expect(await page.locator('#room-root').evaluate(node=>node.scrollHeight<=node.clientHeight+1),width+'x'+height+' fits one screen').toBe(true);
          for(const selector of ['[data-send]',...(who==='alice'?['[data-prepare]']:[])]){
            await expect.poll(()=>page.locator(selector).evaluate(node=>{node.scrollIntoView({block:'nearest'});const r=node.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.width>=30&&r.height>=22&&r.y>=0&&r.bottom<=innerHeight+1&&(node===hit||node.contains(hit));}),selector+' reachable at '+width+'x'+height).toBe(true);
          }
        }
        if(dir==='creature-battle'&&who==='alice'){
          const setup=await page.locator('.cc-room-stage>.cc-room-setup').boundingBox();
          const arena=await page.locator('.cc-room-stage').boundingBox();
          const creatures=await page.locator('.cc-room-fighters').boundingBox();
          expect(setup.y).toBeGreaterThanOrEqual(creatures.y+creatures.height-1);
          expect(setup.y+setup.height).toBeLessThanOrEqual(arena.y+arena.height);
        }
        // Opening optional panels must also fit. Includes battle rules, manual decks and histories.
        await page.locator('details').evaluateAll(nodes=>nodes.forEach(node=>{node.open=true;}));
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),dir+' '+width+' expanded overflow').toBe(true);
        if(dir==='creature-battle')await page.locator('details').evaluateAll(nodes=>nodes.forEach(node=>{node.open=false;}));
        if(process.env.SPACE_APP_SCREENSHOTS&&preview.has(dir)&&width!==320)
          await page.screenshot({path:path.join(process.env.SPACE_APP_SCREENSHOTS,dir+'-'+width+'-'+height+'.png'),fullPage:true});
        await page.close();
      }
      expect(errors).toEqual([]);results.push(dir);console.log('PASS '+dir);
    }
    expect(errors).toEqual([]);
    console.log(JSON.stringify({apps:results.length,viewports:[1100,390,320],themes:['light','dark'],javascriptErrors:errors.length}));
  }finally{
    await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
