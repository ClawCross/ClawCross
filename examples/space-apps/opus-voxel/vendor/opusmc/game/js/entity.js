'use strict';
// ---------------------------------------------------------------------------
// Entities, physics, mobs, particles, explosions
// ---------------------------------------------------------------------------
function blockBoxes(world, x0, y0, z0, x1, y1, z1, out) {
  out.length = 0;
  const ix0 = Math.floor(x0), iy0 = Math.floor(y0) - 1, iz0 = Math.floor(z0);
  const ix1 = Math.floor(x1), iy1 = Math.floor(y1), iz1 = Math.floor(z1);
  for (let x = ix0; x <= ix1; x++) for (let z = iz0; z <= iz1; z++) {
    if (!world.isLoaded(x, z)) { out.push([x, -1e3, z, x + 1, 1e3, z + 1]); continue; }
    for (let y = iy0; y <= iy1; y++) {
      const id = world.getBlock(x, y, z);
      if (!id) continue;
      const b = BLOCKS[id];
      if (!b.solid) continue;
      if (b.shape === SH_BOXES || b.shape === SH_MODEL) {
        if (b.door && (world.getMeta(x, y, z) & 4) && false) continue;
        for (const bx of b.boxes(world.getMeta(x, y, z))) out.push([x + bx[0] / 16, y + bx[1] / 16, z + bx[2] / 16, x + bx[3] / 16, y + bx[4] / 16 * (b.key === 'snow' ? 0 : 1), z + bx[5] / 16]);
      } else if (b.shape === SH_CACTUS) out.push([x + 1 / 16, y, z + 1 / 16, x + 15 / 16, y + 1, z + 15 / 16]);
      else out.push([x, y, z, x + 1, y + 1, z + 1]);
    }
  }
  return out;
}
const _boxes = [];

class Entity {
  constructor(game, x, y, z, w, h) {
    this.game = game; this.world = game.world;
    this.pos = [x, y, z]; this.prev = [x, y, z]; this.vel = [0, 0, 0];
    this.w = w; this.h = h;
    this.yaw = 0; this.pitch = 0; this.prevYaw = 0;
    this.onGround = false; this.collidedH = false; this.inWater = false; this.inLava = false; this.eyeInWater = false;
    this.dead = false; this.age = 0; this.fallDist = 0; this.stepHeight = 0;
    this.id = Entity.nextId++;
  }
  box() { const w = this.w / 2; return [this.pos[0] - w, this.pos[1], this.pos[2] - w, this.pos[0] + w, this.pos[1] + this.h, this.pos[2] + w]; }
  savePrev() { this.prev[0] = this.pos[0]; this.prev[1] = this.pos[1]; this.prev[2] = this.pos[2]; this.prevYaw = this.yaw; this.prevBodyYaw = this.bodyYaw; }
  ipos(a) { return [lerp(this.prev[0], this.pos[0], a), lerp(this.prev[1], this.pos[1], a), lerp(this.prev[2], this.pos[2], a)]; }

  collides(bb) {
    blockBoxes(this.world, bb[0], bb[1], bb[2], bb[3], bb[4], bb[5], _boxes);
    for (const b of _boxes) if (bb[0] < b[3] && bb[3] > b[0] && bb[1] < b[4] && bb[4] > b[1] && bb[2] < b[5] && bb[5] > b[2]) return true;
    return false;
  }

  static clip(bb, boxes, d, axis) {
    const a = axis, o1 = (axis + 1) % 3, o2 = (axis + 2) % 3;
    for (const b of boxes) {
      if (bb[o1] >= b[o1 + 3] || bb[o1 + 3] <= b[o1] || bb[o2] >= b[o2 + 3] || bb[o2 + 3] <= b[o2]) continue;
      if (d > 0 && bb[a + 3] <= b[a] + 1e-7) { const m = b[a] - bb[a + 3]; if (m < d) d = m; }
      else if (d < 0 && bb[a] >= b[a + 3] - 1e-7) { const m = b[a + 3] - bb[a]; if (m > d) d = m; }
    }
    return d;
  }

  move(dx, dy, dz) {
    if (this.noClip) { this.pos[0] += dx; this.pos[1] += dy; this.pos[2] += dz; return; }
    // sneaking: don't walk off edges
    if (this.sneakEdge && this.onGround && !this.flying) {
      const step = 0.05;
      const test = (ddx, ddz) => { const b = this.box(); return this.collides([b[0] + ddx, b[1] - 0.6, b[2] + ddz, b[3] + ddx, b[1], b[5] + ddz]); };
      while (dx !== 0 && !test(dx, 0)) { if (Math.abs(dx) < step) dx = 0; else dx -= Math.sign(dx) * step; }
      while (dz !== 0 && !test(0, dz)) { if (Math.abs(dz) < step) dz = 0; else dz -= Math.sign(dz) * step; }
      while (dx !== 0 && dz !== 0 && !test(dx, dz)) {
        if (Math.abs(dx) < step) dx = 0; else dx -= Math.sign(dx) * step;
        if (Math.abs(dz) < step) dz = 0; else dz -= Math.sign(dz) * step;
      }
    }
    const ox = dx, oy = dy, oz = dz;
    const bb = this.box();
    blockBoxes(this.world, Math.min(bb[0], bb[0] + dx) - 0.01, Math.min(bb[1], bb[1] + dy) - 0.01, Math.min(bb[2], bb[2] + dz) - 0.01,
      Math.max(bb[3], bb[3] + dx) + 0.01, Math.max(bb[4], bb[4] + dy) + 0.01, Math.max(bb[5], bb[5] + dz) + 0.01, _boxes);
    const boxes = _boxes.slice();
    dy = Entity.clip(bb, boxes, dy, 1); bb[1] += dy; bb[4] += dy;
    dx = Entity.clip(bb, boxes, dx, 0); bb[0] += dx; bb[3] += dx;
    dz = Entity.clip(bb, boxes, dz, 2); bb[2] += dz; bb[5] += dz;
    const grounded = this.onGround || (oy !== dy && oy < 0);
    if (this.stepHeight > 0 && grounded && (dx !== ox || dz !== oz)) {
      const sb = this.box();
      blockBoxes(this.world, Math.min(sb[0], sb[0] + ox) - 0.01, sb[1] - 0.01, Math.min(sb[2], sb[2] + oz) - 0.01,
        Math.max(sb[3], sb[3] + ox) + 0.01, sb[4] + this.stepHeight + 0.01, Math.max(sb[5], sb[5] + oz) + 0.01, _boxes);
      const b2 = _boxes.slice();
      let sy = Entity.clip(sb, b2, this.stepHeight, 1); sb[1] += sy; sb[4] += sy;
      let sx = Entity.clip(sb, b2, ox, 0); sb[0] += sx; sb[3] += sx;
      let sz = Entity.clip(sb, b2, oz, 2); sb[2] += sz; sb[5] += sz;
      let down = Entity.clip(sb, b2, -sy, 1); sb[1] += down; sb[4] += down;
      if (sx * sx + sz * sz > dx * dx + dz * dz + 1e-6) {
        for (let i = 0; i < 6; i++) bb[i] = sb[i];
        dx = sx; dz = sz; dy = sy + down;
        this.stepped = true;
      }
    }
    this.collidedH = Math.abs(dx - ox) > 1e-6 || Math.abs(dz - oz) > 1e-6;
    this.onGround = oy < 0 && Math.abs(dy - oy) > 1e-7 || (this.stepped && oy <= 0 && this.onGround);
    this.stepped = false;
    if (Math.abs(dy - oy) > 1e-7) this.vel[1] = 0;
    if (Math.abs(dx - ox) > 1e-7) this.vel[0] = 0;
    if (Math.abs(dz - oz) > 1e-7) this.vel[2] = 0;
    this.pos[0] = (bb[0] + bb[3]) / 2; this.pos[1] = bb[1]; this.pos[2] = (bb[2] + bb[5]) / 2;
    // fall distance
    if (this.onGround) {
      if (this.fallDist > 0) this.onLand(this.fallDist);
      this.fallDist = 0;
    } else if (dy < 0) this.fallDist -= dy;
  }
  onLand(d) { }

