// Shared Studio/mobile importer: creating, confirming replacement and cancelling.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function scenario(status, confirm) {
  const calls = [];
  const prompts = [];
  const window = { confirm: text => { prompts.push(text); return confirm; } };
  const context = {
    window, Response,
    document: { documentElement: { lang: 'zh' } },
    localStorage: { getItem: () => 'zh' },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../src/frontend/static/js/snapshot_zip_progress.js'), 'utf8'), context);
  const result = await window.teamSnapshotImport(async replace => {
    calls.push(replace);
    return new Response(JSON.stringify({ code: 'team_exists' }), {
      status: replace ? 200 : status, headers: { 'Content-Type': 'application/json' },
    });
  }, 'demo');
  return { calls, prompts, result };
}

(async () => {
  const created = await scenario(200, false);
  assert.deepEqual(created.calls, [false]);
  assert.equal(created.prompts.length, 0);
  assert.equal(created.result.status, 200);

  const cancelled = await scenario(409, false);
  assert.deepEqual(cancelled.calls, [false]);
  assert.equal(cancelled.result, null);
  assert.equal(cancelled.prompts.length, 1);

  const replaced = await scenario(409, true);
  assert.deepEqual(replaced.calls, [false, true]);
  assert.equal(replaced.result.status, 200);
  assert.match(replaced.prompts[0], /清除旧团队/);
  assert.match(replaced.prompts[0], /创建新成员会话/);
  console.log('3 Team import UI cases passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
