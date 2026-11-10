'use strict';
// ---------------------------------------------------------------------------
// World: chunk storage, generation, lighting, fluids, block updates
// ---------------------------------------------------------------------------
const OPACITY = new Uint8Array(256), EMIT = new Uint8Array(256), SOLIDCUBE = new Uint8Array(256);
const IS_FLUID = new Uint8Array(256), REPLACEABLE = new Uint8Array(256);
for (let i = 0; i < 256; i++) {
  const b = BLOCKS[i]; if (!b) { OPACITY[i] = 15; continue; }
  OPACITY[i] = b.opacity; EMIT[i] = b.light; SOLIDCUBE[i] = b.opaque ? 1 : 0;
  IS_FLUID[i] = b.shape === SH_LIQUID ? 1 : 0; REPLACEABLE[i] = b.replaceable ? 1 : 0;
}
const AIR = 0, WATER = 7, LAVA = 8;

function ckey(cx, cz) { return (cx + 32768) * 65536 + (cz + 32768); }

class Chunk {
  constructor(cx, cz) {
    this.cx = cx; this.cz = cz;
    this.blocks = new Uint8Array(16 * 16 * HEIGHT);
    this.meta = new Uint8Array(16 * 16 * HEIGHT);
    this.light = new Uint8Array(16 * 16 * HEIGHT);
    this.grass = new Uint8Array(256 * 3);   // biome tint per column
    this.foliage = new Uint8Array(256 * 3);
    this.biome = new Uint8Array(256);
    this.dirty = 0;           // bitmask of sections needing remesh
    this.lit = false;
    this.meshes = new Array(SECTIONS).fill(null);
    this.modified = false;
    this.sectionCount = new Uint16Array(SECTIONS); // non-air block count per section
  }
  idx(x, y, z) { return (y << 8) | (z << 4) | x; }
  recount() {
    this.sectionCount.fill(0);
    const b = this.blocks;
    for (let i = 0; i < b.length; i++) if (b[i]) this.sectionCount[i >> 12]++;
  }
}

const BIOMES = ['海洋', '沙滩', '平原', '森林', '桦木森林', '针叶林', '雪原', '沙漠', '山地', '河流', '雪山', '冻洋'];
const BI = { OCEAN: 0, BEACH: 1, PLAINS: 2, FOREST: 3, BIRCH: 4, TAIGA: 5, SNOWY: 6, DESERT: 7, MOUNTAIN: 8, RIVER: 9, SNOWPEAK: 10, FROZEN_OCEAN: 11 };

class World {
  constructor(seed) {
    this.seed = seed | 0;
    this.chunks = new Map();
    this.nCont = new Noise(seed);
    this.nErosion = new Noise(seed + 1);
    this.nPeaks = new Noise(seed + 2);
    this.nHills = new Noise(seed + 3);
    this.nTemp = new Noise(seed + 4);
    this.nHumid = new Noise(seed + 5);
    this.nCave1 = new Noise(seed + 6);
    this.nCave2 = new Noise(seed + 7);
    this.nCave3 = new Noise(seed + 8);
    this.nRiver = new Noise(seed + 9);
    this.nSurf = new Noise(seed + 10);
    this.listener = null;     // game hooks
    this.ticks = new Map();   // scheduled block ticks: key -> {x,y,z,t}
    this.time = 0;            // game tick counter
    this.savedChunks = null;  // map key -> saved data, filled by save system
    this.lightQ = [];
    this.grassMap = null; this.foliageMap = null;
  }

  // ---- access -------------------------------------------------------------
  getChunk(cx, cz) { return this.chunks.get(ckey(cx, cz)); }
  getBlock(x, y, z) {
    if (y < 0) return 6; if (y >= HEIGHT) return 0;
    const c = this.chunks.get(ckey(x >> 4, z >> 4));
    return c ? c.blocks[(y << 8) | ((z & 15) << 4) | (x & 15)] : 0;
  }
  getMeta(x, y, z) {
    if (y < 0 || y >= HEIGHT) return 0;
    const c = this.chunks.get(ckey(x >> 4, z >> 4));
    return c ? c.meta[(y << 8) | ((z & 15) << 4) | (x & 15)] : 0;
  }
  isLoaded(x, z) { return this.chunks.has(ckey(x >> 4, z >> 4)); }
  getLight(x, y, z) {
    if (y >= HEIGHT) return 0xF0; if (y < 0) return 0;
    const c = this.chunks.get(ckey(x >> 4, z >> 4));
    return c ? c.light[(y << 8) | ((z & 15) << 4) | (x & 15)] : 0xF0;
  }
  skyAt(x, y, z) { return this.getLight(x, y, z) >> 4; }
  blockLightAt(x, y, z) { return this.getLight(x, y, z) & 15; }
  surfaceY(x, z) {
    for (let y = HEIGHT - 1; y > 0; y--) { const b = this.getBlock(x, y, z); if (b && BLOCKS[b].solid) return y; }
    return 0;
  }

