'use strict';
// ---------------------------------------------------------------------------
// Menu screens
// ---------------------------------------------------------------------------
const SPLASHES = ['也试试 Java 版！', '100% 纯手工打造！', '方块！', '在浏览器里运行！', '无需安装！', '还有苦力怕！', '挖矿与合成！', 'WebGL2！', '平滑光照！', '流动的水！', '会爆炸的 TNT！', '自带昼夜循环！', '小心岩浆！', '钻石！', '羊毛有很多颜色！'];

class TitleScreen extends MenuScreen {
  constructor(game) { super(game, null); this.bgDim = 0; this.splash = SPLASHES[(Math.random() * SPLASHES.length) | 0]; }
  layout() {
    const u = this.game.ui, cx = u.w / 2, y = u.h / 4 + 48;
    this.widgets = [
      { x: cx - 100, y, w: 200, h: 20, label: '单人游戏', onClick: () => this.game.ui.open(new WorldListScreen(this.game)) },
      { x: cx - 100, y: y + 24, w: 200, h: 20, label: '游戏说明', onClick: () => this.game.ui.open(new HelpScreen(this.game, this)) },
      { x: cx - 100, y: y + 60, w: 98, h: 20, label: '选项…', onClick: () => this.game.ui.open(new OptionsScreen(this.game, this)) },
      { x: cx + 2, y: y + 60, w: 98, h: 20, label: '全屏', onClick: () => toggleFullscreen() },
    ];
  }
  drawExtra(ui) {
    const c = ui.ctx, w = ui.w;
    const gr = c.createLinearGradient(0, 0, 0, ui.h);
    gr.addColorStop(0, 'rgba(0,0,0,0.35)'); gr.addColorStop(0.5, 'rgba(0,0,0,0.1)'); gr.addColorStop(1, 'rgba(0,0,0,0.45)');
    c.fillStyle = gr; c.fillRect(0, 0, ui.w, ui.h);
    const logoW = 256, logoH = 64;
    const lx = Math.floor(w / 2 - logoW / 2), ly = 30;
    c.imageSmoothingEnabled = true;
    c.drawImage(ui.img.logo, 0, 0, 1024, 256, lx, ly, logoW, logoH);
    c.drawImage(ui.img.edition, 0, 0, 512, 64, Math.floor(w / 2 - 64), ly + 50, 128, 16);
    c.imageSmoothingEnabled = false;
    // splash
    c.save();
    c.translate(lx + logoW - 30, ly + 48);
    c.rotate(-20 * Math.PI / 180);
    const s = 1.8 - Math.abs(Math.sin(performance.now() / 1000 * Math.PI * 2 % (Math.PI * 2)) * 0.1);
    const sw = Font.width(this.splash);
    c.scale(s * 100 / (sw + 32) , s * 100 / (sw + 32));
    Font.center(c, this.splash, 0, -8, '#FFFF00');
    c.restore();
    Font.draw(c, 'Minecraft JS 复刻 (使用 26.2 官方材质)', 2, ui.h - 10, '#FFFFFF');
    const t = '非官方作品 · 仅供比赛展示';
    Font.draw(c, t, ui.w - Font.width(t) - 2, ui.h - 10, '#FFFFFF');
  }
}

class LoadingScreen extends MenuScreen {
  constructor(game, text) { super(game, null); this.text = text; this.bgDirt = true; this.pauses = false; }
  drawExtra(ui) {
    const c = ui.ctx, g = this.game;
    const msg = g.state === 'loading' ? '正在生成地形…' : this.text;
    Font.center(c, msg, ui.w / 2, ui.h / 2 - 20, '#FFFFFF');
    const p = g.loadProgress || 0, bw = 182, bx = Math.floor(ui.w / 2 - bw / 2), by = Math.floor(ui.h / 2);
    c.fillStyle = '#808080'; c.fillRect(bx, by, bw, 3);
    c.fillStyle = '#80FF80'; c.fillRect(bx, by, Math.floor(bw * p), 3);
    Font.center(c, `区块: ${g.world ? g.world.chunks.size : 0}`, ui.w / 2, by + 10, '#A0A0A0');
  }
}

