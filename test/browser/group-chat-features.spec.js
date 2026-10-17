const {test,expect}=require('@playwright/test');

test('visible Agent activity uses gateway states and clears the running marker',async({page})=>{
  await mobileSetup(page);let running=true;
  await page.route('**/v1/agents/activity',route=>route.fulfill({json:{data:[{agent_id:'activity-agent',state:running?'running':'idle'}]}}));
  await page.evaluate(()=>{
    const row=document.createElement('div');row.id='activity-check';row.dataset.agentActivity='activity-agent';
    row.innerHTML='<span class="agent-activity-dot" hidden></span>';document.body.appendChild(row);
    return AgentActivity.refresh();
  });
  await expect(page.locator('#activity-check .agent-activity-dot')).toBeVisible();
  running=false;await page.evaluate(()=>AgentActivity.refresh());
  await expect(page.locator('#activity-check .agent-activity-dot')).toBeHidden();
});

test('group announcement and Team introduction can be edited without rendering HTML',async({page})=>{
  const {errors}=await mobileSetup(page);
  await page.evaluate(()=>toggleChatActions());
  await page.locator('#chat-actions-sheet').getByRole('button',{name:'群公告',exact:true}).click();
  await page.getByRole('textbox',{name:'群公告',exact:true}).fill('群约定 <img src=x>');
  await page.getByRole('button',{name:'保存公告',exact:true}).click();
  await expect(page.locator('#group-announcement')).toHaveText('群公告 · 群约定 <img src=x>');
  await expect(page.locator('#group-announcement img')).toHaveCount(0);
  await page.locator('#group-announcement').click();
  await expect(page.getByRole('textbox',{name:'群公告',exact:true})).toHaveValue('群约定 <img src=x>');
  await page.getByRole('textbox',{name:'群公告',exact:true}).fill('');await page.getByRole('button',{name:'保存公告',exact:true}).click();
  await expect(page.locator('#group-announcement')).toBeHidden();
  let description='介绍';
  await page.route('**/v1/teams/Project',route=>{
    if(route.request().method()==='PATCH')description=route.request().postDataJSON().description;
    return route.fulfill({json:{team:'Project',title:'Project',description}});
  });
  await page.evaluate(()=>GroupNetworkUI.teamInfo('Project'));
  await expect(page.getByRole('textbox',{name:'Team 介绍',exact:true})).toHaveValue('介绍');
  await page.getByRole('textbox',{name:'Team 介绍',exact:true}).fill('共同目标');
  await page.getByRole('button',{name:'保存介绍',exact:true}).click();
  await expect(page.locator('.group-network-dialog')).toHaveCount(0);
  expect(description).toBe('共同目标');expect(errors).toEqual([]);
});

test('history search combines dates and members and opens the original context safely',async({page})=>{
  await page.setViewportSize({width:390,height:844});await mobileSetup(page);
  await page.route('**/proxy_groups/rg_one/search?**',route=>{
    const q=new URL(route.request().url()).searchParams;
    expect(q.get('sender')).toBe('u:alice');expect(Number(q.get('from_ts'))).toBeGreaterThan(0);expect(Number(q.get('to_ts'))).toBeGreaterThan(Number(q.get('from_ts')));
    return route.fulfill({json:{messages:[{id:2,sender:'u:alice',sender_name:'Alice',content:'测试内容 <script>bad</script>',created_at:1790000000}],next_before_id:0}});
  });
  await page.route('**/proxy_groups/rg_one/messages/2/context',route=>route.fulfill({json:{focus_id:2,messages:[
    {id:1,sender_name:'Alice',content:'之前的消息',created_at:1790000000},
    {id:2,sender_name:'Alice',content:'测试内容 <script>bad</script>',created_at:1790000001},
    {id:3,sender_name:'Alice',content:'之后的消息',created_at:1790000002}]}}));
  await page.evaluate(()=>GroupNetworkUI.search('rg_one'));
  const layout=await page.locator('.gn-search-filters').evaluate(filters=>{
    const query=filters.previousElementSibling.getBoundingClientRect(),box=filters.getBoundingClientRect();
    return {queryWidth:query.width,filterWidth:box.width,stacked:box.top>=query.bottom};
  });
  expect(layout.stacked).toBe(true);
  expect(layout.queryWidth).toBeGreaterThan(250);
  await page.getByRole('searchbox',{name:'搜索聊天记录'}).fill('测试');
  await page.getByLabel('按成员筛选').selectOption('u:alice');
  await page.getByLabel('开始日期').fill('2026-09-01');await page.getByLabel('结束日期').fill('2026-10-09');
  await page.getByRole('button',{name:'搜索',exact:true}).click();
  await expect(page.locator('.gn-search-result mark')).toHaveText('测试');
  await page.getByRole('button',{name:'查看上下文'}).click();
  await expect(page.locator('.gn-message-focus')).toContainText('测试内容');
  await expect(page.locator('.group-network-dialog').last()).toContainText('之前的消息');
  await expect(page.locator('.group-network-dialog script')).toHaveCount(0);
  await page.locator('.gn-message-focus').getByRole('button',{name:'引用回复'}).click();
  await expect(page.locator('.group-network-dialog')).toHaveCount(0);
  await expect(page.locator('#group-reply-preview')).toContainText('测试内容');
});

