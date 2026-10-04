// User/session context and approval settings. Values are saved as overrides.
let runtimeSettingsView = null;

function runtimeSettingsText(zh, en) {
    return !document.documentElement.lang.startsWith('en') ? zh : en;
}

function runtimeSettingsCurrentAgent() {
    if (typeof getMobileRuntimeAgentId === 'function') return getMobileRuntimeAgentId();
    return window.ExternalAgentSettings?.currentTarget() || (typeof currentSessionId !== 'undefined' ? currentSessionId : '') || '';
}

async function openRuntimeSettings(sessionId = '', tab = 'context') {
    const targetSession = sessionId || runtimeSettingsCurrentAgent();
    if (!targetSession) {
        if (typeof toast === 'function') toast(runtimeSettingsText('请先选择一个 Agent。', 'Choose an Agent first.'));
        return;
    }
    let external = false;
    if (targetSession && window.ExternalAgentSettings) {
        try {
            const card = await ExternalAgentSettings.capabilities(targetSession);
            external = card.transport === 'acpx';
        } catch (_) { /* An unsaved WeBot session still uses its normal settings. */ }
    }
    let overlay = document.getElementById('runtime-settings-modal');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'runtime-settings-modal';
        overlay.className = 'settings-modal-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.onclick = event => { if (event.target === overlay) closeRuntimeSettings(); };
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'flex';
    overlay.setAttribute('aria-labelledby', 'runtime-settings-title');
    overlay.innerHTML = `<div class="settings-modal runtime-settings-dialog">
        <div class="runtime-settings-header">
            <div><h2 id="runtime-settings-title">${runtimeSettingsText('上下文与审核', 'Context and approvals')}</h2>
            <p>${runtimeSettingsText('仅修改这个 Agent，其他 Agent 不受影响。', 'Applies only to this Agent.')}</p></div>
            <button type="button" class="runtime-settings-close" onclick="closeRuntimeSettings()" aria-label="${runtimeSettingsText('关闭', 'Close')}">×</button>
        </div>
        <div class="runtime-settings-scope-row">
            <label for="runtime-settings-scope">${runtimeSettingsText('应用到', 'Apply to')}</label>
            <select id="runtime-settings-scope" class="runtime-settings-input" onchange="loadRuntimeSettingsScope()">
                <option value="session" selected>${runtimeSettingsText('当前 Agent', 'This Agent')}</option>
            </select>
            <span>${external ? runtimeSettingsText('审核和沙盒仅约束 ClawCross 工具；原生工具由外部程序管理。', 'Approvals and sandbox govern ClawCross tools; native tools follow their own permissions.') : runtimeSettingsText('上下文、模式与沙盒均独立保存', 'Context, mode and sandbox are saved separately for each Agent')}</span>
        </div>
        <div class="runtime-settings-tabs" role="tablist" aria-label="${runtimeSettingsText('设置分类', 'Settings categories')}">
            <button id="runtime-settings-context-tab" type="button" role="tab" aria-selected="true" aria-controls="runtime-settings-context" ${external ? 'disabled' : ''} onclick="showRuntimeSettingsTab('context')">${runtimeSettingsText('上下文压缩', 'Context')}</button>
            <button id="runtime-settings-approval-tab" type="button" role="tab" aria-selected="false" aria-controls="runtime-settings-approval" tabindex="-1" onclick="showRuntimeSettingsTab('approval')">${runtimeSettingsText('工具审核', 'Approvals')}</button>
        </div>
        <div id="runtime-settings-fields" class="runtime-settings-body"></div>
        <div class="runtime-settings-footer">
            <div id="runtime-settings-result" role="status" aria-live="polite"></div>
            <div class="runtime-settings-footer-actions">
                <button id="runtime-settings-reset" type="button" class="runtime-settings-btn runtime-settings-btn-secondary" onclick="saveRuntimeSettingsForm(true)">${runtimeSettingsText('恢复继承设置', 'Reset overrides')}</button>
                <button id="runtime-settings-save" type="button" class="runtime-settings-btn runtime-settings-btn-primary" onclick="saveRuntimeSettingsForm()">${runtimeSettingsText('保存设置', 'Save settings')}</button>
            </div>
        </div></div>`;
    runtimeSettingsView = { targetSession, original: null, activeTab: external ? 'approval' : tab, external, returnFocus: document.activeElement };
    overlay.querySelector('.runtime-settings-close').focus();
    await loadRuntimeSettingsScope();
}

