---
name: "Clawcross"
description: "A multi-agent orchestration platform with visual workflow (OASIS). Create and configure agents (OpenClaw/external API), orchestrate them into Teams, build new Teams with ClawCross Creator, and design workflows via visual canvas. Supports Team conversations, OASIS Town with living GraphRAG memory, scheduled tasks, Telegram/QQ bots, TinyFish internet search agent, and Cloudflare Tunnel for remote access."
user-invokable: true
compatibility:
  - "deepseek"
  - "openai"
  - "gemini"
  - "claude"
  - "anthropic"
  - "ollama"
  - "antigravity"
  - "minimax"

argument-hint: "[RECOMMENDED] Configure LLM_API_KEY and LLM_BASE_URL in the first-login wizard. [MODEL] The wizard can discover available models when LLM_MODEL is empty. [OPTIONAL] TTS_MODEL/TTS_VOICE, STT_MODEL/WHISPER_MODEL, TINYFISH_*, TELEGRAM_BOT_TOKEN/QQ_APP_ID, PORT_*. [INTEGRATIONS] OpenClaw detection needs --with-openclaw; Cloudflare Tunnel needs --tunnel and an installed cloudflared binary."

metadata:
  version: "1.1.0"
  github: "https://github.com/ClawCross/ClawCross"
  ports:
    agent: 51200
    scheduler: 51201
    oasis: 51202
    frontend: 51209
  auth_methods:
    - "user_password"
    - "internal_token"
    - "channel_whitelist"
  integrations:
    - "openclaw"
    - "acpx"
    - "tinyfish"
    - "telegram"
    - "qq"
    - "cloudflare_tunnel"
---

# Clawcross — Agent Instructions

Use this file when you are an AI coding agent that needs to install, configure, run, operate, troubleshoot, or modify Clawcross.

## Progressive Disclosure

**Do NOT load all docs by default.** Follow this 3-layer protocol:

**Layer 0 — This file (AGENTS.md)**
- Behavior rules and deny invariants
- Task Router: which doc to open for the current task
- Repository indexing pointer

**Layer 1 — Task-specific docs**
- Install / configure / debug → [`SKILL.md`](./SKILL.md)
- Find the right doc for any other task → [`docs/index.md`](./docs/index.md)

**Layer 2 — Deep-dive references (open only when needed)**
- Codebase map → [`docs/repo-index.md`](./docs/repo-index.md)
- Topic docs under `docs/*.md` (CLI, OASIS, Teams, OpenClaw, etc.)

Use [`README.md`](./README.md) for product overview and user-facing positioning, not as the canonical operator reference.

## Task Router

Read only the docs relevant to the current task:

| Task | Read First | Then Read |
|---|---|---|
| Install / configure / start | [`SKILL.md`](./SKILL.md) | [`docs/ports.md`](./docs/ports.md) if ports matter |
| Understand what Clawcross is | [`docs/overview.md`](./docs/overview.md) | [`README.md`](./README.md) |
| Build a Team / use ClawCross Creator | [`docs/team-creator.md`](./docs/team-creator.md) | [`docs/build_team.md`](./docs/build_team.md) |
| OASIS / Town Mode / GraphRAG | [`docs/oasis-reference.md`](./docs/oasis-reference.md) | [`docs/create_workflow.md`](./docs/create_workflow.md) |
| Runtime architecture / auth | [`docs/runtime-reference.md`](./docs/runtime-reference.md) | [`docs/ports.md`](./docs/ports.md) |
| CLI commands | [`docs/cli.md`](./docs/cli.md) | `uv run src/cli/cli.py <cmd> --help` |
| Workflow YAML | [`docs/create_workflow.md`](./docs/create_workflow.md) | [`docs/example_team.md`](./docs/example_team.md) |
| OpenClaw integration | [`docs/openclaw-commands.md`](./docs/openclaw-commands.md) | [`docs/build_team.md`](./docs/build_team.md) |
| ACP / acpx | [`docs/runtime-reference.md`](./docs/runtime-reference.md) | [`docs/oasis-reference.md`](./docs/oasis-reference.md) |
| TinyFish internet search | [`docs/tinyfish-monitor.md`](./docs/tinyfish-monitor.md) | [`docs/runtime-reference.md`](./docs/runtime-reference.md) |
| Debug ports / proxies | [`docs/ports.md`](./docs/ports.md) | [`docs/repo-index.md`](./docs/repo-index.md) |

## Repository Indexing

Before touching code, read [`docs/repo-index.md`](./docs/repo-index.md). It maps:

