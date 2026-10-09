/* Visible Agent activity comes from the gateway, never from adapter probes. */
(() => {
  let pending = false;
  async function refresh() {
    if (pending || document.hidden) return;
    const rows = [...document.querySelectorAll('[data-agent-activity], [data-group-agent-ids]')].filter(el=>el.getClientRects().length);
    if (!rows.length) return;
    pending = true;
    try {
      const response = await fetch('/v1/agents/activity');
      if (!response.ok) return;
      const states = new Map(((await response.json()).data || []).map(agent=>[agent.agent_id,agent]));
      const english = document.documentElement.lang.startsWith('en');
      for (const row of rows) {
        if (row.dataset.agentActivity) {
          const state = states.get(row.dataset.agentActivity)?.state || 'unknown';
          row.dataset.activityState = state;
          const dot=row.querySelector('.agent-activity-dot');
          if (dot) { dot.hidden=state!=='running'; dot.title=english?'Agent is working':'Agent 正在处理'; dot.setAttribute('aria-label',dot.title); }
        } else {
          let ids=[]; try { ids=JSON.parse(row.dataset.groupAgentIds || '[]'); } catch (_) {}
          const count=ids.filter(id=>states.get(id)?.state==='running').length;
          const label=row.querySelector('.group-activity-label');
          if (label) { label.hidden=!count; label.textContent=english?`${count} working`:`${count} 位伙伴处理中`; }
        }
      }
    } catch (_) { /* Reconnection never replaces the conversation. */ }
    finally { pending=false; }
  }
  window.AgentActivity={refresh};
  setInterval(refresh,4000);
  document.addEventListener('visibilitychange',refresh);
  document.addEventListener('DOMContentLoaded',refresh,{once:true});
})();
