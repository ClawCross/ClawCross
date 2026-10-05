const {test,expect}=require('@playwright/test');
const path=require('node:path');
for(const [name,width,height] of [['studio',1100,800],['mobile',390,844]]) {
    test(`${name} workspace sources save switches and only custom paths`,async({page})=>{
        await page.setViewportSize({width,height});
        await page.route('**/studio',route=>route.fulfill({contentType:'text/html',body:'<html lang="zh"><body><button id="open">工作区</button></body></html>'}));
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
        await page.addStyleTag({path:path.resolve('src/frontend/static/css/runtime-settings.css')});
        await page.addStyleTag({path:path.resolve('src/frontend/static/css/workspace-settings.css')});
        await page.addScriptTag({path:path.resolve('src/frontend/static/js/workspace-settings.js')});
        await page.evaluate(()=>{document.getElementById('open').onclick=()=>openWorkspaceSettings('agent-1');});
        await page.locator('#open').click();
        const dialog=page.locator('#workspace-settings-modal');
        await expect(dialog.getByRole('button',{name:'保存',exact:true})).toBeEnabled();
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
    });
}
