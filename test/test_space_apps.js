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
      const notes=await Promise.all(Object.entries(pages).map(async([m,p])=>m+': '+await p.locator('#app-error').textContent().catch(()=>'')));
      throw new Error(error.message.split('\n').filter((line,i)=>!i||/^(Locator|Expected|Received)/.test(line.trim())).join(' ').replace(/\x1b\[\d+m/g,'')+' || page: '+shown+' || errors: '+errors.join('; ')+' || shown errors: '+notes.join('; ')+' || asked: '+test.asked.map(a=>a.agent).join(','));
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
      if(agent==='Beta'&&prompt.includes('「立论」'))await new Promise(resolve=>{coached=resolve;setTimeout(resolve,20000);});
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

scenarios['research-roundtable']=()=>scenario('research-roundtable',{
  replies(agent,prompt){
    if(prompt.includes('点评其他人'))return JSON.stringify({critiques:[{target:agent==='Alpha'?'Beta':'Alpha',comment:agent+' 觉得数据不足'}]});
    if(prompt.includes('汇总人'))return '结论：先做工具类。\n共识：都看好 agent。\n分歧：定价。\n未知：监管。';
    return JSON.stringify({summary:agent+' 的结论',points:[agent+' 要点一',agent+' 要点二'],sources:[{title:agent+' 来源',url:'https://example.com/'+agent}]});
  },
  async play({alice,bob,test}){
    await bob.locator('#question').fill('个人开发者做什么 AI 产品？');
    await bob.locator('#writer').selectOption({label:'Beta'});
    await bob.locator('#ask').click();
    await expect(alice.getByText('结论：先做工具类。')).toBeVisible({timeout:15000});
    await expect(alice.locator('.brief').filter({has:alice.getByRole('heading',{name:'Alpha'})})).toContainText('→ 对 Beta：Alpha 觉得数据不足');
    await expect(alice.getByRole('link',{name:'Beta 来源'})).toHaveAttribute('href','https://example.com/Beta');
    await expect(alice.getByRole('heading',{name:'报告 · Beta'})).toBeVisible();
    expect(test.asked.length).toBe(5);
    expect(test.asked.at(-1).agent).toBe('Beta');
  }
});

scenarios['news-roundtable']=()=>scenario('news-roundtable',{
  replies(agent,prompt){
    if(prompt.includes('问你：'))return agent+' 回答：因为成本降了';
    return JSON.stringify({stance:agent==='Alpha'?'看好':'看衰',take:agent+' 认为这是大事',question:'你凭什么这么说？'});
  },
  async play({alice,bob,test}){
    for(const page of [alice,bob])await page.route(/hacker-news\.firebaseio\.com/,route=>route.fulfill({json:route.request().url().includes('topstories')?[1]:{id:1,title:'Open models catch up',score:321,url:'https://example.com/hn'}}));
    await alice.reload();
    await alice.locator('.story',{hasText:'Open models catch up'}).getByRole('button',{name:'上桌'}).click();
    await expect(bob.locator('.stance.看衰')).toBeVisible({timeout:15000});
    await expect(bob.getByText('↳ Beta：Beta 回答：因为成本降了')).toBeVisible({timeout:10000});
    await expect(bob.getByText('↳ Alpha：Alpha 回答：因为成本降了')).toBeVisible();
    await bob.getByRole('button',{name:'🔥 0'}).first().click();
    await expect(alice.getByRole('button',{name:'🔥 1'})).toBeVisible();
    expect(test.asked[0].content).toContain('Open models catch up');
    await alice.route(/zh\.wikipedia\.org/,route=>route.fulfill({json:{mostread:{articles:[{titles:{normalized:'国庆日'},views:120000,content_urls:{desktop:{page:'https://zh.wikipedia.org/wiki/x'}}}]}}}));
    await alice.locator('#source').selectOption('wiki');
    await expect(alice.locator('.story',{hasText:'国庆日'})).toContainText('120k');
  }
});

scenarios.gomoku=()=>{
  const replies=[{x:'H',y:8},{x:'A',y:1},{x:'B',y:1},{x:'C',y:1},{x:'D',y:1},{x:'E',y:1}];   // the first is taken: Beta retries
  return scenario('gomoku',{
    replies:(agent,prompt)=>JSON.stringify(agent==='Alpha'?{x:'I',y:8,why:'连成活二'}:{...replies.shift(),why:'随便下'}),
    async play({alice,bob,test}){
      await alice.getByRole('button',{name:'我来下'}).first().click();
      await bob.locator('#agent-white').selectOption({label:'Beta'});
      await bob.getByRole('button',{name:'交给 agent'}).nth(1).click();
      await alice.getByRole('button',{name:'开局'}).click();
      await alice.getByRole('button',{name:'H8',exact:true}).click();
      await expect(alice.locator('#board i.white')).toHaveCount(1,{timeout:10000});
      expect(test.asked.filter(a=>a.agent==='Beta').length).toBe(2);   // one illegal point, then a legal one
      await alice.getByRole('button',{name:'问军师'}).click();
      await expect(alice.getByText('军师建议 I8：连成活二')).toBeVisible({timeout:10000});
      await expect(bob.locator('#advice')).not.toContainText('军师建议');
      for(const [i,cell] of ['I8','J8','K8','L8'].entries()){
        await alice.getByRole('button',{name:cell,exact:true}).click();
        if(cell!=='L8')await expect(alice.locator('#board i.white')).toHaveCount(i+2,{timeout:10000});
      }
      await expect(bob.locator('#status')).toHaveText('黑棋 Alice 连成五子获胜！',{timeout:10000});
      await expect(bob.locator('#board i.black')).toHaveCount(5);
      await expect(bob.locator('#board i.white')).toHaveCount(4);
    }
  });
};

scenarios.quiz=()=>scenario('quiz',{
  replies(agent,prompt){
    if(prompt.includes('出 5 道'))return JSON.stringify({questions:[
      {question:'宋朝的开国皇帝是？',options:['赵匡胤','李世民','朱元璋','刘邦'],answer:0,explain:'960 年陈桥兵变'},
      {question:'《清明上河图》的作者？',options:['王希孟','张择端','苏轼','米芾'],answer:1,explain:'北宋张择端'}]});
    return JSON.stringify({choice:prompt.includes('开国皇帝')?'A':'C'});
  },
  async play({alice,bob,test}){
    await alice.locator('#topic').fill('宋朝历史');await alice.locator('#master').selectOption({label:'Alpha'});
    await alice.getByRole('button',{name:'开擂'}).click();
    await expect(bob.getByRole('heading',{name:'宋朝的开国皇帝是？'})).toBeVisible({timeout:10000});
    await expect(bob.getByText('已作答：Beta')).toBeVisible({timeout:10000});
    await bob.getByRole('button',{name:'B. 李世民'}).click();
    await alice.getByRole('button',{name:'A. 赵匡胤'}).click();
    await expect(bob.locator('main')).not.toContainText('陈桥兵变');
    await alice.getByRole('button',{name:'揭晓答案'}).click();
    await expect(bob.getByText('解析：960 年陈桥兵变')).toBeVisible();
    await expect(bob.locator('.board')).toContainText('Beta 1');
    await expect(bob.locator('.board')).toContainText('Alice 1');
    await expect(bob.locator('.board')).toContainText('Bob 0');
    await alice.getByRole('button',{name:'下一题'}).click();
    await expect(bob.getByRole('heading',{name:'《清明上河图》的作者？'})).toBeVisible();
    await bob.getByRole('button',{name:'B. 张择端'}).click();
    await expect(bob.getByText(/已作答：.*Bob/)).toBeVisible({timeout:10000});
    await alice.getByRole('button',{name:'揭晓答案'}).click();
    await alice.getByRole('button',{name:'结束'}).click();
    await expect(bob.getByRole('heading',{name:/冠军：(Beta|Alice|Bob)/})).toBeVisible();
    expect(test.asked.filter(a=>a.agent==='Alpha').length).toBe(1);
  }
});

scenarios['translation-relay']=()=>scenario('translation-relay',{
  replies(agent,prompt){
    if(prompt.includes('点评'))return '意思保留了大半，在日语那一步丢了典故。保真分 62';
    const lang=/译成(.+?)，/.exec(prompt)[1];
    return '['+lang+'] '+prompt.split('\n').pop();
  },
  async play({alice,bob,test}){
    await bob.locator('#text').fill('塞翁失马');
    await bob.getByRole('button',{name:'开始接力'}).click();
    await expect(alice.getByText(/保真分 62/)).toBeVisible({timeout:15000});
    await expect(alice.locator('.hop')).toHaveCount(5);
    await expect(alice.locator('.final')).toHaveText('回到中文：[中文] [法语] [日语] [英语] 塞翁失马');
    expect(test.asked.map(a=>a.agent)).toEqual(['Alpha','Beta','Alpha','Beta','Alpha']);
  }
});

scenarios.auction=()=>scenario('auction',{
  replies(agent,prompt){
    if(prompt.includes('拍卖师'))return JSON.stringify({lots:['会唱歌的硬盘','AI 写的情书','火星土豆','一秒钟的未来'].map(name=>({name,story:name+'的传说'}))});
    const value=Number(/它值 (\d+)/.exec(prompt)[1]);
    return JSON.stringify({bid:Math.floor(value/2),line:'我出一半！'});
  },
  async play({alice,bob,test}){
    await alice.locator('#host').selectOption({label:'Alpha'});await alice.locator('#theme').fill('赛博古董');
    await alice.getByRole('button',{name:'开拍'}).click();
    await expect(bob.getByRole('heading',{name:'会唱歌的硬盘'})).toBeVisible({timeout:10000});
    await expect(bob.locator('.mine')).toHaveCount(4);
    await expect(alice.locator('.mine')).toHaveCount(0);
    await bob.locator('#strategy').fill('稳一点，只出一半');await bob.getByRole('button',{name:'交代'}).click();
    for(let lot=1;lot<=4;lot++){
      await alice.getByRole('button',{name:lot===1?'第一件开拍':'下一件开拍'}).click();
      await expect(alice.locator('.win')).toHaveCount(lot,{timeout:10000});
    }
    await expect(bob.getByRole('heading',{name:'拍卖结束，赢家：Beta'})).toBeVisible({timeout:15000});
    await expect(alice.locator('.win')).toHaveCount(4);
    await expect(alice.getByText(/各家心理价：Beta \d+/).first()).toBeVisible();
    expect(test.asked.filter(a=>a.agent==='Beta').every(a=>a.content.includes('稳一点，只出一半'))).toBe(true);
  }
});

scenarios['story-relay']=()=>scenario('story-relay',{
  replies(agent,prompt){
    if(prompt.includes('画一张插图'))return JSON.stringify({svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 260"><rect width="400" height="260" fill="#123"/></svg>'});
    if(prompt.includes('写结局'))return '终点站到了，他终于明白那张车票是写给自己的。';
    return agent+' 写道：列车又开过了一站。';
  },
  async play({alice,bob,test}){
    await alice.locator('#title').fill('最后一班地铁');await alice.locator('#opening').fill('凌晨一点，车厢里只剩他一个人。');
    await alice.getByRole('button',{name:'开始接龙'}).click();
    // Order: Alpha, Alice, Beta, Bob. Alpha writes on its own first.
    await expect(alice.locator('#next')).toBeVisible({timeout:10000});
    await alice.locator('#next').fill('广播突然报出一个不存在的站名。');await alice.getByRole('button',{name:'写好了'}).click();
    await expect(bob.locator('#next')).toBeVisible({timeout:10000});
    await expect(bob.locator('.para')).toHaveCount(4);
    await expect(bob.locator('.art img')).toHaveCount(1,{timeout:10000});   // after the fourth paragraph
    await bob.locator('#next').fill('门开了，站台上站着年轻时的自己。');await bob.getByRole('button',{name:'写好了'}).click();
    await expect(alice.locator('#next')).toBeVisible({timeout:10000});
    await alice.getByRole('button',{name:'请 agent 写结局'}).click();
    await expect(bob.getByText('那张车票是写给自己的')).toBeVisible({timeout:10000});
    await expect(bob.getByText('Alpha（结局）')).toBeVisible();
  }
});

scenarios['decision-room']=()=>scenario('decision-room',{
  replies:(agent,prompt)=>JSON.stringify(agent==='Alpha'
    ?{scores:{'离线模式':{价值:9,成本:6},'插件市场':{价值:6,成本:3}},reasons:{'离线模式':'用户呼声高','插件市场':'生态长期价值'}}
    :{scores:{'离线模式':{价值:7,成本:8},'插件市场':{价值:8}},reasons:{'离线模式':'改动面大'}}),
  async play({alice,bob,test}){
    await bob.locator('#question').fill('下季度先做什么');await bob.locator('#options').fill('离线模式, 插件市场');await bob.locator('#criteria').fill('价值, 成本');
    await bob.getByRole('button',{name:'请 agent 打分'}).click();
    await expect(alice.getByText('Alpha ✓　Beta ✓')).toBeVisible({timeout:10000});
    // Value weighs +1, cost −1 by default: 离线 8−7=1.0, 插件 7−3=4.0 (Beta left 插件's cost empty).
    await expect(alice.locator('.bar').first()).toHaveText('4.0');
    await expect(alice.locator('.card',{hasText:'Beta'}).locator('td',{hasText:'-'})).toHaveCount(1);
    await alice.locator('#w-成本').fill('0');
    await expect(alice.locator('.bar').first()).toHaveText('8.0');
    await expect(bob.locator('.bar').first()).toHaveText('4.0');   // weights are each person's own
  }
});

scenarios['agent-plaza']=()=>scenario('agent-plaza',{
  replies:(agent)=>agent==='Alpha'?'早起的人先看到日出。':'闹钟响时想想你的梦想。',
  async play({alice,bob,test}){
    await alice.getByRole('button',{name:'用一句话说服我明天早起'}).click();
    await alice.getByRole('button',{name:'抛出'}).click();
    await bob.getByRole('button',{name:'派我的 agent 回答（Beta）'}).click();
    await alice.getByRole('button',{name:'派我的 agent 回答（Alpha）'}).click();
    await bob.locator('#mine').fill('因为早餐店六点开门');await bob.getByRole('button',{name:'回答',exact:true}).click();
    await expect(alice.locator('.answer')).toHaveCount(3,{timeout:10000});
    await expect(alice.getByText('3 个回答')).toBeVisible({timeout:10000});
    await bob.locator('.answer',{hasText:'早起的人先看到日出'}).getByRole('button').click();
    await alice.locator('.answer',{hasText:'早起的人先看到日出'}).getByRole('button').click();
    await expect(bob.locator('.answer').first()).toContainText('🤖 Alpha · Alice');
    await expect(bob.locator('.answer').first().getByRole('button')).toHaveText('▲ 2');
    await expect(bob.getByRole('button',{name:'派我的 agent 回答（Beta）'})).toBeDisabled();
    expect(test.asked.length).toBe(2);
  }
});

scenarios.estimation=()=>scenario('estimation',{
  replies:(agent)=>JSON.stringify({value:agent==='Alpha'?500:420,why:agent+' 按线路数推算'}),
  async play({alice,bob,test}){
    await alice.locator('#q').fill('上海一共有多少个地铁站？');await alice.locator('#truth').fill('508');await alice.locator('#unit').fill('个');
    await alice.getByRole('button',{name:'出题'}).click();
    await bob.locator('#guess').fill('300');await bob.getByRole('button',{name:'提交'}).click();
    await bob.getByRole('button',{name:'让我的 agent 也估（Beta）'}).click();
    await alice.getByRole('button',{name:'让我的 agent 也估（Alpha）'}).click();
    await alice.locator('#guess').fill('450');await alice.getByRole('button',{name:'提交'}).click();
    await expect(alice.getByRole('button',{name:'揭晓答案（已有 4 个估计）'})).toBeVisible({timeout:10000});
    await expect(bob.locator('main')).not.toContainText('508');
    await alice.getByRole('button',{name:/揭晓答案/}).click();
    await expect(bob.getByText('答案：508 个')).toBeVisible();
    await expect(bob.getByText('所有人的中位数 435，人类中位数 375，agent 中位数 460')).toBeVisible();
    await expect(bob.getByText('最接近：')).toContainText('Alpha（500）');
    await expect(bob.locator('.dot')).toHaveCount(4);
  }
});

scenarios['agent-canvas']=()=>scenario('agent-canvas',{
  replies:(agent,prompt)=>JSON.stringify({svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><circle cx="'+(agent==='Alpha'?500:100)+'" cy="80" r="40" fill="#ffe066"/></svg>'}),
  async play({alice,bob,test}){
    await bob.locator('#painter').selectOption({label:'Alpha'});
    await bob.locator('#ask').fill('右上角一轮月亮');await bob.getByRole('button',{name:'请它画'}).click();
    await expect(alice.locator('#canvas img')).toHaveCount(1,{timeout:10000});   // the second painter sees the first layer
    await alice.locator('#painter').selectOption({label:'Beta'});
    await alice.locator('#ask').fill('左上角一个太阳');await alice.getByRole('button',{name:'请它画'}).click();
    await expect(alice.locator('#canvas img')).toHaveCount(2,{timeout:10000});
    expect(test.asked.find(a=>a.agent==='Beta').content).toContain('Alpha 画了「右上角一轮月亮」');
    await expect(bob.locator('.layer',{hasText:'左上角一个太阳'}).getByRole('button')).toHaveCount(0);   // Bob can't undo Alice's layer
    await bob.locator('.layer',{hasText:'右上角一轮月亮'}).getByRole('button',{name:'撤掉'}).click();
    await expect(alice.locator('#canvas img')).toHaveCount(1);
  }
});

scenarios.adventure=()=>scenario('adventure',{
  replies(agent,prompt){
    if(prompt.includes('主持人'))return JSON.stringify(prompt.includes('开场')
      ?{scene:'霓虹雨夜，失窃的芯片最后出现在面馆后巷。',hp:{},ended:false}
      :{scene:'Beta 追进后巷，被机械狗咬了一口，但拿回了芯片。',hp:{Beta:-3},ended:true});
    return JSON.stringify({action:prompt.includes('追进后巷')?'追进后巷抓人':'四处看看'});
  },
  async play({alice,bob,test}){
    await alice.locator('#master').selectOption({label:'Alpha'});
    await alice.getByRole('button',{name:'出发'}).click();
    await expect(bob.getByText('霓虹雨夜，失窃的芯片最后出现在面馆后巷。')).toBeVisible({timeout:10000});
    await expect(alice.locator('#order-'+(await bob.locator('[id^=order-]').getAttribute('id')).slice(6))).toHaveCount(0);
    await bob.locator('[id^=order-]').fill('追进后巷');await bob.getByRole('button',{name:'下令'}).click();
    await expect(bob.getByText('已下令：追进后巷')).toBeVisible();
    await alice.getByRole('button',{name:'进入下一回合'}).click();
    await expect(alice.getByText(/追进后巷抓人 🎲\d+/)).toBeVisible({timeout:10000});
    await expect(alice.getByText('被机械狗咬了一口')).toBeVisible({timeout:10000});
    await expect(bob.locator('.hero .hp')).toHaveText('♥ 7');
    await expect(bob.getByRole('heading',{name:'冒险结束'})).toBeVisible();
  }
});

scenarios['game-workshop']=()=>scenario('game-workshop',{
  replies(agent,prompt){
    const game=title=>'<!doctype html><html><head><title>t</title></head><body>'+title+'<button id="b">点我</button><p id="s">0</p><p id="net"></p>'+
      '<script>let n=0;b.onclick=()=>s.textContent=++n;fetch("https://example.com/").then(()=>net.textContent="leaked",()=>net.textContent="offline")</script></body></html>';
    return agent==='Alpha'?'做了一个点击计数器\n```html\n'+game('')+'\n```':'**加上了标题**\n```html\n'+game('<h1>点点乐</h1>')+'\n```';
  },
  async play({alice,bob,test}){
    await expect(alice.locator('#builder option').first()).toHaveText('Alpha');   // your own agent first
    await expect(bob.locator('#builder option').first()).toHaveText('Beta');
    await alice.locator('#ask').fill('做一个点击计数器');await alice.locator('#send').click();
    const bobGame=bob.frameLocator('#preview');
    await bobGame.locator('#b').click({timeout:15000});
    await expect(bobGame.locator('#s')).toHaveText('1');
    await expect(bobGame.locator('#net')).toHaveText('offline');                 // no network inside the game
    await bob.locator('#ask').fill('加一个标题');await bob.locator('#send').click();
    await expect(bobGame.locator('h1')).toHaveText('点点乐',{timeout:15000});
    expect(test.asked[1].agent).toBe('Beta');
    expect(test.asked[1].content).toContain('id="b"');                            // built on the last version
    expect(test.asked[1].content).toContain('Alpha：做一个点击计数器 → 做了一个点击计数器');
    await expect(bob.getByText('第 2 版：加上了标题')).toBeVisible();
    await expect(bob.getByRole('button',{name:'回到这一版'})).toHaveCount(0);
    await alice.getByRole('button',{name:'回到这一版'}).click();
    await expect(bobGame.locator('h1')).toHaveCount(0,{timeout:10000});
    await expect(bob.locator('#version')).toHaveText('第 3 版');
  }
});

scenarios['who-is-human']=()=>scenario('who-is-human',{
  replies(agent,prompt){
    const key=text=>(new RegExp('([A-P])\\. '+text).exec(prompt)||[])[1];
    if(prompt.includes('判断哪些是人写的'))return JSON.stringify(agent==='Alpha'?{human:[key('煎饼')],why:'有具体的加蛋细节'}:{human:[key('豆浆')],why:'抱怨油条很真实'});
    return JSON.stringify({answer:agent==='Alpha'?'豆浆油条，油条有点软':'没吃，起晚了 hhh'});
  },
  async play({alice,bob,test}){
    await alice.getByRole('button',{name:'开一轮'}).click();
    await expect(bob.getByText('用一句话描述你今天的早餐')).toBeVisible();
    await bob.locator('#answer').dispatchEvent('compositionstart');               // Bob is mid-IME: his page holds still
    const before=await bob.locator('#round').innerText();
    await alice.locator('#answer').fill('煎饼果子加两个蛋');await alice.getByRole('button',{name:'提交'}).click();
    await expect(alice.getByText('你的回答：煎饼果子加两个蛋')).toBeVisible();
    await bob.waitForTimeout(1000);
    expect(await bob.locator('#round').innerText()).toBe(before);
    await bob.locator('#answer').dispatchEvent('compositionend');
    await expect(bob.locator('#round')).not.toHaveText(before);
    await bob.locator('#answer').fill('一杯冰美式');await bob.getByRole('button',{name:'提交'}).click();
    await expect(alice.getByText('已收到 4 份回答')).toBeVisible({timeout:10000});
    await expect(bob.locator('main')).not.toContainText('煎饼');                  // nobody sees answers yet
    await expect(bob.getByText('你的回答：一杯冰美式')).toBeVisible();
    await expect(bob.getByRole('button',{name:'开始猜'})).toHaveCount(0);
    await alice.getByRole('button',{name:'开始猜'}).click();
    const label=async(page,text,v)=>{const row=page.locator('.entry',{hasText:text});await row.getByRole('button',{name:v,exact:true}).click();await expect(row.getByRole('button',{name:v,exact:true})).toHaveClass('on');};
    await expect(bob.locator('.entry',{hasText:'一杯冰美式'})).toContainText('你的');
    await expect(bob.locator('.entry',{hasText:'一杯冰美式'}).getByRole('button')).toHaveCount(0);
    await expect(bob.locator('#entries')).not.toContainText('Alpha');            // authors stay hidden
    await label(bob,'煎饼','人');await label(bob,'豆浆','AI');await label(bob,'没吃','人');
    await label(alice,'冰美式','人');await label(alice,'豆浆','人');await label(alice,'没吃','AI');
    await expect(alice.getByText('2 人猜完 · agent 判断 2/2')).toBeVisible({timeout:10000});
    expect(test.asked.find(a=>a.agent==='Alpha'&&a.content.includes('判断')).content).not.toContain('豆浆油条');   // its own answer is left out
    await alice.getByRole('button',{name:'揭晓'}).click();
    await expect(bob.locator('.entry',{hasText:'豆浆'})).toContainText('Alpha · 3 人中 2 人认为是人类');
    const score=name=>bob.locator('.score',{hasText:name}).locator('b');
    await expect(score('Alpha')).toHaveText('4');await expect(score('Alice')).toHaveText('3');
    await expect(score('Bob')).toHaveText('3');await expect(score('Beta')).toHaveText('1');
    await expect(bob.getByText('积分 · 人类 6 : 5 agent')).toBeVisible();
    await expect(bob.getByText('Beta 的判断：抱怨油条很真实')).toBeVisible();
  }
});

scenarios['meet-midway']=()=>scenario('meet-midway',{
  replies:(agent,prompt)=>JSON.stringify({places:agent==='Alpha'
    ?[{name:'外滩火锅',lat:31.2360,lng:121.4880,why:'两边都近'}]
    :[{name:'<img src=x onerror=window.pwned=1>江边火锅',lat:31.2400,lng:121.4900,why:'看江景'},{name:'远方小馆',lat:32.5,lng:122.9,why:'太远'}]}),
  async play({alice,bob,test}){
    const places={'人民广场':['31.2304','121.4737'],'陆家嘴':['31.2397','121.4998']};
    for(const page of [alice,bob])await page.route(/nominatim\.openstreetmap\.org/,route=>{
      const url=new URL(route.request().url());
      if(url.pathname.includes('reverse'))return route.fulfill({json:{display_name:'外滩, 黄浦区, 上海'}});
      const [lat,lon]=places[url.searchParams.get('q')];return route.fulfill({json:[{lat,lon,display_name:url.searchParams.get('q')+', 上海'}]});
    });
    const pin=async(page,q)=>{await page.locator('#q').fill(q);await page.locator('#find').click();await page.getByRole('button',{name:q+', 上海'}).click();};
    await pin(alice,'人民广场');await pin(bob,'陆家嘴');
    await expect(alice.locator('#me')).toContainText('你的位置：人民广场');
    await expect(alice.locator('#middle')).toHaveText('大家的中点：外滩, 黄浦区');
    const bobState=await bob.evaluate(()=>fetch('state').then(r=>r.text()));
    expect(bobState).not.toContain('121.4737');expect(bobState).not.toContain('人民广场');     // Alice's pin is blurred for Bob
    await bob.locator('#want').fill('火锅，人均 100');await bob.locator('#ask').click();
    await expect(alice.getByText('外滩火锅')).toBeVisible({timeout:15000});
    await expect(alice.getByText('<img src=x onerror=window.pwned=1>江边火锅')).toBeVisible();
    await expect(alice.getByText('远方小馆')).toHaveCount(0);                                      // too far, dropped
    await expect(alice.locator('.place',{hasText:'外滩火锅'})).toContainText(/最远 1\.\d 公里 · 平均 1\.\d 公里 · 两边都近/);
    const prompt=test.asked[0].content;
    expect(prompt).toContain('31.23, 121.47');expect(prompt).not.toContain('121.4737');
    await bob.locator('.place',{hasText:'外滩火锅'}).getByRole('button').click();
    await expect(alice.locator('.place',{hasText:'外滩火锅'})).toContainText('最受欢迎');
    await expect(alice.locator('.place',{hasText:'外滩火锅'}).getByRole('button')).toHaveText('👍 1');
    if(await alice.evaluate(()=>!!window.L)){
      await alice.evaluate(()=>layer.eachLayer(l=>l.getPopup&&l.getPopup()&&l.openPopup()));
      await alice.waitForTimeout(300);
      expect(await alice.evaluate(()=>window.pwned)).toBeUndefined();
    }
  }
});

scenarios.standup=()=>scenario('standup',{
  replies:(agent,prompt)=>JSON.stringify(prompt.includes('主持站会')
    ?{summary:'两人推进顺利。',risks:['登录接口 500 会拖住支付'],pairs:['Alice 和 Bob：Alice 刚修过支付页，可以帮看登录接口']}
    :{done:['完成登录页'],next:['接支付'],blockers:['卡在登录接口 500']}),
  async play({alice,bob,test}){
    await expect(bob.getByRole('button',{name:'让 Beta 写'})).toBeVisible();
    await expect(bob.getByRole('button',{name:'让 Alpha 写'})).toHaveCount(0);   // only your own agent
    expect(await bob.evaluate(async()=>{const s=await fetch('state').then(r=>r.json());const a=s.members.find(m=>m.name==='Alpha');
      return fetch('write',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:a.id})}).then(r=>r.status);})).toBe(403);
    await bob.locator('#note').fill('卡在登录接口');await bob.getByRole('button',{name:'让 Beta 写'}).click();
    await expect(alice.locator('.report',{hasText:'Bob'})).toContainText('卡在登录接口 500',{timeout:10000});
    expect(test.asked[0].agent).toBe('Beta');expect(test.asked[0].content).toContain('Bob 的补充：卡在登录接口');
    await alice.locator('#note').fill('修了支付页\n写了周报');await alice.getByRole('button',{name:'直接发布'}).click();
    await expect(bob.locator('.report',{hasText:'Alice'})).toContainText('自己写');
    await expect(bob.locator('.report',{hasText:'Alice'}).locator('li')).toHaveText(['修了支付页','写了周报']);
    await expect(bob.getByRole('button',{name:'汇总'})).toHaveCount(0);
    await alice.locator('#writer').selectOption({label:'Alpha'});await alice.getByRole('button',{name:'汇总'}).click();
    await expect(bob.locator('.summary')).toContainText('两人推进顺利。',{timeout:10000});
    await expect(bob.locator('.summary')).toContainText('Alice 和 Bob：Alice 刚修过支付页');
    expect(test.asked[1].agent).toBe('Alpha');expect(test.asked[1].content).toContain('卡点：卡在登录接口 500');
    await expect(alice.locator('.report',{hasText:'Bob'}).getByRole('button',{name:'撤回'})).toHaveCount(0);
    await bob.locator('.report',{hasText:'Bob'}).getByRole('button',{name:'撤回'}).click();
    await expect(alice.getByText('完成登录页')).toHaveCount(0);
  }
});

scenarios['twenty-questions']=()=>scenario('twenty-questions',{
  replies(agent,prompt){
    if(prompt.includes('在心里选一个'))return JSON.stringify({secret:agent==='Alpha'?'熊猫':'企鹅'});
    if(prompt.includes('是不是同一个东西'))return JSON.stringify({same:false});
    if(prompt.includes('如实回答'))return JSON.stringify({answer:'是'});
    if(agent==='Alpha')return JSON.stringify(prompt.includes('猜「海豹」')?{guess:'企鹅！'}:prompt.includes('问：它会游泳吗')?{guess:'海豹'}:{question:'它会游泳吗？'});
    return JSON.stringify({question:prompt.includes('问：它是黑白')?'它吃竹子吗？':'它是黑白色的吗？'});
  },
  async play({alice,bob,test}){
    await alice.locator('#hint').fill('先问会不会游泳');await alice.locator('#send-hint').click();
    await expect(alice.locator('#hint-note')).toHaveText('待用的提示：先问会不会游泳');
    await alice.locator('#category').selectOption('动物');await alice.getByRole('button',{name:'开始'}).click();
    await expect(alice.locator('#secret')).toHaveText('你的 Alpha 想的是：熊猫（只有你看得到）',{timeout:10000});
    await expect(bob.locator('#secret')).toHaveText('你的 Beta 想的是：企鹅（只有你看得到）');
    await expect(bob.locator('#status')).toHaveText('Alpha 猜中了，获胜！',{timeout:15000});
    const alpha=bob.locator('.card',{hasText:'Alpha 猜 Beta 的'});
    await expect(alpha.locator('.log')).toHaveText(['它会游泳吗？ 是','猜：海豹 ✗','猜：企鹅！ ✓']);
    await expect(alpha).toContainText('Alpha 猜 Beta 的「企鹅」');
    await expect(bob.locator('.card',{hasText:'Beta 猜 Alpha 的'}).locator('.log')).toHaveText(['它是黑白色的吗？ 是','它吃竹子吗？ 是']);
    const turns=test.asked.filter(a=>a.agent==='Alpha'&&a.content.includes('正在猜'));
    expect(turns[0].content).toContain('Alice 悄悄提示你：先问会不会游泳');expect(turns[1].content).not.toContain('悄悄提示');
    expect(test.asked.filter(a=>a.agent==='Alpha').some(a=>a.content.includes('企鹅'))).toBe(false);   // never sees Beta's secret
    expect(test.asked.some(a=>a.agent==='Beta'&&a.content.includes('是不是同一个东西')&&a.content.includes('海豹'))).toBe(true);
    await expect(alice.locator('#hint-note')).toHaveText('');
  }
});

scenarios['agent-arena']=()=>scenario('agent-arena',{
  replies:agent=>agent==='Alpha'?'咪咪、橘子、大黄、胖虎、团子':'豆包、元宝、金桔、橙子、小太阳',
  async play({alice,bob,test}){
    await alice.locator('#task').fill('给一只橘猫取五个名字');await alice.locator('#send').click();
    await expect(bob.getByText('咪咪、橘子')).toBeVisible({timeout:10000});await expect(bob.getByText('豆包、元宝')).toBeVisible();
    await expect(bob.locator('#battles')).not.toContainText('Alpha');await expect(bob.locator('#battles')).not.toContainText('Beta');   // blind until you vote
    await bob.getByRole('button',{name:'A 更好'}).click();
    await expect(bob.getByText('你投了：A 更好')).toBeVisible();
    await expect(bob.getByRole('button',{name:'B 更好'})).toHaveCount(0);
    const first=(await bob.locator('.side h3').first().innerText()).split(' · ')[1],second=first==='Alpha'?'Beta':'Alpha';
    await expect(bob.locator('.side.won')).toContainText(first);
    await expect(alice.locator('#battles')).not.toContainText(first);                   // Alice hasn't voted: still blind
    const row=(page,name)=>page.locator('tr',{hasText:name}).locator('td');
    await expect(row(alice,first)).toHaveText(['1',first,'1012','1','0','0']);
    await alice.getByRole('button',{name:'差不多'}).click();
    await expect(row(bob,first)).toHaveText(['1',first,'1011','1','0','1']);
    await expect(row(bob,second)).toHaveText(['2',second,'989','0','1','1']);
    expect(test.asked.map(a=>a.content.includes('题目：给一只橘猫取五个名字'))).toEqual([true,true]);
  }
});

scenarios['code-duel']=()=>scenario('code-duel',{
  replies:agent=>agent==='Alpha'
    ?"好的：\n```js\nfunction isPalindrome(s){s=s.toLowerCase().replace(/[^a-z0-9]/g,'');return s===[...s].reverse().join('');}\n```"
    :'```javascript\nfunction isPalindrome(s){while(true){}}\n```',
  async play({alice,bob,test}){
    await alice.locator('#pick').selectOption({label:'回文判断'});await alice.getByRole('button',{name:'开题'}).click();
    await expect(bob.getByText('第 1 题 · 回文判断')).toBeVisible();
    await expect(bob.locator('#code')).toHaveValue('function isPalindrome(s) {\n  \n}');
    await bob.locator('#code').fill("function isPalindrome(s){return s===[...s].reverse().join('')}");
    await bob.getByRole('button',{name:'试运行样例'}).click();
    await expect(bob.locator('#tried')).toHaveText('样例 1/2 通过');
    await bob.locator('#code').fill("const isPalindrome=s=>{const t=s.toLowerCase().replace(/[^a-z0-9]/g,'');return t===[...t].reverse().join('');};");
    await bob.getByRole('button',{name:'试运行样例'}).click();
    await expect(bob.locator('#tried')).toHaveText('样例 2/2 通过');
    await bob.getByRole('button',{name:'提交我的'}).click();
    await expect(alice.locator('#solutions .card')).toHaveCount(3);
    for(const name of ['Alpha','Beta','Bob'])await expect(alice.locator('#solutions .card',{hasText:name})).toContainText('已提交',{timeout:10000});
    await expect(alice.locator('#solutions')).not.toContainText('reverse');               // code stays secret
    await expect(bob.getByRole('button',{name:'结束，揭晓代码'})).toHaveCount(0);
    await alice.getByRole('button',{name:'结束，揭晓代码'}).click();
    const cards=bob.locator('.solution');
    await expect(cards.nth(2)).toContainText('超时',{timeout:10000});                     // Beta's loop is cut off; the page lives on
    await expect(cards.nth(0)).toContainText('Alpha');await expect(cards.nth(0)).toContainText('6/6');
    await expect(cards.nth(1)).toContainText('Bob');await expect(cards.nth(1)).toContainText('6/6');
    await expect(cards.nth(2).locator('.rank')).toHaveText('–');
    expect(test.asked[0].content).toContain('isPalindrome("race a car") → false');
  }
});

scenarios['bomb-defusal']=()=>scenario('bomb-defusal',{
  replies:(agent,prompt)=>prompt.trimEnd().endsWith('Alice：有几根线？')?'先告诉我线的颜色顺序和序列号末位。':'收到，继续说。',
  async play({alice,bob,test}){
    const {solve}=require(path.join(apps,'bomb-defusal','server.js'));
    await alice.locator('#expert').selectOption({label:'Beta'});await alice.getByRole('button',{name:'开始'}).click();
    await expect(bob.locator('.bomb')).toBeVisible();
    await expect(bob.locator('#say-box')).toBeHidden();await expect(bob.locator('.wire button')).toHaveCount(0);   // Bob only watches
    await alice.locator('#say').fill('有几根线？');await alice.locator('#send').click();
    await expect(bob.getByText('先告诉我线的颜色顺序和序列号末位。')).toBeVisible({timeout:10000});
    const state=async()=>(await alice.evaluate(()=>fetch('state').then(r=>r.json()))).state;
    const bomb=(await state()).bomb;
    expect(bomb.button.strip).toBeUndefined();                                   // the strip shows only while holding
    expect(test.asked[0].agent).toBe('Beta');expect(test.asked[0].content).toContain('【拆弹手册】');
    expect(test.asked[0].content).not.toContain(bomb.serial);                    // the expert never sees the bomb
    const answer=solve({...bomb,button:{...bomb.button,strip:'blue'}});
    const wrong=answer.wire===0?1:0;
    await alice.locator('.wire').nth(wrong).getByRole('button',{name:'剪'}).click();
    await expect(bob.locator('#head')).toContainText('失误 ✗○○');
    await alice.locator('#say').fill('剪错了');await alice.locator('#send').click();
    await expect.poll(()=>test.asked.length).toBe(2);
    expect(test.asked[1].content).toContain('剪断了第 '+(wrong+1)+' 根线');expect(test.asked[1].content).toContain('错了，记一次失误');
    await alice.locator('.wire').nth(answer.wire).getByRole('button',{name:'剪'}).click();
    const panel=alice.locator('.module').nth(1);
    if(answer.button==='press')await panel.locator('.bigbutton').click();
    else{
      await panel.locator('button.ghost',{hasText:'按住'}).click();
      await expect(panel.locator('.strip')).toBeVisible();
      const digit={blue:4,white:1,yellow:5,red:1}[(await state()).holding];
      await panel.getByRole('button',{name:String(digit),exact:true}).click();
    }
    for(const key of answer.keys)await alice.locator('.key',{hasText:key}).click();
    await expect(bob.locator('#head')).toContainText('💚 炸弹已拆除');
    await expect(bob.locator('.module.solved')).toHaveCount(3);
  }
});

scenarios['murder-mystery']=()=>scenario('murder-mystery',{
  replies(agent,prompt){
    if(prompt.includes('剧本杀编剧'))return JSON.stringify({title:'寿宴毒酒',story:'周老爷在寿宴上喝下一杯酒后倒地身亡。',
      suspects:[{name:'管家',intro:'跟了周家二十年',secret:'我在酒里下了毒，因为老爷要赶我走',guilty:true},{name:'厨娘',intro:'负责寿宴菜品',secret:'我偷拿过厨房的银器',guilty:false},
        {name:'侄子',intro:'欠了一身赌债',secret:'我昨晚去了赌场',guilty:false}],
      clues:['酒杯是管家端上来的','厨房的门八点就锁了','侄子九点才到'],hidden:['管家房里有一包药粉','银器少了两把','赌场的票根'],truth:'管家因被辞退怀恨，在酒里下毒。'});
    return prompt.includes('扮演「管家」')?'我一直在大厅伺候老爷，哪儿也没去。':'厨房八点就锁了，我早回房了。';
  },
  async play({alice,bob,test}){
    await alice.locator('#writer').selectOption({label:'Alpha'});await alice.getByRole('button',{name:'开案'}).click();
    await expect(bob.getByRole('heading',{name:'寿宴毒酒'})).toBeVisible({timeout:10000});
    await expect(bob.locator('.suspect')).toHaveCount(3);await expect(bob.locator('.suspect').first()).toContainText('由 Beta 扮演');
    await expect(bob.locator('main')).not.toContainText('下了毒');await expect(bob.locator('main')).not.toContainText('偷拿');   // secrets stay hidden
    await bob.locator('#suspect').selectOption('管家');await bob.locator('#question').fill('你端酒前去过哪里？');await bob.locator('#ask').click();
    await expect(alice.getByText('管家：我一直在大厅伺候老爷，哪儿也没去。')).toBeVisible({timeout:10000});
    const butler=test.asked.find(a=>a.content.includes('扮演「管家」'));
    expect(butler.agent).toBe('Beta');expect(butler.content).toContain('我在酒里下了毒');expect(butler.content).toContain('你就是凶手');
    expect(butler.content).not.toContain('偷拿过厨房的银器');                      // only its own sheet
    await bob.locator('#suspect').selectOption('厨娘');await bob.locator('#question').fill('你几点离开厨房？');await bob.locator('#ask').click();
    await expect(alice.getByText('厨娘：厨房八点就锁了，我早回房了。')).toBeVisible({timeout:10000});
    const cook=test.asked.find(a=>a.content.includes('扮演「厨娘」'));
    expect(cook.content).toContain('你不是凶手');expect(cook.content).toContain('管家：我一直在大厅伺候老爷');
    await expect(bob.locator('#left')).toHaveText('你还能问 4 个问题 · 待搜查线索 3 条');
    await bob.getByRole('button',{name:'搜查一次'}).click();
    await expect(alice.getByText('🔎 管家房里有一包药粉（Bob 搜到）')).toBeVisible();
    await bob.locator('.suspect',{hasText:'管家'}).getByRole('button',{name:'指认'}).click();
    await alice.locator('.suspect',{hasText:'厨娘'}).getByRole('button',{name:'指认'}).click();
    await expect(alice.getByText('2 位侦探已指认')).toBeVisible();
    await expect(bob.getByRole('button',{name:'结案，揭晓真相'})).toHaveCount(0);
    await alice.getByRole('button',{name:'结案，揭晓真相'}).click();
    await expect(bob.getByText('猜对的侦探：Bob')).toBeVisible();
    await expect(bob.locator('.suspect',{hasText:'管家'}).locator('.guilty')).toHaveText('凶手 · 我在酒里下了毒，因为老爷要赶我走');
    await expect(bob.getByText('管家因被辞退怀恨，在酒里下毒。')).toBeVisible();
  }
});

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
