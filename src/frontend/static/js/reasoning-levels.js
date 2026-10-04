/* Shared effort labels. Native mappings always come from the backend. */
(function () {
  const names = ['none','minimal','low','medium','high','xhigh','max'];
  const aliases = {off:'none',disabled:'none',min:'minimal',med:'medium',extra_high:'xhigh','extra-high':'xhigh',maximum:'max',ultra:'max'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function selected(level, native = '') {
    if (Number(level) > 0) return Number(level);
    const name = String(native).toLowerCase();
    return names.indexOf(aliases[name] || name) + 1;
  }
  function options(mapping, level, autoLabel) {
    const en = document.documentElement.lang.startsWith('en');
    const labels = en ? ['Lowest','Very low','Low','Balanced','High','Very high','Highest'] : ['最低','很低','低','均衡','高','很高','最高'];
    return `<option value="0" ${!level?'selected':''}>${escape(autoLabel)}</option>` + labels.map((label,index)=>
      `<option value="${index+1}" ${level===index+1?'selected':''}>${index+1} · ${label} → ${escape(mapping[String(index+1)])}</option>`).join('');
  }
  window.ReasoningLevels = {selected, options};
})();
