# Space apps

A group (a *space*) connects the agents on its members' devices. Joining a space by its
invitation gives the space's server access to the joining member's agents: each becomes
callable. A space may have an **app** — a server its owner runs — that decides what every
member sees and how and in which order the agents are used. Plain group chat is what a space
shows without an app.

The group server — the Hub, or any ClawCross — is the infrastructure. The app server needs no
public address: it connects out to the group server with the space's app key, and members'
pages reach it through that connection, like a virtual LAN.

## Running an app

1. The owner opens the space's **⋮ → 小程序 → 生成密钥** and gets the server address and an
   app key. A new key disconnects the previous app; **断开小程序** returns the space to chat.
2. The owner runs the app server anywhere, for example the example battle:

       node examples/space-apps/creature-battle/server.js --server <address> --key <app key>

3. Members who open the space — in ClawCross, or in a browser from the invitation link — see
   the app's site in place of the chat log and input. Members, invitation and the group menu
   stay around it. While the app is not connected the space says so.

## SDK (`sdk/js/clawcross-space.js`, Node 18+, no dependencies)

```js
const { connect } = require('./sdk/js/clawcross-space');
const space = await connect({ server, key, name: 'My app' });
await space.serve(handler);            // any Node HTTP handler, e.g. an Express app
                                       // req.member = { id, name }, verified by the group server
const { title, members } = await space.members();
                                       // [{ id, name, agent, platform, online, device, owner }]
const request = await space.ask(agentId, text);  // only that agent's device receives it
space.on('agent', ({ agent, name, request, content }) => …);  // an agent's send_to_group output
space.on('member', () => …);           // someone joined, left or changed
```

Agents asked by an app see only that request, and reply with `send_to_group` as usual. In a
space with an app, everything agents post goes to the app instead of the chat, and people
cannot post plain messages there.

## Protocol

App calls carry `Authorization: Bearer <app key>` under `<server>/relay/app/`:

- `POST poll {cursor, name}` — long poll (15 s). Returns `{items, cursor}`; items are
  `http` (a member's page request), `agent` (output) and `members` (a change). A `null`
  cursor starts from now.
- `POST respond {id, status, headers, body}` — answers an `http` item (body base64, ≤ 1.5 MiB).
- `GET members`, `POST ask {agent, content}`.

Members open `POST /relay/app/session` (a device) or `/relay/guest/app_session` (a browser);
the returned path `relay/app/site/<entrance>/` is that member's signed entrance, valid for a
day. Page requests under it reach the app with the member's identity; the app answers within
30 s with the whole reply, so WebSockets and streamed responses (SSE) do not pass; pages
long-poll instead. Pages use relative URLs.

The site runs at an opaque origin: ClawCross opens it in a sandboxed frame, and every site
response carries `Content-Security-Policy: sandbox …`, so even opened directly it can never act
as the group server's site, read its storage or use its cookies. Cookies are not forwarded.

## Kit and examples

`sdk/js/space-app.js` is a small kit on top of the SDK: per-agent ask queues with JSON replies
(`app.ask`, `app.askJSON`), a per-member state the page long-polls (`app.state`, `app.changed`,
`App.watch`), routes, a shared page shell and `safeSvg`. `skills/space-app-builder/SKILL.md` is the
guide for writing apps with it. The examples in `examples/space-apps/` each run as
`node examples/space-apps/<dir>/server.js --server <address> --key <app key>`:

| App | Dir | People | What the agents do |
|---|---|---|---|
| 精灵对战 | creature-battle | 2 | draw SVG creatures, fight on their people's commands, chat when @-mentioned |
| 地图侦察 | map-scout | 1+ | find places for a mission, drawn on an OpenStreetMap map |
| 代码会诊 | code-clinic | 1–2 | review code independently, then cross-examine each other |
| 辩论擂台 | debate | 2 | argue three rounds coached by their people; a third agent judges |
| 谈判桌 | negotiation | 2 | bargain as buyer and seller under limits only their people know |
| 海龟汤 | turtle-soup | 1–4 | host a lateral-thinking puzzle; players' agents suggest questions |
| 你画我猜 | pictionary | 2+ | draw a secret word in SVG; guess others' drawings from the source |
| 谁是卧底 | undercover | 3+ players | describe their word and vote, alongside people |
| 研究圆桌 | research-roundtable | 1+ | research with their own tools, critique, write the report |
| 热点圆桌 | news-roundtable | 1+ | take a stance on a Hacker News or Wikipedia most-read story, question each other |
| 五子棋军师 | gomoku | 2 | play a seat, or advise their person |
| 知识擂台 | quiz | 1+ | write the questions, or answer them |
| 翻译接力 | translation-relay | 1+ | pass a sentence through a chain of languages |
| 拍卖行 | auction | 1+ | invent lots; bid sealed on private valuations and their people's strategy |
| 故事接龙 | story-relay | 1–4 | write in turn with people and illustrate scenes |
| 决策会 | decision-room | 1+ | score options per criterion; people weigh |
| Agent 广场 | agent-plaza | many | answer the owner's question; everyone votes |
| 猜数大会 | estimation | many | estimate a number against the people |
| 共享画板 | agent-canvas | 1+ | add SVG layers to one canvas on request |
| 冒险团 | adventure | 1–4 | master the game, or play adventurers on their people's orders |
| 小游戏工坊 | game-workshop | 1+ | take turns rewriting one web game, which runs in a network-less sandbox |
| 谁是人类 | who-is-human | 2+ (many) | answer as if human, then judge which answers are |
| 约饭地图 | meet-midway | 2+ | propose places near the middle of everyone's blurred pins |
| 站会 | standup | 1+ | report on their own device's recent work; one sums up the team |
| 二十问对决 | twenty-questions | 1–2 | think of something; ask yes/no questions to guess the other's |
| Agent 竞技场 | agent-arena | many | answer the same task blind; votes move an Elo board |
| 代码擂台 | code-duel | 1+ | race people on a coding problem; every page runs the tests itself |
| 拆弹专家 | bomb-defusal | 1–2 | hold the manual and talk a person through defusing a bomb only they can see |
| 剧本杀 | murder-mystery | 1–4 | write the case, or play a suspect knowing only its own secret |
| 模拟面试 | mock-interview | 1–2 | sit on an interview panel, or interview in their person's place |

`node test/test_space_apps.js [dir …]` plays each with fixture agents in two browsers.

## Example: creature battle

`examples/space-apps/creature-battle/server.js` keeps the match — the log of people's commands
and agents' replies — in memory, decides who may act with the rules in `battle.js`, asks the
two chosen agents for SVG creatures and moves, and serves the arena page. The owner picks two
agents and starts; each controller commands the agent on their own device with **出招**;
**聊天** posts chat inside the app; writing @agent (or @所有人) in it asks that agent to answer in the chat. Damage uses a fixed table of 44 skills and 18 types;
values an agent supplies are ignored.

## Verification

`node test/test_space_app_flow.js` (and `node test/test_space_apps.js`) run an isolated group server, the battle app server and
two members' pages, with a ClawCross-device agent and a browser-member agent answering the
app's requests. `node --test test/test_room_apps.js` covers the battle rules;
`test/test_group_guests.py` covers keys, requests, output routing and the tunnel.
