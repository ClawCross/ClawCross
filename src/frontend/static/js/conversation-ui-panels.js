/* Movable conversation panels. Tool code stays inside an opaque sandbox iframe. */
window.ConversationUiPanels = (() => {
    const panels = new Map();
    const layouts = new Map();
    const occurrences = new Map();
    let activeSession = 'default';
    let topLayer = 1000;
    let centerPanel = null;
    let panelId = 0;
    const layout = document.getElementById('studio-chat-layout');
    const workspace = document.getElementById('studio-ui-workspace');
    const stage = document.getElementById('studio-ui-stage');
    const divider = document.getElementById('studio-ui-divider');
    const split = { desktop: 60, mobile: 50 };
    try { Object.assign(split, JSON.parse(localStorage.getItem('clawcross_studio_ui_split') || '{}')); } catch (_) { /* private mode */ }
    const mobileLayout = () => matchMedia('(max-width:768px)').matches;

    function positionCenter() {
        if (!centerPanel || !stage) return;
        const rect = stage.getBoundingClientRect();
        // Keep the original iframe in place: reparenting reloads its document and loses user state.
        centerPanel.section.hidden = rect.width < 1 || rect.height < 1;
        Object.assign(centerPanel.section.style, { left: `${rect.left}px`, top: `${rect.top}px`,
            width: `${rect.width}px`, height: `${rect.height}px`, zIndex: '20' });
    }
    function setSplit(value, persist = false) {
        if (!layout || !divider) return;
        const vertical = mobileLayout();
        const total = Math.max(1, (vertical ? layout.clientHeight : layout.clientWidth) - 10);
        const min = Math.min(vertical ? 120 : 240, total * .35) / total * 100;
        const max = 100 - Math.min(vertical ? 180 : 300, total * .4) / total * 100;
        const share = Math.max(min, Math.min(max, Number.isFinite(value) ? value : vertical ? 50 : 60));
        split[vertical ? 'mobile' : 'desktop'] = share;
        layout.style.setProperty(vertical ? '--studio-ui-share-mobile' : '--studio-ui-share', `calc(${share}% - ${share / 10}px)`);
        divider.setAttribute('aria-orientation', vertical ? 'horizontal' : 'vertical');
        divider.setAttribute('aria-valuemin', String(Math.ceil(min)));
        divider.setAttribute('aria-valuemax', String(Math.floor(max)));
        divider.setAttribute('aria-valuenow', String(Math.round(share)));
        if (persist) { try { localStorage.setItem('clawcross_studio_ui_split', JSON.stringify(split)); } catch (_) { /* private mode */ } }
        positionCenter();
    }
    function deactivateCenter(record) {
        if (centerPanel !== record) return;
        centerPanel = null;
        workspace.hidden = true; divider.hidden = true;
        stage.removeAttribute('aria-owns');
        layout.classList.remove('has-ui-panel');
    }
    function inline(record) {
        deactivateCenter(record);
        record.location = 'inline'; record.geometry = null;
        record.section.classList.remove('is-docked', 'is-floating');
        for (const name of ['left','top','width','height','z-index']) record.section.style.removeProperty(name);
        if (record.frame) record.frame.style.height = `${record.naturalHeight || 160}px`;
        record.placeholder.hidden = true;
        record.layoutButton.textContent = '放到中间';
        record.layoutButton.setAttribute('aria-label', '放到中间');
        remember(record); updateMenu();
    }
    function dock(record) {
        if (!stage || !layout || !workspace || !divider || record.state !== 'open') return;
        if (centerPanel === record) return;
        if (centerPanel && centerPanel !== record) inline(centerPanel);
        centerPanel = record; record.location = 'center';
        record.section.classList.remove('is-floating'); record.section.classList.add('is-docked');
        record.placeholder.hidden = false;
        record.layoutButton.textContent = '返回对话';
        record.layoutButton.setAttribute('aria-label', '返回对话');
        stage.setAttribute('aria-owns', record.section.id);
        workspace.hidden = false; divider.hidden = false; layout.classList.add('has-ui-panel');
        setSplit(split[mobileLayout() ? 'mobile' : 'desktop']);
        requestAnimationFrame(positionCenter);
        remember(record); updateMenu();
    }
    if (stage && divider && layout) {
        new ResizeObserver(positionCenter).observe(stage);
        divider.addEventListener('pointerdown', event => {
            if (event.button !== 0) return;
            event.preventDefault(); divider.setPointerCapture(event.pointerId); layout.classList.add('is-resizing');
            const move = point => {
                const rect = layout.getBoundingClientRect(), vertical = mobileLayout();
                setSplit(((vertical ? point.clientY - rect.top : point.clientX - rect.left) - 5) /
                    Math.max(1, (vertical ? rect.height : rect.width) - 10) * 100, true);
            };
            const end = () => {
                layout.classList.remove('is-resizing');
                divider.removeEventListener('pointermove', move);
                for (const type of ['pointerup','pointercancel','lostpointercapture']) divider.removeEventListener(type, end);
                if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
            };
            divider.addEventListener('pointermove', move);
            for (const type of ['pointerup','pointercancel','lostpointercapture']) divider.addEventListener(type, end);
        });
        divider.addEventListener('keydown', event => {
            const step = {ArrowLeft:-2,ArrowRight:2,ArrowUp:-2,ArrowDown:2}[event.key];
            if (step === undefined && !['Home','End'].includes(event.key)) return;
            event.preventDefault();
            setSplit(event.key === 'Home' ? 0 : event.key === 'End' ? 100 : split[mobileLayout() ? 'mobile' : 'desktop'] + step, true);
        });
    }

    function fingerprint(panel) {
        let hash = 2166136261;
        for (const char of JSON.stringify(panel)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
        return (hash >>> 0).toString(36);
    }
    function bounds(geometry) {
        const width = Math.max(1, Math.min(Math.max(260, geometry.width), innerWidth - 16));
        const height = Math.max(1, Math.min(Math.max(180, geometry.height), innerHeight - 16));
        return { width, height,
            x: Math.max(8, Math.min(geometry.x, innerWidth - width - 8)),
            y: Math.max(8, Math.min(geometry.y, innerHeight - height - 8)) };
    }
    function remember(record) {
        layouts.set(record.key, { state: record.state, geometry: record.geometry, location: record.location });
    }
    function focus(record) {
        if (record.location === 'center') return;
        if (topLayer > 1900) {
            topLayer = 1000;
            panels.forEach(item => { item.section.style.zIndex = '1000'; });
        }
        record.section.style.zIndex = String(++topLayer);
    }
    function place(record, geometry) {
        record.geometry = bounds(geometry);
        record.section.classList.add('is-floating');
        const { x, y, width, height } = record.geometry;
        Object.assign(record.section.style, { left: `${x}px`, top: `${y}px`, width: `${width}px`, height: `${height}px` });
        remember(record);
    }
    function makeFrame(panel) {
        const frame = document.createElement('iframe');
        frame.title = panel.title;
        frame.setAttribute('sandbox', 'allow-scripts');
        frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.setAttribute('loading', 'lazy');
        const css = panel.css.replace(/<\/style/gi, '<\\/style');
        const js = panel.javascript.replace(/<\/script/gi, '<\\/script');
        const resize = 'const reportSize=()=>parent.postMessage({kind:"clawcross_ui_panel_resize_v1",height:document.body.scrollHeight+4},"*");new ResizeObserver(reportSize).observe(document.body);window.addEventListener("load",reportSize);reportSize();';
        const select = 'document.addEventListener("click",event=>{if(event.isTrusted)parent.postMessage({kind:"clawcross_ui_panel_select_v1"},"*")});';
        frame.srcdoc = '<!doctype html><html><head><meta charset="utf-8">'
            + '<meta name="viewport" content="width=device-width,initial-scale=1">'
            + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; connect-src \'none\'; form-action \'none\'; base-uri \'none\'; frame-src \'none\'">'
            + '<style>*,*::before,*::after{box-sizing:border-box}body{margin:0;padding:16px;font:14px system-ui,sans-serif;color:#17233d;overflow-wrap:anywhere}img,svg,video,canvas{max-width:100%}pre{overflow-x:auto}'
            + css + '</style></head><body>' + panel.html + '<script>' + js + '</script><script>' + resize + select + '</script></body></html>';
        return frame;
    }
    function restore(record) {
        if (!record.frame) {
            record.frame = makeFrame(record.panel);
            record.section.insertBefore(record.frame, record.handle);
        }
        record.state = 'open';
        record.section.hidden = false;
        if (record.location === 'center' || (!record.geometry && stage)) dock(record);
        else if (record.geometry) place(record, record.geometry);
        else record.host.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        focus(record);
        remember(record);
        updateMenu();
        const menu = document.getElementById('studio-more-menu');
        if (menu) menu.open = false;
    }
    function hide(record, state) {
        deactivateCenter(record);
        record.state = state;
        record.section.hidden = true;
        record.placeholder.hidden = true;
        if (state === 'closed') {
            record.frame?.remove(); // Stop the closed panel's scripts and timers.
            record.frame = null;
        }
        remember(record);
        updateMenu();
    }
    function updateMenu() {
        const list = document.getElementById('conversation-ui-panel-list');
        if (!list) return;
        list.replaceChildren();
        if (!panels.size) {
            const empty = document.createElement('div');
            empty.className = 'conversation-ui-panel-empty';
            empty.textContent = '暂无对话面板';
            list.appendChild(empty);
        }
        panels.forEach(record => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'conversation-ui-panel-menu-item';
            const title = document.createElement('span');
            title.textContent = record.panel.title;
            const status = document.createElement('small');
            status.textContent = record.state === 'open' && record.location === 'center' ? '已在中间打开' : { open: '查看', minimized: '已最小化 · 恢复', closed: '已关闭 · 重新打开' }[record.state];
            row.append(title, status);
            row.addEventListener('click', () => restore(record));
            list.appendChild(row);
        });
    }
    function pointerOperation(record, control, resize) {
        control.addEventListener('pointerdown', event => {
            if (record.location === 'center' || event.button !== 0 || (!resize && event.target.closest('button'))) return;
            event.preventDefault();
            const rect = record.section.getBoundingClientRect();
            const start = { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
            const origin = { x: event.clientX, y: event.clientY };
            place(record, start);
            focus(record);
            record.section.classList.add('is-manipulating');
            control.setPointerCapture(event.pointerId);
            const move = point => {
                const dx = point.clientX - origin.x;
                const dy = point.clientY - origin.y;
                place(record, resize ? { ...start, width: start.width + dx, height: start.height + dy }
                    : { ...start, x: start.x + dx, y: start.y + dy });
            };
            const end = () => {
                record.section.classList.remove('is-manipulating');
                control.removeEventListener('pointermove', move);
                control.removeEventListener('pointerup', end);
                control.removeEventListener('pointercancel', end);
                if (control.hasPointerCapture(event.pointerId)) control.releasePointerCapture(event.pointerId);
            };
            control.addEventListener('pointermove', move);
            control.addEventListener('pointerup', end);
            control.addEventListener('pointercancel', end);
        });
        control.addEventListener('keydown', event => {
            if (record.location === 'center') return;
            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
            event.preventDefault();
            const rect = record.section.getBoundingClientRect();
            const geometry = record.geometry || { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
            const dx = event.key === 'ArrowLeft' ? -16 : event.key === 'ArrowRight' ? 16 : 0;
            const dy = event.key === 'ArrowUp' ? -16 : event.key === 'ArrowDown' ? 16 : 0;
            place(record, resize ? { ...geometry, width: geometry.width + dx, height: geometry.height + dy }
                : { ...geometry, x: geometry.x + dx, y: geometry.y + dy });
        });
    }
    function beginSession(session) {
        panels.forEach(record => { remember(record); record.host.remove(); });
        if (centerPanel) deactivateCenter(centerPanel);
        panels.clear();
        occurrences.clear();
        activeSession = session || 'default';
        updateMenu();
    }
    function create(panel, session) {
        if ((session || 'default') !== activeSession) beginSession(session);
        const hash = fingerprint(panel);
        const count = occurrences.get(hash) || 0;
        occurrences.set(hash, count + 1);
        const key = `${activeSession}:${hash}:${count}`;
        const saved = layouts.get(key) || {};
        const host = document.createElement('div');
        host.className = 'conversation-ui-panel-host';
        const section = document.createElement('section');
        section.className = 'conversation-ui-panel';
        section.id = `conversation-ui-${++panelId}`;
        section.setAttribute('aria-label', panel.title);
        const heading = document.createElement('div');
        heading.className = 'conversation-ui-panel-heading';
        heading.tabIndex = 0;
        heading.setAttribute('aria-label', `${panel.title}，拖动标题移动面板`);
        const title = document.createElement('span');
        title.textContent = panel.title;
        heading.appendChild(title);
        const handle = document.createElement('button');
        handle.type = 'button';
        handle.className = 'conversation-ui-panel-resize';
        handle.title = '拖动调整大小';
        handle.setAttribute('aria-label', '调整面板大小');
        handle.textContent = '◢';
        const placeholder = document.createElement('button');placeholder.type='button';placeholder.className='conversation-ui-panel-placeholder';
        placeholder.textContent = `在中间查看 · ${panel.title}`;placeholder.hidden=true;
        const layoutButton = document.createElement('button');layoutButton.type='button';layoutButton.className='panel-layout-button';
        layoutButton.textContent='放到中间';layoutButton.setAttribute('aria-label','放到中间');layoutButton.hidden=!stage;
        const record = { key, panel, host, section, handle, placeholder, layoutButton, state: saved.state || 'open', geometry: saved.geometry, location: saved.location || 'inline', frame: null };
        layoutButton.addEventListener('click',()=>record.location === 'center' ? inline(record) : dock(record));
        placeholder.addEventListener('click',()=>restore(record));
        heading.appendChild(layoutButton);
        for (const [label, icon, action] of [
            ['最小化面板', '−', () => hide(record, 'minimized')],
            ['关闭面板', '×', () => hide(record, 'closed')],
        ]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.title = label;
            button.setAttribute('aria-label', label);
            button.textContent = icon;
            button.addEventListener('click', action);
            heading.appendChild(button);
        }
        section.append(heading, handle);
        if (record.state !== 'closed') {
            record.frame = makeFrame(panel);
            section.insertBefore(record.frame, handle);
        }
        section.hidden = record.state !== 'open';
        host.append(section,placeholder);
        if (record.geometry && record.location !== 'center') place(record, record.geometry);
        section.addEventListener('pointerdown', () => focus(record));
        pointerOperation(record, heading, false);
        pointerOperation(record, handle, true);
        panels.set(key, record);
        if (record.location === 'center' && record.state === 'open') dock(record);
        updateMenu();
        return host;
    }
    window.addEventListener('message', event => {
        if (event.data?.kind === 'clawcross_ui_panel_select_v1') {
            panels.forEach(record => { if (record.frame && event.source === record.frame.contentWindow && !record.geometry) dock(record); });
            return;
        }
        if (!event.data || event.data.kind !== 'clawcross_ui_panel_resize_v1') return;
        const height = Number(event.data.height);
        if (!Number.isFinite(height)) return;
        panels.forEach(record => {
            if (record.frame && event.source === record.frame.contentWindow) {
                record.naturalHeight = Math.max(120, Math.min(480, Math.ceil(height)));
                if (!record.geometry && record.location !== 'center') record.frame.style.height = `${record.naturalHeight}px`;
            }
        });
    });
    window.addEventListener('resize', () => {
        panels.forEach(record => { if (record.geometry && record.location !== 'center') place(record, record.geometry); });
        if (centerPanel) setSplit(split[mobileLayout() ? 'mobile' : 'desktop']);
    });
    window.addEventListener('scroll', positionCenter, true);
    document.addEventListener('toggle', event => {
        if (event.target.id === 'conversation-ui-panel-menu') updateMenu();
    }, true);
    function reset() {
        beginSession(null);
        layouts.clear();
    }
    return { create, beginSession, reset };
})();
