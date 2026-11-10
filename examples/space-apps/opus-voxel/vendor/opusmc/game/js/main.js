'use strict';
// ---------------------------------------------------------------------------
// Input binding & bootstrap
// ---------------------------------------------------------------------------
function bindInput(g) {
  const canvas = g.canvas, ui = g.ui;
  const locked = () => document.pointerLockElement === canvas;
  window.addEventListener('keydown', (e) => {
    if (document.activeElement === ui.input) return;
    Sound.init();
    const code = e.code;
    if (['Space', 'Tab', 'F1', 'F3', 'F5', 'Slash', 'KeyQ'].includes(code) || (e.ctrlKey && ['KeyS', 'KeyD', 'KeyW', 'KeyA'].includes(code))) e.preventDefault();
    if (ui.screen) {
      if (ui.key(e)) return;
      if (code === 'Escape' || (code === 'KeyE' && ui.screen instanceof ContainerScreen)) {
        if (ui.screen.onEsc) ui.screen.onEsc();
        else if (g.state === 'play' && !(ui.screen instanceof DeathScreen)) ui.close();
      }
      return;
    }
    if (g.state !== 'play') return;
    if (e.repeat && !code.startsWith('Digit')) { g.keys[code] = true; return; }
    g.keys[code] = true;
    const p = g.player, now = performance.now();
    if (code === 'Escape') { ui.open(new PauseScreen(g)); return; }
    if (code === 'KeyE') { ui.open(g.mode === 'creative' ? new CreativeScreen(g) : new CraftingScreen(g, 2)); return; }
    if (code === 'KeyT') { e.preventDefault(); ui.open(new ChatScreen(g)); return; }
    if (code === 'Slash') { ui.open(new ChatScreen(g, '/')); return; }
    if (code === 'KeyH') { ui.open(new HelpScreen(g, null)); ui.screen.parent = null; return; }
    if (code.startsWith('Digit')) { const n = +code.slice(5); if (n >= 1 && n <= 9) p.inv.sel = n - 1; }
    if (code === 'KeyQ') {
      const h = p.inv.held;
      if (h) { const n = e.ctrlKey ? h.count : 1; g.dropFromPlayer({ ...h, count: n }); h.count -= n; if (!h.count) p.inv.slots[p.inv.sel] = null; p.swingT = 6; }
    }
    if (code === 'F1') ui.hideHud = !ui.hideHud;
    if (code === 'F3') ui.debug = !ui.debug;
    if (code === 'F5') g.perspective = (g.perspective + 1) % 3;
    if (code === 'Space') {
      if (g.mode === 'creative' && now - (g.lastTap.space || 0) < 300) { p.flying = !p.flying; p.vel[1] = 0; g.lastTap.space = 0; }
      else g.lastTap.space = now;
    }
    if (code === 'KeyW') {
      if (now - (g.lastTap.w || 0) < 280) g.sprintToggle = true;
      g.lastTap.w = now;
    }
  });
  window.addEventListener('keyup', (e) => {
    g.keys[e.code] = false;
    if (e.code === 'KeyW') g.sprintToggle = false;
  });
  window.addEventListener('blur', () => { g.keys = {}; g.mouseBtn = [false, false, false]; g.sprintToggle = false; });
  document.addEventListener('pointerlockchange', () => {
    if (!locked()) { g.mouseBtn = [false, false, false]; g.keys = {}; if (g.state === 'play' && !ui.screen) ui.open(new PauseScreen(g)); }
  });
  window.addEventListener('mousemove', (e) => {
    if (locked() && !ui.screen && g.state === 'play') {
      const p = g.player, s = g.settings.sens * 0.006 + 0.0006;
      if (Math.abs(e.movementX) > 400 || Math.abs(e.movementY) > 400) return; // browser glitch spikes
      p.yaw -= e.movementX * s;
      p.pitch = clamp(p.pitch - e.movementY * s, -Math.PI / 2 + 0.001, Math.PI / 2 - 0.001);
    } else ui.mouseMove(e);
  });
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  ui.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  window.addEventListener('mousedown', (e) => {
    if (e.target === ui.input) return;
    Sound.init();
    if (ui.screen) { ui.mouseDown(e); return; }
    if (g.state !== 'play') return;
    if (!locked()) { g.capturePointer(); return; }
    g.mouseBtn[e.button] = true;
    if (e.button === 0) { g.mouseClicked0 = true; g.attackCd = Math.min(g.attackCd, 0); if (!g.targetEntity && !g.target) g.player.swingT = 6; }
    if (e.button === 2) g.useCd = 0;
    if (e.button === 1) { e.preventDefault(); g.pickBlock(); }
  });
  window.addEventListener('mouseup', (e) => {
    if (ui.screen) { ui.mouseUp(e); }
    g.mouseBtn[e.button] = false;
  });
  window.addEventListener('wheel', (e) => {
    if (ui.screen) { ui.wheel(e.deltaY); return; }
    if (g.state !== 'play') return;
    const p = g.player;
    p.inv.sel = mod(p.inv.sel + Math.sign(e.deltaY), 9);
  }, { passive: true });
  window.addEventListener('beforeunload', (e) => {
    if (g.state === 'play') { g.saveWorld(true); e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden && g.state === 'play') g.saveWorld(true); });
}

