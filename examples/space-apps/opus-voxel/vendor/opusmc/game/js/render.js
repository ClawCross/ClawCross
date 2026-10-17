'use strict';
// ---------------------------------------------------------------------------
// WebGL2 renderer
// ---------------------------------------------------------------------------
const CHUNK_VS = `#version 300 es
layout(location=0) in vec3 a_pos;
layout(location=1) in vec2 a_uv;
layout(location=2) in uvec2 a_tex;
layout(location=3) in vec4 a_col;
layout(location=4) in vec2 a_light;
layout(location=5) in uint a_anim;
uniform mat4 u_vp; uniform mat4 u_model;
uniform vec3 u_cam; uniform float u_tick; uniform vec3 u_lightOv; // x>=0: override sky/block
out vec2 v_uv; flat out uint v_layer; flat out uint v_overlay; out vec4 v_col; out vec2 v_light; out float v_dist;
void main(){
  vec4 wp = u_model * vec4(a_pos,1.0);
  gl_Position = u_vp * wp;
  v_uv = a_uv;
  uint layer = a_tex.x;
  if (a_anim > 0u) layer += uint(floor(u_tick / 2.0)) % a_anim;
  v_layer = layer; v_overlay = a_tex.y;
  v_col = a_col;
  v_light = u_lightOv.z > 0.5 ? u_lightOv.xy : a_light;
  v_dist = length(wp.xz - u_cam.xz);
  float dy = abs(wp.y - u_cam.y); v_dist = max(v_dist, dy * 0.5);
}`;
const CHUNK_FS = `#version 300 es
precision highp float; precision highp sampler2DArray;
uniform sampler2DArray u_tex;
uniform float u_day; uniform vec3 u_fogColor; uniform vec2 u_fog; uniform float u_alphaTest; uniform vec4 u_colorMul;
uniform float u_gamma; uniform vec3 u_nightTint;
in vec2 v_uv; flat in uint v_layer; flat in uint v_overlay; in vec4 v_col; in vec2 v_light; in float v_dist;
out vec4 o;
float bright(float x){ float a = x / (4.0 - 3.0 * x); float b = 1.0 - pow(1.0 - x, 3.0); return mix(a, b, u_gamma); }
void main(){
  vec4 t = texture(u_tex, vec3(v_uv, float(v_layer)));
  vec3 c;
  if (v_overlay > 0u) {
    vec4 ov = texture(u_tex, vec3(v_uv, float(v_overlay)));
    c = mix(t.rgb, ov.rgb * v_col.rgb, ov.a);
  } else c = t.rgb * v_col.rgb;
  if (t.a < u_alphaTest) discard;
  float sky = bright(v_light.x * u_day);
  float blk = bright(v_light.y);
  vec3 skyC = mix(u_nightTint, vec3(1.0), u_day) * sky;
  vec3 blkC = vec3(1.0, 0.88, 0.7) * blk * 1.05;
  vec3 L = max(skyC, blkC);
  L = max(L, vec3(mix(0.03, 0.14, u_gamma)));
  c *= L * v_col.a;
  float f = clamp((v_dist - u_fog.x) / (u_fog.y - u_fog.x), 0.0, 1.0);
  c = mix(c, u_fogColor, f);
  o = vec4(c, t.a) * u_colorMul;
}`;

const ENT_VS = `#version 300 es
layout(location=0) in vec3 a_pos; layout(location=1) in vec2 a_uv; layout(location=2) in vec3 a_n;
uniform mat4 u_vp; uniform mat4 u_model; uniform vec3 u_cam;
out vec2 v_uv; out float v_shade; out float v_dist;
void main(){
  vec4 wp = u_model * vec4(a_pos,1.0);
  gl_Position = u_vp * wp;
  v_uv = a_uv;
  vec3 n = normalize(mat3(u_model) * a_n);
  v_shade = 0.62 + 0.38 * clamp(dot(n, normalize(vec3(0.3,1.0,0.5))), 0.0, 1.0) + 0.1 * abs(n.x);
  v_dist = length(wp.xz - u_cam.xz);
}`;
const ENT_FS = `#version 300 es
precision highp float;
uniform sampler2D u_tex; uniform vec2 u_light; uniform float u_day; uniform vec4 u_overlay; uniform vec3 u_tint;
uniform vec3 u_fogColor; uniform vec2 u_fog; uniform float u_gamma; uniform vec3 u_nightTint;
in vec2 v_uv; in float v_shade; in float v_dist; out vec4 o;
float bright(float x){ float a = x / (4.0 - 3.0 * x); float b = 1.0 - pow(1.0 - x, 3.0); return mix(a, b, u_gamma); }
void main(){
  vec4 t = texture(u_tex, v_uv);
  if (t.a < 0.1) discard;
  vec3 c = t.rgb * u_tint;
  c = mix(c, u_overlay.rgb, u_overlay.a);
  vec3 L = max(mix(u_nightTint, vec3(1.0), u_day) * bright(u_light.x * u_day), vec3(1.0,0.86,0.68) * bright(u_light.y));
  c *= max(L, vec3(mix(0.03, 0.14, u_gamma))) * min(v_shade, 1.0);
  float f = clamp((v_dist - u_fog.x) / (u_fog.y - u_fog.x), 0.0, 1.0);
  o = vec4(mix(c, u_fogColor, f), t.a);
}`;

const SKY_VS = `#version 300 es
const vec2 P[3] = vec2[3](vec2(-1.0,-1.0), vec2(3.0,-1.0), vec2(-1.0,3.0));
out vec2 v_p;
void main(){ v_p = P[gl_VertexID]; gl_Position = vec4(P[gl_VertexID], 0.9999, 1.0); }`;
const SKY_FS = `#version 300 es
precision highp float;
uniform mat4 u_invVP; uniform vec3 u_sky; uniform vec3 u_fogColor; uniform vec3 u_sunDir; uniform vec4 u_sunset; uniform vec3 u_void;
in vec2 v_p; out vec4 o;
void main(){
  vec4 a = u_invVP * vec4(v_p, 1.0, 1.0);
  vec3 d = normalize(a.xyz / a.w);
  float h = d.y;
  vec3 c = mix(u_fogColor, u_sky, smoothstep(0.0, 0.45, h));
  if (h < 0.0) c = mix(u_fogColor, u_void, smoothstep(-0.12, -0.6, h));
  // sunrise / sunset glow
  vec3 sd = normalize(vec3(u_sunDir.x, 0.0, u_sunDir.z) + vec3(0.0001));
  float g = pow(max(dot(normalize(vec3(d.x, 0.0, d.z)), sd), 0.0), 3.0) * (1.0 - smoothstep(0.0, 0.5, abs(h - 0.05)));
  c = mix(c, u_sunset.rgb, clamp(g * u_sunset.a, 0.0, 1.0));
  o = vec4(c, 1.0);
}`;

