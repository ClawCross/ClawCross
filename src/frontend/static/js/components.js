// Downloads are explicit, scoped to their settings, and never enable a feature.
function componentControlMarkup(name) {
    return `<div data-component="${name}" style="margin:8px 0;font-size:12px;line-height:1.6;overflow-wrap:anywhere"><span data-component-status role="status">正在检查组件…</span> <button type="button" data-component-install style="padding:5px 10px;border:1px solid var(--border,#d1d5db);border-radius:8px;background:transparent;color:inherit;cursor:pointer" hidden>下载并安装</button></div>`;
}
async function refreshComponentControl(container) {
    if (!container || !container.isConnected) return;
    const name = container.dataset.component;
    const status = container.querySelector('[data-component-status]');
    const button = container.querySelector('[data-component-install]');
    try {
        const response = await fetch(`/proxy_components/${name}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '无法检查组件');
        const installing = data.state === 'installing';
        status.textContent = installing ? '正在下载并安装…' : data.state === 'failed' ? `安装失败：${data.detail}` : data.installed ? `${name === 'srt-system' ? '沙盒系统依赖' : name.toUpperCase()} 已安装` : `${name === 'srt-system' ? '沙盒系统依赖' : name.toUpperCase()} 尚未安装`;
        if (name === 'srt' && data.missing.some(x => !x.startsWith('npm') && x !== 'srt-update')) {
            let dependencies = container.querySelector('[data-component="srt-system"]');
            if (!dependencies) {
                container.insertAdjacentHTML('beforeend', componentControlMarkup('srt-system'));
                dependencies = container.querySelector('[data-component="srt-system"]');
                dependencies.querySelector('button').textContent = data.platform === 'win32' ? '初始化 Windows 沙盒（需管理员确认）' : '安装系统依赖（需要服务器管理员权限）';
                refreshComponentControl(dependencies);
            }
        }
        if (!installing && data.missing.includes('uv')) status.textContent += '；未找到 uv，请检查服务器启动环境。';
        const ordinaryMissing = data.missing.filter(x => !['uv', 'windows-install', 'srt-update'].includes(x));
        if (!installing && ordinaryMissing.length) status.textContent += `；缺少 ${ordinaryMissing.join('、')}，请在服务器安装${ordinaryMissing.some(x => x.startsWith('npm')) ? ' Node.js 20.11+（含 npm）' : '系统依赖'}。`;
        if (!installing && data.missing.includes('srt-update')) status.textContent += '；请更新至包含 Windows 后端的 SRT 0.0.78+。';
        if (!installing && data.missing.includes('windows-install')) status.textContent += '；需要在 Windows 主机初始化专用沙盒账号和网络隔离（会请求管理员确认）。';
        if (name === 'srt-system' && data.platform === 'win32') button.textContent = '初始化 Windows 沙盒（需管理员确认）';
        else if (data.needs_update) button.textContent = '更新沙盒组件';
        button.hidden = (data.installed && !data.needs_update) || installing;
        button.disabled = !data.can_install;
        button.onclick = async () => {
            button.disabled = true;
            try {
                const result = await fetch(`/proxy_components/${name}`, {method: 'POST', headers: {'X-Requested-With':'ClawCross'}});
                const job = await result.json();
                if (!result.ok || job.state === 'busy') throw new Error(job.error || job.detail);
                await refreshComponentControl(container);
            } catch (error) { status.textContent = error.message; button.disabled = false; }
        };
        if (installing) setTimeout(() => refreshComponentControl(container), 1500);
    } catch (error) { status.textContent = error.message || '组件状态暂时不可用'; }
}
function initComponentControls(root = document) {
    root.querySelectorAll('[data-component]').forEach(refreshComponentControl);
}


const componentSettingsGroups = {
    agents: {title:'连接其它 Agent', items:[['acpx','连接 Codex、Claude Code 等命令行 Agent。对应 Agent 的程序、账号和授权需另行准备。']]},
    channels: {title:'连接聊天平台', items:[['weclaw','连接微信，需要下载 WeClaw 程序。'],['nonebot','连接 NoneBot 支持的平台；适配器和账号在渠道设置中配置。'],['channels','QQ 与 Telegram 的 Python 连接依赖。']]},
    tunnel: {title:'公网访问', items:[['cloudflared','下载 Cloudflare Tunnel 程序。安装后在公网访问设置中主动开启通道。']]},
};
function componentSettingsMarkup(group) {
    const definition = componentSettingsGroups[group];
    return definition.items.map(([name,description]) => `<section style="padding:12px 0;border-bottom:1px solid var(--border,#e5e7eb)"><strong>${name === 'channels' ? 'QQ / Telegram' : name}</strong><p style="margin:6px 0;color:var(--text-secondary,#6b7280);font-size:13px">${description}</p>${componentControlMarkup(name)}</section>`).join('');
}
