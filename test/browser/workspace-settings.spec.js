const {test,expect}=require('@playwright/test');
const path=require('node:path');
const fs=require('node:fs');
test('a new Studio Agent can configure workspaces before its first conversation',async({page})=>{
    let created=0;const writes=[];
    const settings={companion:true,user_shared:false,cli:true,teams:true,paths:[]};
    await page.route('**/studio',route=>route.fulfill({contentType:'text/html',body:'<html lang="zh"><body></body></html>'}));
    await page.route('**/v1/agents/draft-agent/workspaces',route=>route.fulfill(created?{json:{settings,folders:[]}}:{status:404,json:{detail:'Agent not found'}}));
    await page.route('**/v1/agents',route=>{created++;return route.fulfill({json:{agent_id:'draft-agent'}});});
    await page.route('**/v1/agents/draft-agent',route=>{writes.push(route.request().postDataJSON());return route.fulfill({json:{agent_id:'draft-agent'}});});
    await page.goto('/studio');
    await page.addScriptTag({path:path.resolve('src/frontend/static/js/runtime-settings.js')});
    await page.addScriptTag({path:path.resolve('src/frontend/static/js/workspace-settings.js')});
    await page.evaluate(()=>{
        window.currentSessionId='draft-agent';
        window.studioEnsureAgent=(id,fields)=>fetch('/v1/agents',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent_id:id,...fields})});
        return openWorkspaceSettings('draft-agent');
    });
    expect(created).toBe(0);
    const dialog=page.locator('#workspace-settings-modal');
    await expect(dialog.locator('[name="companion"]')).toBeDisabled();
    await dialog.getByRole('button',{name:'创建并配置工作区',exact:true}).click();
    await expect(dialog.locator('[name="companion"]')).toBeEnabled();expect(created).toBe(1);
    await dialog.locator('[name="paths"]').fill('/my/project');
    await dialog.getByRole('button',{name:'保存',exact:true}).click();
    await expect(dialog.getByRole('status')).toContainText('已保存');
    expect(writes[0].settings.workspaces.paths).toEqual(['/my/project']);expect(created).toBe(1);
});
for(const [name,width,height] of [['studio',1100,800],['mobile',390,844]]) {
    test(`${name} workspace sources save switches and only custom paths`,async({page})=>{
        await page.setViewportSize({width,height});
        await page.route('**/studio',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<html lang="zh"><body><div id="runtime-settings-modal" class="settings-modal-overlay open" style="display:flex"><div class="settings-modal runtime-settings-dialog"><button id="open">工作区</button><button id="parent-last">原窗口</button></div></div></body></html>'}));
        const writes=[];
        const settings={companion:true,user_shared:false,cli:true,teams:true,paths:[]};
        await page.route('**/v1/agents/agent-1/workspaces',route=>route.fulfill({json:{settings,folders:[
            {source:'companion',path:'/runtime/users/alice/agents/agent-1'},
            {source:'team',team:'project',path:'/runtime/teams/alice/project/workspace'}]}}));
        await page.route('**/v1/agents/agent-1',route=>{
            const body=route.request().postDataJSON(); writes.push(body); Object.assign(settings,body.settings.workspaces);
            return route.fulfill({json:{agent_id:'agent-1',settings}});
        });
        await page.goto('/studio');
        if (name==='mobile') {
            const mobile=fs.readFileSync(path.resolve('src/frontend/templates/group_chat_mobile.html'),'utf8');
            await page.addStyleTag({content:mobile.match(/<style[^>]*>([\s\S]*?)<\/style>/)[1]});
        } else {
            await page.addStyleTag({path:path.resolve('src/frontend/static/css/style.css')});
        }
        await page.addStyleTag({path:path.resolve('src/frontend/static/css/runtime-settings.css')});
        await page.addStyleTag({path:path.resolve('src/frontend/static/css/workspace-settings.css')});
        await page.addScriptTag({path:path.resolve('src/frontend/static/js/runtime-settings.js')});
        await page.addScriptTag({path:path.resolve('src/frontend/static/js/workspace-settings.js')});
        await page.evaluate(()=>{runtimeSettingsView={returnFocus:document.getElementById('open')}; document.getElementById('open').onclick=()=>openWorkspaceSettings('agent-1');});
        await page.locator('#open').click();
        const dialog=page.locator('#workspace-settings-modal');
        await expect(dialog.getByRole('button',{name:'保存',exact:true})).toBeEnabled();
        const visibleAboveParent=await dialog.evaluate(el=>{
            const parent=document.getElementById('runtime-settings-modal');
            const box=el.querySelector('[name="companion"]').getBoundingClientRect();
            return getComputedStyle(el).position==='fixed' &&
                Number(getComputedStyle(el).zIndex)>Number(getComputedStyle(parent).zIndex) &&
                box.x>=0 && box.right<=innerWidth && box.y>=0 && box.bottom<=innerHeight &&
                el.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2));
        });
        expect(visibleAboveParent).toBe(true);
        await dialog.getByRole('button',{name:'关闭',exact:true}).focus();
        await page.keyboard.press('Tab');
        expect(await dialog.evaluate(el=>el.contains(document.activeElement))).toBe(true);

        await expect(dialog.locator('[name="user_shared"]')).not.toBeChecked();
        await dialog.locator('[name="user_shared"]').check();
        await dialog.locator('[name="paths"]').fill('/my/project\n/another/project');
        await dialog.getByRole('button',{name:'保存',exact:true}).click();
        await expect(dialog.getByRole('status')).toContainText('已保存');
        expect(writes).toEqual([{settings:{workspaces:{companion:true,user_shared:true,cli:true,teams:true,paths:['/my/project','/another/project']}}}]);
        const overflowing=await page.evaluate(()=>[...document.querySelectorAll('#workspace-settings-modal input,#workspace-settings-modal textarea,#workspace-settings-modal button')].some(el=>{const box=el.getBoundingClientRect();return box.x<0||box.right>innerWidth+1;}));
        expect(overflowing).toBe(false);
        await dialog.getByRole('button',{name:'关闭',exact:true}).click();
        await expect(dialog).toHaveCount(0); await expect(page.locator('#open')).toBeFocused();
        await expect(page.locator('#runtime-settings-modal')).toBeVisible();
    });
}