class WorldListScreen extends MenuScreen {
  constructor(game) { super(game, '选择世界'); this.bgDirt = true; this.worlds = null; this.sel = -1; this.scroll = 0; DB.listWorlds().then(w => { this.worlds = w; this.layout(); }); }
  layout() {
    const u = this.game.ui, cx = u.w / 2, by = u.h - 52;
    const has = this.sel >= 0 && this.worlds && this.worlds[this.sel];
    this.widgets = [
      { x: cx - 154, y: by, w: 150, h: 20, label: '进入选中的世界', enabled: !!has, onClick: () => this.play() },
      { x: cx + 4, y: by, w: 150, h: 20, label: '创建新的世界', onClick: () => this.game.ui.open(new CreateWorldScreen(this.game, this)) },
      { x: cx - 154, y: by + 24, w: 72, h: 20, label: '删除', enabled: !!has, onClick: () => this.del() },
      { x: cx - 78, y: by + 24, w: 72, h: 20, label: '重命名', enabled: !!has, onClick: () => this.rename() },
      { x: cx + 4, y: by + 24, w: 150, h: 20, label: '取消', onClick: () => this.game.ui.open(new TitleScreen(this.game)) },
    ];
    this.listY = 36; this.listH = by - 8 - 36; this.titleY = 16;
  }
  play() { const w = this.worlds[this.sel]; if (w) this.game.loadWorld(w.id); }
  async del() {
    const w = this.worlds[this.sel]; if (!w) return;
    if (this.confirmDel !== w.id) { this.confirmDel = w.id; this.game.ui.message(`再次点击“删除”以确认删除 “${w.name}”`, '#FF5555'); this.widgets[2].label = '确认删除？'; return; }
    await DB.deleteWorld(w.id); this.confirmDel = null; this.sel = -1;
    this.worlds = await DB.listWorlds(); this.layout();
  }
  rename() {
    const w = this.worlds[this.sel]; if (!w) return;
    const u = this.game.ui, cx = u.w / 2;
    u.showInput(cx - 100, this.listY + 4 + (this.sel - this.scroll) * 36, 200, 14, w.name, async (v) => { w.name = v.trim() || w.name; await DB.putWorld(w); u.hideInput(); });
  }
  drawExtra(ui) {
    const c = ui.ctx, cx = ui.w / 2;
    c.fillStyle = 'rgba(0,0,0,0.5)'; c.fillRect(0, this.listY, ui.w, this.listH);
    if (DB.backend === 'memory') Font.center(c, '注意：当前浏览器禁止本地存储，存档在关闭页面后会丢失', cx, this.listY + this.listH + 2 - 12, '#FF5555');
    if (!this.worlds) { Font.center(c, '载入中…', cx, this.listY + 20, '#AAAAAA'); return; }
    if (!this.worlds.length) Font.center(c, '还没有世界，点击“创建新的世界”开始吧！', cx, this.listY + 20, '#AAAAAA');
    const vis = Math.floor(this.listH / 36);
    this.worlds.slice(this.scroll, this.scroll + vis).forEach((w, i) => {
      const y = this.listY + 4 + i * 36, x = cx - 110, idx = i + this.scroll;
      if (idx === this.sel) { c.strokeStyle = '#FFFFFF'; c.lineWidth = 1; c.strokeRect(x - 2.5, y - 2.5, 225, 35); c.fillStyle = 'rgba(0,0,0,0.6)'; c.fillRect(x - 2, y - 2, 224, 34); }
      ui.drawItem({ id: w.mode === 'creative' ? item('grass_block') : item('iron_pickaxe'), count: 1 }, x + 4, y + 7);
      Font.draw(c, w.name, x + 28, y + 1, '#FFFFFF');
      Font.draw(c, new Date(w.lastPlayed).toLocaleString('zh-CN'), x + 28, y + 11, '#808080');
      Font.draw(c, (w.mode === 'creative' ? '创造模式' : '生存模式') + (w.flat ? '  超平坦' : '  种子: ' + w.seed), x + 28, y + 21, '#808080');
    });
  }
  mouseDown(ui, mx, my, b) {
    if (this.worlds && my >= this.listY && my < this.listY + this.listH && Math.abs(mx - ui.w / 2) < 112) {
      const i = Math.floor((my - this.listY - 4) / 36) + this.scroll;
      if (i < this.worlds.length && i >= 0) {
        const now = performance.now();
        if (this.sel === i && now - (this.lastT || 0) < 350) { this.play(); return; }
        this.sel = i; this.lastT = now; this.confirmDel = null; this.layout(); Sound.init(); Sound.click();
      }
      return;
    }
    super.mouseDown(ui, mx, my, b);
  }
  wheel(ui, dy) { if (this.worlds) this.scroll = clamp(this.scroll + Math.sign(dy), 0, Math.max(0, this.worlds.length - Math.floor(this.listH / 36))); }
  onEsc() { this.game.ui.open(new TitleScreen(this.game)); }
}

