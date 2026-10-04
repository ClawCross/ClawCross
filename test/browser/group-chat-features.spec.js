const {test,expect}=require('@playwright/test');

async function mobileSetup(page) {
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{localStorage.setItem('clawcross_lang','zh');window.alert=()=>{};});
  await page.route('**/proxy_check_session',route=>route.fulfill({json:{valid:true,user_id:'alice',has_password:true,mode:'local'}}));
  await page.route('**/api/llm_config_status',route=>route.fulfill({json:{configured:true}}));
  let title='朋友们';
  const messages=[{id:10,sender:'u:alice',sender_name:'Alice',content:'旧消息 <script>bad</script>',created_at:1790000000}];
  const calls=[];
  await page.route('**/proxy_groups**',async route=>{
    const request=route.request(),url=new URL(request.url());calls.push({path:url.pathname,method:request.method(),body:request.postDataJSON()});
    const group={group_id:'rg_one',title,kind:'group',owner:'alice',members:[{principal:'u:alice',name:'Alice',is_agent:false}],messages,federated:true,member_count:1};
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
    if(request.method()==='PATCH') title=request.postDataJSON().title;
    return route.fulfill({json:request.method()==='PATCH'?{...group,title}:group});
  });
  await page.goto('/mobile/group_chat');
  await expect(page.locator('#tab-chats')).toBeVisible();
  await page.evaluate(()=>openChat('rg_one','朋友们'));
  return {errors,calls};
}

for(const mobile of [false,true]) {
  test(`group features: ${mobile?'mobile':'desktop'} rename, search, quote and invite QR`,async({page})=>{
    await page.setViewportSize(mobile?{width:390,height:844}:{width:1200,height:850});
    const {errors,calls}=await mobileSetup(page);
    await expect(page.locator('#chat-body')).toContainText('旧消息');
    await page.evaluate(()=>GroupNetworkUI.rename('rg_one'));
    await page.getByRole('textbox',{name:'群名',exact:true}).fill('我们的新群');
    await page.getByRole('button',{name:'保存群名',exact:true}).click();
    await expect(page.locator('#chat-title')).toHaveText('我们的新群');
    expect(calls.some(call=>call.method==='PATCH'&&call.path==='/proxy_groups/rg_one'&&call.body.title==='我们的新群')).toBe(true);
    await page.evaluate(()=>GroupNetworkUI.search('rg_one'));
    await page.getByRole('searchbox',{name:'搜索聊天记录'}).fill('早期');
    await page.getByRole('button',{name:'搜索',exact:true}).click();
    await expect(page.locator('.gn-search-results')).toContainText('早期历史 <img');
    await expect(page.locator('.gn-search-results img')).toHaveCount(0);
    await page.getByRole('button',{name:'引用回复',exact:true}).click();
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
    await page.getByRole('button',{name:'关闭',exact:true}).click();
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
  await page.getByRole('button',{name:'引用回复',exact:true}).click();await expect(page.locator('#reply-preview')).toBeVisible();
  await page.locator('#text').fill('引用回答');await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.locator('#status')).toContainText('暂时断线');await expect(page.locator('#reply-preview')).toBeVisible();
  await page.getByRole('button',{name:'发送',exact:true}).click();await expect(page.locator('#reply-preview')).toBeHidden();
  await expect(page.locator('#messages .quote')).toContainText('旧消息 <script>');
  await page.reload();await expect(page.locator('#messages .quote')).toContainText('旧消息');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);expect(errors).toEqual([]);
});
