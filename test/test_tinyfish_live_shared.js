const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const helperPath = path.resolve(__dirname, '../frontend/js/tinyfish-live-shared.js');
const code = fs.readFileSync(helperPath, 'utf8');

const context = {
  window: {},
  TextDecoder: global.TextDecoder,
};
vm.createContext(context);
vm.runInContext(code, context);

const helper = context.window.ClawcrossTinyFish;
assert.ok(helper, 'ClawcrossTinyFish helper should be exposed');

const mismatch = helper.normalizeTinyFishEvent({
  type: 'PROGRESS',
  _sse_event: 'HEARTBEAT',
  message: 'still crawling',
});
assert.strictEqual(mismatch._tinyfish_type, 'PROGRESS');
assert.strictEqual(mismatch._tinyfish_sse_event, 'HEARTBEAT');
assert.strictEqual(mismatch._tinyfish_type_mismatch, true);
assert.strictEqual(helper.isIgnorableHeartbeat(mismatch), false);

const emptyHeartbeat = helper.normalizeTinyFishEvent({
  type: 'HEARTBEAT',
  _sse_event: 'HEARTBEAT',
  run_id: 'run-1',
  timestamp: '2026-03-31T00:00:00Z',
});
assert.strictEqual(helper.isIgnorableHeartbeat(emptyHeartbeat), true);

const progressWithoutDetail = helper.normalizeTinyFishEvent({
  type: 'PROGRESS',
  run_id: 'run-1',
  screenshot_url: 'https://example.test/shot.png',
  phase: 'extract',
});
const progressDetail = helper.formatTinyFishEventDetail(progressWithoutDetail);
assert.ok(progressDetail.includes('screenshot_url'), 'progress fallback detail should include raw payload keys');

const heartbeatWithDetail = helper.normalizeTinyFishEvent({
  type: 'HEARTBEAT',
  _sse_event: 'HEARTBEAT',
  status_message: 'browser alive',
});
assert.strictEqual(helper.isIgnorableHeartbeat(heartbeatWithDetail), false);
assert.strictEqual(helper.formatTinyFishEventDetail(heartbeatWithDetail), 'browser alive');

console.log('ok');