const TEX_VS = `#version 300 es
layout(location=0) in vec3 a_pos; layout(location=1) in vec2 a_uv;
uniform mat4 u_vp; uniform mat4 u_model;
out vec2 v_uv;
void main(){ v_uv = a_uv; gl_Position = u_vp * u_model * vec4(a_pos, 1.0); }`;
const TEX_FS = `#version 300 es
precision highp float;
uniform sampler2D u_tex; uniform vec4 u_color;
in vec2 v_uv; out vec4 o;
void main(){ vec4 t = texture(u_tex, v_uv); o = t * u_color; }`;

const COL_VS = `#version 300 es
layout(location=0) in vec3 a_pos; layout(location=1) in vec4 a_col;
uniform mat4 u_vp; uniform mat4 u_model; uniform vec3 u_cam; uniform float u_psize;
out vec4 v_col; out float v_dist;
void main(){ vec4 wp = u_model * vec4(a_pos,1.0); gl_Position = u_vp * wp; v_col = a_col; v_dist = length(wp.xz - u_cam.xz); gl_PointSize = u_psize; }`;
const COL_FS = `#version 300 es
precision highp float;
uniform vec4 u_color; uniform vec3 u_fogColor; uniform vec2 u_fog;
in vec4 v_col; in float v_dist; out vec4 o;
void main(){ vec4 c = v_col * u_color; float f = clamp((v_dist - u_fog.x) / (u_fog.y - u_fog.x), 0.0, 1.0); o = vec4(mix(c.rgb, u_fogColor, f), c.a * (1.0 - f * 0.0)); }`;

function compile(gl, vs, fs) {
  const mk = (type, src) => {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) + '\n' + src);
    return s;
  };
  const p = gl.createProgram();
  gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  const u = {};
  const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); u[info.name] = gl.getUniformLocation(p, info.name); }
  return { p, u };
}

function loadImage(src) {
  return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; });
}