function openAgentRuntimeSettings(tab = 'context') {
    const agent = agentCenterSelectedAgent();
    if (agent) openRuntimeSettings(agent.agent_id || agent.session_id || agent.identity || '', tab);
}

async function loadRuntimeSettingsScope() {
    const view = runtimeSettingsView;
    const scope = document.getElementById('runtime-settings-scope').value;
    const sessionId = view.targetSession;
    const status = document.getElementById('runtime-settings-result');
    view.original = null;
    document.getElementById('runtime-settings-save').disabled = true;
    status.textContent = runtimeSettingsText('加载中…', 'Loading…');
    try {
        const response = await fetch('/proxy_webot_runtime_settings?session_id=' + encodeURIComponent(sessionId));
        const payload = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(payload.detail || payload.error));
        if (runtimeSettingsView !== view || document.getElementById('runtime-settings-scope').value !== scope) return;
        view.original = payload.settings;
        view.scope = scope;
        const context = payload.settings.context;
        const approval = payload.settings.approval;
        const inference = payload.settings.inference || {reasoning_effort:''};
        const capabilities = payload.model_capabilities || {};
        const levels = capabilities.reasoning_effort_levels || [];
        const levelMap = capabilities.reasoning_level_map || {};
        const unified = window.ReasoningLevels && Object.keys(levelMap).length;
        const selectedLevel = unified ? ReasoningLevels.selected(inference.reasoning_level, inference.reasoning_effort) : 0;
        const networkClosed = Array.isArray(payload.sandbox_network_maximum) && !payload.sandbox_network_maximum.length;
        if (scope === 'session' && ['chat', 'readonly', 'manual', 'auto', 'bypass'].includes(payload.effective_mode)) approval.mode = payload.effective_mode;
        const escape = value => escapeHtml(String(value)).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        const text = runtimeSettingsText;
        const number = (key, zh, en, min, max, hint = '') => `<label class="runtime-settings-field"><span>${text(zh, en)}</span>
            <input data-section="context" data-key="${key}" type="number" min="${min}" max="${max}" value="${context[key]}" class="runtime-settings-input">
            ${hint ? `<small>${hint}</small>` : ''}</label>`;
        const model = (section, key, value) => `<label class="runtime-settings-field"><span>${text('使用模型', 'Model')}</span>
            <input data-section="${section}" data-key="${key}" maxlength="200" value="${escape(value)}" placeholder="${text('跟随默认模型', 'Use default model')}" class="runtime-settings-input"></label>`;
        const instructions = (section, key, value, zh, en, placeholderZh, placeholderEn) => `<label class="runtime-settings-field"><span>${text(zh, en)}</span>
            <textarea data-section="${section}" data-key="${key}" maxlength="4000" rows="3" placeholder="${text(placeholderZh, placeholderEn)}" class="runtime-settings-input runtime-settings-textarea">${escape(value)}</textarea></label>`;
        const autoHint = text('0 表示自动，根据上面设置的窗口调整', '0 = automatic, based on the configured window');
        document.getElementById('runtime-settings-fields').innerHTML = `
            <section id="runtime-settings-context" role="tabpanel" aria-labelledby="runtime-settings-context-tab">
                ${unified ? `<label class="runtime-settings-field"><span>${text('思考强度 · 7 级', 'Reasoning effort · 7 levels')}</span>${ReasoningLevels.slider({mapping:levelMap,level:selectedLevel,autoLabel:text('自动 · 模型预设','Automatic · Model preset') + (capabilities.reasoning_effort_default ? ' · ' + capabilities.reasoning_effort_default : ''),attributes:'data-section="inference" data-key="reasoning_level" data-value-type="number"'})}<small>${escape(capabilities.model || '')} · ${text('箭头后是实际原生档位；部分级别会重复。','The arrow shows the native setting; some levels repeat.')}</small></label>` : levels.length ? `<label class="runtime-settings-field"><span>${text('思考强度', 'Reasoning effort')}</span>${ReasoningLevels.slider({choices:[{value:'',label:text('使用模型预设', 'Use model preset') + (capabilities.reasoning_effort_default ? ' · ' + capabilities.reasoning_effort_default : '')},...levels.map(value=>({value,label:value}))],value:inference.reasoning_effort,attributes:'data-section="inference" data-key="reasoning_effort"'})}<small>${escape(capabilities.model || '')}</small></label>` : ''}
                <div id="runtime-settings-usage">${renderRuntimeContextUsage(payload.context_usage || (typeof sessionContextUsageState !== 'undefined' ? sessionContextUsageState : {}), context.context_window_tokens)}</div>
                <div class="runtime-settings-toggle-row">
                    <div><h3>${text('自动压缩', 'Automatic compaction')}</h3><p>${text('上下文变长时，整理早期对话并保留近期原文。', 'Summarize older conversations while keeping recent turns intact.')}</p></div>
                    <label class="runtime-settings-switch"><input type="checkbox" data-section="context" data-key="auto_compact" aria-label="${text('自动压缩上下文', 'Compact automatically')}" ${context.auto_compact ? 'checked' : ''}><span aria-hidden="true"></span></label>
                </div>
                <label class="runtime-settings-field runtime-settings-capacity"><span>${text('上下文窗口（tokens）', 'Context window (tokens)')}</span><input type="number" data-section="context" data-key="context_window_tokens" min="0" max="4000000" value="${context.context_window_tokens}" class="runtime-settings-input"><small>${text('0 表示跟随本地模型目录；手动值优先，请填写服务商支持的容量', '0 follows the local model catalog. An explicit capacity overrides it.')}</small></label>
                <div class="runtime-settings-grid">
                    ${number('trigger_tokens', '开始压缩时的 token 数', 'Trigger at (tokens)', 0, 4000000, autoHint)}
                    ${number('target_tokens', '压缩后的目标 token 数', 'Compact to (tokens)', 0, 4000000, autoHint)}
                    ${number('preserve_recent_turns', '保留最近几轮原文', 'Recent turns to retain', 1, 100, text('这些对话不参与摘要', 'Keep these turns outside the summary'))}
                    ${number('history_tokens', '历史上下文预算', 'History budget (tokens)', 0, 4000000, autoHint)}
                </div>
                ${instructions('context', 'preserve_instructions', context.preserve_instructions, '希望保留什么', 'What should be retained?', '例如：任务目标、已确认的决定、未完成的工作', 'For example: goals, decisions, and unfinished work')}
                <details class="runtime-settings-advanced"><summary>${text('高级压缩设置', 'Advanced compaction settings')}<span>${text('模型与摘要预算', 'Model and summary budgets')}</span></summary>
                    <div class="runtime-settings-advanced-body">
                        ${model('context', 'summarizer_model', context.summarizer_model)}
                        <div class="runtime-settings-grid">
                            ${number('summary_tokens', '摘要 token 上限', 'Summary token limit', 128, 32000)}
                            ${number('summarizer_input_tokens', '摘要模型输入预算', 'Summarizer input budget (tokens)', 1024, 128000)}
                        </div>
                    </div>
                </details>
            </section>
            <section id="runtime-settings-approval" role="tabpanel" aria-labelledby="runtime-settings-approval-tab" hidden>
                <div class="runtime-settings-section-intro"><h3>${text('工具使用模式', 'Tool use mode')}</h3><p>${text('选择 Agent 可以做什么，以及如何批准操作。', 'Choose what the agent can do and how actions are approved.')}</p></div>
                <label class="runtime-settings-field"><span>${text('运行模式', 'Run mode')}</span>
                    <select data-section="approval" data-key="mode" class="runtime-settings-input" onchange="updateRuntimeReviewerHint()">
                        <option value="chat" ${approval.mode === 'chat' ? 'selected' : ''}>${text('交流模式 · 无工具', 'Chat · No tools')}</option>
                        <option value="readonly" ${approval.mode === 'readonly' ? 'selected' : ''}>${text('只读模式', 'Read-only')}</option>
                        <option value="manual" ${approval.mode === 'manual' ? 'selected' : ''}>${text('Manual · 人工审核', 'Manual · Human review')}</option>
                        <option value="auto" ${(approval.mode || 'auto') === 'auto' ? 'selected' : ''}>${text('Auto · 替我审核', 'Auto · Review for me')}</option>
                        <option value="bypass" ${approval.mode === 'bypass' ? 'selected' : ''}>${text('Bypass · 无审核', 'Bypass · No review')}</option>
                    </select>
                </label>
                <p id="runtime-settings-reviewer-hint" class="runtime-settings-note"></p>
                <label class="runtime-settings-field"><span>${text('命令沙盒', 'Command sandbox')}</span>
                    <select data-section="approval" data-key="command_sandbox" class="runtime-settings-input">
                        <option value="off" ${!approval.command_sandbox || approval.command_sandbox === 'off' ? 'selected' : ''}>${text('关闭 · 命令在宿主机执行', 'Off · Commands run on host')}</option>
                        <option value="auto" ${approval.command_sandbox === 'auto' ? 'selected' : ''}>${text('自动 · SRT / Linux Landlock', 'Auto · SRT / Linux Landlock')}</option>
                        <option value="landlock" ${approval.command_sandbox === 'landlock' ? 'selected' : ''}>${text('Linux Landlock · 文件与网络', 'Linux Landlock · Files and network')}</option>
                        <option value="srt" ${approval.command_sandbox === 'srt' ? 'selected' : ''}>${text('SRT · 前台、后台、交互命令', 'SRT · Foreground, background, interactive')}</option>
                    </select><small>${text('SRT 需显式安装；Linux Landlock 使用内核和 libseccomp，无需新容器。自动模式先探测 SRT，Linux 不兼容时使用 Landlock。Landlock 在支持的 systemd 主机上提供受控联网，其他环境保持禁网；保留基础资源上限。不满足要求时拒绝执行。', 'SRT requires explicit installation. Linux Landlock uses the kernel and libseccomp without a new container. Auto probes SRT first, then Landlock on Linux. Landlock provides controlled networking on supported systemd hosts; other environments remain offline. Basic resource limits apply. Missing capabilities block execution.')}</small></label>
                <label class="runtime-settings-field"><span>${text('安全等级', 'Security level')}</span>
                    <select data-section="approval" data-key="sandbox_security" class="runtime-settings-input">
                        <option value="standard" ${approval.sandbox_security !== 'strict' ? 'selected' : ''}>${text('普通 · 审核后有限提权', 'Standard · Reviewed permission expansion')}</option>
                        <option value="strict" ${approval.sandbox_security === 'strict' ? 'selected' : ''}>${text('严格 · 禁止提权', 'Strict · No permission expansion')}</option>
                    </select><small>${text('两种等级都使用配置目录之外的工作区，支持基础资源查询和后台任务管理。普通模式可在管理员上限内审核扩大文件、网络权限；严格模式自动启用沙盒，每个 Agent 使用独立目录，不使用历史提权，也不允许审核扩大范围。预设网站许可仍有效。安全配置与任务控制文件保存在工作区外。', 'Both levels use workspaces outside configuration directories and support resource queries and background jobs. Standard allows reviewed file and network grants within administrator limits. Strict enables isolation, uses a separate directory per Agent, and ignores prior escalation grants without allowing expansion. Explicit network destinations still apply. Security configuration and job controls stay outside workspaces.')}</small></label>
                <label class="runtime-settings-field"><span>${text('允许直接访问的网站', 'Allowed network destinations')}</span>
                    <textarea data-section="approval" data-key="sandbox_allowed_domains" data-value-type="lines" class="runtime-settings-input" rows="2" placeholder="example.com:443">${escapeHtml((approval.sandbox_allowed_domains || []).join('\n'))}</textarea>
                    <small>${text('每行一个域名或公网 IP，可加端口；不接受 URL 或通配符。留空时不直接放行任何网站，新目标按当前模式审核。脚本须使用沙盒提供的 HTTP/SOCKS 代理；直接连接仍被阻止。', 'One domain or public IP per line, optionally with a port; no URLs or wildcards. An empty list grants no direct access; new destinations are reviewed under the current mode. Scripts must use the sandbox HTTP/SOCKS proxies; direct connections remain blocked.')}</small>
                    ${networkClosed ? `<small class="runtime-settings-error">${text('管理员显式关闭了网络提权，未列出的目标不能送审。', 'The administrator explicitly disabled network escalation; unlisted destinations cannot be reviewed.')}</small>` : ''}</label>
                <input type="hidden" data-section="approval" data-key="sandbox_grants" data-value-type="json" value="${escape(JSON.stringify(approval.sandbox_grants || []))}">
                <details class="runtime-settings-advanced"><summary>${text('已记住的沙盒权限', 'Remembered sandbox permissions')}<span>${(approval.sandbox_grants || []).length}</span></summary>
                    <div class="runtime-settings-advanced-body">
                        <p class="runtime-settings-note">${text('KEEP Y 保存具体目标和访问类型，下次命令自动使用；仍受管理员权限上限约束。移除后点击保存设置。', 'KEEP Y saves the specific target and access type for later commands, subject to administrator limits. Save settings after removing an entry.')}</p>
                        ${(approval.sandbox_grants || []).map(grant => `<div class="runtime-settings-field" data-sandbox-grant>
                            <span style="overflow-wrap:anywhere">${text({network:'联网',read_path:'只读',write_path:'读写'}[grant.access], {network:'Network',read_path:'Read',write_path:'Read/write'}[grant.access])} · ${escapeHtml(grant.target)}</span>
                            <button type="button" class="btn btn-secondary" data-access="${escape(grant.access)}" data-target="${escape(grant.target)}" onclick="removeRememberedSandboxGrant(this)">${text('移除', 'Remove')}</button>
                        </div>`).join('')}
                    </div>
                </details>
                ${typeof componentControlMarkup === 'function' ? componentControlMarkup('srt') : ''}
                ${instructions('approval', 'reviewer_policy', approval.reviewer_policy, '补充审核要求', 'Additional review instructions', '例如：允许安装任务所需依赖；删除文件没有明确授权时拒绝', 'For example: allow task dependencies; deny deletion without explicit authorization')}
                <details class="runtime-settings-advanced"><summary>${text('高级审核设置', 'Advanced review settings')}<span>${text('模型与等待时间', 'Model and timeout')}</span></summary>
                    <div class="runtime-settings-advanced-body runtime-settings-grid">
                        ${model('approval', 'reviewer_model', approval.reviewer_model)}
                        <label class="runtime-settings-field"><span>${text('审核等待上限（秒）', 'Review timeout (seconds)')}</span><input data-section="approval" data-key="reviewer_timeout_seconds" type="number" min="5" max="120" value="${approval.reviewer_timeout_seconds}" class="runtime-settings-input"></label>
                        <label class="runtime-settings-field"><span>${text('审核输出上限（含思考 tokens）', 'Review output limit (including reasoning tokens)')}</span><input data-section="approval" data-key="reviewer_max_tokens" type="number" min="1024" max="16384" value="${approval.reviewer_max_tokens || 16384}" class="runtime-settings-input"></label>
                    </div>
                </details>
            </section>`;
        showRuntimeSettingsTab(view.activeTab);
        updateRuntimeReviewerHint();
        if (typeof initComponentControls === 'function') initComponentControls(document.getElementById('runtime-settings-fields'));
        const compact = payload.last_compaction;
        status.textContent = compact && Number.isFinite(compact.before_tokens) && Number.isFinite(compact.after_tokens) ? runtimeSettingsText(
            `上次压缩：约 ${compact.before_tokens} → ${compact.after_tokens} tokens，${compact.duration_ms} ms${compact.target_met === false ? '；近期保留内容超过目标' : ''}`,
            `Last compaction: ~${compact.before_tokens} → ${compact.after_tokens} tokens, ${compact.duration_ms} ms${compact.target_met === false ? '; retained turns exceed target' : ''}`,
        ) : '';
        document.getElementById('runtime-settings-save').disabled = false;
    } catch (error) { status.textContent = String(error.message || error); }
}

