const {test,expect}=require('@playwright/test');
const path=require('node:path');

test('saved SSH devices register ordinary Agents and disconnect without deleting them',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await page.route('**/studio',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body></body></html>'}));
  const rows=[],agents=[],calls=[];
  await page.route('**/v1/connections**',route=>{
    const req=route.request(),pathname=new URL(req.url()).pathname;calls.push({path:pathname,method:req.method()});
    if(req.method()==='POST'&&pathname==='/v1/connections')rows.push({id:'ssh_device',...req.postDataJSON(),state:'disconnected'});
    if(pathname.endsWith('/connect')){rows[0].state='connected';return route.fulfill({json:{...rows[0],remote:{acpx_available:true}}});}
    if(pathname.endsWith('/disconnect'))rows[0].state='paused';
    return route.fulfill({json:req.method()==='GET'?{data:rows}:rows[0]||{}});
  });
  await page.route('**/v1/agents',route=>{
    const body=route.request().postDataJSON();agents.push(body);return route.fulfill({json:{agent_id:'remote-agent',...body}});
  });
  await page.goto('/studio');
  await page.addStyleTag({path:path.resolve('src/frontend/static/css/connections.css')});
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/connections.js')});
  await page.evaluate(()=>{void DeviceConnections.open();});
  await page.getByRole('button',{name:'添加设备',exact:true}).click();
  await page.getByLabel('设备名称',{exact:true}).fill('远端笔记本');
  await page.getByLabel('IP、域名或 SSH 配置别名').fill('laptop');
  await page.getByRole('button',{name:'保存连接',exact:true}).click();
  await expect(page.locator('.device-connection-row')).toContainText('远端笔记本');
  await page.getByRole('button',{name:'连接并检测',exact:true}).click();
  await expect(page.locator('.device-connection-row')).toContainText('已连接');
  await page.getByRole('button',{name:'新增远端 Agent',exact:true}).click();
  await page.getByLabel('Agent 名称',{exact:true}).fill('代码伙伴');
  await page.getByLabel('平台',{exact:true}).fill('claude');
  await page.getByLabel('远端工作目录',{exact:true}).fill('/projects/demo');
  await page.getByRole('button',{name:'创建 Agent',exact:true}).click();
  await expect.poll(()=>agents.length).toBe(1);
  expect(agents[0]).toMatchObject({name:'代码伙伴',platform:'claude',connection_id:'ssh_device',remote_cwd:'/projects/demo'});
  await page.getByRole('button',{name:'断开',exact:true}).click();
  await expect(page.locator('[data-status]')).toContainText('Agent 和历史保留');
  expect(calls.filter(call=>call.method==='DELETE')).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
