# Group apps

A group is a connector between Agents on many devices: each member's Agent can be
called through ordinary group messages. A group may also carry one **app** — a web
page that decides what every member sees and how the Agents are used. Plain chat is
what a group shows without an app; a game such as the creature battle is an app.

The app is a property of the group and lives on the group server. ClawCross carries
no app code, so a new game needs no ClawCross release: the owner uploads its page.

## Using an app

The owner of a group hosted by this ClawCross opens **⋮ → 小程序** and uploads one
`.html` file (at most 1.5 MiB). Members who open the group — in ClawCross after
joining by invitation, or in a browser from the invitation link — see the app in
place of the chat log and input. **移除小程序** returns the group to plain chat.
Messages and members are unchanged by adding, replacing or removing an app.

## How an app reaches the group

ClawCross loads `static/room-app-frame.html` into a sandboxed iframe and hands it
the app page. The app runs at an opaque origin under that frame's policy: no
network, no forms, no storage, no access to the host page, its cookies or its
tokens. It reaches the group through `window.ClawCrossRoomApp`:

- `onSnapshot(fn)`: the group title and announcement, its members (people and
  Agents, grouped by device), who is looking (`actor`), whether they own the group,
  and the messages the app has not seen yet.
- `send(content, mentions)`: post a group message as the person looking.
  Mentioned Agents are woken and reply in the group, as in plain chat.
- `history()`: every message of the group, for replaying state.
- `configure(announcement)`: the owner sets the group announcement.

`room-sdk.js` is the shared protocol for apps written against this API:
`[ClawCrossRoomConfig:v1]` is one JSON line in the announcement for an app's
settings, and `[ClawCrossRoom:v1]` JSON messages are an app's protocol messages.
`RoomClient` wraps the calls above, and `sendToAgent` asks one Agent through a
mention. A plain chat view shows protocol messages as `[小程序消息]`.

## Server side

The group server keeps the page in `relay_apps`; the group card carries
`app: {name, sha256, size}` so every device notices a change. The page itself is
read with `GET /relay/app` (devices), `GET /relay/guest/app` (browser members) and
`GET /groups/{gid}/app` (this ClawCross's UI). The owner sets it with the
`app` manage action (`PUT` / `DELETE /groups/{gid}/app`).

## Example: creature battle

`examples/room-apps/creature-battle/battle.js` is the battle; build the page to
upload with:

    python examples/room-apps/creature-battle/build.py --output creature-battle.html

The owner picks two Agents and clicks **生成双方精灵**. Each Agent receives a group
message asking for an original SVG creature, one or two of 18 types and four of 44
fixed skills, in a fixed reply format. After both creatures arrive the owner clicks
**开始对战**. Controllers take turns: a skill card or a sentence in the app's input,
sent with **出招**, asks their Agent for a move; **聊天** posts plain chat.

Only the human on an Agent's device commands its moves; the owner prepares the
avatars and starts the match. The app checks the actual sender, the current command
and the turn; duplicate, stale, unknown and human-authored moves do not count. Damage
uses the fixed skill table with 2× / 0.5× / 0× effectiveness, multiplied for dual
types, and 1.5× for same-type attacks; HP or power supplied by an Agent is ignored.
SVGs are bounded, reject scripts and external resources, and are shown as image data
URLs. Game rules are published in the group announcement for the Agents.

## Verification

`node --test test/test_room_apps.js` covers the battle rules, replay, identity mapping
and SVG restrictions. The browser smokes (`mobile-agents-smoke`, `group-guests`)
load the built battle as a group app in the message center and on the browser member
page. `test/test_group_guests.py` covers storing and serving the app page.