async function mobileSetup(page) {
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{localStorage.setItem('clawcross_lang','zh');window.alert=()=>{};});
  await page.route('**/proxy_check_session',route=>route.fulfill({json:{valid:true,user_id:'alice',has_password:true,mode:'local'}}));
  await page.route('**/api/llm_config_status',route=>route.fulfill({json:{configured:true}}));
  await page.route('**/proxy_tunnel/status',route=>route.fulfill({json:{running:false,public_domain:'https://chat.example'}}));
  let title='朋友们',announcement='',version=1;
  const messages=[{id:10,sender:'u:alice',sender_name:'Alice',content:'旧消息 <script>bad</script>',created_at:1790000000}];
  const calls=[];
  await page.route('**/proxy_groups**',async route=>{
    const request=route.request(),url=new URL(request.url());calls.push({path:url.pathname,method:request.method(),body:request.postDataJSON()});
    const group={group_id:'rg_one',title,announcement,version,kind:'group',owner:'alice',members:[{principal:'u:alice',name:'Alice',is_agent:false}],messages,federated:true,member_count:1};
    if(url.pathname==='/proxy_groups') return route.fulfill({json:{groups:[group]}});
    if(url.pathname.endsWith('/search')) {
      expect(url.searchParams.get('query')).toBe('早期');
      return route.fulfill({json:{messages:[{id:2,sender:'old',sender_name:'Bob',content:'早期历史 <img src=x onerror=alert(1)>',created_at:1780000000}],next_before_id:0}});
    }
    if(url.pathname.endsWith('/guest-link')) return route.fulfill({json:{url:'https://chat.example/group-guest#exact-ticket',qr:'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iYmxhY2siLz48L3N2Zz4='}});
    if(url.pathname.endsWith('/guest-qr')) return route.fulfill({json:{qr:'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIj48L3N2Zz4='}});
    if(url.pathname.endsWith('/messages')&&request.method()==='POST') {
      const body=request.postDataJSON();messages.push({id:20,sender:'u:alice',sender_name:'Alice',created_at:1790000010,...body,reply:body.reply_to?{id:2,sender_name:'Bob',content:'早期历史'}:null});
      return route.fulfill({json:{created:true,message:messages.at(-1)}});
    }
    if(url.pathname.endsWith('/messages')) return route.fulfill({json:{messages}});
    if(request.method()==='PATCH') {
      const body=request.postDataJSON();if(body.title!==undefined)title=body.title;if(body.announcement!==undefined)announcement=body.announcement;version++;
    }
    return route.fulfill({json:request.method()==='PATCH'?{...group,title,announcement,version}:group});
  });
  await page.goto('/mobile/group_chat');
  await expect(page.locator('#tab-chats')).toBeVisible();
  await page.evaluate(()=>openChat('rg_one','朋友们'));
  return {errors,calls};
}

