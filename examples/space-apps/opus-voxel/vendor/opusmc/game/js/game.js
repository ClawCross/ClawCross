'use strict';
// ---------------------------------------------------------------------------
// Game: state, main loop, chunk streaming, interaction, rendering glue
// ---------------------------------------------------------------------------
const ADVANCEMENTS = {
  wood: ['获得木头', 'oak_log'], crafting_table: ['这是？工作台！', 'crafting_table'], pickaxe: ['采矿时间到！', 'wooden_pickaxe'],
  stone_pickaxe: ['获得升级', 'stone_pickaxe'], furnace: ['热腾腾的', 'furnace'], iron: ['来硬的', 'iron_ingot'],
  diamond: ['钻石！', 'diamond'], bread: ['烤面包', 'bread'], kill: ['怪物猎人', 'iron_sword'], armor: ['整装上阵', 'iron_chestplate'],
  tnt: ['轰隆隆！', 'tnt'], deep: ['深入地底', 'deepslate'], night: ['活过第一夜', 'torch'], build: ['建筑师', 'bricks'], farm: ['开始耕作', 'wheat'],
  cook: ['美味佳肴', 'cooked_beef'], bow: ['狙击手', 'bow'], sleep: ['甜蜜的梦', 'red_bed'],
};

const TUTORIAL = [
  { title: '移动', text: '使用 W A S D 键四处走走', icon: 'grass_block', done: (g) => g.tutStat.moved > 8 },
  { title: '获取木头', text: '对着树干按住鼠标左键', icon: 'oak_log', done: (g) => g.adv.wood },
  { title: '打开物品栏', text: '按 E 键打开物品栏', icon: 'chest', done: (g) => g.tutStat.inv },
  { title: '合成木板', text: '用左侧配方书或 2x2 合成格', icon: 'oak_planks', done: (g) => g.tutStat.planks },
  { title: '制作工作台', text: '用 4 个木板合成工作台', icon: 'crafting_table', done: (g) => g.adv.crafting_table },
  { title: '制作木镐', text: '放下工作台，右键打开它', icon: 'wooden_pickaxe', done: (g) => g.adv.pickaxe },
];

class Game {
  constructor() {
    this.settings = Object.assign({ fov: 70, renderDist: 8, sens: 0.5, volume: 0.8, music: 0.45, gamma: 0.5, guiScale: 0, clouds: true, bobbing: true, advTooltips: false, resScale: 1 }, this.loadSettings());
    this.state = 'boot';
    this.mode = 'survival';
    this.entities = []; this.blockEntities = new Map();
    this.keys = {}; this.mouseBtn = [false, false, false];
    this.tickCount = 0; this.worldTime = 1000; this.fps = 0;
    this.perspective = 0;
    this.weather = 0; this.rainTarget = 0; this.weatherTimer = 12000 + Math.random() * 24000;
    this.breaking = null; this.useCd = 0; this.attackCd = 0;
    this.stats = { kills: 0, placed: 0, mined: 0 };
    this.adv = {};
    this.lastTap = {};
    this.sleepFade = 0;
  }
  loadSettings() { try { return JSON.parse(localStorage.getItem('mcjs-settings') || '{}'); } catch (e) { return {}; } }
  saveSettings() { try { localStorage.setItem('mcjs-settings', JSON.stringify(this.settings)); } catch (e) { } }

  async init() {
    this.canvas = document.getElementById('gl');
    this.renderer = new Renderer(this.canvas);
    this.renderer.resScale = this.settings.resScale;
    await this.renderer.init();
    Font.init(this.renderer.images.font);
    this.renderer.buildIcons();
    this.ui = new UI(this, document.getElementById('ui'));
    this.particles = new Particles(this);
    this.applySettings();
    await DB.open();
    this.resize();
    window.addEventListener('resize', () => this.resize());
    bindInput(this);
    this.startTitle();
    let last = performance.now(), acc = 0, frames = 0, fpsT = last;
    const loop = (now) => {
      requestAnimationFrame(loop);
      let dt = now - last; last = now;
      if (dt > 250) dt = 250;
      acc += dt;
      frames++;
      if (now - fpsT > 1000) { this.fps = frames; frames = 0; fpsT = now; }
      if (this.debugPause) { try { this.render(1, now); } catch (e) { console.error(e); } return; }
      try {
        this.frame(dt, now);
        let n = 0;
        while (acc >= 50 && n < 5) { this.tick(); acc -= 50; n++; }
        if (n === 5) acc = 0;
        this.render(acc / 50, now);
      } catch (e) { console.error(e); this.showError(e); }
    };
    requestAnimationFrame(loop);
  }
  showError(e) {
    if (this._errShown) return; this._errShown = true;
    this.ui.message('错误: ' + (e && e.message || e), '#FF5555');
  }
  applySettings() {
    const s = this.settings;
    this.renderer.renderDist = s.renderDist; this.renderer.gamma = s.gamma; this.renderer.showClouds = s.clouds;
    Sound.setVolume(s.volume); Sound.setMusic(s.music);
    if (this.renderer.resScale !== s.resScale) { this.renderer.resScale = s.resScale; this.resize(); }
  }
  resize() { this.renderer.resize(); if (this.ui) this.ui.resize(); }