  updateFluids() {
    const b = this.box();
    this.inWater = false; this.inLava = false;
    const x0 = Math.floor(b[0] + 0.001), x1 = Math.floor(b[3] - 0.001), y0 = Math.floor(b[1] + 0.001), y1 = Math.floor(b[4] - 0.001), z0 = Math.floor(b[2] + 0.001), z1 = Math.floor(b[5] - 0.001);
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) {
      const id = this.world.getBlock(x, y, z);
      if (id === WATER) { this.inWater = true; this.fallDist = 0; }
      else if (id === LAVA) this.inLava = true;
    }
    const ey = this.pos[1] + (this.eyeH || this.h * 0.85);
    const eid = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(ey), Math.floor(this.pos[2]));
    this.eyeInWater = eid === WATER && ey < Math.floor(ey) + 0.9;
  }

  lightAt() {
    const l = this.world.getLight(Math.floor(this.pos[0]), Math.floor(this.pos[1] + this.h * 0.5), Math.floor(this.pos[2]));
    return [(l >> 4) / 15, (l & 15) / 15];
  }
  distTo(e) { return Math.hypot(this.pos[0] - e.pos[0], this.pos[1] - e.pos[1], this.pos[2] - e.pos[2]); }
}
Entity.nextId = 1;

// ---------------------------------------------------------------------------
// Living entities
// ---------------------------------------------------------------------------
class Living extends Entity {
  constructor(game, x, y, z, w, h, hp) {
    super(game, x, y, z, w, h);
    this.health = hp; this.maxHealth = hp;
    this.hurtTime = 0; this.invuln = 0; this.deathTime = 0;
    this.fire = 0; this.air = 300;
    this.limbSwing = 0; this.limbAmt = 0; this.bodyYaw = 0; this.headYaw = 0;
    this.stepHeight = 0.6; this.slowFall = false;
  }
  hurt(amount, source) {
    if (this.dead || this.deathTime > 0) return false;
    if (this.invuln > 10 && amount <= this.lastDamage) return false;
    const actual = this.invuln > 10 ? amount - this.lastDamage : amount;
    this.lastDamage = amount; this.invuln = 20; this.hurtTime = 10;
    this.health -= this.reduceDamage ? this.reduceDamage(actual, source) : actual;
    if (source && source.knockFrom) {
      const dx = this.pos[0] - source.knockFrom[0], dz = this.pos[2] - source.knockFrom[2];
      const d = Math.hypot(dx, dz) || 1, k = source.knock ?? 0.4;
      this.vel[0] = this.vel[0] / 2 + dx / d * k; this.vel[2] = this.vel[2] / 2 + dz / d * k;
      if (this.onGround) this.vel[1] = Math.min(0.4, this.vel[1] / 2 + 0.36);
    }
    this.onHurt(source);
    if (this.health <= 0) { this.health = 0; this.die(source); }
    return true;
  }
  onHurt() { }
  die() { this.deathTime = 1; }
  baseTick() {
    this.age++;
    if (this.hurtTime > 0) this.hurtTime--;
    if (this.invuln > 0) this.invuln--;
    this.updateFluids();
    if (this.inLava) { this.hurt(4, { type: 'lava' }); this.fire = 300; }
    if (this.fire > 0) {
      this.fire--;
      if (this.inWater) this.fire = 0;
      else if (this.fire % 20 === 0) this.hurt(1, { type: 'fire' });
      if (Math.random() < 0.3) this.game.particles.spawn('flame', this.pos[0] + (Math.random() - 0.5) * this.w, this.pos[1] + Math.random() * this.h, this.pos[2] + (Math.random() - 0.5) * this.w, 0, 0.02, 0);
    }
    if (this.eyeInWater && !this.waterBreather) {
      this.air--;
      if (this.air <= -20) { this.air = 0; this.hurt(2, { type: 'drown' }); }
    } else this.air = Math.min(300, this.air + 4);
    // contact damage (cactus, magma)
    const b = this.box();
    for (const [x, y, z] of [[b[0] - 0.01, b[1] + 0.3, this.pos[2]], [b[3] + 0.01, b[1] + 0.3, this.pos[2]], [this.pos[0], b[1] + 0.3, b[2] - 0.01], [this.pos[0], b[1] + 0.3, b[5] + 0.01]]) {
      if (this.world.getBlock(Math.floor(x), Math.floor(y), Math.floor(z)) === 37) { this.hurt(1, { type: 'cactus' }); break; }
    }
    if (this.onGround && this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.1), Math.floor(this.pos[2])) === 103 && !this.sneaking) this.hurt(1, { type: 'fire' });
    if (this.pos[1] < -40) this.hurt(4, { type: 'void' });
  }
  onLand(d) {
    if (this.slowFall) return;
    const id = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.2), Math.floor(this.pos[2]));
    if (d > 1.5 && id && BLOCKS[id].shape !== SH_LIQUID) {
      const n = Math.min(30, Math.floor(d * 3));
      for (let i = 0; i < n; i++) this.game.particles.spawn('block', this.pos[0] + (Math.random() - 0.5) * this.w, this.pos[1] + 0.1, this.pos[2] + (Math.random() - 0.5) * this.w, (Math.random() - 0.5) * 0.3, 0.1 + Math.random() * 0.1, (Math.random() - 0.5) * 0.3, { id, meta: 0, tint: BLOCKS[id].tint ? this.game.tintAt(Math.floor(this.pos[0]), Math.floor(this.pos[2]), BLOCKS[id].tint) : null });
      if (d > 3) Sound.block('step', BLOCKS[id].sound, this.pos);
    }
    if (d > 3.0 && !this.inWater && id !== 87) {
      this.hurt(Math.ceil(d - 3), { type: 'fall' });
      if (BLOCKS[id]) Sound.block('break', BLOCKS[id].sound, this.pos);
    }
  }
  // MC-like movement physics; fwd/strafe in [-1,1] relative to yaw
  travel(fwd, strafe, speed, jump) {
    const v = this.vel;
    if (this.inWater || this.inLava) {
      const drag = this.inWater ? 0.8 : 0.5;
      this.accel(fwd, strafe, 0.02 * (this.waterSpeed || 1));
      if (jump) v[1] += 0.04;
      this.move(v[0], v[1], v[2]);
      v[0] *= drag; v[1] *= drag; v[2] *= drag;
      v[1] -= 0.02;
      if (this.collidedH && jump) {
        const b = this.box();
        if (!this.collides([b[0] + v[0], b[1] + 0.6, b[2] + v[2], b[3] + v[0], b[4] + 0.6, b[5] + v[2]])) v[1] = 0.3;
      }
      return;
    }
    const below = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.5), Math.floor(this.pos[2]));
    const slip = this.onGround ? (BLOCKS[below] ? BLOCKS[below].slip : 0.6) : 1;
    const fr = this.onGround ? slip * 0.91 : 0.91;
    const a = this.onGround ? speed * (0.21600002 / (slip * slip * slip)) : (this.airSpeed || 0.02);
    this.accel(fwd, strafe, a);
    if (this.onLadder()) {
      v[0] = clamp(v[0], -0.15, 0.15); v[2] = clamp(v[2], -0.15, 0.15);
      v[1] = Math.max(v[1], -0.15);
      if (this.sneaking && v[1] < 0) v[1] = 0;
      this.fallDist = 0;
    }
    this.move(v[0], v[1], v[2]);
    if (this.onLadder() && (this.collidedH || jump)) v[1] = 0.2;
    v[1] -= 0.08; v[1] *= 0.98;
    if (this.slowFall && v[1] < -0.1) { v[1] = -0.1; this.fallDist = 0; }
    v[0] *= fr; v[2] *= fr;
  }
  accel(fwd, strafe, a) {
    let l = fwd * fwd + strafe * strafe;
    if (l < 1e-4) return;
    l = Math.sqrt(l); if (l < 1) l = 1;
    fwd *= a / l; strafe *= a / l;
    const s = Math.sin(this.yaw), c = Math.cos(this.yaw);
    this.vel[0] += -s * fwd + c * strafe;
    this.vel[2] += -c * fwd - s * strafe;
  }
  onLadder() {
    const id = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1]), Math.floor(this.pos[2]));
    return id && BLOCKS[id].climbable;
  }
  animate() {
    const dx = this.pos[0] - this.prev[0], dz = this.pos[2] - this.prev[2];
    const sp = Math.min(1, Math.hypot(dx, dz) * 4);
    this.limbAmt += (sp - this.limbAmt) * 0.4;
    this.limbSwing += this.limbAmt;
  }
}