- Runtime entrypoints (`src/backend/server.py`, `src/frontend/server.py`, `src/backend/scheduler/service.py`, `src/backend/oasis/server.py`)
- Install scripts (`launch/run.*`, `configure.py`)
- Frontend files, OASIS Town / GraphRAG files, ACP adapter
- Team / workflow data paths under `data/user_files/...`

If the task is narrow, inspect only the indexed files for that area instead of scanning the whole repo.

## Agent Rules

### Zero-Question Install Flow

The goal is a **silent, non-interactive install**. NEVER repeatedly ask the user for `LLM_API_KEY` or `LLM_BASE_URL` during installation. An empty `LLM_MODEL` allows the web UI to start; configure it in the UI before sending LLM requests. Set `CLAWCROSS_REQUIRE_LLM_MODEL=1` for strict startup validation.

1. Run **`start`** (or `start-foreground`). First run **does not** require a separate `setup`: `start` prepares Python 3.11, a virtual environment, and core Python dependencies when needed. Optional integrations such as `acpx`, NoneBot, WeClaw, cloudflared, and SRT require an explicit `install-component` command. You do **not** need `configure --init` before `start` — `start` creates `config/.env` from the template if missing. The `start` command:
   - Creates a default `.env` if missing
   - Warns if `LLM_MODEL` is empty; strict mode with `CLAWCROSS_REQUIRE_LLM_MODEL=1` stops before launching services
   - Detects and warms OpenClaw only with `--with-openclaw`
   - Imports OpenClaw LLM config only with `--with-openclaw` and only when ClawCross has no real API key
     (it will NOT overwrite a user-provided real `LLM_API_KEY`)
   - Starts locally by default; `--tunnel` uses an already installed cloudflared binary for remote access
   - Prints a local Magic link, plus a remote link when the explicit tunnel is ready
2. **Do NOT ask the user for API credentials before starting** unless `LLM_MODEL` discovery requires a configured provider/key. The frontend provides a first-login setup wizard where the user can configure API Key, provider, and model values.
3. After `start`, **always tell the user the access URLs** — copy them from the command output into your reply (do not assume the user saw the terminal):
   - Local web UI: `http://127.0.0.1:<PORT_FRONTEND>`
   - **Magic links** (passwordless): the script prints `🔗 Magic link` with **本机** `http://127.0.0.1:.../login-link/...?user=default` and, when Tunnel is ready, **远程** `https://...trycloudflare.com/login-link/...?user=default`. HTTPS / phone login needs the remote link; **you must relay both lines when present**.
   - Mobile message center: `<PUBLIC_DOMAIN>/mobile_group_chat` (after `PUBLIC_DOMAIN` is written to `config/.env`)
4. The first-login flow handles everything:
   - Passwordless localhost login
   - Setup wizard auto-appears if LLM not configured
   - One-click import from OpenClaw or Antigravity-Manager if detected

### General Rules

Do not add AI tool or model names as authors or `Co-authored-by` trailers. Preserve the user's configured Git identity; add co-authorship only when the user explicitly requests it.

4. Do not install or configure OpenClaw unless the user explicitly asks for it.
5. Cloudflare Tunnel requires `start --tunnel` or `start-tunnel` and an already installed cloudflared binary.
6. On Windows, prefer the PowerShell flow. Use WSL only if the user prefers it.
7. Audio settings should follow the detected LLM provider when left blank:
   - OpenAI: `TTS_MODEL=gpt-4o-mini-tts`, `TTS_VOICE=alloy`, `STT_MODEL=whisper-1`
   - Gemini: `TTS_MODEL=gemini-2.5-flash-preview-tts`, `TTS_VOICE=charon`
8. Never auto-retry a workflow because it looks stuck. Check `topics show` first, report the current status or error, and retry only after user confirmation.
9. Never let a sub-agent start a child workflow unless explicitly instructed.
10. Before adding an OpenClaw agent into a Team, always run `openclaw sessions` and confirm the target agent already exists.
11. On Windows PowerShell, prefer `openclaw.cmd` for channel and plugin commands.
12. For the Weixin plugin on Windows, fall back to manual plugin install with `openclaw.cmd` if the official installer fails.
13. If Clawcross LLM settings change after OpenClaw is installed, finish the provider/model selection before pushing config back into OpenClaw. Use `sync-openclaw-llm` when the desired LLM config is final.

## Reference Docs

- [`SKILL.md`](./SKILL.md) — complete install, config, debug, and troubleshooting guide
- [`docs/index.md`](./docs/index.md) — canonical task-based documentation map
- [`docs/repo-index.md`](./docs/repo-index.md) — codebase and file index
- [`docs/cli.md`](./docs/cli.md) — CLI command reference
- [`docs/ports.md`](./docs/ports.md) — service map and ports
