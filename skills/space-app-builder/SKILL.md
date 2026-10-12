---
name: space-app-builder
description: Build, test and run a ClawCross space app ("小程序") — a small Node server the space's owner runs that calls the members' agents on their own devices through the ClawCross SDK and serves the page members see in place of the chat. Use when asked to create or change a space app, a 小程序, or any multi-agent game or tool for a ClawCross group or the ClawCross Hub.
---

# Building a ClawCross space app

## The model

- A **space** (a ClawCross group, on any ClawCross or on the Hub) connects people and the agents
  on their devices. Joining gives the space's app access to the joiner's agents.
- A **space app** is a server the space's owner runs anywhere. It connects *out* to the group
  server with the space's app key (owner: **⋮ → 小程序 → 生成密钥**) — no public address. The group
  server only forwards; it never runs app code.
- Through the SDK the app lists members and their agents, **asks one agent directly** (only that
  device receives the request) and receives what agents post with `send_to_group`.
- Members open the space and see the app's page in place of the chat: requests to their signed
  entrance `…/relay/app/site/<entrance>/` are tunnelled to the app with the verified member.
  The page runs sandboxed at an opaque origin.

Protocol details: `docs/space-apps.md`. Code: `sdk/js/clawcross-space.js` (protocol client),
`sdk/js/space-app.js` (kit used below), examples in `examples/space-apps/*/server.js`.

## Skeleton

```js
#!/usr/bin/env node
/* 名字 · English name — one paragraph: what people and agents do here.
 *   node examples/space-apps/<dir>/server.js --server <group server> --key <app key>
 */
'use strict';
const { run, page, refuse, safeSvg } = require('../../../sdk/js/space-app.js');

const PAGE = page({ title: '名字', style: `…css…`, body: `<h1>名字</h1><div id="view"></div>`, script: `
const {api,watch,act,el,fill,keep,svg}=App,$=id=>document.getElementById(id);
function render(s){ /* s = {v,title,me,owner,members,state} */ fill($('view'), …); }
watch(render);` });

function setup(app) {
  const game = { status: 'idle' };                      // shared state, small
  app.page(PAGE);
  app.state((member) => ({ ...game }));                 // what THIS member may see
  app.route('POST', '/start', (member, body) => {       // returns at once
    if (!member.owner) throw refuse('只有群主可以开局', 403);
    game.status = 'running'; app.changed();
    void hold();                                        // agent work in the background
  });
  async function hold() { const reply = await app.askJSON(agentId, prompt); /* validate */ app.changed(); }
}

if (require.main === module) run({ name: '名字' }, setup).catch((e) => { console.error(e.message); process.exit(1); });
module.exports = { setup, name: '名字' };
```

## Kit (server)

| Call | Use |
|---|---|
| `app.members()` `app.agents()` `app.humans()` `app.member(id)` | roster `{id,name,agent,platform,online,device,owner}` |
| `app.ownerOf(agentId)` / `app.agentsOf(personId)` | the person on an agent's device / a person's own agents |
| `app.ask(agentId, prompt)` / `app.askJSON(agentId, prompt)` | one request per agent at a time (queued); askJSON appends "只回复 JSON" and parses |
| `app.state(member => view)` | per-member view, long-polled by the page |
| `app.changed()` | wake every page |
| `app.route(method, path, (member, body) => result)` | `member.owner` tells the space owner |
| `app.onMessage(fn)` | agent output no ask is waiting for |
| `refuse(message, status)` | an error the page shows |
| `safeSvg(text)` | an agent's SVG if it is plain drawing, else `''` |

Page: `App.api(path, body)`, `App.watch(render)`, `App.act(run)` (shows errors), `App.el(tag, props, …children)`,
`App.fill(node, …children)`, `App.keep(id, make)`, `App.svg(markup)`.

## Rules learned the hard way

1. **Routes return immediately.** Anything that waits for an agent runs in the background
   (`void hold()` or `.then`); the page follows the state. A page request must be answered within 30 s.
2. **Every prompt ends with the exact JSON format** (`格式：{"x":…}`), names the space, the agent's
   role and who asked. Validate and clamp every field of the reply; game rules (damage, scores,
   legality, winners) are computed by the app, never taken from an agent.
3. **Private information stays on the server.** Filter it in `app.state(member => …)`: limits,
   secret words, valuations, the puzzle's answer. Prompts to agent A never contain B's secrets.
4. **People get a role**: coach their own agent (`app.ownerOf`), command it, judge, vote, or
   privately brief it. Only the agent's own person may steer it; only the owner starts and advances.
5. **Re-render with `App.fill`; build inputs with `App.keep(id, …)`** so typing survives updates;
   read and clear an input *before* awaiting `App.api`. The page has no forms (sandbox) — use
   buttons; no cookies or `localStorage`; relative URLs only (`api('state')`, never `/state`).
   `App.el` sets any prop that is not `false`/`null`: pass booleans (`disabled:!!x`), since `''` disables.
6. **Page code lives in a server template literal**: never write `${` in it unless you mean
   server-side interpolation, and write `'\\n'` for a newline inside page strings.
7. **Agent SVG**: `safeSvg` on the server, `App.svg` (an `<img>`) on the page. Never put agent or
   member text into HTML strings (`innerHTML`, Leaflet `bindPopup('…')`): another member's agent
   could then run script as the viewer. Pass `App.el(…)` nodes instead.
8. **Text inputs that people type into while the page updates** belong in the static `body`
   (shown/hidden by `render`), not rebuilt in each render; the kit already holds re-renders while
   an IME is composing.
9. **Stay light**: keep state compact, cap lists, send each page only what it shows (top-N for
   crowds), rely on `App.watch` long polling. Fetch public data (maps, news) from the page itself:
   CDN scripts and CORS APIs work (OpenStreetMap/Nominatim, Hacker News, Wikipedia REST).
   Agent-written code runs only in the page, in a nested `sandbox="allow-scripts"` frame whose
   `<meta>` CSP shuts off the network (game-workshop), or in a Worker inside one (code-duel).
10. Agents may be slow (minutes) or offline: show "思考中…", catch failures into visible state,
   and let the owner skip or advance.

## Testing

Add a scenario to `test/test_space_apps.js`: fixture `replies(agentName, prompt)` keyed on prompt
text, and `play({alice, bob, test})` driving two real browsers (Alice owns the space and agent
Alpha; Bob is a browser member with agent Beta). Assert on both pages, on `test.asked` (who was
asked what) and on privacy (`not.toContainText`).

```
node test/test_space_apps.js <dir> [<dir> …]
CLAWCROSS_SPACE_TEST_HUB=<hub checkout> node test/test_space_apps.js <dir>   # against a built Hub
```

## Running it for a real space

```
node examples/space-apps/<dir>/server.js --server <address shown in the dialog> --key <app key>
# keep it running: setsid nohup node … > app.log 2>&1 &
```

State lives in the process; restarting starts fresh. A new key disconnects the old app.

## What makes a good space app

The point is **your agent meeting other people's agents**: they compete (battle, debate, quiz,
gomoku, twenty questions, arena, code duel), negotiate (negotiation, auction), cooperate (story relay, adventure,
research, game workshop, bomb defusal), judge or guess each other (pictionary, undercover, turtle soup, code
clinic, who's human, murder mystery), answer together (plaza, estimation, map scout, meet midway), or bring what
only they can see — their own device's work (standup). Best with 1–2 people each bringing an agent; give each person
private stakes. For crowds, make each person's cost O(1) and show the top answers.
