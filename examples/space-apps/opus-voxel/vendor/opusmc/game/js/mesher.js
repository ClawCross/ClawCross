'use strict';
// ---------------------------------------------------------------------------
// Chunk section mesher. Vertex layout (32 bytes):
//  0 pos f32x3 | 12 uv f32x2 | 20 layer u16, overlay u16 | 24 rgb+shade u8x4 | 28 sky u8, block u8, anim u8, pad
// ---------------------------------------------------------------------------
const VSTRIDE = 32;
const FACE_SHADE = [0.6, 0.6, 0.5, 1.0, 0.8, 0.8];
const AO_CURVE = [0.42, 0.62, 0.8, 1.0];
// face corners (BL, BR, TR, TL as seen from outside) and uv
const FACES = [
  { n: [-1, 0, 0], c: [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]], uv: (x, y, z) => [z, 1 - y] },
  { n: [1, 0, 0], c: [[1, 0, 1], [1, 0, 0], [1, 1, 0], [1, 1, 1]], uv: (x, y, z) => [1 - z, 1 - y] },
  { n: [0, -1, 0], c: [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], uv: (x, y, z) => [x, 1 - z] },
  { n: [0, 1, 0], c: [[0, 1, 0], [0, 1, 1], [1, 1, 1], [1, 1, 0]], uv: (x, y, z) => [x, z] },
  { n: [0, 0, -1], c: [[1, 0, 0], [0, 0, 0], [0, 1, 0], [1, 1, 0]], uv: (x, y, z) => [1 - x, 1 - y] },
  { n: [0, 0, 1], c: [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], uv: (x, y, z) => [x, 1 - y] },
];
const ANIM_FRAMES = new Uint8Array(1024);
function initAnimTable() {
  for (const k in ASSETS.anims) ANIM_FRAMES[ASSETS.tiles[k]] = ASSETS.anims[k];
}

class MeshBuffer {
  constructor(cap) { this.alloc(cap); this.n = 0; }
  alloc(cap) {
    const buf = new ArrayBuffer(cap * VSTRIDE);
    if (this.buf) new Uint8Array(buf).set(new Uint8Array(this.buf, 0, this.n * VSTRIDE));
    this.buf = buf; this.cap = cap;
    this.f = new Float32Array(buf); this.u16 = new Uint16Array(buf); this.u8 = new Uint8Array(buf);
  }
  reset() { this.n = 0; }
  // push one vertex
  v(x, y, z, u, v, layer, overlay, r, g, b, shade, sky, blk, anim) {
    if (this.n >= this.cap) this.alloc(this.cap * 2);
    const o = this.n * 8, o16 = this.n * 16, o8 = this.n * 32;
    const f = this.f;
    f[o] = x; f[o + 1] = y; f[o + 2] = z; f[o + 3] = u; f[o + 4] = v;
    this.u16[o16 + 10] = layer; this.u16[o16 + 11] = overlay;
    const u8 = this.u8;
    u8[o8 + 24] = r; u8[o8 + 25] = g; u8[o8 + 26] = b; u8[o8 + 27] = shade;
    u8[o8 + 28] = sky; u8[o8 + 29] = blk; u8[o8 + 30] = anim; u8[o8 + 31] = 0;
    this.n++;
  }
  slice() { return new Uint8Array(this.buf.slice(0, this.n * VSTRIDE)); }
}

