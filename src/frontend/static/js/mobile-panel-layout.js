/* Secondary pages belong to the conversation column, including centered shells. */
(() => {
  const desktop = matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)');
  let frame;
  function update() {
    if (!desktop.matches) return;
    const shell = document.querySelector('.app-container');
    if (!shell) return;
    const bounds = shell.getBoundingClientRect();
    const columns = getComputedStyle(shell).gridTemplateColumns.split(' ').map(parseFloat);
    if (!Number.isFinite(columns[0] + columns[1])) return;
    const root = document.documentElement.style;
    for (const [name, value] of Object.entries({
      left: bounds.left + columns[0] + columns[1], right: Math.max(0, innerWidth - bounds.right),
      top: bounds.top, bottom: Math.max(0, innerHeight - bounds.bottom),
    })) root.setProperty('--conversation-panel-' + name, value + 'px');
  }
  const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); };
  document.addEventListener('DOMContentLoaded', () => {
    const shell = document.querySelector('.app-container');
    if (shell) {
      const observer = new ResizeObserver(schedule);
      observer.observe(shell);
      if (shell.parentElement) observer.observe(shell.parentElement);
    }
    schedule();
  }, {once: true});
  addEventListener('resize', schedule);
  desktop.addEventListener('change', schedule);
})();
