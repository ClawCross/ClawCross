const {test,expect}=require('@playwright/test');
test.use({launchOptions:{executablePath:process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome'}});
const settings={context:{auto_compact:true,context_window_tokens:1000000,history_tokens:0,trigger_tokens:0,target_tokens:0,preserve_recent_turns:4,summary_tokens:2000,summarizer_input_tokens:8000,summarizer_model:'',preserve_instructions:''},approval:{mode:'auto',reviewer_policy:'',reviewer_model:'',reviewer_timeout_seconds:30,command_sandbox:'off'}};

test('Studio plus keeps runtime settings while global settings expose contextual component downloads',async({page})=>{
  const installs=[];
  await page.route('**/proxy_check_session',route=>route.fulfill({contentType:'application/json',body:'{"valid":true,"user_id":"tester"}'}));
  await page.route('**/api/llm_config_status',route=>route.fulfill({contentType:'application/json',body:'{"configured":true}'}));
  await page.route('**/api/setup_status',route=>route.fulfill({contentType:'application/json',body:'{"llm_configured":true}'}));
  await page.route('**/proxy_webot_runtime_settings**',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({settings})}));
  await page.route('**/proxy_components/*',route=>{
    const name=new URL(route.request().url()).pathname.split('/').pop();
    if(route.request().method()==='POST') installs.push(name);
    return route.fulfill({contentType:'application/json',body:JSON.stringify({name,installed:installs.includes(name),ready:installs.includes(name),missing:[],can_install:true,platform:'linux'})});
  });
  await page.goto('/studio');
  await page.evaluate(()=>{currentLang='zh-CN';currentSessionId='s1';document.getElementById('login-screen').style.display='none';document.getElementById('chat-screen').style.display='flex';});
  await expect(page.locator('#app-splash')).toBeHidden();
  await expect(page.locator('#setup-wizard-modal')).toBeHidden();
  const menu=page.locator('#studio-more-menu');
  await menu.locator(':scope > summary').click();
  await page.locator('#studio-runtime-menu > summary').click();
  await page.locator('#studio-sandbox-settings').click();
  await expect(page.locator('#runtime-settings-approval')).toBeVisible();
  await expect(page.locator('[data-key="command_sandbox"]')).toHaveValue('off');
  await expect(page.locator('#runtime-settings-modal [data-component="srt"] button')).toBeVisible();
  await page.locator('#runtime-settings-modal .runtime-settings-close').click();
  await expect(page.locator('#studio-more-menu')).not.toContainText('连接聊天平台');
  await expect(page.locator('#studio-more-menu')).not.toContainText('公网访问');
  await expect(page.locator('#studio-components-menu')).toHaveCount(0);
  await page.route('**/proxy_settings_full',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({settings:{}})}));
  await page.evaluate(()=>openSettings());
  await expect(page.locator('#settings-external-agents [data-component="acpx"] button')).toBeVisible();
  for(const name of ['weclaw','nonebot','channels','cloudflared']) await expect(page.locator(`#settings-body [data-component="${name}"] button`)).toBeVisible();
  await expect(page.locator('#settings-body .settings-group').filter({has:page.locator('[data-component="weclaw"]')})).toContainText('机器人集成');
  expect(installs).toEqual([]);
  await page.locator('#settings-body [data-component="weclaw"] button').click();
  await expect(page.locator('#settings-body [data-component="weclaw"]')).toContainText('已安装');
  expect(installs).toEqual(['weclaw']);
});

test('completed backend status clears the compaction spinner even when local polling was stale',async({page})=>{
  await page.goto('/studio');
  await page.evaluate(()=>{
    currentLang='zh-CN';currentSessionId='s1';
    sessionCompactBusy=true;sessionCompactStatus='正在后台整理早期对话…';
    updateBackendCompactionStatus({job_id:'job1',state:'running',kind:'manual'});
  });
  await expect(page.locator('#session-compact-btn')).toContainText('压缩中');
  await page.evaluate(()=>updateBackendCompactionStatus({job_id:'job1',state:'completed',kind:'manual'}));
  await expect(page.locator('#session-compact-btn')).toContainText('压缩历史');
  await expect(page.locator('#session-compact-btn')).toBeEnabled();
  await expect(page.locator('#session-context-detail')).not.toContainText('正在后台整理');
  await expect(page.locator('#session-context-detail')).toContainText('已完成');
});
