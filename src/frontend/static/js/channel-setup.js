(function (global) {
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const zh = () => !String(document.documentElement.lang || 'zh').startsWith('en');
    const text = (cn, en) => zh() ? cn : en;
    async function api(method, body, sessionId = '') {
        const response = await fetch('/proxy_channel_setup' + (method === 'GET' ? '?session_id=' + encodeURIComponent(sessionId) : ''), {
            method, headers: {'Content-Type':'application/json'}, ...(body ? {body: JSON.stringify(body)} : {})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || text('设置服务不可用', 'Setup service unavailable'));
        return data;
    }
    function form(item) {
        const node = document.createElement('section');
        node.className = 'cc-channel-form'; node.dataset.channelRequest = item.id;
        node.innerHTML = `<div class="cc-channel-heading"><strong>${esc(item.schema.label)} · ${text('连接设置', 'Connection setup')}</strong><span>${text('私密填写', 'Private input')}</span></div>
            <p>${text('密钥只发送到服务器，不进入对话或模型。留空保留已有设置。', 'Credentials go directly to the server, never to chat or the model. Leave blank to retain saved values.')}</p>
            <form>${item.schema.fields.map(field => {
                const value = item.draft?.[field.name] ?? field.default ?? '';
                const label = `${esc(field.label || field.name)}${field.required ? ' *' : ''}`;
                const help = field.help ? `<small>${esc(field.help)}</small>` : '';
                if (field.type === 'boolean') return `<label class="cc-channel-field cc-channel-check"><input name="${esc(field.name)}" type="checkbox" ${String(value) === 'true' ? 'checked' : ''}>${label}${help}</label>`;
                const secret = field.type === 'password';
                const attrs = `name="${esc(field.name)}" autocomplete="off" maxlength="8192" placeholder="${esc(field.placeholder || '')}"`;
                return `<label class="cc-channel-field"><span>${label}${secret ? ' 🔒' : ''}</span>${field.type === 'textarea' ? `<textarea ${attrs} rows="3">${esc(value)}</textarea>` : `<input ${attrs} type="${secret ? 'password' : 'text'}" value="${secret ? '' : esc(value)}">`}${help}</label>`;
            }).join('')}<div class="cc-channel-actions"><button type="submit">${text('保存连接设置', 'Save connection')}</button><button type="button" data-cancel>${text('取消', 'Cancel')}</button></div><div role="status" aria-live="polite"></div></form>`;
        const submit = async cancel => {
            const inputs = node.querySelectorAll('input,textarea,button');
            const values = {};
            node.querySelectorAll('input,textarea').forEach(input => {
                if (input.type === 'checkbox') values[input.name] = input.checked ? 'true' : 'false';
                else if (input.value) values[input.name] = input.value;
            });
            inputs.forEach(input => {input.disabled = true;});
            try {
                const result = await api('POST', {request_id: item.id, values: cancel ? {} : values, cancel});
                // Remove values from DOM and local variables before notifying chat.
                Object.keys(values).forEach(key => {delete values[key];});
                node.querySelector('form').replaceChildren();
                node.querySelector('form').textContent = result.status === 'completed' ? text('已保存，渠道将在后台重新连接。', 'Saved. The channel will reconnect in the background.') : text('已取消', 'Cancelled');
            } catch (error) {
                Object.keys(values).forEach(key => {delete values[key];});
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
        const data = await api('GET');
        for (const item of data.requests || []) {
            if (!sessionIds.includes(item.session_id) || [...root.querySelectorAll('[data-channel-request]')].some(node => node.dataset.channelRequest === item.id)) continue;
            root.appendChild(form(item));
        }
    }
    function markup() {
        return `<section class="cc-channel-picker"><h3>${text('连接你的消息平台', 'Connect your messaging platform')}</h3>
            <p>${text('先连接机器人，再设置访问身份；通知收件地址可让 Agent 帮你设置。', 'Connect a bot, then set who may access it. Your Agent can help set notification recipients.')}</p>
            <select aria-label="${text('消息平台', 'Messaging platform')}"></select><p data-channel-help></p>
            <button type="button" data-channel-start>${text('填写连接设置', 'Set up connection')}</button><div data-channel-form-slot></div></section>`;
    }
    async function mount(root) {
        if (!root) return;
        root.innerHTML = markup();
        try {
            const data = await api('GET');
            const select = root.querySelector('select');
            select.innerHTML = data.channels.map(ch => `<option value="${esc(ch.id)}">${esc(ch.label)}</option>`).join('');
            const update = () => {root.querySelector('[data-channel-help]').textContent = data.channels.find(ch => ch.id === select.value)?.help || '';};
            select.addEventListener('change', update); update();
            root.querySelector('[data-channel-start]').addEventListener('click', async event => {
                event.currentTarget.disabled = true;
                try {
                    const result = await api('POST', {channel: select.value, session_id: 'settings'});
                    const pending = await api('GET', null, 'settings');
                    const item = pending.requests.find(req => req.id === result.id);
                    root.querySelector('[data-channel-form-slot]').replaceChildren(form(item));
                } catch (error) {root.querySelector('[data-channel-help]').textContent = error.message;}
                finally {root.querySelector('[data-channel-start]').disabled = false;}
            });
        } catch (error) {root.textContent = error.message;}
    }
    global.ClawcrossChannelSetup = {sync, mount};
})(window);
