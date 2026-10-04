/* API profiles are user-owned; choosing one changes only the selected Agent. */
(function () {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = (zh, en) => document.documentElement.lang.startsWith('en') ? en : zh;
  let sequence = 0;
  async function request(path, method = 'GET', body) {
    const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data.error));
      error.status = response.status; throw error;
    }
    return data;
  }
  async function open(id = runtimeSettingsCurrentAgent()) {
    if (!id) { window.alert(text('请先选择一个本地 Agent。', 'Choose a local Agent first.')); return; }
    // An unsaved external profile still uses its native settings and explicit creation.
    if (window.ExternalAgentSettings?.currentTarget() === id && typeof _ocChatMode !== 'undefined' && _ocChatMode === 'acp') {
      return openExternalAgentSettings(id);
    }
    const token = ++sequence;
    let card, library, runtime;
    try {
      [card, library, runtime] = await Promise.all([
        request('/v1/agents/' + encodeURIComponent(id)).catch(error => {
          if (error.status === 404) return {agent_id:id, platform:'webot', settings:{}, uncreated:true};
          throw error;
        }),
        request('/v1/agents/model-profiles'),
        request('/proxy_webot_runtime_settings?session_id=' + encodeURIComponent(id)),
      ]);
    } catch (error) { window.alert(error.message); return; }
    if (token !== sequence) return;
    if (!['webot', 'http', 'llm'].includes(card.platform)) return openExternalAgentSettings(id);
    if (card.platform !== 'webot') { window.alert(text('此 Agent 使用自己的 API 连接配置。', 'This Agent uses its own API connection settings.')); return; }
    document.getElementById('agent-model-settings')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'agent-model-settings'; overlay.className = 'external-settings-overlay'; overlay.tabIndex = -1;
    overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'agent-model-title');
    const saved = card.settings.llm || {}, selected = saved.profile_id || '';
    const current = saved.model || library.default.model || text('尚未配置', 'Not configured');
    const capabilities = runtime.model_capabilities || {};
    const effort = runtime.settings?.inference?.reasoning_effort || '';
    const levels = capabilities.reasoning_effort_levels || [];
    const levelMap = capabilities.reasoning_level_map || {};
    const unified = window.ReasoningLevels && Object.keys(levelMap).length;
    const selectedLevel = unified ? ReasoningLevels.selected(runtime.settings?.inference?.reasoning_level, effort) : 0;
    const platformLabel = text('平台默认', 'Platform default') + ' · ' + (library.default.model || text('未配置', 'Not configured'));
    overlay.innerHTML = `<section class="external-settings-dialog model-profile-dialog">
      <header><h2 id="agent-model-title">${text('模型与思考', 'Model and reasoning')}</h2><button type="button" data-close aria-label="${text('关闭', 'Close')}">×</button></header>
      <p>${esc(card.name || id)} · ${text('仅修改这个 Agent，平台默认与其他 Agent 不变。', 'Changes only this Agent. Other Agents and the platform default stay unchanged.')}</p>
      <div class="model-profile-current"><span>${text('当前模型', 'Current model')}</span><strong>${esc(current)}</strong><small>${esc(saved.provider || library.default.provider)}</small></div>
      ${card.uncreated ? `<p>${text('应用配置后会创建这个 Agent，首次对话即可使用所选模型。', 'Applying settings creates this Agent, so its first message uses the selected model.')}</p>` : ''}
      <div class="external-settings-fields">
        <label>${text('选择已保存配置', 'Choose a saved profile')}<select data-profile>
          <option value="">${esc(platformLabel)}</option>
          ${saved.model && (!selected || !library.profiles.some(p => p.id === selected)) ? `<option value="__current__" selected>${esc(saved.model)} · ${text('当前独立配置', 'Current custom configuration')}</option>` : ''}
          ${library.profiles.map(p => `<option value="${esc(p.id)}" ${p.id === selected ? 'selected' : ''}>${esc(p.name)} · ${esc(p.model)}${p.id.startsWith('platform:') ? ' · ' + text('平台配置', 'Platform profile') : ''}</option>`).join('')}
        </select></label>
        <small data-profile-count>${text('已保存','Saved')} ${library.profiles.length} ${text('套模型配置；可在下方添加多套配置。','model profiles. Add more below.')}</small>
        ${selected && !library.profiles.some(p => p.id === selected) ? `<small>${text('原配置已不在列表中；当前 Agent 仍保留已应用的配置。', 'The original profile is no longer listed; this Agent keeps its applied configuration.')}</small>` : ''}
        <div data-effort-area>${unified ? `<label>${text('思考强度 · 7 级','Reasoning effort · 7 levels')}<select data-effort data-unified>${ReasoningLevels.options(levelMap,selectedLevel,text('自动 · 模型预设','Automatic · Model preset'))}</select><small>${text('箭头后是实际原生档位；部分级别会重复。按 Agent 保存，切换模型会重新映射。','The arrow shows the native setting; some levels repeat. Saved per Agent and remapped when switching models.')}</small></label>` : levels.length ? `<label>${text('思考强度', 'Reasoning effort')}<select data-effort><option value="">${text('模型预设', 'Model preset')}${capabilities.reasoning_effort_default ? ' · ' + esc(capabilities.reasoning_effort_default) : ''}</option>${levels.map(value => `<option value="${esc(value)}" ${value === effort ? 'selected' : ''}>${esc(value)}</option>`).join('')}</select><small>${text('按当前模型显示可用值，独立保存在这个 Agent。', 'Available values follow the current model and are saved for this Agent.')}</small></label>` : `<small>${text('此模型未声明可配置的思考强度，使用模型预设。', 'This model advertises no configurable effort; using its preset.')}</small>`}</div>
        <details class="model-profile-create"><summary>${text('保存或更新模型配置', 'Save or update a model profile')}</summary>
          <form data-profile-form>
            <label>${text('配置名称', 'Profile name')}<input name="name" required maxlength="100" autocomplete="off" placeholder="${text('例如：日常、编程', 'For example: everyday, coding')}"></label>
            <label>${text('API 提供方', 'API provider')}<select name="provider">${['openai','deepseek','anthropic','google','minimax','ollama'].map(p => `<option value="${p}">${p}</option>`).join('')}</select></label>
            <label>${text('模型名称', 'Model name')}<input name="model" required maxlength="200" placeholder="${text('填写 API 的准确模型名称', 'Exact API model name')}"></label>
            <label>${text('API 地址', 'API base URL')}<input name="base_url" type="url" placeholder="${text('留空使用提供方地址', 'Leave blank for the provider URL')}"></label>
            <label>${text('API 密钥', 'API key')}<input name="api_key" type="password" autocomplete="new-password" placeholder="${text('更新同名配置时留空保留密钥', 'Leave blank to keep the key when updating')}"></label>
            <small>${text('配置仅保存给当前用户；密钥直接提交到后端，不发送给对话模型。更新后重新应用才会改变 Agent。', 'Profiles belong to your user. Keys go directly to the backend, never the chat model. Reapply an updated profile to change an Agent.')}</small>
            <button type="submit">${text('保存配置', 'Save profile')}</button><span role="status" data-form-status></span>
          </form>
        </details>
      </div>
      <footer><span role="status" data-status></span><button type="button" data-save>${text('应用到当前 Agent', 'Apply to this Agent')}</button></footer>
    </section>`;
    const previousFocus = document.activeElement;
    let pendingLevel = runtime.settings?.inference?.reasoning_level || (window.ReasoningLevels ? ReasoningLevels.selected(0,effort) : 0);
    const renderSelectedEffort = () => {
      const previous = overlay.querySelector('[data-effort][data-unified]');
      if (previous) pendingLevel = Number(previous.value);
      const profileId = overlay.querySelector('[data-profile]').value;
      const chosen = profileId === '__current__' || profileId === selected && saved.model ? capabilities : profileId ? library.profiles.find(p=>p.id===profileId)?.model_capabilities : library.default.model_capabilities;
      const map = chosen?.reasoning_level_map || {};
      if (!window.ReasoningLevels || !chosen) return; // Compatibility with older capability responses.
      const host = overlay.querySelector('[data-effort-area]');
      host.innerHTML = Object.keys(map).length ? `<label>${text('思考强度 · 7 级','Reasoning effort · 7 levels')}<select data-effort data-unified>${ReasoningLevels.options(map,pendingLevel,text('自动 · 模型预设','Automatic · Model preset'))}</select><small>${text('箭头后是所选模型的实际档位；部分级别会重复。','The arrow shows the selected model’s native setting; some levels repeat.')}</small></label>` : `<small>${text('此模型未声明可配置的思考强度，使用模型预设。','This model advertises no configurable effort; using its preset.')}</small>`;
    };
    overlay.querySelector('[data-profile]').onchange = () => {
      const input = overlay.querySelector('[data-effort][data-unified]');
      if (input) pendingLevel = Number(input.value);
      renderSelectedEffort();
    };
    const close = () => { ++sequence; overlay.remove(); previousFocus?.focus(); };
    overlay.querySelector('[data-close]').onclick = close;
    overlay.onclick = event => { if (event.target === overlay) close(); };
    overlay.onkeydown = event => {
      if (event.key === 'Escape') close();
      if (event.key !== 'Tab') return;
      const focusable = [...overlay.querySelectorAll('button,input,select,summary')].filter(el => el.offsetParent !== null && !el.disabled);
      if (document.activeElement === overlay || (event.shiftKey && document.activeElement === focusable[0])) {
        event.preventDefault(); (event.shiftKey ? focusable.at(-1) : focusable[0])?.focus();
      } else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0]?.focus(); }
    };
    overlay.querySelector('[data-save]').onclick = async function () {
      const status = overlay.querySelector('[data-status]'); this.disabled = true;
      try {
        const profile_id = overlay.querySelector('[data-profile]').value;
        if (profile_id !== '__current__') await request('/v1/agents/' + encodeURIComponent(id) + '/model-profile', 'POST', {profile_id});
        const effortInput = overlay.querySelector('[data-effort]');
        if (effortInput?.hasAttribute('data-unified')) {
          const level = Number(effortInput.value);
          if (level !== (runtime.settings?.inference?.reasoning_level || 0) || effort) await request('/proxy_webot_runtime_settings', 'POST', {
            session_id:id, settings:{inference:{reasoning_level:level,reasoning_effort:''}}});
        } else if (effortInput && effortInput.value !== effort) await request('/proxy_webot_runtime_settings', 'POST', {
          session_id:id, settings:{inference:{reasoning_effort:effortInput.value}}});
        document.dispatchEvent(new CustomEvent('clawcross:runtime-settings-saved', {detail:{agentId:id}}));
        if (overlay.isConnected) {
          await open(id);
          const fresh = document.getElementById('agent-model-settings');
          if (fresh) fresh.querySelector('[data-status]').textContent = text('已保存，下次调用生效。', 'Saved. Applies on the next call.');
        }
      } catch (error) { status.textContent = error.message; }
      finally { this.disabled = false; }
    };
    overlay.querySelector('[data-profile-form]').onsubmit = async event => {
      event.preventDefault();
      const form = event.currentTarget, button = form.querySelector('button'), status = form.querySelector('[data-form-status]');
      button.disabled = true;
      try {
        const profile = await request('/v1/agents/model-profiles', 'POST', Object.fromEntries(new FormData(form)));
        const index = library.profiles.findIndex(p=>p.id===profile.id);
        if (index>=0) library.profiles[index]=profile; else library.profiles.push(profile);
        overlay.querySelector('[data-profile-count]').textContent = text('已保存','Saved') + ' ' + library.profiles.length + ' ' + text('套模型配置。','model profiles.');
        form.elements.api_key.value = '';
        const selector = overlay.querySelector('[data-profile]');
        let option = [...selector.options].find(item => item.value === profile.id);
        if (!option) { option = document.createElement('option'); selector.appendChild(option); }
        option.value = profile.id; option.textContent = profile.name + ' · ' + profile.model;
        selector.value = profile.id;
        renderSelectedEffort();
        status.textContent = text('配置已保存；点击“应用到当前 Agent”切换。', 'Profile saved. Apply it to switch this Agent.');
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    };
    document.body.appendChild(overlay); overlay.focus();
  }
  window.openAgentModelSettings = open;
})();