async function holdMessage(locator) {
  await locator.evaluate(target => {
    const box = target.getBoundingClientRect();
    target.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',pointerId:7,isPrimary:true,clientX:box.left+20,clientY:box.top+20}));
  });
  await expect(locator.page().getByRole('menu',{name:'消息操作'})).toBeVisible();
  await locator.evaluate(target => target.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch',pointerId:7,isPrimary:true})));
}

test('an invitation enables Hub without installing a tunnel binary',async({page})=>{
  await mobileSetup(page);
  let enabled=false,installs=0;
  await page.route('**/proxy_tunnel/status',route=>route.fulfill({json:{running:enabled,provider:'hub',phase:enabled?'connected':'stopped',public_domain:enabled?'https://d-example.clawcross.net':''}}));
  await page.route('**/proxy_tunnel/start',route=>{
    expect(route.request().postDataJSON()).toEqual({provider:'hub'});enabled=true;
    return route.fulfill({json:{status:'started'}});
  });
  await page.route('**/proxy_components/cloudflared',route=>{if(route.request().method()==='POST')installs++;return route.fulfill({json:{installed:false}});});
  page.on('dialog',dialog=>{expect(dialog.message()).toContain('ClawCross Hub');return dialog.accept();});
  await page.evaluate(()=>GroupNetworkUI.sharing('rg_one'));
  await page.getByRole('button',{name:'生成邀请链接',exact:true}).click();
  await expect(page.getByAltText('扫描二维码加入群聊')).toBeVisible();
  expect(enabled).toBe(true);expect(installs).toBe(0);
});

for(const mobile of [false,true]) {
  test(`group features: ${mobile?'mobile':'desktop'} rename, search, quote and invite QR`,async({page})=>{
    await page.setViewportSize(mobile?{width:390,height:844}:{width:1200,height:850});
    const {errors,calls}=await mobileSetup(page);
    await expect(page.locator('#chat-body')).toContainText('旧消息');
    await expect(page.locator('.msg-reply-action')).toHaveCount(0);
    const bubble = page.locator('#chat-body [data-group-message-id="10"]');
    if (mobile) await holdMessage(bubble); else await bubble.click({button:'right'});
    await expect(page.getByRole('menu',{name:'消息操作'})).toBeVisible();
    const bounds = await page.getByRole('menu').boundingBox();
    expect(bounds.x >= 0 && bounds.y >= 0 && bounds.x+bounds.width <= page.viewportSize().width && bounds.y+bounds.height <= page.viewportSize().height).toBe(true);
    await page.getByRole('menuitem',{name:'引用回复',exact:true}).click();
    await expect(page.locator('#group-reply-preview')).toContainText('旧消息');
    await page.getByRole('button',{name:'取消引用',exact:true}).click();
    await bubble.focus(); await bubble.press('Shift+F10');
    await expect(page.getByRole('menu')).toBeVisible(); await page.keyboard.press('Escape');
    await expect(page.getByRole('menu')).toHaveCount(0);
    await bubble.click({button:'right'}); await page.locator('#msg-input').click();
    await expect(page.getByRole('menu')).toHaveCount(0);
    if (mobile) {
      await bubble.evaluate(target => {
        target.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',pointerId:8,isPrimary:true,clientX:60,clientY:160}));
        target.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerType:'touch',pointerId:8,isPrimary:true,clientX:60,clientY:220}));
      });
      await page.waitForTimeout(600);
      await expect(page.getByRole('menu')).toHaveCount(0);
    }
    await page.evaluate(()=>toggleChatActions());
    await page.locator('#action-group-rename').click();
    await page.getByRole('textbox',{name:'群名',exact:true}).fill('我们的新群');
    await page.getByRole('button',{name:'保存群名',exact:true}).click();
    await expect(page.locator('#chat-title')).toHaveText('我们的新群');
    await page.evaluate(()=>applyGroupDetail({group_id:'rg_one',version:1,title:'旧快照',members:[]}));
    await expect(page.locator('#chat-title')).toHaveText('我们的新群');
    expect(calls.some(call=>call.method==='PATCH'&&call.path==='/proxy_groups/rg_one'&&call.body.title==='我们的新群')).toBe(true);
    await page.evaluate(()=>toggleChatActions());
    await page.locator('#chat-actions-sheet').getByRole('button',{name:'查找聊天记录'}).click();
    await page.getByRole('searchbox',{name:'搜索聊天记录'}).fill('早期');
    await page.getByRole('button',{name:'搜索',exact:true}).click();
    await expect(page.locator('.gn-search-results')).toContainText('早期历史 <img');
    await expect(page.locator('.gn-search-results img')).toHaveCount(0);
    await page.locator('.gn-search-result').click({button:'right'});
    await page.getByRole('menuitem',{name:'引用回复',exact:true}).click();
    await expect(page.locator('#group-reply-preview')).toBeVisible();
    await expect(page.locator('#group-reply-preview')).toContainText('Bob');
    await page.locator('#msg-input').fill('答复旧消息');
    await page.locator('#send-btn').click();
    await expect(page.locator('#group-reply-preview')).toBeHidden();
    expect(calls.some(call=>call.method==='POST'&&call.path==='/proxy_groups/rg_one/messages'&&call.body.reply_to===2)).toBe(true);
    await expect(page.locator('.msg-reply-quote')).toContainText('早期历史');
    await page.evaluate(()=>GroupNetworkUI.sharing('rg_one'));
    await page.getByRole('button',{name:'生成邀请链接',exact:true}).click();
    await expect(page.getByAltText('扫描二维码加入群聊')).toBeVisible();
    await expect(page.locator('.gn-link')).toHaveValue('https://chat.example/group-guest#exact-ticket');
    await expect(page.getByRole('link',{name:'保存二维码'})).toHaveAttribute('download','group-invite.svg');
    expect(await page.locator('.gn-qr').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
    await page.locator('.group-network-dialog').getByRole('button',{name:'关闭',exact:true}).click();
    await page.evaluate(()=>GroupNetworkUI.sharing('rg_one'));
    await expect(page.getByAltText('扫描二维码加入群聊')).toBeVisible();
    expect(calls.filter(call=>call.path.endsWith('/guest-link')).length).toBe(1);
    expect(calls.find(call=>call.path.endsWith('/guest-qr')).body.url).toBe('https://chat.example/group-guest#exact-ticket');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
}

test('guest history search quotes older messages and preserves the reference on retry',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let attempts=0;const messages=[{id:100,sender:'alice',sender_name:'Alice',content:'最近的消息',created_at:1790000000}];
  await page.route('**/group-guest-api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),action=url.pathname.split('/').pop();
    if(action==='info') return route.fulfill({json:{title:'Friends'}});
    if(action==='join') return route.fulfill({json:{token:'guest-token'}});
    if(action==='state') return route.fulfill({json:{title:'Friends',principal:'bob',name:'Bob',members:[{principal:'bob',name:'Bob'}],messages,cursor:101}});
    if(action==='search') {
      expect(url.searchParams.get('query')).toBe('旧消息');
      return route.fulfill({json:{messages:[{id:2,sender:'alice',sender_name:'Alice',content:'旧消息 <script>bad</script>',created_at:1780000000}],next_before_id:0}});
    }
    if(action==='messages') {
      const body=request.postDataJSON();expect(body.reply_to).toBe(2);
      if(++attempts===1) return route.fulfill({status:503,json:{error:'暂时断线'}});
      messages.push({id:101,sender:'bob',sender_name:'Bob',content:body.content,reply_to:2,reply:{id:2,sender_name:'Alice',content:'旧消息 <script>bad</script>'},created_at:1790000010});
      return route.fulfill({json:{created:true}});
    }
    return route.fulfill({json:{}});
  });
  await page.goto('/group-guest#invite');await page.locator('#name').fill('Bob');await page.locator('#password').fill('guest-password');
  await page.getByRole('button',{name:'进入群聊',exact:true}).click();await expect(page.locator('#chat')).toBeVisible();
  await page.locator('#history summary').click();await page.locator('#history-query').fill('旧消息');await page.getByRole('button',{name:'搜索',exact:true}).click();
  await expect(page.locator('#history-results')).toContainText('旧消息 <script>');
  await page.locator('.history-result').click({button:'right'});
  await page.getByRole('menuitem',{name:'引用回复',exact:true}).click();await expect(page.locator('#reply-preview')).toBeVisible();
  await page.locator('#text').fill('引用回答');await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('#status')).toContainText('暂时断线');await expect(page.locator('#reply-preview')).toBeVisible();
  await page.getByRole('button',{name:'发送',exact:true}).click();await expect(page.locator('#reply-preview')).toBeHidden();
  await expect(page.locator('#messages .quote')).toContainText('旧消息 <script>');
  await page.reload();await expect(page.locator('#messages .quote')).toContainText('旧消息');
  await expect(page.locator('.quote-action')).toHaveCount(0);
  await holdMessage(page.locator('#messages [data-group-message-id="100"]'));
  await page.getByRole('menuitem',{name:'引用回复',exact:true}).click();
  await expect(page.locator('#reply-preview')).toContainText('最近的消息');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);expect(errors).toEqual([]);
});