const Mesher = {
  ids: new Uint8Array(18 * 18 * 18),
  metas: new Uint8Array(18 * 18 * 18),
  lights: new Uint8Array(18 * 18 * 18),
  tints: new Uint8Array(18 * 18 * 6), // grass rgb + foliage rgb per padded column
  opaqueBuf: new MeshBuffer(65536),
  transBuf: new MeshBuffer(16384),
  P(x, y, z) { return ((y + 1) * 18 + (z + 1)) * 18 + (x + 1); },

  // Fill padded arrays for section (cx, sy, cz)
  gather(world, cx, sy, cz) {
    const ids = this.ids, metas = this.metas, lights = this.lights, tints = this.tints;
    const y0 = sy * 16;
    for (let dcz = -1; dcz <= 1; dcz++) for (let dcx = -1; dcx <= 1; dcx++) {
      const ch = world.getChunk(cx + dcx, cz + dcz);
      const xa = dcx < 0 ? 15 : 0, xb = dcx > 0 ? 0 : 15, za = dcz < 0 ? 15 : 0, zb = dcz > 0 ? 0 : 15;
      for (let lz = za; lz <= zb; lz++) for (let lx = xa; lx <= xb; lx++) {
        const px = lx + dcx * 16, pz = lz + dcz * 16;
        const ti = ((pz + 1) * 18 + (px + 1)) * 6;
        if (ch) {
          const ci = (lz * 16 + lx) * 3;
          tints[ti] = ch.grass[ci]; tints[ti + 1] = ch.grass[ci + 1]; tints[ti + 2] = ch.grass[ci + 2];
          tints[ti + 3] = ch.foliage[ci]; tints[ti + 4] = ch.foliage[ci + 1]; tints[ti + 5] = ch.foliage[ci + 2];
        }
        for (let py = -1; py <= 16; py++) {
          const y = y0 + py, pi = ((py + 1) * 18 + (pz + 1)) * 18 + (px + 1);
          if (!ch || y < 0) { ids[pi] = y < 0 ? 6 : 0; metas[pi] = 0; lights[pi] = y < 0 ? 0 : 0xF0; continue; }
          if (y >= HEIGHT) { ids[pi] = 0; metas[pi] = 0; lights[pi] = 0xF0; continue; }
          const i = (y << 8) | (lz << 4) | lx;
          ids[pi] = ch.blocks[i]; metas[pi] = ch.meta[i]; lights[pi] = ch.light[i];
        }
      }
    }
  },

  tintOf(b, x, z) {
    const t = this.tints, ti = ((z + 1) * 18 + (x + 1)) * 6;
    switch (b.tint) {
      case 'grass': return [t[ti], t[ti + 1], t[ti + 2]];
      case 'foliage': return [t[ti + 3], t[ti + 4], t[ti + 5]];
      case 'birch': return [128, 167, 85];
      case 'spruce': return [97, 153, 97];
      case 'water': return [63, 118, 228];
    }
    return [255, 255, 255];
  },

  build(world, cx, sy, cz) {
    this.gather(world, cx, sy, cz);
    const ob = this.opaqueBuf, tb = this.transBuf;
    ob.reset(); tb.reset();
    const ids = this.ids;
    const ox = cx * 16, oy = sy * 16, oz = cz * 16;
    for (let y = 0; y < 16; y++) for (let z = 0; z < 16; z++) for (let x = 0; x < 16; x++) {
      const id = ids[((y + 1) * 18 + (z + 1)) * 18 + (x + 1)];
      if (!id) continue;
      this.block(id, x, y, z, ox, oy, oz);
    }
    return { opaque: ob.n ? ob.slice() : null, trans: tb.n ? tb.slice() : null, nOpaque: ob.n, nTrans: tb.n };
  },

  // Mesh a single block at local (0,0,0) with full light (icons, dropped items, held blocks)
  single(id, meta, fullBright = true) {
    this.ids.fill(0); this.metas.fill(0); this.lights.fill(fullBright ? 0xF0 : 0xF0);
    for (let i = 0; i < this.tints.length; i += 6) { this.tints.set([124, 189, 107, 89, 174, 48], i); }
    const p = this.P(0, 0, 0);
    this.ids[p] = id; this.metas[p] = meta;
    if (BLOCKS[id].door) { this.ids[this.P(0, 1, 0)] = 0; }
    const ob = this.opaqueBuf, tb = this.transBuf;
    ob.reset(); tb.reset();
    this.block(id, 0, 0, 0, 0, 0, 0, true);
    const out = new MeshBuffer(ob.n + tb.n + 4);
    out.u8.set(ob.u8.subarray(0, ob.n * VSTRIDE), 0);
    out.u8.set(tb.u8.subarray(0, tb.n * VSTRIDE), ob.n * VSTRIDE);
    out.n = ob.n + tb.n;
    return out;
  },

  block(id, x, y, z, ox, oy, oz, single) {
    const b = BLOCKS[id];
    const meta = this.metas[this.P(x, y, z)];
    switch (b.shape) {
      case SH_CUBE: this.cube(b, id, meta, x, y, z, ox, oy, oz, single); break;
      case SH_CROSS: this.cross(b, meta, x, y, z, ox, oy, oz, single); break;
      case SH_CROP: this.crop(b, meta, x, y, z, ox, oy, oz); break;
      case SH_LIQUID: this.liquid(b, id, meta, x, y, z, ox, oy, oz, single); break;
      case SH_BOXES: this.boxes(b, id, meta, x, y, z, ox, oy, oz, single); break;
      case SH_TORCH: this.torch(b, meta, x, y, z, ox, oy, oz); break;
      case SH_CACTUS: this.cactus(b, meta, x, y, z, ox, oy, oz); break;
      case SH_MODEL: this.modelShape(b, meta, x, y, z, ox, oy, oz, single); break;
    }
  },

  texOf(b, face, meta) { return b.texFn ? b.texFn.call(b, face, meta) : b.tex[face]; },

  cube(b, id, meta, x, y, z, ox, oy, oz, single) {
    const ids = this.ids, lights = this.lights;
    const buf = b.render === 'translucent' ? this.transBuf : this.opaqueBuf;
    let tint = b.tint ? this.tintOf(b, x, z) : null;
    for (let f = 0; f < 6; f++) {
      const F = FACES[f], n = F.n;
      const nid = ids[this.P(x + n[0], y + n[1], z + n[2])];
      if (SOLIDCUBE[nid]) continue;
      if (b.cullSame && nid === id) continue;
      if (b.render === 'translucent' && IS_FLUID[nid]) continue; // avoids order-dependent blending against water
      if (b.render === 'translucent' && BLOCKS[nid].render === 'translucent' && SOLIDCUBE[nid] === 0 && nid !== 0 && BLOCKS[nid].shape === SH_CUBE && id !== 35) continue;
      let layer = this.texOf(b, f, meta);
      let overlay = b.overlay[f];
      let r = 255, g = 255, bl = 255;
      if (id === 2) {
        if ((meta & 1) && f !== 2 && f !== 3) overlay = 0;
        else if (f === 3) { r = tint[0]; g = tint[1]; bl = tint[2]; }
        // side: base untinted, overlay tinted (shader uses rgb for overlay when overlay present)
        if (overlay) { r = tint[0]; g = tint[1]; bl = tint[2]; }
      } else if (tint) { r = tint[0]; g = tint[1]; bl = tint[2]; }
      const rot = b.axis && (meta & 3) ? this.axisRot(meta & 3, f) : 0;
      this.quadAO(buf, F, x, y, z, ox, oy, oz, layer, overlay, r, g, bl, f, rot, single);
    }
  },

  axisRot(a, f) {
    if (a === 1) return (f === 0 || f === 1) ? 0 : 1; // x-axis log: rotate side textures on y/z faces
    if (a === 2) return (f === 4 || f === 5) ? 0 : (f === 2 || f === 3 ? 0 : 1);
    return 0;
  },

  // Quad with smooth lighting + ambient occlusion
  quadAO(buf, F, x, y, z, ox, oy, oz, layer, overlay, r, g, bl, f, rot, single) {
    const ids = this.ids, lights = this.lights, n = F.n;
    const fx = x + n[0], fy = y + n[1], fz = z + n[2];
    const ao = [0, 0, 0, 0], sk = [0, 0, 0, 0], bk = [0, 0, 0, 0];
    const anim = ANIM_FRAMES[layer];
    for (let k = 0; k < 4; k++) {
      const c = F.c[k];
      // tangent offsets
      let s1x = 0, s1y = 0, s1z = 0, s2x = 0, s2y = 0, s2z = 0;
      if (n[0]) { s1y = c[1] ? 1 : -1; s2z = c[2] ? 1 : -1; }
      else if (n[1]) { s1x = c[0] ? 1 : -1; s2z = c[2] ? 1 : -1; }
      else { s1x = c[0] ? 1 : -1; s2y = c[1] ? 1 : -1; }
      const p0 = this.P(fx, fy, fz);
      const p1 = this.P(fx + s1x, fy + s1y, fz + s1z);
      const p2 = this.P(fx + s2x, fy + s2y, fz + s2z);
      const p3 = this.P(fx + s1x + s2x, fy + s1y + s2y, fz + s1z + s2z);
      const o1 = SOLIDCUBE[ids[p1]], o2 = SOLIDCUBE[ids[p2]], o3 = SOLIDCUBE[ids[p3]];
      ao[k] = single ? 3 : (o1 && o2 ? 0 : 3 - o1 - o2 - o3);
      let ls = lights[p0] >> 4, lb = lights[p0] & 15, cnt = 1;
      if (!o1) { ls += lights[p1] >> 4; lb += lights[p1] & 15; cnt++; }
      if (!o2) { ls += lights[p2] >> 4; lb += lights[p2] & 15; cnt++; }
      if (!o3 && !(o1 && o2)) { ls += lights[p3] >> 4; lb += lights[p3] & 15; cnt++; }
      sk[k] = ls / cnt; bk[k] = lb / cnt;
    }
    const shade = FACE_SHADE[f];
    const order = (ao[0] + ao[2] + (sk[0] + sk[2]) * 0.01 < ao[1] + ao[3] + (sk[1] + sk[3]) * 0.01) ? [1, 2, 3, 0] : [0, 1, 2, 3];
    for (const k of order) {
      const c = F.c[k];
      let uv = F.uv(c[0], c[1], c[2]);
      if (rot) uv = [1 - uv[1], uv[0]];
      buf.v(ox + x + c[0], oy + y + c[1], oz + z + c[2], uv[0], uv[1], layer, overlay, r, g, bl,
        Math.round(shade * AO_CURVE[ao[k]] * 255), Math.round(sk[k] * 17), Math.round(bk[k] * 17), anim);
    }
  },

  // Flat-lit quad from explicit corners (array of 4 [x,y,z,u,v])
  quadFlat(buf, pts, layer, overlay, r, g, bl, shade, light, anim = 0) {
    const s = Math.round((light >> 4) * 17), k = Math.round((light & 15) * 17), sh = Math.round(shade * 255);
    for (const p of pts) buf.v(p[0], p[1], p[2], p[3], p[4], layer, overlay, r, g, bl, sh, s, k, anim);
  },

  cross(b, meta, x, y, z, ox, oy, oz, single) {
    const buf = this.opaqueBuf, light = this.lights[this.P(x, y, z)];
    const layer = this.texOf(b, 0, meta);
    const t = b.tint ? this.tintOf(b, x, z) : [255, 255, 255];
    let jx = 0, jz = 0;
    if (!single && (b.key === 'short_grass' || b.key === 'fern' || FLOWER_IDS.includes(b.id))) {
      const h = hash3(ox + x, oy + y, oz + z, 99);
      jx = (h - 0.5) * 0.4; jz = (hash3(oz + z, ox + x, oy + y, 7) - 0.5) * 0.4;
    }
    const X = ox + x + jx, Y = oy + y, Z = oz + z + jz, d = 0.15, e = 0.85;
    const quads = [
      [[X + d, Y, Z + d, 0, 1], [X + e, Y, Z + e, 1, 1], [X + e, Y + 1, Z + e, 1, 0], [X + d, Y + 1, Z + d, 0, 0]],
      [[X + e, Y, Z + e, 0, 1], [X + d, Y, Z + d, 1, 1], [X + d, Y + 1, Z + d, 1, 0], [X + e, Y + 1, Z + e, 0, 0]],
      [[X + d, Y, Z + e, 0, 1], [X + e, Y, Z + d, 1, 1], [X + e, Y + 1, Z + d, 1, 0], [X + d, Y + 1, Z + e, 0, 0]],
      [[X + e, Y, Z + d, 0, 1], [X + d, Y, Z + e, 1, 1], [X + d, Y + 1, Z + e, 1, 0], [X + e, Y + 1, Z + d, 0, 0]],
    ];
    for (const q of quads) this.quadFlat(buf, q, layer, 0, t[0], t[1], t[2], 0.9, light);
  },

  crop(b, meta, x, y, z, ox, oy, oz) {
    const buf = this.opaqueBuf, light = this.lights[this.P(x, y, z)];
    const layer = this.texOf(b, 0, meta);
    const X = ox + x, Y = oy + y - 1 / 16, Z = oz + z;
    for (const o of [0.25, 0.75]) {
      for (const flip of [0, 1]) {
        const a = flip ? 1 : 0, c = flip ? 0 : 1;
        this.quadFlat(buf, [[X + o, Y, Z + a, 0, 1], [X + o, Y, Z + c, 1, 1], [X + o, Y + 1, Z + c, 1, 0], [X + o, Y + 1, Z + a, 0, 0]], layer, 0, 255, 255, 255, 0.85, light);
        this.quadFlat(buf, [[X + c, Y, Z + o, 0, 1], [X + a, Y, Z + o, 1, 1], [X + a, Y + 1, Z + o, 1, 0], [X + c, Y + 1, Z + o, 0, 0]], layer, 0, 255, 255, 255, 0.85, light);
      }
    }
  },

  fluidHeight(id, x, y, z) {
    // height of fluid column at padded cell; -1 if not fluid
    const p = this.P(x, y, z);
    if (this.ids[p] !== id) return SOLIDCUBE[this.ids[p]] ? -2 : -1;
    if (this.ids[this.P(x, y + 1, z)] === id) return 1;
    const m = this.metas[p];
    if (m & 8) return 0.9;
    return m === 0 ? 0.889 : (8 - m) / 9;
  },
  cornerHeight(id, x, y, z, dx, dz) {
    // average heights of the 4 columns around corner (x+dx, z+dz) where dx,dz in {0,1}
    let sum = 0, cnt = 0;
    for (const [ax, az] of [[dx - 1, dz - 1], [dx, dz - 1], [dx - 1, dz], [dx, dz]]) {
      const h = this.fluidHeight(id, x + ax, y, z + az);
      if (h === 1) return 1;
      if (h >= 0) { sum += h; cnt++; }
      else if (h === -1 && this.ids[this.P(x + ax, y, z + az)] === 0) { cnt++; }
    }
    return cnt ? sum / cnt : 0.889;
  },

  liquid(b, id, meta, x, y, z, ox, oy, oz, single) {
    const buf = b.render === 'translucent' ? this.transBuf : this.opaqueBuf;
    const ids = this.ids, layer = b.tex[0], anim = ANIM_FRAMES[layer];
    const t = b.tint ? this.tintOf(b, x, z) : [255, 255, 255];
    const above = ids[this.P(x, y + 1, z)];
    let h00, h10, h01, h11;
    if (above === id || single) h00 = h10 = h01 = h11 = single ? 0.889 : 1;
    else {
      h00 = this.cornerHeight(id, x, y, z, 0, 0); h10 = this.cornerHeight(id, x, y, z, 1, 0);
      h01 = this.cornerHeight(id, x, y, z, 0, 1); h11 = this.cornerHeight(id, x, y, z, 1, 1);
    }
    const X = ox + x, Y = oy + y, Z = oz + z;
    const L = (dx, dy, dz) => this.lights[this.P(x + dx, y + dy, z + dz)];
    const own = this.lights[this.P(x, y, z)];
    const lmax = (a, c) => (Math.max(a >> 4, c >> 4) << 4) | Math.max(a & 15, c & 15);
    // top
    if (above !== id) {
      const l = lmax(L(0, 1, 0), own);
      const e = 0.001;
      this.quadFlat(buf, [[X, Y + h00 - e, Z, 0, 0], [X, Y + h01 - e, Z + 1, 0, 1], [X + 1, Y + h11 - e, Z + 1, 1, 1], [X + 1, Y + h10 - e, Z, 1, 0]], layer, 0, t[0], t[1], t[2], 1, l, anim);
      if (b.render === 'translucent') // underside visible from below the surface
        this.quadFlat(buf, [[X + 1, Y + h10 - e, Z, 1, 0], [X + 1, Y + h11 - e, Z + 1, 1, 1], [X, Y + h01 - e, Z + 1, 0, 1], [X, Y + h00 - e, Z, 0, 0]], layer, 0, t[0], t[1], t[2], 0.8, l, anim);
    }
    // bottom
    const below = ids[this.P(x, y - 1, z)];
    if (below !== id && !SOLIDCUBE[below]) {
      this.quadFlat(buf, [[X, Y, Z, 0, 0], [X + 1, Y, Z, 1, 0], [X + 1, Y, Z + 1, 1, 1], [X, Y, Z + 1, 0, 1]], layer, 0, t[0], t[1], t[2], 0.5, lmax(L(0, -1, 0), own), anim);
    }
    // sides
    // [face, dx, dz, BL(x,z), BR(x,z), height at BR, height at BL]
    const sides = [
      [0, -1, 0, X, Z, X, Z + 1, h01, h00],
      [1, 1, 0, X + 1, Z + 1, X + 1, Z, h10, h11],
      [4, 0, -1, X + 1, Z, X, Z, h00, h10],
      [5, 0, 1, X, Z + 1, X + 1, Z + 1, h11, h01],
    ];
    for (const [f, dx, dz, ax, az, bx, bz, hb, ha] of sides) {
      const nid = ids[this.P(x + dx, y, z + dz)];
      if (nid === id || SOLIDCUBE[nid]) continue;
      const q = [[ax, Y, az, 0, 1], [bx, Y, bz, 1, 1], [bx, Y + hb, bz, 1, 1 - hb], [ax, Y + ha, az, 0, 1 - ha]];
      this.quadFlat(buf, q, layer, 0, t[0], t[1], t[2], FACE_SHADE[f], lmax(L(dx, 0, dz), own), anim);
    }
  },

  boxes(b, id, meta, x, y, z, ox, oy, oz, single) {
    const buf = b.render === 'translucent' ? this.transBuf : this.opaqueBuf;
    const ids = this.ids, lights = this.lights;
    const own = lights[this.P(x, y, z)];
    const tint = b.tint ? this.tintOf(b, x, z) : [255, 255, 255];
    const list = b.boxes(meta);
    for (const bx of list) {
      const x0 = bx[0] / 16, y0 = bx[1] / 16, z0 = bx[2] / 16, x1 = bx[3] / 16, y1 = bx[4] / 16, z1 = bx[5] / 16;
      for (let f = 0; f < 6; f++) {
        const n = FACES[f].n;
        const onEdge = (f === 0 && x0 === 0) || (f === 1 && x1 === 1) || (f === 2 && y0 === 0) || (f === 3 && y1 === 1) || (f === 4 && z0 === 0) || (f === 5 && z1 === 1);
        let light = own;
        if (onEdge) {
          const nid = ids[this.P(x + n[0], y + n[1], z + n[2])];
          if (SOLIDCUBE[nid] && !single) continue;
          if (nid === id && b.cullSame) continue;
          light = lights[this.P(x + n[0], y + n[1], z + n[2])];
          if (SOLIDCUBE[nid]) light = own;
        }
        if (b.slab && (meta & 3) === 2 && !onEdge) continue;
        if (single) light = 0xF0;
        const layer = this.texOf(b, f, meta);
        const pts = FACES[f].c.map(c => {
          const px = c[0] ? x1 : x0, py = c[1] ? y1 : y0, pz = c[2] ? z1 : z0;
          const uv = FACES[f].uv(px, py, pz);
          return [ox + x + px, oy + y + py, oz + z + pz, uv[0], uv[1]];
        });
        this.quadFlat(buf, pts, layer, 0, tint[0], tint[1], tint[2], FACE_SHADE[f], light);
      }
    }
  },

  modelShape(b, meta, x, y, z, ox, oy, oz, single) {
    const buf = this.opaqueBuf, ids = this.ids, lights = this.lights;
    const own = lights[this.P(x, y, z)];
    const { els, rot } = b.model(meta);
    const FN = { west: 0, east: 1, down: 2, up: 3, north: 4, south: 5 };
    const rotDir = [[0, 1, 2, 3, 4, 5], [4, 5, 2, 3, 1, 0], [1, 0, 2, 3, 5, 4], [5, 4, 2, 3, 0, 1]][rot]; // model face -> world face
    const rp = (px, pz) => { // rotate point around block centre, clockwise seen from above
      for (let k = 0; k < rot; k++) { const t = px; px = 1 - pz; pz = t; }
      return [px, pz];
    };
    for (const e of els) {
      const x0 = e.from[0] / 16, y0 = e.from[1] / 16, z0 = e.from[2] / 16, x1 = e.to[0] / 16, y1 = e.to[1] / 16, z1 = e.to[2] / 16;
      for (const fname in e.faces) {
        const f = FN[fname], [u0, v0, u1, v1, layer] = e.faces[fname];
        const wf = rotDir[f];
        const n = FACES[wf].n;
        const onEdge = (f === 0 && x0 === 0) || (f === 1 && x1 === 1) || (f === 2 && y0 === 0) || (f === 3 && y1 === 1) || (f === 4 && z0 === 0) || (f === 5 && z1 === 1);
        let light = own;
        if (onEdge && !single) {
          const nid = ids[this.P(x + n[0], y + n[1], z + n[2])];
          if (SOLIDCUBE[nid]) continue;
          light = lights[this.P(x + n[0], y + n[1], z + n[2])];
        }
        if (single) light = 0xF0;
        const pts = FACES[f].c.map((c) => {
          const px = c[0] ? x1 : x0, py = c[1] ? y1 : y0, pz = c[2] ? z1 : z0;
          const U = FACES[f].uv(c[0], c[1], c[2]);
          const [rx, rz] = rp(px, pz);
          return [ox + x + rx, oy + y + py, oz + z + rz, (u0 + U[0] * (u1 - u0)) / 16, (v0 + U[1] * (v1 - v0)) / 16];
        });
        this.quadFlat(buf, pts, layer, 0, 255, 255, 255, FACE_SHADE[wf], light);
      }
    }
  },

  // several blocks at given local offsets (icons for multi-block items)
  multi(list) {
    this.ids.fill(0); this.metas.fill(0); this.lights.fill(0xF0);
    for (const [id, meta, x, y, z] of list) { const p = this.P(x, y, z); this.ids[p] = id; this.metas[p] = meta; }
    const ob = this.opaqueBuf; ob.reset(); this.transBuf.reset();
    for (const [id, meta, x, y, z] of list) this.block(id, x, y, z, 0, 0, 0, true);
    const out = new MeshBuffer(ob.n + 4);
    out.u8.set(ob.u8.subarray(0, ob.n * VSTRIDE), 0); out.n = ob.n;
    return out;
  },

  cactus(b, meta, x, y, z, ox, oy, oz) {
    const buf = this.opaqueBuf, light = this.lights[this.P(x, y, z)];
    const X = ox + x, Y = oy + y, Z = oz + z, i = 1 / 16;
    for (let f = 0; f < 6; f++) {
      const F = FACES[f], n = F.n;
      if ((f === 2 || f === 3) && SOLIDCUBE[this.ids[this.P(x + n[0], y + n[1], z + n[2])]]) continue;
      if ((f === 3 || f === 2) && this.ids[this.P(x + n[0], y + n[1], z + n[2])] === b.id) continue;
      const pts = F.c.map(c => {
        let px = c[0], py = c[1], pz = c[2];
        if (f === 0) px = i; if (f === 1) px = 1 - i; if (f === 4) pz = i; if (f === 5) pz = 1 - i;
        const uv = F.uv(c[0], c[1], c[2]);
        return [X + px, Y + py, Z + pz, uv[0], uv[1]];
      });
      this.quadFlat(buf, pts, b.tex[f], 0, 255, 255, 255, FACE_SHADE[f], light);
    }
  },

  torch(b, meta, x, y, z, ox, oy, oz) {
    const buf = this.opaqueBuf, light = 0xF0 | 14 | (this.lights[this.P(x, y, z)] & 0xF0);
    const layer = b.tex[0];
    const X = ox + x + 0.5, Z = oz + z + 0.5;
    let bx = 0, bz = 0, by = 0, lean = 0, lx = 0, lz = 0;
    if (meta) {
      const D = DIRS[meta];
      bx = D[0] * 0.38; bz = D[2] * 0.38; by = 0.2; lean = 0.32; lx = -D[0]; lz = -D[2];
    }
    const Y = oy + y + by;
    const w = 1 / 16, h = 10 / 16;
    const P = (px, py, pz) => [X + bx + px + lx * lean * py / h * 0.6, Y + py, Z + bz + pz + lz * lean * py / h * 0.6];
    const u0 = 7 / 16, u1 = 9 / 16, v0 = 6 / 16, v1 = 1;
    const sides = [
      [[-w, 0, -w], [-w, 0, w], [-w, h, w], [-w, h, -w], 0],
      [[w, 0, w], [w, 0, -w], [w, h, -w], [w, h, w], 1],
      [[w, 0, -w], [-w, 0, -w], [-w, h, -w], [w, h, -w], 4],
      [[-w, 0, w], [w, 0, w], [w, h, w], [-w, h, w], 5],
    ];
    for (const s of sides) {
      const pts = [[...P(...s[0]), u0, v1], [...P(...s[1]), u1, v1], [...P(...s[2]), u1, v0], [...P(...s[3]), u0, v0]];
      this.quadFlat(buf, pts, layer, 0, 255, 255, 255, FACE_SHADE[s[4]], light);
    }
    const top = [[...P(-w, h, -w), u0, 8 / 16], [...P(-w, h, w), u0, 6 / 16], [...P(w, h, w), u1, 6 / 16], [...P(w, h, -w), u1, 8 / 16]];
    this.quadFlat(buf, top, layer, 0, 255, 255, 255, 1, light);
    const bot = [[...P(-w, 0, -w), u0, 1], [...P(w, 0, -w), u1, 1], [...P(w, 0, w), u1, 14 / 16], [...P(-w, 0, w), u0, 14 / 16]];
    this.quadFlat(buf, bot, layer, 0, 255, 255, 255, 0.5, light);
  },
};