// ---------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------
class Inventory {
  constructor() { this.slots = new Array(40).fill(null); this.sel = 0; }
  get held() { return this.slots[this.sel]; }
  add(stack) {
    if (!stack) return 0;
    let n = stack.count;
    const ms = maxStack(stack.id);
    const order = [];
    for (let i = 0; i < 36; i++) order.push(i);
    for (const i of order) {
      const s = this.slots[i];
      if (s && s.id === stack.id && s.count < ms && !ITEMS[s.id].durability) { const k = Math.min(n, ms - s.count); s.count += k; n -= k; if (!n) return 0; }
    }
    for (const i of order) {
      if (!this.slots[i]) { const k = Math.min(n, ms); this.slots[i] = { id: stack.id, count: k, dmg: stack.dmg || 0 }; n -= k; if (!n) return 0; }
    }
    return n;
  }
  count(id) { let n = 0; for (let i = 0; i < 36; i++) if (this.slots[i] && this.slots[i].id === id) n += this.slots[i].count; return n; }
  remove(id, n) {
    for (let i = 0; i < 36 && n > 0; i++) {
      const s = this.slots[i];
      if (s && s.id === id) { const k = Math.min(n, s.count); s.count -= k; n -= k; if (!s.count) this.slots[i] = null; }
    }
  }
  armorPoints() {
    let p = 0;
    for (let i = 36; i < 40; i++) { const s = this.slots[i]; if (s && ITEMS[s.id].armor) p += ITEMS[s.id].armor.points; }
    return p;
  }
  toJSON() { return { slots: this.slots, sel: this.sel }; }
}

// ---------------------------------------------------------------------------
// Player
// ---------------------------------------------------------------------------
class Player extends Living {
  constructor(game, x, y, z) {
    super(game, x, y, z, 0.6, 1.8, 20);
    this.eyeH = 1.62;
    this.inv = new Inventory();
    this.food = 20; this.saturation = 5; this.exhaustion = 0; this.foodTimer = 0;
    this.flying = false; this.sprinting = false; this.sneaking = false;
    this.spawn = [x, y, z];
    this.bob = 0; this.prevBob = 0; this.bobAmt = 0;
    this.swing = 0; this.swingT = 0; this.useT = 0;
    this.xp = 0;
    this.distWalked = 0;
  }
  get creative() { return this.game.mode === 'creative'; }
  eye() { return this.sneaking && !this.flying ? 1.27 : 1.62; }
  reduceDamage(a, src) {
    if (this.creative && src && src.type !== 'void') return 0;
    if (src && ['fall', 'drown', 'starve', 'void', 'fire'].includes(src.type)) return a;
    const armor = this.inv.armorPoints();
    if (armor) {
      for (let i = 36; i < 40; i++) { const s = this.inv.slots[i]; if (s) { s.dmg = (s.dmg || 0) + 1; if (s.dmg >= ITEMS[s.id].durability) { this.inv.slots[i] = null; Sound.block('break', 'metal', this.pos); } } }
    }
    return a * (1 - Math.min(20, armor) / 25);
  }
  hurt(a, src) {
    if (this.creative && (!src || src.type !== 'void')) return false;
    if (this.game.gameOver) return false;
    const r = super.hurt(a, src);
    if (r) { this.exhaust(0.1); Sound.hurt(null); this.game.onPlayerHurt(src); }
    return r;
  }
  die(src) {
    super.die(src);
    this.game.onPlayerDeath(src);
  }
  exhaust(x) { if (!this.creative) this.exhaustion += x; }
  tick(input) {
    this.savePrev();
    this.baseTick();
    if (this.deathTime > 0) { this.deathTime++; return; }
    const cr = this.creative;
    if (!cr && this.flying) this.flying = false;
    this.sneaking = input.sneak && !this.flying;
    this.sneakEdge = this.sneaking;
    this.h = this.sneaking ? 1.5 : 1.8;
    if (this.h === 1.8 && this.collides(this.box())) { this.h = 1.5; this.sneaking = true; }
    // sprinting
    const canSprint = cr || this.food > 6;
    if (input.sprint && input.fwd > 0 && canSprint && !this.sneaking) this.sprinting = true;
    if (input.fwd <= 0 || this.collidedH || !canSprint || this.sneaking) this.sprinting = false;
    const sp = this.sprinting ? 0.13 : 0.1;
    let fwd = input.fwd * 0.98, str = input.strafe * 0.98;
    if (this.sneaking) { fwd *= 0.3; str *= 0.3; }
    if (this.useT > 0 && this.usingItem) { fwd *= 0.2; str *= 0.2; }
    this.airSpeed = this.sprinting ? 0.026 : 0.02;
    this.slowFall = false;
    if (this.flying) {
      const v = this.vel;
      this.accel(fwd, str, this.sprinting ? 0.1 : 0.05);
      let ty = 0; if (input.jump) ty += 0.375; if (input.sneak) ty -= 0.375;
      v[1] += (ty - v[1]) * 0.4;
      this.move(v[0], v[1], v[2]);
      v[0] *= 0.91; v[2] *= 0.91; v[1] *= 0.6;
      if (this.onGround && input.sneak) this.flying = false;
      this.fallDist = 0;
    } else {
      if (input.jump && this.onGround && !this.inWater && !this.inLava) {
        this.vel[1] = 0.42;
        if (this.sprinting) { this.vel[0] += -Math.sin(this.yaw) * 0.2; this.vel[2] += -Math.cos(this.yaw) * 0.2; this.exhaust(0.2); }
        else this.exhaust(0.05);
        this.jumpT = 0;
      }
      const wasGround = this.onGround;
      this.travel(fwd, str, sp, input.jump);
    }
    // walking distance & footsteps
    const dx = this.pos[0] - this.prev[0], dz = this.pos[2] - this.prev[2];
    if (this.sprinting && this.onGround && !this.inWater) {
      const bid = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.2), Math.floor(this.pos[2]));
      if (bid && BLOCKS[bid].shape !== SH_LIQUID) this.game.particles.spawn('block', this.pos[0] + (Math.random() - 0.5) * 0.6, this.pos[1] + 0.1, this.pos[2] + (Math.random() - 0.5) * 0.6, -dx * 2, 0.15, -dz * 2, { id: bid, meta: this.world.getMeta(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.2), Math.floor(this.pos[2])), tint: BLOCKS[bid].tint ? this.game.tintAt(Math.floor(this.pos[0]), Math.floor(this.pos[2]), BLOCKS[bid].tint) : null });
    }
    const dist = Math.hypot(dx, dz);
    if (this.onGround && !this.flying) {
      this.distWalked += dist;
      if (this.sprinting) this.exhaust(0.1 * dist);
      if (this.distWalked > 1.7 && !this.sneaking) {
        this.distWalked = 0;
        const id = this.world.getBlock(Math.floor(this.pos[0]), Math.floor(this.pos[1] - 0.2), Math.floor(this.pos[2]));
        if (id && BLOCKS[id]) Sound.block('step', BLOCKS[id].sound, [this.pos[0], this.pos[1], this.pos[2]]);
      }
    } else if (this.inWater && dist > 0.05 && this.age % 12 === 0) Sound.splash(null);
    // view bobbing
    this.prevBob = this.bob; this.prevBobAmt = this.bobAmt;
    const target = this.onGround && !this.flying ? Math.min(0.1, dist) : 0;
    this.bobAmt += (target - this.bobAmt) * 0.4;
    this.bob += dist;
    // hunger
    if (!cr) {
      if (this.exhaustion >= 4) { this.exhaustion -= 4; if (this.saturation > 0) this.saturation = Math.max(0, this.saturation - 1); else this.food = Math.max(0, this.food - 1); }
      this.foodTimer++;
      if (this.food >= 18 && this.health < this.maxHealth) {
        if (this.foodTimer >= (this.saturation > 0 && this.food === 20 ? 10 : 80)) { this.health = Math.min(this.maxHealth, this.health + 1); this.exhaust(6); this.foodTimer = 0; }
      } else if (this.food <= 0) {
        if (this.foodTimer >= 80) { if (this.health > 1) this.hurt(1, { type: 'starve' }); this.foodTimer = 0; }
      } else this.foodTimer = 0;
    } else { this.food = 20; this.health = this.maxHealth; this.air = 300; this.fire = 0; }
    if (this.swingT > 0) this.swingT--;
    this.animate();
  }
}