for (const viewport of [{width:390,height:844},{width:360,height:640}]) {
  test(`mobile settings fit ${viewport.width}px and retain edits across sections`, async ({page}) => {
    await page.setViewportSize(viewport);
    await mobileSetup(page);
    const saved=[];
    await page.route('**/proxy_settings_full',route=>{
      if(route.request().method()==='POST') {
        saved.push(route.request().postDataJSON().settings);
        return route.fulfill({json:{status:'success',updated:['LLM_MODEL']}});
      }
      return route.fulfill({json:{settings:{LLM_PROVIDER:'openai',LLM_MODEL:'mobile-model',PORT_AGENT:'51200'}}});
    });
    await page.route('**/proxy_tunnel/status',route=>route.fulfill({json:{running:false,provider:'hub',public_domain:''}}));
    await page.evaluate(()=>openSettings());
    const modal=page.locator('#settings-modal');
    await expect(page.getByLabel('默认模型',{exact:true})).toHaveValue('mobile-model');
    await page.getByLabel('默认模型',{exact:true}).fill('mobile-draft');
    await modal.screenshot({path:`/tmp/clawcross-settings-mobile-${viewport.width}.png`});
    for(const category of ['public','channels','integrations','account','advanced','models']) {
      await page.locator(`[data-settings-tab="${category}"]`).click();
      await expect(modal.locator('.settings-footer .settings-btn-save')).toBeInViewport();
      const bounds=await modal.locator('.settings-modal').boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x+bounds.width).toBeLessThanOrEqual(viewport.width);
      expect(bounds.y+bounds.height).toBeLessThanOrEqual(viewport.height);
      expect(await modal.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    }
    await expect(page.getByLabel('默认模型',{exact:true})).toHaveValue('mobile-draft');
    await page.locator('[data-settings-tab="public"]').click();
    await expect(page.locator('#tunnel-provider')).toHaveValue('hub');
    await expect(page.locator('#tunnel-cloudflare-download')).toBeHidden();
    await page.locator('#tunnel-provider').selectOption('cloudflare');
    await expect(page.locator('#tunnel-cloudflare-download')).toBeVisible();
    await page.getByRole('searchbox',{name:'搜索设置'}).fill('PORT_AGENT');
    await expect(page.locator('[data-key="PORT_AGENT"]')).toBeVisible();
    await modal.locator('.settings-footer .settings-btn-save').click();
    expect(saved).toEqual([{LLM_MODEL:'mobile-draft'}]);
    await page.evaluate(()=>openSettings('user-password'));
    await expect(page.locator('#settings-user-password')).toBeVisible();
    await expect(page.locator('[data-settings-tab="account"]')).toHaveAttribute('aria-pressed','true');
  });
}

