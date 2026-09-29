import Phaser from 'phaser';

import type { OasisTopicDetail, OasisTownRuntimeApi } from './types';
import { OasisTownScene } from './scenes/OasisTownScene';

declare global {
  interface Window {
    OasisTown?: OasisTownRuntimeApi;
  }
}

class OasisTownRuntime implements OasisTownRuntimeApi {
  private game: Phaser.Game | null = null;
  private scene: OasisTownScene | null = null;
  private container: HTMLElement | null = null;
  private topicId: string | null = null;

  mount(container: HTMLElement, detail: OasisTopicDetail): void {
    if (!container) return;

    const shouldRecreate = !this.game || this.container !== container || this.topicId !== detail.topic_id;
    if (shouldRecreate) {
      this.destroy();
      this.container = container;
      this.topicId = detail.topic_id;
      this.scene = new OasisTownScene(detail);
      this.game = new Phaser.Game({
        type: Phaser.AUTO,
        pixelArt: true,
        roundPixels: true,
        backgroundColor: '#4a7c59',
        parent: container,
        scene: [this.scene],
        scale: {
          mode: Phaser.Scale.RESIZE,
          autoCenter: Phaser.Scale.CENTER_BOTH,
          width: container.clientWidth || 800,
          height: container.clientHeight || 520,
        },
      });
      return;
    }

    this.update(detail);
  }

  update(detail: OasisTopicDetail): void {
    this.topicId = detail.topic_id;
    this.scene?.setTopicDetail(detail);
  }

  destroy(): void {
    if (this.game) {
      this.game.destroy(true);
      this.game = null;
    }
    this.scene = null;
    this.topicId = null;
    if (this.container) {
      this.container.innerHTML = '';
    }
    this.container = null;
  }
}

window.OasisTown = new OasisTownRuntime();
