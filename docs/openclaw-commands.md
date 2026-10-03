# OpenClaw in ClawCross

OpenClaw is an ACP agent, driven the same way as Codex or Claude Code: through
acpx, which runs `openclaw acp`. ClawCross never writes OpenClaw's
configuration — it does not start the gateway, create or delete OpenClaw agents,
edit their skills, tools, channels or workspace files, or push LLM settings into
OpenClaw. Manage OpenClaw itself with OpenClaw's own CLI and UI.

## Requirements

- acpx: `bash launch/run.sh install-component acpx` (Windows: `run.ps1 install-component acpx`)
- the `openclaw` CLI on `PATH`, with its gateway running and authenticated as
  OpenClaw's documentation describes (`openclaw acp` connects to it)

## Using it

- Studio: pick the **openclaw** tab next to WeBot and the other ACP tools; a new
  conversation is a new agent.
- Teams and group chats: add a member with platform `openclaw`, as for `codex`.
- CLI: `uv run src/cli/cli.py agents create --platform openclaw --name <name>`

Each ClawCross agent has its own OpenClaw session. `openclaw acp` takes a gateway
session key, `agent:main:clawcross-<owner>-<agent id>`, so every new agent talks to
OpenClaw's `main` agent. Cancel and reset work as for other ACP agents; reset
starts a new session key.

## Limits

- `openclaw acp` accepts no per-session MCP servers, so ClawCross tools are not
  attached to OpenClaw sessions. OpenClaw agents reach ClawCross through its CLI.
- The model is OpenClaw's own setting; ACP does not expose model selection.

## Agents made before this change

OpenClaw agents created when ClawCross talked to the OpenClaw HTTP gateway are
migrated once to ACP agents. They keep their OpenClaw session and the OpenClaw agent
they pointed at; the gateway URL, token and model are dropped from their settings.

## Importing LLM settings from OpenClaw

Read-only towards OpenClaw: the setup wizard's **从 OpenClaw 导入** button, or
`bash launch/run.sh import-openclaw-llm`, reads `~/.openclaw/openclaw.json` and the
main agent's `models.json` and writes ClawCross's `config/.env`.
`start --with-openclaw` does the same at startup while ClawCross has no API key of
its own; an existing key is never replaced.
