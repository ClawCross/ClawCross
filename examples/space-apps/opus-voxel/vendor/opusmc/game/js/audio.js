'use strict';
// ---------------------------------------------------------------------------
// Procedural sound effects + ambient piano music (WebAudio, no sound files)
// ---------------------------------------------------------------------------
const Sound = {
  ctx: null, master: null, sfx: null, music: null, noiseBuf: null, reverb: null,
  volume: 0.8, musicVolume: 0.45, listener: { x: 0, y: 0, z: 0, yaw: 0 },
  init() {
    if (this.ctx) { if (this.ctx.state === 'suspended') this.ctx.resume(); return; }
    try { this.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return; }
    const c = this.ctx;
    this.master = c.createGain(); this.master.gain.value = 1; this.master.connect(c.destination);
    this.sfx = c.createGain(); this.sfx.gain.value = this.volume; this.sfx.connect(this.master);
    this.music = c.createGain(); this.music.gain.value = this.musicVolume; this.music.connect(this.master);
    const len = c.sampleRate * 2;
    this.noiseBuf = c.createBuffer(1, len, c.sampleRate);
    const d = this.noiseBuf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    // reverb impulse
    const rl = c.sampleRate * 3, ir = c.createBuffer(2, rl, c.sampleRate);
    for (let ch = 0; ch < 2; ch++) { const x = ir.getChannelData(ch); for (let i = 0; i < rl; i++) x[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / rl, 3); }
    this.reverb = c.createConvolver(); this.reverb.buffer = ir;
    const rg = c.createGain(); rg.gain.value = 0.5; this.reverb.connect(rg); rg.connect(this.music);
    this.nextMusic = c.currentTime + 20 + Math.random() * 30;
  },
  setVolume(v) { this.volume = v; if (this.sfx) this.sfx.gain.value = v; },
  setMusic(v) { this.musicVolume = v; if (this.music) this.music.gain.value = v; },
  // spatial output node
  out(pos, vol = 1) {
    const c = this.ctx;
    const g = c.createGain();
    let v = vol;
    if (pos) {
      const L = this.listener, dx = pos[0] - L.x, dy = pos[1] - L.y, dz = pos[2] - L.z;
      const d = Math.hypot(dx, dy, dz);
      v *= Math.max(0, 1 - d / 20);
      if (v <= 0.001) return null;
      if (c.createStereoPanner) {
        const p = c.createStereoPanner();
        const right = dx * Math.cos(L.yaw) - dz * Math.sin(L.yaw);
        p.pan.value = clamp(right / (d + 0.5), -1, 1) * 0.7;
        g.connect(p); p.connect(this.sfx);
      } else g.connect(this.sfx);
    } else g.connect(this.sfx);
    g.gain.value = v;
    return g;
  },
  noise(pos, { dur = 0.15, vol = 0.5, type = 'bandpass', freq = 1000, q = 1, freqEnd, attack = 0.005, delay = 0 }) {
    if (!this.ctx) return;
    const c = this.ctx, o = this.out(pos, vol); if (!o) return;
    const t = c.currentTime + delay;
    const src = c.createBufferSource(); src.buffer = this.noiseBuf;
    src.playbackRate.value = 0.8 + Math.random() * 0.4;
    const f = c.createBiquadFilter(); f.type = type; f.frequency.setValueAtTime(freq, t); f.Q.value = q;
    if (freqEnd) f.frequency.exponentialRampToValueAtTime(freqEnd, t + dur);
    const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(1, t + attack); g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    src.connect(f); f.connect(g); g.connect(o);
    src.start(t, Math.random() * 1.5); src.stop(t + dur + 0.05);
  },
  tone(pos, { freq = 440, freqEnd, dur = 0.2, vol = 0.3, type = 'sine', attack = 0.01, delay = 0, dest }) {
    if (!this.ctx) return;
    const c = this.ctx, o = dest || this.out(pos, vol); if (!o) return;
    const t = c.currentTime + delay;
    const osc = c.createOscillator(); osc.type = type; osc.frequency.setValueAtTime(freq, t);
    if (freqEnd) osc.frequency.exponentialRampToValueAtTime(freqEnd, t + dur);
    const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(dest ? vol : 1, t + attack); g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    osc.connect(g); g.connect(o); osc.start(t); osc.stop(t + dur + 0.05);
  },
  MAT: {
    stone: { freq: 900, q: 0.8, dur: 0.12, type: 'bandpass' },
    wood: { freq: 450, q: 2.5, dur: 0.13, type: 'bandpass' },
    grass: { freq: 2400, q: 0.5, dur: 0.14, type: 'highpass' },
    gravel: { freq: 1300, q: 0.6, dur: 0.16, type: 'bandpass' },
    sand: { freq: 3000, q: 0.4, dur: 0.16, type: 'highpass' },
    snow: { freq: 3500, q: 0.4, dur: 0.18, type: 'highpass' },
    glass: { freq: 3500, q: 3, dur: 0.1, type: 'bandpass' },
    wool: { freq: 600, q: 0.4, dur: 0.15, type: 'lowpass' },
    metal: { freq: 2000, q: 6, dur: 0.15, type: 'bandpass' },
  },
  block(kind, mat, pos) {
    if (!this.ctx) return;
    const m = this.MAT[mat] || this.MAT.stone;
    if (kind === 'step') this.noise(pos, { ...m, vol: 0.18, dur: m.dur * 0.8 });
    else if (kind === 'hit') this.noise(pos, { ...m, vol: 0.22, dur: m.dur * 0.6 });
    else if (kind === 'place') { this.noise(pos, { ...m, vol: 0.55, freq: m.freq * 0.8 }); if (mat === 'wood') this.tone(pos, { freq: 180, freqEnd: 120, dur: 0.08, vol: 0.25, type: 'triangle' }); }
    else if (kind === 'break') {
      this.noise(pos, { ...m, vol: 0.7, dur: m.dur * 1.6 });
      this.noise(pos, { ...m, vol: 0.4, dur: m.dur * 1.2, delay: 0.04, freq: m.freq * 0.7 });
      if (mat === 'glass') for (let i = 0; i < 4; i++) this.tone(pos, { freq: 2000 + Math.random() * 3000, dur: 0.15, vol: 0.12, delay: i * 0.03, type: 'sine' });
    }
  },
  pop(pos) { this.tone(pos, { freq: 600 + Math.random() * 300, freqEnd: 1400 + Math.random() * 300, dur: 0.08, vol: 0.25, type: 'sine' }); },
  click() { this.tone(null, { freq: 800, freqEnd: 500, dur: 0.05, vol: 0.25, type: 'square' }); },
  hurt(pos) { this.tone(pos, { freq: 330, freqEnd: 180, dur: 0.18, vol: 0.4, type: 'sawtooth' }); this.noise(pos, { freq: 600, dur: 0.12, vol: 0.3, type: 'lowpass' }); },
  explode(pos) {
    this.noise(pos, { type: 'lowpass', freq: 1500, freqEnd: 60, dur: 1.8, vol: 1.2, q: 0.5 });
    this.noise(pos, { type: 'lowpass', freq: 300, freqEnd: 40, dur: 2.5, vol: 1.0, q: 0.3 });
  },
  fuse(pos) { this.noise(pos, { type: 'highpass', freq: 4000, dur: 1.2, vol: 0.4, attack: 0.05 }); },
  fizz(pos) { this.noise(pos, { type: 'highpass', freq: 3000, dur: 0.5, vol: 0.4 }); },
  splash(pos) { this.noise(pos, { type: 'lowpass', freq: 2000, freqEnd: 300, dur: 0.5, vol: 0.5 }); },
  eat(pos) { this.noise(pos, { type: 'bandpass', freq: 900 + Math.random() * 400, q: 1.5, dur: 0.1, vol: 0.45 }); },
  burp(pos) { this.tone(pos, { freq: 120, freqEnd: 80, dur: 0.3, vol: 0.4, type: 'sawtooth' }); },
  bow(pos) { this.tone(pos, { freq: 300, freqEnd: 900, dur: 0.15, vol: 0.3, type: 'triangle' }); this.noise(pos, { freq: 2000, dur: 0.15, vol: 0.2 }); },
  arrowHit(pos) { this.noise(pos, { freq: 800, dur: 0.08, vol: 0.4, q: 2 }); },
  door(pos, open) { this.tone(pos, { freq: open ? 200 : 160, freqEnd: open ? 140 : 100, dur: 0.18, vol: 0.4, type: 'triangle' }); this.noise(pos, { freq: 500, q: 3, dur: 0.15, vol: 0.3 }); },
  levelup() { [0, 4, 7, 12].forEach((n, i) => this.tone(null, { freq: 523 * Math.pow(2, n / 12), dur: 0.35, vol: 0.18, type: 'triangle', delay: i * 0.09 })); },
  toast() { [0, 7].forEach((n, i) => this.tone(null, { freq: 880 * Math.pow(2, n / 12), dur: 0.3, vol: 0.15, type: 'sine', delay: i * 0.12 })); },
  note(pos, pitch) { this.tone(pos, { freq: 185 * Math.pow(2, pitch / 12), dur: 0.8, vol: 0.4, type: 'triangle' }); },
  mob(type, kind, pos) {
    if (!this.ctx) return;
    const r = Math.random();
    switch (type) {
      case 'pig': this.tone(pos, { freq: 260 + r * 60, freqEnd: 180, dur: 0.25, vol: 0.35, type: 'sawtooth' }); this.tone(pos, { freq: 240, freqEnd: 160, dur: 0.2, vol: 0.25, type: 'square', delay: 0.15 }); break;
      case 'cow': this.tone(pos, { freq: 140 + r * 20, freqEnd: 110, dur: 0.9, vol: 0.4, type: 'sawtooth', attack: 0.1 }); break;
      case 'sheep': for (let i = 0; i < 4; i++) this.tone(pos, { freq: 420 + r * 50, freqEnd: 400, dur: 0.12, vol: 0.25, type: 'sawtooth', delay: i * 0.1 }); break;
      case 'chicken': for (let i = 0; i < 2; i++) this.tone(pos, { freq: 1100 + r * 300, freqEnd: 700, dur: 0.08, vol: 0.2, type: 'square', delay: i * 0.12 }); break;
      case 'zombie': this.tone(pos, { freq: 110 + r * 30, freqEnd: 80, dur: 0.9, vol: 0.45, type: 'sawtooth', attack: 0.15 }); this.noise(pos, { freq: 300, dur: 0.8, vol: 0.2, type: 'lowpass', attack: 0.1 }); break;
      case 'skeleton': for (let i = 0; i < 4; i++) this.noise(pos, { freq: 2500, q: 5, dur: 0.05, vol: 0.35, delay: i * 0.07 }); break;
      case 'creeper': this.fuse(pos); break;
      case 'spider': this.noise(pos, { freq: 1200, q: 4, dur: 0.3, vol: 0.3 }); this.tone(pos, { freq: 90, dur: 0.3, vol: 0.2, type: 'sawtooth' }); break;
    }
    if (kind === 'hurt') this.tone(pos, { freq: 500, freqEnd: 250, dur: 0.12, vol: 0.2, type: 'square' });
  },
  // ---- generative ambient piano --------------------------------------------------
  piano(freq, t, vol) {
    const c = this.ctx;
    const g = c.createGain(); g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(vol, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0008, t + 4);
    const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 2200;
    g.connect(lp); lp.connect(this.music); lp.connect(this.reverb);
    for (const [m, a, ty] of [[1, 1, 'triangle'], [2, 0.3, 'sine'], [3, 0.12, 'sine'], [4.01, 0.05, 'sine']]) {
      const o = c.createOscillator(); o.type = ty; o.frequency.value = freq * m;
      const og = c.createGain(); og.gain.value = a;
      o.connect(og); og.connect(g); o.start(t); o.stop(t + 4.2);
    }
  },
  updateMusic(enabled) {
    if (!this.ctx || !enabled) return;
    const c = this.ctx;
    if (c.currentTime < this.nextMusic) return;
    // play a short phrase from a calm scale
    const scales = [[0, 2, 4, 7, 9, 12, 14, 16, 19], [0, 3, 5, 7, 10, 12, 15, 17], [0, 2, 4, 5, 7, 9, 11, 12, 14]];
    const sc = scales[(Math.random() * scales.length) | 0];
    const root = [196, 220, 174.6, 261.6][(Math.random() * 4) | 0];
    let t = c.currentTime + 0.1;
    const n = 12 + ((Math.random() * 14) | 0);
    let idx = (Math.random() * 4) | 0;
    for (let i = 0; i < n; i++) {
      idx = clamp(idx + ((Math.random() * 5) | 0) - 2, 0, sc.length - 1);
      const f = root * Math.pow(2, sc[idx] / 12);
      this.piano(f, t, 0.12);
      if (Math.random() < 0.3) this.piano(f / 2, t, 0.08);
      if (i % 4 === 0) this.piano(root / 2 * Math.pow(2, sc[(idx + 2) % sc.length] / 12) / 2, t, 0.06);
      t += [0.6, 0.9, 1.2, 0.6, 1.8][(Math.random() * 5) | 0];
    }
    this.nextMusic = t + 60 + Math.random() * 120;
  },
};
