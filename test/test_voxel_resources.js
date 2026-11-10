/* Private resource opt-in, real WebGL upload, unchanged saves and per-member isolation. */
const {chromium,expect}=require('@playwright/test');
const {startSpace}=require('./space-app-harness');
const {connect}=require('../sdk/js/clawcross-space');
const {createApp}=require('../examples/space-apps/opus-voxel/server');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 let fixture,space,browser;
 try{
  fixture=await startSpace({port:Number(process.env.CLAWCROSS_SPACE_TEST_PORT||51242),replies:{}});
  // CI uses our own art as a compatible test pack; optional local full-pack testing needs no redistribution.
  fs.mkdirSync(fixture.dataDir,{recursive:true});const compatible=path.join(fixture.dataDir,'test-assets.js');
  fs.writeFileSync(compatible,fs.readFileSync(path.join(__dirname,'../examples/space-apps/opus-voxel/procedural-assets.js')));
  const testAssets=process.env.CLAWCROSS_TEST_VOXEL_FULL_ASSETS||compatible;
  const start=async(options={testAssets})=>{space=await connect({server:fixture.server,key:fixture.key,dataDir:fixture.dataDir});await space.serve(createApp(space,options).handle);};
  await start();
  browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME||'/usr/bin/google-chrome',args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const errors=[],alice=await browser.newPage({viewport:{width:390,height:650}});
  alice.on('pageerror',e=>errors.push(e.message));
  // Menus and resource loading remain real; avoid repeatedly creating expensive title worlds.
  await alice.route('**/js/main.js',route=>route.fulfill({body:'',contentType:'application/javascript'}));
  await alice.goto(await fixture.entrance('alice'),{waitUntil:'domcontentloaded'});
  expect(await alice.evaluate(()=>VOXEL_PACK.mode)).toBe('original');
  await alice.evaluate(async()=>{await DB.putWorld({id:'keep-world',name:'保留世界',lastPlayed:1});window.game={state:'title'};const screen=new OptionsScreen({ui:{w:390,h:650},settings:{}},null);screen.layout();screen.widgets.find(w=>w.label==='材质与开发测试…').onClick();});
  await expect(alice.getByRole('switch')).toBeEnabled();await alice.getByRole('switch').check();
  await Promise.all([alice.waitForEvent('load'),alice.getByRole('button',{name:'应用并重新加载'}).click()]);
  expect(await alice.evaluate(()=>VOXEL_PACK.mode)).toBe('upstream');
  expect(await alice.evaluate(async()=>(await DB.listWorlds())[0].name)).toBe('保留世界');
  const textures=await alice.evaluate(async()=>{const renderer=new Renderer(document.querySelector('#gl'));await renderer.init();return{count:ASSETS.count,images:Object.keys(renderer.images).length,texture:!!renderer.texArray};});
  expect(textures.texture).toBe(true);expect(textures.images).toBeGreaterThan(50);
  const bob=await browser.newPage();await bob.route('**/js/main.js',route=>route.fulfill({body:'',contentType:'application/javascript'}));
  await bob.goto(await fixture.entrance('bob'),{waitUntil:'domcontentloaded'});
  expect(await bob.evaluate(()=>VOXEL_PACK.mode)).toBe('original');
  expect(await bob.evaluate(()=>fetch('resources',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{"testAssets":true}'}).then(r=>r.status))).toBe(403);
  await alice.setViewportSize({width:800,height:600});
  await alice.evaluate(()=>{window.game={state:'play'};VoxelResources.open();});
  await expect(alice.getByRole('switch')).toBeDisabled();await expect(alice.getByRole('button',{name:'应用并重新加载'})).toBeDisabled();
  space.close();await start();await alice.reload({waitUntil:'domcontentloaded'});expect(await alice.evaluate(()=>VOXEL_PACK.mode)).toBe('upstream');
  space.close();await start({testAssets:null});await alice.reload({waitUntil:'domcontentloaded'});expect(await alice.evaluate(()=>VOXEL_PACK.mode)).toBe('original');
  expect(await alice.evaluate(()=>fetch('resources',{method:'POST',headers:{'Content-Type':'text/plain'},body:'{"testAssets":true}'}).then(r=>r.status))).toBe(409);
  expect(await alice.evaluate(async()=>(await DB.listWorlds())[0].name)).toBe('保留世界');expect(errors).toEqual([]);
  console.log('PASS: settings toggle, complete texture WebGL upload, owner-only private opt-in, unchanged worlds, restart and missing-file fallback',JSON.stringify(textures));
 }finally{await browser?.close();space?.close();if(fixture)await fixture.stop();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