// ---------------------------------------------------------------------------
// Mobs
// ---------------------------------------------------------------------------
const MOB_TYPES = {
  pig: { cn: '猪', hp: 10, w: 0.9, h: 0.9, speed: 0.25, model: 'pig', passive: true, food: 'carrot', drops: [['porkchop', 1, 3, 'cooked_porkchop']], scale: 1 },
  cow: { cn: '牛', hp: 10, w: 0.9, h: 1.4, speed: 0.2, model: 'cow', passive: true, food: 'wheat', drops: [['beef', 1, 3, 'cooked_beef'], ['leather', 0, 2]], scale: 1 },
  sheep: { cn: '羊', hp: 8, w: 0.9, h: 1.3, speed: 0.23, model: 'sheep', passive: true, food: 'wheat', drops: [['mutton', 1, 2, 'cooked_mutton']], scale: 1 },
  chicken: { cn: '鸡', hp: 4, w: 0.4, h: 0.7, speed: 0.25, model: 'chicken', passive: true, food: 'wheat_seeds', drops: [['chicken', 1, 1, 'cooked_chicken'], ['feather', 0, 2]], scale: 1 },
  zombie: { cn: '僵尸', hp: 20, w: 0.6, h: 1.95, speed: 0.23, model: 'zombie', hostile: true, attack: 3, burns: true, drops: [['rotten_flesh', 0, 2]], scale: 0.9375 },
  skeleton: { cn: '骷髅', hp: 20, w: 0.6, h: 1.99, speed: 0.25, model: 'skeleton', hostile: true, burns: true, ranged: true, drops: [['bone', 0, 2], ['arrow', 0, 2]], scale: 0.9375 },
  creeper: { cn: '苦力怕', hp: 20, w: 0.6, h: 1.7, speed: 0.25, model: 'creeper', hostile: true, drops: [['gunpowder', 0, 2]], scale: 1 },
  spider: { cn: '蜘蛛', hp: 16, w: 1.4, h: 0.9, speed: 0.3, model: 'spider', hostile: true, attack: 2, climbs: true, drops: [['string', 0, 2]], scale: 1 },
};