  // ---- terrain functions -----------------------------------------------------
  column(x, z) {
    const cont = this.nCont.fbm2(x / 900, z / 900, 4) * 1.4;
    const ero = this.nErosion.fbm2(x / 500, z / 500, 3);
    const pk = 1 - Math.abs(this.nPeaks.fbm2(x / 280, z / 280, 4));
    const hills = this.nHills.fbm2(x / 140, z / 140, 4);
    const det = this.nHills.n2(x / 32 + 100, z / 32);
    // continentalness spline
    let h;
    if (cont < -0.45) h = lerp(30, 40, (cont + 1) / 0.55);
    else if (cont < -0.18) h = lerp(40, 55, (cont + 0.45) / 0.27);
    else if (cont < -0.06) h = lerp(55, 62, (cont + 0.18) / 0.12);
    else if (cont < 0.05) h = lerp(62, 66, (cont + 0.06) / 0.11);
    else h = lerp(66, 78, clamp((cont - 0.05) / 0.6, 0, 1));
    const land = smoothstep(-0.1, 0.1, cont);
    const mount = smoothstep(0.02, 0.45, cont) * smoothstep(0.05, -0.35, ero);
    h += mount * pk * pk * 95;
    h += hills * (5 + 10 * land) * (0.6 + mount) + det * 2;
    // rivers
    const rv = Math.abs(this.nRiver.fbm2(x / 420, z / 420, 3));
    let river = 0;
    if (cont > -0.12) {
      river = 1 - smoothstep(0.0, 0.045, rv);
      const target = 57;
      if (river > 0 && h > target) h = lerp(h, target, river * (h > 100 ? 0.5 : 1));
    }
    const temp = this.nTemp.fbm2(x / 1100, z / 1100, 3) * 1.6 + this.nSurf.n2(x / 40, z / 40) * 0.03;
    const humid = this.nHumid.fbm2(x / 900, z / 900, 3) * 1.6;
    return { h: Math.floor(h), temp, humid, river, cont, mount };
  }
  biomeOf(col) {
    const h = col.h, t = col.temp, hu = col.humid;
    if (h < SEA - 3) return (col.river > 0.5 && col.cont > -0.12) ? BI.RIVER : (t < -0.5 ? BI.FROZEN_OCEAN : BI.OCEAN);
    if (h <= SEA + 1 && col.river > 0.3) return BI.RIVER;
    if (h <= SEA + 2 && col.cont < 0.02) return t > 0.35 && hu < 0.1 ? BI.DESERT : BI.BEACH;
    if (h > 118) return BI.SNOWPEAK;
    if (h > 98 && col.mount > 0.3) return BI.MOUNTAIN;
    if (t > 0.35 && hu < 0.1) return BI.DESERT;
    if (t < -0.42) return BI.SNOWY;
    if (t < -0.15) return BI.TAIGA;
    if (hu > 0.38 && t > -0.05) return BI.BIRCH;
    if (hu > 0.08) return BI.FOREST;
    return BI.PLAINS;
  }
  tintFor(map, temp, humid, out, o) {
    const T = clamp(0.62 + temp * 0.45, 0, 1), H = clamp(0.5 + humid * 0.5, 0, 1) * T;
    const px = Math.floor((1 - T) * 255), py = Math.floor((1 - H) * 255);
    const i = (py * 256 + px) * 4;
    out[o] = map[i]; out[o + 1] = map[i + 1]; out[o + 2] = map[i + 2];
  }