test('adding group members reports refusal details and retries only failed selections',async({page})=>{
  await mobileSetup(page);
  const attempts=[];
  let blocked=true;
  await page.route('**/proxy_groups/rg_one/members',route=>{
    const aid=route.request().postDataJSON().agent;attempts.push(aid);
    if(aid==='needs-retry' && blocked)return route.fulfill({status:409,json:{error:'这个名字已有人使用，请换一个'}});
    return route.fulfill({json:{group_id:'rg_one',title:'朋友们',version:20,kind:'group',members:[]}});
  });
  await page.evaluate(()=>{
    document.getElementById('add-agent-modal').classList.add('show');
    document.getElementById('add-agent-team-select').innerHTML='<option value="">全部 Agent</option>';
    addAgentSelected.clear();addAgentPicks.clear();
    for(const id of ['already-added','needs-retry']) {
      addAgentSelected.add('agent:'+id);addAgentPicks.set('agent:'+id,{agent_id:id});
    }
    return confirmAddAgents();
  });
  await expect(page.locator('#add-agent-error')).toContainText('这个名字已有人使用，请换一个');
  await expect(page.locator('#add-agent-modal')).toHaveClass(/show/);
  expect(await page.evaluate(()=>[...addAgentSelected])).toEqual(['agent:needs-retry']);
  blocked=false;
  await page.evaluate(()=>confirmAddAgents());
  expect(attempts).toEqual(['already-added','needs-retry','needs-retry']);
  await expect(page.locator('#add-agent-error')).toHaveCount(0);
  await expect(page.locator('#add-agent-modal')).not.toHaveClass(/show/);
});

