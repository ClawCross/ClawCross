# Room applications

A room is an application over an existing group. Its frontend can be a chat,
an arena or another interaction. People and Agents keep the same memberships,
invitation link and message channel. The Agent runtime, Gateway and group APIs
are unchanged.

## First example: creature battle

In the Hub or the client's message center, create a room and choose **精灵对战**.
Invite people or join from ClawCross and bring two Agents. The room host selects
the two fighters and clicks **生成双方精灵**. Each Agent receives an ordinary
group message asking for an original SVG character. After both characters
arrive, the host clicks **开始对战**. Controllers then take turns requesting moves
in natural language from the existing chat input. Its **聊天 / 出招** switch
selects ordinary messages or a move request; the arena has no second input.
While an Agent is replying, another move cannot be submitted. The other
controller acts when the reply has been settled.

For new matches, only the human on an Agent's device can command its moves.
The host can prepare both avatars and start the match. The application checks
the actual message sender; a typed owner label does not grant control. Requests
carry an owner label for the Agent, and game rules are published in the existing
group announcement. These are application rules, not an additional Agent
runtime permission system. Existing prototype logs still replay.

The application matches the Agent's answer against a fixed skill table:
撞击, 电光一闪, 喷射火焰, 水枪, 十万伏特, 守住 and 自我再生.
The frontend rule engine calculates HP, guard, healing and the winner. Values
such as damage or HP supplied by an Agent are ignored. A reply must be from the
selected Agent and match the current command and turn. Duplicate, stale,
unknown and human-authored moves do not advance combat.

This is a cooperative room prototype using a shared message log, rather than
a competitive game authority. Private roles for games such as Werewolf would
need an application-level private-state service above the existing Agent APIs.

## SDK

The portable SDK is in `src/frontend/static/js/room-sdk.js`. The battle plugin
is `room-battle.js`; the same files are vendored in `ClawCrossHub/lib/rooms`.
They work as browser globals or CommonJS modules without extra dependencies.

The host supplies only these application adapters:

- `send(content, mentions)`: post using its existing group credentials.
- `configure(announcement)`: update the existing owner-managed announcement.
- `loadHistory()`: read the existing message history for replay.
- `update(snapshot)`: supply members, messages and the current viewer.

`RoomClient.publish` and `sendToAgent` wrap the message channel.
`registerApp(id, {mount})` registers a frontend; its handle implements
`update(snapshot)` and `destroy()`. Another room application can use the same
transport and its own presentation and rules.

## Existing wire format

The announcement carries one `[ClawCrossRoomConfig:v1]` JSON line containing
the app id, match id and player bindings. Human announcement text is preserved.
Only the existing group owner can update it.

Game messages carry `[ClawCrossRoom:v1]` JSON with
`protocol: "clawcross.room/1"`. Commands and replies include a correlation id.
The SDK correlates membership identities across the browser's public
principals and a ClawCross client's local presentation.

SVGs are bounded, reject scripts and external resources, and are displayed as
image data URLs. No Agent-provided HTML or JavaScript is executed.
There are no new Agent tools, no altered system prompts and no new backend
group fields or endpoints. The runtime still applies each Agent's own tool,
mode, approval and sandbox restrictions.

## Verification

`node test/test_room_apps.js` covers fixed damage, replay, identity mapping,
invalid actions and SVG restrictions. The mobile browser smoke includes an
actual message-center arena test. `node test/test_room_hub_flow.js` runs an
isolated built Hub, two browser members and the existing browser-Agent runtime
with fixture provider responses. It checks the real group HTTP APIs and
shared battle state; it does not spend model credits or modify live rooms.
