import Phaser from 'phaser';

import type { OasisTimelineEvent, OasisTopicDetail, OasisTopicPost } from '../types';
import { TOWN_MAP, type TownArea } from '../vendor/agent-town-game/maps/town-map';
import { TownRenderer } from '../vendor/agent-town-game/rendering/TownRenderer';
import { AgentSprite } from '../vendor/agent-town-game/sprites/AgentSprite';
import { PetSprite } from '../vendor/agent-town-game/sprites/PetSprite';
import { PathfindingManager } from '../vendor/agent-town-game/pathfinding/PathfindingManager';
import { AgentMovementController } from '../vendor/agent-town-game/pathfinding/AgentMovementController';
import { TILE_SIZE } from '../vendor/agent-town-game/tiles/tileset-generator';
import { TouchInputManager } from '../vendor/agent-town-game/systems/TouchInputManager';
import { GameTimeSystem, type TimeSpeed } from '../vendor/agent-town-game/systems/GameTimeSystem';
import { ScheduleSystem } from '../vendor/agent-town-game/systems/ScheduleSystem';
import { DayNightCycle } from '../vendor/agent-town-game/systems/DayNightCycle';
import { EnvironmentSystem } from '../vendor/agent-town-game/systems/EnvironmentSystem';
import { generateRandomWeather, getWeatherIcon } from '../vendor/agent-town-game/systems/WeatherService';

type PlaybackEvent =
  | { key: string; kind: 'post'; elapsed: number; post: OasisTopicPost }
  | { key: string; kind: 'timeline'; elapsed: number; timeline: OasisTimelineEvent };

type ResidentWorkState = 'working' | 'thinking' | 'idle' | 'error';

interface ResidentMetrics {
  posts: number;
  upvotes: number;
  downvotes: number;
  replies: number;
  mentions: number;
}

interface ResidentEntity {
  name: string;
  sprite: AgentSprite;
  controller: AgentMovementController;
  area: TownArea;
  homeArea: TownArea;
  defaultTile: { x: number; y: number };
  metrics: ResidentMetrics;
}