  // ---- generation ------------------------------------------------------------
  generateFlat(cx, cz) {
    const ch = new Chunk(cx, cz);
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      for (let y = 0; y <= 60; y++) ch.blocks[(y << 8) | (z << 4) | x] = y === 0 ? 6 : y < 57 ? 1 : y < 60 ? 3 : 2;
      ch.biome[z * 16 + x] = BI.PLAINS;
      this.tintFor(this.grassMap, 0.3, 0.2, ch.grass, (z * 16 + x) * 3);
      this.tintFor(this.foliageMap, 0.3, 0.2, ch.foliage, (z * 16 + x) * 3);
    }
    ch.recount();
    return ch;
  }
  generate(cx, cz) {
    if (this.flat) return this.generateFlat(cx, cz);
    const ch = new Chunk(cx, cz);
    const B = ch.blocks, M = ch.meta;
    const x0 = cx * 16, z0 = cz * 16;
    const seed = this.seed;
    // columns incl. 1-block border for slope
    const cols = new Array(18 * 18);
    for (let z = -1; z < 17; z++) for (let x = -1; x < 17; x++) cols[(z + 1) * 18 + x + 1] = this.column(x0 + x, z0 + z);
    let maxH = 0;
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      const c = cols[(z + 1) * 18 + x + 1];
      maxH = Math.max(maxH, c.h);
      const bi = this.biomeOf(c);
      ch.biome[z * 16 + x] = bi;
      this.tintFor(this.grassMap, c.temp, c.humid, ch.grass, (z * 16 + x) * 3);
      this.tintFor(this.foliageMap, c.temp, c.humid, ch.foliage, (z * 16 + x) * 3);
    }
    // cave noise on a coarse grid (4x8x4) then trilinear interpolation
    const gy = Math.min(HEIGHT, maxH + 9) >> 3;
    const GX = 5, GY = gy + 1, GZ = 5;
    const g1 = new Float32Array(GX * GY * GZ), g2 = new Float32Array(GX * GY * GZ), g3 = new Float32Array(GX * GY * GZ);
    for (let ix = 0; ix < GX; ix++) for (let iz = 0; iz < GZ; iz++) for (let iy = 0; iy < GY; iy++) {
      const wx = x0 + ix * 4, wy = iy * 8, wz = z0 + iz * 4;
      const i = (ix * GZ + iz) * GY + iy;
      g1[i] = this.nCave1.n3(wx / 48, wy / 32, wz / 48);
      g2[i] = this.nCave2.n3(wx / 48, wy / 32, wz / 48);
      g3[i] = this.nCave3.n3(wx / 90, wy / 40, wz / 90);
    }
    const sample = (g, x, y, z) => {
      const fx = x / 4, fy = y / 8, fz = z / 4;
      const ix = Math.min(fx | 0, GX - 2), iy = Math.min(fy | 0, GY - 2), iz = Math.min(fz | 0, GZ - 2);
      const tx = fx - ix, ty = fy - iy, tz = fz - iz;
      const i000 = (ix * GZ + iz) * GY + iy, i100 = ((ix + 1) * GZ + iz) * GY + iy;
      const i010 = i000 + 1, i110 = i100 + 1, i001 = i000 + GY, i101 = i100 + GY, i011 = i001 + 1, i111 = i101 + 1;
      const a = lerp(lerp(g[i000], g[i100], tx), lerp(g[i001], g[i101], tx), tz);
      const b = lerp(lerp(g[i010], g[i110], tx), lerp(g[i011], g[i111], tx), tz);
      return lerp(a, b, ty);
    };
    const rng = RNG(hashStr(seed + ':' + cx + ':' + cz));
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      const ci = (z + 1) * 18 + x + 1, c = cols[ci];
      const h = c.h, bi = ch.biome[z * 16 + x];
      const slope = Math.max(Math.abs(cols[ci - 1].h - cols[ci + 1].h), Math.abs(cols[ci - 18].h - cols[ci + 18].h));
      const wx = x0 + x, wz = z0 + z;
      let top, filler, fillDepth = 3 + ((hash3(wx, 0, wz, seed) * 2) | 0);
      if (bi === BI.DESERT) { top = 9; filler = 9; fillDepth = 4; }
      else if (bi === BI.BEACH) { top = 9; filler = 9; }
      else if (bi === BI.OCEAN || bi === BI.FROZEN_OCEAN || bi === BI.RIVER) {
        const n = this.nSurf.n2(wx / 12, wz / 12);
        top = h < SEA - 1 ? (n > 0.35 ? 10 : (n < -0.5 ? 36 : 9)) : 9; filler = top === 36 ? 36 : 9;
        if (bi === BI.RIVER && h >= SEA - 1) { top = 9; }
      }
      else if (bi === BI.MOUNTAIN || bi === BI.SNOWPEAK) { top = slope > 3 ? 1 : 2; filler = slope > 3 ? 1 : 3; if (h > 135 + hash3(wx, 1, wz, seed) * 6) { top = 33; } }
      else { top = 2; filler = 3; }
      if (slope > 5 && top === 2) { top = 1; filler = 1; }
      for (let y = 0; y <= h && y < HEIGHT; y++) {
        let id;
        if (y === 0) id = 6;
        else if (y < 4 && hash3(wx, y, wz, seed) < (4 - y) / 4) id = 6;
        else if (y === h) id = top;
        else if (y > h - fillDepth) id = filler;
        else if (bi === BI.DESERT && y > h - fillDepth - 3) id = 17;
        else id = (y < 8 || (y < 16 && hash3(wx, y, wz, seed + 77) < (16 - y) / 8)) ? 75 : 1;
        // caves
        if (y > 4 && id !== 6) {
          const nearSurf = y > h - 5;
          const wet = h < SEA + 2;
          if (!(wet && y > h - 9)) {
            const a = sample(g1, x, y, z), b = sample(g2, x, y, z);
            const tun = a * a + b * b;
            let carve = tun < (nearSurf ? 0.006 : 0.011);
            if (!carve && y < 52 && !nearSurf) carve = sample(g3, x, y, z) > 0.62 - (52 - y) * 0.004;
            if (carve) id = y <= 10 ? LAVA : AIR;
          }
        }
        B[(y << 8) | (z << 4) | x] = id;
      }
      // water
      for (let y = h + 1; y <= SEA; y++) {
        const i = (y << 8) | (z << 4) | x;
        if (B[i] === AIR) B[i] = (y === SEA && (bi === BI.FROZEN_OCEAN || (bi === BI.SNOWY && c.temp < -0.5))) ? 35 : WATER;
      }
      // snow cover
      if ((bi === BI.SNOWY || bi === BI.SNOWPEAK || (bi === BI.MOUNTAIN && h > 125)) && h >= SEA && h + 1 < HEIGHT) {
        const ti = (h << 8) | (z << 4) | x;
        if (B[ti] === 2) M[ti] = 1;
        if (B[ti] !== AIR && B[ti] !== WATER && B[ti] !== 35 && B[ti] !== 33 && B[(h + 1 << 8) | (z << 4) | x] === AIR) B[((h + 1) << 8) | (z << 4) | x] = 34;
      }
    }
    this.genOres(ch, rng);
    if (rng() < 0.11) this.genDungeon(ch, cols, rng);
    this.decorate(ch, cols);
    ch.recount();
    return ch;
  }

  genOres(ch, rng) {
    const B = ch.blocks;
    const vein = (ore, deepOre, count, ymin, ymax, size) => {
      for (let n = 0; n < count; n++) {
        let x = rng() * 16, y = ymin + rng() * (ymax - ymin), z = rng() * 16;
        const s = size * (0.6 + rng() * 0.6);
        for (let i = 0; i < s; i++) {
          const ix = x | 0, iy = y | 0, iz = z | 0;
          if (ix >= 0 && ix < 16 && iz >= 0 && iz < 16 && iy > 0 && iy < HEIGHT) {
            const k = (iy << 8) | (iz << 4) | ix;
            if (B[k] === 1) B[k] = ore; else if (B[k] === 75 && deepOre) B[k] = deepOre;
          }
          x += rng() * 2 - 1; y += rng() * 2 - 1; z += rng() * 2 - 1;
        }
      }
    };
    vein(74, 74, 6, 0, 90, 30); vein(73, 73, 5, 0, 90, 30); vein(72, 72, 6, 0, 90, 30);
    vein(3, 0, 6, 10, 100, 24); vein(10, 0, 5, 5, 100, 24);
    vein(13, 76, 22, 5, 130, 12);
    vein(12, 77, 16, 1, 72, 9);
    vein(11, 78, 3, 1, 32, 8);
    vein(80, 0, 6, 1, 16, 7);
    vein(81, 0, 2, 1, 32, 6);
    vein(18, 79, 2, 1, 16, 6);
  }

  genDungeon(ch, cols, rng) {
    const B = ch.blocks, M = ch.meta;
    const lx0 = rng.int(7), lz0 = rng.int(7), W = 8 + rng.int(2), D = 8 + rng.int(2);
    if (lx0 + W > 15 || lz0 + D > 15) return;
    let minH = 999;
    for (let z = lz0; z <= lz0 + D; z++) for (let x = lx0; x <= lx0 + W; x++) minH = Math.min(minH, cols[(z + 1) * 18 + x + 1].h);
    const y0 = 8 + rng.int(Math.max(1, Math.min(40, minH - 20)));
    if (y0 + 12 > minH) return;
    for (let y = 0; y <= 5; y++) for (let z = 0; z <= D; z++) for (let x = 0; x <= W; x++) {
      const i = ((y0 + y) << 8) | ((lz0 + z) << 4) | (lx0 + x);
      const edge = x === 0 || z === 0 || x === W || z === D || y === 0 || y === 5;
      if (edge) { if (y === 5 && B[i] === AIR) continue; B[i] = (y === 0 && rng() < 0.6) || rng() < 0.25 ? 24 : 4; }
      else B[i] = AIR;
      M[i] = 0;
    }
    // openings so caves can connect
    const chests = 1 + (rng() < 0.5 ? 1 : 0);
    const spots = [[1, Math.floor(D / 2), 1], [W - 1, Math.floor(D / 2), 0], [Math.floor(W / 2), 1, 5], [Math.floor(W / 2), D - 1, 4]];
    for (let k = 0; k < chests; k++) {
      const [x, z, face] = spots[rng.int(4)];
      const i = ((y0 + 1) << 8) | ((lz0 + z) << 4) | (lx0 + x);
      B[i] = 52; M[i] = face | 8;
    }
    // a few cobwebs-like decorations: mossy pillars
    for (let k = 0; k < 3; k++) { const i = ((y0 + 1) << 8) | ((lz0 + 2 + rng.int(D - 3)) << 4) | (lx0 + 2 + rng.int(W - 3)); if (B[i] === AIR && rng() < 0.5) B[i] = 70 + rng.int(2); }
    ch.dungeon = [ch.cx * 16 + lx0 + W / 2, y0 + 1, ch.cz * 16 + lz0 + D / 2];
  }

  // Trees are deterministic per chunk so neighbours can place their overhanging parts.
  treesFor(ncx, ncz) {
    const k = ckey(ncx, ncz);
    if (!this._treeCache) this._treeCache = new Map();
    let t = this._treeCache.get(k);
    if (t) return t;
    t = [];
    const r = RNG(hashStr('trees' + this.seed + ':' + ncx + ':' + ncz));
    const cc = this.column(ncx * 16 + 8, ncz * 16 + 8);
    const bi = this.biomeOf(cc);
    const dens = { [BI.FOREST]: 9, [BI.BIRCH]: 9, [BI.TAIGA]: 7, [BI.SNOWY]: 1.5, [BI.PLAINS]: 0.25, [BI.MOUNTAIN]: 0.6, [BI.RIVER]: 0.3 }[bi] || 0;
    let n = Math.floor(dens + r());
    for (let i = 0; i < n; i++) {
      const lx = r.int(16), lz = r.int(16);
      const X = ncx * 16 + lx, Z = ncz * 16 + lz;
      const c = this.column(X, Z);
      const b = this.biomeOf(c);
      if (c.h <= SEA || c.h > 125) continue;
      if (b === BI.DESERT || b === BI.BEACH || b === BI.OCEAN || b === BI.SNOWPEAK) continue;
      // slope check
      const c2 = this.column(X + 1, Z), c3 = this.column(X, Z + 1);
      if (Math.abs(c2.h - c.h) > 2 || Math.abs(c3.h - c.h) > 2) continue;
      let type = 'oak';
      if (b === BI.TAIGA || b === BI.SNOWY || (b === BI.MOUNTAIN)) type = 'spruce';
      else if (b === BI.BIRCH) type = r() < 0.85 ? 'birch' : 'oak';
      else if (b === BI.FOREST) type = r() < 0.25 ? 'birch' : (r() < 0.1 ? 'big' : 'oak');
      t.push({ x: X, y: c.h + 1, z: Z, type, h: 4 + r.int(3), seed: r.int(1 << 30) });
    }
    if (this._treeCache.size > 4000) this._treeCache.clear();
    this._treeCache.set(k, t);
    return t;
  }

  placeTree(set, tr) {
    const { x, y, z, type } = tr;
    const r = RNG(tr.seed);
    if (type === 'oak' || type === 'birch' || type === 'big') {
      const log = type === 'birch' ? 46 : 14, leaf = type === 'birch' ? 47 : 15;
      const h = type === 'birch' ? tr.h + 1 : tr.h + (type === 'big' ? 2 : 0);
      for (let dy = h - 3; dy <= h; dy++) {
        const rad = dy >= h - 1 ? 1 : 2;
        for (let dx = -rad; dx <= rad; dx++) for (let dz = -rad; dz <= rad; dz++) {
          if (Math.abs(dx) === rad && Math.abs(dz) === rad && (dy === h || r() < 0.5)) continue;
          set(x + dx, y + dy, z + dz, leaf, 0, false);
        }
      }
      if (type === 'big') for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) if (!dx || !dz) set(x + dx, y + h + 1, z + dz, leaf, 0, false);
      for (let dy = 0; dy < h; dy++) set(x, y + dy, z, log, 0, true);
      set(x, y - 1, z, 3, 0, true);
    } else if (type === 'spruce') {
      const h = tr.h + 3;
      let rad = 0;
      for (let dy = h; dy >= 2; dy--) {
        for (let dx = -rad; dx <= rad; dx++) for (let dz = -rad; dz <= rad; dz++) {
          if (Math.abs(dx) + Math.abs(dz) > rad + (rad > 1 ? 1 : 0)) continue;
          set(x + dx, y + dy, z + dz, 50, 0, false);
        }
        rad = rad >= 2 ? 1 : rad + 1;
        if (dy === h) rad = 1;
      }
      set(x, y + h + 1, z, 50, 0, false);
      for (let dy = 0; dy < h; dy++) set(x, y + dy, z, 49, 0, true);
      set(x, y - 1, z, 3, 0, true);
    }
  }

  decorate(ch, cols) {
    const B = ch.blocks, M = ch.meta, x0 = ch.cx * 16, z0 = ch.cz * 16;
    const set = (wx, wy, wz, id, meta, force) => {
      const lx = wx - x0, lz = wz - z0;
      if (lx < 0 || lx > 15 || lz < 0 || lz > 15 || wy < 1 || wy >= HEIGHT) return;
      const i = (wy << 8) | (lz << 4) | lx, cur = B[i];
      if (force ? (cur !== 6) : (cur === AIR || cur === 34 || cur === 39 || cur === 40)) { B[i] = id; M[i] = meta; }
    };
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      for (const tr of this.treesFor(ch.cx + dx, ch.cz + dz)) {
        if (tr.x < x0 - 3 || tr.x > x0 + 18 || tr.z < z0 - 3 || tr.z > z0 + 18) continue;
        // only grow if the trunk base is on ground in its own chunk (checked loosely via column height)
        if (dx === 0 && dz === 0) {
          const g = B[((tr.y - 1) << 8) | ((tr.z - z0) << 4) | (tr.x - x0)];
          if (g !== 2 && g !== 3) continue;
        }
        this.placeTree(set, tr);
      }
    }
    // ground plants
    const r = RNG(hashStr('plants' + this.seed + ':' + ch.cx + ':' + ch.cz));
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      const c = cols[(z + 1) * 18 + x + 1];
      const h = c.h; if (h + 1 >= HEIGHT || h < SEA) continue;
      const bi = ch.biome[z * 16 + x];
      const gi = (h << 8) | (z << 4) | x, ai = ((h + 1) << 8) | (z << 4) | x;
      if (B[ai] !== AIR) continue;
      const g = B[gi], v = r();
      if (g === 2) {
        if (bi === BI.PLAINS) {
          if (v < 0.25) B[ai] = 39;
          else if (v < 0.28) B[ai] = [41, 42, 98, 44, 101, 43][(this.nSurf.n2((x0 + x) / 30, (z0 + z) / 30) * 3 + 3) | 0] || 41;
          else if (v < 0.2805) B[ai] = 53;
        } else if (bi === BI.FOREST || bi === BI.BIRCH) {
          if (v < 0.12) B[ai] = 39; else if (v < 0.135) B[ai] = [41, 42, 99, 102, 100][r.int(5)];
          else if (v < 0.138) B[ai] = r() < 0.5 ? 70 : 71;
        } else if (bi === BI.TAIGA) {
          if (v < 0.08) B[ai] = 40; else if (v < 0.1) B[ai] = 39; else if (v < 0.103) B[ai] = 71;
        } else if (bi === BI.MOUNTAIN) {
          if (v < 0.05) B[ai] = 39;
        } else if (bi === BI.RIVER || bi === BI.BEACH) {
          if (v < 0.1) B[ai] = 39;
        }
      } else if (g === 9 && bi === BI.DESERT) {
        if (v < 0.006) { const hh = 1 + r.int(3); for (let k = 1; k <= hh && h + k < HEIGHT; k++) B[((h + k) << 8) | (z << 4) | x] = 37; }
        else if (v < 0.014) B[ai] = 45;
      }
      // sugar cane next to water
      if ((g === 2 || g === 9 || g === 3) && h <= SEA + 1 && r() < 0.12) {
        let wet = false;
        for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nx = x + dx, nz = z + dz;
          if (nx >= 0 && nx < 16 && nz >= 0 && nz < 16 && B[(h << 8) | (nz << 4) | nx] === WATER) wet = true;
        }
        if (wet) { const hh = 1 + r.int(3); for (let k = 1; k <= hh; k++) B[((h + k) << 8) | (z << 4) | x] = 38; }
      }
    }
  }

  // ---- lighting -------------------------------------------------------------
  initLight(ch) {
    const B = ch.blocks, L = ch.light;
    L.fill(0);
    const heights = new Int16Array(256);
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      let l = 15, top = 0;
      for (let y = HEIGHT - 1; y >= 0; y--) {
        const i = (y << 8) | (z << 4) | x, op = OPACITY[B[i]];
        if (op >= 15) l = 0; else if (op > 0 || l < 15) l = Math.max(0, l - Math.max(1, op));
        if (l === 15) top = y;
        L[i] = l << 4;
        if (l === 0) break;
      }
      heights[z * 16 + x] = top;
    }
    ch.heights = heights;
    const q = this.lightQ; q.length = 0;
    const x0 = ch.cx * 16, z0 = ch.cz * 16;
    // horizontal sky seeds
    for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      const h = heights[z * 16 + x];
      let mx = h;
      if (x > 0) mx = Math.max(mx, heights[z * 16 + x - 1]); if (x < 15) mx = Math.max(mx, heights[z * 16 + x + 1]);
      if (z > 0) mx = Math.max(mx, heights[z * 16 + x - 16]); if (z < 15) mx = Math.max(mx, heights[z * 16 + x + 16]);
      for (let y = h; y <= mx && y < HEIGHT; y++) if ((L[(y << 8) | (z << 4) | x] >> 4) > 1) q.push(x0 + x, y, z0 + z);
    }
    this.propagate(q, 4);
    // block light
    q.length = 0;
    for (let i = 0; i < B.length; i++) {
      const e = EMIT[B[i]];
      if (e) { L[i] = (L[i] & 0xF0) | e; q.push(x0 + (i & 15), i >> 8, z0 + ((i >> 4) & 15)); }
    }
    this.propagate(q, 0);
    // exchange with neighbours
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const n = this.getChunk(ch.cx + dx, ch.cz + dz);
      if (!n) continue;
      const qs = [], qb = [];
      for (let y = 0; y < HEIGHT; y++) for (let k = 0; k < 16; k++) {
        let ax, az, bx, bz; // a in ch, b in n (local)
        if (dx === 1) { ax = 15; az = k; bx = 0; bz = k; } else if (dx === -1) { ax = 0; az = k; bx = 15; bz = k; }
        else if (dz === 1) { ax = k; az = 15; bx = k; bz = 0; } else { ax = k; az = 0; bx = k; bz = 15; }
        const la = ch.light[(y << 8) | (az << 4) | ax], lb = n.light[(y << 8) | (bz << 4) | bx];
        if ((la >> 4) > 1) qs.push(x0 + ax, y, z0 + az);
        if ((lb >> 4) > 1) qs.push(n.cx * 16 + bx, y, n.cz * 16 + bz);
        if ((la & 15) > 1) qb.push(x0 + ax, y, z0 + az);
        if ((lb & 15) > 1) qb.push(n.cx * 16 + bx, y, n.cz * 16 + bz);
      }
      this.propagate(qs, 4);
      this.propagate(qb, 0);
    }
    ch.lit = true;
  }

  markLight(ch, lx, y, lz) {
    const s = 1 << (y >> 4);
    let m = s; const yy = y & 15;
    if (yy === 0 && y > 0) m |= s >> 1; else if (yy === 15) m |= s << 1;
    ch.dirty |= m;
    if (lx === 0 || lx === 15 || lz === 0 || lz === 15) {
      const dx = lx === 0 ? -1 : lx === 15 ? 1 : 0, dz = lz === 0 ? -1 : lz === 15 ? 1 : 0;
      if (dx) { const n = this.chunks.get(ckey(ch.cx + dx, ch.cz)); if (n) n.dirty |= m; }
      if (dz) { const n = this.chunks.get(ckey(ch.cx, ch.cz + dz)); if (n) n.dirty |= m; }
      if (dx && dz) { const n = this.chunks.get(ckey(ch.cx + dx, ch.cz + dz)); if (n) n.dirty |= m; }
    }
  }

  // BFS light propagation; q holds flat x,y,z triples. shift 4 = sky, 0 = block
  propagate(q, shift) {
    const sky = shift === 4;
    let head = 0;
    let cache = null, ccx = 0, ccz = 0;
    while (head < q.length) {
      const x = q[head++], y = q[head++], z = q[head++];
      const cx = x >> 4, cz = z >> 4;
      if (!cache || cx !== ccx || cz !== ccz) { cache = this.chunks.get(ckey(cx, cz)); ccx = cx; ccz = cz; }
      if (!cache) continue;
      const l = (cache.light[(y << 8) | ((z & 15) << 4) | (x & 15)] >> shift) & 15;
      if (l <= 1) continue;
      for (let d = 0; d < 6; d++) {
        const D = DIRS[d];
        const nx = x + D[0], ny = y + D[1], nz = z + D[2];
        if (ny < 0 || ny >= HEIGHT) continue;
        const nc = (nx >> 4 === cx && nz >> 4 === cz) ? cache : this.chunks.get(ckey(nx >> 4, nz >> 4));
        if (!nc) continue;
        const i = (ny << 8) | ((nz & 15) << 4) | (nx & 15);
        const op = OPACITY[nc.blocks[i]];
        if (op >= 15) continue;
        const nl = (sky && d === 2 && l === 15 && op === 0) ? 15 : l - Math.max(1, op);
        const cur = (nc.light[i] >> shift) & 15;
        if (nl > cur) {
          nc.light[i] = sky ? ((nc.light[i] & 0x0F) | (nl << 4)) : ((nc.light[i] & 0xF0) | nl);
          this.markLight(nc, nx & 15, ny, nz & 15);
          q.push(nx, ny, nz);
        }
      }
    }
    q.length = 0;
  }

  // light update after block change at x,y,z
  relight(x, y, z) {
    for (const shift of [4, 0]) {
      const sky = shift === 4;
      const rem = [], add = [];
      const ch = this.getChunk(x >> 4, z >> 4);
      const i0 = (y << 8) | ((z & 15) << 4) | (x & 15);
      const v0 = (ch.light[i0] >> shift) & 15;
      if (v0 > 0) {
        ch.light[i0] &= sky ? 0x0F : 0xF0;
        this.markLight(ch, x & 15, y, z & 15);
        rem.push(x, y, z, v0);
      }
      let h = 0;
      while (h < rem.length) {
        const rx = rem[h++], ry = rem[h++], rz = rem[h++], rv = rem[h++];
        for (let d = 0; d < 6; d++) {
          const D = DIRS[d];
          const nx = rx + D[0], ny = ry + D[1], nz = rz + D[2];
          if (ny < 0 || ny >= HEIGHT) continue;
          const nc = this.chunks.get(ckey(nx >> 4, nz >> 4));
          if (!nc) continue;
          const i = (ny << 8) | ((nz & 15) << 4) | (nx & 15);
          const nl = (nc.light[i] >> shift) & 15;
          if (nl === 0) continue;
          if (nl < rv || (sky && d === 2 && rv === 15 && nl === 15)) {
            nc.light[i] &= sky ? 0x0F : 0xF0;
            this.markLight(nc, nx & 15, ny, nz & 15);
            rem.push(nx, ny, nz, nl);
          } else add.push(nx, ny, nz);
        }
      }
      // emission
      if (!sky) {
        const e = EMIT[ch.blocks[i0]];
        if (e) { ch.light[i0] = (ch.light[i0] & 0xF0) | e; add.push(x, y, z); }
      } else if (y === HEIGHT - 1 && OPACITY[ch.blocks[i0]] === 0) { ch.light[i0] |= 0xF0; add.push(x, y, z); }
      for (let d = 0; d < 6; d++) add.push(x + DIRS[d][0], Math.max(0, Math.min(HEIGHT - 1, y + DIRS[d][1])), z + DIRS[d][2]);
      this.propagate(add, shift);
    }
  }

  // ---- block modification -----------------------------------------------------
  markBlock(ch, lx, y, lz) {
    const s = 1 << (y >> 4); let m = s;
    const yy = y & 15;
    if (yy === 0 && y > 0) m |= s >> 1; else if (yy === 15) m |= s << 1;
    ch.dirty |= m;
    if (lx === 0 || lx === 15 || lz === 0 || lz === 15) {
      const dx = lx === 0 ? -1 : lx === 15 ? 1 : 0, dz = lz === 0 ? -1 : lz === 15 ? 1 : 0;
      if (dx) { const n = this.chunks.get(ckey(ch.cx + dx, ch.cz)); if (n) n.dirty |= m; }
      if (dz) { const n = this.chunks.get(ckey(ch.cx, ch.cz + dz)); if (n) n.dirty |= m; }
      if (dx && dz) { const n = this.chunks.get(ckey(ch.cx + dx, ch.cz + dz)); if (n) n.dirty |= m; }
    }
  }

  // flags: 1 = notify neighbours, 2 = skip light
  setBlock(x, y, z, id, meta = 0, flags = 1) {
    if (y < 0 || y >= HEIGHT) return false;
    const ch = this.getChunk(x >> 4, z >> 4);
    if (!ch) return false;
    const i = (y << 8) | ((z & 15) << 4) | (x & 15);
    const old = ch.blocks[i], oldMeta = ch.meta[i];
    if (old === id && oldMeta === meta) return false;
    ch.blocks[i] = id; ch.meta[i] = meta;
    if (!old && id) ch.sectionCount[y >> 4]++; else if (old && !id) ch.sectionCount[y >> 4]--;
    ch.modified = true;
    this.markBlock(ch, x & 15, y, z & 15);
    if (!(flags & 2) && (OPACITY[old] !== OPACITY[id] || EMIT[old] !== EMIT[id])) this.relight(x, y, z);
    if (flags & 1) {
      this.notify(x, y, z, old);
      for (let d = 0; d < 6; d++) this.notify(x + DIRS[d][0], y + DIRS[d][1], z + DIRS[d][2], -1);
    }
    if (this.listener) this.listener.onBlockChanged(x, y, z, old, id, oldMeta);
    return true;
  }
  setMeta(x, y, z, meta) {
    const ch = this.getChunk(x >> 4, z >> 4);
    if (!ch || y < 0 || y >= HEIGHT) return;
    const i = (y << 8) | ((z & 15) << 4) | (x & 15);
    if (ch.meta[i] === meta) return;
    ch.meta[i] = meta; ch.modified = true;
    this.markBlock(ch, x & 15, y, z & 15);
  }

  notify(x, y, z, changedOld) {
    if (y < 0 || y >= HEIGHT) return;
    const id = this.getBlock(x, y, z);
    if (IS_FLUID[id]) this.schedule(x, y, z, id === WATER ? 5 : 30);
    if (this.listener && id) this.listener.onNeighborUpdate(x, y, z, id);
  }

  schedule(x, y, z, delay) {
    const k = `${x},${y},${z}`;
    if (!this.ticks.has(k)) this.ticks.set(k, { x, y, z, t: this.time + delay });
  }

  tickScheduled() {
    if (!this.ticks.size) return;
    const due = [];
    for (const [k, v] of this.ticks) if (v.t <= this.time) { due.push(v); this.ticks.delete(k); if (due.length > 400) break; }
    for (const t of due) this.fluidTick(t.x, t.y, t.z);
  }

  canFlowInto(id) { return id === AIR || (REPLACEABLE[id] && !IS_FLUID[id]) || (BLOCKS[id] && BLOCKS[id].needsSupport && !BLOCKS[id].solid && id !== 21 ? true : id === 21); }

  fluidTick(x, y, z) {
    if (!this.isLoaded(x, z)) return;
    const id = this.getBlock(x, y, z);
    if (!IS_FLUID[id]) return;
    let meta = this.getMeta(x, y, z);
    const water = id === WATER, drop = water ? 1 : 2, other = water ? LAVA : WATER;
    // fluid contact
    if (!water) {
      for (let d = 0; d < 6; d++) {
        if (d === 2) continue;
        if (this.getBlock(x + DIRS[d][0], y + DIRS[d][1], z + DIRS[d][2]) === WATER) {
          this.setBlock(x, y, z, meta === 0 ? 28 : 4, 0);
          if (this.listener) this.listener.onFizz(x, y, z);
          return;
        }
      }
    }
    if (meta !== 0) {
      let nm;
      const above = this.getBlock(x, y + 1, z);
      if (above === id) nm = 8;
      else {
        let min = 8, sources = 0;
        for (const d of [0, 1, 4, 5]) {
          const nx = x + DIRS[d][0], nz = z + DIRS[d][2];
          if (this.getBlock(nx, y, nz) !== id) continue;
          const m = this.getMeta(nx, y, nz);
          if (m === 0) sources++;
          min = Math.min(min, (m & 8) ? 0 : (m & 7));
        }
        const below = this.getBlock(x, y - 1, z);
        if (water && sources >= 2 && (BLOCKS[below].solid || (below === WATER && this.getMeta(x, y - 1, z) === 0))) nm = 0;
        else if (min + drop >= 8) nm = -1;
        else nm = min + drop;
      }
      if (nm !== meta) {
        if (nm < 0) { this.setBlock(x, y, z, AIR, 0); return; }
        this.setBlock(x, y, z, id, nm);
        meta = nm;
      }
    }
    // spread down
    const bid = this.getBlock(x, y - 1, z);
    if (y > 0 && (this.canFlowInto(bid) || bid === other)) {
      if (bid === other) { this.setBlock(x, y - 1, z, water ? (this.getMeta(x, y - 1, z) === 0 ? 28 : 1) : 1, 0); if (this.listener) this.listener.onFizz(x, y - 1, z); }
      else { if (bid && this.listener) this.listener.onFluidDestroy(x, y - 1, z, bid); this.setBlock(x, y - 1, z, id, 8); }
      if (meta !== 0) return;
    }
    if (bid === id && meta !== 0) return;
    const lvl = (meta & 8) ? 0 : meta;
    const nl = lvl + drop;
    if (nl >= 8) return;
    // prefer directions leading to a drop within 4 blocks
    const dirs = [0, 1, 4, 5];
    let best = 99; const dist = [99, 99, 99, 99];
    dirs.forEach((d, k) => {
      const nx = x + DIRS[d][0], nz = z + DIRS[d][2];
      const nid = this.getBlock(nx, y, nz);
      if (!(this.canFlowInto(nid) || (nid === id && (this.getMeta(nx, y, nz) & 7) > nl && !(this.getMeta(nx, y, nz) & 8)))) return;
      for (let s = 1; s <= (water ? 4 : 2); s++) {
        const px = x + DIRS[d][0] * s, pz = z + DIRS[d][2] * s;
        const pid = this.getBlock(px, y, pz);
        if (BLOCKS[pid].solid && pid !== id) break;
        const under = this.getBlock(px, y - 1, pz);
        if (this.canFlowInto(under) || under === id) { dist[k] = s; break; }
      }
      if (dist[k] === 99) dist[k] = 50;
      best = Math.min(best, dist[k]);
    });
    dirs.forEach((d, k) => {
      if (dist[k] === 99 || dist[k] !== best) return;
      const nx = x + DIRS[d][0], nz = z + DIRS[d][2];
      const nid = this.getBlock(nx, y, nz);
      if (nid === other) { this.setBlock(nx, y, nz, water ? (this.getMeta(nx, y, nz) === 0 ? 28 : 4) : 1, 0); if (this.listener) this.listener.onFizz(nx, y, nz); return; }
      if (nid && nid !== id && this.listener) this.listener.onFluidDestroy(nx, y, nz, nid);
      this.setBlock(nx, y, nz, id, nl);
    });
  }

  // ---- random ticks ----------------------------------------------------------
  randomTick(px, pz, rng) {
    const pcx = px >> 4, pcz = pz >> 4;
    for (let dx = -4; dx <= 4; dx++) for (let dz = -4; dz <= 4; dz++) {
      const ch = this.getChunk(pcx + dx, pcz + dz);
      if (!ch || !ch.lit) continue;
      for (let s = 0; s < SECTIONS; s++) {
        if (!ch.sectionCount[s]) continue;
        for (let k = 0; k < 3; k++) {
          const lx = rng.int(16), ly = (s << 4) + rng.int(16), lz = rng.int(16);
          const id = ch.blocks[(ly << 8) | (lz << 4) | lx];
          if (id && this.listener) this.listener.onRandomTick(ch.cx * 16 + lx, ly, ch.cz * 16 + lz, id, ch);
        }
      }
    }
  }
}