class Mob extends Living {
  constructor(game, type, x, y, z) {
    const T = MOB_TYPES[type];
    super(game, x, y, z, T.w, T.h, T.hp);
    this.type = type; this.T = T;
    this.yaw = Math.random() * Math.PI * 2; this.bodyYaw = this.yaw;
    this.target = null; this.wander = null; this.wanderT = 0; this.panic = 0; this.attackCd = 0;
    this.fuse = -1; this.prevFuse = -1; this.sheared = false; this.woolColor = 0;
    this.persistent = !!T.passive;
    this.idleSound = 100 + Math.random() * 300;
    this.stuck = 0; this.eggT = 6000 + Math.random() * 6000;
    this.waterSpeed = 1.5;
    if (type === 'chicken') this.slowFall = true;
    if (type === 'sheep') { const r = Math.random(); this.woolColor = r < 0.05 ? 15 : r < 0.1 ? 7 : r < 0.15 ? 8 : r < 0.18 ? 12 : r < 0.182 ? 6 : 0; }
  }
  hurt(a, src) {
    const r = super.hurt(a, src);
    if (r) {
      Sound.mob(this.type, 'hurt', this.pos);
      if (this.T.passive) this.panic = 100;
      if (src && src.attacker && !this.T.passive) this.target = src.attacker;
      if (this.type === 'spider' && src && src.attacker) this.target = src.attacker;
    }
    return r;
  }
  die(src) {
    super.die(src);
    if (src && (src.attacker === this.game.player || src.player)) this.game.stats.kills++;
    const r = Math.random;
    if (!this.baby) for (const [k, mn, mx, cooked] of this.T.drops) {
      const n = mn + Math.floor(r() * (mx - mn + 1));
      if (n > 0) this.game.dropItem(this.pos[0], this.pos[1] + 0.5, this.pos[2], makeStack(this.fire > 0 && cooked ? cooked : k, n));
    }
    if (this.type === 'sheep' && !this.sheared) this.game.dropItem(this.pos[0], this.pos[1] + 0.5, this.pos[2], makeStack(128 + this.woolColor, 1));
    if (this.type === 'creeper' && src && src.type === 'arrow' && Math.random() < 0.2) this.game.dropItem(this.pos[0], this.pos[1] + 0.5, this.pos[2], makeStack('tnt', 1));
    this.game.onMobKilled(this, src);
  }
  tick() {
    this.savePrev();
    this.baseTick();
    if (this.deathTime > 0) {
      this.deathTime++;
      this.travel(0, 0, 0, false);
      if (this.deathTime > 20) {
        this.dead = true;
        for (let i = 0; i < 12; i++) this.game.particles.spawn('poof', this.pos[0] + (Math.random() - 0.5) * this.w, this.pos[1] + Math.random() * this.h, this.pos[2] + (Math.random() - 0.5) * this.w, (Math.random() - 0.5) * 0.1, Math.random() * 0.1, (Math.random() - 0.5) * 0.1);
      }
      return;
    }
    const g = this.game, p = g.player, T = this.T;
    let fwd = 0, jump = false, mod = 1;
    let lookAt = null;
    const dP = this.distTo(p);
    const day = g.isDay();
    // despawn hostiles far away
    if (!this.persistent && (dP > 100 || (dP > 40 && this.age > 600 && Math.random() < 0.001))) { this.dead = true; return; }
    // burn in daylight
    if (T.burns && day && !this.inWater && this.age % 20 === 0) {
      const sky = this.world.skyAt(Math.floor(this.pos[0]), Math.floor(this.pos[1] + this.h), Math.floor(this.pos[2]));
      if (sky >= 15 && g.weather < 0.5) this.fire = Math.max(this.fire, 160);
    }
    if (--this.idleSound < 0) { this.idleSound = 200 + Math.random() * 400; if (dP < 20) Sound.mob(this.type, 'idle', this.pos); }
    if (this.attackCd > 0) this.attackCd--;
    const playerOk = p.deathTime === 0 && !p.creative;
    if (T.hostile) {
      if (this.type === 'spider' && day && !this.target) { }
      else if (playerOk && dP < 16 && (!this.target || this.target === p)) this.target = p;
      if (this.target && (this.target.dead || this.target.deathTime > 0 || (this.target === p && (!playerOk || dP > 24)))) this.target = null;
      if (this.type === 'spider' && day && this.target && !this.provoked && this.age % 100 === 0) this.target = null;
    }
    if (this.target) {
      const t = this.target;
      const dx = t.pos[0] - this.pos[0], dz = t.pos[2] - this.pos[2], d = Math.hypot(dx, dz);
      const want = Math.atan2(-dx, -dz);
      lookAt = t;
      if (T.ranged) {
        this.turnTo(want, 0.3);
        if (d > 10) fwd = 1; else if (d < 5) fwd = -0.6;
        if (this.attackCd <= 0 && d < 16 && this.canSee(t)) { this.shoot(t); this.attackCd = 40 + Math.floor(Math.random() * 20); }
      } else if (this.type === 'creeper') {
        this.turnTo(want, 0.4);
        if (dP < 3 && this.canSee(t)) { if (this.fuse < 0) { this.fuse = 0; Sound.fuse(this.pos); } }
        else if (dP > 7 && this.fuse >= 0) this.fuse = Math.max(-1, this.fuse - 2);
        if (this.fuse < 0) fwd = 1;
      } else {
        this.turnTo(want, 0.4);
        fwd = 1;
        if (this.type === 'spider' && this.onGround && d < 4 && d > 1.5 && Math.random() < 0.1) { this.vel[1] = 0.4; this.vel[0] += dx / d * 0.3; this.vel[2] += dz / d * 0.3; }
        const reach = this.w / 2 + t.w / 2 + 0.6;
        if (d < reach && Math.abs(t.pos[1] - this.pos[1]) < 1.8 && this.attackCd <= 0) {
          this.attackCd = 20; this.swingT = 6;
          t.hurt(T.attack, { type: 'mob', attacker: this, knockFrom: this.pos });
        }
      }
    } else if (this.panic > 0) {
      this.panic--;
      mod = 1.6;
      if (!this.wander || this.wanderT <= 0 || this.age % 20 === 0) this.pickWander(6);
    } else {
      // follow player holding food
      const held = p.inv.held;
      if (T.passive && held && T.food && held.id === item(T.food) && dP < 8 && dP > 2) {
        lookAt = p;
        this.turnTo(Math.atan2(-(p.pos[0] - this.pos[0]), -(p.pos[2] - this.pos[2])), 0.3);
        fwd = 1;
      } else if (dP < 8 && Math.random() < 0.02) lookAt = p;
    }
    if (this.creeperTick()) return;
    if (!this.target && fwd === 0) {
      if (this.wander) {
        this.wanderT--;
        const dx = this.wander[0] - this.pos[0], dz = this.wander[2] - this.pos[2];
        if (Math.hypot(dx, dz) < 0.8 || this.wanderT <= 0) this.wander = null;
        else { this.turnTo(Math.atan2(-dx, -dz), 0.25); fwd = 1; }
      } else if (Math.random() < (this.panic > 0 ? 1 : 0.008)) this.pickWander(10);
    }
    // obstacle avoidance: don't walk off cliffs or into lava
    if (fwd > 0) {
      const ax = this.pos[0] - Math.sin(this.yaw) * (this.w / 2 + 0.5), az = this.pos[2] - Math.cos(this.yaw) * (this.w / 2 + 0.5);
      const fx = Math.floor(ax), fz = Math.floor(az), fy = Math.floor(this.pos[1]);
      let drop = 0;
      while (drop < 5 && !BLOCKS[this.world.getBlock(fx, fy - 1 - drop, fz)].solid && !IS_FLUID[this.world.getBlock(fx, fy - 1 - drop, fz)]) drop++;
      const danger = this.world.getBlock(fx, fy - 1 - drop, fz) === LAVA || this.world.getBlock(fx, fy, fz) === LAVA;
      if (danger || (drop > 3 && !this.target)) { fwd = 0; this.wander = null; }
      if (this.collidedH && this.onGround) jump = true;
      if (this.collidedH && T.climbs) { this.vel[1] = 0.2; this.fallDist = 0; }
    }
    if (this.inWater && (this.vel[1] < 0.02 || this.collidedH)) jump = true;
    // stuck detection
    if (fwd > 0 && Math.hypot(this.pos[0] - this.prev[0], this.pos[2] - this.prev[2]) < 0.01) { if (++this.stuck > 30) { this.yaw += (Math.random() - 0.5) * 2; this.stuck = 0; this.wander = null; } }
    else this.stuck = 0;
    if (jump && this.onGround) this.vel[1] = 0.42;
    const speed = T.speed * T.speed * mod * (this.fuse >= 0 ? 0.4 : 1);
    this.travel(fwd, 0, speed, jump);
    // body/head rotation
    let d = this.yaw - this.bodyYaw; while (d > Math.PI) d -= Math.PI * 2; while (d < -Math.PI) d += Math.PI * 2;
    this.bodyYaw += d * 0.3;
    if (lookAt) {
      const dx = lookAt.pos[0] - this.pos[0], dz = lookAt.pos[2] - this.pos[2];
      let hy = Math.atan2(-dx, -dz) - this.bodyYaw; while (hy > Math.PI) hy -= Math.PI * 2; while (hy < -Math.PI) hy += Math.PI * 2;
      this.headYaw += (clamp(hy, -1.2, 1.2) - this.headYaw) * 0.3;
      const dy = (lookAt.pos[1] + (lookAt.eyeH || 1)) - (this.pos[1] + this.h * 0.85);
      this.pitch = clamp(Math.atan2(dy, Math.hypot(dx, dz)), -0.8, 0.8);
    } else { this.headYaw *= 0.9; this.pitch *= 0.9; }
    // chickens lay eggs
    if (this.type === 'chicken' && --this.eggT < 0) { this.eggT = 6000 + Math.random() * 6000; this.game.dropItem(this.pos[0], this.pos[1] + 0.3, this.pos[2], makeStack('egg', 1)); Sound.pop(this.pos); }
    // sheep eat grass to regrow wool
    if (this.type === 'sheep' && this.sheared && Math.random() < 0.002) {
      const bx = Math.floor(this.pos[0]), by = Math.floor(this.pos[1] - 0.5), bz = Math.floor(this.pos[2]);
      if (this.world.getBlock(bx, by, bz) === 2) { this.world.setBlock(bx, by, bz, 3, 0); this.sheared = false; this.eatT = 40; }
    }
    if (this.eatT > 0) this.eatT--;
    if (this.loveT > 0) { this.loveT--; if (this.age % 10 === 0) this.game.particles.spawn('heart', this.pos[0] + (Math.random() - 0.5) * this.w, this.pos[1] + this.h, this.pos[2] + (Math.random() - 0.5) * this.w, 0, 0.02, 0); }
    if (this.baby && --this.growT <= 0) { this.baby = false; this.w = this.T.w; this.h = this.T.h; }
    if (this.swingT > 0) this.swingT--;
    this.animate();
  }
  makeBaby() { this.baby = true; this.growT = 6000; this.w = this.T.w * 0.5; this.h = this.T.h * 0.5; return this; }
  creeperTick() {
    if (this.type !== 'creeper' || this.fuse < 0) { this.prevFuse = this.fuse; return false; }
    this.prevFuse = this.fuse;
    this.fuse++;
    if (this.fuse >= 30) {
      this.dead = true;
      this.game.explode(this.pos[0], this.pos[1] + 0.8, this.pos[2], 3, this);
      return true;
    }
    return false;
  }
  turnTo(a, rate) {
    let d = a - this.yaw; while (d > Math.PI) d -= Math.PI * 2; while (d < -Math.PI) d += Math.PI * 2;
    this.yaw += clamp(d, -rate, rate);
  }
  pickWander(r) {
    const a = Math.random() * Math.PI * 2, d = 3 + Math.random() * r;
    this.wander = [this.pos[0] + Math.sin(a) * d, this.pos[1], this.pos[2] + Math.cos(a) * d];
    this.wanderT = 120;
  }
  canSee(t) {
    const a = [this.pos[0], this.pos[1] + this.h * 0.85, this.pos[2]], b = [t.pos[0], t.pos[1] + (t.eyeH || 1), t.pos[2]];
    const d = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]), n = Math.ceil(d * 2);
    for (let i = 1; i < n; i++) {
      const k = i / n;
      const id = this.world.getBlock(Math.floor(lerp(a[0], b[0], k)), Math.floor(lerp(a[1], b[1], k)), Math.floor(lerp(a[2], b[2], k)));
      if (SOLIDCUBE[id]) return false;
    }
    return true;
  }
  shoot(t) {
    const sx = this.pos[0], sy = this.pos[1] + 1.5, sz = this.pos[2];
    const dx = t.pos[0] - sx, dz = t.pos[2] - sz, dist = Math.hypot(dx, dz);
    const dy = t.pos[1] + 1.2 - sy + dist * 0.2 * 0.5;
    const l = Math.hypot(dx, dy, dz);
    const sp = 1.6, err = 0.08;
    const a = new Arrow(this.game, sx, sy, sz, [dx / l * sp + (Math.random() - 0.5) * err, dy / l * sp + (Math.random() - 0.5) * err, dz / l * sp + (Math.random() - 0.5) * err], this);
    this.game.addEntity(a);
    Sound.bow(this.pos);
    this.swingT = 6;
  }
  pose(a) {
    const swing = lerp(this.limbSwing - this.limbAmt, this.limbSwing, a) * 0.6662, amt = this.limbAmt;
    const l = Math.cos(swing) * 1.4 * amt, r = Math.cos(swing + Math.PI) * 1.4 * amt;
    const P = { head: { ry: this.headYaw, rx: this.pitch } };
    switch (this.T.model) {
      case 'zombie': case 'skeleton': {
        const raise = this.type === 'zombie' ? -Math.PI / 2 : (this.target ? -Math.PI / 2 : 0);
        P.rarm = { rx: raise + (this.type === 'zombie' ? 0 : r * 0.5) - (this.swingT > 0 ? Math.sin(this.swingT / 6 * Math.PI) * 0.6 : 0) };
        P.larm = { rx: raise + (this.type === 'zombie' ? 0 : l * 0.5) };
        P.rleg = { rx: l }; P.lleg = { rx: r };
        break;
      }
      case 'creeper': P.leg0 = { rx: l }; P.leg1 = { rx: r }; P.leg2 = { rx: r }; P.leg3 = { rx: l }; break;
      case 'chicken': {
        P.leg0 = { rx: l }; P.leg1 = { rx: r };
        const flap = this.onGround ? 0 : Math.sin(this.age * 1.5) * 0.8 + 0.8;
        P.wing0 = { rz: -flap }; P.wing1 = { rz: flap };
        break;
      }
      case 'spider': {
        for (let i = 0; i < 8; i++) {
          const right = i % 2 === 0, row = i >> 1;
          const spread = [0.7, 0.3, -0.3, -0.7][row];
          const ph = Math.cos(swing * 2 + (row % 2 ? Math.PI : 0) + (right ? 0 : Math.PI)) * 0.4 * amt;
          P['leg' + i] = { ry: (right ? -spread : spread) + ph, rz: right ? -0.6 + Math.abs(ph) * 0.3 : 0.6 - Math.abs(ph) * 0.3 };
        }
        break;
      }
      default:
        P.leg0 = { rx: l }; P.leg1 = { rx: r }; P.leg2 = { rx: r }; P.leg3 = { rx: l };
        if (this.type === 'sheep' && this.eatT > 0) P.head.rx = -0.9;
    }
    return P;
  }
}

