/* Shared presentation for Studio and Mobile. Switching sections preserves form drafts. */
window.ClawcrossSettings = (() => {
    const sections = [
        ['models', '模型与语音', 'Models & audio', '设置主机默认的模型连接与语音服务。单个 Agent 的选择在 Agent 设置中调整。', 'Set the host defaults for models and audio. Individual Agents keep their own settings.', 'M4 7h16M7 4v6M4 17h16M17 14v6'],
        ['public', '公网访问', 'Public access', '让其他设备通过 HTTPS 访问本机 ClawCross。', 'Access this ClawCross from other devices over HTTPS.', 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c4 5 4 13 0 18-4-5-4-13 0-18'],
        ['channels', '消息渠道', 'Messaging', '连接常用的消息平台，在熟悉的地方与 Agent 对话。', 'Connect messaging platforms and talk to your Agents where you already chat.', 'M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0ZM8 10h8M8 14h5'],
        ['integrations', '扩展与服务', 'Integrations', '按需连接外部 Agent、安装组件或配置网络检索。', 'Connect external Agents, install optional components, or configure web research.', 'M9 3h6v6h6v6h-6v6H9v-6H3V9h6Z'],
        ['account', '账号与安全', 'Account & security', '管理当前账号的登录密码与主机访问凭证。', 'Manage your login password and host access credentials.', 'M12 3l8 4v5c0 5-8 9-8 9s-8-4-8-9V7ZM9 12l2 2 4-4'],
        ['advanced', '高级设置', 'Advanced', '配置助手、服务端口及其他运行参数。', 'Configuration assistant, service ports, and other runtime options.', 'M4 7h16M4 17h16M8 4v6M16 14v6'],
    ];
    const categories = {llm:'models', tts:'models', network:'public', bots:'channels', tinyfish:'integrations', security:'account'};
    const labels = {
        LLM_PROVIDER:['模型服务商','Provider'], LLM_API_KEY:['API 密钥','API key'],
        LLM_BASE_URL:['API 地址','API base URL'], LLM_MODEL:['默认模型','Default model'],
        LLM_VISION_SUPPORT:['图像识别能力','Vision support'], TTS_MODEL:['语音合成模型','Speech model'],
        TTS_VOICE:['声音','Voice'], STT_MODEL:['语音识别模型','Transcription model'],
        PUBLIC_DOMAIN:['固定公网地址','Fixed public URL'], BARK_PUBLIC_URL:['Bark 服务地址','Bark server URL'],
        INTERNAL_TOKEN:['内部访问凭证','Internal access token'],
    };
    const icon = path => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`;

    function mount(body, language) {
        const zh = language.startsWith('zh');
        const text = (a,b) => zh ? a : b;
        const modal = body.closest('.settings-modal');
        modal.setAttribute('role','dialog');
        modal.setAttribute('aria-modal','true');
        modal.setAttribute('aria-label',text('设置','Settings'));
        let layout = modal.querySelector('.settings-layout');
        if (!layout) {
            layout = document.createElement('div');
            layout.className = 'settings-layout';
            body.before(layout);
            layout.append(body);
        }
        layout.querySelector('.settings-navigation')?.remove();
        body.querySelector(':scope > .settings-hint')?.remove();
        const cards = [...body.children];
        for (const card of cards) card.dataset.settingsCategory ||= 'advanced';
        const importArea = body.querySelector('#settings-import-openclaw-btn')?.parentElement?.parentElement;
        if (importArea?.parentElement.classList.contains('settings-group-body')) {
            const details = document.createElement('details');
            details.className = 'settings-import-options';
            const summary = document.createElement('summary');
            summary.textContent = text('从已有配置导入','Import existing settings');
            details.append(summary);
            importArea.parentElement.append(details);
            details.append(importArea);
        }
        const nav = document.createElement('nav');
        nav.className = 'settings-navigation';
        nav.setAttribute('aria-label',text('设置分类','Settings sections'));
        nav.innerHTML = `<label class="settings-search">${icon('M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0')}<input type="search" placeholder="${text('搜索设置','Search settings')}" aria-label="${text('搜索设置','Search settings')}"></label><div class="settings-nav-items">${sections.map(([id,cn,en,,,path])=>`<button type="button" data-settings-tab="${id}" aria-controls="settings-body">${icon(path)}<span>${text(cn,en)}</span></button>`).join('')}</div><p class="settings-nav-note">ClawCross</p>`;
        layout.prepend(nav);
        const heading = document.createElement('div');
        heading.className = 'settings-page-heading';
        heading.innerHTML = '<h3></h3><p></p>';
        body.prepend(heading);
        const empty = document.createElement('div');
        empty.className = 'settings-empty';
        empty.textContent = text('没有找到相关设置，试试“模型”或“公网”。','No matching settings. Try “model” or “public”.');
        empty.hidden = true;
        body.append(empty);
        const search = nav.querySelector('input');
        let selected = 'models';
        const searchable = card => [card.textContent,...[...card.querySelectorAll('[data-key]')].map(el=>el.dataset.key)].join(' ').toLowerCase();
        function show(category, query = '') {
            selected = category;
            const section = sections.find(item=>item[0]===category) || sections[0];
            heading.querySelector('h3').textContent = query ? text('搜索结果','Search results') : text(section[1],section[2]);
            heading.querySelector('p').textContent = query ? text('可按名称或配置项查找；切换分类不会丢失未保存的内容。','Search by name or setting key. Section changes preserve unsaved edits.') : text(section[3],section[4]);
            let matches = 0;
            for (const card of cards) {
                const meta = sections.find(item=>item[0]===card.dataset.settingsCategory);
                card.hidden = query ? !(searchable(card).includes(query) || (meta && text(meta[1],meta[2]).toLowerCase().includes(query))) : card.dataset.settingsCategory !== category;
                if (!card.hidden) {
                    matches++;
                    if (query) {
                        card.classList.remove('collapsed');
                        card.querySelector(':scope > .settings-group-title')?.setAttribute('aria-expanded','true');
                        card.querySelectorAll('details').forEach(details=>{details.open=true;});
                    }
                }
            }
            empty.hidden = matches > 0;
            nav.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(!query && button.dataset.settingsTab===category)));
            body.scrollTop = 0;
        }
        nav.addEventListener('click',event=>{
            const button = event.target.closest('[data-settings-tab]');
            if (!button) return;
            search.value = '';
            show(button.dataset.settingsTab);
        });
        search.addEventListener('input',()=>show(selected,search.value.trim().toLowerCase()));
        body._revealSettings = target => {
            search.value = '';
            show(target.closest('[data-settings-category]')?.dataset.settingsCategory || 'models');
            target.classList.remove('collapsed');
            target.querySelector(':scope > .settings-group-title')?.setAttribute('aria-expanded','true');
        };
        body.querySelectorAll('.settings-label').forEach(label=>{
            const field = label.closest('.settings-field')?.querySelector('[data-key]');
            if (!field) return;
            if (!field.id) field.id = 'setting-' + field.dataset.key.toLowerCase();
            label.htmlFor = field.id;
            if (labels[field.dataset.key]) {
                label.title = field.dataset.key;
                label.textContent = text(...labels[field.dataset.key]);
            }
        });
        body.querySelectorAll('.settings-group-title').forEach(title=>{
            title.setAttribute('role','button');
            title.tabIndex = 0;
            title.setAttribute('aria-expanded',String(!title.parentElement.classList.contains('collapsed')));
            title.addEventListener('click',()=>title.setAttribute('aria-expanded',String(!title.parentElement.classList.contains('collapsed'))));
            title.addEventListener('keydown',event=>{
                if (event.key==='Enter' || event.key===' ') {event.preventDefault();title.click();}
            });
        });
        show(selected);
    }

    function publicAccessMarkup(language, component) {
        const text = (a,b) => language.startsWith('zh') ? a : b;
        return `<div class="settings-group settings-public-card" data-settings-category="public"><div class="settings-group-title" onclick="this.parentElement.classList.toggle('collapsed')">${text('远程访问本机','Access this device remotely')}<span class="settings-chevron">▼</span></div><div class="settings-group-body">
            <p class="settings-description">${text('开启后，用专属 HTTPS 链接访问完整页面。仍需登录，且本机需要保持运行。','Get an HTTPS link to the full app. Login is still required, and this device must stay online.')}</p>
            <div class="settings-field"><label class="settings-label" for="tunnel-provider">${text('连接服务','Connection service')}</label><select id="tunnel-provider" class="settings-input" onchange="document.getElementById('tunnel-cloudflare-download').hidden=this.value!=='cloudflare'"><option value="hub">ClawCross Hub</option><option value="cloudflare">Cloudflare</option></select><p class="settings-field-note">${text('Hub 无需安装额外程序；Cloudflare 作为可选连接方式。','Hub needs no extra software. Cloudflare is an optional alternative.')}</p></div>
            <div id="tunnel-cloudflare-download" hidden>${component}</div>
            <div class="settings-connection-state"><span id="tunnel-status-text" role="status">${text('正在获取状态…','Checking connection…')}</span><button id="tunnel-toggle-btn" class="settings-action-button" type="button" onclick="toggleTunnel()" disabled>${text('开启公网访问','Enable public access')}</button></div>
            <div id="tunnel-url-row" style="display:none"><label class="settings-label" for="tunnel-url-input">${text('访问链接','Access link')}</label><div class="settings-url-control"><input id="tunnel-url-input" class="settings-input" type="text" readonly onclick="this.select()"><button type="button" onclick="ClawcrossSettings.copyPublicUrl(this)">${text('复制','Copy')}</button></div></div>
            </div></div>`;
    }
    async function copyPublicUrl(button) {
        const input = document.getElementById('tunnel-url-input');
        if (!input?.value) return;
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
            else {input.select();if (!document.execCommand('copy')) return;}
            const original = button.textContent;
            button.textContent = original === 'Copy' ? 'Copied' : '已复制';
            setTimeout(()=>{button.textContent=original;},1800);
        } catch (_) {input.focus();input.select();}
    }
    return {mount,publicAccessMarkup,copyPublicUrl,categoryForGroup:id=>categories[id] || 'advanced',reveal:target=>target && document.getElementById('settings-body')._revealSettings?.(target)};
})();
