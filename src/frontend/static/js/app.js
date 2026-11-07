// === Native App Enhancements ===

// 1. Splash screen dismiss
window.addEventListener('load', () => {
    setTimeout(() => {
        const splash = document.getElementById('app-splash');
        if (splash) {
            splash.classList.add('fade-out');
            setTimeout(() => splash.remove(), 600);
        }
    }, 100);
});

// 2. Prevent pull-to-refresh and overscroll bounce
document.addEventListener('touchmove', function(e) {
    // Allow scrolling inside scrollable containers
    let el = e.target;
    while (el && el !== document.body) {
        const style = window.getComputedStyle(el);
        const canScrollY = (style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight;
        const canScrollX = (style.overflowX === 'auto' || style.overflowX === 'scroll') && el.scrollWidth > el.clientWidth;
        if (canScrollY || canScrollX) {
            return; // Allow scroll inside this element
        }
        el = el.parentElement;
    }
    e.preventDefault();
}, { passive: false });

// 3. Keep text double taps from zooming without swallowing control activation.
let lastTouchEnd = 0;
let lastTouchTarget = null;
document.addEventListener('touchend', function(e) {
    const target = e.target instanceof Element ? e.target : e.target?.parentElement;
    if (!target || target.closest('a,button,input,textarea,select,label,summary,[role="button"]')) {
        lastTouchEnd = 0;
        lastTouchTarget = null;
        return;
    }
    const now = Date.now();
    if (target === lastTouchTarget && now - lastTouchEnd <= 300) {
        e.preventDefault();
    }
    lastTouchEnd = now;
    lastTouchTarget = target;
}, false);

// 4. Prevent pinch zoom
document.addEventListener('gesturestart', function(e) {
    e.preventDefault();
});
document.addEventListener('gesturechange', function(e) {
    e.preventDefault();
});

// 5. Prevent context menu on long press - mobile only (except in chat messages)
const isTouchDevice = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
if (isTouchDevice) {
    document.addEventListener('contextmenu', function(e) {
        const allowed = e.target.closest('.message-agent, .message-user, .markdown-body, textarea, input');
        if (!allowed) {
            e.preventDefault();
        }
    });
}

// 6. Online/Offline detection
function updateOnlineStatus() {
    const banner = document.getElementById('offline-banner');
    if (navigator.onLine) {
        banner.classList.remove('show');
    } else {
        banner.classList.add('show');
    }
}
window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);

// 7. Register Service Worker for PWA caching
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// 8. Installed windows keep same-origin navigation, including Message, in-app.
if (window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches) {
    document.addEventListener('click', function(e) {
        const target = e.target instanceof Element ? e.target : e.target?.parentElement;
        const a = target?.closest('a[href]');
        if (!e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey
            && a && !a.target && !a.hasAttribute('download') && a.origin === location.origin) {
            e.preventDefault();
            location.assign(a.href);
        }
    });
}

// 9. Keep the mobile shell inside the visible viewport as the iOS keyboard pans it.
if (isTouchDevice && window.visualViewport) {
    const viewport = window.visualViewport;
    const mobile = window.matchMedia('(max-width: 768px)');
    const shell = document.getElementById('chat-screen');
    const root = document.documentElement;
    const properties = ['--app-height','--studio-viewport-top','--studio-viewport-left','--studio-viewport-width'];
    let frame = 0;
    let restingHeight = Math.max(window.innerHeight,viewport.height);
    function syncViewport() {
        frame = 0;
        if (!shell || !mobile.matches) {
            properties.forEach(name=>root.style.removeProperty(name));
            shell?.classList.remove('studio-keyboard-open');
            return;
        }
        if (!Number.isFinite(viewport.height) || viewport.height <= 0) return;
        const editing = document.activeElement?.matches('input,textarea,[contenteditable="true"]');
        if (!editing) restingHeight = Math.max(window.innerHeight,viewport.height);
        const keyboardOpen = editing && Math.max(restingHeight,window.innerHeight) - viewport.height > 100;
        shell.classList.toggle('studio-keyboard-open',Boolean(keyboardOpen));
        root.style.setProperty('--app-height',viewport.height+'px');
        root.style.setProperty('--studio-viewport-top',Math.max(0,viewport.offsetTop || 0)+'px');
        root.style.setProperty('--studio-viewport-left',Math.max(0,viewport.offsetLeft || 0)+'px');
        root.style.setProperty('--studio-viewport-width',(viewport.width || window.innerWidth)+'px');
    }
    function scheduleViewport() {
        if (!frame) frame = requestAnimationFrame(syncViewport);
    }
    viewport.addEventListener('resize',scheduleViewport);
    viewport.addEventListener('scroll',scheduleViewport);
    window.addEventListener('resize',scheduleViewport);
    window.addEventListener('orientationchange',()=>{
        restingHeight = Math.max(window.innerHeight,viewport.height);
        scheduleViewport();
    });
    mobile.addEventListener('change',scheduleViewport);
    document.addEventListener('focusin',scheduleViewport);
    document.addEventListener('focusout',scheduleViewport);
    syncViewport();
}

