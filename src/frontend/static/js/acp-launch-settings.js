/* Shared launch form for Studio, mobile and per-Agent settings. No process starts here. */
(function (root) {
  const esc = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const en = () => document.documentElement.lang.startsWith('en');
  const text = (zh, english) => en() ? english : zh;
  const presets = {codebuddy:'codebuddy --acp', zcode:'zcode-acp-server', acp:''};
  function mount(id, platform, saved = {}, locked = false) {
    const host = document.getElementById(id); if (!host) return;
    const name = saved.agent_name || '', command = saved.agent_command || presets[platform] || '';
    const custom = Object.hasOwn(presets, platform);
    host.innerHTML = `<details ${custom ? 'open' : ''} class="acp-launch-form"><summary>${text('ACP 启动方式','ACP launcher')}</summary>
      <p style="font-size:12px;opacity:.75;line-height:1.6">${text('使用已安装并登录的 ACP 程序。仅保存配置，点击测试连接或发消息时才会启动。','Use an installed, authenticated ACP program. Saving does not start it; testing or messaging does.')}</p>
      <label style="display:block;margin:10px 0">${text('调用方式','Launch method')}<select data-launch-kind ${locked ? 'disabled' : ''} style="display:block;width:100%;margin-top:5px;padding:8px;border-radius:9px;border:1px solid var(--border,#d5dce5);background:var(--bg-card,#fff);color:inherit">
        <option value="command" ${!name?'selected':''}>${text('启动命令 · acpx --agent','Command · acpx --agent')}</option>
        <option value="name" ${name?'selected':''}>${text('acpx 中已配置的 Agent 名称','Agent name configured in acpx')}</option></select></label>
      <label style="display:block;margin:10px 0">${text('名称或命令','Name or command')}<input data-launch-value value="${esc(name || command)}" ${locked?'readonly':''} placeholder="${custom ? 'my-agent --acp' : text('留空使用平台原生启动方式','Leave empty for the native launcher')}" autocomplete="off" spellcheck="false" style="display:block;width:100%;box-sizing:border-box;margin-top:5px;padding:10px;border-radius:9px;border:1px solid var(--border,#d5dce5);background:var(--bg-card,#fff);color:inherit;font-family:monospace"></label>
      <small style="display:block;opacity:.75;line-height:1.6">${locked ? text('当前会话已连接，启动方式已固定。使用其他程序请新建 Agent。','The connected session keeps its launcher. Create another Agent to use a different program.') : text('命令填写 --agent 后面的内容，保留路径和参数的大小写。名称方式可引用 ~/.acpx/config.json 中的自定义配置；Windows 建议用名称及结构化 argv 配置。','For a command, enter the value after --agent, preserving case. Name mode uses ~/.acpx/config.json aliases; on Windows use a named agent with structured argv.')}</small>
      ${platform==='zcode'?'<p style="font-size:12px">ZCode '+text('需要安装第三方 ACP 桥接程序','requires a third-party ACP bridge')+' <code>zcode-acp-server</code> · <a href="https://github.com/william0wang/zcode-acp" target="_blank" rel="noopener">'+text('安装说明','Installation')+'</a></p>':''}
      ${platform==='codebuddy'?'<p style="font-size:12px"><a href="https://www.codebuddy.ai/docs/cli/acp" target="_blank" rel="noopener">CodeBuddy ACP '+text('安装与登录说明','setup and login')+'</a></p>':''}
    </details>`;
  }
  function value(id, platform) {
    const host = document.getElementById(id), input = host?.querySelector('[data-launch-value]');
    if (!input) return {};
    const value = input.value.trim(), named = host.querySelector('[data-launch-kind]').value === 'name';
    if (platform === 'acp' && !value) throw new Error(text('请填写 ACP Agent 名称或启动命令','Enter an ACP agent name or command'));
    if (!value) return {};
    return {agent_name: named ? value : '', agent_command: named ? '' : value};
  }
  root.AcpLaunchSettings = {mount, value, presets};
})(window);