const PIXEL_FONT_STACK = '"Press Start 2P", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", monospace';
const MIN_CAMERA_ZOOM = 0.18;
const MAX_CAMERA_ZOOM = 3.2;

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = ((hash << 5) - hash) + value.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function stripMarkdown(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_>#~-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function truncateBubbleText(text: string, maxLength = 90): string {
  const clean = stripMarkdown(text);
  if (clean.length <= maxLength) {
    return clean || '...';
  }
  return `${clean.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
}

function isWideCharacter(char: string): boolean {
  const code = char.codePointAt(0) || 0;
  return (
    (code >= 0x1100 && code <= 0x11ff) ||
    (code >= 0x2e80 && code <= 0xa4cf) ||
    (code >= 0xac00 && code <= 0xd7af) ||
    (code >= 0xf900 && code <= 0xfaff) ||
    (code >= 0xfe10 && code <= 0xfe6f) ||
    (code >= 0xff00 && code <= 0xffef)
  );
}

function measureVisualWidth(text: string): number {
  return [...text].reduce((sum, char) => sum + (isWideCharacter(char) ? 2 : 1), 0);
}

function formatElapsedLabel(seconds: number): string {
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) {
    return `T+${rounded}s`;
  }
  const minutes = Math.floor(rounded / 60);
  const remain = rounded % 60;
  return `T+${minutes}m${remain.toString().padStart(2, '0')}s`;
}

function buildResidentMetrics(detail: OasisTopicDetail): Map<string, ResidentMetrics> {
  const metrics = new Map<string, ResidentMetrics>();
  const ensure = (name: string) => {
    if (!metrics.has(name)) {
      metrics.set(name, { posts: 0, upvotes: 0, downvotes: 0, replies: 0, mentions: 0 });
    }
    return metrics.get(name)!;
  };

  const postById = new Map<number, OasisTopicPost>();
  detail.posts.forEach((post) => {
    postById.set(post.id, post);
  });

  detail.posts.forEach((post) => {
    const stat = ensure(post.author);
    stat.posts += 1;
    stat.upvotes += post.upvotes;
    stat.downvotes += post.downvotes;
    if (post.reply_to) {
      stat.replies += 1;
      const target = postById.get(post.reply_to);
      if (target) {
        ensure(target.author).mentions += 1;
      }
    }
  });

  detail.timeline.forEach((entry) => {
    if (entry.agent) {
      ensure(entry.agent).mentions += 1;
    }
  });

  return metrics;
}

function dedupeParticipants(detail: OasisTopicDetail): string[] {
  const set = new Set<string>();
  detail.timeline.forEach((entry) => {
    if (entry.agent) set.add(entry.agent);
  });
  detail.posts.forEach((post) => {
    if (post.author) set.add(post.author);
  });
  return [...set];
}

function areaDisplayName(area: TownArea): string {
  return TOWN_MAP.areas[area].name;
}

function pickHomeArea(name: string): TownArea {
  const lower = name.toLowerCase();
  if (lower.includes('creative') || lower.includes('design') || lower.includes('artist')) {
    return 'coffeeShop';
  }
  if (lower.includes('data') || lower.includes('engineer') || lower.includes('dev') || lower.includes('research')) {
    return 'office';
  }
  if (lower.includes('critic') || lower.includes('review') || lower.includes('legal')) {
    return 'store';
  }
  if (lower.includes('lead') || lower.includes('synth') || lower.includes('summary')) {
    return 'plaza';
  }
  const areas: TownArea[] = ['office', 'park', 'plaza', 'coffeeShop', 'store', 'residential'];
  return areas[hashString(name) % areas.length];
}

function getAreaSlots(area: TownArea): Array<{ x: number; y: number }> {
  switch (area) {
    case 'office':
      return TOWN_MAP.locations.workstations.map((slot) => ({ x: slot.x, y: slot.y + 1 }));
    case 'park':
      return TOWN_MAP.locations.parkBenches.map((slot) => ({ x: slot.x, y: slot.y + 1 }));
    case 'plaza':
      return TOWN_MAP.locations.plazaBenches.map((slot) => ({ x: slot.x, y: slot.y + 1 }));
    case 'coffeeShop':
      return TOWN_MAP.locations.cafeTables.map((slot) => ({ x: slot.x, y: slot.y + 1 }));
    case 'residential':
      return TOWN_MAP.locations.homes.map((slot) => ({ x: slot.x + 1, y: slot.y }));
    case 'store': {
      const areaBox = TOWN_MAP.areas.store;
      return Array.from({ length: 10 }, (_, index) => ({
        x: areaBox.x + 2 + (index % 4),
        y: areaBox.y + 3 + Math.floor(index / 4) * 2,
      }));
    }
    default:
      return [{ x: 18, y: 18 }];
  }
}

export class OasisTownScene extends Phaser.Scene {
  private currentDetail: OasisTopicDetail | null;
  private pathfinder!: PathfindingManager;
  private touchInput!: TouchInputManager;
  private gameTime!: GameTimeSystem;
  private scheduleSystem!: ScheduleSystem;
  private dayNightCycle!: DayNightCycle;
  private environmentSystem!: EnvironmentSystem;
  private residents = new Map<string, ResidentEntity>();
  private processedKeys = new Set<string>();
  private queuedKeys = new Set<string>();
  private eventQueue: PlaybackEvent[] = [];
  private postIndex = new Map<number, OasisTopicPost>();
  private nextEventTimer: Phaser.Time.TimerEvent | null = null;
  private activeWorldBubbles: Phaser.GameObjects.Container[] = [];
  private activeEffects: Phaser.GameObjects.Container[] = [];
  private hudTitle!: Phaser.GameObjects.Text;
  private hudStatus!: Phaser.GameObjects.Text;
  private hudWeather!: Phaser.GameObjects.Text;
  private hudClock!: Phaser.GameObjects.Text;
  private hudSpeed!: Phaser.GameObjects.Text;
  private hudArea!: Phaser.GameObjects.Text;
  private minimapContainer!: Phaser.GameObjects.Container;
  private minimapGraphics!: Phaser.GameObjects.Graphics;
  private minimapViewport!: Phaser.GameObjects.Graphics;
  private minimapResidentDots = new Map<string, Phaser.GameObjects.Graphics>();
  private pets: PetSprite[] = [];
  private detailPanel: Phaser.GameObjects.Container | null = null;
  private selectedResidentName: string | null = null;
  private highlightGraphics: Phaser.GameObjects.Graphics | null = null;
  private highlightTween: Phaser.Tweens.Tween | null = null;
  private currentArea: TownArea = 'plaza';
  private currentWeather = 'sunny';
  private weatherTimer: number | null = null;
  private mapWidth = 0;
  private mapHeight = 0;
  private lastElapsed = 0;

  constructor(detail?: OasisTopicDetail) {
    super({ key: 'OasisTownScene' });
    this.currentDetail = detail ?? null;
  }

  create(): void {
    const { width, height, layers } = TOWN_MAP;
    this.mapWidth = width * TILE_SIZE;
    this.mapHeight = height * TILE_SIZE;

    this.pathfinder = new PathfindingManager();
    this.pathfinder.setGrid(layers.collision);

    const renderer = new TownRenderer(this, TOWN_MAP);
    renderer.renderAll();

    const now = new Date();
    this.gameTime = new GameTimeSystem(this, {
      startHour: now.getHours(),
      startMinute: now.getMinutes(),
      speed: 10,
    });
    this.gameTime.start();

    this.dayNightCycle = new DayNightCycle(this, { cycleDurationMs: 180000, startTime: 'day' });
    this.dayNightCycle.create(this.mapWidth, this.mapHeight);
    this.syncDayNightToGameClock();

    this.scheduleSystem = new ScheduleSystem(this, this.gameTime, this.pathfinder);
    this.scheduleSystem.onLocationChange((agentId, location, activity) => {
      const resident = this.residents.get(agentId);
      if (!resident) return;
      resident.area = location;
      resident.sprite.setLocation(areaDisplayName(location));
      resident.sprite.setActivity(activity);
    });
    this.gameTime.onTimeChange(() => {
      this.syncDayNightToGameClock();
      this.hudClock?.setText(this.gameTime.getTimeString());
    });

    const weather = generateRandomWeather();
    this.currentWeather = weather.condition;
    this.environmentSystem = new EnvironmentSystem(this, this.mapWidth, this.mapHeight, {
      weather: weather.condition,
      holiday: 'none',
      particleCount: 50,
    });
    this.environmentSystem.create();
    this.environmentSystem.onWeatherChange((nextWeather) => {
      this.currentWeather = nextWeather;
      this.hudWeather?.setText(getWeatherIcon(nextWeather));
    });

    this.touchInput = new TouchInputManager(this, { minZoom: MIN_CAMERA_ZOOM, maxZoom: MAX_CAMERA_ZOOM });
    this.touchInput.create();

    this.cameras.main.setBounds(0, 0, this.mapWidth, this.mapHeight);
    this.cameras.main.setBackgroundColor('#4a7c59');
    this.fitMapToScreen();
    this.scale.on('resize', this.handleResize, this);

    this.createHud(weather.icon);
    this.createDesktopCameraControls();
    this.registerKeyboardShortcuts();
    this.createMinimap();
    this.spawnPets();
    this.startWeatherCycle();

    if (this.currentDetail) {
      this.applyTopicDetail(this.currentDetail, true);
    }
  }

  update(_time?: number, delta?: number): void {
    this.gameTime.update();
    this.environmentSystem.update();
    this.scheduleSystem.update();
    this.hudClock.setText(this.gameTime.getTimeString());
    this.residents.forEach((resident) => resident.sprite.updateDepth());
    this.pets.forEach((pet) => pet.update(_time || 0, delta || this.game.loop.delta));
    this.checkPetInteractions();
    this.updateMinimapViewport();
    this.updateMinimapResidents();
    this.refreshSelectedResidentPanel();
  }

  destroy(): void {
    this.clearPlayback();
    this.clearBubbles();
    this.clearEffects();
    this.scale.off('resize', this.handleResize, this);
    if (this.weatherTimer !== null) {
      window.clearTimeout(this.weatherTimer);
      this.weatherTimer = null;
    }
    this.hideResidentDetailPanel(true);
    this.clearHighlight();
    this.minimapResidentDots.forEach((dot) => dot.destroy());
    this.minimapResidentDots.clear();
    this.minimapContainer?.destroy();
    this.pets.forEach((pet) => pet.destroy());
    this.pets = [];
    this.residents.forEach((resident) => {
      this.scheduleSystem?.unregisterAgent(resident.name);
      resident.controller.destroy();
      resident.sprite.destroy();
    });
    this.residents.clear();
    this.gameTime?.destroy();
    this.scheduleSystem?.destroy();
    this.dayNightCycle?.destroy();
    this.environmentSystem?.destroy();
    super.destroy();
  }

  setTopicDetail(detail: OasisTopicDetail): void {
    const reset = !this.currentDetail || this.currentDetail.topic_id !== detail.topic_id;
    this.applyTopicDetail(detail, reset);
  }

  private applyTopicDetail(detail: OasisTopicDetail, reset: boolean): void {
    const previousPosts = reset
      ? new Map<number, OasisTopicPost>()
      : new Map<number, OasisTopicPost>((this.currentDetail?.posts || []).map((post) => [post.id, post]));
    this.currentDetail = detail;

    if (reset) {
      this.resetSceneForTopic();
    }

    this.postIndex = new Map(detail.posts.map((post) => [post.id, post]));
    this.syncResidents(detail);
    this.updateHud(detail);
    this.enqueueEvents(detail);
    this.showVoteDeltaEffects(previousPosts, detail.posts);
    if (this.selectedResidentName && this.residents.has(this.selectedResidentName)) {
      this.showResidentDetailPanel(this.selectedResidentName);
    }
    this.startPlaybackLoop();
  }

  private resetSceneForTopic(): void {
    this.clearPlayback();
    this.clearBubbles();
    this.clearEffects();
    this.hideResidentDetailPanel(true);
    this.clearHighlight();
    this.processedKeys.clear();
    this.queuedKeys.clear();
    this.eventQueue = [];
    this.lastElapsed = 0;

    this.residents.forEach((resident) => {
      this.scheduleSystem?.unregisterAgent(resident.name);
      resident.controller.destroy();
      resident.sprite.destroy();
    });
    this.residents.clear();
  }

  private clearPlayback(): void {
    if (this.nextEventTimer) {
      this.nextEventTimer.destroy();
      this.nextEventTimer = null;
    }
  }

  private clearBubbles(): void {
    this.activeWorldBubbles.forEach((bubble) => bubble.destroy());
    this.activeWorldBubbles = [];
  }

  private clearEffects(): void {
    this.activeEffects.forEach((effect) => effect.destroy());
    this.activeEffects = [];
  }

  private createHud(initialWeatherIcon: string): void {
    this.hudTitle = this.add.text(12, 12, 'OASIS TOWN', {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '10px',
      color: '#fff1e8',
      stroke: '#000000',
      strokeThickness: 3,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.72)',
    }).setDepth(1001).setScrollFactor(0);

    this.hudStatus = this.add.text(12, 34, 'Waiting for topic...', {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '8px',
      color: '#c2c3c7',
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.62)',
    }).setDepth(1001).setScrollFactor(0);

    this.hudWeather = this.add.text(12, 58, initialWeatherIcon, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '10px',
      color: '#fff1e8',
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.62)',
    }).setDepth(1001).setScrollFactor(0);

    this.hudClock = this.add.text(48, 58, this.gameTime.getTimeString(), {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '8px',
      color: '#fff1e8',
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.62)',
    }).setDepth(1001).setScrollFactor(0);

    this.hudSpeed = this.add.text(114, 58, `${this.gameTime.getSpeed()}x`, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '8px',
      color: '#ffe085',
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.62)',
    }).setDepth(1001).setScrollFactor(0);
    this.hudSpeed.setInteractive({ useHandCursor: true });
    this.hudSpeed.on('pointerdown', () => this.cycleTimeSpeed());

    this.hudArea = this.add.text(12, 82, '[1-6] areas · [0] fit', {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color: '#d6e8ff',
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
      padding: { x: 6, y: 4 },
      backgroundColor: 'rgba(29,43,83,0.56)',
    }).setDepth(1001).setScrollFactor(0);

    this.environmentSystem.onWeatherChange((weather) => {
      this.hudWeather.setText(getWeatherIcon(weather));
    });
  }

  private updateHud(detail: OasisTopicDetail): void {
    this.hudTitle.setText((detail.question || 'OASIS TOWN').slice(0, 40));
    const summary = `${detail.status.toUpperCase()}  P${detail.posts.length}  R${detail.current_round}/${detail.max_rounds}`;
    this.hudStatus.setText(summary);
  }

  private createDesktopCameraControls(): void {
    let isDragging = false;
    let startX = 0;
    let startY = 0;

    this.input.on('pointerdown', (pointer: Phaser.Input.Pointer) => {
      if (pointer.rightButtonDown()) return;
      isDragging = true;
      startX = pointer.x;
      startY = pointer.y;
    });

    this.input.on('pointermove', (pointer: Phaser.Input.Pointer) => {
      if (!isDragging || !pointer.isDown) return;
      const dx = startX - pointer.x;
      const dy = startY - pointer.y;
      const cam = this.cameras.main;
      cam.scrollX += dx / cam.zoom;
      cam.scrollY += dy / cam.zoom;
      startX = pointer.x;
      startY = pointer.y;
    });

    this.input.on('pointerup', () => {
      isDragging = false;
    });
  }

  private fitMapToScreen(): void {
    const cam = this.cameras.main;
    const zoomX = cam.width / this.mapWidth;
    const zoomY = cam.height / this.mapHeight;
    const zoom = Phaser.Math.Clamp(Math.min(zoomX, zoomY), MIN_CAMERA_ZOOM, MAX_CAMERA_ZOOM);
    cam.setZoom(zoom);
    cam.centerOn(this.mapWidth / 2, this.mapHeight / 2);
    this.currentArea = 'plaza';
    if (this.hudArea) {
      this.hudArea.setText(`AREA ${areaDisplayName(this.currentArea).toUpperCase()} · [1-6] [0]`);
    }
  }

  private handleResize(gameSize: Phaser.Structs.Size): void {
    this.cameras.main.setViewport(0, 0, gameSize.width, gameSize.height);
    this.fitMapToScreen();
    this.positionMinimap();
    this.refreshSelectedResidentPanel();
  }

  private syncDayNightToGameClock(): void {
    const hour = this.gameTime.getHour();
    if (hour >= 6 && hour < 8) {
      this.dayNightCycle.setTime('dawn');
    } else if (hour >= 8 && hour < 18) {
      this.dayNightCycle.setTime('day');
    } else if (hour >= 18 && hour < 20) {
      this.dayNightCycle.setTime('dusk');
    } else {
      this.dayNightCycle.setTime('night');
    }
  }

  private cycleTimeSpeed(): void {
    const speeds: TimeSpeed[] = [1, 10, 60];
    const index = speeds.indexOf(this.gameTime.getSpeed());
    this.setTimeSpeed(speeds[(index + 1) % speeds.length]);
  }

  private setTimeSpeed(speed: TimeSpeed): void {
    this.gameTime.setSpeed(speed);
    this.hudSpeed.setText(`${speed}x`);
  }

  private shouldIgnoreHotkeys(): boolean {
    const active = document.activeElement as HTMLElement | null;
    if (!active) return false;
    const tag = active.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || active.isContentEditable;
  }

  private registerKeyboardShortcuts(): void {
    const keyboard = this.input.keyboard;
    if (!keyboard) return;
    keyboard.on('keydown-ONE', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('office'); });
    keyboard.on('keydown-TWO', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('park'); });
    keyboard.on('keydown-THREE', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('plaza'); });
    keyboard.on('keydown-FOUR', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('coffeeShop'); });
    keyboard.on('keydown-FIVE', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('store'); });
    keyboard.on('keydown-SIX', () => { if (!this.shouldIgnoreHotkeys()) this.navigateToArea('residential'); });
    keyboard.on('keydown-COMMA', () => { if (!this.shouldIgnoreHotkeys()) this.setTimeSpeed(1); });
    keyboard.on('keydown-PERIOD', () => { if (!this.shouldIgnoreHotkeys()) this.setTimeSpeed(10); });
    keyboard.on('keydown-FORWARD_SLASH', () => { if (!this.shouldIgnoreHotkeys()) this.setTimeSpeed(60); });
    keyboard.on('keydown-ZERO', () => { if (!this.shouldIgnoreHotkeys()) this.fitMapToScreen(); });
  }

  private navigateToArea(area: TownArea): void {
    const target = TOWN_MAP.areas[area];
    if (!target) return;
    this.currentArea = area;
    this.hudArea.setText(`AREA ${target.name.toUpperCase()} · [1-6] [0]`);
    this.cameras.main.pan(
      (target.x + target.width / 2) * TILE_SIZE,
      (target.y + target.height / 2) * TILE_SIZE,
      500,
      'Power2',
    );
    this.showSkyBanner(`AREA · ${target.name}`, 0xd6e8ff);
  }

  private createMinimap(): void {
    this.minimapContainer = this.add.container(0, 0);
    this.minimapContainer.setScrollFactor(0);
    this.minimapContainer.setDepth(1001);

    const background = this.add.graphics();
    background.fillStyle(0x000000, 0.65);
    background.fillRoundedRect(0, 0, 120, 90, 4);
    background.lineStyle(1, 0xffffff, 0.28);
    background.strokeRoundedRect(0, 0, 120, 90, 4);
    this.minimapContainer.add(background);

    this.minimapGraphics = this.add.graphics();
    this.minimapContainer.add(this.minimapGraphics);
    this.drawMinimapTerrain(120, 90);

    this.minimapViewport = this.add.graphics();
    this.minimapContainer.add(this.minimapViewport);

    const label = this.add.text(60, 6, 'MAP', {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '6px',
      color: '#ffffff',
      resolution: 2,
    }).setOrigin(0.5, 0);
    label.setAlpha(0.6);
    this.minimapContainer.add(label);
    this.positionMinimap();
  }

  private positionMinimap(): void {
    if (!this.minimapContainer) return;
    this.minimapContainer.setPosition(this.cameras.main.width - 130, this.cameras.main.height - 100);
  }

  private drawMinimapTerrain(width: number, height: number): void {
    const scaleX = width / this.mapWidth;
    const scaleY = height / this.mapHeight;
    const areaColors: Record<TownArea, number> = {
      office: 0x8898b0,
      park: 0x7ab87a,
      plaza: 0xc8b898,
      coffeeShop: 0xb8885a,
      store: 0xe0dcd0,
      residential: 0x9a8a70,
    };

    Object.entries(TOWN_MAP.areas).forEach(([key, area]) => {
      this.minimapGraphics.fillStyle(areaColors[key as TownArea] ?? 0x888888, 0.7);
      this.minimapGraphics.fillRect(
        area.x * TILE_SIZE * scaleX,
        area.y * TILE_SIZE * scaleY,
        area.width * TILE_SIZE * scaleX,
        area.height * TILE_SIZE * scaleY,
      );
    });

    this.minimapGraphics.fillStyle(0x8a8890, 0.82);
    this.minimapGraphics.fillRect(2 * TILE_SIZE * scaleX, 13 * TILE_SIZE * scaleY, 53 * TILE_SIZE * scaleX, 2 * TILE_SIZE * scaleY);
    this.minimapGraphics.fillRect(20 * TILE_SIZE * scaleX, 1 * TILE_SIZE * scaleY, 2 * TILE_SIZE * scaleX, 32 * TILE_SIZE * scaleY);
    this.minimapGraphics.fillStyle(0x3a8abb, 0.78);
    this.minimapGraphics.fillRect(0, 35 * TILE_SIZE * scaleY, width, 3 * TILE_SIZE * scaleY);
  }

  private updateMinimapViewport(): void {
    if (!this.minimapViewport) return;
    const cam = this.cameras.main;
    const scaleX = 120 / this.mapWidth;
    const scaleY = 90 / this.mapHeight;
    this.minimapViewport.clear();
    this.minimapViewport.lineStyle(1, 0xffff00, 0.85);
    this.minimapViewport.strokeRect(
      cam.scrollX * scaleX,
      cam.scrollY * scaleY,
      (cam.width / cam.zoom) * scaleX,
      (cam.height / cam.zoom) * scaleY,
    );
  }

  private updateMinimapResidents(): void {
    const scaleX = 120 / this.mapWidth;
    const scaleY = 90 / this.mapHeight;

    this.residents.forEach((resident, name) => {
      let dot = this.minimapResidentDots.get(name);
      if (!dot) {
        dot = this.add.graphics();
        this.minimapContainer.add(dot);
        this.minimapResidentDots.set(name, dot);
      }
      dot.clear();
      const score = resident.metrics.upvotes - resident.metrics.downvotes;
      const color = score < 0 ? 0xff6b6b : score > 1 ? 0x4dff88 : 0xffd54f;
      dot.fillStyle(color, 1);
      dot.fillCircle(resident.sprite.x * scaleX, resident.sprite.y * scaleY, 2);
    });

    [...this.minimapResidentDots.keys()].forEach((name) => {
      if (this.residents.has(name)) return;
      this.minimapResidentDots.get(name)?.destroy();
      this.minimapResidentDots.delete(name);
    });
  }

  private spawnPets(): void {
    const pets: Array<{ type: 'cat' | 'dog'; name: string; area: TownArea }> = [
      { type: 'cat', name: 'Mochi', area: 'office' },
      { type: 'dog', name: 'Buddy', area: 'park' },
      { type: 'cat', name: 'Luna', area: 'coffeeShop' },
      { type: 'dog', name: 'Rex', area: 'residential' },
      { type: 'cat', name: 'Whiskers', area: 'plaza' },
    ];
    pets.forEach((pet, index) => {
      const area = TOWN_MAP.areas[pet.area];
      const px = (area.x + 2 + Math.random() * Math.max(2, area.width - 4)) * TILE_SIZE;
      const py = (area.y + 2 + Math.random() * Math.max(2, area.height - 4)) * TILE_SIZE;
      this.pets.push(new PetSprite(this, px, py, pet.type, pet.name, index, this.mapWidth, this.mapHeight));
    });
  }

  private checkPetInteractions(): void {
    this.pets.forEach((pet) => {
      if (pet.getIsInteracting()) return;
      for (const resident of this.residents.values()) {
        const dx = pet.x - resident.sprite.x;
        const dy = pet.y - resident.sprite.y;
        if (Math.sqrt(dx * dx + dy * dy) < TILE_SIZE * 1.5) {
          pet.showInteraction(pet.getPetType() === 'cat' ? '😺' : '🐶');
          break;
        }
      }
    });
  }

  private startWeatherCycle(): void {
    const rotateWeather = () => {
      const next = generateRandomWeather();
      this.environmentSystem.setWeather(next.condition);
      this.weatherTimer = window.setTimeout(rotateWeather, 120000 + Math.random() * 180000);
    };
    this.weatherTimer = window.setTimeout(rotateWeather, 90000 + Math.random() * 60000);
  }

  private syncResidents(detail: OasisTopicDetail): void {
    const metricsMap = buildResidentMetrics(detail);
    const participants = dedupeParticipants(detail);
    const participantSet = new Set(participants);

    [...this.residents.keys()].forEach((name) => {
      if (participantSet.has(name)) return;
      const resident = this.residents.get(name);
      if (!resident) return;
      this.scheduleSystem?.unregisterAgent(name);
      resident.controller.destroy();
      resident.sprite.destroy();
      this.residents.delete(name);
    });

    participants.forEach((name, index) => {
      const metrics = metricsMap.get(name) || { posts: 0, upvotes: 0, downvotes: 0, replies: 0, mentions: 0 };
      const existing = this.residents.get(name);
      if (existing) {
        existing.metrics = metrics;
        this.applyResidentMetrics(existing);
        return;
      }

      const homeArea = pickHomeArea(name);
      const defaultTile = this.getDefaultTileForResident(name, index, homeArea);
      const sprite = new AgentSprite(
        this,
        defaultTile.x * TILE_SIZE + TILE_SIZE / 2,
        (defaultTile.y + 1) * TILE_SIZE,
        name,
      );
      sprite.setInteractive(new Phaser.Geom.Rectangle(-12, -38, 24, 38), Phaser.Geom.Rectangle.Contains);
      sprite.on('pointerdown', () => this.focusResident(name));
      sprite.on('pointerover', () => sprite.setScale(1.08));
      sprite.on('pointerout', () => sprite.setScale(1));
      sprite.setWanderBounds(this.getAreaBounds(homeArea));
      sprite.setLocation(areaDisplayName(homeArea));
      sprite.setActivity('waiting');
      sprite.setMood('neutral');

      const controller = new AgentMovementController(this, sprite, this.pathfinder);
      controller.setPosition(defaultTile.x, defaultTile.y);

      const resident: ResidentEntity = {
        name,
        sprite,
        controller,
        area: homeArea,
        homeArea,
        defaultTile,
        metrics,
      };
      this.residents.set(name, resident);
      this.scheduleSystem.registerAgent(name, sprite, controller);
      this.applyResidentMetrics(resident);
      if (metrics.posts === 0) {
        sprite.startWandering();
      }
    });
  }

  private applyResidentMetrics(resident: ResidentEntity): void {
    const netVotes = resident.metrics.upvotes - resident.metrics.downvotes;
    let status: ResidentWorkState = 'idle';
    if (resident.metrics.downvotes > resident.metrics.upvotes + 1) {
      status = 'error';
    } else if (resident.metrics.posts > 0 && netVotes >= 0) {
      status = netVotes > 1 ? 'working' : 'thinking';
    }

    resident.sprite.setWorkStatus(status);
    resident.sprite.setActivity(
      status === 'working' ? 'speaking' :
      status === 'thinking' ? 'thinking' :
      status === 'error' ? 'debating' : 'waiting',
    );
    resident.sprite.setMood(
      status === 'working' ? 'confident' :
      status === 'thinking' ? 'focused' :
      status === 'error' ? 'stubborn' : 'calm',
    );
    if (status === 'idle') {
      resident.sprite.rest();
      resident.sprite.startWandering();
    } else if (status === 'working') {
      resident.sprite.work();
      resident.sprite.stopWandering();
    } else if (status === 'thinking') {
      resident.sprite.think();
      resident.sprite.stopWandering();
    } else {
      resident.sprite.error();
      resident.sprite.stopWandering();
    }
  }

  private getAreaBounds(area: TownArea): { x: number; y: number; width: number; height: number } {
    const box = TOWN_MAP.areas[area];
    return {
      x: box.x * TILE_SIZE,
      y: box.y * TILE_SIZE,
      width: box.width * TILE_SIZE,
      height: box.height * TILE_SIZE,
    };
  }

  private getDefaultTileForResident(name: string, index: number, area: TownArea): { x: number; y: number } {
    const slots = getAreaSlots(area);
    const slot = slots[(hashString(name) + index) % slots.length];
    return { x: slot.x, y: slot.y };
  }

  private enqueueEvents(detail: OasisTopicDetail): void {
    const events: PlaybackEvent[] = [];
    detail.timeline.forEach((entry, index) => {
      const key = `timeline:${index}:${entry.elapsed}:${entry.event}:${entry.agent || ''}:${entry.detail || ''}`;
      if (this.processedKeys.has(key) || this.queuedKeys.has(key)) return;
      events.push({ key, kind: 'timeline', elapsed: entry.elapsed || 0, timeline: entry });
      this.queuedKeys.add(key);
    });
    detail.posts.forEach((post) => {
      const key = `post:${post.id}`;
      if (this.processedKeys.has(key) || this.queuedKeys.has(key)) return;
      events.push({ key, kind: 'post', elapsed: post.elapsed || 0, post });
      this.queuedKeys.add(key);
    });

    events.sort((left, right) => {
      if (left.elapsed !== right.elapsed) return left.elapsed - right.elapsed;
      if (left.kind === right.kind) return left.key.localeCompare(right.key);
      return left.kind === 'timeline' ? -1 : 1;
    });

    this.eventQueue.push(...events);
  }

  private startPlaybackLoop(): void {
    if (this.nextEventTimer || this.eventQueue.length === 0) {
      return;
    }

    const next = this.eventQueue.shift();
    if (!next) return;
    this.queuedKeys.delete(next.key);

    const maxElapsed = Math.max(next.elapsed, this.currentDetail?.posts.at(-1)?.elapsed || 0, this.currentDetail?.timeline.at(-1)?.elapsed || 0, 1);
    const compression = Math.max(maxElapsed / 24, 1);
    const gapMs = this.lastElapsed === 0
      ? 160
      : Phaser.Math.Clamp(((next.elapsed - this.lastElapsed) / compression) * 1000, 180, 1800);

    this.nextEventTimer = this.time.delayedCall(gapMs, () => {
      this.nextEventTimer = null;
      this.playEvent(next);
      this.processedKeys.add(next.key);
      this.lastElapsed = next.elapsed;
      this.startPlaybackLoop();
    });
  }

  private playEvent(event: PlaybackEvent): void {
    if (event.kind === 'timeline') {
      this.playTimelineEvent(event.timeline);
      return;
    }
    this.playPostEvent(event.post);
  }

  private playTimelineEvent(event: OasisTimelineEvent): void {
    const detailText = event.detail || event.event;
    const target = event.agent ? this.residents.get(event.agent) : null;

    if (event.event === 'round') {
      this.gatherResidentsAt('plaza');
      this.showSkyBanner(`ROUND ${this.currentDetail?.current_round || 1} · ${detailText}`.slice(0, 64), 0xffcc66);
      return;
    }

    if (event.event === 'conclude') {
      this.gatherResidentsAt('plaza');
      this.showSkyBanner(`CONCLUSION · ${detailText}`.slice(0, 64), 0x9cffb8);
      return;
    }

    if (target) {
      if (event.event === 'agent_call') {
        this.sendResidentToArea(target, 'office', 'heading to guild hall');
      } else if (event.event === 'manual_post') {
        this.sendResidentToArea(target, 'plaza', 'preparing a statement');
      } else if (event.event === 'if_branch') {
        this.sendResidentToArea(target, 'store', 'branching options');
      }
      this.showSpeechBubble(target.sprite, truncateBubbleText(detailText, 52), 0xe9f5ff, 0x5f574f);
      this.focusOnSprite(target.sprite);
      return;
    }

    this.showSkyBanner(detailText.slice(0, 64), 0xd6e8ff);
  }

  private playPostEvent(post: OasisTopicPost): void {
    const speaker = this.residents.get(post.author);
    if (!speaker) return;

    const replyTarget = post.reply_to ? this.postIndex.get(post.reply_to) : null;
    const replyResident = replyTarget ? this.residents.get(replyTarget.author) : null;
    const arrival = replyResident
      ? this.arrangeReplyEncounter(speaker, replyResident, post.id)
      : this.sendResidentToArea(speaker, this.pickConversationArea(post), 'sharing a new point');

    arrival.finally(() => {
      this.showSpeechBubble(
        speaker.sprite,
        truncateBubbleText(post.content),
        0xfff8ef,
        0x5f574f,
        post.reply_to ? `↩ #${post.reply_to}` : `#${post.id}`,
      );
      this.showVoteEffect(speaker.sprite, post);
      this.focusOnSprite(speaker.sprite);
    });
  }

  private pickConversationArea(post: OasisTopicPost): TownArea {
    const hash = (post.id + hashString(post.author)) % 4;
    if (post.reply_to) return 'office';
    return ['plaza', 'coffeeShop', 'park', 'store'][hash] as TownArea;
  }

  private sendResidentToArea(resident: ResidentEntity, area: TownArea, activity: string): Promise<boolean> {
    const target = this.getDefaultTileForResident(resident.name, resident.metrics.posts + resident.metrics.replies, area);
    resident.area = area;
    resident.sprite.setLocation(areaDisplayName(area));
    resident.sprite.setActivity(activity);
    resident.sprite.stopWandering();
    return resident.controller.moveTo(target.x, target.y).then((moved) => {
      resident.sprite.faceTowards(this.mapWidth / 2, this.mapHeight / 2);
      resident.sprite.idle();
      return moved;
    });
  }

  private gatherResidentsAt(area: TownArea): void {
    let slotIndex = 0;
    this.residents.forEach((resident) => {
      const slots = getAreaSlots(area);
      const slot = slots[slotIndex % slots.length];
      slotIndex += 1;
      resident.area = area;
      resident.sprite.setLocation(areaDisplayName(area));
      resident.sprite.setActivity(`gathering at ${areaDisplayName(area)}`);
      resident.sprite.stopWandering();
      resident.controller.moveTo(slot.x, slot.y).then(() => resident.sprite.idle());
    });
  }

  private arrangeReplyEncounter(speaker: ResidentEntity, target: ResidentEntity, postId: number): Promise<void> {
    const seats = TOWN_MAP.locations.meetingSeats;
    const base = postId % Math.max(1, seats.length / 2);
    const leftSeat = seats[base];
    const rightSeat = seats[(base + 3) % seats.length];

    speaker.area = 'office';
    target.area = 'office';
    speaker.sprite.setLocation(areaDisplayName('office'));
    target.sprite.setLocation(areaDisplayName('office'));
    speaker.sprite.setActivity(`replying to ${target.name}`);
    target.sprite.setActivity(`listening to ${speaker.name}`);
    speaker.sprite.stopWandering();
    target.sprite.stopWandering();

    const speakerMove = speaker.controller.moveTo(leftSeat.x, leftSeat.y + 1).then(() => {
      speaker.sprite.faceTowards(target.sprite.x, target.sprite.y);
      speaker.sprite.think();
    });
    const targetMove = target.controller.moveTo(rightSeat.x, rightSeat.y + 1).then(() => {
      target.sprite.faceTowards(speaker.sprite.x, speaker.sprite.y);
      target.sprite.idle();
    });
    return Promise.all([speakerMove, targetMove]).then(() => undefined);
  }

  private focusResident(name: string): void {
    const resident = this.residents.get(name);
    if (!resident) return;
    this.selectedResidentName = name;
    this.focusOnSprite(resident.sprite);
    this.showResidentDetailPanel(name);
    this.showResidentHighlight(name);
    const metrics = resident.metrics;
    const summary = `${name} · P${metrics.posts} · +${metrics.upvotes} / -${metrics.downvotes}`;
    this.showSkyBanner(summary.slice(0, 72), 0xfff1a8);
  }

  private focusOnSprite(sprite: AgentSprite): void {
    this.cameras.main.pan(sprite.x, sprite.y - 24, 280, 'Sine.easeOut');
  }

  private showResidentHighlight(name: string): void {
    const resident = this.residents.get(name);
    if (!resident) return;
    this.clearHighlight();

    this.highlightGraphics = this.add.graphics();
    this.highlightGraphics.setDepth(resident.sprite.depth - 1);
    let radius = 18;
    const draw = () => {
      if (!this.highlightGraphics || !resident.sprite.active) return;
      this.highlightGraphics.clear();
      this.highlightGraphics.lineStyle(3, 0xffff00, 0.8);
      this.highlightGraphics.strokeCircle(resident.sprite.x, resident.sprite.y - 12, radius);
      this.highlightGraphics.lineStyle(2, 0xffffff, 0.35);
      this.highlightGraphics.strokeCircle(resident.sprite.x, resident.sprite.y - 12, radius + 4);
    };
    draw();
    this.highlightTween = this.tweens.addCounter({
      from: 18,
      to: 28,
      duration: 650,
      yoyo: true,
      repeat: 5,
      onUpdate: (tween) => {
        radius = tween.getValue();
        draw();
      },
      onComplete: () => this.clearHighlight(),
    });
  }

  private clearHighlight(): void {
    this.highlightTween?.stop();
    this.highlightTween = null;
    this.highlightGraphics?.destroy();
    this.highlightGraphics = null;
  }

  private showResidentDetailPanel(name: string): void {
    this.hideResidentDetailPanel(true);
    const resident = this.residents.get(name);
    if (!resident) return;

    const panel = this.add.container(0, 0).setDepth(1002).setScrollFactor(0);
    this.detailPanel = panel;
    this.selectedResidentName = name;

    const bg = this.add.graphics();
    bg.fillStyle(0x1a1a2e, 0.95);
    bg.fillRoundedRect(0, 0, 184, 110, 8);
    bg.lineStyle(2, 0x4a4a6a, 1);
    bg.strokeRoundedRect(0, 0, 184, 110, 8);
    panel.add(bg);

    const title = this.add.text(92, 10, name.slice(0, 18), {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '9px',
      color: '#ffffff',
      resolution: 2,
    }).setOrigin(0.5, 0);
    panel.add(title);

    const netVotes = resident.metrics.upvotes - resident.metrics.downvotes;
    const statusText = netVotes < 0 ? 'debating' : netVotes > 1 ? 'leading' : 'active';
    const statusColor = netVotes < 0 ? '#ff7a7a' : netVotes > 1 ? '#7cff9b' : '#ffd54f';
    panel.add(this.add.text(12, 28, `● ${statusText.toUpperCase()}`, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '8px',
      color: statusColor,
      resolution: 2,
    }));
    panel.add(this.add.text(12, 44, `P ${resident.metrics.posts}  R ${resident.metrics.replies}`, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color: '#e0e0e0',
      resolution: 2,
    }));
    panel.add(this.add.text(12, 58, `+${resident.metrics.upvotes} / -${resident.metrics.downvotes}`, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color: '#d6e8ff',
      resolution: 2,
    }));
    panel.add(this.add.text(12, 72, `${resident.sprite.getLocation()}`.slice(0, 24), {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color: '#e0e0e0',
      resolution: 2,
    }));
    panel.add(this.add.text(12, 86, `${resident.sprite.getActivity()}`.slice(0, 24), {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color: '#c9d3ff',
      resolution: 2,
    }));

    panel.setScale(0);
    this.tweens.add({
      targets: panel,
      scaleX: 1,
      scaleY: 1,
      duration: 150,
      ease: 'Back.easeOut',
    });
    this.positionResidentDetailPanel(resident);
    this.time.delayedCall(5000, () => {
      if (this.selectedResidentName === name) {
        this.hideResidentDetailPanel();
      }
    });
  }

  private positionResidentDetailPanel(resident: ResidentEntity): void {
    if (!this.detailPanel) return;
    const screenX = (resident.sprite.x - this.cameras.main.scrollX) * this.cameras.main.zoom;
    const screenY = (resident.sprite.y - this.cameras.main.scrollY) * this.cameras.main.zoom;
    let panelX = screenX - 92;
    let panelY = screenY - 146;
    panelX = Phaser.Math.Clamp(panelX, 10, this.cameras.main.width - 194);
    panelY = Phaser.Math.Clamp(panelY, 10, this.cameras.main.height - 120);
    this.detailPanel.setPosition(panelX, panelY);
  }

  private refreshSelectedResidentPanel(): void {
    if (!this.selectedResidentName || !this.detailPanel) return;
    const resident = this.residents.get(this.selectedResidentName);
    if (!resident) {
      this.hideResidentDetailPanel(true);
      return;
    }
    this.positionResidentDetailPanel(resident);
  }

  private hideResidentDetailPanel(immediate = false): void {
    if (!this.detailPanel) {
      this.selectedResidentName = null;
      return;
    }
    const panel = this.detailPanel;
    this.detailPanel = null;
    this.selectedResidentName = null;
    if (immediate) {
      panel.destroy();
      return;
    }
    this.tweens.add({
      targets: panel,
      scaleX: 0,
      scaleY: 0,
      duration: 100,
      onComplete: () => panel.destroy(),
    });
  }

  private showSpeechBubble(
    sprite: AgentSprite,
    text: string,
    backgroundColor: number,
    borderColor: number,
    label = '',
  ): void {
    const container = this.add.container(sprite.x, sprite.y - 52);
    container.setDepth(1000);

    const fontSize = 9;
    const wrapWidth = 120;
    const lines = this.wrapBubbleText(text, 20);
    const bubbleWidth = Math.min(wrapWidth, Math.max(...lines.map((line) => measureVisualWidth(line)), 8) * 6 + 18);
    const bubbleHeight = lines.length * 12 + (label ? 14 : 0) + 16;

    const graphics = this.add.graphics();
    graphics.fillStyle(backgroundColor, 0.96);
    graphics.fillRoundedRect(-bubbleWidth / 2, -bubbleHeight, bubbleWidth, bubbleHeight, 8);
    graphics.lineStyle(2, borderColor, 1);
    graphics.strokeRoundedRect(-bubbleWidth / 2, -bubbleHeight, bubbleWidth, bubbleHeight, 8);
    graphics.fillTriangle(-6, 0, 6, 0, 0, 12);
    graphics.lineBetween(-6, 0, 0, 12);
    graphics.lineBetween(6, 0, 0, 12);
    container.add(graphics);

    if (label) {
      const labelText = this.add.text(0, -bubbleHeight + 8, label, {
        fontFamily: PIXEL_FONT_STACK,
        fontSize: '7px',
        color: '#83769c',
        resolution: 2,
      }).setOrigin(0.5, 0);
      container.add(labelText);
    }

    const body = this.add.text(0, -bubbleHeight / 2 + (label ? 8 : 0), lines.join('\n'), {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: `${fontSize}px`,
      color: '#1d2b53',
      align: 'center',
      lineSpacing: 4,
      resolution: 2,
    }).setOrigin(0.5);
    container.add(body);

    container.setScale(0);
    this.tweens.add({
      targets: container,
      scaleX: 1,
      scaleY: 1,
      duration: 160,
      ease: 'Back.easeOut',
    });
    this.activeWorldBubbles.push(container);

    this.time.delayedCall(2600, () => {
      this.tweens.add({
        targets: container,
        alpha: 0,
        y: container.y - 14,
        duration: 220,
        onComplete: () => {
          container.destroy();
          this.activeWorldBubbles = this.activeWorldBubbles.filter((item) => item !== container);
        },
      });
    });
  }

  private wrapBubbleText(text: string, maxCharsPerLine: number): string[] {
    const normalized = text.replace(/\s+/g, ' ').trim();
    const lines: string[] = [];
    let current = '';
    let currentWidth = 0;
    for (const char of normalized) {
      const charWidth = isWideCharacter(char) ? 2 : 1;
      if (currentWidth + charWidth > maxCharsPerLine && current.trim()) {
        lines.push(current.trimEnd());
        current = char === ' ' ? '' : char;
        currentWidth = char === ' ' ? 0 : charWidth;
      } else {
        current += char;
        currentWidth += charWidth;
      }
    }
    if (current.trim()) lines.push(current.trim());
    return lines.slice(0, 4);
  }

  private showVoteEffect(sprite: AgentSprite, post: OasisTopicPost): void {
    const container = this.add.container(sprite.x + 22, sprite.y - 62);
    container.setDepth(1000);

    const scoreText = `👍${post.upvotes} 👎${post.downvotes}`;
    const score = post.upvotes - post.downvotes;
    const color = score >= 0 ? '#00e436' : '#ff004d';
    const text = this.add.text(0, 0, scoreText, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '7px',
      color,
      stroke: '#000000',
      strokeThickness: 2,
      resolution: 2,
    }).setOrigin(0.5);
    container.add(text);
    this.activeEffects.push(container);

    this.tweens.add({
      targets: container,
      y: container.y - 28,
      alpha: 0,
      duration: 1200,
      ease: 'Sine.easeOut',
      onComplete: () => {
        container.destroy();
        this.activeEffects = this.activeEffects.filter((item) => item !== container);
      },
    });
  }

  private showVoteDeltaEffects(previous: Map<number, OasisTopicPost>, posts: OasisTopicPost[]): void {
    posts.forEach((post) => {
      const old = previous.get(post.id);
      if (!old || (old.upvotes === post.upvotes && old.downvotes === post.downvotes)) {
        return;
      }
      const resident = this.residents.get(post.author);
      if (!resident) return;
      const deltaUp = post.upvotes - old.upvotes;
      const deltaDown = post.downvotes - old.downvotes;
      if (deltaUp > 0) {
        resident.sprite.showNotificationIndicator('success', 2200);
      } else if (deltaDown > 0) {
        resident.sprite.showNotificationIndicator('alert', 2200);
      }
      this.showVoteEffect(resident.sprite, post);
    });
  }

  private showSkyBanner(text: string, tint: number): void {
    const container = this.add.container(this.cameras.main.midPoint.x, 96);
    container.setDepth(1002);
    container.setScrollFactor(0);

    const width = Math.min(360, Math.max(measureVisualWidth(text) * 8 + 26, 110));
    const graphics = this.add.graphics();
    graphics.fillStyle(0x1d2b53, 0.92);
    graphics.fillRoundedRect(-width / 2, -14, width, 28, 10);
    graphics.lineStyle(2, tint, 1);
    graphics.strokeRoundedRect(-width / 2, -14, width, 28, 10);
    container.add(graphics);

    const body = this.add.text(0, 0, text, {
      fontFamily: PIXEL_FONT_STACK,
      fontSize: '8px',
      color: '#fff1e8',
      align: 'center',
      resolution: 2,
    }).setOrigin(0.5);
    container.add(body);

    this.activeEffects.push(container);
    this.tweens.add({
      targets: container,
      alpha: 0,
      y: container.y - 12,
      duration: 1600,
      delay: 450,
      ease: 'Sine.easeOut',
      onComplete: () => {
        container.destroy();
        this.activeEffects = this.activeEffects.filter((item) => item !== container);
      },
    });
  }
}