for(const consent of [false,true]) {
  test(`private chat conversion ${consent?'preserves the conversation':'can be cancelled before any write'}`,async({page})=>{
    await mobileSetup(page);
    const writes=[];
    await page.route('**/proxy_groups/rg_one/members',route=>{
      writes.push(route.request().postDataJSON());
      return route.fulfill({json:{group_id:'rg_one',kind:'group',title:'朋友们',version:101,members:[]}});
    });
    page.on('dialog',dialog=>{
      expect(dialog.message()).toContain('原有聊天记录也会对新成员可见');
      return consent?dialog.accept():dialog.dismiss();
    });
    await page.evaluate(()=>{
      applyGroupDetail({group_id:'rg_one',kind:'direct',title:'朋友们',version:100,members:[]});
      addAgentSelected.clear();addAgentPicks.clear();
      addAgentSelected.add('agent:second');addAgentPicks.set('agent:second',{agent_id:'second'});
      return confirmAddAgents();
    });
    expect(writes).toEqual(consent?[{agent:'second',convert_to_group:true}]:[]);
    expect(await page.evaluate(()=>currentGroupId)).toBe('rg_one');
    await expect(page.locator('#chat-body')).toContainText('旧消息');
    expect(await page.evaluate(()=>_cacheGetFresh(__mobileGroupDetailCache,'rg_one').kind)).toBe(consent?'group':'direct');
  });
}

test('room metadata polls preserve iframe focus and do not pull it back from the host page',async({page})=>{
  await mobileSetup(page);
  const appGroup={group_id:'rg_one',title:'朋友们',kind:'group',version:1,members:[],app:{name:'精灵对战',online:true}};
  await page.route('**/proxy_groups',route=>route.fulfill({json:{groups:[appGroup]}}));
  await page.route('**/proxy_groups/rg_one',route=>route.fulfill({json:appGroup}));
  await page.route('**/proxy_groups/rg_one/app-session',route=>route.fulfill({json:{url:'/focus-fixture/'}}));
  await page.route('**/focus-fixture/',route=>route.fulfill({contentType:'text/html',body:'<textarea id="game-input" aria-label="Game input"></textarea>'}));
  await page.evaluate(()=>MobileRooms.update({group_id:'rg_one',app:{name:'精灵对战',online:true}}));
  const frame=page.frameLocator('#room-app-surface iframe');
  await frame.getByLabel('Game input').fill('do not lose my draft');
  await frame.getByLabel('Game input').evaluate(input=>input.setSelectionRange(3,7));
  await page.evaluate(()=>MobileRooms.metadata({group_id:'rg_one',app:{name:'精灵对战',online:true}}));
  await expect(frame.getByLabel('Game input')).toBeFocused();
  expect(await frame.getByLabel('Game input').evaluate(input=>[input.selectionStart,input.selectionEnd])).toEqual([3,7]);
  await page.evaluate(()=>{
    const button=document.createElement('button');button.id='outside-app';button.textContent='Outside app';
    button.style.cssText='position:fixed;top:10px;left:10px;z-index:99999';document.body.append(button);
  });
  await page.locator('#outside-app').click();
  await page.evaluate(()=>{
    for(let i=0;i<5;i++)MobileRooms.metadata({group_id:'rg_one',app:{name:'精灵对战',online:true}});
  });
  await expect(page.locator('#outside-app')).toBeFocused();
  await expect(frame.getByLabel('Game input')).toHaveValue('do not lose my draft');
});

test('shared app rendering restores the caret only while its document has focus',async({page})=>{
  const {page:appPage}=require('../../sdk/js/space-app.js');
  await page.setContent('<button id="outside">Other controls</button><iframe id="app" style="width:800px;height:500px"></iframe>');
  await page.locator('#app').evaluate((el,html)=>{el.srcdoc=html;},appPage({title:'Focus fixture',body:'<div id="fields"><textarea id="draft" aria-label="Draft"></textarea></div>'}));
  const input=page.frameLocator('#app').getByLabel('Draft');
  await input.fill('abcdef');
  await input.evaluate(el=>el.setSelectionRange(2,4,'backward'));
  await input.evaluate(()=>App.fill(document.getElementById('fields'),App.keep('draft',()=>null)));
  await expect(input).toBeFocused();
  expect(await input.evaluate(el=>[el.selectionStart,el.selectionEnd,el.selectionDirection])).toEqual([2,4,'backward']);
  await input.evaluate(el=>{window.focusEvents=0;el.addEventListener('focus',()=>window.focusEvents++);});
  await page.locator('#outside').click();
  expect(await input.evaluate(()=>document.hasFocus())).toBe(false);
  await input.evaluate(()=>App.fill(document.getElementById('fields'),App.keep('draft',()=>null)));
  expect(await input.evaluate(()=>window.focusEvents)).toBe(0);
  await expect(page.locator('#outside')).toBeFocused();
});
