/* The example space apps, each in an isolated space (test/space-app-harness.js) with fixture agents
 * Alpha (Alice's device) and Beta (Bob's browser) and two real browser pages.
 *
 *   node test/test_space_apps.js [app ...]     # all apps, or the named ones
 *   CLAWCROSS_SPACE_TEST_HUB=<hub checkout> node test/test_space_apps.js   # against a built Hub
 */
const {chromium,expect}=require('@playwright/test');
const path=require('node:path');
const {run}=require('../sdk/js/space-app.js');
const {startSpace}=require('./space-app-harness.js');

const apps=path.resolve(__dirname,'../examples/space-apps');
const scenarios={};

/** Runs one app: fixture `replies(name, prompt)`, then `play({alice,bob,test})` in two browsers. */
async function scenario(dir,{replies,play}){
  const {setup,name}=require(path.join(apps,dir,'server.js'));
  const test=await startSpace({port:Number(process.env.CLAWCROSS_SPACE_TEST_PORT || 51240),
    replies:{alpha:prompt=>replies('Alpha',prompt),beta:prompt=>replies('Beta',prompt)}});
  let browser,app;
  try{
    app=await run({name},setup,{server:test.server,key:test.key});
    browser=await chromium.launch({headless:true,executablePath:process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'});
    const errors=[],pages={};
    for(const [member,width] of [['alice',1100],['bob',390]]){
      const page=await browser.newPage({viewport:{width,height:900}});
      page.on('pageerror',error=>errors.push(member+': '+error.message));
      await page.route(/tile\.openstreetmap\.org/,route=>route.fulfill({status:204}));
      await page.goto(await test.entrance(member));
      pages[member]=page;
    }
    try{await play({...pages,test,app});}
    catch(error){
      const shown=(await pages.alice.locator('main').innerText().catch(()=>'')).replace(/\s+/g,' ').slice(0,500);
      throw new Error(error.message.split('\n')[0]+' || page: '+shown+' || errors: '+errors.join('; ')+' || asked: '+test.asked.map(a=>a.agent).join(','));
    }
    for(const page of Object.values(pages))expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    return {asked:test.asked.map(x=>x.agent)};
  }finally{
    app?.space.close();
    if(browser)await browser.close();
    await test.stop();
  }
}

scenarios['map-scout']=()=>scenario('map-scout',{
  replies:(agent,prompt)=>JSON.stringify({places:agent==='Alpha'
    ?[{name:'安静咖啡馆',lat:31.2401,lng:121.4901,why:'靠窗座位多'},{name:'书店咖啡',lat:31.2380,lng:121.4850,why:'有插座'}]
    :[{name:'江边小馆',lat:31.2420,lng:121.4950,why:'看江景'}],note:agent==='Beta'?'周末人多':''}),
  async play({alice,bob,test}){
    await alice.route(/nominatim\.openstreetmap\.org/,route=>route.fulfill({json:[{lat:'31.2397',lon:'121.4998',display_name:'外滩, 黄浦区, 上海'}]}));
    await alice.locator('#q').fill('外滩');await alice.locator('#find').click();
    await alice.getByRole('button',{name:'外滩, 黄浦区, 上海'}).click();
    await expect(alice.locator('#center')).toContainText('外滩');
    await alice.locator('#task').fill('找适合安静办公的咖啡馆');
    await alice.locator('#send').click();
    await expect(bob.getByText('安静咖啡馆')).toBeVisible({timeout:15000});
    await expect(bob.getByText('江边小馆')).toBeVisible();
    await expect(bob.getByText('周末人多')).toBeVisible();
    expect(test.asked.length).toBe(2);
    expect(test.asked[0].content).toContain('外滩');
    await bob.locator('.place',{hasText:'江边小馆'}).getByRole('button').click();
    await expect(alice.locator('.place',{hasText:'江边小馆'}).getByRole('button')).toHaveText('👍 1');
    if(await alice.evaluate(()=>!!window.L))await expect(alice.locator('path.leaflet-interactive')).toHaveCount(3);
  }
});

scenarios['code-clinic']=()=>scenario('code-clinic',{
  replies:(agent,prompt)=>prompt.includes('逐条判断')
    ?JSON.stringify({verdicts:[{id:'F1',agree:agent==='Beta',reason:agent==='Beta'?'确实会注入':'参数已转义'},{id:'F2',agree:true,reason:'同意'}]})
    :JSON.stringify({findings:agent==='Alpha'
      ?[{severity:'high',line:2,title:'SQL 注入',detail:'字符串拼接查询',fix:'用参数化查询'}]
      :[{severity:'low',line:null,title:'缺少注释',detail:'函数用途不清',fix:'补一句说明'}]}),
  async play({alice,bob,test}){
    await alice.locator('#code').fill('def login(name):\n    db.execute("select * from users where name=\'" + name + "\'")');
    await alice.locator('#start').click();
    const injection=bob.locator('.card',{hasText:'SQL 注入'});
    await expect(injection).toContainText('Beta：确实会注入',{timeout:15000});
    await expect(bob.locator('.card',{hasText:'缺少注释'})).toContainText('Alpha：同意');
    await expect(alice.locator('.code .hit')).toHaveCount(1);
    await expect(bob.locator('.card .sev').first()).toHaveText('high');
    await injection.getByRole('button',{name:/👍/}).click();
    await expect(alice.locator('.card',{hasText:'SQL 注入'}).getByRole('button',{name:/👍/})).toHaveText('👍 1');
    expect(test.asked.map(a=>a.agent).sort()).toEqual(['Alpha','Alpha','Beta','Beta']);
  }
});

scenarios.debate=()=>{
  let coached=null;
  return scenario('debate',{
    async replies(agent,prompt){
      if(prompt.includes('评委'))return JSON.stringify({winner:'con',reason:'反方回应更直接'});
      // Beta takes its time on the opening so Bob can pass it a note before the rebuttal.
      if(agent==='Beta'&&prompt.includes('「立论」'))await new Promise(resolve=>{coached=resolve;setTimeout(resolve,8000);});
      return agent+' 的'+(/「(.+?)」发言/.exec(prompt)?.[1]||'')+'发言';
    },
    async play({alice,bob,test}){
      await expect(bob.locator('#setup')).toBeHidden();
      await alice.getByRole('button',{name:'远程办公比坐班更高效'}).click();
      await alice.locator('#judge').selectOption({label:'Alpha'});
      await alice.getByRole('button',{name:'开始辩论'}).click();
      await expect(bob.locator('#coach')).toBeVisible({timeout:10000});
      await expect(alice.locator('#coach')).toHaveCount(1);   // Alice coaches Alpha
      await bob.locator('#coach').fill('抓住通勤时间这一点');
      await bob.getByRole('button',{name:'递纸条'}).click();
      await expect(bob.getByText('待用纸条：抓住通勤时间这一点')).toBeVisible();
      coached?.();
      await expect(alice.locator('.speech')).toHaveCount(6,{timeout:15000});
      await expect(alice.locator('.speech.con').nth(1)).toContainText('Beta 的反驳发言');
      await expect(bob.getByText('评委 Alpha：反方胜')).toBeVisible({timeout:10000});
      await bob.getByRole('button',{name:/支持反方/}).click();
      await expect(alice.getByRole('button',{name:/支持反方/})).toHaveText('支持反方 1');
      const rebuttal=test.asked.find(a=>a.agent==='Beta'&&a.content.includes('「反驳」'));
      expect(rebuttal.content).toContain('抓住通勤时间这一点');
      expect(test.asked.find(a=>a.agent==='Beta'&&a.content.includes('「总结」')).content).not.toContain('抓住通勤时间');
    }
  });
};

scenarios.negotiation=()=>{
  const script={Beta:[{say:'九千，不能再少',offer:9000,accept:false},{say:'七千五成交吧',offer:7500,accept:false}],
    Alpha:[{say:'六千五最多了',offer:6500,accept:false},{say:'好，七千五',offer:null,accept:true}]};
  return scenario('negotiation',{
    replies:(agent,prompt)=>JSON.stringify(script[agent].shift()),
    async play({alice,bob,test}){
      await alice.getByRole('button',{name:'一台用了两年的索尼 A7M4 相机，带两个镜头'}).click();
      await alice.getByRole('button',{name:'摆上谈判桌'}).click();
      await alice.locator('#limit').fill('8000');await alice.locator('#wants').fill('要发票');
      await alice.getByRole('button',{name:'交给 agent'}).click();
      await bob.locator('#limit').fill('6000');await bob.getByRole('button',{name:'交给 agent'}).click();
      await expect(alice.getByRole('button',{name:'开始谈判'})).toBeEnabled();
      // Each side's limit stays with its own person until the end.
      await expect(bob.locator('main')).not.toContainText('8000');
      await expect(alice.locator('main')).not.toContainText('6000');
      await alice.getByRole('button',{name:'开始谈判'}).click();
      await expect(bob.getByText('成交价 7500')).toBeVisible({timeout:15000});
      await expect(alice.getByText('买方省下 500，卖方多赚 1500')).toBeVisible();
      expect(test.asked[0].agent).toBe('Beta');
      expect(test.asked.find(a=>a.agent==='Alpha').content).toContain('最高愿付 8000；要发票');
      expect(test.asked.find(a=>a.agent==='Beta').content).not.toContain('8000');
    }
  });
};

(async()=>{
  const chosen=process.argv.slice(2).length?process.argv.slice(2):Object.keys(scenarios);
  const results={};
  for(const name of chosen){
    const started=Date.now();
    try{results[name]={ok:true,...await scenarios[name](),seconds:Math.round((Date.now()-started)/1000)};}
    catch(error){results[name]={ok:false,error:String(error.message||error).split('\n').slice(0,6).join(' | ')};process.exitCode=1;}
    console.log(name,JSON.stringify(results[name]));
  }
})();
