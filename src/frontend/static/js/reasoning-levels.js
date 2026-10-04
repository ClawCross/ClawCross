/* Shared effort controls. Native mappings always come from the backend. */
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
  function slider({mapping, level = 0, autoLabel, choices, value, attributes = '', disabled = false, label}) {
    const en = document.documentElement.lang.startsWith('en');
    if (mapping) {
      const labels = en ? ['Lowest','Very low','Low','Balanced','High','Very high','Highest'] : ['最低','很低','低','均衡','高','很高','最高'];
      choices = [{value:'0', label:autoLabel}, ...labels.map((name, index) => ({
        value:String(index + 1), label:`${index + 1} · ${name} → ${mapping[String(index + 1)]}`,
      }))];
      value = String(level);
    }
    const index = Math.max(0, choices.findIndex(choice => String(choice.value) === String(value)));
    const current = choices[index].label;
    return `<span class="reasoning-slider">
      <output data-reasoning-output>${escape(current)}</output>
      <input type="range" min="0" max="${Math.max(1, choices.length - 1)}" step="1" value="${index}"
        data-reasoning-slider data-reasoning-choices="${escape(JSON.stringify(choices))}"
        aria-label="${escape(label || (en ? 'Reasoning effort' : '思考强度'))}" aria-valuetext="${escape(current)}"
        ${disabled ? 'disabled' : ''} ${attributes}>
      ${disabled ? '' : `<span class="reasoning-slider-scale" aria-hidden="true"><span>${escape(mapping ? (en ? 'Auto' : '自动') : choices[0].label)}</span><span>${escape(mapping ? (en ? 'Highest' : '最高') : choices.at(-1).label)}</span></span>`}
    </span>`;
  }
  function choice(input) {
    return JSON.parse(input.dataset.reasoningChoices)[Number(input.value)];
  }
  function value(input) {
    return input.hasAttribute('data-reasoning-slider') ? String(choice(input).value) : input.value;
  }
  document.addEventListener('input', event => {
    const input = event.target;
    if (!input.matches('[data-reasoning-slider]')) return;
    const current = choice(input).label;
    input.closest('.reasoning-slider').querySelector('[data-reasoning-output]').textContent = current;
    input.setAttribute('aria-valuetext', current);
  });
  window.ReasoningLevels = {selected, options, slider, value};
})();