class CreateWorldScreen extends MenuScreen {
  constructor(game, parent) { super(game, '创建新的世界'); this.bgDirt = true; this.parent = parent; this.name = '新的世界'; this.seed = ''; this.mode = 'survival'; this.flat = false; this.field = 0; }
  layout() {
    const u = this.game.ui, cx = u.w / 2;
    this.fields = [{ x: cx - 100, y: 60, w: 200, h: 20 }, { x: cx - 100, y: 104, w: 200, h: 20 }];
    this.widgets = [
      { x: cx - 100, y: 136, w: 200, h: 20, label: () => '游戏模式: ' + (this.mode === 'creative' ? '创造' : '生存'), onClick: () => { this.mode = this.mode === 'creative' ? 'survival' : 'creative'; },
        tip: ['生存：收集资源、合成、对抗怪物', '创造：无限方块、飞行、无敌'] },
      { x: cx - 100, y: 160, w: 200, h: 20, label: () => '世界类型: ' + (this.flat ? '超平坦' : '默认'), onClick: () => { this.flat = !this.flat; },
        tip: ['默认：山地、森林、海洋、洞穴与矿石', '超平坦：一望无际的草地，适合建筑'] },
      { x: cx - 154, y: u.h - 28, w: 150, h: 20, label: '创建新的世界', onClick: () => this.create() },
      { x: cx + 4, y: u.h - 28, w: 150, h: 20, label: '取消', onClick: () => this.game.ui.open(this.parent) },
    ];
    this.focus(this.field);
  }
  focus(i) {
    this.field = i;
    const f = this.fields[i], u = this.game.ui;
    u.showInput(f.x + 4, f.y + 2, f.w - 8, f.h - 4, i === 0 ? this.name : this.seed, () => this.create(), { onInput: (v) => { if (this.field === 0) this.name = v; else this.seed = v; }, onEsc: () => u.open(this.parent), max: 32 });
  }
  create() { this.game.ui.hideInput(); this.game.createWorld(this.name.trim() || '新的世界', this.seed.trim(), this.mode, this.flat); }
  drawExtra(ui) {
    const c = ui.ctx;
    Font.draw(c, '世界名称', this.fields[0].x, this.fields[0].y - 11, '#A0A0A0');
    Font.draw(c, '世界生成器的种子（留空则随机）', this.fields[1].x, this.fields[1].y - 11, '#A0A0A0');
    this.fields.forEach((f, i) => { ui.nine(i === this.field ? 'text_field_hl' : 'text_field', f.x, f.y, f.w, f.h, 1); });
  }
  mouseDown(ui, mx, my, b) {
    for (let i = 0; i < 2; i++) if (ui.hit(this.fields[i], mx, my)) { this.focus(i); return; }
    super.mouseDown(ui, mx, my, b);
  }
  onEsc() { this.game.ui.open(this.parent); }
}