// Player preview for the survival inventory
Renderer.prototype.playerPreview = function (game, ui, x, y, w, h) {
  const gl = this.gl, S = Math.max(2, ui.scale), W = w * S, H = h * S;
  if (!this.pv || this.pv.W !== W || this.pv.H !== H) {
    if (this.pv) { gl.deleteFramebuffer(this.pv.fb); gl.deleteTexture(this.pv.tex); gl.deleteRenderbuffer(this.pv.rb); }
    const fb = gl.createFramebuffer(), tex = gl.createTexture(), rb = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, tex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, W, H, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.bindRenderbuffer(gl.RENDERBUFFER, rb); gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, W, H);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb);
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    this.pv = { fb, tex, rb, W, H, cv, ctx: cv.getContext('2d'), px: new Uint8Array(W * H * 4), img: cv.getContext('2d').createImageData(W, H) };
  }
  const pv = this.pv;
  gl.bindFramebuffer(gl.FRAMEBUFFER, pv.fb);
  gl.viewport(0, 0, W, H);
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST); gl.enable(gl.CULL_FACE);
  const vp = M4.create(); M4.ortho(vp, -0.7, 0.7, -0.05, 1.95, -5, 5);
  const mx = (ui.mouse.x - (x + w / 2)) / 40, my = (ui.mouse.y - (y + h * 0.25)) / 40;
  const m = M4.create(); M4.translate(m, 0, 0, 0); M4.rotY(m, Math.PI - Math.atan(mx) * 0.7);
  M4.scale(m, 0.9375, 0.9375, 0.9375);
  const env = { day: 1, fogColor: [0, 0, 0], fogNear: 1e8, fogFar: 2e8, nightTint: [1, 1, 1] };
  const savedCam = this.camPos; this.camPos = [0, 0, 0];
  this.drawModel('steve', 'steve', m, { head: { ry: -Math.atan(mx) * 0.5, rx: Math.atan(my) * 0.6 } }, [1, 0], env, { vp });
  this.camPos = savedCam;
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, pv.px);
  for (let r = 0; r < H; r++) pv.img.data.set(pv.px.subarray((H - 1 - r) * W * 4, (H - r) * W * 4), r * W * 4);
  pv.ctx.putImageData(pv.img, 0, 0);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  return pv.cv;
};

window.addEventListener('load', async () => {
  const g = window.game = new Game();
  try { await g.init(); }
  catch (e) {
    console.error(e);
    const d = document.getElementById('err');
    d.style.display = 'block';
    d.textContent = '启动失败：' + (e && e.message || e) + '\n\n请使用最新版 Chrome / Edge / Firefox 浏览器打开，并确保已启用 WebGL2 / 硬件加速。';
  }
});
