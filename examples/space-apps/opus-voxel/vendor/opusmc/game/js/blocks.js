'use strict';
// ---------------------------------------------------------------------------
// Block & item registry, recipes, smelting
// ---------------------------------------------------------------------------
function T(name) {
  const l = ASSETS.tiles[name];
  if (l === undefined) { console.warn('missing tile', name); return ASSETS.tiles.white; }
  return l;
}

const BLOCKS = new Array(256).fill(null);
const ITEMS = [];          // id -> item def (blocks + items)
const ITEM_KEY = {};       // key -> id
const BID = {};            // block key -> id

// Shape constants
const SH_CUBE = 0, SH_CROSS = 1, SH_LIQUID = 2, SH_BOXES = 3, SH_TORCH = 4, SH_NONE = 5, SH_CROP = 6, SH_CACTUS = 7, SH_MODEL = 8;

function defBlock(id, key, cn, o = {}) {
  const shape = o.shape ?? SH_CUBE;
  const b = {
    id, key, cn, shape,
    render: o.render || (shape === SH_CUBE ? 'opaque' : 'cutout'),
    solid: o.solid ?? (shape === SH_CUBE || shape === SH_BOXES || shape === SH_CACTUS || shape === SH_MODEL),
    model: o.model || null, bed: !!o.bed,
    opaque: false,
    light: o.light || 0,
    hardness: o.hardness ?? 1,
    tool: o.tool || null, level: o.level || 0, needsTool: o.needsTool ?? false,
    sound: o.sound || 'stone',
    tint: o.tint || null,
    gravity: !!o.gravity,
    replaceable: !!o.replaceable,
    cullSame: !!o.cullSame,
    drops: o.drops,
    boxes: o.boxes || null,
    climbable: !!o.climbable,
    facing: !!o.facing, axis: !!o.axis,
    needsSupport: !!o.needsSupport,
    onUse: o.onUse || null,
    flammable: !!o.flammable,
    texFn: o.texFn || null,
    fuel: o.fuel || 0,
    noItem: !!o.noItem,
    icon: o.icon || null,
    damage: o.damage || 0,
    slip: o.slip || 0.6,
    tex: [0, 0, 0, 0, 0, 0], overlay: [0, 0, 0, 0, 0, 0],
    particle: 0,
    slab: !!o.slab, stairs: !!o.stairs, door: !!o.door, trapdoor: !!o.trapdoor, ladder: !!o.ladder,
  };
  b.opaque = o.opaque ?? (shape === SH_CUBE && b.render === 'opaque');
  b.opacity = o.opacity ?? (b.opaque ? 15 : 0);
  const t = o.tex;
  if (typeof t === 'string') b.tex.fill(T(t));
  else if (t) {
    const side = t.side ? T(t.side) : 0;
    b.tex = [side, side, T(t.bottom || t.top || t.side), T(t.top || t.side), side, side];
    if (t.front) b.texFront = T(t.front);
    if (t.frontOn) b.texFrontOn = T(t.frontOn);
  }
  if (o.overlay) b.overlay = [T(o.overlay), T(o.overlay), 0, 0, T(o.overlay), T(o.overlay)];
  b.particle = o.particle ? T(o.particle) : b.tex[0];
  if (b.texFront && !o.texFn) {
    b.texFn = (face, meta) => (face === (meta & 7) ? ((meta & 8) && b.texFrontOn ? b.texFrontOn : b.texFront) : b.tex[face]);
  }
  BLOCKS[id] = b; BID[key] = id;
  if (!b.noItem) {
    ITEMS[id] = { id, key, cn, block: id, stack: o.stack || 64, fuel: b.fuel, flat: o.flatIcon || null };
    ITEM_KEY[key] = id;
  }
  return b;
}

// ---- helpers for drops -----------------------------------------------------
const drop = (key, n = 1) => () => [[key, n]];
const nothing = () => [];

// Axis textures: logs rotate their texture
function logTex(face, meta) {
  const a = meta & 3; // 0 y, 1 x, 2 z
  const b = this;
  if (a === 0) return b.tex[face];
  if (a === 1) return (face === 0 || face === 1) ? b.tex[3] : b.tex[0];
  return (face === 4 || face === 5) ? b.tex[3] : b.tex[0];
}

// Box shapes (units of 1/16)
const SLAB_BOXES = [[[0, 0, 0, 16, 8, 16]], [[0, 8, 0, 16, 16, 16]], [[0, 0, 0, 16, 16, 16]]];
function stairBoxes(meta) {
  const f = meta & 3, top = meta & 4;
  const base = top ? [0, 8, 0, 16, 16, 16] : [0, 0, 0, 16, 8, 16];
  const y0 = top ? 0 : 8, y1 = top ? 8 : 16;
  // f: 0 = high side east(+x), 1 = west(-x), 2 = south(+z), 3 = north(-z)
  const step = [[8, y0, 0, 16, y1, 16], [0, y0, 0, 8, y1, 16], [0, y0, 8, 16, y1, 16], [0, y0, 0, 16, y1, 8]][f];
  return [base, step];
}
function doorBoxes(meta) {
  // meta: bits0-1 facing (0 E,1 W,2 S,3 N) bit2 open, bit3 upper
  let f = meta & 3;
  if (meta & 4) f = [2, 3, 1, 0][f];
  return [[[0, 0, 0, 3, 16, 16], [13, 0, 0, 16, 16, 16], [0, 0, 0, 16, 16, 3], [0, 0, 13, 16, 16, 16]][f]];
}
function ladderBoxes(meta) {
  // meta = dir index of the wall it's attached to
  return [{ 0: [0, 0, 0, 2, 16, 16], 1: [14, 0, 0, 16, 16, 16], 4: [0, 0, 0, 16, 16, 2], 5: [0, 0, 14, 16, 16, 16] }[meta] || [0, 0, 0, 16, 16, 2]];
}

// ---------------------------------------------------------------------------
// Blocks
// ---------------------------------------------------------------------------
defBlock(0, 'air', '空气', { shape: SH_NONE, solid: false, opaque: false, noItem: true, replaceable: true, hardness: 0 });
defBlock(1, 'stone', '石头', { tex: 'stone', hardness: 1.5, tool: 'pickaxe', needsTool: true, drops: drop('cobblestone') });
defBlock(2, 'grass_block', '草方块', { tex: { top: 'grass_block_top', bottom: 'dirt', side: 'grass_side_base' }, overlay: 'grass_block_side_overlay', tint: 'grass', hardness: 0.6, tool: 'shovel', sound: 'grass', drops: drop('dirt'), particle: 'dirt',
  texFn(face, meta) { return (meta & 1) && face !== 2 && face !== 3 ? T('grass_block_snow') : this.tex[face]; } });