class OptionsScreen extends MenuScreen {
  constructor(game, parent) { super(game, '选项'); this.parent = parent; this.pauses = !(parent instanceof TitleScreen); this.bgDim = parent instanceof TitleScreen ? 0 : 0.6; this.bgDirt = parent instanceof TitleScreen; }
  layout() {
    const u = this.game.ui, cx = u.w / 2, s = this.game.settings, g = this.game;
    const sl = (x, y, label, val, set) => ({ slider: true, x, y, w: 150, h: 20, value: val, label, onChange: (v) => { set(v); g.applySettings(); g.saveSettings(); } });
    const bt = (x, y, label, fn) => ({ x, y, w: 150, h: 20, label, onClick: () => { fn(); g.applySettings(); g.saveSettings(); } });
    let y = 40;
    this.widgets = [
      sl(cx - 155, y, () => '视野: ' + Math.round(s.fov), (s.fov - 30) / 80, v => s.fov = Math.round(30 + v * 80)),
      sl(cx + 5, y, () => '渲染距离: ' + s.renderDist + ' 区块', (s.renderDist - 2) / 14, v => s.renderDist = Math.round(2 + v * 14)),
      sl(cx - 155, y += 24, () => '鼠标灵敏度: ' + Math.round(s.sens * 200) + '%', s.sens, v => s.sens = Math.max(0.05, v)),
      sl(cx + 5, y, () => '亮度: ' + (s.gamma < 0.05 ? '昏暗' : s.gamma > 0.95 ? '明亮' : Math.round(s.gamma * 100) + '%'), s.gamma, v => s.gamma = v),
      sl(cx - 155, y += 24, () => '音效音量: ' + Math.round(s.volume * 100) + '%', s.volume, v => s.volume = v),
      sl(cx + 5, y, () => '音乐音量: ' + Math.round(s.music * 100) + '%', s.music, v => s.music = v),
      bt(cx - 155, y += 24, () => '云: ' + (s.clouds ? '开' : '关'), () => s.clouds = !s.clouds),
      bt(cx + 5, y, () => '视角摇晃: ' + (s.bobbing ? '开' : '关'), () => s.bobbing = !s.bobbing),
      bt(cx - 155, y += 24, () => '界面尺寸: ' + (s.guiScale || '自动'), () => { s.guiScale = (s.guiScale + 1) % 5; setTimeout(() => g.ui.resize(), 0); }),
      bt(cx + 5, y, () => '渲染分辨率: ' + Math.round(s.resScale * 100) + '%', () => { s.resScale = s.resScale >= 1 ? 0.5 : s.resScale + 0.25; }),
      bt(cx - 155, y += 24, () => '高级提示框: ' + (s.advTooltips ? '开' : '关'), () => s.advTooltips = !s.advTooltips),
      bt(cx + 5, y, () => '全屏', () => toggleFullscreen()),
      { x: cx - 100, y: u.h - 30, w: 200, h: 20, label: '完成', onClick: () => this.done() },
    ];
  }
  done() { this.game.saveSettings(); this.game.ui.open(this.parent); }
  onEsc() { this.done(); }
}

class PauseScreen extends MenuScreen {
  constructor(game) { super(game, '游戏菜单'); this.pauses = true; this.titleY = 40; }
  layout() {
    const u = this.game.ui, cx = u.w / 2, y = u.h / 4 + 8, g = this.game;
    this.widgets = [
      { x: cx - 102, y, w: 204, h: 20, label: '回到游戏', onClick: () => g.ui.close() },
      { x: cx - 102, y: y + 24, w: 98, h: 20, label: '进度', onClick: () => g.ui.open(new AdvancementsScreen(g, this)) },
      { x: cx + 4, y: y + 24, w: 98, h: 20, label: '统计信息', onClick: () => g.ui.open(new StatsScreen(g, this)) },
      { x: cx - 102, y: y + 48, w: 98, h: 20, label: '游戏说明', onClick: () => g.ui.open(new HelpScreen(g, this)) },
      { x: cx + 4, y: y + 48, w: 98, h: 20, label: () => '模式: ' + (g.mode === 'creative' ? '创造' : '生存'), onClick: () => { g.setMode(g.mode === 'creative' ? 'survival' : 'creative'); } },
      { x: cx - 102, y: y + 72, w: 204, h: 20, label: '选项…', onClick: () => g.ui.open(new OptionsScreen(g, this)) },
      { x: cx - 102, y: y + 108, w: 204, h: 20, label: '保存并退回到标题屏幕', onClick: () => g.quitToTitle() },
    ];
  }
  onEsc() { this.game.ui.close(); }
}

