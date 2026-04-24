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

argument-hint: "[RECOMMENDED] LLM_API_KEY, LLM_BASE_URL (auto-detected from OpenClaw/Antigravity, or configured via frontend wizard on first login). [MODEL] If LLM_MODEL is not provided, the frontend setup wizard will auto-detect available models. [OPTIONAL] TTS_MODEL/TTS_VOICE, STT_MODEL/WHISPER_MODEL, OPENCLAW_*, TINYFISH_*, TELEGRAM_BOT_TOKEN/QQ_APP_ID, PORT_*. [TUNNEL] Cloudflare Tunnel starts automatically with 'start' command for mobile access; PUBLIC_DOMAIN is set by tunnel.py."

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
    - "chatbot_whitelist"
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
| CLI commands | [`docs/cli.md`](./docs/cli.md) | `uv run scripts/cli.py <cmd> --help` |
| Workflow YAML | [`docs/create_workflow.md`](./docs/create_workflow.md) | [`docs/example_team.md`](./docs/example_team.md) |
| OpenClaw integration | [`docs/openclaw-commands.md`](./docs/openclaw-commands.md) | [`docs/build_team.md`](./docs/build_team.md) |
| ACP / acpx | [`docs/runtime-reference.md`](./docs/runtime-reference.md) | [`docs/oasis-reference.md`](./docs/oasis-reference.md) |
| TinyFish internet search | [`docs/tinyfish-monitor.md`](./docs/tinyfish-monitor.md) | [`docs/runtime-reference.md`](./docs/runtime-reference.md) |
| Debug ports / proxies | [`docs/ports.md`](./docs/ports.md) | [`docs/repo-index.md`](./docs/repo-index.md) |

## Repository Indexing

Before touching code, read [`docs/repo-index.md`](./docs/repo-index.md). It maps:

- Runtime entrypoints (`src/mainagent.py`, `src/front.py`, `src/utils/scheduler_service.py`, `oasis/server.py`)
- Install scripts (`selfskill/scripts/run.*`, `configure.py`)
- Frontend files, OASIS Town / GraphRAG files, ACP adapter
- Team / workflow data paths under `data/user_files/...`

If the task is narrow, inspect only the indexed files for that area instead of scanning the whole repo.

## Agent Rules

### Zero-Question Install Flow

The goal is a **silent, non-interactive install**. NEVER repeatedly ask the user for `LLM_API_KEY` or `LLM_BASE_URL` during installation. `LLM_MODEL` is required by startup; if it is empty, configure a model (or run `auto-model` after provider/key values exist) before starting. For shell-only diagnostics, a single run may set `CLAWCROSS_ALLOW_EMPTY_LLM_MODEL=1`.

1. Run **`start`** (or `start-foreground`). First run **does not** require a separate `setup`: `start` **runs the same environment bootstrap as `setup` when needed** (venv, Python deps, and on Linux/macOS `acpx` when `npm` is available). You do **not** need `configure --init` before `start` — `start` creates `config/.env` from the template if missing. The `start` command:
   - Creates a default `.env` if missing
   - Stops early with a clear message if `LLM_MODEL` is empty, unless `CLAWCROSS_ALLOW_EMPTY_LLM_MODEL=1` is set
   - Automatically warms an installed OpenClaw gateway and refreshes runtime `OPENCLAW_*` values
   - If `config/.env` has no real `LLM_API_KEY` (missing/placeholder), it **tries** to import OpenClaw LLM config into Clawcross (optional; **failure does not block startup**)
     (it will NOT overwrite a user-provided real `LLM_API_KEY`)
   - **Automatically starts Cloudflare Tunnel** for mobile remote access and prints the public URL
   - After Tunnel (or when it is still starting), prints **Magic link** lines (local + remote when `PUBLIC_DOMAIN` is ready)
2. **Do NOT ask the user for API credentials before starting** unless `LLM_MODEL` discovery requires a configured provider/key. The frontend provides a first-login setup wizard where the user can configure API Key and provider values — but startup itself requires `LLM_MODEL`.
3. After `start`, **always tell the user the access URLs** — copy them from the command output into your reply (do not assume the user saw the terminal):
   - Local web UI: `http://127.0.0.1:<PORT_FRONTEND>`
   - **Magic links** (passwordless): the script prints `🔗 Magic link` with **本机** `http://127.0.0.1:.../login-link/...?user=default` and, when Tunnel is ready, **远程** `https://...trycloudflare.com/login-link/...?user=default`. HTTPS / phone login needs the remote link; **you must relay both lines when present**.
   - Mobile message center: `<PUBLIC_DOMAIN>/mobile_group_chat` (after `PUBLIC_DOMAIN` is written to `config/.env`)
4. The first-login flow handles everything:
   - Passwordless localhost login
   - Setup wizard auto-appears if LLM not configured
   - One-click import from OpenClaw or Antigravity-Manager if detected

### General Rules

4. Do not install or configure OpenClaw unless the user explicitly asks for it.
5. Cloudflare Tunnel is started **automatically** by `start`. No manual `start-tunnel` needed.
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