class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, preserveDrawingBuffer: false, powerPreference: 'high-performance' });
    if (!gl) throw new Error('WebGL2 不可用');
    this.gl = gl;
    this.aniso = gl.getExtension('EXT_texture_filter_anisotropic');
    this.progChunk = compile(gl, CHUNK_VS, CHUNK_FS);
    this.progEnt = compile(gl, ENT_VS, ENT_FS);
    this.progSky = compile(gl, SKY_VS, SKY_FS);
    this.progTex = compile(gl, TEX_VS, TEX_FS);
    this.progCol = compile(gl, COL_VS, COL_FS);
    this.proj = M4.create(); this.view = M4.create(); this.vp = M4.create();
    this.identity = M4.create();
    this.fov = 70; this.renderDist = 8;
    this.camPos = [0, 0, 0];
    this.sections = new Map(); // key -> {vao, n, transVao, tn, x,y,z}
    this.emptyVao = gl.createVertexArray();
    this.gamma = 0.5;
    this.entityTex = {};
    this.modelCache = {};
    this.particleBuf = new MeshBuffer(4096);
    this.particleGL = this.makeDynamic();
    this.miscBuf = new MeshBuffer(1024);
    this.miscGL = this.makeDynamic();
  }

  async init() {
    const gl = this.gl;
    // ---- texture array
    const strip = await loadImage(ASSETS.strip);
    const n = ASSETS.count;
    const cv = document.createElement('canvas'); cv.width = 16; cv.height = 16 * n;
    const cx = cv.getContext('2d'); cx.drawImage(strip, 0, 0);
    const data = cx.getImageData(0, 0, 16, 16 * n).data;
    this.stripCanvas = cv;
    _spriteAlpha = new Uint8Array(n * 256);
    for (let i = 0; i < n * 256; i++) _spriteAlpha[i] = data[i * 4 + 3];
    this.tileData = data;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, tex);
    const levels = 5;
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, levels, gl.RGBA8, 16, 16, n);
    let cur = new Uint8Array(data.buffer.slice(0)), size = 16;
    // pre-multiply-free mip generation that keeps color of transparent texels sane
    for (let i = 0; i < n * 256; i++) if (cur[i * 4 + 3] === 0) { cur[i * 4] = cur[i * 4 + 1] = cur[i * 4 + 2] = 0; }
    this.fillTransparent(cur, 16, n);
    gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, 0, 16, 16, n, gl.RGBA, gl.UNSIGNED_BYTE, cur);
    for (let l = 1; l < levels; l++) {
      const ns = size >> 1;
      const nx = new Uint8Array(ns * ns * n * 4);
      for (let k = 0; k < n; k++) for (let y = 0; y < ns; y++) for (let x = 0; x < ns; x++) {
        let r = 0, g = 0, b = 0, a = 0, w = 0;
        for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
          const i = ((k * size + y * 2 + dy) * size + x * 2 + dx) * 4;
          const al = cur[i + 3];
          a += al;
          const ww = al + 1;
          r += cur[i] * ww; g += cur[i + 1] * ww; b += cur[i + 2] * ww; w += ww;
        }
        const o = ((k * ns + y) * ns + x) * 4;
        nx[o] = r / w; nx[o + 1] = g / w; nx[o + 2] = b / w; nx[o + 3] = a / 4;
      }
      gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, l, 0, 0, 0, ns, ns, n, gl.RGBA, gl.UNSIGNED_BYTE, nx);
      cur = nx; size = ns;
    }
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.NEAREST_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAX_LEVEL, levels - 1);
    // anisotropic filtering is intentionally off: on D3D (ANGLE) it also makes magnification linear and blurs the pixel art
    this.texArray = tex;
    initAnimTable();

    // ---- 2D images
    this.images = {};
    for (const k in ASSETS.images) this.images[k] = await loadImage(ASSETS.images[k]);
    for (const k of ['pig', 'cow', 'sheep', 'sheep_wool', 'chicken', 'zombie', 'skeleton', 'creeper', 'spider', 'spider_eyes', 'steve', 'sun', 'moon0', 'moon1', 'moon2', 'moon3', 'moon4', 'moon5', 'moon6', 'moon7', 'arrow_entity', 'shadow', 'rain', 'snowfall']) {
      this.entityTex[k] = this.tex2D(this.images[k], k === 'rain' || k === 'snowfall');
    }
    // colormaps
    const cm = (img) => { const c = document.createElement('canvas'); c.width = 256; c.height = 256; const x = c.getContext('2d'); x.drawImage(img, 0, 0); return x.getImageData(0, 0, 256, 256).data; };
    this.grassMap = cm(this.images.grass_color); this.foliageMap = cm(this.images.foliage_color);
    // clouds
    {
      const c = document.createElement('canvas'); c.width = 256; c.height = 256; const x = c.getContext('2d');
      x.drawImage(this.images.clouds, 0, 0);
      const d = x.getImageData(0, 0, 256, 256).data;
      this.cloudMap = new Uint8Array(256 * 256);
      for (let i = 0; i < 65536; i++) this.cloudMap[i] = d[i * 4 + 3] > 128 ? 1 : 0;
    }
    // shared quad index buffer
    const maxQuads = 1 << 18;
    const idx = new Uint32Array(maxQuads * 6);
    for (let i = 0; i < maxQuads; i++) { const v = i * 4, o = i * 6; idx[o] = v; idx[o + 1] = v + 1; idx[o + 2] = v + 2; idx[o + 3] = v; idx[o + 4] = v + 2; idx[o + 5] = v + 3; }
    this.quadIdx = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    this.initStars();
    this.lineVao = gl.createVertexArray(); this.lineBuf = gl.createBuffer();
    this.cloudGL = null; this.cloudKey = '';
    this.initModels();
  }

  // Bleed opaque colours into fully transparent texels so mipmaps & filtering don't show dark fringes.
  fillTransparent(d, size, n) {
    for (let k = 0; k < n; k++) {
      for (let pass = 0; pass < 2; pass++) for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        const i = ((k * size + y) * size + x) * 4;
        if (d[i + 3] !== 0) continue;
        let r = 0, g = 0, b = 0, c = 0;
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const xx = x + dx, yy = y + dy;
          if (xx < 0 || yy < 0 || xx >= size || yy >= size) continue;
          const j = ((k * size + yy) * size + xx) * 4;
          if (d[j + 3] || d[j] || d[j + 1] || d[j + 2]) { r += d[j]; g += d[j + 1]; b += d[j + 2]; c++; }
        }
        if (c) { d[i] = r / c; d[i + 1] = g / c; d[i + 2] = b / c; }
      }
    }
  }

  tex2D(img, repeat) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE);
    return t;
  }

  // ---- VAO helpers ------------------------------------------------------------
  setupChunkAttribs() {
    const gl = this.gl;
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, VSTRIDE, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, VSTRIDE, 12);
    gl.enableVertexAttribArray(2); gl.vertexAttribIPointer(2, 2, gl.UNSIGNED_SHORT, VSTRIDE, 20);
    gl.enableVertexAttribArray(3); gl.vertexAttribPointer(3, 4, gl.UNSIGNED_BYTE, true, VSTRIDE, 24);
    gl.enableVertexAttribArray(4); gl.vertexAttribPointer(4, 2, gl.UNSIGNED_BYTE, true, VSTRIDE, 28);
    gl.enableVertexAttribArray(5); gl.vertexAttribIPointer(5, 1, gl.UNSIGNED_BYTE, VSTRIDE, 30);
  }
  makeVao(data) {
    const gl = this.gl;
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    this.setupChunkAttribs();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
    gl.bindVertexArray(null);
    return { vao, buf };
  }
  makeDynamic() {
    return { vao: null, buf: null };
  }
  uploadDynamic(d, mb) {
    const gl = this.gl;
    if (!d.vao) {
      d.vao = gl.createVertexArray(); gl.bindVertexArray(d.vao);
      d.buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, d.buf);
      this.setupChunkAttribs();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
    } else { gl.bindVertexArray(d.vao); gl.bindBuffer(gl.ARRAY_BUFFER, d.buf); }
    gl.bufferData(gl.ARRAY_BUFFER, mb.u8.subarray(0, mb.n * VSTRIDE), gl.DYNAMIC_DRAW);
    gl.bindVertexArray(null);
  }
  meshToGL(mb) {
    // static GL object for a MeshBuffer (items etc.)
    if (mb.gl) return mb.gl;
    mb.gl = this.makeVao(mb.u8.subarray(0, mb.n * VSTRIDE));
    mb.gl.n = mb.n;
    return mb.gl;
  }

  setSection(key, cx, sy, cz, res) {
    const gl = this.gl;
    const old = this.sections.get(key);
    if (old) this.freeSection(old);
    if (!res.opaque && !res.trans) { this.sections.delete(key); return; }
    const s = { x: cx * 16, y: sy * 16, z: cz * 16, n: res.nOpaque, tn: res.nTrans };
    if (res.opaque) Object.assign(s, { o: this.makeVao(res.opaque) });
    if (res.trans) s.t = this.makeVao(res.trans);
    this.sections.set(key, s);
  }
  freeSection(s) {
    const gl = this.gl;
    if (s.o) { gl.deleteVertexArray(s.o.vao); gl.deleteBuffer(s.o.buf); }
    if (s.t) { gl.deleteVertexArray(s.t.vao); gl.deleteBuffer(s.t.buf); }
  }
  dropChunk(cx, cz) {
    for (let sy = 0; sy < SECTIONS; sy++) {
      const k = (ckey(cx, cz) * 16) + sy;
      const s = this.sections.get(k);
      if (s) { this.freeSection(s); this.sections.delete(k); }
    }
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.floor(window.innerWidth * dpr * this.resScale), h = Math.floor(window.innerHeight * dpr * this.resScale);
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
  }

  // ---- stars --------------------------------------------------------------------
  initStars() {
    const gl = this.gl, r = RNG(10842), pts = [];
    for (let i = 0; i < 1500; i++) {
      let x = r() * 2 - 1, y = r() * 2 - 1, z = r() * 2 - 1;
      const l = Math.hypot(x, y, z); if (l > 1 || l < 0.01) continue;
      x /= l; y /= l; z /= l;
      const b = 0.5 + r() * 0.5;
      pts.push(x * 100, y * 100, z * 100, b, b, b, 1);
    }
    this.starCount = pts.length / 7;
    this.starVao = gl.createVertexArray(); gl.bindVertexArray(this.starVao);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pts), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 28, 12);
    gl.bindVertexArray(null);
    // textured quad for sun/moon
    this.quadVao = gl.createVertexArray(); gl.bindVertexArray(this.quadVao);
    const qb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, qb);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, 0, -1, 0, 0, 1, 0, -1, 1, 0, 1, 0, 1, 1, 1, -1, 0, 1, 0, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 20, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 20, 12);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
    gl.bindVertexArray(null);
  }

  // ---- clouds ------------------------------------------------------------------------
  buildClouds(px, pz, drift) {
    const CELL = 12, R = 22;
    const ox = Math.floor((px + drift) / CELL), oz = Math.floor(pz / CELL);
    const key = ox + ',' + oz;
    if (key === this.cloudKey) return;
    this.cloudKey = key;
    const map = this.cloudMap;
    const at = (x, z) => map[mod(z, 256) * 256 + mod(x, 256)];
    const v = [];
    const H = 4, Y = 0;
    const push = (x, y, z, s) => v.push(x, y, z, s, s, s, 1);
    for (let z = oz - R; z <= oz + R; z++) for (let x = ox - R; x <= ox + R; x++) {
      if (!at(x, z)) continue;
      const X0 = x * CELL, X1 = X0 + CELL, Z0 = z * CELL, Z1 = Z0 + CELL;
      push(X0, Y + H, Z0, 1); push(X0, Y + H, Z1, 1); push(X1, Y + H, Z1, 1); push(X1, Y + H, Z0, 1);
      push(X0, Y, Z0, 0.7); push(X1, Y, Z0, 0.7); push(X1, Y, Z1, 0.7); push(X0, Y, Z1, 0.7);
      if (!at(x - 1, z)) { push(X0, Y, Z0, 0.85); push(X0, Y, Z1, 0.85); push(X0, Y + H, Z1, 0.85); push(X0, Y + H, Z0, 0.85); }
      if (!at(x + 1, z)) { push(X1, Y, Z1, 0.85); push(X1, Y, Z0, 0.85); push(X1, Y + H, Z0, 0.85); push(X1, Y + H, Z1, 0.85); }
      if (!at(x, z - 1)) { push(X1, Y, Z0, 0.8); push(X0, Y, Z0, 0.8); push(X0, Y + H, Z0, 0.8); push(X1, Y + H, Z0, 0.8); }
      if (!at(x, z + 1)) { push(X0, Y, Z1, 0.8); push(X1, Y, Z1, 0.8); push(X1, Y + H, Z1, 0.8); push(X0, Y + H, Z1, 0.8); }
    }
    const gl = this.gl;
    if (!this.cloudGL) {
      const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
      const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
      gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 28, 12);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
      gl.bindVertexArray(null);
      this.cloudGL = { vao, buf, n: 0 };
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, this.cloudGL.buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.DYNAMIC_DRAW);
    this.cloudGL.n = v.length / 7;
  }

  // ---- entity models -----------------------------------------------------------------
  // box: [u, v, x, y, z, w, h, d, inflate, mirror] in pixels relative to the part pivot.
  // Convention: y up, model faces -z, model's right side is +x.
  boxGeom(out, u, v, x, y, z, w, h, d, inf, tw, th, mirror) {
    const x0 = x - inf, y0 = y - inf, z0 = z - inf, x1 = x + w + inf, y1 = y + h + inf, z1 = z + d + inf;
    // [BL, BR, TR, TL], uLeft, uRight, vBottom, vTop, normal
    const F = [
      [[x1, y0, z0], [x0, y0, z0], [x0, y1, z0], [x1, y1, z0], u + d, u + d + w, v + d + h, v + d, [0, 0, -1]],
      [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], u + 2 * d + w, u + 2 * d + 2 * w, v + d + h, v + d, [0, 0, 1]],
      [[x1, y0, z1], [x1, y0, z0], [x1, y1, z0], [x1, y1, z1], u, u + d, v + d + h, v + d, [1, 0, 0]],
      [[x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0], u + d + w, u + 2 * d + w, v + d + h, v + d, [-1, 0, 0]],
      [[x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1], u + d, u + d + w, v + d, v, [0, 1, 0]],
      [[x1, y0, z1], [x0, y0, z1], [x0, y0, z0], [x1, y0, z0], u + d + w, u + d + 2 * w, v + d, v, [0, -1, 0]],
    ];
    for (const f of F) {
      let uL = f[4], uR = f[5];
      if (mirror) { const t = uL; uL = uR; uR = t; }
      const uv = [[uL, f[6]], [uR, f[6]], [uR, f[7]], [uL, f[7]]];
      for (let k = 0; k < 4; k++) {
        const c = f[k];
        out.push(c[0] / 16, c[1] / 16, c[2] / 16, uv[k][0] / tw, uv[k][1] / th, f[8][0], f[8][1], f[8][2]);
      }
    }
  }
  makeModelPart(boxes, tw, th) {
    const gl = this.gl, data = [];
    for (const b of boxes) this.boxGeom(data, b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8] || 0, tw, th, b[9]);
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 32, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 32, 12);
    gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 3, gl.FLOAT, false, 32, 20);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.quadIdx);
    gl.bindVertexArray(null);
    return { vao, n: data.length / 8 };
  }
  initModels() {
    const M = this.models = {};
    const P = (tw, th, parts) => { for (const p of parts) p.mesh = this.makeModelPart(p.boxes, tw, th); return { parts }; };
    const R = -Math.PI / 2;
    const humanoid = (aw, lu, lv, llu, llv, mirrorLeft, hat) => [
      { name: 'head', pivot: [0, 24, 0], boxes: [[0, 0, -4, 0, -4, 8, 8, 8], ...(hat ? [[32, 0, -4, 0, -4, 8, 8, 8, 0.5]] : [])] },
      { name: 'body', pivot: [0, 12, 0], boxes: [[16, 16, -4, 0, -2, 8, 12, 4]] },
      { name: 'rarm', pivot: [4 + aw / 2, 22, 0], boxes: [[40, 16, -aw / 2, -10, -aw / 2, aw, 12, aw]] },
      { name: 'larm', pivot: [-4 - aw / 2, 22, 0], boxes: [[lu, lv, -aw / 2, -10, -aw / 2, aw, 12, aw, 0, mirrorLeft]] },
      { name: 'rleg', pivot: [2, 12, 0], boxes: [[0, 16, -aw / 2, -12, -aw / 2, aw, 12, aw]] },
      { name: 'lleg', pivot: [-2, 12, 0], boxes: [[llu, llv, -aw / 2, -12, -aw / 2, aw, 12, aw, 0, mirrorLeft]] },
    ];
    M.zombie = P(64, 64, humanoid(4, 40, 16, 0, 16, true, true));
    M.steve = P(64, 64, humanoid(4, 32, 48, 16, 48, false, true));
    M.skeleton = P(64, 32, humanoid(2, 40, 16, 0, 16, true, false));
    M.creeper = P(64, 32, [
      { name: 'head', pivot: [0, 18, 0], boxes: [[0, 0, -4, 0, -4, 8, 8, 8]] },
      { name: 'body', pivot: [0, 6, 0], boxes: [[16, 16, -4, 0, -2, 8, 12, 4]] },
      { name: 'leg0', pivot: [2, 6, 4], boxes: [[0, 16, -2, -6, -2, 4, 6, 4]] },
      { name: 'leg1', pivot: [-2, 6, 4], boxes: [[0, 16, -2, -6, -2, 4, 6, 4]] },
      { name: 'leg2', pivot: [2, 6, -4], boxes: [[0, 16, -2, -6, -2, 4, 6, 4]] },
      { name: 'leg3', pivot: [-2, 6, -4], boxes: [[0, 16, -2, -6, -2, 4, 6, 4]] },
    ]);
    const legs4 = (y, zb, zf, x, h) => [
      { name: 'leg0', pivot: [x, y, zb], boxes: [[0, 16, -2, -h, -2, 4, h, 4]] },
      { name: 'leg1', pivot: [-x, y, zb], boxes: [[0, 16, -2, -h, -2, 4, h, 4]] },
      { name: 'leg2', pivot: [x, y, zf], boxes: [[0, 16, -2, -h, -2, 4, h, 4]] },
      { name: 'leg3', pivot: [-x, y, zf], boxes: [[0, 16, -2, -h, -2, 4, h, 4]] },
    ];
    M.pig = P(64, 64, [
      { name: 'head', pivot: [0, 12, -6], boxes: [[0, 0, -4, -4, -8, 8, 8, 8], [16, 16, -2, -3, -9, 4, 3, 1]] },
      { name: 'body', pivot: [0, 10, 0], boxes: [[28, 8, -5, -8, -4, 10, 16, 8]], rotX: R },
      ...legs4(6, 7, -5, 3, 6),
    ]);
    M.cow = P(64, 64, [
      { name: 'head', pivot: [0, 20, -8], boxes: [[0, 0, -4, -4, -6, 8, 8, 6], [22, 0, 4, 2, -4, 1, 3, 1], [22, 0, -5, 2, -4, 1, 3, 1]] },
      { name: 'body', pivot: [0, 17, 1], boxes: [[18, 4, -6, -9, -5, 12, 18, 10]], rotX: R },
      ...legs4(12, 7, -6, 4, 12),
    ]);
    M.sheep = P(64, 32, [
      { name: 'head', pivot: [0, 18, -8], boxes: [[0, 0, -3, -2, -6, 6, 6, 8]] },
      { name: 'body', pivot: [0, 15, 0], boxes: [[28, 8, -4, -8, -3, 8, 16, 6]], rotX: R },
      ...legs4(12, 7, -5, 3, 12),
    ]);
    M.sheep_wool = P(64, 32, [
      { name: 'head', pivot: [0, 18, -8], boxes: [[0, 0, -3, -2, -4, 6, 6, 6, 0.6]] },
      { name: 'body', pivot: [0, 15, 0], boxes: [[28, 8, -4, -8, -3, 8, 16, 6, 1.75]], rotX: R },
      { name: 'leg0', pivot: [3, 12, 7], boxes: [[0, 16, -2, -6, -2, 4, 6, 4, 0.5]] },
      { name: 'leg1', pivot: [-3, 12, 7], boxes: [[0, 16, -2, -6, -2, 4, 6, 4, 0.5]] },
      { name: 'leg2', pivot: [3, 12, -5], boxes: [[0, 16, -2, -6, -2, 4, 6, 4, 0.5]] },
      { name: 'leg3', pivot: [-3, 12, -5], boxes: [[0, 16, -2, -6, -2, 4, 6, 4, 0.5]] },
    ]);
    M.chicken = P(64, 32, [
      { name: 'head', pivot: [0, 9, -4], boxes: [[0, 0, -2, 0, -2, 4, 6, 3], [14, 0, -2, 2, -4, 4, 2, 2], [14, 4, -1, 0, -3, 2, 2, 2]] },
      { name: 'body', pivot: [0, 8, 0], boxes: [[0, 9, -3, -4, -3, 6, 8, 6]], rotX: R },
      { name: 'leg0', pivot: [1.5, 5, 1], boxes: [[26, 0, -1.5, -5, -3, 3, 5, 3]] },
      { name: 'leg1', pivot: [-1.5, 5, 1], boxes: [[26, 0, -1.5, -5, -3, 3, 5, 3]] },
      { name: 'wing0', pivot: [4, 11, 0], boxes: [[24, 13, -1, -4, -3, 1, 4, 6]] },
      { name: 'wing1', pivot: [-4, 11, 0], boxes: [[24, 13, 0, -4, -3, 1, 4, 6]] },
    ]);
    const spiderLegs = [];
    for (let i = 0; i < 8; i++) {
      const right = i % 2 === 0, row = i >> 1; // row 0 = front
      spiderLegs.push({ name: 'leg' + i, pivot: [right ? 4 : -4, 9, -1 + row], boxes: [[18, 0, right ? -1 : -15, -1, -1, 16, 2, 2]] });
    }
    M.spider = P(64, 32, [
      { name: 'head', pivot: [0, 9, -3], boxes: [[32, 4, -4, -4, -8, 8, 8, 8]] },
      { name: 'neck', pivot: [0, 9, 0], boxes: [[0, 0, -3, -3, -3, 6, 6, 6]] },
      { name: 'body', pivot: [0, 9, 9], boxes: [[0, 12, -5, -4, -6, 10, 8, 12]] },
      ...spiderLegs,
    ]);
    M.arm = P(64, 64, [{ name: 'arm', pivot: [0, 0, 0], boxes: [[40, 16, -2, -10, -2, 4, 12, 4]] }]);
  }

  // ---- frame ---------------------------------------------------------------------------
  setCamera(pos, yaw, pitch, fovDeg, extra) {
    const w = this.canvas.width, h = this.canvas.height;
    M4.perspective(this.proj, fovDeg * Math.PI / 180, w / h, 0.05, Math.max(256, this.renderDist * 16 * 1.6 + 64));
    const v = M4.identity(this.view);
    if (extra) extra(v);
    M4.rotX(v, -pitch); M4.rotY(v, -yaw);
    M4.translate(v, -pos[0], -pos[1], -pos[2]);
    M4.mul(this.vp, this.proj, v);
    this.camPos = pos;
    this.planes = frustumPlanes(this.vp);
  }

  useChunkProg(env) {
    const gl = this.gl, P = this.progChunk;
    gl.useProgram(P.p);
    gl.uniformMatrix4fv(P.u.u_vp, false, this.vp);
    gl.uniformMatrix4fv(P.u.u_model, false, this.identity);
    gl.uniform3fv(P.u.u_cam, this.camPos);
    gl.uniform1f(P.u.u_tick, env.tick);
    gl.uniform1f(P.u.u_day, env.day);
    gl.uniform3fv(P.u.u_fogColor, env.fogColor);
    gl.uniform2f(P.u.u_fog, env.fogNear, env.fogFar);
    gl.uniform1f(P.u.u_alphaTest, 0.35);
    gl.uniform4f(P.u.u_colorMul, 1, 1, 1, 1);
    gl.uniform3f(P.u.u_lightOv, 0, 0, 0);
    gl.uniform1f(P.u.u_gamma, this.gamma);
    gl.uniform3fv(P.u.u_nightTint, env.nightTint);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.texArray);
    return P;
  }

  drawSky(env) {
    const gl = this.gl, S = this.progSky;
    gl.disable(gl.DEPTH_TEST); gl.depthMask(false);
    gl.useProgram(S.p);
    gl.disable(gl.CULL_FACE);
    const inv = invert4(this.vpNoTranslate());
    gl.uniformMatrix4fv(S.u.u_invVP, false, inv);
    gl.uniform3fv(S.u.u_sky, env.skyColor);
    gl.uniform3fv(S.u.u_fogColor, env.fogColor);
    gl.uniform3fv(S.u.u_sunDir, env.sunDir);
    gl.uniform4fv(S.u.u_sunset, env.sunset);
    gl.uniform3fv(S.u.u_void, env.voidColor);
    gl.bindVertexArray(this.emptyVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    // stars
    const vpr = this.vpNoTranslate();
    if (env.starBright > 0.01) {
      const C = this.progCol; gl.useProgram(C.p);
      const m = M4.create(); M4.rotY(m, -Math.PI / 2); M4.rotX(m, env.celestial * Math.PI * 2);
      gl.uniformMatrix4fv(C.u.u_vp, false, vpr); gl.uniformMatrix4fv(C.u.u_model, false, m);
      gl.uniform4f(C.u.u_color, 1, 1, 1, env.starBright); gl.uniform3f(C.u.u_cam, 0, 0, 0); gl.uniform2f(C.u.u_fog, 1e9, 2e9);
      gl.uniform3fv(C.u.u_fogColor, env.fogColor); gl.uniform1f(C.u.u_psize, Math.max(1, this.canvas.height / 700));
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.bindVertexArray(this.starVao); gl.drawArrays(gl.POINTS, 0, this.starCount);
    }
    // sun & moon
    const T = this.progTex; gl.useProgram(T.p);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
    gl.uniformMatrix4fv(T.u.u_vp, false, vpr);
    gl.bindVertexArray(this.quadVao);
    gl.activeTexture(gl.TEXTURE0);
    const a = env.celestial * Math.PI * 2;
    for (const which of ['sun', 'moon']) {
      const m = M4.create(); M4.rotY(m, -Math.PI / 2); M4.rotX(m, a + (which === 'moon' ? Math.PI : 0));
      M4.translate(m, 0, 100, 0); M4.scale(m, which === 'sun' ? 30 : 20, 1, which === 'sun' ? 30 : 20);
      gl.uniformMatrix4fv(T.u.u_model, false, m);
      gl.uniform4f(T.u.u_color, 1, 1, 1, which === 'sun' ? env.sunAlpha : 1 - env.rain * 0.9);
      gl.bindTexture(gl.TEXTURE_2D, this.entityTex[which === 'sun' ? 'sun' : 'moon' + env.moonPhase]);
      gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_INT, 0);
    }
    gl.disable(gl.BLEND);
    gl.enable(gl.DEPTH_TEST); gl.depthMask(true); gl.enable(gl.CULL_FACE);
  }
  vpNoTranslate() {
    const v = new Float32Array(this.view); v[12] = v[13] = v[14] = 0;
    const out = M4.create(); M4.mul(out, this.proj, v); return out;
  }

  drawTerrain(env) {
    const gl = this.gl;
    const P = this.useChunkProg(env);
    gl.enable(gl.DEPTH_TEST); gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK);
    gl.disable(gl.BLEND);
    const cam = this.camPos, maxD = (this.renderDist + 1) * 16;
    const vis = [];
    for (const s of this.sections.values()) {
      const dx = s.x + 8 - cam[0], dz = s.z + 8 - cam[2];
      if (dx * dx + dz * dz > maxD * maxD) continue;
      if (!aabbInFrustum(this.planes, s.x, s.y, s.z, s.x + 16, s.y + 16, s.z + 16)) continue;
      s.d = dx * dx + dz * dz + (s.y + 8 - cam[1]) ** 2;
      vis.push(s);
    }
    vis.sort((a, b) => a.d - b.d);
    let tris = 0;
    for (const s of vis) if (s.o) { gl.bindVertexArray(s.o.vao); gl.drawElements(gl.TRIANGLES, s.n * 1.5, gl.UNSIGNED_INT, 0); tris += s.n / 2; }
    this.visSections = vis;
    this.stats = { sections: vis.length, tris };
  }

  drawTranslucent(env) {
    const gl = this.gl;
    const P = this.useChunkProg(env);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.disable(gl.CULL_FACE);
    gl.depthMask(false);
    gl.uniform1f(P.u.u_alphaTest, 0.01);
    const vis = this.visSections || [];
    for (let i = vis.length - 1; i >= 0; i--) {
      const s = vis[i];
      if (s.t) { gl.bindVertexArray(s.t.vao); gl.drawElements(gl.TRIANGLES, s.tn * 1.5, gl.UNSIGNED_INT, 0); }
    }
    gl.depthMask(true);
    gl.enable(gl.CULL_FACE);
    gl.disable(gl.BLEND);
  }

  drawClouds(env, px, pz) {
    if (!this.showClouds) return;
    const drift = env.tick * 0.03;
    this.buildClouds(px, pz, drift);
    const gl = this.gl, C = this.progCol;
    gl.useProgram(C.p);
    const m = M4.create(); M4.translate(m, -drift, 192, 0);
    gl.uniformMatrix4fv(C.u.u_vp, false, this.vp); gl.uniformMatrix4fv(C.u.u_model, false, m);
    const cc = env.cloudColor;
    gl.uniform4f(C.u.u_color, cc[0], cc[1], cc[2], 0.8);
    gl.uniform3fv(C.u.u_cam, this.camPos); gl.uniform3fv(C.u.u_fogColor, env.fogColor);
    gl.uniform2f(C.u.u_fog, this.renderDist * 16 * 1.2, this.renderDist * 16 * 2.2 + 40); gl.uniform1f(C.u.u_psize, 1);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.CULL_FACE);
    gl.bindVertexArray(this.cloudGL.vao);
    // depth pre-pass so overlapping cloud faces don't double blend
    gl.colorMask(false, false, false, false);
    gl.drawElements(gl.TRIANGLES, this.cloudGL.n * 1.5, gl.UNSIGNED_INT, 0);
    gl.colorMask(true, true, true, true);
    gl.depthFunc(gl.LEQUAL);
    gl.drawElements(gl.TRIANGLES, this.cloudGL.n * 1.5, gl.UNSIGNED_INT, 0);
    gl.depthFunc(gl.LESS);
    gl.disable(gl.BLEND);
  }

  // Render a MeshBuffer (item / block) with a model matrix using the chunk program
  drawMesh(mb, model, env, light, opts = {}) {
    const gl = this.gl;
    const g = this.meshToGL(mb);
    const P = this.useChunkProg(env);
    gl.uniformMatrix4fv(P.u.u_model, false, model);
    if (light) gl.uniform3f(P.u.u_lightOv, light[0], light[1], 1); else gl.uniform3f(P.u.u_lightOv, 0, 0, 0);
    gl.uniform4fv(P.u.u_colorMul, opts.color || [1, 1, 1, 1]);
    gl.uniform1f(P.u.u_alphaTest, 0.1);
    gl.bindVertexArray(g.vao);
    gl.drawElements(gl.TRIANGLES, g.n * 1.5, gl.UNSIGNED_INT, 0);
  }

  drawModel(name, texName, model, poses, light, env, opts = {}) {
    const gl = this.gl, E = this.progEnt, mdl = this.models[name];
    gl.useProgram(E.p);
    gl.uniformMatrix4fv(E.u.u_vp, false, opts.vp || this.vp);
    gl.uniform3fv(E.u.u_cam, this.camPos);
    gl.uniform2f(E.u.u_light, light[0], light[1]);
    gl.uniform1f(E.u.u_day, env.day);
    gl.uniform4fv(E.u.u_overlay, opts.overlay || [0, 0, 0, 0]);
    gl.uniform3fv(E.u.u_tint, opts.tint || [1, 1, 1]);
    gl.uniform3fv(E.u.u_fogColor, env.fogColor); gl.uniform2f(E.u.u_fog, env.fogNear, env.fogFar);
    gl.uniform1f(E.u.u_gamma, this.gamma); gl.uniform3fv(E.u.u_nightTint, env.nightTint);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.entityTex[texName]);
    gl.uniform1i(E.u.u_tex, 1);
    const attached = [];
    for (const part of mdl.parts) {
      const pose = poses && poses[part.name];
      if (pose && pose.hidden) continue;
      const m = new Float32Array(model);
      M4.translate(m, part.pivot[0] / 16, part.pivot[1] / 16, part.pivot[2] / 16);
      if (pose) {
        if (pose.t) M4.translate(m, pose.t[0], pose.t[1], pose.t[2]);
        if (pose.ry) M4.rotY(m, pose.ry);
        if (pose.rx) M4.rotX(m, pose.rx);
        if (pose.rz) M4.rotZ(m, pose.rz);
      }
      if (part.rotX) M4.rotX(m, part.rotX);
      gl.uniformMatrix4fv(E.u.u_model, false, m);
      gl.bindVertexArray(part.mesh.vao);
      gl.drawElements(gl.TRIANGLES, part.mesh.n * 1.5, gl.UNSIGNED_INT, 0);
      if (opts.attach && opts.attach[part.name]) attached.push([opts.attach[part.name], new Float32Array(m)]);
    }
    for (const [fn, m] of attached) fn(m);
    gl.activeTexture(gl.TEXTURE0);
  }

  drawShadow(x, y, z, r, alpha) {
    const gl = this.gl, T = this.progTex;
    gl.useProgram(T.p);
    const m = M4.create(); M4.translate(m, x, y + 0.01, z); M4.scale(m, r, 1, r);
    gl.uniformMatrix4fv(T.u.u_vp, false, this.vp); gl.uniformMatrix4fv(T.u.u_model, false, m);
    gl.uniform4f(T.u.u_color, 1, 1, 1, alpha);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(-1, -1);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.entityTex.shadow);
    gl.bindVertexArray(this.quadVao);
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_INT, 0);
    gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.depthMask(true); gl.disable(gl.BLEND);
  }

  // Draw dynamic chunk-format geometry (particles, break overlay, rain)
  drawDynamic(mb, dyn, env, opts = {}) {
    if (!mb.n) return;
    const gl = this.gl;
    this.uploadDynamic(dyn, mb);
    const P = this.useChunkProg(env);
    if (opts.colorMul) gl.uniform4fv(P.u.u_colorMul, opts.colorMul);
    gl.uniform1f(P.u.u_alphaTest, opts.alphaTest ?? 0.1);
    if (opts.blend === 'mul') { gl.enable(gl.BLEND); gl.blendFunc(gl.DST_COLOR, gl.SRC_COLOR); gl.depthMask(false); gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(-2, -2); }
    else if (opts.blend === 'alpha') { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); }
    if (opts.noCull) gl.disable(gl.CULL_FACE);
    gl.bindVertexArray(dyn.vao);
    gl.drawElements(gl.TRIANGLES, mb.n * 1.5, gl.UNSIGNED_INT, 0);
    gl.disable(gl.BLEND); gl.depthMask(true); gl.disable(gl.POLYGON_OFFSET_FILL); gl.enable(gl.CULL_FACE);
  }

  drawLines(verts, color) {
    const gl = this.gl, C = this.progCol;
    gl.useProgram(C.p);
    gl.uniformMatrix4fv(C.u.u_vp, false, this.vp); gl.uniformMatrix4fv(C.u.u_model, false, this.identity);
    gl.uniform4fv(C.u.u_color, color); gl.uniform3fv(C.u.u_cam, this.camPos); gl.uniform2f(C.u.u_fog, 1e9, 2e9); gl.uniform1f(C.u.u_psize, 1);
    gl.bindVertexArray(this.lineVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 28, 12);
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.drawArrays(gl.LINES, 0, verts.length / 7);
    gl.disable(gl.BLEND);
  }

  // ---- GUI icons: render each block item in 3D into an atlas canvas ------------------------
  buildIcons() {
    const gl = this.gl, S = 64;
    const ids = ITEMS.map((it, i) => it ? i : -1).filter(i => i >= 0);
    const cols = 32, rows = Math.ceil((Math.max(...ids) + 1) / cols);
    const atlas = document.createElement('canvas'); atlas.width = cols * S; atlas.height = rows * S;
    const actx = atlas.getContext('2d');
    actx.imageSmoothingEnabled = false;
    const fb = gl.createFramebuffer(), tex = gl.createTexture(), rb = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, S, S, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.bindRenderbuffer(gl.RENDERBUFFER, rb); gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, S, S);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb);
    gl.viewport(0, 0, S, S);
    const px = new Uint8Array(S * S * 4), img = actx.createImageData(S, S);
    const env = { tick: 0, day: 1, fogColor: [0, 0, 0], fogNear: 1e8, fogFar: 2e8, nightTint: [1, 1, 1] };
    const savedVP = this.vp; const savedCam = this.camPos;
    this.camPos = [0, 0, 0];
    const vp = M4.create(); M4.ortho(vp, -0.86, 0.86, -0.86, 0.86, -10, 10);
    M4.rotX(vp, 30 * Math.PI / 180); M4.rotY(vp, -135 * Math.PI / 180 + Math.PI);
    this.vp = vp;
    for (const id of ids) {
      const it = ITEMS[id];
      const cx = (id % cols) * S, cy = Math.floor(id / cols) * S;
      const b = BLOCKS[id];
      let flat = !it.block || it.flat || (b && (b.shape === SH_CROSS || b.shape === SH_TORCH || b.ladder || b.door));
      if (flat) {
        const layer = it.block ? (it.flat ? T(it.flat) : b.tex[0]) : it.tex;
        const tint = b && b.tint === 'grass' ? [124, 189, 107] : null;
        this.drawFlatIcon(actx, layer, cx, cy, S, tint);
        continue;
      }
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      let meta = 0;
      if (b.facing) meta = 5;
      if (b.stairs) meta = 1;
      const isBed = b.bed;
      const mb = isBed ? Mesher.multi([[id, 0, 0, 0, 0], [id, 4, 0, 0, -1]]) : Mesher.single(id, meta);
      const model = M4.create();
      if (isBed) { M4.scale(model, 0.62, 0.62, 0.62); M4.translate(model, -0.5, -0.3, 0); } else M4.translate(model, -0.5, -0.5, -0.5);
      this.useChunkProg(env);
      gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      this.drawMesh(mb, model, env, [1, 0]);
      gl.readPixels(0, 0, S, S, gl.RGBA, gl.UNSIGNED_BYTE, px);
      for (let y = 0; y < S; y++) img.data.set(px.subarray((S - 1 - y) * S * 4, (S - y) * S * 4), y * S * 4);
      actx.putImageData(img, cx, cy);
      if (mb.gl) { gl.deleteVertexArray(mb.gl.vao); gl.deleteBuffer(mb.gl.buf); }
    }
    gl.disable(gl.BLEND); gl.enable(gl.CULL_FACE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(fb); gl.deleteTexture(tex); gl.deleteRenderbuffer(rb);
    this.vp = savedVP; this.camPos = savedCam;
    this.iconAtlas = atlas; this.iconCols = cols; this.iconSize = S;
  }
  drawFlatIcon(ctx, layer, x, y, S, tint) {
    const c = document.createElement('canvas'); c.width = 16; c.height = 16;
    const cx = c.getContext('2d');
    cx.drawImage(this.stripCanvas, 0, layer * 16, 16, 16, 0, 0, 16, 16);
    if (tint) {
      const d = cx.getImageData(0, 0, 16, 16);
      for (let i = 0; i < 1024; i += 4) { d.data[i] = d.data[i] * tint[0] / 255; d.data[i + 1] = d.data[i + 1] * tint[1] / 255; d.data[i + 2] = d.data[i + 2] * tint[2] / 255; }
      cx.putImageData(d, 0, 0);
    }
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(c, 0, 0, 16, 16, x + 4, y + 4, S - 8, S - 8);
  }
}

function invert4(m) {
  const inv = new Float32Array(16);
  inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
  inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
  inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
  inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
  inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
  inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
  inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
  inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
  inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
  inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
  inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
  inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
  inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
  inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
  inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
  inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];
  let det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
  det = 1 / det;
  for (let i = 0; i < 16; i++) inv[i] *= det;
  return inv;
}