class AdvancementsScreen extends MenuScreen {
  constructor(game, parent) { super(game, '进度'); this.parent = parent; this.pauses = true; this.bgDim = 0.75; this.titleY = 16; }
  layout() { const u = this.game.ui; this.widgets = [{ x: u.w / 2 - 100, y: u.h - 28, w: 200, h: 20, label: '完成', onClick: () => this.game.ui.open(this.parent) }]; }
  drawExtra(ui) {
    const c = ui.ctx, keys = Object.keys(ADVANCEMENTS);
    const done = keys.filter(k => this.game.adv[k]).length;
    Font.center(c, `已完成 ${done} / ${keys.length}`, ui.w / 2, 28, '#AAAAAA');
    const cols = 2, cw = 150, x0 = ui.w / 2 - cw;
    keys.forEach((k, i) => {
      const x = x0 + (i % cols) * cw, y = 42 + Math.floor(i / cols) * 20;
      const got = !!this.game.adv[k];
      c.fillStyle = got ? 'rgba(40,120,40,0.6)' : 'rgba(0,0,0,0.5)'; c.fillRect(x + 2, y, cw - 4, 18);
      ui.drawItem({ id: item(ADVANCEMENTS[k][1]), count: 1 }, x + 4, y + 1);
      Font.draw(c, ADVANCEMENTS[k][0], x + 24, y + 5, got ? '#FFFF55' : '#808080');
    });
  }
  onEsc() { this.game.ui.open(this.parent); }
}

class StatsScreen extends MenuScreen {
  constructor(game, parent) { super(game, '统计信息'); this.parent = parent; this.pauses = true; this.bgDim = 0.75; }
  layout() { const u = this.game.ui; this.widgets = [{ x: u.w / 2 - 100, y: u.h - 28, w: 200, h: 20, label: '完成', onClick: () => this.game.ui.open(this.parent) }]; }
  drawExtra(ui) {
    const g = this.game, s = g.stats, c = ui.ctx;
    const lines = [['游戏天数', Math.floor(g.worldTime / 24000) + 1], ['放置的方块', s.placed], ['挖掘的方块', s.mined], ['击杀的生物', s.kills], ['经验等级', g.xpLevel()], ['世界种子', g.world.seed]];
    lines.forEach(([a, b], i) => { Font.draw(c, a, ui.w / 2 - 100, 50 + i * 14, '#FFFFFF'); const t = String(b); Font.draw(c, t, ui.w / 2 + 100 - Font.width(t), 50 + i * 14, '#FFFF55'); });
  }
  onEsc() { this.game.ui.open(this.parent); }
}

class DeathScreen extends MenuScreen {
  constructor(game) { super(game, null); this.bgDim = 0; this.pauses = false; this.t = performance.now(); }
  layout() {
    const u = this.game.ui, cx = u.w / 2, y = u.h / 4 + 72;
    this.widgets = [
      { x: cx - 100, y, w: 200, h: 20, label: '重生', onClick: () => this.game.respawn() },
      { x: cx - 100, y: y + 24, w: 200, h: 20, label: '标题屏幕', onClick: () => { this.game.player.health = 20; this.game.player.deathTime = 0; this.game.respawn(); this.game.quitToTitle(); } },
    ];
  }
  draw(ui) {
    const c = ui.ctx;
    c.save(); c.translate(ui.w / 2, 60); c.scale(2, 2); Font.center(c, '你死了！', 0, 0, '#FFFFFF'); c.restore();
    Font.center(c, this.game.deathMsg || '', ui.w / 2, 85, '#FFFFFF');
    Font.center(c, '分数: ' + (this.game.deathScore || 0), ui.w / 2, 100, '#FFFF55');
    const ok = performance.now() - this.t > 1000;
    for (const w of this.widgets) { w.enabled = ok; ui.button(w); }
  }
}

