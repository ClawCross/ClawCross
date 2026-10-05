const {test, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const main = fs.readFileSync(path.resolve('src/frontend/static/js/main.js'), 'utf8');
const mobile = fs.readFileSync(path.resolve('src/frontend/templates/group_chat_mobile.html'), 'utf8');
const between = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const helpers = `function escapeHtml(value) { const el=document.createElement('div'); el.textContent=value??''; return el.innerHTML.replace(/"/g,'&quot;'); }
function esc(value) { return escapeHtml(value); } function t(value) { return value; }`;

test('Studio separates fixed user space and loads Skill edits by directory key', async ({page}) => {
    await page.route('**/studio', route => route.fulfill({contentType:'text/html', body:`<html lang="zh"><body>
        <div id="group-list"></div><div id="team-skills-list"></div><div id="team-skill-view-empty"></div>
        <div id="team-skill-view"><span id="team-skill-view-name"></span><span id="team-skill-view-meta"></span>
        <textarea id="team-skill-view-content"></textarea><div id="team-skill-view-status"></div><button id="team-skill-save-btn">保存</button></div>
        <table><tbody id="team-experts-table-body"></tbody></table></body></html>`}));
    let content = 'first body';
    await page.route('**/teams/__default__/skills', route => route.fulfill({json:{skills:{team:[],personal:[
        {id:'folder-key',name:'中文显示名',description:'user workspace skill'},
        {id:'other-skill',name:'另一个 Skill',description:'another skill'}]}}}));
    const writes = [];
    await page.route('**/teams/__default__/skills/*', route => {
        if (route.request().method() === 'PUT') {
            writes.push(route.request().postDataJSON());
            content = writes.at(-1).content;
        }
        const second = route.request().url().includes('/other-skill');
        return route.fulfill({json:{skill:{name:second?'另一个 Skill':'中文显示名',content:second?'second body':content}}});
    });
    await page.route('**/teams/__default__/experts', route => route.fulfill({json:{experts:[
        {tag:'preset',name:'预设人设',persona:'public persona',deletable:false},
        {tag:'mine',name:"用户的人设",persona:'custom persona',deletable:true}]}}));
    await page.goto('/studio');
    await page.addStyleTag({path:path.resolve('src/frontend/static/css/style.css')});
    await page.addScriptTag({path:path.resolve('src/frontend/static/js/workspace-settings.js')});
    await page.addScriptTag({content: helpers + `let currentGroupId='__default__'; let currentLang='zh';
        let _teamSkillCurrentName=''; let _teamSkillCurrentScope='personal';` +
        between(main, 'function renderGroupList(', 'async function openGroup(') +
        between(main, 'async function loadTeamExperts(', '// ── Team Skills') +
        between(main, 'async function loadTeamSkills(', 'async function deleteTeamSkillDetail(')});
    await page.evaluate(() => {renderGroupList(['project','__default__']); return loadTeamSkills();});
    const fixed = page.locator('#group-list .group-item').first();
    await expect(fixed).toContainText('用户空间');
    await expect(fixed).toContainText('固定视图');
    await expect(fixed.locator('button')).toHaveCount(0);
    await expect(page.locator('[data-team="project"] button')).toHaveCount(2);
    await page.locator('#team-skills-list button', {hasText:'中文显示名'}).click();
    const editor = page.locator('#team-skill-view-content');
    await expect(editor).toHaveValue('first body');
    await editor.fill('unsaved body');
    await page.locator('#team-skills-list button', {hasText:'另一个 Skill'}).click();
    await expect(editor).toHaveValue('second body');
    await page.locator('#team-skills-list button', {hasText:'中文显示名'}).click();
    await expect(editor).toHaveValue('first body');
    await editor.fill('saved body');
    await page.evaluate(() => saveTeamSkillDetail());
    expect(writes).toEqual([{content:'saved body'}]);
    await expect(editor).toHaveValue('saved body');
    await page.evaluate(() => loadTeamExperts());
    await expect(page.locator('#team-experts-table-body tr').first()).toContainText('预设 · 只读');
    await expect(page.locator('#team-experts-table-body tr').first().locator('button')).toHaveCount(0);
    await expect(page.locator('#team-experts-table-body tr').last().locator('button')).toHaveCount(2);
});

test('Mobile Skills use user space and clear the editor when switching to a Team', async ({page}) => {
    await page.setViewportSize({width:390,height:844});
    const markup = between(mobile, '<div class="drawer-overlay side-panel-overlay" id="mobile-skill-manager-overlay"', '<!--')
        .split('<div class="drawer-overlay side-panel-overlay" id="create-agent-overlay"')[0];
    await page.route('**/studio', route => route.fulfill({contentType:'text/html',body:`<html lang="zh"><body>${markup}</body></html>`}));
    await page.route('**/teams', route => route.fulfill({json:{teams:['__default__','real-team']}}));
    await page.route('**/skills', route => route.fulfill({json:{skills:{personal:[{id:'folder-key',name:'中文显示名',description:'user skill'}]}}}));
    await page.route('**/skills/folder-key', route => route.fulfill({json:{skill:{id:'folder-key',name:'中文显示名',content:'user skill body'}}}));
    await page.route('**/teams/real-team/skills', route => route.fulfill({json:{skills:{team:[]}}}));
    await page.goto('/studio');
    await page.addStyleTag({content:mobile.match(/<style[^>]*>([\s\S]*?)<\/style>/)[1]});
    await page.addStyleTag({path:path.resolve('src/frontend/static/css/settings-polish.css')});
    await page.addScriptTag({path:path.resolve('src/frontend/static/js/workspace-settings.js')});
    await page.addScriptTag({content:helpers + `let mobileSkillScope='global'; let mobileSkillCurrentName='';
        async function api(method,url,data) { const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined}); const body=await r.json(); if(!r.ok) throw new Error(body.error); return body; }` +
        between(mobile, 'function mobileSkillStatus(', 'async function importMobileManagedSkillZip(')});
    await page.evaluate(() => showMobileSkillManager());
    await page.getByRole('button',{name:'中文显示名 user skill'}).click();
    await expect(page.locator('#mobile-skill-detail-content')).toHaveValue('user skill body');
    await page.getByRole('button',{name:'Team Skill',exact:true}).click();
    await expect(page.locator('#mobile-skill-team-select option[value="__default__"]')).toHaveCount(0);
    await expect(page.locator('#mobile-skill-detail')).toBeHidden();
    await expect(page.locator('#mobile-skill-list')).toContainText('暂无 Skill');
    const contactOptions = await page.evaluate(() => workspaceTeamOptions(['real-team','__default__']));
    expect(contactOptions.indexOf('value="__default__"')).toBeLessThan(contactOptions.indexOf('<optgroup'));
    expect(contactOptions).toContain('用户空间 · 固定视图');
    expect(await page.evaluate(() => {
        const select=document.createElement('select');
        select.innerHTML=workspaceTeamOptions(['project"\'&','__default__']);
        return select.options[1].value;
    })).toBe('project"\'&');
    const fits = await page.locator('#mobile-skill-manager').evaluate(el => {
        const box=el.getBoundingClientRect(); return box.x>=0 && box.right<=innerWidth+1;
    });
    expect(fits).toBe(true);
});