// ============================================================
// 通用可拖动分割线 — 让用户自由调整各侧栏/面板宽度
// ============================================================
(function initDividerResize() {
    // 配置表: { dividerId, leftSelector, rightSelector (optional), minLeft, minRight, direction }
    const dividers = [
        // 编排页：左侧专家池 ↔ 画布
        { id: 'orch-divider-left', leftSel: '.orch-sidebar', rightSel: null, min: 200, max: 600 },
        // 编排页：画布 ↔ 右侧设置面板
        { id: 'orch-divider-right', rightSel: '.orch-right-panel', leftSel: null, min: 180, max: 500 },
        // 主页面：会话侧栏 ↔ 聊天区
        { id: 'session-divider', leftSel: '#session-sidebar', rightSel: null, min: 160, max: 450 },
        // 主页面：聊天区 ↔ OASIS 讨论面板
        { id: 'oasis-divider', rightSel: '#oasis-panel', leftSel: null, min: 280, max: 700 },
    ];

    dividers.forEach(cfg => {
        const divEl = document.getElementById(cfg.id);
        if (!divEl) return;

        let startX = 0, startW = 0, target = null, isRight = false;

        divEl.addEventListener('mousedown', onDown);
        divEl.addEventListener('touchstart', onDown, { passive: false });

        function onDown(e) {
            // 在移动端不启用拖动
            if (window.innerWidth <= 768) return;
            e.preventDefault();

            if (cfg.leftSel) {
                target = divEl.parentElement.querySelector(cfg.leftSel) || document.querySelector(cfg.leftSel);
                isRight = false;
            } else if (cfg.rightSel) {
                target = divEl.parentElement.querySelector(cfg.rightSel) || document.querySelector(cfg.rightSel);
                isRight = true;
            }
            if (!target) return;

            startX = e.type === 'touchstart' ? e.touches[0].clientX : e.clientX;
            startW = target.getBoundingClientRect().width;
            // 拖动时禁用 transition 避免卡顿
            target.style.transition = 'none';
            divEl.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            document.addEventListener('touchmove', onMove, { passive: false });
            document.addEventListener('touchend', onUp);
        }

        function onMove(e) {
            const clientX = e.type === 'touchmove' ? e.touches[0].clientX : e.clientX;
            const dx = clientX - startX;
            let newW = isRight ? startW - dx : startW + dx;
            newW = Math.max(cfg.min, Math.min(cfg.max, newW));
            target.style.width = newW + 'px';
        }

        function onUp() {
            divEl.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            // 恢复 transition
            if (target) target.style.transition = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onUp);
        }
    });

    // ============================================
    // 四宫格内部行/列 divider 拖动逻辑
    // ============================================

    // 列 divider：调整同行内左右两个 cell 的宽度比
    function initColDivider(dividerId) {
        const divEl = document.getElementById(dividerId);
        if (!divEl) return;
        let startX = 0, leftCell = null, rightCell = null, startLeftW = 0, startRightW = 0;

        divEl.addEventListener('mousedown', onDown);
        divEl.addEventListener('touchstart', onDown, { passive: false });

        function onDown(e) {
            if (window.innerWidth <= 768) return;
            e.preventDefault();
            leftCell = divEl.previousElementSibling;
            rightCell = divEl.nextElementSibling;
            if (!leftCell || !rightCell) return;
            startX = e.type === 'touchstart' ? e.touches[0].clientX : e.clientX;
            startLeftW = leftCell.getBoundingClientRect().width;
            startRightW = rightCell.getBoundingClientRect().width;
            leftCell.style.transition = 'none';
            rightCell.style.transition = 'none';
            divEl.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            document.addEventListener('touchmove', onMove, { passive: false });
            document.addEventListener('touchend', onUp);
        }
        function onMove(e) {
            const clientX = e.type === 'touchmove' ? e.touches[0].clientX : e.clientX;
            const dx = clientX - startX;
            const totalW = startLeftW + startRightW;
            let newLeft = Math.max(80, Math.min(totalW - 80, startLeftW + dx));
            let newRight = totalW - newLeft;
            leftCell.style.flex = '0 0 ' + newLeft + 'px';
            rightCell.style.flex = '0 0 ' + newRight + 'px';
        }
        function onUp() {
            divEl.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            if (leftCell) leftCell.style.transition = '';
            if (rightCell) rightCell.style.transition = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onUp);
        }
    }

    // 行 divider：调整上下两行的高度比
    function initRowDivider(dividerId) {
        const divEl = document.getElementById(dividerId);
        if (!divEl) return;
        let startY = 0, topRow = null, bottomRow = null, startTopH = 0, startBottomH = 0;

        divEl.addEventListener('mousedown', onDown);
        divEl.addEventListener('touchstart', onDown, { passive: false });

        function onDown(e) {
            if (window.innerWidth <= 768) return;
            e.preventDefault();
            topRow = divEl.previousElementSibling;
            bottomRow = divEl.nextElementSibling;
            if (!topRow || !bottomRow) return;
            startY = e.type === 'touchstart' ? e.touches[0].clientY : e.clientY;
            startTopH = topRow.getBoundingClientRect().height;
            startBottomH = bottomRow.getBoundingClientRect().height;
            topRow.style.transition = 'none';
            bottomRow.style.transition = 'none';
            divEl.classList.add('dragging');
            document.body.style.cursor = 'row-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            document.addEventListener('touchmove', onMove, { passive: false });
            document.addEventListener('touchend', onUp);
        }
        function onMove(e) {
            const clientY = e.type === 'touchmove' ? e.touches[0].clientY : e.clientY;
            const dy = clientY - startY;
            const totalH = startTopH + startBottomH;
            let newTop = Math.max(60, Math.min(totalH - 60, startTopH + dy));
            let newBottom = totalH - newTop;
            topRow.style.flex = '0 0 ' + newTop + 'px';
            bottomRow.style.flex = '0 0 ' + newBottom + 'px';
        }
        function onUp() {
            divEl.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            if (topRow) topRow.style.transition = '';
            if (bottomRow) bottomRow.style.transition = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onUp);
        }
    }

    // 初始化四宫格内部 divider
    initColDivider('orch-grid-col-divider-top');
    initRowDivider('orch-grid-row-divider');
})();