defBlock(3, 'dirt', '泥土', { tex: 'dirt', hardness: 0.5, tool: 'shovel', sound: 'gravel' });
defBlock(4, 'cobblestone', '圆石', { tex: 'cobblestone', hardness: 2, tool: 'pickaxe', needsTool: true });
defBlock(5, 'oak_planks', '橡木木板', { tex: 'oak_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300, flammable: true });
defBlock(6, 'bedrock', '基岩', { tex: 'bedrock', hardness: -1 });
defBlock(7, 'water', '水', { shape: SH_LIQUID, tex: 'water_still', render: 'translucent', solid: false, opacity: 2, hardness: -1, replaceable: true, noItem: true, tint: 'water', cullSame: true });
defBlock(8, 'lava', '熔岩', { shape: SH_LIQUID, tex: 'lava_still', render: 'opaque', solid: false, opacity: 15, light: 15, hardness: -1, replaceable: true, noItem: true, cullSame: true });
defBlock(9, 'sand', '沙子', { tex: 'sand', hardness: 0.5, tool: 'shovel', sound: 'sand', gravity: true });
defBlock(10, 'gravel', '沙砾', { tex: 'gravel', hardness: 0.6, tool: 'shovel', sound: 'gravel', gravity: true, drops: (m, r) => [[r() < 0.1 ? 'flint' : 'gravel', 1]] });
defBlock(11, 'gold_ore', '金矿石', { tex: 'gold_ore', hardness: 3, tool: 'pickaxe', level: 2, needsTool: true, drops: drop('raw_gold') });
defBlock(12, 'iron_ore', '铁矿石', { tex: 'iron_ore', hardness: 3, tool: 'pickaxe', level: 1, needsTool: true, drops: drop('raw_iron') });
defBlock(13, 'coal_ore', '煤矿石', { tex: 'coal_ore', hardness: 3, tool: 'pickaxe', needsTool: true, drops: drop('coal'), xp: 1 });
defBlock(14, 'oak_log', '橡木原木', { tex: { top: 'oak_log_top', side: 'oak_log' }, hardness: 2, tool: 'axe', sound: 'wood', axis: true, texFn: logTex, fuel: 300, flammable: true });
defBlock(15, 'oak_leaves', '橡树树叶', { tex: 'oak_leaves', render: 'cutout', opaque: false, opacity: 1, hardness: 0.2, tool: 'hoe', sound: 'grass', tint: 'foliage',
  drops: (m, r) => { const d = []; if (r() < 0.05) d.push(['oak_sapling', 1]); if (r() < 0.02) d.push(['stick', 1 + (r() * 2 | 0)]); if (r() < 0.02) d.push(['apple', 1]); return d; } });
defBlock(16, 'glass', '玻璃', { tex: 'glass', render: 'cutout', opaque: false, hardness: 0.3, sound: 'glass', drops: nothing, cullSame: true });
defBlock(17, 'sandstone', '砂岩', { tex: { top: 'sandstone_top', bottom: 'sandstone_bottom', side: 'sandstone' }, hardness: 0.8, tool: 'pickaxe', needsTool: true });
defBlock(18, 'diamond_ore', '钻石矿石', { tex: 'diamond_ore', hardness: 3, tool: 'pickaxe', level: 2, needsTool: true, drops: drop('diamond') });
defBlock(19, 'crafting_table', '工作台', { tex: { top: 'crafting_table_top', bottom: 'oak_planks', side: 'crafting_table_side', front: 'crafting_table_front' }, facing: true, hardness: 2.5, tool: 'axe', sound: 'wood', fuel: 300, onUse: 'crafting' });
defBlock(20, 'furnace', '熔炉', { tex: { top: 'furnace_top', side: 'furnace_side', front: 'furnace_front', frontOn: 'furnace_front_on' }, facing: true, hardness: 3.5, tool: 'pickaxe', needsTool: true, onUse: 'furnace' });
defBlock(21, 'torch', '火把', { shape: SH_TORCH, tex: 'torch', solid: false, light: 14, hardness: 0, sound: 'wood', needsSupport: true, flatIcon: 'torch' });
defBlock(22, 'bricks', '砖块', { tex: 'bricks', hardness: 2, tool: 'pickaxe', needsTool: true });
defBlock(23, 'stone_bricks', '石砖', { tex: 'stone_bricks', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(24, 'mossy_cobblestone', '苔石', { tex: 'mossy_cobblestone', hardness: 2, tool: 'pickaxe', needsTool: true });
defBlock(25, 'bookshelf', '书架', { tex: { top: 'oak_planks', side: 'bookshelf' }, hardness: 1.5, tool: 'axe', sound: 'wood', drops: drop('book', 3), fuel: 300 });
defBlock(26, 'tnt', 'TNT', { tex: { top: 'tnt_top', bottom: 'tnt_bottom', side: 'tnt_side' }, hardness: 0, sound: 'grass' });
defBlock(27, 'glowstone', '荧石', { tex: 'glowstone', light: 15, hardness: 0.3, sound: 'glass' });
defBlock(28, 'obsidian', '黑曜石', { tex: 'obsidian', hardness: 50, tool: 'pickaxe', level: 3, needsTool: true });
defBlock(29, 'iron_block', '铁块', { tex: 'iron_block', hardness: 5, tool: 'pickaxe', level: 1, needsTool: true, sound: 'metal' });
defBlock(30, 'gold_block', '金块', { tex: 'gold_block', hardness: 3, tool: 'pickaxe', level: 2, needsTool: true, sound: 'metal' });
defBlock(31, 'diamond_block', '钻石块', { tex: 'diamond_block', hardness: 5, tool: 'pickaxe', level: 2, needsTool: true, sound: 'metal' });
defBlock(32, 'coal_block', '煤炭块', { tex: 'coal_block', hardness: 5, tool: 'pickaxe', needsTool: true, fuel: 16000 });
defBlock(33, 'snow_block', '雪块', { tex: 'snow', hardness: 0.2, tool: 'shovel', needsTool: true, sound: 'snow', drops: drop('snowball', 4) });
defBlock(34, 'snow', '雪', { shape: SH_BOXES, boxes: () => [[0, 0, 0, 16, 2, 16]], tex: 'snow', render: 'opaque', solid: false, hardness: 0.1, tool: 'shovel', needsTool: true, sound: 'snow', replaceable: true, needsSupport: true, drops: drop('snowball') });
defBlock(35, 'ice', '冰', { tex: 'ice', render: 'translucent', opaque: false, opacity: 2, hardness: 0.5, tool: 'pickaxe', sound: 'glass', drops: nothing, cullSame: true, slip: 0.98 });
defBlock(36, 'clay', '黏土块', { tex: 'clay', hardness: 0.6, tool: 'shovel', sound: 'gravel', drops: drop('clay_ball', 4) });
defBlock(37, 'cactus', '仙人掌', { shape: SH_CACTUS, tex: { top: 'cactus_top', bottom: 'cactus_bottom', side: 'cactus_side' }, render: 'cutout', hardness: 0.4, sound: 'wool', needsSupport: true, damage: 1 });
defBlock(38, 'sugar_cane', '甘蔗', { shape: SH_CROSS, tex: 'sugar_cane', solid: false, hardness: 0, sound: 'grass', needsSupport: true, drops: drop('sugar_cane'), noItem: true });
defBlock(39, 'short_grass', '草', { shape: SH_CROSS, tex: 'short_grass', solid: false, hardness: 0, sound: 'grass', tint: 'grass', replaceable: true, needsSupport: true, drops: (m, r) => r() < 0.125 ? [['wheat_seeds', 1]] : [], flatIcon: 'short_grass' });
defBlock(40, 'fern', '蕨', { shape: SH_CROSS, tex: 'fern', solid: false, hardness: 0, sound: 'grass', tint: 'grass', replaceable: true, needsSupport: true, drops: (m, r) => r() < 0.125 ? [['wheat_seeds', 1]] : [], flatIcon: 'fern' });
const FLOWERS = [['dandelion', '蒲公英'], ['poppy', '虞美人'], ['cornflower', '矢车菊'], ['oxeye_daisy', '滨菊'], ['azure_bluet', '蓝花美耳草'], ['allium', '绒球葱'], ['blue_orchid', '兰花'], ['red_tulip', '红色郁金香'], ['lily_of_the_valley', '铃兰']];
const FLOWER_IDS = [41, 42, 43, 44, 98, 99, 100, 101, 102];
FLOWERS.forEach(([k, cn], i) => defBlock(FLOWER_IDS[i], k, cn, { shape: SH_CROSS, tex: k, solid: false, hardness: 0, sound: 'grass', needsSupport: true, flatIcon: k }));
defBlock(45, 'dead_bush', '枯死的灌木', { shape: SH_CROSS, tex: 'dead_bush', solid: false, hardness: 0, sound: 'grass', replaceable: true, needsSupport: true, drops: (m, r) => [['stick', r() * 3 | 0]], flatIcon: 'dead_bush' });
defBlock(46, 'birch_log', '白桦原木', { tex: { top: 'birch_log_top', side: 'birch_log' }, hardness: 2, tool: 'axe', sound: 'wood', axis: true, texFn: logTex, fuel: 300 });
defBlock(47, 'birch_leaves', '白桦树叶', { tex: 'birch_leaves', render: 'cutout', opaque: false, opacity: 1, hardness: 0.2, tool: 'hoe', sound: 'grass', tint: 'birch',
  drops: (m, r) => { const d = []; if (r() < 0.05) d.push(['birch_sapling', 1]); if (r() < 0.02) d.push(['stick', 1]); return d; } });
defBlock(48, 'birch_planks', '白桦木板', { tex: 'birch_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(49, 'spruce_log', '云杉原木', { tex: { top: 'spruce_log_top', side: 'spruce_log' }, hardness: 2, tool: 'axe', sound: 'wood', axis: true, texFn: logTex, fuel: 300 });
defBlock(50, 'spruce_leaves', '云杉树叶', { tex: 'spruce_leaves', render: 'cutout', opaque: false, opacity: 1, hardness: 0.2, tool: 'hoe', sound: 'grass', tint: 'spruce',
  drops: (m, r) => { const d = []; if (r() < 0.05) d.push(['spruce_sapling', 1]); if (r() < 0.02) d.push(['stick', 1]); return d; } });
defBlock(51, 'spruce_planks', '云杉木板', { tex: 'spruce_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(52, 'chest', '箱子', { shape: SH_BOXES, boxes: () => [[1, 0, 1, 15, 14, 15]], tex: { top: 'chest_top', side: 'chest_side', front: 'chest_front' }, render: 'opaque', facing: true, hardness: 2.5, tool: 'axe', sound: 'wood', onUse: 'chest', fuel: 300 });
defBlock(53, 'pumpkin', '南瓜', { tex: { top: 'pumpkin_top', side: 'pumpkin_side' }, hardness: 1, tool: 'axe', sound: 'wood' });
defBlock(54, 'jack_o_lantern', '南瓜灯', { tex: { top: 'pumpkin_top', side: 'pumpkin_side', front: 'jack_o_lantern' }, facing: true, light: 15, hardness: 1, tool: 'axe', sound: 'wood' });
defBlock(55, 'melon', '西瓜', { tex: { top: 'melon_top', side: 'melon_side' }, hardness: 1, tool: 'axe', sound: 'wood', drops: (m, r) => [['melon_slice', 3 + (r() * 5 | 0)]] });
defBlock(56, 'smooth_stone', '平滑石头', { tex: 'smooth_stone', hardness: 2, tool: 'pickaxe', needsTool: true });
const slabDrop = (k) => (m) => [[k, (m & 3) === 2 ? 2 : 1]];
defBlock(57, 'smooth_stone_slab', '平滑石台阶', { shape: SH_BOXES, boxes: (m) => SLAB_BOXES[m & 3], tex: { top: 'smooth_stone', side: 'smooth_stone_slab_side' }, render: 'opaque', hardness: 2, tool: 'pickaxe', needsTool: true, slab: true, drops: slabDrop('smooth_stone_slab') });
defBlock(58, 'oak_slab', '橡木台阶', { shape: SH_BOXES, boxes: (m) => SLAB_BOXES[m & 3], tex: 'oak_planks', render: 'opaque', hardness: 2, tool: 'axe', sound: 'wood', slab: true, fuel: 150, drops: slabDrop('oak_slab') });
defBlock(59, 'cobblestone_slab', '圆石台阶', { shape: SH_BOXES, boxes: (m) => SLAB_BOXES[m & 3], tex: 'cobblestone', render: 'opaque', hardness: 2, tool: 'pickaxe', needsTool: true, slab: true, drops: slabDrop('cobblestone_slab') });
defBlock(60, 'oak_stairs', '橡木楼梯', { shape: SH_BOXES, boxes: stairBoxes, tex: 'oak_planks', render: 'opaque', hardness: 2, tool: 'axe', sound: 'wood', stairs: true, fuel: 300 });
defBlock(61, 'cobblestone_stairs', '圆石楼梯', { shape: SH_BOXES, boxes: stairBoxes, tex: 'cobblestone', render: 'opaque', hardness: 2, tool: 'pickaxe', needsTool: true, stairs: true });
defBlock(62, 'stone_brick_stairs', '石砖楼梯', { shape: SH_BOXES, boxes: stairBoxes, tex: 'stone_bricks', render: 'opaque', hardness: 1.5, tool: 'pickaxe', needsTool: true, stairs: true });
defBlock(63, 'farmland', '耕地', { shape: SH_BOXES, boxes: () => [[0, 0, 0, 16, 15, 16]], tex: { top: 'farmland', side: 'dirt' }, render: 'opaque', hardness: 0.6, tool: 'shovel', sound: 'gravel', drops: drop('dirt'),
  texFn(face, meta) { return face === 3 ? (meta ? T('farmland_moist') : T('farmland')) : this.tex[face]; } });
const WHEAT_TEX = [0, 1, 2, 3, 4, 5, 6, 7].map(i => 'wheat_stage' + i);
defBlock(64, 'wheat', '小麦', { shape: SH_CROP, tex: 'wheat_stage0', solid: false, hardness: 0, sound: 'grass', needsSupport: true, noItem: true,
  texFn(face, meta) { return T(WHEAT_TEX[meta & 7]); },
  drops: (m, r) => (m & 7) >= 7 ? [['wheat', 1], ['wheat_seeds', 1 + (r() * 3 | 0)]] : [['wheat_seeds', 1]] });
defBlock(65, 'oak_sapling', '橡树树苗', { shape: SH_CROSS, tex: 'oak_sapling', solid: false, hardness: 0, sound: 'grass', needsSupport: true, flatIcon: 'oak_sapling', fuel: 100 });
defBlock(66, 'birch_sapling', '白桦树苗', { shape: SH_CROSS, tex: 'birch_sapling', solid: false, hardness: 0, sound: 'grass', needsSupport: true, flatIcon: 'birch_sapling', fuel: 100 });
defBlock(67, 'spruce_sapling', '云杉树苗', { shape: SH_CROSS, tex: 'spruce_sapling', solid: false, hardness: 0, sound: 'grass', needsSupport: true, flatIcon: 'spruce_sapling', fuel: 100 });
defBlock(68, 'ladder', '梯子', { shape: SH_BOXES, boxes: ladderBoxes, tex: 'ladder', render: 'cutout', hardness: 0.4, tool: 'axe', sound: 'wood', climbable: true, flatIcon: 'ladder', fuel: 300, ladder: true });
defBlock(69, 'oak_door', '橡木门', { shape: SH_BOXES, boxes: doorBoxes, tex: 'oak_door_bottom', render: 'cutout', hardness: 3, tool: 'axe', sound: 'wood', noItem: true, door: true,
  texFn(face, meta) { return (meta & 8) ? T('oak_door_top') : T('oak_door_bottom'); }, drops: drop('oak_door') });
defBlock(70, 'red_mushroom', '红色蘑菇', { shape: SH_CROSS, tex: 'red_mushroom', solid: false, hardness: 0, sound: 'grass', needsSupport: true, flatIcon: 'red_mushroom' });
defBlock(71, 'brown_mushroom', '棕色蘑菇', { shape: SH_CROSS, tex: 'brown_mushroom', solid: false, hardness: 0, sound: 'grass', needsSupport: true, light: 1, flatIcon: 'brown_mushroom' });
defBlock(72, 'andesite', '安山岩', { tex: 'andesite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(73, 'diorite', '闪长岩', { tex: 'diorite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(74, 'granite', '花岗岩', { tex: 'granite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(75, 'deepslate', '深板岩', { tex: { top: 'deepslate_top', side: 'deepslate' }, hardness: 3, tool: 'pickaxe', needsTool: true, sound: 'stone' });
defBlock(76, 'deepslate_coal_ore', '深层煤矿石', { tex: 'deepslate_coal_ore', hardness: 4.5, tool: 'pickaxe', needsTool: true, drops: drop('coal') });
defBlock(77, 'deepslate_iron_ore', '深层铁矿石', { tex: 'deepslate_iron_ore', hardness: 4.5, tool: 'pickaxe', level: 1, needsTool: true, drops: drop('raw_iron') });
defBlock(78, 'deepslate_gold_ore', '深层金矿石', { tex: 'deepslate_gold_ore', hardness: 4.5, tool: 'pickaxe', level: 2, needsTool: true, drops: drop('raw_gold') });
defBlock(79, 'deepslate_diamond_ore', '深层钻石矿石', { tex: 'deepslate_diamond_ore', hardness: 4.5, tool: 'pickaxe', level: 2, needsTool: true, drops: drop('diamond') });
defBlock(80, 'redstone_ore', '红石矿石', { tex: 'redstone_ore', hardness: 3, tool: 'pickaxe', level: 2, needsTool: true, drops: (m, r) => [['redstone', 4 + (r() * 2 | 0)]] });
defBlock(81, 'lapis_ore', '青金石矿石', { tex: 'lapis_ore', hardness: 3, tool: 'pickaxe', level: 1, needsTool: true, drops: (m, r) => [['lapis_lazuli', 4 + (r() * 5 | 0)]] });
defBlock(82, 'emerald_ore', '绿宝石矿石', { tex: 'emerald_ore', hardness: 3, tool: 'pickaxe', level: 2, needsTool: true, drops: drop('emerald') });
defBlock(83, 'terracotta', '陶瓦', { tex: 'terracotta', hardness: 1.25, tool: 'pickaxe', needsTool: true });
defBlock(84, 'quartz_block', '石英块', { tex: { top: 'quartz_block_top', side: 'quartz_block_side' }, hardness: 0.8, tool: 'pickaxe', needsTool: true });
defBlock(85, 'sea_lantern', '海晶灯', { tex: 'sea_lantern', light: 15, hardness: 0.3, sound: 'glass' });
defBlock(86, 'prismarine', '海晶石', { tex: 'prismarine', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(87, 'hay_block', '干草块', { tex: { top: 'hay_block_top', side: 'hay_block_side' }, hardness: 0.5, tool: 'hoe', sound: 'grass', drops: drop('wheat', 9), axis: true, texFn: logTex });
defBlock(88, 'moss_block', '苔藓块', { tex: 'moss_block', hardness: 0.1, tool: 'hoe', sound: 'grass' });
defBlock(89, 'mud_bricks', '泥砖', { tex: 'mud_bricks', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(90, 'polished_andesite', '磨制安山岩', { tex: 'polished_andesite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(91, 'polished_diorite', '磨制闪长岩', { tex: 'polished_diorite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(92, 'polished_granite', '磨制花岗岩', { tex: 'polished_granite', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(93, 'cracked_stone_bricks', '裂纹石砖', { tex: 'cracked_stone_bricks', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(94, 'mossy_stone_bricks', '苔石砖', { tex: 'mossy_stone_bricks', hardness: 1.5, tool: 'pickaxe', needsTool: true });
defBlock(95, 'bone_block', '骨块', { tex: { top: 'bone_block_top', side: 'bone_block_side' }, hardness: 2, tool: 'pickaxe', needsTool: true });
defBlock(96, 'packed_ice', '浮冰', { tex: 'packed_ice', hardness: 0.5, tool: 'pickaxe', sound: 'glass', slip: 0.98, drops: nothing });
defBlock(97, 'red_sand', '红沙', { tex: 'red_sand', hardness: 0.5, tool: 'shovel', sound: 'sand', gravity: true });
defBlock(103, 'magma_block', '岩浆块', { tex: 'magma', light: 3, hardness: 0.5, tool: 'pickaxe', needsTool: true, damage: 1 });
defBlock(104, 'dark_oak_planks', '深色橡木木板', { tex: 'dark_oak_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(105, 'jungle_planks', '丛林木板', { tex: 'jungle_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(106, 'acacia_planks', '金合欢木板', { tex: 'acacia_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(107, 'cherry_planks', '樱花木板', { tex: 'cherry_planks', hardness: 2, tool: 'axe', sound: 'wood', fuel: 300 });
defBlock(108, 'lapis_block', '青金石块', { tex: 'lapis_block', hardness: 3, tool: 'pickaxe', level: 1, needsTool: true });
defBlock(109, 'emerald_block', '绿宝石块', { tex: 'emerald_block', hardness: 5, tool: 'pickaxe', level: 2, needsTool: true, sound: 'metal' });
defBlock(110, 'redstone_block', '红石块', { tex: 'redstone_block', hardness: 5, tool: 'pickaxe', needsTool: true, sound: 'metal' });
defBlock(111, 'note_block', '音符盒', { tex: 'note_block', hardness: 0.8, tool: 'axe', sound: 'wood', onUse: 'note', fuel: 300 });
defBlock(112, 'oak_trapdoor', '橡木活板门', { shape: SH_BOXES, boxes: (m) => (m & 4) ? [[0, 0, 13, 16, 16, 16]] : (m & 8 ? [[0, 13, 0, 16, 16, 16]] : [[0, 0, 0, 16, 3, 16]]), tex: 'oak_trapdoor', render: 'cutout', hardness: 3, tool: 'axe', sound: 'wood', trapdoor: true, fuel: 300 });
defBlock(113, 'stone_brick_slab', '石砖台阶', { shape: SH_BOXES, boxes: (m) => SLAB_BOXES[m & 3], tex: 'stone_bricks', render: 'opaque', hardness: 1.5, tool: 'pickaxe', needsTool: true, slab: true, drops: slabDrop('stone_brick_slab') });
defBlock(114, 'brick_stairs', '砖楼梯', { shape: SH_BOXES, boxes: stairBoxes, tex: 'bricks', render: 'opaque', hardness: 2, tool: 'pickaxe', needsTool: true, stairs: true });
defBlock(115, 'spruce_stairs', '云杉木楼梯', { shape: SH_BOXES, boxes: stairBoxes, tex: 'spruce_planks', render: 'opaque', hardness: 2, tool: 'axe', sound: 'wood', stairs: true, fuel: 300 });
defBlock(117, 'lit_furnace', '熔炉', { tex: { top: 'furnace_top', side: 'furnace_side', front: 'furnace_front_on' }, facing: true, light: 13, hardness: 3.5, tool: 'pickaxe', needsTool: true, onUse: 'furnace', noItem: true, drops: drop('furnace') });
// Bed: vanilla template_bed_head / template_bed_foot elements (head points north at rotation 0)
const BED_HEAD = [
  { from: [0, 3, 0], to: [16, 9, 16], faces: { down: [0, 0, 16, 16, 'bed_down'], up: [0, 0, 16, 16, 'red_bed_head_up'], north: [0, 7, 16, 13, 'bed_head_north'], west: [0, 7, 16, 13, 'red_bed_head_west'], east: [0, 7, 16, 13, 'red_bed_head_east'] } },
  { from: [0, 0, 0], to: [3, 3, 3], faces: { down: [6, 13, 9, 16, 'red_bed_head_west'], north: [13, 13, 16, 16, 'bed_head_north'], south: [3, 13, 6, 16, 'red_bed_head_west'], west: [0, 13, 3, 16, 'red_bed_head_west'], east: [10, 13, 13, 16, 'bed_head_north'] } },
  { from: [13, 0, 0], to: [16, 3, 3], faces: { down: [7, 13, 10, 16, 'red_bed_head_east'], north: [0, 13, 3, 16, 'bed_head_north'], south: [10, 13, 13, 16, 'red_bed_head_east'], west: [3, 13, 6, 16, 'bed_head_north'], east: [13, 13, 16, 16, 'red_bed_head_east'] } },
];
const BED_FOOT = [
  { from: [0, 3, 0], to: [16, 9, 16], faces: { down: [0, 0, 16, 16, 'bed_down'], up: [0, 0, 16, 16, 'red_bed_foot_up'], south: [0, 7, 16, 13, 'red_bed_foot_south'], west: [0, 7, 16, 13, 'red_bed_foot_west'], east: [0, 7, 16, 13, 'red_bed_foot_east'] } },
  { from: [0, 0, 13], to: [3, 3, 16], faces: { down: [7, 13, 10, 16, 'red_bed_foot_west'], north: [10, 13, 13, 16, 'red_bed_foot_west'], south: [0, 13, 3, 16, 'red_bed_foot_south'], west: [13, 13, 16, 16, 'red_bed_foot_west'], east: [3, 13, 6, 16, 'red_bed_foot_south'] } },
  { from: [13, 0, 13], to: [16, 3, 16], faces: { down: [6, 13, 9, 16, 'red_bed_foot_east'], north: [3, 13, 6, 16, 'red_bed_foot_east'], south: [13, 13, 16, 16, 'red_bed_foot_south'], west: [10, 13, 13, 16, 'red_bed_foot_south'], east: [0, 13, 3, 16, 'red_bed_foot_east'] } },
];
for (const els of [BED_HEAD, BED_FOOT]) for (const e of els) for (const f in e.faces) e.faces[f][4] = T(e.faces[f][4]);
// meta: bits0-1 rotation (0 head north, 1 east, 2 south, 3 west), bit2 = head half
defBlock(118, 'red_bed', '红色的床', { shape: SH_MODEL, model: (m) => ({ els: (m & 4) ? BED_HEAD : BED_FOOT, rot: m & 3 }), boxes: () => [[0, 0, 0, 16, 9, 16]],
  tex: 'red_bed_foot_up', render: 'opaque', hardness: 0.2, sound: 'wool', bed: true, stack: 1, particle: 'red_bed_head_up', drops: (m) => (m & 4) ? [] : [['red_bed', 1]] });
defBlock(116, 'birch_slab', '白桦木台阶', { shape: SH_BOXES, boxes: (m) => SLAB_BOXES[m & 3], tex: 'birch_planks', render: 'opaque', hardness: 2, tool: 'axe', sound: 'wood', slab: true, fuel: 150, drops: slabDrop('birch_slab') });

const WOOLS = [['white', '白色'], ['orange', '橙色'], ['magenta', '品红色'], ['light_blue', '淡蓝色'], ['yellow', '黄色'], ['lime', '黄绿色'], ['pink', '粉红色'], ['gray', '灰色'], ['light_gray', '淡灰色'], ['cyan', '青色'], ['purple', '紫色'], ['blue', '蓝色'], ['brown', '棕色'], ['green', '绿色'], ['red', '红色'], ['black', '黑色']];
WOOLS.forEach(([c, cn], i) => defBlock(128 + i, c + '_wool', cn + '羊毛', { tex: c + '_wool', hardness: 0.8, sound: 'wool', tool: 'shears', fuel: 100 }));
const SGLASS = [['white', '白色'], ['orange', '橙色'], ['light_blue', '淡蓝色'], ['yellow', '黄色'], ['lime', '黄绿色'], ['pink', '粉红色'], ['cyan', '青色'], ['purple', '紫色'], ['blue', '蓝色'], ['red', '红色'], ['black', '黑色'], ['green', '绿色']];
SGLASS.forEach(([c, cn], i) => defBlock(144 + i, c + '_stained_glass', cn + '染色玻璃', { tex: c + '_stained_glass', render: 'translucent', opaque: false, hardness: 0.3, sound: 'glass', drops: nothing, cullSame: true }));
const CONCRETE = [['white', '白色'], ['orange', '橙色'], ['yellow', '黄色'], ['lime', '黄绿色'], ['light_blue', '淡蓝色'], ['blue', '蓝色'], ['red', '红色'], ['black', '黑色'], ['gray', '灰色']];
CONCRETE.forEach(([c, cn], i) => defBlock(160 + i, c + '_concrete', cn + '混凝土', { tex: c + '_concrete', hardness: 1.8, tool: 'pickaxe', needsTool: true }));

// ---------------------------------------------------------------------------
// Items
// ---------------------------------------------------------------------------
let nextItem = 256;
function defItem(key, cn, o = {}) {
  const id = nextItem++;
  const it = Object.assign({ id, key, cn, stack: 64, tex: T('i_' + (o.texKey || key)) }, o);
  ITEMS[id] = it; ITEM_KEY[key] = id;
  return it;
}
const TOOL_MAT = { wooden: [0, 2, 59, '木'], stone: [1, 4, 131, '石'], iron: [2, 6, 250, '铁'], golden: [0, 12, 32, '金'], diamond: [3, 8, 1561, '钻石'] };
const TOOL_DMG = { sword: [4, 5, 6, 4, 7], axe: [7, 9, 9, 7, 9], pickaxe: [2, 3, 4, 2, 5], shovel: [2.5, 3.5, 4.5, 2.5, 5.5], hoe: [1, 1, 1, 1, 1] };
const TOOL_CN = { pickaxe: '镐', axe: '斧', shovel: '锹', sword: '剑', hoe: '锄' };

defItem('stick', '木棍', { fuel: 100 });
defItem('coal', '煤炭', { fuel: 1600 });
defItem('charcoal', '木炭', { fuel: 1600 });
defItem('raw_iron', '粗铁');
defItem('iron_ingot', '铁锭');
defItem('raw_gold', '粗金');
defItem('gold_ingot', '金锭');
defItem('diamond', '钻石');
defItem('emerald', '绿宝石');
defItem('lapis_lazuli', '青金石');
defItem('redstone', '红石粉');
defItem('flint', '燧石');
defItem('apple', '苹果', { food: [4, 2.4] });
defItem('bread', '面包', { food: [5, 6] });
defItem('wheat', '小麦');
defItem('wheat_seeds', '小麦种子', { places: 'wheat' });
defItem('porkchop', '生猪排', { food: [3, 1.8] });
defItem('cooked_porkchop', '熟猪排', { food: [8, 12.8] });
defItem('beef', '生牛肉', { food: [3, 1.8] });
defItem('cooked_beef', '牛排', { food: [8, 12.8] });
defItem('mutton', '生羊肉', { food: [2, 1.2] });
defItem('cooked_mutton', '熟羊肉', { food: [6, 9.6] });
defItem('chicken', '生鸡肉', { food: [2, 1.2] });
defItem('cooked_chicken', '熟鸡肉', { food: [6, 7.2] });
defItem('rotten_flesh', '腐肉', { food: [4, 0.8] });
defItem('melon_slice', '西瓜片', { food: [2, 1.2] });
defItem('cookie', '曲奇', { food: [2, 0.4] });
defItem('pumpkin_pie', '南瓜派', { food: [8, 4.8] });
defItem('golden_apple', '金苹果', { food: [4, 9.6], heal: 4 });
defItem('baked_potato', '烤马铃薯', { food: [5, 6] });
defItem('carrot', '胡萝卜', { food: [3, 3.6] });
defItem('bone', '骨头');
defItem('bone_meal', '骨粉');
defItem('arrow', '箭');
defItem('bow', '弓', { stack: 1, durability: 384 });
defItem('string', '线');
defItem('gunpowder', '火药');
defItem('feather', '羽毛');
defItem('leather', '皮革');
defItem('egg', '鸡蛋', { stack: 16 });
defItem('snowball', '雪球', { stack: 16 });
defItem('shears', '剪刀', { stack: 1, durability: 238 });
defItem('flint_and_steel', '打火石', { stack: 1, durability: 64 });
defItem('bucket', '桶', { stack: 16 });
defItem('water_bucket', '水桶', { stack: 1, places: 'water' });
defItem('lava_bucket', '熔岩桶', { stack: 1, places: 'lava', fuel: 20000 });
for (const mat of ['wooden', 'stone', 'iron', 'golden', 'diamond']) {
  const [level, speed, dur, cn] = TOOL_MAT[mat];
  const mi = ['wooden', 'stone', 'iron', 'golden', 'diamond'].indexOf(mat);
  for (const t of ['pickaxe', 'axe', 'shovel', 'sword', 'hoe']) {
    defItem(mat + '_' + t, cn + TOOL_CN[t], { stack: 1, durability: dur, tool: { type: t, level, speed }, attack: TOOL_DMG[t][mi], fuel: mat === 'wooden' ? 200 : 0 });
  }
}
defItem('oak_door', '橡木门', { places: 'oak_door', fuel: 200 });
defItem('sugar_cane', '甘蔗', { places: 'sugar_cane' });
defItem('sugar', '糖');
defItem('paper', '纸');
defItem('book', '书');
defItem('clay_ball', '黏土球');
defItem('brick', '红砖');
const ARMOR_PTS = { leather: [1, 3, 2, 1], iron: [2, 6, 5, 2], diamond: [3, 8, 6, 3] };
const ARMOR_DUR = { leather: 80, iron: 240, diamond: 528 };
const ARMOR_CN = { leather: '皮革', iron: '铁', diamond: '钻石' };
for (const mat of ['leather', 'iron', 'diamond']) {
  ['helmet', 'chestplate', 'leggings', 'boots'].forEach((p, i) => {
    defItem(mat + '_' + p, ARMOR_CN[mat] + ['头盔', '胸甲', '护腿', '靴子'][i], { stack: 1, durability: ARMOR_DUR[mat], armor: { slot: i, points: ARMOR_PTS[mat][i] } });
  });
}

for (const [m, cn] of [['pig', '猪'], ['cow', '牛'], ['sheep', '羊'], ['chicken', '鸡'], ['zombie', '僵尸'], ['skeleton', '骷髅'], ['creeper', '苦力怕'], ['spider', '蜘蛛']]) defItem(m + '_spawn_egg', cn + '刷怪蛋', { spawnEgg: m });

function item(key) { const id = ITEM_KEY[key]; if (id === undefined) throw new Error('no item ' + key); return id; }
function itemDef(id) { return ITEMS[id]; }
function makeStack(key, count = 1) { const id = typeof key === 'number' ? key : item(key); return { id, count, dmg: 0 }; }
function maxStack(id) { return ITEMS[id] ? ITEMS[id].stack : 64; }

// ---------------------------------------------------------------------------
// Recipes
// ---------------------------------------------------------------------------
const TAGS = {
  planks: ['oak_planks', 'birch_planks', 'spruce_planks', 'dark_oak_planks', 'jungle_planks', 'acacia_planks', 'cherry_planks'],
  coals: ['coal', 'charcoal'],
  stone_tool: ['cobblestone'],
  logs: ['oak_log', 'birch_log', 'spruce_log'],
  wool: ['white_wool', 'orange_wool', 'magenta_wool', 'light_blue_wool', 'yellow_wool', 'lime_wool', 'pink_wool', 'gray_wool', 'light_gray_wool', 'cyan_wool', 'purple_wool', 'blue_wool', 'brown_wool', 'green_wool', 'red_wool', 'black_wool'],
};
const RECIPES = [];
function shaped(result, count, pattern, keys) {
  const k = {};
  for (const c in keys) k[c] = (TAGS[keys[c]] || [keys[c]]).map(item);
  RECIPES.push({ type: 'shaped', result: item(result), count, pattern, keys: k, w: pattern[0].length, h: pattern.length });
}
function shapeless(result, count, ingredients) {
  RECIPES.push({ type: 'shapeless', result: item(result), count, ing: ingredients.map(i => (TAGS[i] || [i]).map(item)) });
}
shapeless('oak_planks', 4, ['oak_log']);
shapeless('birch_planks', 4, ['birch_log']);
shapeless('spruce_planks', 4, ['spruce_log']);
shaped('stick', 4, ['#', '#'], { '#': 'planks' });
shaped('crafting_table', 1, ['##', '##'], { '#': 'planks' });
shaped('furnace', 1, ['###', '# #', '###'], { '#': 'cobblestone' });
shaped('chest', 1, ['###', '# #', '###'], { '#': 'planks' });
shaped('torch', 4, ['C', '#'], { C: 'coals', '#': 'stick' });
const TOOL_KEYS = { wooden: 'planks', stone: 'cobblestone', iron: 'iron_ingot', golden: 'gold_ingot', diamond: 'diamond' };
for (const m in TOOL_KEYS) {
  const X = TOOL_KEYS[m];
  shaped(m + '_pickaxe', 1, ['XXX', ' # ', ' # '], { X, '#': 'stick' });
  shaped(m + '_axe', 1, ['XX', 'X#', ' #'], { X, '#': 'stick' });
  shaped(m + '_shovel', 1, ['X', '#', '#'], { X, '#': 'stick' });
  shaped(m + '_sword', 1, ['X', 'X', '#'], { X, '#': 'stick' });
  shaped(m + '_hoe', 1, ['XX', ' #', ' #'], { X, '#': 'stick' });
}
for (const [m, X] of [['leather', 'leather'], ['iron', 'iron_ingot'], ['diamond', 'diamond']]) {
  shaped(m + '_helmet', 1, ['XXX', 'X X'], { X });
  shaped(m + '_chestplate', 1, ['X X', 'XXX', 'XXX'], { X });
  shaped(m + '_leggings', 1, ['XXX', 'X X', 'X X'], { X });
  shaped(m + '_boots', 1, ['X X', 'X X'], { X });
}
shaped('bow', 1, [' #S', '# S', ' #S'], { '#': 'stick', S: 'string' });
shaped('arrow', 4, ['F', '#', 'E'], { F: 'flint', '#': 'stick', E: 'feather' });
shaped('bucket', 1, ['X X', ' X '], { X: 'iron_ingot' });
shaped('shears', 1, [' X', 'X '], { X: 'iron_ingot' });
shapeless('flint_and_steel', 1, ['iron_ingot', 'flint']);
for (const [b, i] of [['iron_block', 'iron_ingot'], ['gold_block', 'gold_ingot'], ['diamond_block', 'diamond'], ['coal_block', 'coal'], ['emerald_block', 'emerald'], ['lapis_block', 'lapis_lazuli'], ['redstone_block', 'redstone'], ['hay_block', 'wheat']]) {
  shaped(b, 1, ['XXX', 'XXX', 'XXX'], { X: i });
  shapeless(i, 9, [b]);
}
shaped('melon', 1, ['XXX', 'XXX', 'XXX'], { X: 'melon_slice' });
shaped('tnt', 1, ['#S#', 'S#S', '#S#'], { '#': 'gunpowder', S: 'sand' });
shaped('bread', 1, ['WWW'], { W: 'wheat' });
shapeless('sugar', 1, ['sugar_cane']);
shaped('paper', 3, ['###'], { '#': 'sugar_cane' });
shapeless('book', 1, ['paper', 'paper', 'paper', 'leather']);
shaped('bookshelf', 1, ['###', 'BBB', '###'], { '#': 'planks', B: 'book' });
shaped('bricks', 1, ['##', '##'], { '#': 'brick' });
shaped('clay', 1, ['##', '##'], { '#': 'clay_ball' });
shaped('stone_bricks', 4, ['##', '##'], { '#': 'stone' });
shaped('sandstone', 1, ['##', '##'], { '#': 'sand' });
shaped('snow_block', 1, ['##', '##'], { '#': 'snowball' });
shaped('white_wool', 1, ['##', '##'], { '#': 'string' });
shaped('polished_andesite', 4, ['##', '##'], { '#': 'andesite' });
shaped('polished_diorite', 4, ['##', '##'], { '#': 'diorite' });
shaped('polished_granite', 4, ['##', '##'], { '#': 'granite' });
shaped('smooth_stone_slab', 6, ['###'], { '#': 'smooth_stone' });
shaped('oak_slab', 6, ['###'], { '#': 'oak_planks' });
shaped('birch_slab', 6, ['###'], { '#': 'birch_planks' });
shaped('cobblestone_slab', 6, ['###'], { '#': 'cobblestone' });
shaped('stone_brick_slab', 6, ['###'], { '#': 'stone_bricks' });
shaped('oak_stairs', 4, ['#  ', '## ', '###'], { '#': 'oak_planks' });
shaped('spruce_stairs', 4, ['#  ', '## ', '###'], { '#': 'spruce_planks' });
shaped('cobblestone_stairs', 4, ['#  ', '## ', '###'], { '#': 'cobblestone' });
shaped('stone_brick_stairs', 4, ['#  ', '## ', '###'], { '#': 'stone_bricks' });
shaped('brick_stairs', 4, ['#  ', '## ', '###'], { '#': 'bricks' });
shaped('oak_door', 3, ['##', '##', '##'], { '#': 'planks' });
shaped('oak_trapdoor', 2, ['###', '###'], { '#': 'planks' });
shaped('ladder', 3, ['# #', '###', '# #'], { '#': 'stick' });
shaped('red_bed', 1, ['WWW', 'PPP'], { W: 'wool', P: 'planks' });
shapeless('jack_o_lantern', 1, ['pumpkin', 'torch']);
shapeless('bone_meal', 3, ['bone']);
shaped('golden_apple', 1, ['GGG', 'GAG', 'GGG'], { G: 'gold_ingot', A: 'apple' });
shapeless('pumpkin_pie', 1, ['pumpkin', 'sugar', 'egg']);
shaped('mossy_cobblestone', 1, ['#M'], { '#': 'cobblestone', M: 'moss_block' });
shaped('glowstone', 1, ['RG', 'GR'], { R: 'redstone', G: 'glass' });
shaped('sea_lantern', 1, ['LGL', 'GGG', 'LGL'], { L: 'lapis_lazuli', G: 'glass' });
shaped('note_block', 1, ['###', '#R#', '###'], { '#': 'planks', R: 'redstone' });
shaped('quartz_block', 1, ['##', '##'], { '#': 'diorite' });

// Match a crafting grid (array of stacks|null, width w) against recipes
function matchRecipe(grid, w) {
  const h = grid.length / w;
  // find bounding box
  let minX = w, minY = h, maxX = -1, maxY = -1, n = 0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (grid[y * w + x]) { n++; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
  if (!n) return null;
  const bw = maxX - minX + 1, bh = maxY - minY + 1;
  for (const r of RECIPES) {
    if (r.type === 'shaped') {
      if (r.w !== bw || r.h !== bh) continue;
      for (const mirror of [false, true]) {
        let ok = true;
        for (let y = 0; y < bh && ok; y++) for (let x = 0; x < bw && ok; x++) {
          const c = r.pattern[y][mirror ? bw - 1 - x : x];
          const s = grid[(minY + y) * w + minX + x];
          if (c === ' ' || c === undefined) { if (s) ok = false; }
          else if (!s || !r.keys[c].includes(s.id)) ok = false;
        }
        if (ok) return r;
      }
    } else {
      if (r.ing.length !== n) continue;
      const used = new Array(r.ing.length).fill(false);
      let ok = true;
      for (const s of grid) {
        if (!s) continue;
        let found = false;
        for (let i = 0; i < r.ing.length; i++) if (!used[i] && r.ing[i].includes(s.id)) { used[i] = true; found = true; break; }
        if (!found) { ok = false; break; }
      }
      if (ok) return r;
    }
  }
  return null;
}

const SMELTING = {};
function smelt(a, b, xp = 0.1) { SMELTING[item(a)] = { result: item(b), xp }; }
smelt('raw_iron', 'iron_ingot', 0.7); smelt('raw_gold', 'gold_ingot', 1); smelt('sand', 'glass'); smelt('red_sand', 'glass');
smelt('cobblestone', 'stone'); smelt('stone', 'smooth_stone'); smelt('clay_ball', 'brick', 0.3); smelt('clay', 'terracotta', 0.35);
smelt('oak_log', 'charcoal', 0.15); smelt('birch_log', 'charcoal', 0.15); smelt('spruce_log', 'charcoal', 0.15);
smelt('porkchop', 'cooked_porkchop', 0.35); smelt('beef', 'cooked_beef', 0.35); smelt('mutton', 'cooked_mutton', 0.35); smelt('chicken', 'cooked_chicken', 0.35);
smelt('stone_bricks', 'cracked_stone_bricks'); smelt('cactus', 'green_concrete' in ITEM_KEY ? 'green_concrete' : 'lime_concrete');
smelt('carrot', 'baked_potato', 0.35);

function fuelValue(id) { const d = ITEMS[id]; return d ? (d.fuel || (BLOCKS[id] && BLOCKS[id].fuel) || 0) : 0; }

// Creative inventory ordering
const CREATIVE_TABS = [
  { name: '建筑方块', icon: 'bricks', items: ['stone', 'cobblestone', 'mossy_cobblestone', 'smooth_stone', 'stone_bricks', 'mossy_stone_bricks', 'cracked_stone_bricks', 'andesite', 'polished_andesite', 'diorite', 'polished_diorite', 'granite', 'polished_granite', 'deepslate',
    'oak_planks', 'birch_planks', 'spruce_planks', 'dark_oak_planks', 'jungle_planks', 'acacia_planks', 'cherry_planks', 'oak_log', 'birch_log', 'spruce_log',
    'bricks', 'mud_bricks', 'sandstone', 'terracotta', 'quartz_block', 'prismarine', 'sea_lantern', 'glowstone', 'obsidian', 'bone_block',
    'smooth_stone_slab', 'oak_slab', 'birch_slab', 'cobblestone_slab', 'stone_brick_slab', 'oak_stairs', 'spruce_stairs', 'cobblestone_stairs', 'stone_brick_stairs', 'brick_stairs',
    'glass', ...SGLASS.map(s => s[0] + '_stained_glass'), ...WOOLS.map(w => w[0] + '_wool'), ...CONCRETE.map(c => c[0] + '_concrete'),
    'iron_block', 'gold_block', 'diamond_block', 'emerald_block', 'lapis_block', 'redstone_block', 'coal_block', 'hay_block'] },
  { name: '自然方块', icon: 'grass_block', items: ['grass_block', 'dirt', 'farmland', 'sand', 'red_sand', 'gravel', 'clay', 'snow_block', 'snow', 'ice', 'packed_ice', 'moss_block', 'magma_block',
    'coal_ore', 'iron_ore', 'gold_ore', 'diamond_ore', 'redstone_ore', 'lapis_ore', 'emerald_ore', 'deepslate_coal_ore', 'deepslate_iron_ore', 'deepslate_gold_ore', 'deepslate_diamond_ore',
    'oak_leaves', 'birch_leaves', 'spruce_leaves', 'oak_sapling', 'birch_sapling', 'spruce_sapling', 'short_grass', 'fern', 'dead_bush', ...FLOWERS.map(f => f[0]),
    'red_mushroom', 'brown_mushroom', 'cactus', 'sugar_cane', 'pumpkin', 'melon', 'bedrock'] },
  { name: '功能方块', icon: 'crafting_table', items: ['crafting_table', 'furnace', 'chest', 'red_bed', 'torch', 'jack_o_lantern', 'bookshelf', 'ladder', 'oak_door', 'oak_trapdoor', 'tnt', 'note_block'] },
  { name: '工具与战斗', icon: 'diamond_pickaxe', items: [] },
  { name: '食物与材料', icon: 'apple', items: [] },
  { name: '刷怪蛋', icon: 'creeper_spawn_egg', items: ['pig_spawn_egg', 'cow_spawn_egg', 'sheep_spawn_egg', 'chicken_spawn_egg', 'zombie_spawn_egg', 'skeleton_spawn_egg', 'creeper_spawn_egg', 'spider_spawn_egg'] },
];
for (const m of ['wooden', 'stone', 'iron', 'golden', 'diamond']) for (const t of ['pickaxe', 'axe', 'shovel', 'hoe', 'sword']) CREATIVE_TABS[3].items.push(m + '_' + t);
CREATIVE_TABS[3].items.push('bow', 'arrow', 'shears', 'flint_and_steel', 'bucket', 'water_bucket', 'lava_bucket');
for (const m of ['leather', 'iron', 'diamond']) for (const p of ['helmet', 'chestplate', 'leggings', 'boots']) CREATIVE_TABS[3].items.push(m + '_' + p);
CREATIVE_TABS[4].items.push('apple', 'golden_apple', 'bread', 'porkchop', 'cooked_porkchop', 'beef', 'cooked_beef', 'mutton', 'cooked_mutton', 'chicken', 'cooked_chicken', 'rotten_flesh', 'melon_slice', 'cookie', 'pumpkin_pie', 'baked_potato', 'carrot',
  'wheat_seeds', 'wheat', 'egg', 'sugar', 'stick', 'coal', 'charcoal', 'raw_iron', 'iron_ingot', 'raw_gold', 'gold_ingot', 'diamond', 'emerald', 'lapis_lazuli', 'redstone', 'flint', 'bone', 'bone_meal', 'string', 'feather', 'gunpowder', 'leather', 'snowball', 'clay_ball', 'brick', 'paper', 'book');
