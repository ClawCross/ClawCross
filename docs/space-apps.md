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
30 s. Pages use relative URLs; WebSockets are not tunnelled, so use polling or SSE.

The site runs at an opaque origin: ClawCross opens it in a sandboxed frame, and every site
response carries `Content-Security-Policy: sandbox …`, so even opened directly it can never act
as the group server's site, read its storage or use its cookies. Cookies are not forwarded.

## Example: creature battle

`examples/space-apps/creature-battle/server.js` keeps the match — the log of people's commands
and agents' replies — in memory, decides who may act with the rules in `battle.js`, asks the
two chosen agents for SVG creatures and moves, and serves the arena page. The owner picks two
agents and starts; each controller commands the agent on their own device with **出招**;
**聊天** posts chat inside the app. Damage uses a fixed table of 44 skills and 18 types;
values an agent supplies are ignored.

## Verification

`node test/test_space_app_flow.js` runs an isolated group server, the battle app server and
two members' pages, with a ClawCross-device agent and a browser-member agent answering the
app's requests. `node --test test/test_room_apps.js` covers the battle rules;
`test/test_group_guests.py` covers keys, requests, output routing and the tunnel.