// Extruded item sprite mesh (for held items and dropped items). Returns MeshBuffer in 0..1 xy, z in [-1/32,1/32]
const ItemMeshCache = new Map();
let _spriteAlpha = null;
function itemSpriteMesh(layer) {
  if (ItemMeshCache.has(layer)) return ItemMeshCache.get(layer);
  const a = _spriteAlpha;
  const buf = new MeshBuffer(512);
  const t = 1 / 32, px = 1 / 16;
  const opaque = (x, y) => x >= 0 && y >= 0 && x < 16 && y < 16 && a[layer * 256 + y * 16 + x] > 0;
  const V = (x, y, z, u, v, sh) => buf.v(x, y, z, u, v, layer, 0, 255, 255, 255, Math.round(sh * 255), 255, 0, 0);
  // front (+z) and back
  V(0, 0, t, 0, 1, 1); V(1, 0, t, 1, 1, 1); V(1, 1, t, 1, 0, 1); V(0, 1, t, 0, 0, 1);
  V(1, 0, -t, 1, 1, 0.8); V(0, 0, -t, 0, 1, 0.8); V(0, 1, -t, 0, 0, 0.8); V(1, 1, -t, 1, 0, 0.8);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    if (!opaque(x, y)) continue;
    const X0 = x * px, X1 = (x + 1) * px, Y1 = 1 - y * px, Y0 = 1 - (y + 1) * px;
    const uc = (x + 0.5) / 16, vc = (y + 0.5) / 16;
    if (!opaque(x - 1, y)) { V(X0, Y0, -t, uc, vc, 0.6); V(X0, Y0, t, uc, vc, 0.6); V(X0, Y1, t, uc, vc, 0.6); V(X0, Y1, -t, uc, vc, 0.6); }
    if (!opaque(x + 1, y)) { V(X1, Y0, t, uc, vc, 0.6); V(X1, Y0, -t, uc, vc, 0.6); V(X1, Y1, -t, uc, vc, 0.6); V(X1, Y1, t, uc, vc, 0.6); }
    if (!opaque(x, y - 1)) { V(X0, Y1, t, uc, vc, 1); V(X1, Y1, t, uc, vc, 1); V(X1, Y1, -t, uc, vc, 1); V(X0, Y1, -t, uc, vc, 1); }
    if (!opaque(x, y + 1)) { V(X0, Y0, -t, uc, vc, 0.5); V(X1, Y0, -t, uc, vc, 0.5); V(X1, Y0, t, uc, vc, 0.5); V(X0, Y0, t, uc, vc, 0.5); }
  }
  ItemMeshCache.set(layer, buf);
  return buf;
}