class HelpScreen extends MenuScreen {
  constructor(game, parent) { super(game, '游戏说明'); this.parent = parent; this.pauses = true; this.bgDim = 0.8; this.bgDirt = parent instanceof TitleScreen; this.page = 0; this.titleY = 10; }
  layout() {
    const u = this.game.ui, cx = u.w / 2;
    this.widgets = [
      { x: cx - 154, y: u.h - 26, w: 74, h: 20, label: '< 上一页', onClick: () => this.page = Math.max(0, this.page - 1) },
      { x: cx - 76, y: u.h - 26, w: 152, h: 20, label: '完成', onClick: () => this.game.ui.open(this.parent) },
      { x: cx + 80, y: u.h - 26, w: 74, h: 20, label: '下一页 >', onClick: () => this.page = Math.min(HELP_PAGES.length - 1, this.page + 1) },
    ];
  }
  drawExtra(ui) {
    const c = ui.ctx, pg = HELP_PAGES[this.page];
    Font.center(c, `${pg.title}  (${this.page + 1}/${HELP_PAGES.length})`, ui.w / 2, 24, '#FFFF55');
    let y = 40;
    for (const line of pg.lines) {
      if (y > ui.h - 40) break;
      const [k, v] = line.split('|');
      if (v !== undefined) { Font.draw(c, k, ui.w / 2 - 150, y, '#FFFFA0'); Font.draw(c, v, ui.w / 2 - 60, y, '#FFFFFF'); }
      else Font.draw(c, k, ui.w / 2 - 150, y, '#E0E0E0');
      y += 11;
    }
  }
  onEsc() { this.game.ui.open(this.parent); }
}
const HELP_PAGES = [
  { title: '基本操作', lines: ['W A S D|移动', '空格|跳跃 / 游泳上浮 / (创造) 双击切换飞行', '左 Shift|潜行（不会从边缘掉落）/ 飞行下降', '左 Ctrl 或 双击 W|疾跑', '鼠标移动|转动视角', '鼠标左键|挖掘方块 / 攻击生物（按住持续挖掘）', '鼠标右键|放置方块 / 使用物品 / 打开容器 / 吃东西 / 拉弓', '鼠标中键|选取方块', '滚轮 / 1-9|切换快捷栏', 'E|打开物品栏', 'Q|丢弃物品 (Ctrl+Q 丢弃一组)', 'F1 / F3 / F5|隐藏界面 / 调试信息 / 切换视角', 'T 或 /|打开聊天与命令', 'Esc|游戏菜单（暂停）'] },
  { title: '物品栏操作', lines: ['左键|拿起 / 放下整组物品', '右键|拿起一半 / 放下一个', 'Shift + 左键|快速移动物品', '按住左键拖动|在多个格子之间平均分配', '按住右键拖动|每个格子放一个', '双击|收集同类物品', '悬停 + 数字键|与快捷栏交换', '悬停 + Q|丢弃物品', '', '创造模式物品栏：点击顶部标签切换分类，', '滚轮滚动；最后一个标签可以搜索物品。', '把物品放到右下角的 X 格可以删除。'] },
  { title: '生存入门', lines: ['1. 徒手撸树获得原木，合成木板（在物品栏 2x2 合成格）', '2. 4 个木板合成工作台，右键打开 3x3 合成', '3. 木板竖放 2 个合成木棍，制作木镐', '4. 挖石头获得圆石，制作石镐、熔炉', '5. 煤炭 + 木棍合成火把，照亮洞穴防止怪物生成', '6. 用熔炉把粗铁冶炼成铁锭，制作铁镐与铁甲', '7. 铁镐可以挖掘钻石矿（在 Y<16 的深层）', '', '饥饿值不足时无法疾跑、不能回血，记得吃东西！', '夜晚会生成僵尸、骷髅、苦力怕和蜘蛛。', '摔落、溺水、岩浆、仙人掌都会造成伤害。', '按 Esc → 进度 查看你的成就目标。'] },
  { title: '常用配方', lines: ['工作台|4 木板 (2x2)', '木棍|2 木板竖放', '镐|上排 3 材料 + 中间竖放 2 木棍', '斧 / 锹 / 剑 / 锄|同原版摆法', '熔炉|8 圆石围一圈', '箱子|8 木板围一圈', '火把|煤炭/木炭 在 木棍 上方', '面包|3 小麦横排', 'TNT|火药与沙子交错 (5 火药 + 4 沙子)', '床|3 羊毛 + 3 木板，夜晚右键睡觉跳过黑夜', '弓 / 箭|木棍+线 / 燧石+木棍+羽毛', '桶|3 铁锭 V 形；可装水和熔岩', '盔甲|皮革/铁/钻石，同原版摆法', '其他: 楼梯、台阶、门、梯子、书架、砖块…'] },
  { title: '玩法机制', lines: ['• 平滑光照与环境光遮蔽，火把/荧石/熔岩发光', '• 真实的水与熔岩流动，水遇熔岩生成黑曜石/圆石', '• 沙子与沙砾会受重力下落', '• TNT 与苦力怕爆炸可以破坏地形并连锁引爆', '• 用锄头耕地、种小麦，骨粉催熟；树苗可以长成树', '• 用剪刀剪羊毛，用小麦/种子/胡萝卜引诱并繁殖动物', '• 熔炉冶炼矿石与烹饪食物，获得经验', '• 昼夜循环、月相、星空、云层、雨雪天气；床可跳过夜晚', '• 多种生物群系：平原、森林、桦木林、针叶林、', '  雪原、沙漠、山地、海洋、河流、沙滩', '• 世界自动保存，可在主菜单管理多个存档'] },
  { title: '命令 (按 T 或 /)', lines: ['/gamemode creative|survival|切换游戏模式', '/time set day|night|noon|<数值>|设置时间', '/weather clear|rain|设置天气', '/give <物品英文名> [数量]|给予物品，如 /give diamond 64', '/tp <x> <y> <z>|传送', '/summon <生物>|生成 pig cow sheep chicken zombie ...', '/kill|自杀', '/spawnpoint|设置重生点为当前位置', '/seed|显示世界种子', '/clear|清空物品栏', '/xp <数量>|获得经验'] },
];

