const { test, expect } = require('@playwright/test');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

for (const mobile of [false, true]) {
  test(`guest chat: ${mobile ? 'mobile' : 'desktop'} identity and messages`, async ({ page }) => {
    await page.setViewportSize(mobile ? {width:390,height:844} : {width:1100,height:800});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    let joins = 0, renamed = false;
    const messages = [{id:1,sender:'host',sender_name:'Alice',content:'欢迎 <script>alert(1)</script>',created_at:1790000000}];
    await page.route('**/group-guest-api/**', async route => {
      const action = new URL(route.request().url()).pathname.split('/').pop();
      let result = {}, status = 200;
      if (action === 'info') result = {title:'朋友们'};
      if (action === 'join') {
        if (++joins === 1) {status=409; result={detail:'这个名字已有人使用，请换一个'};}
        else result={token:'guest-token',name:'Bob'};
      }
      if (action === 'state') result={title:'朋友们',name:renamed?'Carol':'Bob',principal:'bob',members:[{principal:'alice',name:'Alice'},{principal:'bot',name:'创意专家'},{principal:'bob',name:renamed?'Carol':'Bob'}],messages,cursor:messages.length,has_more:false};
      if (action === 'messages') {
        expect(route.request().headers()['x-guest-token']).toBe('guest-token');
        const data=route.request().postDataJSON();
        if (data.content.includes('@创意专家')) expect(data.mentions).toEqual(['bot']);
        if (data.content.includes('@Alice')) expect(data.mentions).toEqual(['alice']);
        messages.push({id:messages.length+1,sender:'bob',sender_name:'Bob',content:data.content,created_at:1790000010}); result={id:messages.length};
      }
      if (action === 'rename') {renamed=true;result={name:'Carol'};}
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(result)});
    });
    await page.goto('/group-guest#test-invite');
    await page.locator('#name').fill('Alice'); await page.getByRole('button',{name:'进入群聊'}).click();
    await expect(page.locator('#status')).toContainText('已有人使用');
    await page.locator('#name').fill('Bob'); await page.getByRole('button',{name:'进入群聊'}).click();
    await expect(page.locator('#chat')).toBeVisible();
    await expect(page.locator('#messages')).toContainText('<script>alert(1)</script>');
    await page.locator('#text').fill('大家好'); await page.getByRole('button',{name:'发送',exact:true}).click();
    await expect(page.locator('#messages')).toContainText('大家好');
    await page.locator('#members summary').click();
    await expect(page.locator('#member-list')).toContainText('创意专家');
    await page.getByRole('button',{name:'提及 创意专家',exact:true}).click();
    await page.locator('#text').press('End'); await page.locator('#text').pressSequentially('你好');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await expect(page.locator('#messages')).toContainText('@创意专家 你好');
    await page.locator('#text').fill('@Ali');
    await expect(page.getByRole('listbox')).toBeVisible();
    await page.locator('#text').press('Enter');
    await expect(page.locator('#text')).toHaveValue('@Alice ');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await expect(page.locator('#messages')).toContainText('@Alice');
    await page.getByRole('button',{name:'提及群成员',exact:true}).click();
    await page.getByRole('option',{name:'创意专家',exact:true}).click();
    await page.locator('#text').fill('删掉提及');
    await page.getByRole('button',{name:'发送',exact:true}).click();
    await expect(page.locator('#messages')).toContainText('删掉提及');
    page.once('dialog', dialog => dialog.accept('Carol')); await page.getByRole('button',{name:'改名',exact:true}).click();
    await expect(page.locator('#status')).toContainText('Carol');
    await page.reload(); await expect(page.locator('#chat')).toBeVisible();
    expect(joins).toBe(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await expect(page.getByRole('button',{name:/管理|添加.*agent/i})).toHaveCount(0);
  });
}
