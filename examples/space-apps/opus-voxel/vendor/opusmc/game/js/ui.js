'use strict';
// ---------------------------------------------------------------------------
// 2D UI (HUD, inventories, menus) drawn on an overlay canvas in GUI pixels
// ---------------------------------------------------------------------------
const Font = {
  img: null, widths: new Uint8Array(256), tinted: new Map(),
  init(img) {
    this.img = img;
    const c = document.createElement('canvas'); c.width = 128; c.height = 128;
    const x = c.getContext('2d'); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, 128, 128).data;
    for (let ch = 0; ch < 256; ch++) {
      const cx = (ch % 16) * 8, cy = Math.floor(ch / 16) * 8;
      let w = 0;
      for (let col = 7; col >= 0; col--) {
        let any = false;
        for (let row = 0; row < 8; row++) if (d[((cy + row) * 128 + cx + col) * 4 + 3] > 0) { any = true; break; }
        if (any) { w = col + 1; break; }
      }
      this.widths[ch] = ch === 32 ? 4 : w + 1;
    }
  },
  sheet(color) {
    let s = this.tinted.get(color);
    if (s) return s;
    s = document.createElement('canvas'); s.width = 128; s.height = 128;
    const x = s.getContext('2d');
    x.drawImage(this.img, 0, 0);
    x.globalCompositeOperation = 'source-in';
    x.fillStyle = color; x.fillRect(0, 0, 128, 128);
    this.tinted.set(color, s);
    return s;
  },
  charW(c) {
    const code = c.charCodeAt(0);
    if (code < 128) return this.widths[code];
    return 9;
  },
  width(str) { let w = 0; for (const c of String(str)) w += this.charW(c); return w; },
  drawRaw(ctx, str, x, y, color) {
    const sh = this.sheet(color);
    for (const c of String(str)) {
      const code = c.charCodeAt(0);
      if (code < 128) {
        ctx.drawImage(sh, (code % 16) * 8, Math.floor(code / 16) * 8, 8, 8, x, y, 8, 8);
        x += this.widths[code];
      } else {
        ctx.fillStyle = color;
        ctx.font = '8.5px "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
        ctx.textBaseline = 'top';
        ctx.fillText(c, x, y - 1.2);
        x += 9;
      }
    }
    return x;
  },
  draw(ctx, str, x, y, color = '#FFFFFF', shadow = true) {
    if (shadow) this.drawRaw(ctx, str, x + 1, y + 1, shade(color));
    return this.drawRaw(ctx, str, x, y, color);
  },
  center(ctx, str, cx, y, color, shadow = true) { this.draw(ctx, str, Math.round(cx - this.width(str) / 2), y, color, shadow); },
};
function shade(c) {
  const n = parseInt(c.slice(1), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return '#' + [r >> 2, g >> 2, b >> 2].map(v => v.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------------------
class UI {
  constructor(game, canvas) {
    this.game = game; this.canvas = canvas; this.ctx = canvas.getContext('2d');
    this.scale = 2; this.w = 320; this.h = 240;
    this.mouse = { x: 0, y: 0, down: false, button: -1 };
    this.screen = null; this.cursor = null;
    this.chat = []; this.toasts = [];
    this.itemNameT = 0; this.lastSel = -1;
    this.debug = false; this.hideHud = false;
    this.input = document.getElementById('textinput');
    this.drag = null;
  }
  get img() { return this.game.renderer.images; }
  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.floor(window.innerWidth * dpr); this.canvas.height = Math.floor(window.innerHeight * dpr);
    const auto = Math.max(1, Math.min(Math.floor(this.canvas.width / 320), Math.floor(this.canvas.height / 240)));
    const pref = this.game.settings.guiScale;
    this.scale = pref ? Math.min(pref * dpr, auto) : Math.min(auto, Math.round(3 * dpr));
    this.scale = Math.max(1, Math.floor(this.scale));
    this.w = Math.ceil(this.canvas.width / this.scale); this.h = Math.ceil(this.canvas.height / this.scale);
    this.dpr = dpr;
    if (this.screen && this.screen.layout) this.screen.layout();
  }
  toGui(ev) { const r = this.canvas.getBoundingClientRect(); return [(ev.clientX - r.left) * this.dpr / this.scale, (ev.clientY - r.top) * this.dpr / this.scale]; }

  open(screen) {
    if (this.screen && this.screen.close) this.screen.close();
    this.screen = screen;
    this.hideInput();
    if (screen) { if (screen.layout) screen.layout(); this.game.releasePointer(); }
  }
  close() {
    if (this.screen && this.screen.close) this.screen.close();
    this.screen = null;
    if (this.cursor) { this.returnStack(this.cursor); this.cursor = null; }
    this.hideInput();
    this.game.capturePointer();
  }
  returnStack(s) {
    const left = this.game.player.inv.add(s);
    if (left) this.game.dropFromPlayer({ ...s, count: left });
  }

  message(text, color = '#FFFFFF') { this.chat.push({ text, color, t: performance.now() }); if (this.chat.length > 100) this.chat.shift(); }
  toast(title, sub, iconId) { this.toasts.push({ title, sub, icon: iconId, t: performance.now() }); Sound.toast(); }

  // ---- primitives ---------------------------------------------------------------
  sprite(name, x, y, w, h, sx = 0, sy = 0, sw, sh) {
    const im = this.img[name];
    this.ctx.drawImage(im, sx, sy, sw ?? (w ?? im.width), sh ?? (h ?? im.height), x, y, w ?? im.width, h ?? im.height);
  }
  nine(name, x, y, w, h, border) {
    const im = this.img[name], c = this.ctx, iw = im.width, ih = im.height, b = border;
    c.drawImage(im, 0, 0, b, b, x, y, b, b); c.drawImage(im, iw - b, 0, b, b, x + w - b, y, b, b);
    c.drawImage(im, 0, ih - b, b, b, x, y + h - b, b, b); c.drawImage(im, iw - b, ih - b, b, b, x + w - b, y + h - b, b, b);
    c.drawImage(im, b, 0, iw - 2 * b, b, x + b, y, w - 2 * b, b); c.drawImage(im, b, ih - b, iw - 2 * b, b, x + b, y + h - b, w - 2 * b, b);
    c.drawImage(im, 0, b, b, ih - 2 * b, x, y + b, b, h - 2 * b); c.drawImage(im, iw - b, b, b, ih - 2 * b, x + w - b, y + b, b, h - 2 * b);
    c.drawImage(im, b, b, iw - 2 * b, ih - 2 * b, x + b, y + b, w - 2 * b, h - 2 * b);
  }
  drawItem(stack, x, y, opts = {}) {
    if (!stack) return;
    const r = this.game.renderer, S = r.iconSize, cols = r.iconCols, c = this.ctx;
    const id = stack.id;
    c.imageSmoothingEnabled = !!(ITEMS[id] && ITEMS[id].block && !ITEMS[id].flat && BLOCKS[id] && BLOCKS[id].shape !== SH_CROSS);
    c.imageSmoothingQuality = 'high';
    c.drawImage(r.iconAtlas, (id % cols) * S, Math.floor(id / cols) * S, S, S, x, y, 16, 16);
    c.imageSmoothingEnabled = false;
    const def = ITEMS[id];
    if (def && def.durability && stack.dmg > 0) {
      const f = 1 - stack.dmg / def.durability;
      c.fillStyle = '#000'; c.fillRect(x + 2, y + 13, 13, 2);
      c.fillStyle = `hsl(${Math.round(f * 120)},100%,50%)`; c.fillRect(x + 2, y + 13, Math.round(13 * f), 1);
    }
    if (stack.count > 1 || opts.showCount) {
      const t = String(stack.count);
      Font.draw(c, t, x + 17 - Font.width(t), y + 9, '#FFFFFF');
    }
  }
  tooltip(lines, x, y) {
    const c = this.ctx;
    let w = 0; for (const l of lines) w = Math.max(w, Font.width(l.text));
    const h = lines.length * 10 + (lines.length > 1 ? 2 : 0) - 2;
    let tx = x + 12, ty = y - 12;
    if (tx + w + 4 > this.w) tx = x - w - 16;
    if (ty + h + 6 > this.h) ty = this.h - h - 6;
    c.fillStyle = 'rgba(16,0,16,0.94)';
    c.fillRect(tx - 3, ty - 4, w + 6, h + 8); c.fillRect(tx - 4, ty - 3, w + 8, h + 6);
    c.fillStyle = 'rgba(80,0,255,0.32)'; c.fillRect(tx - 3, ty - 3, w + 6, 1); c.fillRect(tx - 3, ty + h + 2, w + 6, 1);
    c.fillRect(tx - 3, ty - 3, 1, h + 6); c.fillRect(tx + w + 2, ty - 3, 1, h + 6);
    let yy = ty;
    lines.forEach((l, i) => { Font.draw(c, l.text, tx, yy, l.color || '#FFFFFF'); yy += i === 0 ? 12 : 10; });
  }
  itemLines(stack) {
    const d = ITEMS[stack.id];
    const lines = [{ text: d.cn, color: d.key.includes('diamond') || d.key === 'golden_apple' ? '#55FFFF' : '#FFFFFF' }];
    if (d.food) lines.push({ text: `食物: +${d.food[0]} 饥饿值`, color: '#AAAAAA' });
    if (d.tool) lines.push({ text: `攻击伤害: ${d.attack}`, color: '#00AA00' });
    else if (d.attack) lines.push({ text: `攻击伤害: ${d.attack}`, color: '#00AA00' });
    if (d.armor) lines.push({ text: `护甲值: +${d.armor.points}`, color: '#5555FF' });
    if (d.durability) lines.push({ text: `耐久度: ${d.durability - (stack.dmg || 0)} / ${d.durability}`, color: '#AAAAAA' });
    if (fuelValue(stack.id)) lines.push({ text: `燃料: ${(fuelValue(stack.id) / 200).toFixed(1)} 个物品`, color: '#777777' });
    if (this.game.settings.advTooltips) lines.push({ text: 'minecraft:' + d.key, color: '#555555' });
    return lines;
  }
  button(b) {
    const hover = this.hit(b) && b.enabled !== false;
    this.nine(b.enabled === false ? 'button_dis' : hover ? 'button_hl' : 'button', b.x, b.y, b.w, b.h, 3);
    Font.center(this.ctx, typeof b.label === 'function' ? b.label() : b.label, b.x + b.w / 2, b.y + (b.h - 8) / 2, b.enabled === false ? '#A0A0A0' : hover ? '#FFFFA0' : '#FFFFFF');
  }
  slider(s) {
    this.nine('slider', s.x, s.y, s.w, s.h, 2);
    const hx = s.x + Math.round(s.value * (s.w - 8));
    this.nine(this.hit(s) ? 'slider_handle_hl' : 'slider_handle', hx, s.y, 8, s.h, 2);
    Font.center(this.ctx, s.label(), s.x + s.w / 2, s.y + (s.h - 8) / 2, '#FFFFFF');
  }
  hit(r, mx = this.mouse.x, my = this.mouse.y) { return mx >= r.x && my >= r.y && mx < r.x + r.w && my < r.y + r.h; }
  dim(a = 0.55) { this.ctx.fillStyle = `rgba(16,16,16,${a})`; this.ctx.fillRect(0, 0, this.w, this.h); }
  showInput(x, y, w, h, value, onEnter, opts = {}) {
    const el = this.input, s = this.scale / this.dpr;
    el.style.display = 'block';
    el.style.left = (x * s) + 'px'; el.style.top = (y * s) + 'px'; el.style.width = (w * s) + 'px'; el.style.height = (h * s) + 'px';
    el.style.fontSize = Math.max(12, (h - 6) * s) + 'px';
    el.value = value || ''; el.maxLength = opts.max || 100;
    el.onkeydown = (e) => { e.stopPropagation(); if (e.key === 'Enter') onEnter(el.value); if (e.key === 'Escape') { if (opts.onEsc) opts.onEsc(); } };
    el.oninput = () => { if (opts.onInput) opts.onInput(el.value); };
    setTimeout(() => el.focus(), 0);
  }
  hideInput() { this.input.style.display = 'none'; this.input.onkeydown = null; this.input.blur(); }

  // ---- HUD ---------------------------------------------------------------------------
  drawHUD() {
    const g = this.game, p = g.player, c = this.ctx, W = this.w, H = this.h;
    if (this.hideHud) return;
    // crosshair (inverted)
    if (!this.screen && g.perspective === 0) {
      c.globalCompositeOperation = 'difference';
      this.sprite('crosshair', Math.floor(W / 2 - 7.5), Math.floor(H / 2 - 7.5));
      c.globalCompositeOperation = 'source-over';
    }
    const hx = Math.floor(W / 2 - 91), hy = H - 22;
    this.sprite('hotbar', hx, hy);
    this.sprite('hotbar_sel', hx - 1 + p.inv.sel * 20, hy - 1);
    for (let i = 0; i < 9; i++) this.drawItem(p.inv.slots[i], hx + 3 + i * 20, hy + 3);
    if (!p.creative) {
      // xp bar
      const xb = H - 29;
      this.sprite('xp_bg', hx, xb);
      const lvl = g.xpLevel(), need = g.xpNeeded(lvl), prog = (p.xp - g.xpTotalFor(lvl)) / need;
      const pw = Math.floor(182 * clamp(prog, 0, 1));
      if (pw > 0) c.drawImage(this.img.xp_fg, 0, 0, pw, 5, hx, xb, pw, 5);
      if (lvl > 0) { const t = String(lvl), tx = Math.floor(W / 2 - Font.width(t) / 2); for (const [ox, oy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) Font.drawRaw(c, t, tx + ox, xb - 6 + oy, '#000000'); Font.drawRaw(c, t, tx, xb - 6, '#80FF20'); }
      // hearts
      const hy2 = H - 39;
      const blink = p.invuln > 10 && (p.invuln / 3 | 0) % 2 === 1;
      const low = p.health <= 4;
      for (let i = 0; i < 10; i++) {
        const x = hx + i * 8;
        let y = hy2;
        if (low) y += Math.round(Math.sin(g.tickCount * 1.7 + i * 3.1) * 1);
        this.sprite(blink ? 'heart_container_blink' : 'heart_container', x, y);
        const hpv = p.health - i * 2;
        if (hpv >= 2) this.sprite(blink ? 'heart_full_blink' : 'heart_full', x, y);
        else if (hpv >= 1) this.sprite(blink ? 'heart_half_blink' : 'heart_half', x, y);
      }
      // armor
      const ap = p.inv.armorPoints();
      if (ap > 0) for (let i = 0; i < 10; i++) {
        const v = ap - i * 2;
        this.sprite(v >= 2 ? 'armor_full' : v >= 1 ? 'armor_half' : 'armor_empty', hx + i * 8, hy2 - 10);
      }
      // food
      for (let i = 0; i < 10; i++) {
        const x = hx + 182 - 9 - i * 8;
        let y = hy2;
        if (p.saturation <= 0 && g.tickCount % (p.food * 3 + 1) === 0) y += Math.round(Math.random() * 2 - 1);
        this.sprite('food_empty', x, y);
        const fv = p.food - i * 2;
        if (fv >= 2) this.sprite('food_full', x, y); else if (fv >= 1) this.sprite('food_half', x, y);
      }
      // air
      if (p.eyeInWater || p.air < 300) {
        const bubbles = Math.ceil((p.air - 2) * 10 / 300), full = Math.ceil(p.air * 10 / 300) - bubbles;
        for (let i = 0; i < 10; i++) {
          const x = hx + 182 - 9 - i * 8;
          if (i < bubbles) this.sprite('air', x, hy2 - 10);
          else if (i < bubbles + full && p.air > 0) this.sprite('air_bursting', x, hy2 - 10);
        }
      }
    }
    // held item name
    if (p.inv.sel !== this.lastSel) { this.lastSel = p.inv.sel; this.itemNameT = performance.now(); }
    const held = p.inv.held;
    const dt = performance.now() - this.itemNameT;
    if (held && dt < 2500) {
      const a = dt > 2000 ? 1 - (dt - 2000) / 500 : 1;
      c.globalAlpha = a;
      Font.center(c, ITEMS[held.id].cn, W / 2, H - (p.creative ? 38 : 58), '#FFFFFF');
      c.globalAlpha = 1;
    }
    // action bar message
    if (this.actionBar && performance.now() - this.actionBar.t < 2500) Font.center(c, this.actionBar.text, W / 2, H - (p.creative ? 50 : 70), this.actionBar.color || '#FFFFFF');
    this.drawChat();
    this.drawToasts();
    if (this.debug) this.drawDebug();
    if (g.sleepFade > 0) { c.fillStyle = `rgba(10,10,20,${g.sleepFade})`; c.fillRect(0, 0, W, H); }
    if (!this.screen && g.state === 'play' && document.pointerLockElement !== g.canvas && !g.debugPause) {
      c.fillStyle = 'rgba(0,0,0,0.45)'; c.fillRect(0, H / 2 - 24, W, 20);
      Font.center(c, '点击屏幕继续游戏', W / 2, H / 2 - 18, '#FFFFFF');
    }
  }
  drawChat() {
    const c = this.ctx, now = performance.now(), open = this.screen && this.screen.isChat;
    let y = this.h - 48;
    const list = open ? this.chat.slice(-20) : this.chat.slice(-10);
    for (let i = list.length - 1; i >= 0; i--) {
      const m = list[i], age = now - m.t;
      if (!open && age > 10000) continue;
      const a = open ? 1 : clamp((10000 - age) / 1000, 0, 1);
      c.globalAlpha = a * 0.5; c.fillStyle = '#000'; c.fillRect(2, y - 1, 320, 10); c.globalAlpha = a;
      Font.draw(c, m.text, 4, y, m.color);
      c.globalAlpha = 1;
      y -= 10;
    }
  }
  drawToasts() {
    const c = this.ctx, now = performance.now();
    const g = this.game;
    if (g.state === 'play' && g.tutorial >= 0 && g.mode === 'survival' && !this.toasts.length && !this.debug) {
      const st = TUTORIAL[g.tutorial], x = this.w - 160;
      this.sprite('toast', x, 0);
      this.drawItem({ id: item(st.icon), count: 1 }, x + 8, 8);
      Font.draw(c, st.title, x + 30, 7, '#55FFFF', false);
      Font.draw(c, st.text, x + 30, 18, '#FFFFFF', false);
    }
    this.toasts = this.toasts.filter(t => now - t.t < 5500);
    let y = 0;
    for (const t of this.toasts) {
      const age = now - t.t;
      const slide = age < 400 ? 1 - age / 400 : age > 5000 ? (age - 5000) / 500 : 0;
      const x = this.w - 160 + Math.round(slide * 160);
      this.sprite('toast', x, y);
      if (t.icon) this.drawItem({ id: t.icon, count: 1 }, x + 8, y + 8);
      Font.draw(c, t.title, x + 30, y + 7, '#FFFF00', false);
      Font.draw(c, t.sub, x + 30, y + 18, '#FFFFFF', false);
      y += 32;
    }
  }
  drawDebug() {
    const g = this.game, p = g.player, c = this.ctx;
    const x = Math.floor(p.pos[0]), y = Math.floor(p.pos[1]), z = Math.floor(p.pos[2]);
    const dirs = ['南 (+Z)', '西 (-X)', '北 (-Z)', '东 (+X)'];
    let yaw = ((-p.yaw * 180 / Math.PI) % 360 + 540) % 360 - 180;
    const facing = dirs[Math.round(((yaw + 180) % 360) / 90) % 4];
    const ch = g.world.getChunk(x >> 4, z >> 4);
    const bi = ch ? BIOMES[ch.biome[((z & 15) << 4) | (x & 15)]] : '?';
    const l = g.world.getLight(x, y, z);
    const t = g.dayTime();
    const L = [
      `Minecraft JS 复刻版 (26.2 官方材质)`,
      `${g.fps} fps  区段: ${g.renderer.stats ? g.renderer.stats.sections : 0}  三角形: ${g.renderer.stats ? g.renderer.stats.tris : 0}`,
      `区块: 已加载 ${g.world.chunks.size}  待生成 ${g.genQueueLen || 0}  实体: ${g.entities.length}  粒子: ${g.particles.list.length}`,
      ``,
      `XYZ: ${p.pos[0].toFixed(3)} / ${p.pos[1].toFixed(3)} / ${p.pos[2].toFixed(3)}`,
      `方块: ${x} ${y} ${z}   区块: ${x >> 4} ${z >> 4}`,
      `朝向: ${facing} (${yaw.toFixed(1)} / ${(p.pitch * 180 / Math.PI).toFixed(1)})`,
      `生物群系: ${bi}`,
      `光照: 天空 ${l >> 4}  方块 ${l & 15}`,
      `时间: 第 ${Math.floor(g.worldTime / 24000) + 1} 天  ${String(Math.floor((t / 1000 + 6) % 24)).padStart(2, '0')}:${String(Math.floor((t % 1000) * 0.06)).padStart(2, '0')}`,
      `种子: ${g.world.seed}`,
    ];
    if (g.target) { const b = BLOCKS[g.world.getBlock(...g.target.pos)]; L.push(`目标方块: minecraft:${b ? b.key : '?'} (${g.target.pos.join(', ')})`); }
    let yy = 2;
    for (const s of L) { if (s) { const w = Font.width(s); c.fillStyle = 'rgba(80,80,80,0.55)'; c.fillRect(1, yy - 1, w + 2, 10); Font.draw(c, s, 2, yy, '#E0E0E0', false); } yy += 10; }
  }

  // ---- frame ------------------------------------------------------------------------------
  draw() {
    const c = this.ctx;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    c.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    c.imageSmoothingEnabled = false;
    const g = this.game;
    if (g.state === 'play') {
      this.drawOverlays();
      this.drawHUD();
    }
    if (this.screen) this.screen.draw(this);
    if (this.cursor) this.drawItem(this.cursor, this.mouse.x - 8, this.mouse.y - 8);
  }
  drawOverlays() {
    const g = this.game, p = g.player, c = this.ctx, W = this.w, H = this.h;
    // vignette
    c.globalAlpha = 0.35; c.globalCompositeOperation = 'multiply';
    c.imageSmoothingEnabled = true;
    c.drawImage(this.img.vignette, 0, 0, W, H);
    c.imageSmoothingEnabled = false;
    c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
    if (g.headInWater) {
      c.globalAlpha = 0.25; c.imageSmoothingEnabled = true;
      const ox = (p.yaw * 64) % 64, oy = (p.pitch * 64) % 64;
      for (let x = -64; x < W + 64; x += 64) for (let y = -64; y < H + 64; y += 64) c.drawImage(this.img.underwater, x + ox, y + oy, 64, 64);
      c.globalAlpha = 1; c.imageSmoothingEnabled = false;
    }
    if (g.headInLava) { c.fillStyle = 'rgba(200,60,0,0.75)'; c.fillRect(0, 0, W, H); }
    if (p.fire > 0 && !p.creative && g.perspective === 0) {
      const r = g.renderer, layer = T('fire_0') + (g.tickCount % 32);
      c.globalAlpha = 0.85;
      const s = Math.min(W, H) * 0.45;
      c.drawImage(r.stripCanvas, 0, layer * 16, 16, 16, W / 2 - s * 1.1, H - s * 0.75, s, s);
      c.drawImage(r.stripCanvas, 0, layer * 16, 16, 16, W / 2 + s * 0.1, H - s * 0.75, s, s);
      c.globalAlpha = 1;
    }
    if (p.hurtTime > 0) { c.fillStyle = `rgba(160,0,0,${p.hurtTime / 10 * 0.25})`; c.fillRect(0, 0, W, H); }
    if (p.deathTime > 0) { const a = Math.min(0.6, p.deathTime / 40); const gr = c.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, `rgba(80,0,0,${a})`); gr.addColorStop(1, `rgba(160,20,20,${a})`); c.fillStyle = gr; c.fillRect(0, 0, W, H); }
  }

  // ---- input routing -----------------------------------------------------------------------
  mouseMove(ev) {
    [this.mouse.x, this.mouse.y] = this.toGui(ev);
    if (this.screen && this.screen.mouseMove) this.screen.mouseMove(this, this.mouse.x, this.mouse.y);
  }
  mouseDown(ev) {
    [this.mouse.x, this.mouse.y] = this.toGui(ev);
    this.mouse.down = true; this.mouse.button = ev.button;
    if (this.screen && this.screen.mouseDown) this.screen.mouseDown(this, this.mouse.x, this.mouse.y, ev.button, ev.shiftKey);
  }
  mouseUp(ev) {
    [this.mouse.x, this.mouse.y] = this.toGui(ev);
    this.mouse.down = false;
    if (this.screen && this.screen.mouseUp) this.screen.mouseUp(this, this.mouse.x, this.mouse.y, ev.button);
  }
  wheel(dy) { if (this.screen && this.screen.wheel) this.screen.wheel(this, dy); }
  key(ev) { if (this.screen && this.screen.key) return this.screen.key(this, ev); return false; }
}

// ---------------------------------------------------------------------------
// Container screen base (slot handling, MC click semantics)
// ---------------------------------------------------------------------------
class ContainerScreen {
  constructor(game, bg, w, h, title) { this.game = game; this.bg = bg; this.gw = w; this.gh = h; this.title = title; this.slots = []; this.dragSlots = null; }
  layout() { const u = this.game.ui; this.x = Math.floor((u.w - this.gw) / 2); this.y = Math.floor((u.h - this.gh) / 2); }
  addPlayerSlots(y0, hotY) {
    const inv = this.game.player.inv;
    for (let r = 0; r < 3; r++) for (let c = 0; c < 9; c++) {
      const i = 9 + r * 9 + c;
      this.slots.push({ x: 8 + c * 18, y: y0 + r * 18, get: () => inv.slots[i], set: (s) => { inv.slots[i] = s; }, group: 'main', idx: i });
    }
    for (let c = 0; c < 9; c++) this.slots.push({ x: 8 + c * 18, y: hotY, get: () => inv.slots[c], set: (s) => { inv.slots[c] = s; }, group: 'hotbar', idx: c });
  }
  slotAt(mx, my) {
    for (const s of this.slots) if (mx >= this.x + s.x - 1 && mx < this.x + s.x + 17 && my >= this.y + s.y - 1 && my < this.y + s.y + 17) return s;
    return null;
  }
  drawBase(ui) {
    ui.dim(0.6);
    ui.ctx.drawImage(ui.img[this.bg], 0, 0, this.gw, this.gh, this.x, this.y, this.gw, this.gh);
    if (this.title) Font.draw(ui.ctx, this.title, this.x + 8, this.y + 6, '#404040', false);
  }
  drawSlots(ui) {
    const hov = this.slotAt(ui.mouse.x, ui.mouse.y);
    for (const s of this.slots) {
      const st = s.get();
      if (!st && s.emptyIcon) { ui.ctx.globalAlpha = 0.6; ui.sprite(s.emptyIcon, this.x + s.x, this.y + s.y); ui.ctx.globalAlpha = 1; }
      ui.drawItem(st, this.x + s.x, this.y + s.y);
      if (s === hov || (this.dragSlots && this.dragSlots.has(s))) { ui.ctx.fillStyle = 'rgba(255,255,255,0.5)'; ui.ctx.fillRect(this.x + s.x, this.y + s.y, 16, 16); }
    }
    if (hov && hov.get() && !ui.cursor) ui.tooltip(ui.itemLines(hov.get()), ui.mouse.x, ui.mouse.y);
  }
  draw(ui) { this.drawBase(ui); this.drawExtra && this.drawExtra(ui); this.drawSlots(ui); }
  canPlace(s, st) { return !s.output && (!s.accept || s.accept(st)); }
  maxIn(s, st) { return Math.min(maxStack(st.id), s.max || 64); }
  mouseDown(ui, mx, my, button, shift) {
    const s = this.slotAt(mx, my);
    if (!s) {
      if (this.outside && this.outside(mx, my) && ui.cursor) {
        if (button === 0) { this.game.dropFromPlayer(ui.cursor); ui.cursor = null; }
        else if (button === 2) { this.game.dropFromPlayer({ ...ui.cursor, count: 1 }); ui.cursor.count--; if (!ui.cursor.count) ui.cursor = null; }
      }
      return;
    }
    Sound.init();
    if (s.output) { this.takeOutput(ui, s, shift); return; }
    if (shift && button === 0) { this.quickMove(s); this.changed(); return; }
    const st = s.get();
    // start drag distribution
    if (ui.cursor && (button === 0 || button === 2) && (!st || (st.id === ui.cursor.id))) {
      this.dragSlots = new Set([s]); this.dragButton = button; this.dragStart = s;
    }
    const now = performance.now();
    if (button === 0 && !ui.cursor && st == null && this.lastClick && now - this.lastClick.t < 300 && this.lastClick.id != null) {
      // double click: gather
      const id = this.lastClick.id; ui.cursor = this.lastClick.stack;
      if (ui.cursor) for (const o of this.slots) { const os = o.get(); if (os && !o.output && os.id === id && ui.cursor.count < maxStack(id)) { const k = Math.min(os.count, maxStack(id) - ui.cursor.count); ui.cursor.count += k; os.count -= k; if (!os.count) o.set(null); } }
      this.lastClick = null; this.changed(); return;
    }
    if (this.dragSlots) return; // resolved on mouseUp
    this.clickSlot(ui, s, button);
    this.lastClick = { t: now, id: ui.cursor ? ui.cursor.id : null, stack: ui.cursor };
  }
  clickSlot(ui, s, button) {
    const st = s.get();
    if (button === 0) {
      if (!ui.cursor) { if (st) { ui.cursor = st; s.set(null); } }
      else if (!st) { if (this.canPlace(s, ui.cursor)) { const k = Math.min(ui.cursor.count, this.maxIn(s, ui.cursor)); s.set({ ...ui.cursor, count: k }); ui.cursor.count -= k; if (!ui.cursor.count) ui.cursor = null; } }
      else if (st.id === ui.cursor.id && !ITEMS[st.id].durability) { const k = Math.min(ui.cursor.count, this.maxIn(s, st) - st.count); st.count += k; ui.cursor.count -= k; if (!ui.cursor.count) ui.cursor = null; }
      else if (this.canPlace(s, ui.cursor)) { s.set(ui.cursor); ui.cursor = st; }
    } else if (button === 2) {
      if (!ui.cursor) { if (st) { const k = Math.ceil(st.count / 2); ui.cursor = { ...st, count: k }; st.count -= k; if (!st.count) s.set(null); } }
      else if (!st) { if (this.canPlace(s, ui.cursor)) { s.set({ ...ui.cursor, count: 1 }); ui.cursor.count--; if (!ui.cursor.count) ui.cursor = null; } }
      else if (st.id === ui.cursor.id && st.count < this.maxIn(s, st)) { st.count++; ui.cursor.count--; if (!ui.cursor.count) ui.cursor = null; }
      else if (this.canPlace(s, ui.cursor)) { s.set(ui.cursor); ui.cursor = st; }
    }
    this.changed();
  }
  mouseMove(ui, mx, my) {
    if (this.dragSlots && ui.mouse.down && ui.cursor) {
      const s = this.slotAt(mx, my);
      if (s && !s.output && (!s.get() || s.get().id === ui.cursor.id) && this.canPlace(s, ui.cursor) && this.dragSlots.size < ui.cursor.count + (this.dragButton === 2 ? 0 : 0)) this.dragSlots.add(s);
    }
  }
  mouseUp(ui, mx, my, button) {
    if (!this.dragSlots) return;
    const set = [...this.dragSlots]; this.dragSlots = null;
    if (set.length === 1) { this.clickSlot(ui, set[0], this.dragButton); return; }
    if (!ui.cursor) return;
    const per = this.dragButton === 2 ? 1 : Math.floor(ui.cursor.count / set.length);
    if (per < 1) return;
    for (const s of set) {
      if (!ui.cursor) break;
      const st = s.get();
      const room = this.maxIn(s, ui.cursor) - (st ? st.count : 0);
      const k = Math.min(per, room, ui.cursor.count);
      if (k <= 0) continue;
      if (st) st.count += k; else s.set({ ...ui.cursor, count: k });
      ui.cursor.count -= k; if (!ui.cursor.count) ui.cursor = null;
    }
    this.changed();
  }
  quickMove(s) {
    const st = s.get(); if (!st) return;
    let targets;
    if (s.group === 'hotbar') targets = this.slots.filter(o => o.group === 'container').length ? this.slots.filter(o => o.group === 'container') : this.slots.filter(o => o.group === 'main');
    else if (s.group === 'main') targets = this.slots.filter(o => o.group === 'container').length ? this.slots.filter(o => o.group === 'container') : this.slots.filter(o => o.group === 'hotbar');
    else targets = this.slots.filter(o => o.group === 'hotbar' || o.group === 'main').sort((a, b) => (a.group === 'hotbar' ? 1 : 0) - (b.group === 'hotbar' ? 1 : 0)).reverse();
    if (ITEMS[st.id].armor && (s.group === 'main' || s.group === 'hotbar')) {
      const a = this.slots.find(o => o.armorSlot === ITEMS[st.id].armor.slot && !o.get());
      if (a) { a.set(st); s.set(null); return; }
    }
    targets = targets.filter(t => this.canPlace(t, st));
    for (const t of targets) { const ts = t.get(); if (ts && ts.id === st.id && !ITEMS[st.id].durability) { const k = Math.min(st.count, this.maxIn(t, st) - ts.count); ts.count += k; st.count -= k; if (!st.count) { s.set(null); return; } } }
    for (const t of targets) { if (!t.get()) { const k = Math.min(st.count, this.maxIn(t, st)); t.set({ ...st, count: k }); st.count -= k; if (!st.count) { s.set(null); return; } } }
  }
  takeOutput(ui, s, shift) {
    const st = s.get(); if (!st) return;
    if (shift) {
      let guard = 0;
      while (s.get() && guard++ < 64) {
        const out = s.get();
        if (this.game.player.inv.add({ ...out }) > 0) break;
        s.onTake(out);
        this.changed();
      }
      return;
    }
    if (!ui.cursor) { ui.cursor = { ...st }; s.onTake(st); }
    else if (ui.cursor.id === st.id && ui.cursor.count + st.count <= maxStack(st.id)) { ui.cursor.count += st.count; s.onTake(st); }
    this.changed();
  }
  key(ui, ev) {
    const k = ev.key;
    if (k >= '1' && k <= '9') {
      const s = this.slotAt(ui.mouse.x, ui.mouse.y);
      if (s && !s.output) {
        const inv = this.game.player.inv, i = +k - 1;
        const a = s.get(), b = inv.slots[i];
        if (!b || this.canPlace(s, b)) { s.set(b); inv.slots[i] = a; this.changed(); }
      }
      return true;
    }
    if (k === 'q' || k === 'Q') {
      const s = this.slotAt(ui.mouse.x, ui.mouse.y);
      if (s && s.get() && !s.output) { const st = s.get(); const n = ev.ctrlKey ? st.count : 1; this.game.dropFromPlayer({ ...st, count: n }); st.count -= n; if (!st.count) s.set(null); this.changed(); }
      return true;
    }
    return false;
  }
  outside(mx, my) { return mx < this.x || my < this.y || mx > this.x + this.gw || my > this.y + this.gh; }
  changed() { }
}

class CraftingScreen extends ContainerScreen {
  constructor(game, size) {
    const big = size === 3;
    super(game, big ? 'crafting_table' : 'inventory', 176, 166, big ? '合成' : null);
    this.size = size; this.grid = new Array(size * size).fill(null);
    const ox = big ? 30 : 98, oy = big ? 17 : 18;
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) {
      const i = r * size + c;
      this.slots.push({ x: ox + c * 18, y: oy + r * 18, get: () => this.grid[i], set: (s) => { this.grid[i] = s; }, group: 'craft' });
    }
    this.result = null;
    this.slots.push({ x: big ? 124 : 154, y: big ? 35 : 28, output: true, get: () => this.result, set: () => { }, onTake: () => this.consume() });
    if (!big) {
      const inv = game.player.inv;
      const icons = ['slot_helmet', 'slot_chestplate', 'slot_leggings', 'slot_boots'];
      for (let i = 0; i < 4; i++) this.slots.push({ x: 8, y: 8 + i * 18, armorSlot: i, emptyIcon: icons[i], get: () => inv.slots[36 + i], set: (s) => { inv.slots[36 + i] = s; }, accept: (st) => ITEMS[st.id].armor && ITEMS[st.id].armor.slot === i, max: 1, group: 'armor' });
    }
    this.addPlayerSlots(84, 142);
  }
  changed() {
    const r = matchRecipe(this.grid, this.size);
    this.result = r ? { id: r.result, count: r.count, dmg: 0 } : null;
  }
  layout() {
    super.layout();
    const u = this.game.ui;
    this.bookFits = u.w >= this.gw + 152;
    this.bookOpen = this.bookFits && this.game.settings.recipeBook !== false;
    if (this.bookOpen) this.x = Math.floor((u.w - this.gw - 149) / 2) + 149;
    this.bookBtn = this.size === 3 ? { x: 5, y: 34, w: 20, h: 18 } : { x: 104, y: 61, w: 20, h: 18 };
    this.page = this.page || 0;
  }
  bookRecipes() {
    const n = this.size * this.size;
    const inv = this.game.player.inv;
    const avail = new Map();
    for (let i = 0; i < 36; i++) { const s = inv.slots[i]; if (s) avail.set(s.id, (avail.get(s.id) || 0) + s.count); }
    for (const s of this.grid) if (s) avail.set(s.id, (avail.get(s.id) || 0) + s.count);
    const list = [];
    for (const r of RECIPES) {
      if (r.type === 'shaped' ? (r.w > this.size || r.h > this.size) : r.ing.length > n) continue;
      const plan = planRecipe(r, avail, this.size);
      if (this.onlyCraftable && !plan) continue;
      list.push({ r, plan });
    }
    list.sort((a, b) => (b.plan ? 1 : 0) - (a.plan ? 1 : 0));
    return list;
  }
  fillRecipe(entry, max) {
    const inv = this.game.player.inv, ui = this.game.ui;
    for (let i = 0; i < this.grid.length; i++) if (this.grid[i]) { ui.returnStack(this.grid[i]); this.grid[i] = null; }
    const avail = new Map();
    for (let i = 0; i < 36; i++) { const s = inv.slots[i]; if (s) avail.set(s.id, (avail.get(s.id) || 0) + s.count); }
    const plan = planRecipe(entry.r, avail, this.size);
    if (!plan) { this.changed(); return; }
    let times = 1;
    if (max) {
      const need = new Map(); for (const c of plan) if (c) need.set(c.id, (need.get(c.id) || 0) + 1);
      times = 64;
      for (const [id, k] of need) times = Math.min(times, Math.floor(avail.get(id) / k), maxStack(id));
      times = Math.max(1, times);
    }
    plan.forEach((c, i) => { if (c) { inv.remove(c.id, times); this.grid[i] = { id: c.id, count: times, dmg: 0 }; } });
    this.changed();
    Sound.click();
  }
  bookLayout() { return { x: this.x - 149, y: this.y, w: 147, h: 166 }; }
  drawBook(ui) {
    const c = ui.ctx, P = this.bookLayout();
    c.fillStyle = '#C6C6C6'; c.fillRect(P.x, P.y, P.w, P.h);
    c.fillStyle = '#FFFFFF'; c.fillRect(P.x, P.y, P.w - 1, 1); c.fillRect(P.x, P.y, 1, P.h - 1);
    c.fillStyle = '#555555'; c.fillRect(P.x + 1, P.y + P.h - 1, P.w - 1, 1); c.fillRect(P.x + P.w - 1, P.y + 1, 1, P.h - 1);
    Font.draw(c, '配方书', P.x + 8, P.y + 7, '#404040', false);
    this.filterBtn = { x: P.x + 82, y: P.y + 4, w: 58, h: 14 };
    ui.nine(ui.hit(this.filterBtn) ? 'button_hl' : 'button', this.filterBtn.x, this.filterBtn.y, this.filterBtn.w, this.filterBtn.h, 3);
    Font.center(c, this.onlyCraftable ? '仅可合成' : '全部配方', this.filterBtn.x + 29, this.filterBtn.y + 3, '#FFFFFF');
    const list = this.bookRecipes();
    const per = 20, pages = Math.max(1, Math.ceil(list.length / per));
    this.page = clamp(this.page, 0, pages - 1);
    this.bookCells = [];
    let hover = null;
    list.slice(this.page * per, this.page * per + per).forEach((e, i) => {
      const x = P.x + 11 + (i % 5) * 25, y = P.y + 24 + Math.floor(i / 5) * 25;
      const cell = { x, y, w: 25, h: 25, e };
      this.bookCells.push(cell);
      c.fillStyle = e.plan ? '#7FA06A' : '#A05050';
      c.fillRect(x + 1, y + 1, 23, 23);
      c.fillStyle = e.plan ? '#5E7E4E' : '#7A3A3A'; c.fillRect(x + 1, y + 23, 23, 1); c.fillRect(x + 23, y + 1, 1, 23);
      ui.drawItem({ id: e.r.result, count: e.r.count }, x + 5, y + 5);
      if (ui.hit(cell)) { c.fillStyle = 'rgba(255,255,255,0.3)'; c.fillRect(x + 1, y + 1, 23, 23); hover = e; }
    });
    this.prevBtn = { x: P.x + 38, y: P.y + 130, w: 12, h: 17 }; this.nextBtn = { x: P.x + 93, y: P.y + 130, w: 12, h: 17 };
    if (pages > 1) {
      Font.center(c, (this.page + 1) + '/' + pages, P.x + 73, P.y + 135, '#404040', false);
      Font.draw(c, '<', this.prevBtn.x + 3, this.prevBtn.y + 5, this.page > 0 ? (ui.hit(this.prevBtn) ? '#FFFFA0' : '#404040') : '#909090', false);
      Font.draw(c, '>', this.nextBtn.x + 3, this.nextBtn.y + 5, this.page < pages - 1 ? (ui.hit(this.nextBtn) ? '#FFFFA0' : '#404040') : '#909090', false);
    }
    Font.draw(c, 'Shift+点击 填充最多', P.x + 8, P.y + 152, '#606060', false);
    if (hover && !ui.cursor) {
      const lines = [{ text: ITEMS[hover.r.result].cn + (hover.r.count > 1 ? ' x' + hover.r.count : '') }];
      const need = new Map();
      const ings = hover.r.type === 'shaped' ? hover.r.pattern.join('').split('').filter(ch => ch !== ' ').map(ch => hover.r.keys[ch]) : hover.r.ing;
      for (const opts of ings) { const k = opts.map(id => ITEMS[id].cn).slice(0, 2).join('/'); need.set(k, (need.get(k) || 0) + 1); }
      for (const [k, n] of need) lines.push({ text: k + ' x' + n, color: '#AAAAAA' });
      if (!hover.plan) lines.push({ text: '材料不足', color: '#FF5555' });
      ui.tooltip(lines, ui.mouse.x, ui.mouse.y);
    }
  }
  consume() {
    const r = this.result;
    for (let i = 0; i < this.grid.length; i++) {
      const s = this.grid[i];
      if (s) {
        if (s.id === item('water_bucket') || s.id === item('lava_bucket')) { this.grid[i] = makeStack('bucket'); continue; }
        s.count--; if (!s.count) this.grid[i] = null;
      }
    }
    if (r) this.game.onCraft(r.id, r.count);
    this.changed();
  }
  draw(ui) {
    this.drawBase(ui);
    if (this.bookOpen) this.drawBook(ui);
    this.drawExtra(ui);
    if (this.bookFits) {
      const b = this.bookBtn;
      ui.sprite(ui.hit({ x: this.x + b.x, y: this.y + b.y, w: b.w, h: b.h }) ? 'rb_button_hl' : 'rb_button', this.x + b.x, this.y + b.y);
    }
    this.drawSlots(ui);
  }
  mouseDown(ui, mx, my, button, shift) {
    const b = this.bookBtn;
    if (this.bookFits && ui.hit({ x: this.x + b.x, y: this.y + b.y, w: b.w, h: b.h }, mx, my)) {
      this.game.settings.recipeBook = !this.bookOpen; this.game.saveSettings(); this.layout(); Sound.click(); return;
    }
    if (this.bookOpen) {
      if (ui.hit(this.filterBtn || { x: 0, y: 0, w: 0, h: 0 }, mx, my)) { this.onlyCraftable = !this.onlyCraftable; this.page = 0; Sound.click(); return; }
      if (ui.hit(this.prevBtn || { x: 0, y: 0, w: 0, h: 0 }, mx, my)) { this.page--; Sound.click(); return; }
      if (ui.hit(this.nextBtn || { x: 0, y: 0, w: 0, h: 0 }, mx, my)) { this.page++; Sound.click(); return; }
      for (const c of this.bookCells || []) if (ui.hit(c, mx, my)) { if (!ui.cursor) this.fillRecipe(c.e, shift); return; }
      if (ui.hit(this.bookLayout(), mx, my)) return;
    }
    super.mouseDown(ui, mx, my, button, shift);
  }
  wheel(ui, dy) { if (this.bookOpen && ui.hit(this.bookLayout())) this.page += Math.sign(dy); }
  outside(mx, my) { return super.outside(mx, my) && !(this.bookOpen && this.game.ui.hit(this.bookLayout(), mx, my)); }
  drawExtra(ui) {
    if (this.size === 2) {
      Font.draw(ui.ctx, '合成', this.x + 97, this.y + 6, '#404040', false);
      // player preview
      const pv = this.game.renderer.playerPreview(this.game, ui, this.x + 26, this.y + 8, 49, 70);
      if (pv) { ui.ctx.imageSmoothingEnabled = true; ui.ctx.drawImage(pv, this.x + 26, this.y + 8, 49, 70); ui.ctx.imageSmoothingEnabled = false; }
    } else Font.draw(ui.ctx, '物品栏', this.x + 8, this.y + 73, '#404040', false);
  }
  close() {
    for (const s of this.grid) if (s) this.game.ui.returnStack(s);
    this.grid.fill(null);
  }
}

class ChestScreen extends ContainerScreen {
  constructor(game, be, title = '箱子') {
    super(game, 'generic_54', 176, 168, title);
    this.be = be;
    for (let r = 0; r < 3; r++) for (let c = 0; c < 9; c++) {
      const i = r * 9 + c;
      this.slots.push({ x: 8 + c * 18, y: 18 + r * 18, get: () => be.slots[i], set: (s) => { be.slots[i] = s; }, group: 'container' });
    }
    this.addPlayerSlots(85, 143);
  }
  drawBase(ui) {
    ui.dim(0.6);
    const c = ui.ctx, im = ui.img.generic_54;
    c.drawImage(im, 0, 0, 176, 71, this.x, this.y, 176, 71);
    c.drawImage(im, 0, 126, 176, 96, this.x, this.y + 71, 176, 96);
    Font.draw(c, this.title, this.x + 8, this.y + 6, '#404040', false);
    Font.draw(c, '物品栏', this.x + 8, this.y + 74, '#404040', false);
  }
  close() { this.game.markBlockEntity(this.be); Sound.door(this.be.pos, false); }
}

class FurnaceScreen extends ContainerScreen {
  constructor(game, be) {
    super(game, 'furnace', 176, 166, '熔炉');
    this.be = be;
    this.slots.push({ x: 56, y: 17, get: () => be.input, set: (s) => { be.input = s; }, group: 'container', accept: (st) => !!SMELTING[st.id] });
    this.slots.push({ x: 56, y: 53, get: () => be.fuel, set: (s) => { be.fuel = s; }, group: 'container', accept: (st) => fuelValue(st.id) > 0 });
    this.slots.push({ x: 116, y: 35, output: true, get: () => be.output, set: (s) => { be.output = s; }, onTake: (st) => { this.game.onSmeltTake(st, be); be.output = null; } });
    this.addPlayerSlots(84, 142);
  }
  canPlace(s, st) { if (s.output) return false; return !s.accept || s.accept(st); }
  quickMove(s) {
    const st = s.get(); if (!st) return;
    if (s.group === 'main' || s.group === 'hotbar') {
      const tgt = SMELTING[st.id] ? this.slots[0] : fuelValue(st.id) ? this.slots[1] : null;
      if (tgt) {
        const ts = tgt.get();
        if (!ts) { tgt.set(st); s.set(null); return; }
        if (ts.id === st.id) { const k = Math.min(st.count, maxStack(st.id) - ts.count); ts.count += k; st.count -= k; if (!st.count) s.set(null); return; }
      }
    }
    super.quickMove(s);
  }
  drawExtra(ui) {
    const be = this.be, c = ui.ctx;
    if (be.burn > 0) {
      const f = Math.ceil(be.burn / be.burnMax * 13) + 1;
      c.drawImage(ui.img.lit_progress, 0, 14 - f, 14, f, this.x + 56, this.y + 36 + 14 - f, 14, f);
    }
    if (be.cook > 0) {
      const w = Math.ceil(be.cook / 200 * 24);
      c.drawImage(ui.img.burn_progress, 0, 0, w, 16, this.x + 79, this.y + 34, w, 16);
    }
    Font.draw(c, '物品栏', this.x + 8, this.y + 73, '#404040', false);
  }
  close() { this.game.markBlockEntity(this.be); }
}

class CreativeScreen extends ContainerScreen {
  constructor(game) {
    super(game, 'tab_items', 195, 136, null);
    this.tab = 0; this.scroll = 0; this.search = '';
    this.items = [];
    this.trash = null;
    const inv = game.player.inv;
    for (let c = 0; c < 9; c++) this.slots.push({ x: 9 + c * 18, y: 112, get: () => inv.slots[c], set: (s) => { inv.slots[c] = s; }, group: 'hotbar' });
    for (let r = 0; r < 5; r++) for (let c = 0; c < 9; c++) {
      const k = r * 9 + c;
      this.slots.push({ x: 9 + c * 18, y: 18 + r * 18, creative: true, get: () => { const id = this.items[this.scroll * 9 + k]; return id != null ? { id, count: 1 } : null; }, set: () => { } });
    }
    this.trashSlot = { x: 173, y: 112, trash: true, get: () => null, set: () => { } };
    this.slots.push(this.trashSlot);
    this.setTab(0);
  }
  tabs() { return [...CREATIVE_TABS, { name: '搜索物品', icon: 'compass_search', search: true }]; }
  setTab(t) {
    this.tab = t; this.scroll = 0;
    const tabs = this.tabs();
    if (tabs[t].search) {
      this.bg = 'tab_item_search';
      const q = this.search.trim().toLowerCase();
      this.items = ITEMS.filter(it => it && !(BLOCKS[it.id] && BLOCKS[it.id].noItem)).filter(it => !q || it.cn.includes(q) || it.key.includes(q)).map(it => it.id);
      const u = this.game.ui;
      if (this.x !== undefined) u.showInput(this.x + 82, this.y + 6, 89, 10, this.search, () => { }, { onInput: (v) => { this.search = v; this.setTab(this.tab); }, onEsc: () => u.close(), max: 30 });
    } else {
      this.bg = 'tab_items';
      this.items = tabs[t].items.map(k => item(k));
      this.game.ui.hideInput();
    }
  }
  layout() { super.layout(); this.y += 14; if (this.tabs()[this.tab].search) this.setTab(this.tab); }
  maxScroll() { return Math.max(0, Math.ceil(this.items.length / 9) - 5); }
  wheel(ui, dy) { this.scroll = clamp(this.scroll + Math.sign(dy), 0, this.maxScroll()); }
  tabRect(i) { return { x: this.x + i * 27, y: this.y - 28, w: 26, h: 30 }; }
  draw(ui) {
    ui.dim(0.6);
    const c = ui.ctx, tabs = this.tabs();
    for (let i = 0; i < tabs.length; i++) if (i !== this.tab) { const r = this.tabRect(i); ui.sprite('tab_top_unselected_' + Math.min(7, i + 1), r.x, r.y); }
    c.drawImage(ui.img[this.bg], 0, 0, 195, 136, this.x, this.y, 195, 136);
    { const r = this.tabRect(this.tab); ui.sprite('tab_top_selected_' + Math.min(7, this.tab + 1), r.x, r.y); }
    for (let i = 0; i < tabs.length; i++) {
      const r = this.tabRect(i);
      const icon = tabs[i].search ? item('book') : item(tabs[i].icon);
      ui.drawItem({ id: icon, count: 1 }, r.x + 5, r.y + 9);
    }
    Font.draw(c, tabs[this.tab].name, this.x + 8, this.y + 6, '#404040', false);
    // scroller
    const ms = this.maxScroll();
    const sy = this.y + 18 + (ms ? Math.round(this.scroll / ms * (112 - 15 - 18)) : 0);
    ui.sprite(ms ? 'scroller' : 'scroller_disabled', this.x + 175, sy);
    // trash slot
    ui.ctx.fillStyle = '#8b8b8b'; ui.ctx.fillRect(this.x + 173, this.y + 112, 16, 16);
    Font.center(c, 'X', this.x + 181, this.y + 116, '#FF5555');
    this.drawSlots(ui);
    for (let i = 0; i < tabs.length; i++) if (ui.hit(this.tabRect(i))) ui.tooltip([{ text: tabs[i].name }], ui.mouse.x, ui.mouse.y);
  }
  drawSlots(ui) {
    const hov = this.slotAt(ui.mouse.x, ui.mouse.y);
    for (const s of this.slots) {
      if (s.trash) continue;
      ui.drawItem(s.get(), this.x + s.x, this.y + s.y);
      if (s === hov) { ui.ctx.fillStyle = 'rgba(255,255,255,0.5)'; ui.ctx.fillRect(this.x + s.x, this.y + s.y, 16, 16); }
    }
    if (hov && hov.get() && !ui.cursor) ui.tooltip(ui.itemLines(hov.get()), ui.mouse.x, ui.mouse.y);
  }
  mouseDown(ui, mx, my, button, shift) {
    const tabs = this.tabs();
    for (let i = 0; i < tabs.length; i++) if (ui.hit(this.tabRect(i), mx, my)) { this.setTab(i); Sound.click(); return; }
    if (mx >= this.x + 175 && mx < this.x + 187 && my >= this.y + 18 && my < this.y + 130) { this.scrolling = true; this.mouseMove(ui, mx, my); return; }
    const s = this.slotAt(mx, my);
    if (s && s.trash) { if (ui.cursor) ui.cursor = null; else if (shift) this.game.player.inv.slots.fill(null, 0, 36); return; }
    if (s && s.creative) {
      const st = s.get();
      if (ui.cursor) { ui.cursor = null; return; }
      if (!st) return;
      if (shift) { const inv = this.game.player.inv; const i = inv.slots.findIndex((x, j) => j < 9 && !x); inv.slots[i >= 0 ? i : inv.sel] = { id: st.id, count: maxStack(st.id), dmg: 0 }; return; }
      ui.cursor = { id: st.id, count: button === 2 ? 1 : maxStack(st.id), dmg: 0 };
      return;
    }
    if (s && button === 1 && s.get()) { ui.cursor = { ...s.get(), count: maxStack(s.get().id) }; return; }
    super.mouseDown(ui, mx, my, button, shift);
  }
  mouseMove(ui, mx, my) {
    if (this.scrolling && ui.mouse.down) { const ms = this.maxScroll(); this.scroll = clamp(Math.round((my - this.y - 25) / (112 - 15 - 18) * ms), 0, ms); return; }
    super.mouseMove(ui, mx, my);
  }
  mouseUp(ui, mx, my, b) { this.scrolling = false; super.mouseUp(ui, mx, my, b); }
  quickMove(s) { if (s.group === 'hotbar') s.set(null); }
  outside(mx, my) { return super.outside(mx, my) && my > this.y - 28; }
  key(ui, ev) { if (this.tabs()[this.tab].search && document.activeElement === ui.input) return true; return super.key(ui, ev); }
}

// ---------------------------------------------------------------------------
// Menu screens (buttons & sliders)
// ---------------------------------------------------------------------------
class MenuScreen {
  constructor(game, title) { this.game = game; this.title = title; this.widgets = []; this.bgDim = 0.5; }
  layout() { }
  draw(ui) {
    if (this.bgDirt) this.drawDirt(ui); else if (this.bgDim) ui.dim(this.bgDim);
    if (this.title) Font.center(ui.ctx, this.title, ui.w / 2, this.titleY ?? 30, '#FFFFFF');
    if (this.drawExtra) this.drawExtra(ui);
    for (const w of this.widgets) { if (w.slider) ui.slider(w); else if (w.text) Font.center(ui.ctx, typeof w.text === 'function' ? w.text() : w.text, w.x, w.y, w.color || '#FFFFFF'); else ui.button(w); }
    for (const w of this.widgets) if (w.tip && ui.hit(w)) ui.tooltip(w.tip.map(t => ({ text: t })), ui.mouse.x, ui.mouse.y);
  }
  drawDirt(ui) {
    const c = ui.ctx, r = this.game.renderer;
    if (!this.dirtPat) {
      const t = document.createElement('canvas'); t.width = 32; t.height = 32;
      const x = t.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(r.stripCanvas, 0, T('dirt') * 16, 16, 16, 0, 0, 32, 32);
      x.fillStyle = 'rgba(0,0,0,0.75)'; x.fillRect(0, 0, 32, 32);
      this.dirtPat = c.createPattern(t, 'repeat');
    }
    c.fillStyle = this.dirtPat; c.fillRect(0, 0, ui.w, ui.h);
  }
  mouseDown(ui, mx, my, button) {
    if (button !== 0) return;
    for (const w of this.widgets) {
      if (w.slider && ui.hit(w, mx, my)) { this.dragging = w; this.mouseMove(ui, mx, my); Sound.click(); return; }
      if (!w.slider && !w.text && w.enabled !== false && ui.hit(w, mx, my)) { Sound.init(); Sound.click(); w.onClick(); return; }
    }
  }
  mouseMove(ui, mx) {
    if (this.dragging && ui.mouse.down) { const w = this.dragging; w.value = clamp((mx - w.x - 4) / (w.w - 8), 0, 1); w.onChange(w.value); }
  }
  mouseUp() { this.dragging = null; }
  key(ui, ev) { if (ev.key === 'Escape' && this.onEsc) { this.onEsc(); return true; } return false; }
}

// Choose concrete ingredient ids for a recipe given available counts; returns per-grid-slot list or null
function planRecipe(r, avail, size) {
  const left = new Map(avail);
  const take = (opts) => {
    let best = -1, bestN = 0;
    for (const id of opts) { const n = left.get(id) || 0; if (n > bestN) { best = id; bestN = n; } }
    if (best < 0) return null;
    left.set(best, bestN - 1);
    return { id: best };
  };
  const out = new Array(size * size).fill(null);
  if (r.type === 'shaped') {
    for (let y = 0; y < r.h; y++) for (let x = 0; x < r.w; x++) {
      const ch = r.pattern[y][x];
      if (ch === ' ' || ch === undefined) continue;
      const t = take(r.keys[ch]); if (!t) return null;
      out[y * size + x] = t;
    }
  } else {
    for (let i = 0; i < r.ing.length; i++) { const t = take(r.ing[i]); if (!t) return null; out[i] = t; }
  }
  return out;
}