class ChatScreen extends MenuScreen {
  constructor(game, prefill = '') { super(game, null); this.bgDim = 0; this.isChat = true; this.prefill = prefill; this.hist = game.chatHistory || (game.chatHistory = []); this.hi = this.hist.length; }
  layout() {
    const u = this.game.ui;
    u.showInput(2, u.h - 14, u.w - 4, 12, this.prefill, (v) => { u.close(); if (v.trim()) { this.hist.push(v); runCommand(this.game, v.trim()); } }, { onEsc: () => u.close(), max: 256 });
    const el = u.input, prev = el.onkeydown;
    el.onkeydown = (e) => {
      if (e.key === 'ArrowUp' && this.hi > 0) { this.hi--; el.value = this.hist[this.hi]; e.preventDefault(); }
      else if (e.key === 'ArrowDown') { this.hi = Math.min(this.hist.length, this.hi + 1); el.value = this.hist[this.hi] || ''; e.preventDefault(); }
      else if (e.key === 'Tab') { e.preventDefault(); this.complete(el); }
      prev(e);
    };
  }
  complete(el) {
    const v = el.value, parts = v.split(' ');
    if (parts[0] === '/give' && parts.length === 2) { const m = Object.keys(ITEM_KEY).filter(k => k.startsWith(parts[1])); if (m.length) el.value = '/give ' + m[0]; }
    else if (parts[0] === '/summon' && parts.length === 2) { const m = Object.keys(MOB_TYPES).filter(k => k.startsWith(parts[1])); if (m.length) el.value = '/summon ' + m[0]; }
    else if (parts.length === 1) { const m = ['/gamemode', '/time', '/weather', '/give', '/tp', '/summon', '/kill', '/spawnpoint', '/seed', '/clear', '/xp', '/help'].filter(k => k.startsWith(v)); if (m.length) el.value = m[0] + ' '; }
  }
  draw(ui) { const c = ui.ctx; c.fillStyle = 'rgba(0,0,0,0.5)'; c.fillRect(2, ui.h - 14, ui.w - 4, 12); }
}