// ---------------------------------------------------------------------------
// Item entity
// ---------------------------------------------------------------------------
class ItemEntity extends Entity {
  constructor(game, x, y, z, stack, vel) {
    super(game, x, y, z, 0.25, 0.25);
    this.stack = stack; this.pickup = 10; this.bobOff = Math.random() * Math.PI * 2;
    if (vel) this.vel = vel; else this.vel = [(Math.random() - 0.5) * 0.2, 0.2, (Math.random() - 0.5) * 0.2];
  }
  tick() {
    this.savePrev();
    this.age++;
    if (this.pickup > 0) this.pickup--;
    this.updateFluids();
    const v = this.vel;
    if (this.inWater) { v[1] += 0.01; v[0] *= 0.9; v[2] *= 0.9; v[1] *= 0.9; }
    else v[1] -= 0.04;
    if (this.collides(this.box())) { this.pos[1] += 0.1; v[1] = 0.1; }
    this.move(v[0], v[1], v[2]);
    const f = this.onGround ? 0.6 * 0.98 : 0.98;
    v[0] *= f; v[2] *= f; v[1] *= 0.98;
    if (this.onGround) v[1] *= -0.5;
    if (this.inLava) { this.dead = true; Sound.fizz(this.pos); return; }
    if (this.age > 6000) this.dead = true;
    // merge
    if (this.age % 20 === 0) {
      for (const e of this.game.entities) {
        if (e === this || !(e instanceof ItemEntity) || e.dead || e.stack.id !== this.stack.id || ITEMS[e.stack.id].durability) continue;
        if (this.distTo(e) < 1.2 && this.stack.count + e.stack.count <= maxStack(this.stack.id)) { this.stack.count += e.stack.count; e.dead = true; }
      }
    }
    // pickup
    const p = this.game.player;
    if (this.pickup <= 0 && p.deathTime === 0) {
      const b = p.box();
      if (this.pos[0] > b[0] - 1 && this.pos[0] < b[3] + 1 && this.pos[1] > b[1] - 0.5 && this.pos[1] < b[4] + 0.5 && this.pos[2] > b[2] - 1 && this.pos[2] < b[5] + 1) {
        const left = p.inv.add(this.stack);
        if (left < this.stack.count) {
          this.game.onPickup(this.stack.id, this.stack.count - left);
          Sound.pop(this.pos);
          this.game.particles.pickupAnim(this, p);
        }
        if (left === 0) this.dead = true; else this.stack.count = left;
      }
    }
  }
}

