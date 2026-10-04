/* Owner-managed KEEP Y entries. Changes apply immediately to this Agent. */
(function () {
  const text = (zh, en) => document.documentElement.lang.startsWith('en') ? en : zh;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const accessName = access => text({network:'联网',read_path:'只读文件',write_path:'读写文件'}[access],
                                    {network:'Network',read_path:'Read files',write_path:'Read/write files'}[access]);

  async function mount(container, agentId) {
    const base = '/v1/agents/' + encodeURIComponent(agentId) + '/remembered-approvals';
    container.innerHTML = `<details class="runtime-settings-advanced remembered-permissions">
      <summary>KEEP Y · ${text('持续授权', 'Remembered permissions')}<span data-count>…</span></summary>
      <div class="runtime-settings-advanced-body">
        <p class="runtime-settings-note">${text('仅属于当前 Agent。添加和移除立即保存；工具授权精确匹配参数，沙盒权限仍受管理员上限约束。两者都不能解除明确禁止或严格限制。', 'Only for this Agent. Adding or removing saves immediately. Tool grants match exact arguments; sandbox grants remain within administrator limits. Neither can override explicit denials or strict restrictions.')}</p>
        <p class="remembered-strict-note" data-strict hidden>${text('严格模式：沙盒提权条目不生效，也不能新增；已有条目仍可删除。', 'Strict mode: sandbox expansion grants are inactive and cannot be added. Existing entries can still be removed.')}</p>
        <div data-list>${text('加载中…', 'Loading…')}</div>
        <details class="remembered-add"><summary>＋ ${text('添加持续授权', 'Add a permission')}</summary>
          <form data-add>
            <label class="runtime-settings-field"><span>${text('授权类型', 'Permission type')}</span><select name="kind" class="runtime-settings-input">
              <option value="network">${accessName('network')}</option><option value="read_path">${accessName('read_path')}</option><option value="write_path">${accessName('write_path')}</option><option value="tool">${text('工具调用 · 完整参数', 'Tool call · Exact arguments')}</option>
            </select></label>
            <label class="runtime-settings-field" data-target-field><span>${text('具体目标', 'Exact target')}</span><input name="target" class="runtime-settings-input" maxlength="4096" placeholder="example.com:443" required><small data-target-hint></small></label>
            <div data-tool-fields hidden>
              <label class="runtime-settings-field"><span>${text('ClawCross 工具名称', 'ClawCross tool name')}</span><input name="tool_name" class="runtime-settings-input" maxlength="100" placeholder="web_fetch"></label>
              <label class="runtime-settings-field"><span>${text('工具参数（JSON）', 'Tool arguments (JSON)')}</span><textarea name="arguments" class="runtime-settings-input" rows="3" spellcheck="false" placeholder='{"url":"https://example.com"}'>{}</textarea><small>${text('填写具体操作的完整参数；用户和 Agent 身份由系统绑定。不会执行这个操作。', 'Enter the exact operation arguments. The system binds the user and Agent identity. This does not execute the operation.')}</small></label>
            </div>
            <button type="submit" class="runtime-settings-btn runtime-settings-btn-primary">${text('添加授权', 'Add permission')}</button>
          </form>
        </details>
        <p data-status class="remembered-status" role="status" aria-live="polite"></p>
      </div></details>`;
    const form = container.querySelector('[data-add]');
    const status = container.querySelector('[data-status]');

    function updateFields() {
      const kind = form.elements.kind.value;
      const tool = kind === 'tool';
      container.querySelector('[data-target-field]').hidden = tool;
      container.querySelector('[data-tool-fields]').hidden = !tool;
      form.elements.target.required = !tool;
      form.elements.target.disabled = tool;
      form.elements.tool_name.required = tool;
      form.elements.tool_name.disabled = !tool;
      form.elements.arguments.disabled = !tool;
      form.elements.target.placeholder = kind === 'network' ? 'example.com:443' : text('具体文件或目录的绝对路径', 'Absolute path to a file or directory');
      container.querySelector('[data-target-hint]').textContent = kind === 'network'
        ? text('域名或公网 IP，可带端口；不接受 URL 或通配符。', 'A domain or public IP, optionally with a port. No URLs or wildcards.')
        : text('只能在管理员允许提权的范围内添加；不能授权配置或其他用户的数据。', 'Must be within administrator escalation limits. Configuration and other users’ data cannot be granted.');
    }

    async function request(url = base, options = {}) {
      const response = await fetch(url, options);
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof (payload.detail || payload.error) === 'string'
        ? payload.detail || payload.error : JSON.stringify(payload.detail || payload.error));
      return payload;
    }

    function render(payload) {
      if (!container.isConnected) return;
      const strict = payload.sandbox_security === 'strict';
      const actions = payload.actions || [];
      const grants = payload.sandbox_grants || [];
      container.querySelector('[data-count]').textContent = actions.length + grants.length;
      container.querySelector('[data-strict]').hidden = !strict;
      [...form.elements.kind.options].forEach(option => { option.disabled = strict && option.value !== 'tool'; });
      if (strict) form.elements.kind.value = 'tool';
      updateFields();
      container.querySelector('[data-list]').innerHTML = [...grants.map(grant => `
        <div class="remembered-grant" data-sandbox-grant><div><strong>${escape(accessName(grant.access))}</strong>${strict ? `<small class="remembered-inactive">${text('严格模式下不生效', 'Inactive in strict mode')}</small>` : ''}<p>${escape(grant.target)}</p></div>
          <button type="button" class="runtime-settings-btn runtime-settings-btn-secondary" data-remove data-access="${escape(grant.access)}" data-key="${escape(grant.key)}">${text('移除', 'Remove')}</button></div>`),
        ...actions.map(action => `<div class="remembered-grant" data-tool-grant><div><strong>${text('工具', 'Tool')} · ${escape(action.tool)}</strong>
          <details><summary>${text('查看精确参数', 'View exact arguments')}</summary><pre>${escape(JSON.stringify(action.arguments || {fields:action.summary}, null, 2))}</pre></details></div>
          <button type="button" class="runtime-settings-btn runtime-settings-btn-secondary" data-remove data-tool="${escape(action.tool)}" data-key="${escape(action.key)}">${text('移除', 'Remove')}</button></div>`)].join('') || `<p class="remembered-empty">${text('这个 Agent 还没有 KEEP Y 授权。', 'This Agent has no KEEP Y permissions yet.')}</p>`;
    }

    container.addEventListener('change', event => { if (event.target === form.elements.kind) updateFields(); });
    container.addEventListener('click', async event => {
      const button = event.target.closest('[data-remove]');
      if (!button || button.disabled) return;
      button.disabled = true;
      status.textContent = text('正在移除…', 'Removing…');
      try {
        const segment = button.dataset.access ? 'sandbox/' + encodeURIComponent(button.dataset.access) : encodeURIComponent(button.dataset.tool);
        await request(base + '/' + segment + '/' + encodeURIComponent(button.dataset.key), {method:'DELETE'});
        render(await request());
        status.textContent = text('已移除；后续调用会重新按当前权限审核。', 'Removed. Future calls follow the current approval policy.');
      } catch (error) { status.textContent = error.message; button.disabled = false; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const button = form.querySelector('[type=submit]');
      const body = {kind:form.elements.kind.value};
      try {
        if (body.kind === 'tool') {
          body.tool_name = form.elements.tool_name.value.trim();
          body.arguments = JSON.parse(form.elements.arguments.value);
          if (!body.arguments || Array.isArray(body.arguments) || typeof body.arguments !== 'object') throw new Error(text('工具参数必须是 JSON 对象。', 'Arguments must be a JSON object.'));
        } else body.target = form.elements.target.value.trim();
        button.disabled = true;
        status.textContent = text('正在保存…', 'Saving…');
        render(await request(base, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
        form.elements.target.value = '';
        status.textContent = text('已添加，下一次工具调用生效。', 'Added. Applies to the next tool call.');
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
    updateFields();
    try { render(await request()); }
    catch (error) {
      container.querySelector('[data-list]').textContent = text('授权列表加载失败。', 'Could not load permissions.');
      status.textContent = error.message;
    }
  }
  window.RememberedApprovals = {mount};
})();