async function saveRuntimeSettingsForm(reset = false) {
    const view = runtimeSettingsView;
    if (!view || !view.original) return;
    const status = document.getElementById('runtime-settings-result');
    const settings = {};
    for (const input of document.querySelectorAll('#runtime-settings-fields [data-key]')) {
        if (!reset && !input.checkValidity()) {
            showRuntimeSettingsTab(input.dataset.section === 'inference' ? 'context' : input.dataset.section);
            const advanced = input.closest('details');
            if (advanced) advanced.open = true;
            input.reportValidity();
            return;
        }
        const rawValue = input.hasAttribute('data-reasoning-slider') ? ReasoningLevels.value(input) : input.value;
        const value = input.dataset.valueType === 'json' ? JSON.parse(input.value) : input.dataset.valueType === 'lines' ? input.value.split(/\n/).map(v => v.trim()).filter(Boolean) : input.type === 'checkbox' ? input.checked : input.type === 'number' || input.dataset.valueType === 'number' ? Number(rawValue) : rawValue;
        const {section, key} = input.dataset;
        if (JSON.stringify(value) !== JSON.stringify(view.original[section][key])) (settings[section] ||= {})[key] = value;
    }
    if (settings.inference?.reasoning_level !== undefined) settings.inference.reasoning_effort = '';
    try {
        document.getElementById('runtime-settings-save').disabled = true;
        const response = await fetch('/proxy_webot_runtime_settings', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({session_id: view.targetSession, settings, reset}),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(payload.detail || payload.error));
        await loadRuntimeSettingsScope();
        if (view.targetSession === runtimeSettingsCurrentAgent() && typeof setRunMode === 'function') setRunMode(view.original.approval.mode);
        status.textContent = runtimeSettingsText('已保存，下次调用生效。', 'Saved. Applies on the next call.');
        document.dispatchEvent(new CustomEvent('clawcross:runtime-settings-saved', {detail:{agentId:view.targetSession}}));
    } catch (error) { status.textContent = String(error.message || error); }
    finally { document.getElementById('runtime-settings-save').disabled = false; }
}

