(function (global) {
    const replyPattern = /^\s*(KEEP\s+Y|Y|N)(?:\s+(approval-[a-zA-Z0-9]+))?\s*$/i;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const zh = () => !String(document.documentElement.lang || localStorage.getItem('lang') || 'zh').startsWith('en');
    const humanPending = item => item.status === 'pending' && (item.review?.reviewer === 'user' || (item.review?.conversation_reply && item.review?.reviewer !== 'auto_review'));
    const resolutions = new Map();
    const inFlight = new Set();
    const views = new Map();
    const statusLabel = action => action === 'deny' ? (zh() ? '已拒绝' : 'Denied') : (zh() ? '已批准' : 'Approved');

    async function resolve(approvalId, action, remember, sessionId) {
        if (inFlight.has(approvalId)) throw new Error(zh() ? '此操作正在确认，请稍候。' : 'This approval is being processed.');
        inFlight.add(approvalId);
        try {
        const response = await fetch('/proxy_webot_tool_approval_resolve', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({approval_id: approvalId, action, remember: !!remember, session_id: sessionId || ''}),
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') throw new Error(data.detail || data.error || (zh() ? '审核处理失败' : 'Approval failed'));
        resolutions.set(approvalId, action);
        document.querySelectorAll('[data-approval-bubble]').forEach(node => {
            if (node.dataset.approvalBubble !== approvalId) return;
            const status = node.querySelector('.cc-approval-status');
            if (status) status.textContent = statusLabel(action);
            node.classList.add('resolved');
            node.querySelector('.cc-approval-actions')?.remove();
        });
        const view = views.get(sessionId)?.options;
        if (view) {
            view.onReply?.({approval_id: approvalId, session_id: sessionId, action, remember: !!remember,
                text: (remember ? 'KEEP Y' : action === 'deny' ? 'N' : 'Y') + ' ' + approvalId});
            view.onResolved?.(sessionId, data);
        }
        return data;
        } finally { inFlight.delete(approvalId); }
    }

    // Exact Y/N replies are button actions. Natural language remains ordinary chat.
    async function reply(text, sessionIds) {
        const match = text.match(replyPattern);
        if (!match) return null;
        const response = await fetch('/proxy_webot_tool_approvals?status=pending&limit=100');
        if (!response.ok) throw new Error(zh() ? '无法读取待审核操作，请使用审核按钮。' : 'Cannot load approvals. Please use an approval button.');
        const data = await response.json();
        const scoped = (data.approvals || []).filter(item => humanPending(item) && sessionIds.includes(String(item.session_id || '')));
        const candidates = scoped.filter(item => !match[2] || item.approval_id === match[2]);
        if (!candidates.length && !match[2] && !scoped.length) return null;
        if (candidates.length !== 1) throw new Error(zh() ? '请用审核按钮，或在 Y/N 后填写当前操作的审批编号。' : 'Use an approval button or include the current approval ID after Y/N.');
        const item = candidates[0];
        return {...await resolve(item.approval_id, match[1].toUpperCase() === 'N' ? 'deny' : 'approve', /^KEEP/i.test(match[1]), item.session_id), session_id: item.session_id};
    }

    function renderPrompt(raw) {
        if (!raw.includes('【操作授权请求】\n')) return null;
        const parts = raw.split(/(【操作授权请求】\n[^\n]+\n[^]*?)(?=\n\n【操作授权请求】\n|$)/);
        let found = false;
        const html = parts.map(part => {
            if (!part.startsWith('【操作授权请求】\n')) return escape(part).replace(/\n/g, '<br>');
            let info;
            try { info = JSON.parse(part.split('\n')[1]); } catch (_) { return escape(part).replace(/\n/g, '<br>'); }
            if (!/^approval-[a-zA-Z0-9]+$/.test(info.id || '')) return escape(part);
            found = true;
            const decision = resolutions.get(info.id);
            return `<section class="cc-approval-bubble${decision ? ' resolved' : ''}" data-approval-bubble="${escape(info.id)}">
                <div class="cc-approval-heading"><strong>${zh() ? '操作需要确认' : 'Approval needed'}</strong><span class="cc-approval-status">${decision ? statusLabel(decision) : (zh() ? '等待确认' : 'Awaiting approval')}</span></div>
                <div class="cc-approval-tool">${escape(info.tool)}</div><p>${escape(info.reason)}</p>
                <details><summary>${zh() ? '查看具体操作' : 'View exact action'}</summary><pre>${escape(JSON.stringify(info.args || {}, null, 2))}</pre></details>
                <p class="cc-approval-hint">${zh() ? '点击确认，或在输入框回复' : 'Confirm here, or reply'} <kbd>Y</kbd> / <kbd>N</kbd> / <kbd>KEEP Y</kbd></p>
                <small>${escape(info.id)}</small>
            </section>`;
        }).join('');
        return found ? html : null;
    }

    function sync(root, approvals, options) {
        if (!root) return;
        const pending = approvals.filter(humanPending);
        for (const [sid, view] of views) if (view.root === root) views.delete(sid);
        pending.forEach(item => views.set(item.session_id, {root, options}));
        const active = new Set(pending.map(item => item.approval_id));
        root.querySelectorAll('.cc-approval-actions').forEach(node => {
            if (!active.has(node.closest('[data-approval-bubble]')?.dataset.approvalBubble)) node.remove();
        });
        pending.forEach(item => {
            if (inFlight.has(item.approval_id) || resolutions.has(item.approval_id)) return;
            // Only API records can produce interactive controls. Text written
            // by an agent or tool never supplies approval arguments/identity.
            const matches = [...root.querySelectorAll('[data-approval-bubble]')]
                .filter(node => node.dataset.approvalBubble === item.approval_id && !node.closest('details'));
            let bubble = matches.at(-1);
            if (!bubble) {
                const raw = '【操作授权请求】\n' + JSON.stringify({id: item.approval_id, tool: item.tool_name,
                    args: item.args || {}, reason: item.request_reason || ''}) + '\n请确认此操作。';
                options.appendRequest(raw);
                bubble = [...root.querySelectorAll('[data-approval-bubble]')].find(node => node.dataset.approvalBubble === item.approval_id && !node.closest('details'));
            }
            if (!bubble) return;
            const tool = bubble.querySelector('.cc-approval-tool');
            if (tool) tool.textContent = item.tool_name;
            const reason = bubble.querySelector('p');
            if (reason) reason.textContent = item.request_reason || '';
            const args = bubble.querySelector('pre');
            if (args) args.textContent = JSON.stringify(item.args || {}, null, 2);
            if (bubble.querySelector('.cc-approval-actions')) return;
            const actions = document.createElement('div');
            actions.className = 'cc-approval-actions';
            for (const [action, remember, label] of [
                ['approve', false, zh() ? '同意' : 'Allow'],
                ['approve', true, zh() ? '同意并记住' : 'Allow and remember'],
                ['deny', false, zh() ? '拒绝' : 'Deny'],
            ]) {
                const button = document.createElement('button');
                button.type = 'button'; button.textContent = label;
                button.className = 'cc-approval-button ' + action;
                button.addEventListener('click', async () => {
                    actions.querySelectorAll('button').forEach(node => {node.disabled = true;});
                    try { await resolve(item.approval_id, action, remember, item.session_id); }
                    catch (error) { actions.querySelectorAll('button').forEach(node => {node.disabled = false;}); options.onError?.(error); }
                });
                actions.appendChild(button);
            }
            bubble.querySelector('.cc-approval-hint').before(actions);
        });
    }
    global.ClawcrossApproval = {resolve, reply, renderPrompt, humanPending, sync};
})(window);
