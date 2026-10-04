(function (global) {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const zh = () => !String(document.documentElement.lang || 'zh').startsWith('en');
    const text = (cn, en) => zh() ? cn : en;
    async function api(method, body, sessionId = '', general = false) {
        const response = await fetch((general ? '/proxy_configuration_setup' : '/proxy_channel_setup') + (method === 'GET' ? '?session_id=' + encodeURIComponent(sessionId) : ''), {
            method, headers: {'Content-Type':'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || text('设置服务不可用', 'Setup service unavailable'));
        return data;
    }
    function finish(node, state, message = '') {
        if (node.dataset.formStatus === state) return;
        node.dataset.formStatus = state;
        const label = state === 'completed' ? text('已保存', 'Saved') :
            state === 'cancelled' ? text('已取消', 'Cancelled') : text('已超时', 'Expired');
        node.querySelector('.cc-channel-heading span').textContent = label;
        // Remove all fields, including private inputs, when the request ends.
        const body = node.querySelector('form');
        body.replaceChildren();
        body.textContent = message || label;
    }
    function form(item) {
        const node = document.createElement('section');
        node.className = 'cc-channel-form'; node.dataset.channelRequest = item.id;
        const general = !!item.topic; const isChannel = !general || item.topic.startsWith('channel:');
        node.innerHTML = `<div class="cc-channel-heading"><strong>${esc(item.schema.label)} · ${isChannel ? text('连接设置', 'Connection setup') : text('设置', 'Settings')}</strong><span>${text('私密填写', 'Private input')}</span></div>
            <p>${text('密钥只发送到服务器，不进入对话或模型。留空保留已有设置。', 'Credentials go directly to the server, never to chat or the model. Leave blank to retain saved values.')}</p>
            <p class="cc-setup-help">${esc(item.schema.help || '')}</p><form>${item.schema.fields.map(field => {
                const value = item.draft?.[field.name] ?? field.current ?? field.default ?? '';
                const label = `${esc(field.label || field.name)}${field.required ? ' *' : ''}`;
                const help = `<small>${esc(field.help || '')}${field.configured && field.type === 'password' ? text(' · 已设置，留空保留', ' · Saved; leave blank to keep') : ''}</small>`;
                if (field.type === 'select') return `<label class="cc-channel-field"><span>${label}</span><select name="${esc(field.name)}">${field.options.map(option => `<option value="${esc(option)}" ${String(value) === option ? 'selected' : ''}>${esc(option || text('自动', 'Automatic'))}</option>`).join('')}</select>${help}</label>`;
                if (field.type === 'boolean') return `<label class="cc-channel-field cc-channel-check"><input name="${esc(field.name)}" type="checkbox" ${String(value) === 'true' ? 'checked' : ''}>${label}${help}</label>`;
                const secret = field.type === 'password';
                const attrs = `name="${esc(field.name)}" autocomplete="off" maxlength="8192" placeholder="${esc(field.placeholder || '')}"`;
                return `<label class="cc-channel-field"><span>${label}${secret ? ' 🔒' : ''}</span>${field.type === 'textarea' ? `<textarea ${attrs} rows="3">${esc(value)}</textarea>` : `<input ${attrs} type="${secret ? 'password' : field.type === 'number' ? 'number' : 'text'}" ${field.type === 'number' ? `min="${field.min}" max="${field.max}"` : ''} value="${secret ? '' : esc(value)}">`}${help}</label>`;
            }).join('')}<div class="cc-channel-actions"><button type="submit">${isChannel ? text('保存连接设置', 'Save connection') : text('保存设置', 'Save settings')}</button><button type="button" data-cancel>${text('取消', 'Cancel')}</button></div><div role="status" aria-live="polite"></div></form>`;
        const submit = async cancel => {
            const inputs = node.querySelectorAll('input,textarea,select,button');
            const values = {};
            node.querySelectorAll('input,textarea,select').forEach(input => {
                if (input.type === 'checkbox') values[input.name] = input.checked ? 'true' : 'false';
                else if (input.value || input.tagName === 'SELECT' || (general && !item.schema.fields.find(field => field.name === input.name)?.human_only)) values[input.name] = input.value;
            });
            inputs.forEach(input => {input.disabled = true;});
            try {
                const result = await api('POST', {request_id: item.id, values: cancel ? {} : values, cancel}, '', general);
                // Credentials never become chat text or a tool result.
                Object.keys(values).forEach(key => {delete values[key];});
                finish(node, result.status, result.message || '');
            } catch (error) {
                Object.keys(values).forEach(key => {delete values[key];});
                if (node.dataset.formStatus) return;
                node.querySelector('[role="status"]').textContent = error.message;
                inputs.forEach(input => {input.disabled = false;});
            }
        };
        node.querySelector('form').addEventListener('submit', event => {event.preventDefault(); void submit(false);});
        node.querySelector('[data-cancel]').addEventListener('click', () => void submit(true));
        return node;
    }
    async function sync(root, sessionIds) {
        if (!root || !sessionIds?.length) return;
        const data = await api('GET', null, '', true);
        for (const item of data.requests || []) {
            if (!sessionIds.includes(item.session_id)) continue;
            const existing = [...root.querySelectorAll('[data-channel-request]')].find(node => node.dataset.channelRequest === item.id);
            if (item.status && item.status !== 'pending') {
                if (existing) finish(existing, item.status);
                continue;
            }
            if (existing) continue;
            root.appendChild(form(item));
        }
    }
    function markup() {
        return `<section class="cc-channel-picker"><h3>${text('连接你的消息平台', 'Connect your messaging platform')}</h3>
            <p>${text('先连接机器人，再设置访问身份；通知收件地址可让 Agent 帮你设置。', 'Connect a bot, then set who may access it. Your Agent can help set notification recipients.')}</p>
            <select aria-label="${text('消息平台', 'Messaging platform')}"></select><p data-channel-help></p>
            <button type="button" data-channel-start>${text('填写连接设置', 'Set up connection')}</button><div data-channel-form-slot></div></section>`;
    }
    async function mount(root, {general = false, sessionId = 'settings'} = {}) {
        if (!root) return;
        root.innerHTML = markup();
        try {
            const data = await api('GET', null, '', general);
            const topics = general ? data.topics.filter(topic => topic.scope === 'host' || sessionId !== 'settings') : data.channels;
            if (general) {
                root.querySelector('h3').textContent = text('配置助手', 'Configuration assistant');
                root.querySelector('p').textContent = text('选择设置类别，查看每项用途并私密填写。主机设置供所有用户共用，Agent 设置仅作用于当前对话。', 'Choose a section to see field explanations and enter credentials privately. Host settings are shared; Agent settings apply to this conversation.');
                root.querySelector('[data-channel-start]').textContent = text('填写设置', 'Open form');
            }
            const select = root.querySelector('select');
            select.innerHTML = topics.map(ch => `<option value="${esc(ch.id)}">${esc(ch.label)}</option>`).join('');
            const update = () => {root.querySelector('[data-channel-help]').textContent = topics.find(ch => ch.id === select.value)?.help || '';};
            select.addEventListener('change', update); update();
            root.querySelector('[data-channel-start]').addEventListener('click', async event => {
                event.currentTarget.disabled = true;
                try {
                    const result = await api('POST', {[general ? 'topic' : 'channel']: select.value, session_id: sessionId}, '', general);
                    const pending = await api('GET', null, sessionId, general);
                    const item = pending.requests.find(req => req.id === result.id);
                    root.querySelector('[data-channel-form-slot]').replaceChildren(form(item));
                } catch (error) {root.querySelector('[data-channel-help]').textContent = error.message;}
                finally {root.querySelector('[data-channel-start]').disabled = false;}
            });
        } catch (error) {root.textContent = error.message;}
    }
    global.ClawcrossChannelSetup = {sync, mount};
})(window);
