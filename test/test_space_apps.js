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

scenarios['turtle-soup']=()=>scenario('turtle-soup',{
  replies(agent,prompt){
    if(prompt.includes('原创一道'))return JSON.stringify({title:'半夜的电话',surface:'他接起电话听了一秒，就哭着报了警。',bottom:'电话里是他自己的声音：录音机里循环着他求救的留言。'});
    if(prompt.includes('你在帮'))return JSON.stringify({question:'电话那头的人他认识吗？'});
    if(prompt.includes('给出推理'))return JSON.stringify({correct:prompt.includes('自己的声音'),comment:prompt.includes('自己的声音')?'完全正确':'还差关键一点'});
    return JSON.stringify({answer:prompt.includes('认识')?'是':'否',note:''});
  },
  async play({alice,bob,test}){
    await alice.locator('#host').selectOption({label:'Alpha'});
    await alice.getByRole('button',{name:'熬一锅汤'}).click();
    await expect(bob.getByText('他接起电话听了一秒，就哭着报了警。')).toBeVisible({timeout:10000});
    await expect(bob.locator('main')).not.toContainText('录音机');
    await bob.locator('#q').fill('他在打电话前受伤了吗？');await bob.getByRole('button',{name:'提问',exact:true}).click();
    await expect(alice.locator('.qa',{hasText:'受伤'}).locator('.ans')).toHaveText('否',{timeout:10000});
    await bob.getByRole('button',{name:'让 Beta 帮我问'}).click();
    await expect(alice.locator('.qa',{hasText:'Beta（替 Bob）'})).toContainText('电话那头的人他认识吗？',{timeout:10000});
    await expect(alice.locator('.qa',{hasText:'认识'}).locator('.ans')).toHaveText('是',{timeout:10000});
    await bob.locator('#guess').fill('他听到的是自己的声音');await bob.getByRole('button',{name:'揭晓我的推理'}).click();
    await expect(alice.getByText('Bob 猜中了！汤底：')).toBeVisible({timeout:10000});
    await expect(bob.getByText('录音机里循环着他求救的留言')).toBeVisible();
    expect(test.asked.filter(a=>a.agent==='Beta').length).toBe(1);
  }
});

scenarios.pictionary=()=>scenario('pictionary',{
  replies(agent,prompt){
    const word=/要画的词是「(.+?)」/.exec(prompt)?.[1];
    if(word)return JSON.stringify({svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300"><!--'+word+'--><circle cx="150" cy="150" r="90" fill="#f59f00"/></svg>'});
    return JSON.stringify({guess:/<!--(.+?)-->/.exec(prompt)?.[1]||'不知道'});
  },
  async play({alice,bob,test}){
    await alice.getByRole('button',{name:'下一轮'}).click();
    await expect(bob.locator('.canvas img')).toBeVisible({timeout:10000});
    await expect(bob.getByText('🤖 Beta：')).toBeVisible({timeout:10000});
    const word=/要画的词是「(.+?)」/.exec(test.asked[0].content)[1];
    await expect(bob.locator('main')).not.toContainText('答案：');
    await bob.locator('#guess').fill('不对的词');await bob.getByRole('button',{name:'猜',exact:true}).click();
    await bob.locator('#guess').fill(word);await bob.getByRole('button',{name:'猜',exact:true}).click();
    await expect(alice.locator('.guess',{hasText:'Bob'}).last()).toContainText('猜对了');
    await expect(alice.locator('.scores')).toContainText('Bob 2');
    await expect(alice.locator('.scores')).toContainText('Beta 2');
    await expect(alice.locator('.scores')).toContainText('Alpha 2');
    await alice.getByRole('button',{name:'揭晓并进入下一轮'}).click();
    await expect(bob.getByText('答案：'+word)).toBeVisible();
    await alice.getByRole('button',{name:'下一轮'}).click();
    await expect(bob.getByRole('heading',{name:/第 2 轮 · Beta/})).toBeVisible({timeout:10000});
  }
});

scenarios.undercover=()=>{
  let spy='',known;const found=new Promise(resolve=>known=resolve);
  return scenario('undercover',{
    async replies(agent,prompt){
      // Fixture agents vote once the test has worked out the undercover from everyone's words.
      if(prompt.includes('投票找出卧底')){await found;return JSON.stringify({vote:spy!==agent?spy:/选一个：(.+?)、/.exec(prompt)[1],reason:'描述最不一样'});}
      return JSON.stringify({say:agent+' 说：生活里常见'});
    },
    async play({alice,bob,test}){
      await alice.getByRole('button',{name:'开始一局'}).click();
      const word=async page=>(await page.locator('.word').textContent()).trim();
      await expect(alice.locator('.word')).toBeVisible();
      const words={Alice:await word(alice),Bob:await word(bob)};
      // Describe until the round is over; agents speak on their own.
      for(let i=0;i<40&&!(await alice.getByText('投票中').isVisible());i++){
        for(const [page,name] of [[alice,'Alice'],[bob,'Bob']])if(await page.locator('#say').isVisible()){await page.locator('#say').fill(name+' 觉得很普通');await page.getByRole('button',{name:'说',exact:true}).click();}
        await alice.waitForTimeout(250);
      }
      for(const a of test.asked)words[a.agent]=/你的词是「(.+?)」/.exec(a.content)[1];
      const counts={};for(const w of Object.values(words))counts[w]=(counts[w]||0)+1;
      spy=Object.keys(words).find(n=>counts[words[n]]===1);known();
      expect(Object.keys(words).length).toBe(4);
      for(const [page,name] of [[alice,'Alice'],[bob,'Bob']]){
        const target=name===spy?Object.keys(words).find(n=>n!==spy&&n!==name):spy;
        await page.locator('.player',{hasText:target}).getByRole('button',{name:'投他'}).click();
      }
      await expect(bob.getByRole('heading',{name:'平民获胜！卧底是 '+spy})).toBeVisible({timeout:15000});
      await expect(alice.locator('.player',{hasText:spy})).toHaveClass(/out/);
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