function showRuntimeSettingsTab(section) {
    if (!runtimeSettingsView) return;
    if (runtimeSettingsView.external && section === 'context') section = 'approval';
    runtimeSettingsView.activeTab = section;
    for (const name of ['context', 'approval']) {
        const selected = name === section;
        const tab = document.getElementById(`runtime-settings-${name}-tab`);
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        const panel = document.getElementById(`runtime-settings-${name}`);
        if (panel) panel.hidden = !selected;
    }
}

function removeRememberedSandboxGrant(button) {
    const input = document.querySelector('#runtime-settings-fields [data-key="sandbox_grants"]');
    const grants = JSON.parse(input.value);
    input.value = JSON.stringify(grants.filter(grant => grant.access !== button.dataset.access || grant.target !== button.dataset.target));
    const row = button.closest('[data-sandbox-grant]');
    const count = row.closest('details').querySelector('summary span');
    row.remove();
    count.textContent = JSON.parse(input.value).length;
    document.getElementById('runtime-settings-result').textContent = runtimeSettingsText('已移除，请保存设置。', 'Removed. Save settings to apply.');
}

function updateRuntimeReviewerHint() {
    const mode = document.querySelector('#runtime-settings-fields [data-key="mode"]').value;
    const hints = {
        chat: ['仅通过文字交流，不调用工具。', 'Text conversation only, with no tool calls.'],
        readonly: ['可以查看文件、搜索和分析；不能写入、执行命令或发送消息。', 'View files, search and analyze. No writes, commands, or messages.'],
        manual: ['全部工具可用；允许的操作直接执行，需要批准的操作由你通过按钮或当前对话中的 Y/N/KEEP Y 确认。', 'All tools are available. Allowed actions run directly; approval requests are confirmed by you using buttons or Y/N/KEEP Y in the conversation.'],
        bypass: ['开放工具并跳过操作确认。显式禁止规则仍然生效。', 'Tools are available without confirmation. Explicit deny rules still apply.'],
        auto: ['允许的操作直接执行；需要批准的操作由 AI 选择 Y、N 或 KEEP Y。KEEP Y 在当前 Agent 记住授权；依据不足或审核失败时不执行，可在后续对话中明确授权。', 'Allowed actions run directly; AI chooses Y, N, or KEEP Y for approval requests. KEEP Y remembers permission for this Agent; insufficient authorization or review failure blocks execution. You can authorize it in a later message.'],
    };
    document.getElementById('runtime-settings-reviewer-hint').textContent = runtimeSettingsText(...hints[mode]);
}

