// === Native App Enhancements ===

// 1. Splash screen dismiss
window.addEventListener('load', () => {
    setTimeout(() => {
        const splash = document.getElementById('app-splash');
        if (splash) {
            splash.classList.add('fade-out');
            setTimeout(() => splash.remove(), 600);
        }
    }, 800);
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

// 3. Prevent double-tap zoom
let lastTouchEnd = 0;
document.addEventListener('touchend', function(e) {
    const now = Date.now();
    if (now - lastTouchEnd <= 300) {
        e.preventDefault();
    }
    lastTouchEnd = now;
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

// 8. iOS standalone: handle navigation to stay in-app
if (window.navigator.standalone) {
    document.addEventListener('click', function(e) {
        const a = e.target.closest('a');
        if (a && a.href && !a.target && a.hostname === location.hostname) {
            e.preventDefault();
            location.href = a.href;
        }
    });
}

// 9. Keyboard handling for mobile/PWA - comprehensive solution
if (isTouchDevice && window.visualViewport) {
    const chatMain = document.querySelector('.chat-main');
    const chatContainer = document.querySelector('.chat-container');
    const header = document.querySelector('header');
    const inputArea = document.querySelector('.border-t.p-2');
    
    // PWA Standalone mode detection
    const isPWA = window.matchMedia('(display-mode: standalone)').matches || 
                  window.navigator.standalone === true;
    
    let lastHeight = window.visualViewport.height;
    
    function handleViewportChange() {
        const vh = window.visualViewport.height;
        const windowHeight = window.innerHeight;
        const keyboardHeight = windowHeight - vh;
        
        // Detect if keyboard is open (more than 100px difference)
        const keyboardOpen = keyboardHeight > 100;
        
        if (isPWA || keyboardOpen) {
            // PWA mode or keyboard open: adjust heights
            const availableHeight = vh;
            
            // Update CSS variable for app height
            document.documentElement.style.setProperty('--app-height', availableHeight + 'px');
            
            // Chat main takes full available height
            if (chatMain) {
                chatMain.style.height = availableHeight + 'px';
                chatMain.style.maxHeight = availableHeight + 'px';
            }
            
            // Ensure flex behavior
            if (header) header.style.flexShrink = '0';
            if (inputArea) inputArea.style.flexShrink = '0';
            
            // Chat container gets remaining space via flex
            if (chatContainer) {
                chatContainer.style.flex = '1';
                chatContainer.style.minHeight = '0';
            }
        } else {
            // Normal mode: reset to CSS defaults
            document.documentElement.style.removeProperty('--app-height');
            
            if (chatMain) {
                chatMain.style.height = '';
                chatMain.style.maxHeight = '';
            }
            
            if (header) header.style.flexShrink = '';
            if (inputArea) inputArea.style.flexShrink = '';
            
            if (chatContainer) {
                chatContainer.style.flex = '';
                chatContainer.style.minHeight = '';
            }
        }
        
        lastHeight = vh;
    }
    
    // Debounced resize handler
    let resizeTimeout;
    function debouncedHandleViewportChange() {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(handleViewportChange, 50);
    }
    
    // Initial call
    handleViewportChange();
    
    // Listen for viewport changes
    window.visualViewport.addEventListener('resize', debouncedHandleViewportChange);
    window.visualViewport.addEventListener('scroll', handleViewportChange);
    
    // Also listen for window resize (orientation change)
    window.addEventListener('resize', debouncedHandleViewportChange);
    window.addEventListener('orientationchange', () => {
        setTimeout(handleViewportChange, 100);
    });
}

// Input focus: scroll into view on mobile
const inputEl = document.getElementById('user-input');
if (inputEl && isTouchDevice) {
    inputEl.addEventListener('focus', () => {
        setTimeout(() => {
            // Scroll input into view
            inputEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            // Also trigger viewport check
            if (window.visualViewport) {
                window.dispatchEvent(new Event('resize'));
            }
        }, 100);
    });
    
    // On blur: reset after keyboard closes
    inputEl.addEventListener('blur', () => {
        setTimeout(() => {
            if (window.visualViewport) {
                window.dispatchEvent(new Event('resize'));
            }
        }, 200);
    });
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
    initColDivider('orch-grid-col-divider-bottom');
    initRowDivider('orch-grid-row-divider');
})();
