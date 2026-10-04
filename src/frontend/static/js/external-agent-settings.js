/* Shared ACP settings: native choices come from the adapter, never guessed. */
(function () {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cache = new Map();
  let openSequence = 0;
  function currentTarget() {
    if (typeof _ocChatMode !== 'undefined' && _ocChatMode === 'acp' &&
        typeof acpResolveSessionName === 'function') return acpResolveSessionName();
    return typeof currentSessionId !== 'undefined' ? currentSessionId : '';
  }
  function isAcpTab() {
    return typeof _ocChatMode !== 'undefined' && _ocChatMode === 'acp' && typeof _acpTool !== 'undefined' && Boolean(_acpTool);
  }
  async function request(path, method = 'GET', body) {
    const result = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await result.json();
    if (!result.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data.error));
      error.status = result.status;
      throw error;
    }
    return data;
  }
  async function capabilities(id) {
    if (!id) return null;
    const previous = cache.get(id);
    if (previous && Date.now() - previous.at < 15000) return previous.value;
    const value = await request('/v1/agents/' + encodeURIComponent(id) + '/capabilities');
    cache.set(id, {value, at: Date.now()});
    return value;
  }
  async function open(id = currentTarget(), refreshed = null) {
    if (!id) return;
    const sequence = ++openSequence;
    let card;
    try { card = refreshed?.card || await capabilities(id); } catch (error) {
      // Inspecting an uncreated profile is read-only. Creation requires a button click.
      if (error.status === 404 && isAcpTab() && id === currentTarget()) {
        card = {platform: _acpTool, transport: 'acpx', uncreated: true,
          settings: {clawcross_tools: true}, config_options: []};
      } else { window.alert(error.message); return; }
    }
    if (sequence !== openSequence) return;
    if (card.transport !== 'acpx') return;
    document.getElementById('external-agent-settings')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'external-agent-settings';
    overlay.className = 'external-settings-overlay';
    overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'external-settings-title');
    let uncreated = Boolean(card.uncreated);
    const createIfNeeded = async () => {
      if (!uncreated) return;
      await studioEnsureAgent(id, {platform: card.platform});
      uncreated = false;
      cache.delete(id);
      overlay.querySelector('[data-save]').textContent = '保存';
      overlay.querySelector('[data-test]').textContent = '测试连接';
      overlay.querySelector('[data-uncreated]')?.remove();
    };
    const settings = card.settings || {};
    const options = Array.isArray(card.config_options) ? card.config_options : [];
    overlay.innerHTML = `<section class="external-settings-dialog">
      <header><h2 id="external-settings-title">${escape(card.platform)} · Agent 设置</h2><button type="button" data-close aria-label="关闭">×</button></header>
      ${uncreated ? '<p role="status" data-uncreated>此 Agent 尚未创建。点击下方创建按钮后才会创建。</p>' : ''}
      <p>设置仅用于这个 Agent，从下一轮调用生效。原生模式和权限由外部 Agent 执行。</p>
      <div class="external-settings-fields">${options.length ? options.map((item, index) => {
        const selected = settings.config_options?.[item.id] ?? item.currentValue ?? '';
        if (window.ReasoningLevels && Object.keys(item.reasoning_level_map || {}).length) {
          const level = settings.reasoning_level === 0 ? 0 : ReasoningLevels.selected(settings.reasoning_level,selected);
          return `<label>${escape(item.name || item.id)} · 7 级${ReasoningLevels.slider({mapping:item.reasoning_level_map,level,autoLabel:'自动 · ' + (item.currentValue || selected || '保持原生设置'),attributes:`data-option="${escape(item.id)}" data-unified-effort data-initial-value="${level}"`})}<small>箭头后是当前模型的实际档位；切换模型后，下次调用会按新模型重新映射。</small></label>`;
        }
        if (['reasoning_effort','effort'].includes(item.id) && item.options?.length) return `<label>${escape(item.name || item.id)}${ReasoningLevels.slider({choices:item.options.map(choice=>({value:choice.value,label:choice.name || choice.value})),value:selected,attributes:`data-option="${escape(item.id)}" data-initial-value="${escape(selected)}"`})}${item.description ? `<small>${escape(item.description)}</small>` : ''}</label>`;
        return `<label>${escape(item.name || item.id)}<select data-option="${escape(item.id)}" id="external-option-${index}" data-initial-value="${escape(selected)}">
          ${(item.options || []).map(choice => `<option value="${escape(choice.value)}" ${String(selected) === String(choice.value) ? 'selected' : ''}>${escape(choice.name || choice.value)}</option>`).join('')}
          </select>${item.description ? `<small>${escape(item.description)}</small>` : ''}</label>`;
      }).join('') : '<p>连接成功后会显示模型和思考强度。</p>'}
      <label class="external-settings-toggle"><input type="checkbox" id="external-tools" ${settings.clawcross_tools ? 'checked' : ''}> 使用 ClawCross 工具</label>
      <small>通过 MCP 接入，默认启用。调用受当前用户、Agent 工具名单、模式、命令规则及审核约束。原生 CLI 工具使用自身的权限策略；“替我审核”只审核 ClawCross 工具。</small>
      <details><summary>连接与工具范围</summary>
        <label>调用超时（秒）<input id="external-timeout" type="number" min="5" max="3600" value="${escape(settings.timeout_sec || 180)}"></label>
        <label>空闲连接保留（秒）<input id="external-ttl" type="number" min="60" max="86400" value="${escape(settings.ttl_sec || 300)}"></label>
        <label>允许的 ClawCross 工具<input id="external-tool-list" value="${escape((settings.tools || []).join(', '))}" placeholder="留空跟随全部可用工具；用逗号分隔工具名"></label>
        <small>工具仍受每轮选择与服务器审核限制。关闭连接器可禁止该 Agent 调用 ClawCross 工具。</small>
      </details></div>
      <footer><span role="status" data-status>${escape(refreshed?.status || '')}</span><div><button type="button" data-test>${uncreated ? '创建并测试连接' : '重新检测'}</button> <button type="button" data-save>${uncreated ? '创建并保存' : '保存'}</button></div></footer>
    </section>`;
    const previousFocus = refreshed?.focus || document.activeElement;
    const close = () => { overlay.remove(); previousFocus?.focus(); };
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    overlay.addEventListener('keydown', event => {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const elements = [...overlay.querySelectorAll('button,input,select,summary')].filter(el => el.offsetParent !== null && !el.disabled);
        if (document.activeElement === overlay) {
          event.preventDefault(); (event.shiftKey ? elements.at(-1) : elements[0])?.focus();
        }
        if (event.shiftKey && document.activeElement === elements[0]) { event.preventDefault(); elements.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === elements.at(-1)) { event.preventDefault(); elements[0]?.focus(); }
      }
    });
    overlay.querySelector('[data-close]').onclick = close;
    overlay.querySelector('[data-test]').onclick = async function () {
      this.disabled = true;
      const save = overlay.querySelector('[data-save]'); save.disabled = true;
      const status = overlay.querySelector('[data-status]'); status.textContent = '连接中…';
      try {
        await createIfNeeded();
        const fresh = await request('/v1/agents/' + encodeURIComponent(id) + '/test-connection', 'POST');
        cache.set(id, {value:fresh, at:Date.now()});
        if (!overlay.isConnected) return;
        // Preserve edits made while automatic connection testing was in flight.
        const changed = {};
        overlay.querySelectorAll('[data-option]').forEach(el => {
          const value = ReasoningLevels.value(el);
          if (!el.hasAttribute('data-unified-effort') && value !== el.dataset.initialValue) changed[el.dataset.option] = value;
        });
        const draft = {clawcross_tools: overlay.querySelector('#external-tools').checked,
          timeout_sec: Number(overlay.querySelector('#external-timeout').value),
          ttl_sec: Number(overlay.querySelector('#external-ttl').value)};
        const effortInput = overlay.querySelector('[data-unified-effort]');
        if (effortInput) draft.reasoning_level = Number(effortInput.value);
        const toolList = overlay.querySelector('#external-tool-list').value;
        const display = {...fresh, settings:{...fresh.settings, ...draft,
          config_options:{...fresh.settings?.config_options, ...changed}}};
        await open(id, {card:display, focus:previousFocus, status:'连接成功，配置已更新'});
        document.querySelector('#external-tool-list').value = toolList;
        document.querySelector('#external-agent-settings [data-test]').focus();
        syncMenu();
      } catch (error) { if (overlay.isConnected) status.textContent = '连接失败：' + error.message; }
      finally { if (overlay.isConnected) { this.disabled = false; save.disabled = false; } }
    };
    overlay.querySelector('[data-save]').onclick = async function () {
      this.disabled = true;
      overlay.focus();
      const status = overlay.querySelector('[data-status]');
      status.textContent = '保存中…';
      try {
        const config_options = {};
        overlay.querySelectorAll('[data-option]').forEach(el => { const value = ReasoningLevels.value(el); if (value && !el.hasAttribute('data-unified-effort')) config_options[el.dataset.option] = value; });
        const effortInput = overlay.querySelector('[data-unified-effort]');
        const names = overlay.querySelector('#external-tool-list').value.split(/[,，\s]+/).filter(Boolean);
        await createIfNeeded();
        const saved = await request('/v1/agents/' + encodeURIComponent(id) + '/acp-settings', 'PATCH', {
          config_options, ...(effortInput ? {reasoning_level:Number(effortInput.value)} : {}), clawcross_tools: overlay.querySelector('#external-tools').checked,
          timeout_sec: Number(overlay.querySelector('#external-timeout').value),
          ttl_sec: Number(overlay.querySelector('#external-ttl').value), tools: names.length ? names : null});
        cache.set(id, {value: saved, at: Date.now()});
        status.textContent = '已保存，下一轮生效';
        syncMenu();
      } catch (error) { status.textContent = error.message; }
      finally { this.disabled = false; if (overlay.isConnected) this.focus(); }
    };
    document.body.appendChild(overlay);
    overlay.querySelector('[data-close]').focus();
    if (!uncreated && !refreshed) overlay.querySelector('[data-test]').click();
  }
  async function syncMenu(id = currentTarget()) {
    let card;
    try { card = await capabilities(id); } catch (error) {
      if (error.status === 404 && isAcpTab() && id === currentTarget()) {
        card = {transport: 'acpx', clawcross_tools: true, uncreated: true};
      } else if (error.status === 404 && id === currentTarget()) {
        card = {transport: 'webot'};
      } else { return; }
    }
    if (currentTarget() !== id) return;
    const acp = card?.transport === 'acpx';
    document.querySelectorAll('[data-webot-runtime]').forEach(el => { el.hidden = acp; });
    const button = document.getElementById('studio-external-settings');
    if (button) button.hidden = !acp;
    const context = document.querySelector('.oc-context-usage-wrap');
    if (context) context.hidden = acp;
    const hint = document.getElementById('external-runtime-hint');
    if (hint) { hint.hidden = !acp; hint.textContent = card.uncreated ? '尚未创建 Agent；发送消息或明确点击创建后才会创建。' : card.clawcross_tools ?
      'ClawCross 工具已连接；本轮模式约束 ClawCross 工具。原生工具权限见 Agent 设置。' :
      '当前使用原生工具；ClawCross 工具未连接。原生权限与思考强度见 Agent 设置。'; }
    const wrapper = document.getElementById('tool-panel');
    if (wrapper) wrapper.hidden = acp && !card.clawcross_tools;
    const toggle = document.getElementById('tool-toggle-btn');
    if (toggle) toggle.hidden = acp && !card.clawcross_tools;
    const mode = document.getElementById('oc-run-mode');
    if (mode) mode.title = acp ? 'ClawCross 工具按此模式审核；原生工具由适配器权限控制' : '';
    if (typeof syncAgentRunMode === 'function') syncAgentRunMode(id);
  }
  window.ExternalAgentSettings = {open, capabilities, syncMenu, currentTarget, peek: id => cache.get(id)?.value};
  window.openExternalAgentSettings = id => open(id || currentTarget());
})();