class FallingBlock extends Entity {
  constructor(game, x, y, z, id, meta) { super(game, x + 0.5, y, z + 0.5, 0.98, 0.98); this.blockId = id; this.meta = meta; }
  tick() {
    this.savePrev(); this.age++;
    this.vel[1] = (this.vel[1] - 0.04) * 0.98;
    this.move(0, this.vel[1], 0);
    if (this.onGround || this.age > 600) {
      this.dead = true;
      const x = Math.floor(this.pos[0]), y = Math.floor(this.pos[1] + 0.5), z = Math.floor(this.pos[2]);
      const cur = this.world.getBlock(x, y, z);
      if (cur === 0 || REPLACEABLE[cur]) { this.world.setBlock(x, y, z, this.blockId, this.meta); Sound.block('place', BLOCKS[this.blockId].sound, this.pos); }
      else this.game.dropItem(this.pos[0], this.pos[1], this.pos[2], makeStack(this.blockId, 1));
    }
  }
}

class PrimedTNT extends Entity {
  constructor(game, x, y, z, fuse = 80) {
    super(game, x + 0.5, y, z + 0.5, 0.98, 0.98);
    this.fuse = fuse;
    const a = Math.random() * Math.PI * 2;
    this.vel = [-Math.sin(a) * 0.02, 0.2, -Math.cos(a) * 0.02];
    Sound.fuse(this.pos);
  }
  tick() {
    this.savePrev(); this.age++;
    this.updateFluids();
    this.vel[1] -= 0.04;
    this.move(this.vel[0], this.vel[1], this.vel[2]);
    this.vel[0] *= 0.98; this.vel[1] *= 0.98; this.vel[2] *= 0.98;
    if (this.onGround) { this.vel[0] *= 0.7; this.vel[2] *= 0.7; this.vel[1] *= -0.5; }
    if (Math.random() < 0.5) this.game.particles.spawn('smoke', this.pos[0], this.pos[1] + 1.1, this.pos[2], 0, 0.02, 0);
    if (--this.fuse <= 0) { this.dead = true; this.game.explode(this.pos[0], this.pos[1] + 0.49, this.pos[2], 4, this, this.inWater); }
  }
}

class Arrow extends Entity {
  constructor(game, x, y, z, vel, shooter) {
    super(game, x, y, z, 0.5, 0.5);
    this.vel = vel; this.shooter = shooter; this.stuck = false; this.life = 0;
    this.fromPlayer = shooter === game.player;
    this.updateRot();
  }
  breakSnowball() {
    for (let i = 0; i < 8; i++) this.game.particles.spawn('block', this.pos[0], this.pos[1], this.pos[2], (Math.random() - 0.5) * 0.2, Math.random() * 0.2, (Math.random() - 0.5) * 0.2, { id: 1, layer: ITEMS[this.snowball].tex });
    if (ITEMS[this.snowball].key === 'egg' && Math.random() < 0.125) { const c = new Mob(this.game, 'chicken', this.pos[0], this.pos[1], this.pos[2]).makeBaby(); c.persistent = true; this.game.addEntity(c); }
  }
  updateRot() { const v = this.vel; this.yaw = Math.atan2(-v[0], -v[2]); this.pitch = Math.atan2(v[1], Math.hypot(v[0], v[2])); }
  tick() {
    this.savePrev(); this.age++;
    if (this.stuck) {
      if (++this.life > 1200) this.dead = true;
      if (this.world.getBlock(...this.stuckAt) === 0) { this.stuck = false; }
      if (this.fromPlayer && this.age > 10) {
        const p = this.game.player;
        if (this.distTo(p) < 1.5 && p.inv.add(makeStack('arrow', 1)) === 0) { this.dead = true; Sound.pop(this.pos); }
      }
      return;
    }
    const v = this.vel;
    const steps = Math.ceil(Math.hypot(v[0], v[1], v[2]) / 0.25);
    for (let i = 0; i < steps; i++) {
      const nx = this.pos[0] + v[0] / steps, ny = this.pos[1] + v[1] / steps, nz = this.pos[2] + v[2] / steps;
      // entity hit
      for (const e of this.game.entities.concat([this.game.player])) {
        if (e === this.shooter || !(e instanceof Living) || e.deathTime > 0 || (this.age < 3 && e === this.shooter)) continue;
        const b = e.box();
        if (nx > b[0] - 0.15 && nx < b[3] + 0.15 && ny > b[1] && ny < b[4] && nz > b[2] - 0.15 && nz < b[5] + 0.15) {
          const dmg = this.snowball ? 0 : Math.ceil(Math.hypot(v[0], v[1], v[2]) * 2);
          if (this.snowball) { this.breakSnowball(); }
          e.hurt(dmg, { type: 'arrow', attacker: this.shooter, knockFrom: [this.pos[0] - v[0], 0, this.pos[2] - v[2]], knock: 0.3, player: this.fromPlayer });
          Sound.arrowHit(this.pos);
          this.dead = true; return;
        }
      }
      const bx = Math.floor(nx), by = Math.floor(ny), bz = Math.floor(nz);
      const id = this.world.getBlock(bx, by, bz);
      if (id && BLOCKS[id].solid) {
        if (this.snowball) { this.breakSnowball(); this.dead = true; return; }
        this.stuck = true; this.stuckAt = [bx, by, bz];
        if (id === 26 && this.shooter && this.shooter.fire > 0) { }
        Sound.arrowHit(this.pos);
        return;
      }
      this.pos[0] = nx; this.pos[1] = ny; this.pos[2] = nz;
    }
    this.updateFluids();
    const drag = this.inWater ? 0.6 : 0.99;
    v[0] *= drag; v[1] = v[1] * drag - 0.05; v[2] *= drag;
    this.updateRot();
    if (this.age > 400) this.dead = true;
  }
}