  // ---- worlds -----------------------------------------------------------------
  setupWorld(seed) {
    if (this.world) this.unloadAll();
    this.world = new World(seed);
    this.world.grassMap = this.renderer.grassMap; this.world.foliageMap = this.renderer.foliageMap;
    this.world.listener = this;
    this.entities = []; this.blockEntities = new Map(); this.particles.list = [];
    this.genQueue = []; this.meshQueue = [];
  }
  unloadAll() {
    for (const ch of this.world.chunks.values()) this.renderer.dropChunk(ch.cx, ch.cz);
    this.world.chunks.clear();
    this.renderer.sections.clear();
  }
  startTitle() {
    this.state = 'title';
    this.setupWorld(hashStr('panorama' + ((Math.random() * 8) | 0)));
    const sp = this.findInland();
    this.player = new Player(this, sp[0], sp[1] + 6, sp[2]);
    this.titleCam = [sp[0] + 0.5, sp[1] + 16, sp[2] + 0.5];
    this.worldTime = 2000; this.weather = 0; this.rainTarget = 0;
    this.ui.open(new TitleScreen(this));
  }
  findInland() {
    const w = this.world;
    for (let r = 0; r < 3000; r += 48) for (let a = 0; a < 12; a++) {
      const x = Math.round(Math.cos(a / 12 * Math.PI * 2) * r), z = Math.round(Math.sin(a / 12 * Math.PI * 2) * r);
      const c = w.column(x, z);
      if (c.h < SEA + 4 || c.h > 105) continue;
      let ok = true;
      for (let k = 0; k < 8 && ok; k++) { const cc = w.column(x + Math.cos(k / 8 * Math.PI * 2) * 56, z + Math.sin(k / 8 * Math.PI * 2) * 56); if (cc.h <= SEA + 1) ok = false; }
      if (ok) { let top = c.h; for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) top = Math.max(top, w.column(x + dx, z + dz).h + (w.biomeOf(w.column(x + dx, z + dz)) === BI.FOREST ? 7 : 0)); return [x, top, z]; }
      if (r === 0) break;
    }
    return this.findSpawn();
  }
  findSpawn() {
    const w = this.world;
    if (w.flat) return [0, 61, 0];
    for (let r = 0; r < 400; r += 8) {
      for (let a = 0; a < 16; a++) {
        const x = Math.round(Math.cos(a / 16 * Math.PI * 2) * r), z = Math.round(Math.sin(a / 16 * Math.PI * 2) * r);
        const c = w.column(x, z), b = w.biomeOf(c);
        if (c.h > SEA + 1 && c.h < 100 && b !== BI.OCEAN && b !== BI.RIVER && b !== BI.BEACH) return [x, c.h + 1, z];
        if (r === 0) break;
      }
    }
    return [0, 80, 0];
  }
  async createWorld(name, seedStr, mode, flat) {
    let seed = parseInt(seedStr, 10);
    if (!seedStr) seed = (Math.random() * 2 ** 31) | 0;
    else if (isNaN(seed) || String(seed) !== seedStr.trim()) seed = hashStr(seedStr);
    const id = 'w' + Date.now().toString(36);
    const meta = { id, name: name || '新的世界', seed, mode, flat: !!flat, created: Date.now(), lastPlayed: Date.now(), time: 1000, player: null };
    await DB.putWorld(meta);
    return this.loadWorld(id);
  }
  async loadWorld(id) {
    const meta = await DB.getWorld(id);
    if (!meta) return;
    this.ui.open(new LoadingScreen(this, '正在载入世界…'));
    this.worldMeta = meta;
    this.setupWorld(meta.seed);
    this.world.flat = !!meta.flat;
    this.world.savedChunks = await DB.loadChunks(id);
    this.dirtySaved = new Set();
    this.mode = meta.mode;
    this.worldTime = meta.time || 1000;
    this.weather = meta.weather || 0; this.rainTarget = meta.rainTarget || 0;
    this.adv = meta.adv || {}; this.stats = meta.stats || { kills: 0, placed: 0, mined: 0 };
    this.tutorial = meta.tutorial ?? (meta.mode === 'survival' ? 0 : -1); this.tutStat = { moved: 0 };
    let sp = meta.spawn || this.findSpawn();
    const p = this.player = new Player(this, sp[0] + 0.5, sp[1], sp[2] + 0.5);
    p.spawn = sp;
    if (meta.player) {
      const d = meta.player;
      p.pos = [...d.pos]; p.prev = [...d.pos]; p.yaw = d.yaw; p.pitch = d.pitch;
      p.health = d.health; p.food = d.food; p.saturation = d.sat; p.xp = d.xp || 0; p.flying = !!d.flying;
      p.inv.slots = d.inv.map(s => s ? { ...s } : null); while (p.inv.slots.length < 40) p.inv.slots.push(null);
      p.inv.sel = d.sel || 0;
      p.air = 300;
    } else {
      p.needsGround = true;
      if (this.mode === 'creative') this.giveCreativeKit();
    }
    for (const [k, v] of Object.entries(meta.blockEntities || {})) this.blockEntities.set(k, v);
    this.pendingMobs = meta.mobs || [];
    this.state = 'loading';
    this.loadStart = performance.now();
  }
  giveCreativeKit() {
    const inv = this.player.inv;
    ['grass_block', 'stone', 'oak_planks', 'glass', 'oak_log', 'bricks', 'torch', 'tnt', 'water_bucket'].forEach((k, i) => inv.slots[i] = makeStack(k, k === 'water_bucket' ? 1 : 64));
  }
  async saveWorld(quiet) {
    if (!this.worldMeta || this.state !== 'play') return;
    const w = this.world, p = this.player, list = [];
    for (const ch of w.chunks.values()) if (ch.modified) { const rec = encodeChunk(ch); w.savedChunks.set(ckey(ch.cx, ch.cz), rec); list.push([ch.cx, ch.cz, rec]); ch.modified = false; }
    for (const k of this.dirtySaved) { const rec = w.savedChunks.get(k); if (rec) { const cx = Math.floor(k / 65536) - 32768, cz = (k % 65536) - 32768; list.push([cx, cz, rec]); } }
    this.dirtySaved.clear();
    const m = this.worldMeta;
    m.lastPlayed = Date.now(); m.time = this.worldTime; m.mode = this.mode; m.weather = this.weather; m.rainTarget = this.rainTarget;
    m.spawn = p.spawn; m.adv = this.adv; m.stats = this.stats; m.tutorial = this.tutorial;
    m.player = { pos: [...p.pos], yaw: p.yaw, pitch: p.pitch, health: p.health, food: p.food, sat: p.saturation, xp: p.xp, inv: p.inv.slots, sel: p.inv.sel, flying: p.flying };
    m.blockEntities = Object.fromEntries(this.blockEntities);
    m.mobs = this.entities.filter(e => e instanceof Mob && e.persistent && !e.dead).map(e => ({ type: e.type, pos: [...e.pos], yaw: e.yaw, health: e.health, sheared: e.sheared, wool: e.woolColor, baby: e.baby ? e.growT : 0 }));
    try {
      await DB.putChunks(m.id, list);
      await DB.putWorld(m);
      if (!quiet) this.ui.message('世界已保存', '#AAAAAA');
    } catch (e) { this.ui.message('保存失败: ' + e.message, '#FF5555'); }
  }
  async quitToTitle() {
    await this.saveWorld(true);
    this.worldMeta = null;
    this.ui.cursor = null;
    this.startTitle();
  }

  // ---- chunk streaming ------------------------------------------------------------
  updateChunks(budgetMs) {
    const w = this.world, R = this.state === 'title' ? 6 : this.settings.renderDist;
    const center = this.state === 'title' ? this.titleCam : this.player.pos;
    const pcx = Math.floor(center[0]) >> 4, pcz = Math.floor(center[2]) >> 4;
    const t0 = performance.now();
    // generation queue: nearest missing chunks within R+1
    if (!this.genQueue.length || this._lastCC !== pcx + ',' + pcz) {
      this._lastCC = pcx + ',' + pcz;
      const q = [];
      for (let dx = -R - 1; dx <= R + 1; dx++) for (let dz = -R - 1; dz <= R + 1; dz++) {
        if (dx * dx + dz * dz > (R + 1.5) * (R + 1.5)) continue;
        if (!w.chunks.has(ckey(pcx + dx, pcz + dz))) q.push([pcx + dx, pcz + dz, dx * dx + dz * dz]);
      }
      q.sort((a, b) => b[2] - a[2]);
      this.genQueue = q;
    }
    this.genQueueLen = this.genQueue.length;
    let gen = 0;
    while (this.genQueue.length && (performance.now() - t0 < budgetMs * 0.5 || gen === 0)) {
      const [cx, cz] = this.genQueue.pop();
      if (w.chunks.has(ckey(cx, cz))) continue;
      this.loadChunk(cx, cz);
      gen++;
    }
    // meshing: dirty chunks whose neighbours exist
    const cand = [];
    for (const ch of w.chunks.values()) {
      if (!ch.dirty) continue;
      const dx = ch.cx - pcx, dz = ch.cz - pcz;
      if (dx * dx + dz * dz > (R + 0.5) * (R + 0.5)) continue;
      let ok = true;
      for (let a = -1; a <= 1 && ok; a++) for (let b = -1; b <= 1; b++) if (!w.chunks.has(ckey(ch.cx + a, ch.cz + b))) { ok = false; break; }
      if (ok) cand.push([ch, dx * dx + dz * dz]);
    }
    cand.sort((a, b) => a[1] - b[1]);
    for (const [ch] of cand) {
      if (performance.now() - t0 > budgetMs) break;
      this.meshChunk(ch);
    }
    this.meshPending = cand.length;
    // unload far chunks
    if (this.tickCount % 40 === 0) {
      for (const ch of [...w.chunks.values()]) {
        const dx = ch.cx - pcx, dz = ch.cz - pcz;
        if (dx * dx + dz * dz > (R + 4) * (R + 4)) {
          if (ch.modified && w.savedChunks && this.state === 'play') { const k = ckey(ch.cx, ch.cz); w.savedChunks.set(k, encodeChunk(ch)); this.dirtySaved.add(k); }
          this.renderer.dropChunk(ch.cx, ch.cz);
          w.chunks.delete(ckey(ch.cx, ch.cz));
        }
      }
    }
  }
  meshChunk(ch) {
    const w = this.world;
    for (let sy = 0; sy < SECTIONS; sy++) {
      if (!(ch.dirty & (1 << sy))) continue;
      const key = ckey(ch.cx, ch.cz) * 16 + sy;
      if (!ch.sectionCount[sy]) {
        // also check if neighbours could make faces visible (only needed when this section has blocks)
        const s = this.renderer.sections.get(key);
        if (s) { this.renderer.freeSection(s); this.renderer.sections.delete(key); }
        continue;
      }
      const res = Mesher.build(w, ch.cx, sy, ch.cz);
      this.renderer.setSection(key, ch.cx, sy, ch.cz, res);
    }
    ch.dirty = 0;
  }
  loadChunk(cx, cz) {
    const w = this.world;
    const saved = w.savedChunks && w.savedChunks.get(ckey(cx, cz));
    let ch;
    if (saved) {
      ch = new Chunk(cx, cz);
      decodeChunk(saved, ch);
      for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
        const c = w.flat ? { temp: 0.3, humid: 0.2, h: 60, river: 0, cont: 0.3, mount: 0 } : w.column(cx * 16 + x, cz * 16 + z);
        ch.biome[z * 16 + x] = w.biomeOf(c);
        w.tintFor(w.grassMap, c.temp, c.humid, ch.grass, (z * 16 + x) * 3);
        w.tintFor(w.foliageMap, c.temp, c.humid, ch.foliage, (z * 16 + x) * 3);
      }
      ch.recount();
    } else {
      ch = w.generate(cx, cz);
    }
    w.chunks.set(ckey(cx, cz), ch);
    w.initLight(ch);
    ch.dirty = 0xFFFF;
    for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) { const n = w.getChunk(cx + a, cz + b); if (n && n !== ch) n.dirty |= 0xFFFF; }
    if (this.state === 'play' || this.state === 'loading') {
      // restore saved mobs
      if (this.pendingMobs && this.pendingMobs.length) {
        this.pendingMobs = this.pendingMobs.filter(m => {
          if ((Math.floor(m.pos[0]) >> 4) === cx && (Math.floor(m.pos[2]) >> 4) === cz) {
            const e = new Mob(this, m.type, m.pos[0], m.pos[1], m.pos[2]);
            e.yaw = m.yaw; e.health = m.health; e.sheared = m.sheared; e.woolColor = m.wool || 0;
            if (m.baby) { e.makeBaby(); e.growT = m.baby; }
            this.entities.push(e); return false;
          }
          return true;
        });
      }
      if (!saved && !(this.worldMeta && this.worldMeta.noAnimals)) this.spawnAnimals(ch);
    }
  }
  spawnAnimals(ch) {
    const r = RNG(hashStr('animals' + this.world.seed + ch.cx + ':' + ch.cz));
    if (r() > 0.1) return;
    const types = ['pig', 'cow', 'sheep', 'sheep', 'chicken'];
    const t = types[r.int(types.length)];
    const n = 2 + r.int(3);
    for (let i = 0; i < n; i++) {
      const x = ch.cx * 16 + r.int(16), z = ch.cz * 16 + r.int(16);
      for (let y = HEIGHT - 2; y > SEA; y--) {
        const id = ch.blocks[(y << 8) | ((z & 15) << 4) | (x & 15)];
        if (id === 0 || !BLOCKS[id].solid) continue;
        if (id === 2) this.entities.push(new Mob(this, t, x + 0.5, y + 1, z + 0.5));
        break;
      }
    }
  }

  // ---- per-frame update ---------------------------------------------------------------
  frame(dt, now) {
    if (!this.world) return;
    if (this.state === 'title') {
      this.titleYaw = (this.titleYaw || 0) + dt * 0.00004;
      this.updateChunks(8);
      return;
    }
    if (this.state === 'loading') {
      this.updateChunks(30);
      const p = this.player, pcx = Math.floor(p.pos[0]) >> 4, pcz = Math.floor(p.pos[2]) >> 4;
      let ready = 0, need = 0;
      for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) { need++; const ch = this.world.getChunk(pcx + dx, pcz + dz); if (ch && !ch.dirty) ready++; }
      this.loadProgress = ready / need;
      if (ready === need) {
        if (p.needsGround) {
          const x = Math.floor(p.pos[0]), z = Math.floor(p.pos[2]);
          let y = HEIGHT - 2;
          while (y > 0 && !BLOCKS[this.world.getBlock(x, y, z)].solid && !IS_FLUID[this.world.getBlock(x, y, z)]) y--;
          p.pos[1] = y + 1; p.prev[1] = y + 1; p.spawn = [x, y + 1, z]; p.needsGround = false;
          this.worldMeta.spawn = p.spawn;
        }
        this.state = 'play';
        this.ui.close();
        this.ui.message(`欢迎来到 ${this.worldMeta.name}！按 H 查看操作说明。`, '#FFFF55');
        this.lastSave = now;
        Sound.init();
      }
      return;
    }
    if (this.state !== 'play') return;
    this.updateChunks(6);
    if (now - this.lastSave > 45000) { this.lastSave = now; this.saveWorld(true); }
    Sound.updateMusic(true);
    // mouse look handled in input; continuous mining handled in tick
  }

  // ---- 20 TPS tick ---------------------------------------------------------------------------
  tick() {
    if (!this.world) return;
    if (this.state === 'title') { this.worldTime += 1; this.world.time++; return; }
    if (this.state !== 'play') return;
    if (this.ui.screen && this.ui.screen.pauses) return;
    this.tickCount++;
    this.world.time++;
    this.worldTime++;
    const p = this.player;
    // weather
    if (--this.weatherTimer < 0) { this.rainTarget = this.rainTarget ? 0 : 1; this.weatherTimer = this.rainTarget ? 6000 + Math.random() * 12000 : 12000 + Math.random() * 36000; }
    this.weather += clamp(this.rainTarget - this.weather, -0.01, 0.01);
    const input = this.ui.screen ? { fwd: 0, strafe: 0, jump: false, sneak: false, sprint: false } : this.readInput();
    if (this.tickSleep()) { }
    else if (!p.loadedHere()) { p.savePrev(); }
    else p.tick(input);
    if (p.pos[1] < SEA - 30 && !this.adv.deep && p.pos[1] < 16) this.grant('deep');
    this.handleInteraction();
    // entities
    for (const e of this.entities) {
      if (e.dead) continue;
      if (!this.world.isLoaded(Math.floor(e.pos[0]), Math.floor(e.pos[2])) || !this.world.isLoaded(Math.floor(e.pos[0]) + 16, Math.floor(e.pos[2])) || !this.world.isLoaded(Math.floor(e.pos[0]) - 16, Math.floor(e.pos[2]))) { e.savePrev(); continue; }
      e.tick();
    }
    this.entities = this.entities.filter(e => !e.dead);
    this.particles.tick();
    this.world.tickScheduled();
    this.world.randomTick(Math.floor(p.pos[0]), Math.floor(p.pos[2]), this.rng || (this.rng = RNG(12345)));
    this.tickFurnaces();
    if (this.tickCount % 20 === 0) this.spawnMobs();
    if (this.tutorial >= 0) {
      this.tutStat.moved += Math.hypot(p.pos[0] - p.prev[0], p.pos[2] - p.prev[2]);
      if (this.ui.screen instanceof ContainerScreen) this.tutStat.inv = true;
      if (TUTORIAL[this.tutorial].done(this)) { Sound.toast(); this.tutorial++; if (this.tutorial >= TUTORIAL.length) this.tutorial = -1; }
    }
    if (this.tickCount % 20 === 0 && !this.adv.armor && p.inv.slots.slice(36).some(s => s && ITEMS[s.id].key.startsWith('iron_'))) this.grant('armor');
    // first night advancement
    if (!this.adv.night && this.mode === 'survival' && this.dayTime() > 23500 && this.worldTime > 20000) this.grant('night');
    // rain splashes
    if (this.weather > 0.2 && this.tickCount % 2 === 0) this.rainParticles();
    // eye in fluid
    const eyeY = p.pos[1] + p.eye();
    const eb = this.world.getBlock(Math.floor(p.pos[0]), Math.floor(eyeY), Math.floor(p.pos[2]));
    this.headInWater = eb === WATER; this.headInLava = eb === LAVA;
    if (this.headInWater && Math.random() < 0.1) this.particles.spawn('bubble', p.pos[0] + (Math.random() - 0.5), eyeY - 0.5, p.pos[2] + (Math.random() - 0.5), 0, 0.05, 0);
    // torch / lava ambient particles near player
    this.ambientParticles();
    Sound.listener = { x: p.pos[0], y: p.pos[1] + 1.6, z: p.pos[2], yaw: p.yaw };
  }
  readInput() {
    const k = this.keys;
    return {
      fwd: (k.KeyW ? 1 : 0) - (k.KeyS ? 1 : 0), strafe: (k.KeyD ? 1 : 0) - (k.KeyA ? 1 : 0),
      jump: !!k.Space, sneak: !!(k.ShiftLeft || k.ShiftRight), sprint: !!(k.ControlLeft || this.sprintToggle),
    };
  }
  ambientParticles() {
    const p = this.player, w = this.world;
    for (let i = 0; i < 40; i++) {
      const x = Math.floor(p.pos[0] + (Math.random() - 0.5) * 24), y = Math.floor(p.pos[1] + (Math.random() - 0.5) * 16), z = Math.floor(p.pos[2] + (Math.random() - 0.5) * 24);
      const id = w.getBlock(x, y, z);
      if (id === 21) {
        const m = w.getMeta(x, y, z);
        let fx = x + 0.5, fy = y + 0.7, fz = z + 0.5;
        if (m) { fx += DIRS[m][0] * 0.27 * -1 + DIRS[m][0] * 0.38 * 0; fx = x + 0.5 + DIRS[m][0] * 0.38 - DIRS[m][0] * 0.32 * 0.6 * 1; fz = z + 0.5 + DIRS[m][2] * 0.38 - DIRS[m][2] * 0.32 * 0.6; fy = y + 0.92; }
        this.particles.spawn('flame', fx, fy, fz, 0, 0.002, 0).life = 12;
        this.particles.spawn('smoke', fx, fy + 0.05, fz, 0, 0.01, 0).size = 0.06;
      } else if (id === LAVA && w.getBlock(x, y + 1, z) === 0 && Math.random() < 0.05) {
        this.particles.spawn('lava', x + Math.random(), y + 1, z + Math.random(), (Math.random() - 0.5) * 0.1, 0.25, (Math.random() - 0.5) * 0.1);
        if (Math.random() < 0.3) Sound.noise([x, y, z], { type: 'bandpass', freq: 400, q: 2, dur: 0.1, vol: 0.3 });
      }
    }
  }
  rainParticles() {
    const p = this.player, w = this.world;
    for (let i = 0; i < 6; i++) {
      const x = Math.floor(p.pos[0] + (Math.random() - 0.5) * 20), z = Math.floor(p.pos[2] + (Math.random() - 0.5) * 20);
      const ch = w.getChunk(x >> 4, z >> 4); if (!ch) continue;
      if (this.isCold(x, z)) continue;
      let y = Math.floor(p.pos[1]) + 10;
      while (y > p.pos[1] - 10 && !BLOCKS[w.getBlock(x, y, z)].solid && !IS_FLUID[w.getBlock(x, y, z)]) y--;
      if (y > p.pos[1] - 10) this.particles.spawn('rain', x + Math.random(), y + 1.05, z + Math.random(), 0, 0.05, 0);
    }
  }
  isCold(x, z) {
    const ch = this.world.getChunk(x >> 4, z >> 4);
    if (!ch) return false;
    const b = ch.biome[((z & 15) << 4) | (x & 15)];
    return b === BI.SNOWY || b === BI.SNOWPEAK || b === BI.FROZEN_OCEAN;
  }
  tintAt(x, z, kind) {
    const ch = this.world.getChunk(x >> 4, z >> 4);
    if (!ch) return [124, 189, 107];
    const i = (((z & 15) << 4) | (x & 15)) * 3, a = kind === 'foliage' ? ch.foliage : ch.grass;
    if (kind === 'birch') return [128, 167, 85]; if (kind === 'spruce') return [97, 153, 97]; if (kind === 'water') return [63, 118, 228];
    return [a[i], a[i + 1], a[i + 2]];
  }

  // ---- time ---------------------------------------------------------------------------
  dayTime() { return ((this.worldTime % 24000) + 24000) % 24000; }
  celestial() {
    const f0 = this.dayTime() / 24000 - 0.25;
    const f = f0 - Math.floor(f0);
    const g = 0.5 - Math.cos(f * Math.PI) / 2;
    return f + (g - f) / 3;
  }
  isDay() { const c = Math.cos(this.celestial() * Math.PI * 2); return c > 0.0; }
  daylight() { const c = Math.cos(this.celestial() * Math.PI * 2); return clamp(c * 2 + 0.5, 0, 1); }

  // ---- XP ----------------------------------------------------------------------------
  xpNeeded(l) { return l >= 30 ? 112 + (l - 30) * 9 : l >= 15 ? 37 + (l - 15) * 5 : 7 + l * 2; }
  xpTotalFor(l) { let t = 0; for (let i = 0; i < l; i++) t += this.xpNeeded(i); return t; }
  xpLevel() { let l = 0, t = 0, x = this.player.xp; while (t + this.xpNeeded(l) <= x) { t += this.xpNeeded(l); l++; } return l; }
  addXp(n) {
    if (this.mode === 'creative' || n <= 0) return;
    const before = this.xpLevel();
    this.player.xp += n;
    Sound.tone(null, { freq: 1200 + Math.random() * 400, dur: 0.12, vol: 0.12, type: 'sine' });
    const after = this.xpLevel();
    if (after > before && after % 5 === 0) Sound.levelup();
  }

  grant(key) {
    if (this.adv[key] || this.state !== 'play') return;
    this.adv[key] = Date.now();
    const a = ADVANCEMENTS[key];
    this.ui.toast('进度达成！', a[0], item(a[1]));
    this.ui.message(`玩家取得了进度 [${a[0]}]`, '#55FF55');
  }

  // ---- hooks from world/entities ------------------------------------------------------------
  onBlockChanged(x, y, z, old, id, oldMeta) {
    if (old === id) return;
    const k = `${x},${y},${z}`;
    const be = this.blockEntities.get(k);
    if (be && id !== 52 && id !== 20 && id !== 117) {
      const drops = be.type === 'chest' ? be.slots : [be.input, be.fuel, be.output];
      for (const s of drops) if (s) this.dropItem(x + 0.5, y + 0.5, z + 0.5, { ...s });
      this.blockEntities.delete(k);
      if (this.ui.screen && this.ui.screen.be === be) this.ui.close();
    }
  }
  onNeighborUpdate(x, y, z, id) {
    const w = this.world, b = BLOCKS[id];
    if (b.gravity) {
      const below = w.getBlock(x, y - 1, z);
      if (y > 0 && (below === 0 || REPLACEABLE[below] || IS_FLUID[below]) && w.isLoaded(x, z)) {
        const meta = w.getMeta(x, y, z);
        w.setBlock(x, y, z, 0, 0);
        this.entities.push(new FallingBlock(this, x, y, z, id, meta));
      }
      return;
    }
    if (b.needsSupport || b.door) {
      if (!this.supported(x, y, z, id, w.getMeta(x, y, z))) this.breakBlock(x, y, z, true);
    }
  }
  supported(x, y, z, id, meta) {
    const w = this.world, below = w.getBlock(x, y - 1, z);
    const solidBelow = SOLIDCUBE[below] || (BLOCKS[below].slab && (w.getMeta(x, y - 1, z) & 3) !== 0) || BLOCKS[below].stairs && (w.getMeta(x, y - 1, z) & 4) === 0 || below === 63;
    const b = BLOCKS[id];
    switch (b.key) {
      case 'torch': if (!meta) return solidBelow; return !!SOLIDCUBE[w.getBlock(x + DIRS[meta][0], y, z + DIRS[meta][2])];
      case 'ladder': return !!SOLIDCUBE[w.getBlock(x + DIRS[meta][0], y, z + DIRS[meta][2])];
      case 'wheat': return below === 63;
      case 'sugar_cane': return below === 38 || below === 2 || below === 3 || below === 9 || below === 97;
      case 'cactus': return below === 37 || below === 9 || below === 97;
      case 'dead_bush': return below === 9 || below === 97 || below === 3 || below === 2 || below === 83;
      case 'snow': return !!SOLIDCUBE[below] && below !== 35 && below !== 96;
      case 'oak_door': return (meta & 8) ? w.getBlock(x, y - 1, z) === 69 : (SOLIDCUBE[below] && w.getBlock(x, y + 1, z) === 69);
      case 'red_mushroom': case 'brown_mushroom': return !!SOLIDCUBE[below];
    }
    if (b.needsSupport) return below === 2 || below === 3 || below === 63 || below === 88;
    return true;
  }
  onFizz(x, y, z) { Sound.fizz([x, y, z]); for (let i = 0; i < 6; i++) this.particles.spawn('smoke', x + Math.random(), y + 1, z + Math.random(), 0, 0.05, 0); }
  onFluidDestroy(x, y, z, id) {
    const b = BLOCKS[id];
    if (b && !REPLACEABLE[id] && this.mode !== 'creative') this.dropBlockLoot(x, y, z, id, this.world.getMeta(x, y, z), null);
    else if (b && b.drops && id !== 39 && id !== 40) this.dropBlockLoot(x, y, z, id, 0, null);
  }
  onRandomTick(x, y, z, id, ch) {
    const w = this.world;
    switch (id) {
      case 2: {
        const above = w.getBlock(x, y + 1, z);
        if (SOLIDCUBE[above] || IS_FLUID[above]) { w.setBlock(x, y, z, 3, 0); break; }
        if (w.skyAt(x, y + 1, z) >= 9 || w.blockLightAt(x, y + 1, z) >= 9) {
          const nx = x + ((Math.random() * 3) | 0) - 1, ny = y + ((Math.random() * 5) | 0) - 3, nz = z + ((Math.random() * 3) | 0) - 1;
          if (w.getBlock(nx, ny, nz) === 3 && !SOLIDCUBE[w.getBlock(nx, ny + 1, nz)] && !IS_FLUID[w.getBlock(nx, ny + 1, nz)] && (w.getLight(nx, ny + 1, nz) >> 4) >= 4) w.setBlock(nx, ny, nz, 2, 0);
        }
        break;
      }
      case 64: {
        const m = w.getMeta(x, y, z);
        if (m < 7 && Math.max(w.skyAt(x, y, z) * this.daylight(), w.blockLightAt(x, y, z)) >= 9 && Math.random() < (w.getMeta(x, y - 1, z) ? 0.5 : 0.25)) w.setMeta(x, y, z, m + 1);
        break;
      }
      case 65: case 66: case 67:
        if (Math.random() < 0.15 && (w.skyAt(x, y, z) >= 9 || w.blockLightAt(x, y, z) >= 9)) this.growTree(x, y, z, id);
        break;
      case 63: {
        let wet = false;
        for (let dx = -4; dx <= 4 && !wet; dx++) for (let dz = -4; dz <= 4 && !wet; dz++) for (let dy = 0; dy <= 1; dy++) if (w.getBlock(x + dx, y + dy, z + dz) === WATER) { wet = true; break; }
        const m = w.getMeta(x, y, z);
        if (wet && !m) w.setMeta(x, y, z, 1);
        else if (!wet) { if (m) w.setMeta(x, y, z, 0); else if (w.getBlock(x, y + 1, z) !== 64 && Math.random() < 0.3) w.setBlock(x, y, z, 3, 0); }
        break;
      }
      case 38: case 37: {
        if (w.getBlock(x, y + 1, z) === 0 && Math.random() < 0.08) {
          let h = 1; while (w.getBlock(x, y - h, z) === id) h++;
          if (h < 3) w.setBlock(x, y + 1, z, id, 0);
        }
        break;
      }
      case 15: case 47: case 50: {
        if (w.getMeta(x, y, z) & 4) break; // player placed
        if (!this.nearLog(x, y, z)) this.breakBlock(x, y, z, true, true);
        break;
      }
      case 34: case 35:
        if (w.blockLightAt(x, y, z) > 11 || (id === 35 && w.blockLightAt(x, y + 1, z) > 11)) { if (id === 35) w.setBlock(x, y, z, WATER, 0); else w.setBlock(x, y, z, 0, 0); }
        break;
    }
  }
  nearLog(x, y, z) {
    const w = this.world, seen = new Set([`${x},${y},${z}`]), q = [[x, y, z, 0]];
    while (q.length) {
      const [a, b, c, d] = q.shift();
      for (const D of DIRS) {
        const nx = a + D[0], ny = b + D[1], nz = c + D[2], k = `${nx},${ny},${nz}`;
        if (seen.has(k)) continue; seen.add(k);
        if (!w.isLoaded(nx, nz)) return true;
        const id = w.getBlock(nx, ny, nz);
        if (id === 14 || id === 46 || id === 49) return true;
        if ((id === 15 || id === 47 || id === 50) && d < 4) q.push([nx, ny, nz, d + 1]);
      }
    }
    return false;
  }
  growTree(x, y, z, sap) {
    const w = this.world;
    const type = sap === 66 ? 'birch' : sap === 67 ? 'spruce' : (Math.random() < 0.1 ? 'big' : 'oak');
    const h = 4 + ((Math.random() * 3) | 0);
    for (let dy = 1; dy <= h + 1; dy++) { const id = w.getBlock(x, y + dy, z); if (id && !BLOCKS[id].replaceable && id !== 15 && id !== 47 && id !== 50) return false; }
    w.setBlock(x, y, z, 0, 0, 0);
    this.world.placeTree((wx, wy, wz, id, meta, force) => {
      const cur = w.getBlock(wx, wy, wz);
      if (force ? cur !== 6 : (cur === 0 || REPLACEABLE[cur])) w.setBlock(wx, wy, wz, id, meta);
    }, { x, y, z, type, h, seed: (Math.random() * 1e9) | 0 });
    return true;
  }

  // ---- mob spawning ----------------------------------------------------------------------
  spawnMobs() {
    if (this.mode === 'creative' && !this.settings.creativeMobs) { }
    const p = this.player, w = this.world;
    const hostiles = this.entities.filter(e => e instanceof Mob && e.T.hostile).length;
    const passives = this.entities.filter(e => e instanceof Mob && e.T.passive && e.distTo(p) < 96).length;
    const day = this.daylight();
    for (let attempt = 0; attempt < 3; attempt++) {
      const a = Math.random() * Math.PI * 2, d = 24 + Math.random() * 40;
      const x = Math.floor(p.pos[0] + Math.sin(a) * d), z = Math.floor(p.pos[2] + Math.cos(a) * d);
      if (!w.isLoaded(x, z)) continue;
      const surface = w.surfaceY(x, z);
      // choose y: surface or random cave spot
      let y = surface + 1;
      if (Math.random() < 0.5) {
        const ty = 8 + Math.floor(Math.random() * Math.max(1, surface - 10));
        if (w.getBlock(x, ty, z) === 0 && w.getBlock(x, ty + 1, z) === 0 && BLOCKS[w.getBlock(x, ty - 1, z)].solid) y = ty;
      }
      if (w.getBlock(x, y, z) !== 0 || w.getBlock(x, y + 1, z) !== 0 || !SOLIDCUBE[w.getBlock(x, y - 1, z)]) continue;
      const l = w.getLight(x, y, z), sky = (l >> 4) * day, blk = l & 15;
      if (blk === 0 && sky <= 7 && hostiles < 22 && Math.random() < 0.6 && Math.hypot(x - p.pos[0], z - p.pos[2]) > 24) {
        const r = Math.random();
        const type = r < 0.35 ? 'zombie' : r < 0.6 ? 'skeleton' : r < 0.85 ? 'creeper' : 'spider';
        const m = new Mob(this, type, x + 0.5, y, z + 0.5);
        m.persistent = false;
        if (!m.collides(m.box())) this.entities.push(m);
      } else if (passives < 12 && day > 0.8 && w.getBlock(x, y - 1, z) === 2 && sky >= 9 && Math.random() < 0.02) {
        const t = ['pig', 'cow', 'sheep', 'chicken'][(Math.random() * 4) | 0];
        for (let i = 0; i < 2 + Math.random() * 2; i++) this.entities.push(new Mob(this, t, x + 0.5 + Math.random(), y, z + 0.5 + Math.random()));
      }
    }
  }

  // ---- furnaces ---------------------------------------------------------------------------
  tickFurnaces() {
    for (const [k, be] of this.blockEntities) {
      if (be.type !== 'furnace') continue;
      const [x, y, z] = be.pos;
      if (!this.world.isLoaded(x, z)) continue;
      const wasBurning = be.burn > 0;
      if (be.burn > 0) be.burn--;
      const rec = be.input && SMELTING[be.input.id];
      const canSmelt = rec && (!be.output || (be.output.id === rec.result && be.output.count < maxStack(rec.result)));
      if (be.burn === 0 && canSmelt && be.fuel) {
        const f = fuelValue(be.fuel.id);
        if (f) {
          be.burn = be.burnMax = f;
          if (be.fuel.id === item('lava_bucket')) be.fuel = makeStack('bucket');
          else { be.fuel.count--; if (!be.fuel.count) be.fuel = null; }
        }
      }
      if (be.burn > 0 && canSmelt) {
        if (++be.cook >= 200) {
          be.cook = 0;
          if (be.output) be.output.count++; else be.output = { id: rec.result, count: 1, dmg: 0 };
          be.xp = (be.xp || 0) + rec.xp;
          be.input.count--; if (!be.input.count) be.input = null;
        }
      } else if (be.cook > 0) be.cook = Math.max(0, be.cook - 2);
      const burning = be.burn > 0;
      if (burning !== wasBurning) {
        const meta = this.world.getMeta(x, y, z);
        this.world.setBlock(x, y, z, burning ? 117 : 20, meta, 0);
      }
    }
  }
  markBlockEntity() { }
  getBlockEntity(x, y, z, type) {
    const k = `${x},${y},${z}`;
    let be = this.blockEntities.get(k);
    if (!be) {
      be = type === 'chest' ? { type, pos: [x, y, z], slots: new Array(27).fill(null) } : { type, pos: [x, y, z], input: null, fuel: null, output: null, burn: 0, burnMax: 0, cook: 0 };
      this.blockEntities.set(k, be);
    }
    return be;
  }
  trySleep(x, y, z) {
    const p = this.player, w = this.world;
    const meta = w.getMeta(x, y, z);
    let hx = x, hz = z;
    if (!(meta & 4)) { const D2 = DIRS[[4, 1, 5, 0][meta & 3]]; hx += D2[0]; hz += D2[2]; }
    p.spawn = [hx, y + 1, hz];
    const t = this.dayTime();
    if (t > 1000 && t < 12542 && this.weather < 0.8) { this.ui.actionBar = { text: '你只能在夜间或雷暴中入睡（已设置重生点）', t: performance.now() }; return; }
    if (Math.hypot(p.pos[0] - hx - 0.5, p.pos[2] - hz - 0.5) > 3) { this.ui.actionBar = { text: '你离床太远了', t: performance.now() }; return; }
    const danger = this.entities.some(e => e instanceof Mob && e.T.hostile && e.deathTime === 0 && Math.abs(e.pos[0] - hx) < 8 && Math.abs(e.pos[1] - y) < 5 && Math.abs(e.pos[2] - hz) < 8);
    if (danger) { this.ui.actionBar = { text: '你现在不能休息，周围有怪物在游荡', t: performance.now(), color: '#FF5555' }; return; }
    this.sleeping = { x: hx + 0.5, y: y + 0.5625, z: hz + 0.5, t: 0, rot: meta & 3 };
    p.vel = [0, 0, 0];
    this.ui.message('正在睡觉…（按 Shift 起床）', '#AAAAAA');
  }
  tickSleep() {
    const s = this.sleeping, p = this.player;
    if (!s) { if (this.sleepFade > 0) this.sleepFade = Math.max(0, this.sleepFade - 0.05); return false; }
    s.t++;
    p.pos = [s.x, s.y, s.z]; p.prev = [...p.pos]; p.vel = [0, 0, 0]; p.fallDist = 0;
    this.sleepFade = Math.min(1, s.t / 80);
    const wake = this.keys.ShiftLeft || this.keys.ShiftRight || p.hurtTime > 0;
    if (s.t >= 100) {
      this.worldTime = (Math.floor(this.worldTime / 24000) + 1) * 24000;
      if (this.rainTarget) { this.rainTarget = 0; this.weather = 0; }
      this.ui.message('早上好！', '#FFFF55'); this.grant('sleep');
    }
    if (s.t >= 100 || wake) {
      this.sleeping = null;
      p.pos = [s.x, s.y + 0.5, s.z]; p.prev = [...p.pos];
      while (p.collides(p.box()) && p.pos[1] < HEIGHT) p.pos[1] += 1;
      p.prev = [...p.pos];
    }
    return true;
  }
  fillLoot(be, x, y, z) {
    const r = RNG(hashStr('loot' + this.world.seed + ':' + x + ':' + y + ':' + z));
    const table = [['bread', 1, 3, 0.5], ['wheat', 1, 4, 0.5], ['iron_ingot', 1, 4, 0.55], ['gold_ingot', 1, 3, 0.35], ['coal', 3, 8, 0.5], ['string', 1, 4, 0.4],
      ['bone', 2, 6, 0.5], ['rotten_flesh', 2, 5, 0.5], ['gunpowder', 1, 4, 0.4], ['diamond', 1, 2, 0.2], ['golden_apple', 1, 1, 0.12], ['bucket', 1, 1, 0.15],
      ['iron_pickaxe', 1, 1, 0.08], ['apple', 1, 3, 0.3], ['redstone', 2, 6, 0.25], ['emerald', 1, 2, 0.12], ['wheat_seeds', 2, 5, 0.3], ['iron_chestplate', 1, 1, 0.06], ['bow', 1, 1, 0.1], ['arrow', 4, 12, 0.25]];
    for (const [k, mn, mx, ch] of table) {
      if (r() > ch) continue;
      let slot = r.int(27); let guard = 0;
      while (be.slots[slot] && guard++ < 30) slot = r.int(27);
      be.slots[slot] = makeStack(k, mn + r.int(mx - mn + 1));
    }
  }
  onSmeltTake(st, be) {
    const id = st.id;
    if (id === item('iron_ingot')) this.grant('iron');
    if (['cooked_beef', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken'].some(k => item(k) === id)) this.grant('cook');
    if (be.xp) { this.addXp(Math.floor(be.xp + Math.random())); be.xp = 0; }
  }
  onCraft(id, count) {
    const k = ITEMS[id].key;
    if (k.endsWith('_planks') && this.tutStat) this.tutStat.planks = true;
    if (k === 'crafting_table') this.grant('crafting_table');
    if (k === 'wooden_pickaxe') this.grant('pickaxe');
    if (k === 'stone_pickaxe') this.grant('stone_pickaxe');
    if (k === 'furnace') this.grant('furnace');
    if (k === 'bread') this.grant('bread');
    if (k === 'bow') this.grant('bow');
  }
  onPickup(id, n) {
    const k = ITEMS[id].key;
    if (k.endsWith('_log')) this.grant('wood');
    if (k === 'diamond') this.grant('diamond');
    if (k === 'wheat') this.grant('farm');
  }
  onMobKilled(m, src) {
    if (src && (src.attacker === this.player || src.player)) {
      if (m.T.hostile) { this.grant('kill'); this.addXp(5); } else this.addXp(1 + ((Math.random() * 3) | 0));
    }
  }
  onPlayerHurt(src) { this.hurtTilt = 1; }
  onPlayerDeath(src) {
    const p = this.player;
    const msgs = { fall: '从高处摔了下来', lava: '试图在熔岩里游泳', fire: '被烧死了', drown: '淹死了', starve: '饿死了', mob: '被杀死了', arrow: '被射杀', explosion: '爆炸了', cactus: '被戳死了', void: '掉出了这个世界' };
    let msg = msgs[src && src.type] || '死了';
    if (src && src.attacker && src.attacker.T) msg = (src.type === 'arrow' ? '被' + src.attacker.T.cn + '射杀' : '被' + src.attacker.T.cn + '杀死了');
    if (src && src.type === 'explosion' && src.attacker && src.attacker.T) msg = '被' + src.attacker.T.cn + '炸死了';
    this.deathMsg = '玩家' + msg;
    this.ui.message(this.deathMsg, '#FFFFFF');
    // drop inventory
    for (let i = 0; i < 40; i++) { const s = p.inv.slots[i]; if (s) { this.dropItem(p.pos[0], p.pos[1] + 1, p.pos[2], s, [(Math.random() - 0.5) * 0.5, 0.3, (Math.random() - 0.5) * 0.5]); p.inv.slots[i] = null; } }
    this.deathScore = p.xp; p.xp = 0;
    setTimeout(() => { if (this.state === 'play') this.ui.open(new DeathScreen(this)); }, 1000);
  }
  respawn() {
    const p = this.player, s = p.spawn;
    p.pos = [s[0] + 0.5, s[1], s[2] + 0.5]; p.prev = [...p.pos]; p.vel = [0, 0, 0];
    p.health = 20; p.food = 20; p.saturation = 5; p.deathTime = 0; p.fire = 0; p.air = 300; p.fallDist = 0; p.dead = false;
    let y = s[1];
    while (p.collides(p.box()) && y < HEIGHT) { y++; p.pos[1] = y; }
    p.prev = [...p.pos];
    this.ui.close();
  }

  // ---- items & drops ---------------------------------------------------------------------
  dropItem(x, y, z, stack, vel) {
    if (!stack || stack.count <= 0) return;
    const e = new ItemEntity(this, x, y, z, { id: stack.id, count: stack.count, dmg: stack.dmg || 0 }, vel);
    this.entities.push(e);
    return e;
  }
  dropFromPlayer(stack) {
    const p = this.player;
    const dir = this.lookDir();
    const e = this.dropItem(p.pos[0], p.pos[1] + p.eye() - 0.3, p.pos[2], stack, [dir[0] * 0.3, dir[1] * 0.3 + 0.1, dir[2] * 0.3]);
    if (e) e.pickup = 40;
  }
  addEntity(e) { this.entities.push(e); }
  dropBlockLoot(x, y, z, id, meta, tool) {
    const b = BLOCKS[id];
    let drops;
    if (tool && ITEMS[tool.id].key === 'shears' && (b.key.endsWith('leaves') || id === 39 || id === 40 || id === 45)) drops = [[id, 1]];
    else if (b.drops) drops = b.drops(meta, Math.random, tool);
    else if (!b.noItem) drops = [[id, 1]];
    else drops = [];
    for (const [k, n] of drops) if (n > 0) this.dropItem(x + 0.5 + (Math.random() - 0.5) * 0.3, y + 0.3, z + 0.5 + (Math.random() - 0.5) * 0.3, makeStack(k, n));
  }

  // ---- explosions ----------------------------------------------------------------------------
  explode(x, y, z, power, source, inWater) {
    const w = this.world;
    Sound.explode([x, y, z]);
    if (source instanceof PrimedTNT) this.grant('tnt');
    const destroy = new Set();
    if (!inWater) {
      const r = Math.random;
      for (let i = 0; i < 16; i++) for (let j = 0; j < 16; j++) for (let k = 0; k < 16; k++) {
        if (i > 0 && i < 15 && j > 0 && j < 15 && k > 0 && k < 15) continue;
        let dx = i / 15 * 2 - 1, dy = j / 15 * 2 - 1, dz = k / 15 * 2 - 1;
        const l = Math.hypot(dx, dy, dz); dx /= l; dy /= l; dz /= l;
        let s = power * (0.7 + r() * 0.6), px = x, py = y, pz = z;
        while (s > 0) {
          const bx = Math.floor(px), by = Math.floor(py), bz = Math.floor(pz);
          const id = w.getBlock(bx, by, bz);
          if (id) {
            const b = BLOCKS[id];
            const res = b.hardness < 0 ? 3600000 : IS_FLUID[id] ? 100 : b.hardness >= 50 ? 1200 : (b.tool === 'pickaxe' ? b.hardness * 4 : b.hardness);
            s -= (res + 0.3) * 0.3;
            if (s > 0 && by >= 0) destroy.add(bx + ',' + by + ',' + bz);
          }
          px += dx * 0.3; py += dy * 0.3; pz += dz * 0.3; s -= 0.225;
        }
      }
    }
    for (const k of destroy) {
      const [bx, by, bz] = k.split(',').map(Number);
      const id = w.getBlock(bx, by, bz);
      if (!id || IS_FLUID[id]) continue;
      if (id === 26) { w.setBlock(bx, by, bz, 0, 0); this.entities.push(new PrimedTNT(this, bx, by, bz, 10 + ((Math.random() * 20) | 0))); continue; }
      if (Math.random() < 1 / power && this.mode !== 'creative') this.dropBlockLoot(bx, by, bz, id, w.getMeta(bx, by, bz), null);
      w.setBlock(bx, by, bz, 0, 0);
    }
    // damage entities
    const R = power * 2;
    for (const e of [...this.entities, this.player]) {
      if (e === source) continue;
      const d = Math.hypot(e.pos[0] - x, e.pos[1] + e.h / 2 - y, e.pos[2] - z);
      if (d > R) continue;
      const imp = 1 - d / R;
      const dir = [(e.pos[0] - x) / (d || 1), (e.pos[1] + e.h / 2 - y) / (d || 1), (e.pos[2] - z) / (d || 1)];
      if (e instanceof Living) e.hurt(Math.floor((imp * imp + imp) / 2 * 7 * R + 1), { type: 'explosion', attacker: source && source.T ? source : null });
      e.vel[0] += dir[0] * imp; e.vel[1] += dir[1] * imp + 0.1; e.vel[2] += dir[2] * imp;
    }
    for (let i = 0; i < 24; i++) this.particles.spawn('explosion', x + (Math.random() - 0.5) * power * 1.5, y + (Math.random() - 0.5) * power * 1.5, z + (Math.random() - 0.5) * power * 1.5, 0, 0, 0);
    for (let i = 0; i < 40; i++) this.particles.spawn('smoke', x + (Math.random() - 0.5) * power * 2, y + (Math.random() - 0.5) * power * 2, z + (Math.random() - 0.5) * power * 2, (Math.random() - 0.5) * 0.3, Math.random() * 0.2, (Math.random() - 0.5) * 0.3);
    const p = this.player;
    const pd = Math.hypot(p.pos[0] - x, p.pos[1] - y, p.pos[2] - z);
    if (pd < 30) this.shake = Math.max(this.shake || 0, (1 - pd / 30) * power / 4);
  }

  // ---- raycasting ----------------------------------------------------------------------------
  lookDir() { const p = this.player, cp = Math.cos(p.pitch); return [-Math.sin(p.yaw) * cp, Math.sin(p.pitch), -Math.cos(p.yaw) * cp]; }
  selBoxes(x, y, z, id) {
    const b = BLOCKS[id], m = this.world.getMeta(x, y, z);
    if (b.shape === SH_BOXES || b.shape === SH_MODEL) return b.boxes(m).map(a => a.map(v => v / 16));
    if (b.shape === SH_CROSS || b.shape === SH_CROP) return [[0.15, 0, 0.15, 0.85, b.key === 'wheat' ? 0.25 + (m & 7) / 10 : 0.8, 0.85]];
    if (b.shape === SH_TORCH) {
      if (!m) return [[0.4, 0, 0.4, 0.6, 0.6, 0.6]];
      const D = DIRS[m]; const cx = 0.5 + D[0] * 0.35, cz = 0.5 + D[2] * 0.35;
      return [[cx - 0.15, 0.2, cz - 0.15, cx + 0.15, 0.8, cz + 0.15]];
    }
    if (b.shape === SH_CACTUS) return [[1 / 16, 0, 1 / 16, 15 / 16, 1, 15 / 16]];
    return [[0, 0, 0, 1, 1, 1]];
  }
  raycast(origin, dir, maxD, fluids) {
    const w = this.world;
    let x = Math.floor(origin[0]), y = Math.floor(origin[1]), z = Math.floor(origin[2]);
    const sx = Math.sign(dir[0]), sy = Math.sign(dir[1]), sz = Math.sign(dir[2]);
    const tdx = Math.abs(1 / dir[0]), tdy = Math.abs(1 / dir[1]), tdz = Math.abs(1 / dir[2]);
    let tx = (sx > 0 ? x + 1 - origin[0] : origin[0] - x) * tdx, ty = (sy > 0 ? y + 1 - origin[1] : origin[1] - y) * tdy, tz = (sz > 0 ? z + 1 - origin[2] : origin[2] - z) * tdz;
    for (let i = 0; i < 200; i++) {
      const id = w.getBlock(x, y, z);
      if (id && (!IS_FLUID[id] || (fluids && w.getMeta(x, y, z) === 0))) {
        const boxes = IS_FLUID[id] ? [[0, 0, 0, 1, 0.9, 1]] : this.selBoxes(x, y, z, id);
        let best = null;
        for (const bx of boxes) {
          const hit = rayBox(origin, dir, x + bx[0], y + bx[1], z + bx[2], x + bx[3], y + bx[4], z + bx[5]);
          if (hit && (!best || hit.t < best.t)) best = { ...hit, box: bx };
        }
        if (best && best.t <= maxD) return { pos: [x, y, z], face: best.face, t: best.t, point: [origin[0] + dir[0] * best.t, origin[1] + dir[1] * best.t, origin[2] + dir[2] * best.t], id, box: best.box };
      }
      if (tx < ty && tx < tz) { if (tx > maxD) break; x += sx; tx += tdx; }
      else if (ty < tz) { if (ty > maxD) break; y += sy; ty += tdy; }
      else { if (tz > maxD) break; z += sz; tz += tdz; }
    }
    return null;
  }
  rayEntity(origin, dir, maxD) {
    let best = null;
    for (const e of this.entities) {
      if (!(e instanceof Living) || e.deathTime > 0) continue;
      const b = e.box();
      const hit = rayBox(origin, dir, b[0] - 0.1, b[1] - 0.1, b[2] - 0.1, b[3] + 0.1, b[4] + 0.1, b[5] + 0.1);
      if (hit && hit.t <= maxD && (!best || hit.t < best.t)) best = { e, t: hit.t };
    }
    return best;
  }

  // ---- interaction ---------------------------------------------------------------------------
  reach() { return this.mode === 'creative' ? 5 : 4.5; }
  updateTarget() {
    const p = this.player;
    const eye = [p.pos[0], p.pos[1] + p.eye(), p.pos[2]];
    const dir = this.lookDir();
    this.target = this.raycast(eye, dir, this.reach(), false);
    const ent = this.rayEntity(eye, dir, this.mode === 'creative' ? 5 : 3);
    this.targetEntity = ent && (!this.target || ent.t < this.target.t) ? ent.e : null;
    return { eye, dir };
  }
  handleInteraction() {
    const p = this.player;
    if (this.ui.screen || p.deathTime > 0) { this.breaking = null; p.usingItem = null; return; }
    this.updateTarget();
    if (this.useCd > 0) this.useCd--;
    if (this.attackCd > 0) this.attackCd--;
    // continuous using (eating / bow)
    if (p.usingItem) {
      const held = p.inv.held;
      if (!this.mouseBtn[2] || !held || held.id !== p.usingItem) { if (p.usingItem === item('bow') && held && held.id === p.usingItem) this.releaseBow(); p.usingItem = null; p.useT = 0; }
      else {
        p.useT++;
        const def = ITEMS[held.id];
        if (def.food) {
          if (p.useT % 4 === 0 && p.useT > 6) { Sound.eat(p.pos); this.foodParticles(held.id); }
          if (p.useT >= 32) this.finishEating(held);
        }
      }
    }
    // breaking
    if (this.mouseBtn[0]) {
      if (this.targetEntity) {
        if (this.attackCd <= 0) this.attack(this.targetEntity);
        this.breaking = null;
      } else if (this.target) {
        const t = this.target;
        if (this.mode === 'creative') {
          if (this.attackCd <= 0) {
            const held = p.inv.held;
            if (!(held && ITEMS[held.id].tool && ITEMS[held.id].tool.type === 'sword')) this.breakBlock(...t.pos, false);
            this.attackCd = 5; p.swingT = 6;
          }
        } else {
          const key = t.pos.join(',');
          if (!this.breaking || this.breaking.key !== key) this.breaking = { key, pos: t.pos, progress: 0, id: t.id };
          const b = BLOCKS[t.id];
          if (b.hardness < 0 || this.attackCd > 0) { if (b.hardness < 0) this.breaking.progress = 0; }
          else {
            this.breaking.progress += this.breakSpeed(t.id);
            if (p.swingT <= 0) p.swingT = 6;
            if (this.tickCount % 4 === 0) { Sound.block('hit', b.sound, [t.pos[0] + 0.5, t.pos[1] + 0.5, t.pos[2] + 0.5]); this.particles.blockHit(...t.pos, t.face, t.id, this.world.getMeta(...t.pos)); }
            if (this.breaking.progress >= 1) { this.breakBlock(...t.pos, false); this.breaking = null; this.attackCd = 5; }
          }
        }
      } else { this.breaking = null; if (this.mouseClicked0) p.swingT = 6; }
    } else this.breaking = null;
    this.mouseClicked0 = false;
    // using / placing (hold to repeat)
    if (this.mouseBtn[2] && this.useCd <= 0 && !p.usingItem) {
      this.use();
    }
  }
  breakSpeed(id) {
    const p = this.player, b = BLOCKS[id];
    if (b.hardness === 0) return 1;
    const held = p.inv.held, def = held && ITEMS[held.id];
    let speed = 1, canHarvest = !b.needsTool;
    if (def && def.tool) {
      const t = def.tool;
      if (t.type === b.tool) { speed = t.speed; if (t.level >= b.level) canHarvest = true; }
      if (t.type === 'sword' && (b.key.endsWith('leaves') || b.key === 'cactus' || b.key === 'pumpkin' || b.key === 'melon')) speed = 1.5;
    }
    if (def && def.key === 'shears' && (b.key.endsWith('leaves') || b.key.endsWith('wool'))) { speed = b.key.endsWith('wool') ? 5 : 15; canHarvest = true; }
    if (b.tool === 'shears') canHarvest = true;
    let s = speed / b.hardness / (canHarvest ? 30 : 100);
    if (p.eyeInWater) s /= 5;
    if (!p.onGround && !p.flying) s /= 5;
    return s;
  }
  canHarvest(id, held) {
    const b = BLOCKS[id];
    if (!b.needsTool) return true;
    const def = held && ITEMS[held.id];
    if (b.tool === 'shears') return true;
    return !!(def && def.tool && def.tool.type === b.tool && def.tool.level >= b.level);
  }
  breakBlock(x, y, z, natural, silent) {
    const w = this.world, id = w.getBlock(x, y, z), meta = w.getMeta(x, y, z);
    if (!id || IS_FLUID[id]) return;
    const b = BLOCKS[id];
    const p = this.player, held = natural ? null : p.inv.held;
    if (!silent) { Sound.block('break', b.sound, [x + 0.5, y + 0.5, z + 0.5]); this.particles.blockBreak(x, y, z, id, meta); }
    else this.particles.blockBreak(x, y, z, id, meta);
    if (this.mode !== 'creative' || natural) {
      if (natural || this.canHarvest(id, held)) this.dropBlockLoot(x, y, z, id, meta, held);
      if (!natural) {
        p.exhaust(0.005);
        if (held && ITEMS[held.id].durability && b.hardness > 0) this.damageHeld(1);
        const xp = { coal_ore: [0, 2], deepslate_coal_ore: [0, 2], diamond_ore: [3, 7], deepslate_diamond_ore: [3, 7], redstone_ore: [1, 5], lapis_ore: [2, 5], emerald_ore: [3, 7] }[b.key];
        if (xp && this.canHarvest(id, held)) this.addXp(xp[0] + Math.floor(Math.random() * (xp[1] - xp[0] + 1)));
        this.stats.mined++;
      }
    }
    // door: remove other half
    if (b.door) { const oy = (meta & 8) ? y - 1 : y + 1; if (w.getBlock(x, oy, z) === id) w.setBlock(x, oy, z, 0, 0, 1); }
    if (b.bed) {
      const R = [4, 1, 5, 0][meta & 3], D2 = DIRS[(meta & 4) ? OPP[R] : R];
      const ox = x + D2[0], oz = z + D2[2];
      if (w.getBlock(ox, y, oz) === id) { if ((meta & 4) && this.mode !== 'creative' && !natural) this.dropBlockLoot(ox, y, oz, id, 0, null); w.setBlock(ox, y, oz, 0, 0, 1); }
    }
    // water fills in when breaking next to water? (handled by fluid ticks via notify)
    w.setBlock(x, y, z, 0, 0);
    if (id === 35 && w.getBlock(x, y - 1, z) !== 0 && this.mode !== 'creative' && !natural) w.setBlock(x, y, z, WATER, 0);
    if (!natural) this.flushMesh();
  }
  damageHeld(n) {
    const p = this.player, h = p.inv.held;
    if (!h || this.mode === 'creative') return;
    h.dmg = (h.dmg || 0) + n;
    if (h.dmg >= ITEMS[h.id].durability) { p.inv.slots[p.inv.sel] = null; Sound.block('break', 'wood', p.pos); this.ui.actionBar = { text: ITEMS[h.id].cn + ' 损坏了', t: performance.now(), color: '#FF5555' }; }
  }
  flushMesh() {
    // remesh dirty sections near the player immediately for responsive edits
    const p = this.player, pcx = Math.floor(p.pos[0]) >> 4, pcz = Math.floor(p.pos[2]) >> 4;
    for (let a = -2; a <= 2; a++) for (let b = -2; b <= 2; b++) {
      const ch = this.world.getChunk(pcx + a, pcz + b);
      if (!ch || !ch.dirty) continue;
      let ok = true;
      for (let i = -1; i <= 1 && ok; i++) for (let j = -1; j <= 1; j++) if (!this.world.getChunk(ch.cx + i, ch.cz + j)) { ok = false; break; }
      if (ok) this.meshChunk(ch);
    }
  }
  attack(e) {
    const p = this.player, held = p.inv.held, def = held && ITEMS[held.id];
    let dmg = def && def.attack ? def.attack : 1;
    const crit = p.vel[1] < -0.05 && !p.onGround && !p.inWater;
    if (crit) { dmg *= 1.5; for (let i = 0; i < 12; i++) this.particles.spawn('crit', e.pos[0] + (Math.random() - 0.5) * e.w, e.pos[1] + Math.random() * e.h, e.pos[2] + (Math.random() - 0.5) * e.w, (Math.random() - 0.5) * 0.3, Math.random() * 0.3, (Math.random() - 0.5) * 0.3); }
    const knock = p.sprinting ? 0.8 : 0.4;
    if (e.hurt(dmg, { type: 'player', attacker: p, knockFrom: p.pos, knock, player: true })) {
      if (def && def.durability) this.damageHeld(def.tool && def.tool.type === 'sword' ? 1 : 2);
      p.exhaust(0.1);
      if (p.sprinting) p.sprinting = false;
    }
    p.swingT = 6;
    this.attackCd = 10;
  }
  foodParticles(id) {
    const p = this.player, d = this.lookDir();
    for (let i = 0; i < 3; i++) {
      const pa = this.particles.spawn('block', p.pos[0] + d[0] * 0.5, p.pos[1] + p.eye() - 0.2, p.pos[2] + d[2] * 0.5, (Math.random() - 0.5) * 0.1, 0.1, (Math.random() - 0.5) * 0.1, { id: 1, layer: ITEMS[id].tex });
    }
  }
  finishEating(held) {
    const p = this.player, def = ITEMS[held.id];
    p.food = Math.min(20, p.food + def.food[0]);
    p.saturation = Math.min(p.food, p.saturation + def.food[1]);
    if (def.heal) p.health = Math.min(20, p.health + def.heal);
    if (def.key === 'rotten_flesh' && Math.random() < 0.8) p.food = Math.max(0, p.food - 1);
    Sound.burp(p.pos);
    if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
    p.usingItem = null; p.useT = 0;
    this.useCd = 4;
  }
  releaseBow() {
    const p = this.player, t = p.useT;
    let f = t / 20; f = (f * f + f * 2) / 3; if (f > 1) f = 1;
    if (f < 0.1) return;
    const has = this.mode === 'creative' || p.inv.count(item('arrow')) > 0;
    if (!has) return;
    if (this.mode !== 'creative') p.inv.remove(item('arrow'), 1);
    const d = this.lookDir(), sp = f * 3;
    const a = new Arrow(this, p.pos[0] + d[0] * 0.3, p.pos[1] + p.eye() - 0.1, p.pos[2] + d[2] * 0.3, [d[0] * sp, d[1] * sp, d[2] * sp], p);
    this.entities.push(a);
    Sound.bow(p.pos);
    this.damageHeld(1);
  }
  use() {
    const p = this.player, w = this.world, held = p.inv.held, def = held && ITEMS[held.id];
    const t = this.target;
    this.useCd = 4;
    // entity interaction
    if (this.targetEntity && held) {
      const e = this.targetEntity;
      if (def.key === 'shears' && e.type === 'sheep' && !e.sheared) {
        e.sheared = true;
        for (let i = 0, n = 1 + ((Math.random() * 3) | 0); i < n; i++) this.dropItem(e.pos[0], e.pos[1] + 1, e.pos[2], makeStack(128 + e.woolColor, 1));
        Sound.noise(e.pos, { freq: 3000, q: 3, dur: 0.15, vol: 0.4 });
        this.damageHeld(1); p.swingT = 6; return;
      }
      if (e.T && e.T.food && held.id === item(e.T.food) && !e.loveT && !e.baby) {
        e.loveT = 600;
        for (let i = 0; i < 6; i++) this.particles.spawn('heart', e.pos[0] + (Math.random() - 0.5), e.pos[1] + e.h + Math.random() * 0.5, e.pos[2] + (Math.random() - 0.5), 0, 0.02, 0);
        if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
        // breed with a nearby partner in love
        const mate = this.entities.find(o => o !== e && o instanceof Mob && o.type === e.type && o.loveT > 0 && o.distTo(e) < 8);
        if (mate) {
          e.loveT = 0; mate.loveT = 0;
          const baby = new Mob(this, e.type, (e.pos[0] + mate.pos[0]) / 2, e.pos[1], (e.pos[2] + mate.pos[2]) / 2).makeBaby();
          this.entities.push(baby);
          for (let i = 0; i < 8; i++) this.particles.spawn('heart', baby.pos[0] + (Math.random() - 0.5), baby.pos[1] + 1 + Math.random() * 0.5, baby.pos[2] + (Math.random() - 0.5), 0, 0.02, 0);
          this.addXp(1 + ((Math.random() * 7) | 0));
        }
        p.swingT = 6; return;
      }
    }
    // block interaction
    if (t && !p.sneaking) {
      const b = BLOCKS[t.id];
      const [x, y, z] = t.pos;
      if (b.onUse === 'crafting') { this.ui.open(new CraftingScreen(this, 3)); return; }
      if (b.onUse === 'furnace') { this.ui.open(new FurnaceScreen(this, this.getBlockEntity(x, y, z, 'furnace'))); return; }
      if (b.onUse === 'chest') {
        const be = this.getBlockEntity(x, y, z, 'chest');
        if (w.getMeta(x, y, z) & 8) { this.fillLoot(be, x, y, z); w.setMeta(x, y, z, w.getMeta(x, y, z) & 7); }
        this.ui.open(new ChestScreen(this, be)); Sound.door([x, y, z], true); return;
      }
      if (b.onUse === 'note') { const m = (w.getMeta(x, y, z) + 1) % 25; w.setMeta(x, y, z, m); Sound.note([x, y, z], m); this.particles.spawn('note', x + 0.5, y + 1.2, z + 0.5, 0, 0.02, 0, { col: [255, Math.round(m * 10), Math.round(255 - m * 10)] }); p.swingT = 6; return; }
      if (b.door) {
        const m = w.getMeta(x, y, z), base = (m & 8) ? y - 1 : y;
        const bm = w.getMeta(x, base, z) ^ 4;
        w.setMeta(x, base, z, bm); w.setMeta(x, base + 1, z, bm | 8);
        Sound.door([x, y, z], !!(bm & 4)); p.swingT = 6; this.flushMesh(); return;
      }
      if (b.bed) { this.trySleep(x, y, z); p.swingT = 6; return; }
      if (b.trapdoor) { w.setMeta(x, y, z, w.getMeta(x, y, z) ^ 4); Sound.door([x, y, z], true); p.swingT = 6; this.flushMesh(); return; }
      if (t.id === 26 && def && def.key === 'flint_and_steel') { w.setBlock(x, y, z, 0, 0); this.entities.push(new PrimedTNT(this, x, y, z)); this.damageHeld(1); p.swingT = 6; return; }
    }
    if (!held) return;
    if (def.armor) {
      const slot = 36 + def.armor.slot;
      const cur = p.inv.slots[slot];
      p.inv.slots[slot] = held; p.inv.slots[p.inv.sel] = cur;
      Sound.block('place', 'metal', p.pos); p.swingT = 6;
      if (def.key.startsWith('iron')) this.grant('armor');
      return;
    }
    // food
    if (def.food) { if (p.food < 20 || this.mode === 'creative' || def.key === 'golden_apple') { p.usingItem = held.id; p.useT = 0; } return; }
    if (def.key === 'bow') { if (this.mode === 'creative' || p.inv.count(item('arrow')) > 0) { p.usingItem = held.id; p.useT = 0; } return; }
    if (def.key === 'snowball' || def.key === 'egg') {
      const d = this.lookDir();
      const a = new Arrow(this, p.pos[0], p.pos[1] + p.eye() - 0.1, p.pos[2], [d[0] * 1.5, d[1] * 1.5, d[2] * 1.5], p);
      a.snowball = held.id; this.entities.push(a);
      if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
      Sound.bow(p.pos); p.swingT = 6; return;
    }
    // buckets
    if (def.key === 'bucket') {
      const ft = this.raycast([p.pos[0], p.pos[1] + p.eye(), p.pos[2]], this.lookDir(), this.reach(), true);
      if (ft && IS_FLUID[ft.id] && w.getMeta(...ft.pos) === 0) {
        w.setBlock(...ft.pos, 0, 0);
        const filled = makeStack(ft.id === WATER ? 'water_bucket' : 'lava_bucket');
        if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = filled; else if (p.inv.add(filled)) this.dropFromPlayer(filled); }
        Sound.splash(ft.pos); p.swingT = 6; this.flushMesh();
      }
      return;
    }
    if (!t) return;
    const [x, y, z] = t.pos, D = DIRS[t.face];
    // tools acting on blocks
    if (def.tool && def.tool.type === 'hoe' && (t.id === 2 || t.id === 3) && w.getBlock(x, y + 1, z) === 0) {
      w.setBlock(x, y, z, 63, 0); Sound.block('step', 'gravel', [x, y, z]); this.damageHeld(1); p.swingT = 6; this.flushMesh(); return;
    }
    if (def.key === 'bone_meal') {
      let ok = false;
      if (t.id === 64 && w.getMeta(x, y, z) < 7) { w.setMeta(x, y, z, Math.min(7, w.getMeta(x, y, z) + 2 + ((Math.random() * 3) | 0))); ok = true; }
      else if (t.id >= 65 && t.id <= 67) { if (Math.random() < 0.45) this.growTree(x, y, z, t.id); ok = true; }
      else if (t.id === 2) {
        for (let i = 0; i < 40; i++) {
          const nx = x + ((Math.random() * 7) | 0) - 3, nz = z + ((Math.random() * 7) | 0) - 3;
          if (w.getBlock(nx, y, nz) === 2 && w.getBlock(nx, y + 1, nz) === 0) w.setBlock(nx, y + 1, nz, Math.random() < 0.85 ? 39 : FLOWER_IDS[(Math.random() * 4) | 0], 0);
        }
        ok = true;
      }
      if (ok) {
        for (let i = 0; i < 12; i++) this.particles.spawn('bone_meal', x + Math.random(), y + 1 + Math.random() * 0.5, z + Math.random(), 0, 0.02, 0);
        if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
        p.swingT = 6; this.flushMesh();
      }
      return;
    }
    if (def.key === 'flint_and_steel') return;
    if (def.spawnEgg) {
      const m = new Mob(this, def.spawnEgg, x + D[0] + 0.5, y + Math.max(0, D[1]) + (D[1] < 0 ? -2 : 0), z + D[2] + 0.5);
      m.persistent = true; m.yaw = m.bodyYaw = p.yaw + Math.PI;
      this.entities.push(m);
      for (let i = 0; i < 8; i++) this.particles.spawn('poof', m.pos[0] + (Math.random() - 0.5), m.pos[1] + Math.random(), m.pos[2] + (Math.random() - 0.5), 0, 0.05, 0);
      if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
      p.swingT = 6; return;
    }
    // placement
    let placeId = null;
    if (def.block !== undefined && BLOCKS[def.block]) placeId = def.block;
    else if (def.places) placeId = BID[def.places];
    if (placeId == null) return;
    const pb = BLOCKS[placeId];
    // slab merging
    if (pb.slab && t.id === placeId) {
      const m = w.getMeta(x, y, z) & 3;
      if ((m === 0 && t.face === 3) || (m === 1 && t.face === 2)) { this.placeAt(x, y, z, placeId, 2, held, true); return; }
    }
    let px = x, py = y, pz = z;
    const tb = BLOCKS[t.id];
    if (!(tb.replaceable && !IS_FLUID[t.id] && t.id !== placeId)) { px += D[0]; py += D[1]; pz += D[2]; }
    if (py < 0 || py >= HEIGHT) return;
    const cur = w.getBlock(px, py, pz);
    if (pb.slab && cur === placeId) {
      const m = w.getMeta(px, py, pz) & 3;
      if (m !== 2) { this.placeAt(px, py, pz, placeId, 2, held, true); return; }
    }
    if (cur && !REPLACEABLE[cur]) return;
    if (IS_FLUID[placeId] && cur && IS_FLUID[cur] && w.getMeta(px, py, pz) === 0 && cur === placeId) return;
    // meta from placement context
    let meta = 0;
    const look = this.lookDir();
    const hdir = Math.abs(look[0]) > Math.abs(look[2]) ? (look[0] > 0 ? 1 : 0) : (look[2] > 0 ? 5 : 4);
    if (pb.facing) meta = OPP[hdir];
    if (pb.axis) meta = (t.face === 0 || t.face === 1) ? 1 : (t.face === 4 || t.face === 5) ? 2 : 0;
    if (pb.slab) { const fy = t.point[1] - Math.floor(t.point[1]); meta = t.face === 2 || (t.face !== 3 && fy > 0.5) ? 1 : 0; }
    if (pb.stairs) {
      meta = { 1: 0, 0: 1, 5: 2, 4: 3 }[hdir];
      const fy = t.point[1] - Math.floor(t.point[1]);
      if (t.face === 2 || (t.face !== 3 && fy > 0.5)) meta |= 4;
    }
    if (pb.trapdoor) { const fy = t.point[1] - Math.floor(t.point[1]); if (t.face === 2 || (t.face !== 3 && fy > 0.5)) meta |= 8; }
    if (placeId === 21) { if (t.face === 2) return; meta = t.face === 3 ? 0 : OPP[t.face]; }
    if (pb.ladder) { if (t.face < 2 && false) return; if (t.face === 2 || t.face === 3) return; meta = OPP[t.face]; }
    if (pb.key.endsWith('leaves')) meta = 4;
    if (pb.bed) {
      const rot = { 4: 0, 1: 1, 5: 2, 0: 3 }[hdir];
      const D2 = DIRS[hdir], hx = px + D2[0], hz = pz + D2[2];
      const hcur = w.getBlock(hx, py, hz);
      if (hcur && !REPLACEABLE[hcur]) return;
      if (!SOLIDCUBE[w.getBlock(px, py - 1, pz)] || !SOLIDCUBE[w.getBlock(hx, py - 1, hz)]) return;
      w.setBlock(hx, py, hz, placeId, rot | 4, 0);
      this.placeAt(px, py, pz, placeId, rot, held, false);
      return;
    }
    if (pb.door) {
      if (w.getBlock(px, py + 1, pz) !== 0 && !REPLACEABLE[w.getBlock(px, py + 1, pz)]) return;
      meta = { 1: 0, 0: 1, 5: 2, 4: 3 }[hdir];
    }
    if (pb.needsSupport || pb.door) { if (!this.supportedForPlace(px, py, pz, placeId, meta)) return; }
    // collision with entities
    if (pb.solid) {
      const boxes = pb.shape === SH_BOXES ? pb.boxes(meta) : [[0, 0, 0, 16, 16, 16]];
      for (const e of [p, ...this.entities]) {
        if (!(e instanceof Living) || e.deathTime > 0) continue;
        const eb = e.box();
        for (const bx of boxes) {
          if (eb[0] < px + bx[3] / 16 && eb[3] > px + bx[0] / 16 && eb[1] < py + bx[4] / 16 && eb[4] > py + bx[1] / 16 && eb[2] < pz + bx[5] / 16 && eb[5] > pz + bx[2] / 16) return;
        }
      }
    }
    if (IS_FLUID[placeId]) {
      if (cur && !IS_FLUID[cur]) this.dropBlockLoot(px, py, pz, cur, w.getMeta(px, py, pz), null);
      w.setBlock(px, py, pz, placeId, 0);
      if (this.mode !== 'creative') p.inv.slots[p.inv.sel] = makeStack('bucket');
      Sound.splash([px, py, pz]); p.swingT = 6; this.flushMesh();
      return;
    }
    if (pb.door) w.setBlock(px, py + 1, pz, placeId, meta | 8, 0);
    this.placeAt(px, py, pz, placeId, meta, held, false);
  }
  supportedForPlace(x, y, z, id, meta) {
    const w = this.world, old = w.getBlock(x, y, z);
    if (BLOCKS[id].door) return !!SOLIDCUBE[w.getBlock(x, y - 1, z)];
    return this.supported(x, y, z, id, meta);
  }
  placeAt(x, y, z, id, meta, held, merge) {
    const p = this.player, w = this.world;
    w.setBlock(x, y, z, id, meta);
    Sound.block('place', BLOCKS[id].sound, [x + 0.5, y + 0.5, z + 0.5]);
    if (this.mode !== 'creative') { held.count--; if (!held.count) p.inv.slots[p.inv.sel] = null; }
    p.swingT = 6;
    this.stats.placed++;
    if (this.stats.placed >= 100) this.grant('build');
    if (BLOCKS[id].gravity) this.onNeighborUpdate(x, y, z, id);
    this.flushMesh();
  }
  pickBlock() {
    const t = this.target; if (!t) return;
    let id = t.id;
    const b = BLOCKS[id];
    const map = { 2: 2, 117: 20, 64: 'wheat_seeds', 69: 'oak_door', 38: 'sugar_cane', 63: 3 };
    if (map[id] !== undefined) id = typeof map[id] === 'string' ? item(map[id]) : map[id];
    if (!ITEMS[id]) return;
    const inv = this.player.inv;
    for (let i = 0; i < 9; i++) if (inv.slots[i] && inv.slots[i].id === id) { inv.sel = i; return; }
    if (this.mode === 'creative') {
      let slot = inv.slots.findIndex((s, i) => i < 9 && !s);
      if (slot < 0) slot = inv.sel;
      inv.slots[slot] = { id, count: maxStack(id), dmg: 0 }; inv.sel = slot;
    } else {
      const i = inv.slots.findIndex((s, j) => j >= 9 && j < 36 && s && s.id === id);
      if (i >= 0) { const tmp = inv.slots[inv.sel]; inv.slots[inv.sel] = inv.slots[i]; inv.slots[i] = tmp; }
    }
  }

  // ---- rendering ---------------------------------------------------------------------------
  env(a) {
    const cel = this.celestial();
    const c = Math.cos(cel * Math.PI * 2);
    const br = clamp(c * 2 + 0.5, 0, 1);
    const rain = this.weather;
    let day = lerp(0.1, 1, br) * (1 - rain * 0.35);
    let sky = [0.47 * br, 0.65 * br, 1.0 * br].map((v, i) => v * (1 - rain * 0.6) + [0.01, 0.01, 0.03][i] * (1 - br));
    let fog = [0.72 * br + 0.02, 0.83 * br + 0.03, 1.0 * br + 0.06].map(v => v * (1 - rain * 0.4));
    if (rain > 0) { const g = (sky[0] + sky[1] + sky[2]) / 3; sky = sky.map(v => lerp(v, g, rain * 0.6)); const fg = (fog[0] + fog[1] + fog[2]) / 3; fog = fog.map(v => lerp(v, fg * 0.8, rain * 0.6)); }
    // sunrise / sunset tint
    const sunAlt = Math.cos(cel * Math.PI * 2);
    let sunsetA = 0;
    if (Math.abs(sunAlt) < 0.4) sunsetA = (1 - Math.abs(sunAlt) / 0.4) * 0.85;
    const sunset = [1.0, 0.45 + 0.2 * Math.max(0, sunAlt), 0.2, sunsetA * (1 - rain)];
    // fog color looks toward sun at sunset
    const p = this.player;
    const sunDir = [-Math.sin(cel * Math.PI * 2), Math.cos(cel * Math.PI * 2), 0];
    if (sunsetA > 0) {
      const ld = this.lookDir();
      const facing = Math.max(0, ld[0] * Math.sign(sunDir[0]));
      fog = fog.map((v, i) => lerp(v, sunset[i], facing * sunsetA * 0.6));
    }
    const R = this.renderer.renderDist * 16;
    let fogNear = R * 0.6, fogFar = R * 0.95;
    if (rain > 0) { fogNear *= 1 - rain * 0.4; fogFar *= 1 - rain * 0.2; }
    const nightTint = [0.55, 0.62, 0.95];
    if (this.headInWater) {
      const wl = Math.max(0.12, day * ((this.world.getLight(Math.floor(p.pos[0]), Math.floor(p.pos[1] + 1.6), Math.floor(p.pos[2])) >> 4) / 15));
      fog = [0.05 * wl, 0.18 * wl, 0.45 * wl]; fogNear = -4; fogFar = 28;
    } else if (this.headInLava) { fog = [0.6, 0.1, 0]; fogNear = 0; fogFar = 2; }
    // cave darkness: below y=50 with little sky light, darken fog
    if (!this.headInWater && this.state === 'play') {
      const sl = this.world.skyAt(Math.floor(p.pos[0]), Math.floor(p.pos[1] + 1.6), Math.floor(p.pos[2]));
      const k = clamp(sl / 10, 0.25, 1);
      fog = fog.map(v => v * k + (1 - k) * 0.02);
    }
    const moonPhase = Math.floor(this.worldTime / 24000) % 8;
    return {
      tick: this.worldTime + a, day, fogColor: fog, skyColor: sky, sunDir, sunset, voidColor: [fog[0] * 0.3, fog[1] * 0.3, fog[2] * 0.4],
      celestial: cel, starBright: clamp((0.25 - c) * 2, 0, 1) * 0.9 * (1 - rain), sunAlpha: 1 - rain, rain, moonPhase,
      fogNear, fogFar, nightTint, cloudColor: [lerp(0.15, 1, br) * (1 - rain * 0.4), lerp(0.15, 1, br) * (1 - rain * 0.4), lerp(0.2, 1, br) * (1 - rain * 0.35)],
    };
  }

  render(a, now) {
    const r = this.renderer, gl = r.gl;
    r.resize();
    gl.viewport(0, 0, r.canvas.width, r.canvas.height);
    if (!this.world || this.state === 'boot') { gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT); this.ui.draw(); return; }
    const p = this.player;
    let eye, yaw, pitch, fov = this.settings.fov;
    if (this.state === 'title' || this.state === 'loading') {
      eye = this.titleCam || [0, 100, 0]; yaw = this.titleYaw || 0; pitch = -0.15;
      if (this.state === 'loading') { gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); this.ui.draw(); return; }
    } else {
      const ip = p.ipos(a);
      const eyeH = this.sleeping ? 0.2 : p.sneaking && !p.flying ? 1.27 : 1.62;
      this.smoothEye = this.smoothEye == null ? eyeH : lerp(this.smoothEye, eyeH, 0.4);
      eye = [ip[0], ip[1] + this.smoothEye, ip[2]];
      yaw = p.yaw; pitch = p.pitch;
      if (this.sleeping) { yaw = [0, -Math.PI / 2, Math.PI, Math.PI / 2][this.sleeping.rot] + Math.PI; pitch = 0.5; }
      if (p.deathTime > 0) eye[1] -= Math.min(1.2, p.deathTime / 20);
      if (p.sprinting) fov *= 1.12;
      if (p.flying && p.sprinting) fov *= 1.05;
      if (this.headInWater) fov *= 0.9;
      if (p.usingItem === item('bow')) fov *= 1 - Math.min(1, p.useT / 20) * 0.15;
    }
    this.curFov = lerp(this.curFov || fov, fov, 0.25);
    const env = this.env(a);
    // camera transform extras: hurt tilt, bobbing, shake, third person
    let camPos = eye;
    if (this.state === 'play' && this.perspective > 0) {
      const d = this.perspective === 1 ? 4 : -4;
      const look = [-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch)];
      const back = [-look[0] * d, -look[1] * d, -look[2] * d];
      let dist = Math.abs(d);
      const hit = this.raycast(eye, [Math.sign(d) * -look[0], Math.sign(d) * -look[1], Math.sign(d) * -look[2]], dist, false);
      if (hit) dist = Math.max(0.5, hit.t - 0.3);
      camPos = [eye[0] - look[0] * dist * Math.sign(d), eye[1] - look[1] * dist * Math.sign(d), eye[2] - look[2] * dist * Math.sign(d)];
      if (this.perspective === 2) { yaw += Math.PI; pitch = -pitch; }
    }
    const shake = this.shake || 0;
    this.shake = shake * 0.9;
    r.setCamera(camPos, yaw + (Math.random() - 0.5) * shake * 0.05, pitch + (Math.random() - 0.5) * shake * 0.05, this.curFov, (v) => {
      if (this.state !== 'play') return;
      if (p.hurtTime > 0) { const h = (p.hurtTime - a) / 10; M4.rotZ(v, Math.sin(h * h * h * h * Math.PI) * 0.25); }
      if (p.deathTime > 0) M4.rotZ(v, Math.min(1, p.deathTime / 20) * 0.6);
      if (this.settings.bobbing && this.perspective === 0) {
        const bob = lerp(p.prevBob, p.bob, a) * Math.PI * 0.6, amt = lerp(p.prevBobAmt || 0, p.bobAmt, a);
        M4.translate(v, Math.sin(bob) * amt * 0.5, -Math.abs(Math.cos(bob) * amt), 0);
        M4.rotZ(v, Math.sin(bob) * amt * 0.05);
        M4.rotX(v, Math.abs(Math.cos(bob - 0.2) * amt) * 0.08);
      }
    });
    if (this.headInWater || this.headInLava) { gl.clearColor(env.fogColor[0], env.fogColor[1], env.fogColor[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); }
    else { gl.clear(gl.DEPTH_BUFFER_BIT); r.drawSky(env); }
    r.drawTerrain(env);
    this.drawEntities(a, env);
    if (this.state === 'play') this.drawSelection(env);
    // particles
    const right = [Math.cos(yaw), 0, -Math.sin(yaw)];
    const up = [Math.sin(yaw) * Math.sin(pitch), Math.cos(pitch), Math.cos(yaw) * Math.sin(pitch)];
    this.particles.build(r.particleBuf, a, camPos, right, up);
    r.drawDynamic(r.particleBuf, r.particleGL, env, { noCull: true });
    r.drawTranslucent(env);
    r.drawClouds(env, camPos[0], camPos[2]);
    if (this.state === 'play') {
      this.drawWeather(env, a);
      if (this.perspective === 0 && p.deathTime === 0) this.drawHand(a, env);
    }
    this.ui.draw();
  }

  drawSelection(env) {
    const r = this.renderer, t = this.target;
    if (!t || this.ui.hideHud || this.perspective === 2) return;
    const [x, y, z] = t.pos;
    const v = [], e = 0.002;
    for (const bx of this.selBoxes(x, y, z, t.id)) {
      const x0 = x + bx[0] - e, y0 = y + bx[1] - e, z0 = z + bx[2] - e, x1 = x + bx[3] + e, y1 = y + bx[4] + e, z1 = z + bx[5] + e;
      const L = (a, b) => v.push(a[0], a[1], a[2], 0, 0, 0, 1, b[0], b[1], b[2], 0, 0, 0, 1);
      const c = [[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1], [x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]];
      for (let i = 0; i < 4; i++) { L(c[i], c[(i + 1) % 4]); L(c[i + 4], c[(i + 1) % 4 + 4]); L(c[i], c[i + 4]); }
    }
    r.gl.enable(r.gl.DEPTH_TEST);
    r.drawLines(v, [0, 0, 0, 0.45]);
    // crack overlay
    if (this.breaking && this.breaking.progress > 0) {
      const stage = Math.min(9, Math.floor(this.breaking.progress * 10));
      const mb = r.miscBuf; mb.reset();
      const layer = T('destroy_stage_' + stage);
      const [bx, by, bz] = this.breaking.pos;
      for (const box of this.selBoxes(bx, by, bz, this.world.getBlock(bx, by, bz))) {
        for (let f = 0; f < 6; f++) {
          const F = FACES[f];
          for (const c of F.c) {
            const px = c[0] ? box[3] : box[0], py = c[1] ? box[4] : box[1], pz = c[2] ? box[5] : box[2];
            const uv = F.uv(px, py, pz);
            mb.v(bx + px + (c[0] ? 0.003 : -0.003), by + py + (c[1] ? 0.003 : -0.003), bz + pz + (c[2] ? 0.003 : -0.003), uv[0], uv[1], layer, 0, 255, 255, 255, 255, 255, 255, 0);
          }
        }
      }
      r.drawDynamic(mb, r.miscGL, { ...env, day: 1, fogNear: 1e8, fogFar: 2e8 }, { blend: 'mul', alphaTest: 0.01 });
    }
  }

  drawEntities(a, env) {
    const r = this.renderer, gl = r.gl;
    gl.enable(gl.DEPTH_TEST); gl.enable(gl.CULL_FACE);
    const cam = r.camPos;
    const list = this.entities.slice();
    if (this.state === 'play' && this.perspective > 0) list.push(this.player);
    for (const e of list) {
      const ip = e.ipos(a);
      const dx = ip[0] - cam[0], dz = ip[2] - cam[2];
      if (dx * dx + dz * dz > (r.renderDist * 16) ** 2) continue;
      if (!aabbInFrustum(r.planes, ip[0] - 2, ip[1] - 1, ip[2] - 2, ip[0] + 2, ip[1] + 3, ip[2] + 2)) continue;
      const l = e.lightAt();
      if (e instanceof Mob || e === this.player) this.drawMob(e, ip, a, env, l);
      else if (e instanceof ItemEntity) {
        const st = e.stack, def = ITEMS[st.id];
        const t = (e.age + a) / 20;
        const bob = Math.sin(t * 2 + e.bobOff) * 0.1 + 0.1;
        const n = st.count > 16 ? 3 : st.count > 1 ? 2 : 1;
        for (let i = 0; i < n; i++) {
          const m = M4.create(); M4.translate(m, ip[0] + i * 0.06, ip[1] + bob + 0.05 + i * 0.03, ip[2] + i * 0.04); M4.rotY(m, t + e.bobOff);
          this.drawItemModel(st.id, m, l, env, 'ground');
        }
      } else if (e instanceof FallingBlock || e instanceof PrimedTNT) {
        const id = e instanceof PrimedTNT ? 26 : e.blockId;
        const mb = this.blockMesh(id, e.meta || 0);
        const m = M4.create();
        let s = 1;
        if (e instanceof PrimedTNT && e.fuse < 10) s = 1 + (1 - e.fuse / 10) * 0.2;
        M4.translate(m, ip[0], ip[1] + 0.49, ip[2]); M4.scale(m, s, s, s); M4.translate(m, -0.5, -0.49, -0.5);
        r.drawMesh(mb, m, env, l);
        if (e instanceof PrimedTNT && (e.fuse / 5 | 0) % 2 === 0) {
          gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE); gl.depthFunc(gl.LEQUAL);
          r.drawMesh(mb, m, env, [1, 1], { color: [1, 1, 1, 0.5] });
          gl.disable(gl.BLEND); gl.depthFunc(gl.LESS);
        }
      } else if (e instanceof Arrow) {
        const m = M4.create(); M4.translate(m, ip[0], ip[1], ip[2]);
        M4.rotY(m, e.yaw + Math.PI / 2); M4.rotZ(m, e.pitch - Math.PI / 4);
        M4.scale(m, 0.7, 0.7, 0.7); M4.translate(m, -0.5, -0.5, 0);
        const lay = e.snowball ? ITEMS[e.snowball].tex : T('i_arrow');
        r.drawMesh(this.spriteMesh(lay), m, env, l);
      }
    }
  }
  drawMob(e, ip, a, env, l) {
    const r = this.renderer, gl = r.gl;
    const isPlayer = e === this.player;
    const model = isPlayer ? 'steve' : e.T.model;
    const m = M4.create();
    M4.translate(m, ip[0], ip[1], ip[2]);
    let by = isPlayer ? e.yaw : e.bodyYaw;
    if (!isPlayer && e.prevBodyYaw !== undefined) { let d = e.bodyYaw - e.prevBodyYaw; while (d > Math.PI) d -= Math.PI * 2; while (d < -Math.PI) d += Math.PI * 2; by = e.prevBodyYaw + d * a; }
    M4.rotY(m, by);
    if (e.deathTime > 0) M4.rotZ(m, Math.min(1, Math.sqrt((e.deathTime + a) / 20)) * Math.PI / 2);
    let sc = isPlayer ? 0.9375 : e.T.scale;
    if (e.type === 'creeper' && e.fuse >= 0) { const f = lerp(e.prevFuse, e.fuse, a) / 28; const s = 1 + Math.sin(f * 100) * f * 0.01; sc *= (1 + f * 0.4) * s; }
    if (e.baby) sc *= 0.5;
    M4.scale(m, sc, sc, sc);
    const poses = isPlayer ? this.playerPose(a) : e.pose(a);
    let overlay = e.hurtTime > 0 || e.deathTime > 0 ? [1, 0, 0, 0.4] : null;
    if (e.type === 'creeper' && e.fuse >= 0 && (e.fuse / 4 | 0) % 2 === 0) overlay = [1, 1, 1, 0.5];
    const lt = [l[0], l[1]];
    if (e.fire > 0) lt[1] = 1;
    let heldId = null;
    if (isPlayer && e.inv.held) heldId = e.inv.held.id;
    else if (e.type === 'skeleton') heldId = item('bow');
    const attach = heldId != null ? { rarm: (am) => {
      const def = ITEMS[heldId], b = def.block !== undefined ? BLOCKS[def.block] : null;
      const flat = !b || def.flat || b.shape === SH_CROSS || b.shape === SH_TORCH || b.ladder || b.door;
      M4.translate(am, 0, -9 / 16, -1 / 16);
      if (flat) {
        M4.rotY(am, Math.PI / 2);
        if (def.tool || def.key === 'stick' || def.key === 'bow') { if (def.key === 'bow') M4.rotZ(am, -0.6); M4.scale(am, 0.8, 0.8, 0.8); M4.translate(am, -0.3, -0.3, 0); }
        else { M4.scale(am, 0.5, 0.5, 0.5); M4.translate(am, -0.5, -0.4, 0); }
      } else { M4.scale(am, 0.375, 0.375, 0.375); M4.translate(am, -0.5, -0.8, -0.5); }
      this.drawItemModel(heldId, am, lt, env, 'hand');
    } } : null;
    r.drawModel(model, model === 'steve' ? 'steve' : e.type === 'sheep' ? 'sheep' : model, m, poses, lt, env, { overlay, attach });
    if (e.type === 'sheep' && !e.sheared) {
      const col = WOOL_RGB[e.woolColor] || [1, 1, 1];
      r.drawModel('sheep_wool', 'sheep_wool', m, poses, lt, env, { overlay, tint: col });
    }
    if (e.type === 'spider') { gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); gl.depthFunc(gl.LEQUAL); r.drawModel('spider', 'spider_eyes', m, poses, [1, 1], env, {}); gl.disable(gl.BLEND); gl.depthFunc(gl.LESS); }
    // shadow on the ground below
    if (e.deathTime === 0) {
      const fx = Math.floor(ip[0]), fz = Math.floor(ip[2]);
      let gy = Math.floor(ip[1] + 0.01);
      for (let k = 0; k < 4 && gy > 0 && !SOLIDCUBE[this.world.getBlock(fx, gy - 1, fz)] && !BLOCKS[this.world.getBlock(fx, gy - 1, fz)].solid; k++) gy--;
      const h = ip[1] - gy;
      if (h < 3) r.drawShadow(ip[0], gy, ip[2], e.w * 0.75 * (1 - h / 6), 0.55 * (1 - h / 3));
    }
  }
  playerPose(a) {
    const p = this.player;
    const swing = lerp(p.limbSwing - p.limbAmt, p.limbSwing, a) * 0.6662, amt = p.limbAmt;
    const l = Math.cos(swing) * 1.4 * amt, r = -l;
    const sw = p.swingT > 0 ? Math.sin((6 - p.swingT + a) / 6 * Math.PI) : 0;
    return { head: { rx: p.pitch }, rarm: { rx: r * 0.7 - sw * 1.2 }, larm: { rx: l * 0.7 }, rleg: { rx: l }, lleg: { rx: r } };
  }
  blockMesh(id, meta) {
    if (!this._bm) this._bm = new Map();
    const k = id * 256 + meta;
    let m = this._bm.get(k);
    if (!m) { m = Mesher.single(id, meta); this._bm.set(k, m); }
    return m;
  }
  spriteMesh(layer) { return itemSpriteMesh(layer); }
  drawItemModel(id, m, l, env, ctx) {
    const def = ITEMS[id], r = this.renderer;
    const b = def.block !== undefined ? BLOCKS[def.block] : null;
    const flat = !b || def.flat || b.shape === SH_CROSS || b.shape === SH_TORCH || b.ladder || b.door;
    if (flat) {
      const layer = b ? (def.flat ? T(def.flat) : b.tex[0]) : def.tex;
      const mm = new Float32Array(m);
      if (ctx === 'ground') { M4.scale(mm, 0.5, 0.5, 0.5); M4.translate(mm, -0.5, 0, 0); }
      r.drawMesh(this.spriteMesh(layer), mm, env, l);
    } else {
      const mm = new Float32Array(m);
      if (ctx === 'ground') { M4.scale(mm, 0.25, 0.25, 0.25); M4.translate(mm, -0.5, 0, -0.5); }
      let meta = b.facing ? 5 : 0; if (b.stairs) meta = 1;
      if (b.bed) { r.drawMesh(this.bedMesh || (this.bedMesh = Mesher.multi([[def.block, 0, 0, 0, 0], [def.block, 4, 0, 0, -1]])), mm, env, l); return; }
      r.drawMesh(this.blockMesh(def.block, meta), mm, env, l);
    }
  }
  drawHand(a, env) {
    const r = this.renderer, gl = r.gl, p = this.player;
    gl.clear(gl.DEPTH_BUFFER_BIT);
    const proj = M4.create();
    M4.perspective(proj, 70 * Math.PI / 180, r.canvas.width / r.canvas.height, 0.05, 10);
    const savedVP = r.vp, savedCam = r.camPos;
    r.vp = proj; r.camPos = [0, 0, 0];
    const held = p.inv.held;
    const hid = held ? held.id : -1;
    if (this.lastHeld !== hid) { this.equipT = 1; this.lastHeld = hid; }
    this.equipT = Math.max(0, (this.equipT || 0) - 0.12);
    const eq = this.equipT;
    const sw = p.swingT > 0 ? clamp((6 - p.swingT + a) / 6, 0, 1) : 0;
    const D = Math.PI / 180, PI = Math.PI;
    const m = M4.create();
    // view bobbing applied to hand as in vanilla (slight)
    if (this.settings.bobbing) {
      const bob = lerp(p.prevBob, p.bob, a) * PI * 0.6, amt = lerp(p.prevBobAmt || 0, p.bobAmt, a);
      M4.translate(m, Math.sin(bob) * amt * 0.5, -Math.abs(Math.cos(bob) * amt), 0);
    }
    const lt = p.lightAt();
    const env2 = { ...env, fogNear: 1e8, fogFar: 2e8 };
    if (!held) {
      const f1 = Math.sqrt(sw);
      M4.translate(m, -0.3 * Math.sin(f1 * PI) + 0.64, 0.4 * Math.sin(f1 * PI * 2) - 0.6 - eq * 0.6, -0.4 * Math.sin(sw * PI) - 0.72);
      M4.rotY(m, 45 * D);
      M4.rotY(m, Math.sin(f1 * PI) * 70 * D);
      M4.rotZ(m, Math.sin(sw * sw * PI) * -20 * D);
      M4.translate(m, -1, 3.6, 3.5);
      M4.rotZ(m, 120 * D); M4.rotX(m, 200 * D); M4.rotY(m, -135 * D);
      M4.translate(m, 5.6, 0, 0);
      M4.translate(m, -6 / 16, 2 / 16, 0);
      M4.rotZ(m, PI);
      r.drawModel('arm', 'steve', m, null, lt, env2, { vp: proj });
    } else {
      const def = ITEMS[held.id];
      const b = def.block !== undefined ? BLOCKS[def.block] : null;
      const flat = !b || def.flat || b.shape === SH_CROSS || b.shape === SH_TORCH || b.ladder || b.door;
      const eating = p.usingItem && def.food;
      const bowing = p.usingItem && def.key === 'bow';
      if (eating) {
        const left = 32 - p.useT - a + 1, f1 = left / 32;
        if (f1 < 0.8) M4.translate(m, 0, Math.abs(Math.cos(left / 4 * PI) * 0.1), 0);
        const f3 = 1 - Math.pow(Math.max(0, f1), 27);
        M4.translate(m, f3 * 0.6, f3 * -0.5, 0);
        M4.rotY(m, f3 * 90 * D); M4.rotX(m, f3 * 10 * D); M4.rotZ(m, f3 * 30 * D);
        M4.translate(m, 0.56, -0.52 - eq * 0.6, -0.72);
      } else if (bowing) {
        M4.translate(m, 0.56, -0.52 - eq * 0.6, -0.72);
        M4.translate(m, -0.2785682, 0.18344387, 0.15731531);
        M4.rotX(m, -13.935 * D); M4.rotY(m, 35.3 * D); M4.rotZ(m, -9.785 * D);
        let f = (p.useT + a) / 20; f = (f * f + f * 2) / 3; if (f > 1) f = 1;
        if (f > 0.1) M4.translate(m, 0, Math.sin((p.useT - 0.1) * 1.3) * (f - 0.1) * 0.004, 0);
        M4.translate(m, 0, 0, f * 0.04);
        M4.scale(m, 1, 1, 1 + f * 0.2);
        M4.rotY(m, -45 * D);
      } else {
        const f1 = Math.sqrt(sw);
        M4.translate(m, -0.4 * Math.sin(f1 * PI), 0.2 * Math.sin(f1 * PI * 2), -0.2 * Math.sin(sw * PI));
        M4.translate(m, 0.56, -0.52 - eq * 0.6, -0.72);
        const f = Math.sin(sw * sw * PI);
        M4.rotY(m, (45 + f * -20) * D);
        M4.rotZ(m, f * -20 * D * Math.sin(f1 * PI));
        M4.rotX(m, Math.sin(f1 * PI) * -80 * D);
        M4.rotY(m, -45 * D);
      }
      if (flat) {
        M4.translate(m, 1.13 / 16, 3.2 / 16, 1.13 / 16);
        M4.rotX(m, 0); M4.rotY(m, -90 * D); M4.rotZ(m, 25 * D);
        M4.scale(m, 0.68, 0.68, 0.68);
        M4.translate(m, -0.5, -0.5, 0);
        let layer = b ? (def.flat ? T(def.flat) : b.tex[0]) : def.tex;
        if (bowing) { const f = p.useT; layer = T(f >= 18 ? 'i_bow_pulling_2' : f >= 13 ? 'i_bow_pulling_1' : 'i_bow_pulling_0'); }
        r.drawMesh(this.spriteMesh(layer), m, env2, lt);
      } else {
        M4.rotY(m, 45 * D);
        M4.scale(m, 0.4, 0.4, 0.4);
        M4.translate(m, -0.5, -0.5, -0.5);
        let meta = b.facing ? 5 : 0; if (b.stairs) meta = 1;
        if (b.bed) { M4.translate(m, 0.25, 0.2, 0.5); M4.scale(m, 0.6, 0.6, 0.6); r.drawMesh(this.bedMesh || (this.bedMesh = Mesher.multi([[def.block, 0, 0, 0, 0], [def.block, 4, 0, 0, -1]])), m, env2, lt); }
        else r.drawMesh(this.blockMesh(def.block, meta), m, env2, lt);
      }
    }
    r.vp = savedVP; r.camPos = savedCam;
  }
  drawWeather(env, a) {
    if (this.weather < 0.05) return;
    const r = this.renderer, gl = r.gl, p = this.player, w = this.world;
    const mb = r.miscBuf; mb.reset();
    const px = Math.floor(p.pos[0]), pz = Math.floor(p.pos[2]), py = p.pos[1];
    const t = (this.worldTime + a);
    const layerRain = T('white');
    const v = [];
    for (let dx = -10; dx <= 10; dx++) for (let dz = -10; dz <= 10; dz++) {
      if (dx * dx + dz * dz > 100) continue;
      const x = px + dx, z = pz + dz;
      let top = Math.floor(py) + 12;
      let y = top;
      while (y > py - 12 && !BLOCKS[w.getBlock(x, y, z)].solid && !IS_FLUID[w.getBlock(x, y, z)]) y--;
      const y0 = Math.max(y + 1, py - 8), y1 = py + 12;
      if (y0 >= y1) continue;
      const cold = this.isCold(x, z);
      v.push([x, z, y0, y1, cold]);
    }
    // draw as textured vertical quads facing the camera using the rain/snow textures
    const T2 = r.progTex;
    gl.useProgram(T2.p);
    gl.uniformMatrix4fv(T2.u.u_vp, false, r.vp);
    gl.uniformMatrix4fv(T2.u.u_model, false, r.identity);
    const data = [];
    for (const [x, z, y0, y1, cold] of v) {
      if (cold !== this._weatherCold) { }
      const cx = x + 0.5, cz = z + 0.5;
      const ddx = cx - p.pos[0], ddz = cz - p.pos[2], d = Math.hypot(ddx, ddz) || 1;
      const nx = -ddz / d * 0.5, nz = ddx / d * 0.5;
      const sp = cold ? 0.02 : 0.12;
      const off = (t * sp + (hash3(x, 0, z, 3) * 4)) % 4;
      const u0 = hash3(x, 1, z, 3) * 4;
      data.push([cx - nx, y0, cz - nz, u0, (y1 + off) / 4, cx + nx, y0, cz + nz, u0 + 0.25, (y1 + off) / 4, cx + nx, y1, cz + nz, u0 + 0.25, (y0 + off) / 4, cx - nx, y1, cz - nz, u0, (y0 + off) / 4, cold]);
    }
    for (const cold of [false, true]) {
      const arr = [];
      for (const d of data) if (d[20] === cold) {
        arr.push(d[0], d[1], d[2], d[3], cold ? d[4] * 0.3 : -d[4], d[5], d[6], d[7], d[8], cold ? d[9] * 0.3 : -d[9], d[10], d[11], d[12], d[13], cold ? d[14] * 0.3 : -d[14], d[15], d[16], d[17], d[18], cold ? d[19] * 0.3 : -d[19]);
      }
      if (!arr.length) continue;
      if (!this.weatherVao) {
        this.weatherVao = gl.createVertexArray(); gl.bindVertexArray(this.weatherVao);
        this.weatherBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.weatherBuf);
        gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 20, 0);
        gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 20, 12);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, r.quadIdx);
      }
      gl.bindVertexArray(this.weatherVao);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.weatherBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.DYNAMIC_DRAW);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, r.entityTex[cold ? 'snowfall' : 'rain']);
      const br = env.day;
      gl.uniform4f(T2.u.u_color, br, br, br, this.weather * 0.8);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); gl.disable(gl.CULL_FACE);
      gl.drawElements(gl.TRIANGLES, arr.length / 20 * 6, gl.UNSIGNED_INT, 0);
      gl.depthMask(true); gl.enable(gl.CULL_FACE); gl.disable(gl.BLEND);
    }
    // rain sound
    if (this.tickCount % 10 === 0 && this.weather > 0.3 && !this.isCold(px, pz)) Sound.noise(null, { type: 'highpass', freq: 2500, dur: 0.6, vol: 0.05 * this.weather, attack: 0.2 });
  }

  // ---- pointer -------------------------------------------------------------------------------
  capturePointer() {
    if (this.state !== 'play') return;
    const c = this.canvas, swallow = (p) => { if (p && p.catch) p.catch(() => { }); };
    try {
      const r = c.requestPointerLock({ unadjustedMovement: true });
      if (r && r.catch) r.catch(() => { try { swallow(c.requestPointerLock()); } catch (e) { } });
    } catch (e) { try { swallow(c.requestPointerLock()); } catch (e2) { } }
  }
  releasePointer() { if (document.pointerLockElement) document.exitPointerLock(); }
}