function closeRuntimeSettings() {
    const overlay = document.getElementById('runtime-settings-modal');
    if (overlay) overlay.style.display = 'none';
    if (runtimeSettingsView && runtimeSettingsView.returnFocus) runtimeSettingsView.returnFocus.focus();
}

document.addEventListener('keydown', event => {
    const overlay = document.getElementById('runtime-settings-modal');
    if (!overlay || overlay.style.display === 'none') return;
    if (event.key === 'Escape') {
        event.preventDefault();
        closeRuntimeSettings();
    } else if (event.target.matches('#runtime-settings-modal [role="tab"]') && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault();
        const section = runtimeSettingsView.activeTab === 'context' ? 'approval' : 'context';
        showRuntimeSettingsTab(section);
        document.getElementById(`runtime-settings-${section}-tab`).focus();
    } else if (event.key === 'Tab') {
        const focusable = Array.from(overlay.querySelectorAll('button, input, select, textarea, summary, [tabindex="0"]'))
            .filter(element => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
        const next = event.shiftKey ? focusable[focusable.length - 1] : focusable[0];
        const boundary = event.shiftKey ? focusable[0] : focusable[focusable.length - 1];
        if (event.target === boundary || !overlay.contains(event.target)) { event.preventDefault(); next.focus(); }
    }
});

// Component estimates are scaled to the API total; do not count archived originals.
function renderRuntimeContextUsage(usage = {}, configuredWindow = 0) {
    const n = value => Math.max(0, Number(value) || 0);
    const budget = n(configuredWindow) || n(usage.budget) || 1000000;
    const used = n(usage.tokens);
    const breakdown = usage.breakdown || {};
    const groups = [
        {label: runtimeSettingsText('对话历史', 'Conversation'), value: n(breakdown.messages) + n(breakdown.output), color: '#3b82f6'},
        {label: runtimeSettingsText('工具结果', 'Tool results'), value: n(breakdown.tool_results), color: '#14b8a6'},
        {label: runtimeSettingsText('压缩摘要', 'Summary'), value: n(breakdown.summary), color: '#8b5cf6'},
        {label: runtimeSettingsText('提示词与工具定义', 'Prompts and tools'), value: n(breakdown.system_prompt) + n(breakdown.tools) + n(breakdown.runtime_state), color: '#94a3b8'},
    ];
    const total = groups.reduce((sum, group) => sum + group.value, 0);
    if (!total) groups.splice(0, groups.length, {label: runtimeSettingsText('已用上下文', 'Used context'), value: used, color: '#3b82f6'});
    else if (used > total) groups.push({label: runtimeSettingsText('其他输入', 'Other input'), value: used - total, color: '#cbd5e1'});
    const scale = total > used && used > 0 ? used / total : 1;
    const pct = Math.min(100, used / budget * 100);
    const count = value => Math.round(value).toLocaleString();
    const label = usage.source === 'api'
        ? runtimeSettingsText('上轮上下文占用（API 实测）', 'Last context usage (API measured)')
        : runtimeSettingsText('当前上下文占用（估算，待 API 校准）', 'Current context usage (estimated; awaiting API measurement)');
    const percent = pct === 0 ? '0' : pct < 1 ? pct.toFixed(2) : pct.toFixed(1);
    return `<div class="runtime-context-usage">
        <div class="runtime-context-usage-heading"><span>${label}</span><strong>${percent}%</strong></div>
        <div class="runtime-context-usage-count">${count(used)} / ${count(budget)} tokens</div>
        <div class="runtime-context-usage-bar" role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="${budget}" aria-valuenow="${Math.min(used, budget)}">
            ${groups.filter(g => g.value > 0).map(g => `<span style="width:${Math.min(100, g.value * scale / budget * 100)}%;background:${g.color}" title="${g.label}: ${count(g.value * scale)} tokens"></span>`).join('')}
        </div>
        <div class="runtime-context-usage-legend">${groups.filter(g => g.value > 0).map(g => `<span><i style="background:${g.color}"></i>${g.label} <b>${count(g.value * scale)}</b></span>`).join('')}
        <small>${runtimeSettingsText('剩余', 'Remaining')} ${count(Math.max(0, budget - used))} tokens${usage.source === 'api' && total ? runtimeSettingsText(' · 分项为估算', ' · Component estimates') : ''}</small>
    </div>`;
}
