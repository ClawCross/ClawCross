(function (global) {
    const replyPattern = /^\s*(KEEP\s+Y|Y|N)(?:\s+(approval-[a-zA-Z0-9]+))?\s*$/i;
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const zh = () => !String(document.documentElement.lang || localStorage.getItem('lang') || 'zh').startsWith('en');
    const humanPending = item => item.status === 'pending' && (item.review?.reviewer === 'user' || (item.review?.conversation_reply && item.review?.reviewer !== 'auto_review'));
    const resolutions = new Map();
    const statusLabel = action => action === 'deny' ? (zh() ? '已拒绝' : 'Denied') : (zh() ? '已批准' : 'Approved');

    async function resolve(approvalId, action, remember, sessionId) {
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
        });
        return data;
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
                <div class="cc-approval-heading"><strong>${zh() ? '操作需要确认' : 'Approval needed'}</strong><span class="cc-approval-status">${decision ? statusLabel(decision) : (zh() ? '使用审核按钮或回复选项' : 'Use an approval button or reply')}</span></div>
                <div class="cc-approval-tool">${escape(info.tool)}</div><p>${escape(info.reason)}</p>
                <details><summary>${zh() ? '查看具体操作' : 'View exact action'}</summary><pre>${escape(JSON.stringify(info.args || {}, null, 2))}</pre></details>
                <p class="cc-approval-hint">${zh() ? '使用上方按钮，或回复' : 'Use the buttons above, or reply'} <kbd>Y</kbd> / <kbd>N</kbd> / <kbd>KEEP Y</kbd></p>
                <small>${escape(info.id)}</small>
            </section>`;
        }).join('');
        return found ? html : null;
    }
    global.ClawcrossApproval = {resolve, reply, renderPrompt, humanPending};
})(window);