const WOOL_RGB = [[1, 1, 1], [0.95, 0.55, 0.2], [0.75, 0.3, 0.75], [0.4, 0.6, 0.85], [0.9, 0.85, 0.2], [0.5, 0.8, 0.2], [0.95, 0.6, 0.7], [0.3, 0.3, 0.3], [0.6, 0.6, 0.6], [0.2, 0.55, 0.6], [0.5, 0.25, 0.7], [0.2, 0.3, 0.65], [0.45, 0.3, 0.2], [0.35, 0.45, 0.2], [0.65, 0.2, 0.2], [0.1, 0.1, 0.1]];

Player.prototype.loadedHere = function () {
  const w = this.world, x = Math.floor(this.pos[0]), z = Math.floor(this.pos[2]);
  return w.isLoaded(x, z) && w.getChunk(x >> 4, z >> 4).lit;
};

function rayBox(o, d, x0, y0, z0, x1, y1, z1) {
  let tmin = -Infinity, tmax = Infinity, face = -1;
  const mins = [x0, y0, z0], maxs = [x1, y1, z1];
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-9) { if (o[i] < mins[i] || o[i] > maxs[i]) return null; continue; }
    let t1 = (mins[i] - o[i]) / d[i], t2 = (maxs[i] - o[i]) / d[i];
    let f1 = i * 2, f2 = i * 2 + 1; // face indices: 0 -x,1 +x,2 -y,3 +y,4 -z,5 +z
    if (t1 > t2) { const t = t1; t1 = t2; t2 = t; const f = f1; f1 = f2; f2 = f; }
    if (t1 > tmin) { tmin = t1; face = f1; }
    if (t2 < tmax) tmax = t2;
    if (tmin > tmax) return null;
  }
  if (tmax < 0) return null;
  return { t: Math.max(0, tmin), face };
}
