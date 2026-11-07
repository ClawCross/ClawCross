/* A local UI clock: no polling, requests or model calls. */
(() => {
    const escape=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const text=(cn,en)=>window.ClawCrossLocale?.text(cn,en) || (document.documentElement.lang.startsWith('zh')?cn:en);
    function elapsed(start,end=Date.now()) { const seconds=Math.max(0,Math.floor((end-start)/1000));return seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${seconds%60}s`; }
    function time(start=Date.now()) { return `<time class="runtime-elapsed" data-started-at="${Number(start) || Date.now()}">0s</time>`; }
    function start(node,startedAt=Date.now(),label) {
        node.innerHTML=`<div class="run-activity-line" role="status"><span class="run-activity-pulse" aria-hidden="true"></span><span>${escape(label || text('思考中','Thinking'))}</span>${time(startedAt)}</div>`;
        tick();
    }
    function finish(node) {
        node.querySelectorAll('.runtime-elapsed[data-started-at]').forEach(clock=>{clock.dataset.endedAt=Date.now();clock.textContent=elapsed(Number(clock.dataset.startedAt),Number(clock.dataset.endedAt));});
    }
    function tick() { document.querySelectorAll('.runtime-elapsed[data-started-at]:not([data-ended-at])').forEach(clock=>clock.textContent=elapsed(Number(clock.dataset.startedAt))); }
    setInterval(tick,1000);
    window.RunActivity={start,finish,time,elapsed};
})();
