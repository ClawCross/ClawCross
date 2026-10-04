/* Keep runtime context inspectable without filling the conversation. */
(function () {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function contextDetails(value, legacy = false) {
    return `<details class="runtime-context-details"><summary>${legacy ? '动态块与原始输入' : '动态块'}</summary><pre>${escape(value)}</pre></details>`;
  }
  function user(value, message = {}) {
    if (typeof message.runtime_context === 'string' && typeof message.user_input === 'string') {
      return (message.runtime_context ? contextDetails(message.runtime_context) : '') +
        `<span class="runtime-user-input">${escape(message.user_input)}</span>`;
    }
    const text = String(value || '');
    if (/^【(?:本轮 (?:identity_|groups|group_memberships|teams|skills|mode|workspace|cli_entry|tool_connector)|ClawCross 系统提示词(?:补丁|版本))/.test(text.trimStart())) {
      // Older records have no reliable input boundary. Keep the complete record;
      // never guess where a multi-paragraph user message begins.
      return contextDetails(text, true);
    }
    return escape(text);
  }
  function tool(html, title = '工具轨迹') {
    return `<details class="completed-tool-details"><summary>${escape(title)}</summary>${html}</details>`;
  }
  window.RuntimePresentation = {user, tool};
})();