function runCommand(g, text) {
  const p = g.player, ui = g.ui;
  if (!text.startsWith('/')) { ui.message('<玩家> ' + text); return; }
  const [cmd, ...a] = text.slice(1).split(/\s+/);
  const err = (m) => ui.message(m, '#FF5555');
  try {
    switch (cmd) {
      case 'help': ui.message('可用命令: /gamemode /time /weather /give /tp /summon /kill /spawnpoint /seed /clear /xp', '#FFFF55'); break;
      case 'gamemode': case 'gm': {
        const m = { creative: 'creative', c: 'creative', 1: 'creative', survival: 'survival', s: 'survival', 0: 'survival' }[a[0]];
        if (!m) return err('用法: /gamemode creative|survival');
        g.setMode(m); break;
      }
      case 'time': {
        if (a[0] === 'set' || a[0] === 'add') {
          const named = { day: 1000, noon: 6000, night: 13000, midnight: 18000, sunrise: 23000, sunset: 12000 };
          let v = named[a[1]] ?? parseInt(a[1]);
          if (isNaN(v)) return err('用法: /time set day|night|noon|midnight|<数值>');
          if (a[0] === 'set') g.worldTime = Math.floor(g.worldTime / 24000) * 24000 + v; else g.worldTime += v;
          ui.message('时间已设置为 ' + v, '#AAAAAA');
        } else ui.message('当前时间: ' + g.dayTime(), '#AAAAAA');
        break;
      }
      case 'weather': {
        if (a[0] === 'clear') { g.rainTarget = 0; g.weather = 0; g.weatherTimer = 24000; }
        else if (a[0] === 'rain' || a[0] === 'thunder') { g.rainTarget = 1; g.weather = 1; g.weatherTimer = 12000; }
        else return err('用法: /weather clear|rain');
        ui.message('天气已更改', '#AAAAAA'); break;
      }
      case 'give': {
        const key = (a[0] || '').replace('minecraft:', '');
        const id = ITEM_KEY[key];
        if (id === undefined) return err('未知物品: ' + key);
        const n = Math.max(1, Math.min(64 * 36, parseInt(a[1]) || 1));
        let left = n;
        while (left > 0) { const k = Math.min(left, maxStack(id)); const r = p.inv.add({ id, count: k, dmg: 0 }); if (r) { g.dropFromPlayer({ id, count: r, dmg: 0 }); } left -= k; }
        ui.message(`已将 ${n} 个 [${ITEMS[id].cn}] 给予玩家`, '#AAAAAA'); break;
      }
      case 'tp': {
        const v = a.map((s, i) => s.startsWith('~') ? p.pos[i] + (parseFloat(s.slice(1)) || 0) : parseFloat(s));
        if (v.length < 3 || v.some(isNaN)) return err('用法: /tp <x> <y> <z>');
        p.pos = [v[0], v[1], v[2]]; p.prev = [...p.pos]; p.vel = [0, 0, 0]; p.fallDist = 0;
        ui.message(`已传送至 ${v.map(x => x.toFixed(1)).join(', ')}`, '#AAAAAA'); break;
      }
      case 'summon': {
        const t = (a[0] || '').replace('minecraft:', '');
        if (t === 'tnt') { g.addEntity(new PrimedTNT(g, Math.floor(p.pos[0]), p.pos[1], Math.floor(p.pos[2]))); break; }
        if (!MOB_TYPES[t]) return err('未知生物: ' + t + '（可用: ' + Object.keys(MOB_TYPES).join(' ') + '）');
        const d = g.lookDir();
        const m = new Mob(g, t, p.pos[0] + d[0] * 3, p.pos[1] + 0.5, p.pos[2] + d[2] * 3);
        m.persistent = true;
        g.addEntity(m);
        ui.message('已生成 ' + MOB_TYPES[t].cn, '#AAAAAA'); break;
      }
      case 'kill': if (g.mode === 'creative') { p.health = 0; p.die({ type: 'void' }); } else p.hurt(1000, { type: 'void' }); break;
      case 'spawnpoint': p.spawn = [Math.floor(p.pos[0]), Math.floor(p.pos[1]), Math.floor(p.pos[2])]; ui.message('已设置重生点', '#AAAAAA'); break;
      case 'seed': ui.message('种子: [' + g.world.seed + ']', '#FFFFFF'); break;
      case 'clear': p.inv.slots.fill(null); ui.message('已清空物品栏', '#AAAAAA'); break;
      case 'xp': g.addXp(parseInt(a[0]) || 0); break;
      default: err('未知命令。输入 /help 查看帮助');
    }
  } catch (e) { err('命令出错: ' + e.message); }
}

Game.prototype.setMode = function (m) {
  this.mode = m;
  const p = this.player;
  if (m === 'survival') { p.flying = false; }
  this.ui.message('游戏模式已更新为' + (m === 'creative' ? '创造模式' : '生存模式'), '#AAAAAA');
};

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().then(() => { if (navigator.keyboard && navigator.keyboard.lock) navigator.keyboard.lock(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'Escape']).catch(() => { }); }).catch(() => { });
  } else document.exitFullscreen();
}