// ---------------------------------------------------------------------------
// Particles
// ---------------------------------------------------------------------------
class Particles {
  constructor(game) { this.game = game; this.list = []; this.pickups = []; }
  spawn(type, x, y, z, vx, vy, vz, extra) {
    if (this.list.length > 3000) return;
    const p = { type, x, y, z, px: x, py: y, pz: z, vx, vy, vz, age: 0, life: 20, size: 0.1, grav: 0, layer: 0, u0: 0, v0: 0, us: 1, col: [255, 255, 255], bright: false, frames: 0, base: 0 };
    const r = Math.random;
    switch (type) {
      case 'block': {
        const b = BLOCKS[extra.id];
        p.layer = extra.layer ?? (b.texFn ? b.texFn.call(b, 0, extra.meta || 0) : b.particle);
        p.us = 0.25; p.u0 = r() * 0.75; p.v0 = r() * 0.75;
        p.life = 4 / (r() * 0.9 + 0.1); p.grav = 0.04; p.size = 0.05 + r() * 0.05;
        if (b.tint) { const t = extra.tint || [124, 189, 107]; p.col = t; }
        break;
      }
      case 'smoke': p.frames = 12; p.base = T('p_big_smoke_0'); p.life = 20 + r() * 20; p.size = 0.15 + r() * 0.15; p.grav = -0.002; p.col = [80, 80, 80]; break;
      case 'poof': p.frames = 8; p.base = T('p_generic_0'); p.life = 16 + r() * 8; p.size = 0.15 + r() * 0.1; p.col = [230, 230, 230]; p.reverse = true; break;
      case 'explosion': p.frames = 16; p.base = T('p_explosion_0'); p.life = 8 + r() * 8; p.size = 1 + r() * 0.8; p.bright = true; break;
      case 'flame': p.layer = T('p_flame'); p.life = 10 + r() * 10; p.size = 0.06 + r() * 0.04; p.bright = true; break;
      case 'lava': p.layer = T('p_lava'); p.life = 30; p.size = 0.08; p.grav = 0.04; p.bright = true; break;
      case 'splash': p.layer = T('p_splash_' + (r() * 4 | 0)); p.life = 10; p.size = 0.08; p.grav = 0.04; p.col = [80, 120, 230]; break;
      case 'bubble': p.layer = T('p_bubble'); p.life = 20 + r() * 20; p.size = 0.05; p.grav = -0.01; break;
      case 'crit': p.layer = T('p_critical_hit'); p.life = 12; p.size = 0.08; p.grav = 0.02; p.bright = true; break;
      case 'heart': p.layer = T('p_heart'); p.life = 20; p.size = 0.15; p.grav = -0.005; p.bright = true; break;
      case 'note': p.layer = T('p_note'); p.life = 20; p.size = 0.15; p.bright = true; p.col = extra.col; break;
      case 'damage': p.layer = T('p_damage'); p.life = 15; p.size = 0.1; p.grav = 0.02; break;
      case 'glint': p.layer = T('p_glint'); p.life = 20; p.size = 0.08; p.bright = true; break;
      case 'bone_meal': p.layer = T('p_effect_' + (r() * 3 | 0)); p.life = 20; p.size = 0.08; p.col = [120, 220, 90]; p.bright = true; break;
      case 'rain': p.layer = T('p_splash_' + (r() * 4 | 0)); p.life = 6; p.size = 0.06; p.grav = 0.04; p.col = [100, 140, 230]; break;
    }
    this.list.push(p);
    return p;
  }
  blockBreak(x, y, z, id, meta) {
    const tint = BLOCKS[id].tint ? this.game.tintAt(x, z, BLOCKS[id].tint) : null;
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) {
      const px = x + (i + 0.5) / 4, py = y + (j + 0.5) / 4, pz = z + (k + 0.5) / 4;
      this.spawn('block', px, py, pz, (px - x - 0.5) * 0.3 + (Math.random() - 0.5) * 0.05, (py - y - 0.5) * 0.3 + Math.random() * 0.1, (pz - z - 0.5) * 0.3, { id, meta, tint });
    }
  }
  blockHit(x, y, z, face, id, meta) {
    const D = DIRS[face];
    const px = x + 0.5 + D[0] * 0.52 + (D[0] ? 0 : (Math.random() - 0.5) * 0.9);
    const py = y + 0.5 + D[1] * 0.52 + (D[1] ? 0 : (Math.random() - 0.5) * 0.9);
    const pz = z + 0.5 + D[2] * 0.52 + (D[2] ? 0 : (Math.random() - 0.5) * 0.9);
    const tint = BLOCKS[id].tint ? this.game.tintAt(x, z, BLOCKS[id].tint) : null;
    this.spawn('block', px, py, pz, D[0] * 0.05, 0.05 + D[1] * 0.05, D[2] * 0.05, { id, meta, tint });
  }
  pickupAnim(e, p) { this.pickups.push({ stack: { ...e.stack }, from: [...e.pos], t: 0, target: p }); }
  tick() {
    const w = this.game.world;
    for (const p of this.list) {
      p.px = p.x; p.py = p.y; p.pz = p.z;
      p.age++;
      p.vy -= p.grav;
      let nx = p.x + p.vx, ny = p.y + p.vy, nz = p.z + p.vz;
      if (p.grav > 0) {
        const id = w.getBlock(Math.floor(nx), Math.floor(ny), Math.floor(nz));
        if (id && SOLIDCUBE[id]) { if (p.vy < 0) { ny = p.y; p.vy = 0; p.vx *= 0.7; p.vz *= 0.7; } else { nx = p.x; nz = p.z; } }
      }
      p.x = nx; p.y = ny; p.z = nz;
      p.vx *= 0.98; p.vy *= 0.98; p.vz *= 0.98;
      if (p.type === 'smoke' || p.type === 'explosion') { p.vx *= 0.9; p.vz *= 0.9; }
      if (p.type === 'bubble' && w.getBlock(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)) !== WATER) p.age = p.life;
      if (p.type === 'lava' && Math.random() < 0.1) this.spawn('smoke', p.x, p.y, p.z, 0, 0.01, 0);
      if (p.age % 4 === 1) p.light = w.getLight(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z));
    }
    this.list = this.list.filter(p => p.age < p.life);
    for (const q of this.pickups) q.t++;
    this.pickups = this.pickups.filter(q => q.t < 3);
  }
  // build billboard quads into a MeshBuffer
  build(mb, a, cam, right, up) {
    mb.reset();
    for (const p of this.list) {
      const x = lerp(p.px, p.x, a), y = lerp(p.py, p.y, a), z = lerp(p.pz, p.z, a);
      let layer = p.layer;
      if (p.frames) {
        const f = Math.min(p.frames - 1, Math.floor((p.age + a) / p.life * p.frames));
        layer = p.base + (p.reverse ? p.frames - 1 - f : f);
      }
      const s = p.size;
      const rx = right[0] * s, ry = right[1] * s, rz = right[2] * s, ux = up[0] * s, uy = up[1] * s, uz = up[2] * s;
      const l = p.light ?? 0xF0;
      const sky = p.bright ? 255 : (l >> 4) * 17, blk = p.bright ? 255 : (l & 15) * 17;
      const u0 = p.u0, v0 = p.v0, u1 = u0 + p.us, v1 = v0 + p.us;
      const c = p.col;
      mb.v(x - rx - ux, y - ry - uy, z - rz - uz, u0, v1, layer, 0, c[0], c[1], c[2], 255, sky, blk, 0);
      mb.v(x + rx - ux, y + ry - uy, z + rz - uz, u1, v1, layer, 0, c[0], c[1], c[2], 255, sky, blk, 0);
      mb.v(x + rx + ux, y + ry + uy, z + rz + uz, u1, v0, layer, 0, c[0], c[1], c[2], 255, sky, blk, 0);
      mb.v(x - rx + ux, y - ry + uy, z - rz + uz, u0, v0, layer, 0, c[0], c[1], c[2], 255, sky, blk, 0);
    }
  }
}
