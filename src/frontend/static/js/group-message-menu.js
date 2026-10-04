/* Shared message actions: mouse context menu, touch hold and keyboard menu key. */
(() => {
  'use strict';
  let menu = null, source = null;
  const bound = new WeakSet();
  function close(restoreFocus = false) {
    if (menu) menu.remove();
    menu = null;
    if (restoreFocus && source?.isConnected) source.focus();
    source = null;
  }
  function open(target, message, onReply, x, y) {
    close();
    source = target;
    menu = document.createElement('div');
    menu.className = 'group-message-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '消息操作');
    const reply = document.createElement('button');
    reply.type = 'button'; reply.setAttribute('role', 'menuitem');
    reply.textContent = '引用回复';
    reply.addEventListener('click', () => { close(); onReply(message); });
    menu.append(reply);
    // A modal dialog lives above the document; keep its menu in the same layer.
    (target.closest('dialog') || document.body).append(menu);
    const box = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(x, innerWidth - box.width - 8)) + 'px';
    menu.style.top = Math.max(8, Math.min(y, innerHeight - box.height - 8)) + 'px';
    reply.focus({preventScroll:true});
  }
  document.addEventListener('pointerdown', event => {
    if (menu && !menu.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => {
    if (!menu) return;
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
    else if (event.key === 'Tab') close();
    else if (['ArrowUp','ArrowDown','Home','End'].includes(event.key)) {
      event.preventDefault(); menu.querySelector('[role="menuitem"]').focus();
    }
  });
  document.addEventListener('scroll', () => close(), true);
  window.addEventListener('resize', () => close());
  window.addEventListener('blur', () => close());

  function bind(root, getMessage, onReply) {
    if (!root || bound.has(root)) return;
    bound.add(root);
    let hold = null, timer = null, suppressClickUntil = 0;
    function cancelHold() { clearTimeout(timer); timer = null; hold = null; }
    function targetOf(event) {
      if (event.target.closest('a,button,input,textarea,audio,video')) return null;
      const target = event.target.closest('[data-group-message-id]');
      return target && root.contains(target) ? target : null;
    }
    function show(target, x, y) {
      const message = getMessage(Number(target.dataset.groupMessageId));
      if (message) open(target, message, onReply, x, y);
    }
    root.addEventListener('contextmenu', event => {
      const target = targetOf(event);
      if (!target) return;
      event.preventDefault(); cancelHold();
      show(target, event.clientX, event.clientY);
    });
    root.addEventListener('keydown', event => {
      if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
      const target = targetOf(event);
      if (!target) return;
      event.preventDefault();
      const box = target.getBoundingClientRect(); show(target, box.left + 16, box.top + 16);
    });
    root.addEventListener('pointerdown', event => {
      cancelHold();
      if (event.pointerType !== 'touch' || event.isPrimary === false) return;
      const target = targetOf(event);
      if (!target) return;
      hold = {id:event.pointerId,x:event.clientX,y:event.clientY,target};
      timer = setTimeout(() => {
        if (!hold || !target.isConnected) return;
        show(target, hold.x, hold.y); suppressClickUntil = performance.now() + 700;
      }, 520);
    }, {passive:true});
    root.addEventListener('pointermove', event => {
      if (hold && event.pointerId === hold.id && Math.hypot(event.clientX - hold.x,event.clientY - hold.y) > 10) cancelHold();
    }, {passive:true});
    root.addEventListener('pointerup', () => {
      if (timer && menu) suppressClickUntil = performance.now() + 350;
      cancelHold();
    }, {passive:true});
    root.addEventListener('pointercancel', cancelHold, {passive:true});
    root.addEventListener('click', event => {
      if (performance.now() < suppressClickUntil) { event.preventDefault(); event.stopPropagation(); }
    }, true);
    root.addEventListener('scroll', cancelHold, true);
  }
  window.GroupMessageMenu = {bind, close};
})();
