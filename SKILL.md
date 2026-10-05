---
name: clawcross
description: Complete operator manual for installing, configuring, starting, debugging, and troubleshooting Clawcross across Linux, macOS, Windows, OpenClaw integration, ACP tools, and runtime operations.
metadata:
  short-description: Install, run, and troubleshoot Clawcross
---

# Clawcross — Install, Configure & Debug Guide

> This is the complete operator manual for installing, configuring, running, and troubleshooting Clawcross.
> For agent behavior rules and task routing, see [`AGENTS.md`](./AGENTS.md).
> For the product overview, see [`README.md`](./README.md).

---

## For AI agents that read this SKILL

**Mandatory before starting services**

1. After you have read this file (or the sections needed for the user’s task), you **must ask the user** whether they want to **start Clawcross** now (background `start` or foreground `start-foreground`), and **wait for their answer** before running those commands.
2. **Exception:** if the user already clearly asked to start in the same turn (e.g. “帮我启动 Clawcross”“run start”“执行 start”), you may run `start` / `start-foreground` without asking again.
3. When Clawcross **is** started, copy the **local URL**, Magic link lines, and (if present) **remote** Tunnel URL from the terminal output into your reply — see [`AGENTS.md`](./AGENTS.md) Zero-Question Install Flow.

### Self-Evolution Protocol

This `SKILL.md` is now **self-evolving**. When you hit a real execution failure while operating this repo, do not just work around it silently. Capture the failure and refresh the managed self-evolution block in this document:

```bash
# Linux / macOS
bash launch/run.sh evolve-skill --skill SKILL.md --command "pytest test/test_skill_evolution.py" --strategy harden

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File launch/run.ps1 evolve-skill --skill SKILL.md --command "pytest test/test_skill_evolution.py" --strategy harden
```

Rules:

1. Run the command **only when there is a meaningful execution error** (failed test, startup failure, CLI/runtime error), or pass `--force` if you intentionally want to refresh the verified guidance after a successful command.
2. Supported strategy presets are `auto`, `balanced`, `innovate`, `harden`, and `repair-only`. Use `harden` after flaky runtime failures, `repair-only` during active breakage, and `innovate` only when the issue is really a missing capability.
3. The command writes a managed self-evolution block into this file, stores a Markdown report under `docs/self-evolution/`, and now also emits a machine-readable validation report JSON beside it.
4. After fixing the issue, re-run the narrowest verifier command; if it still fails, refresh the block again so the latest failure evidence is preserved.
5. Prefer exact failing commands and stderr over vague summaries. This file should accumulate operational guardrails, not generic advice.

---

## Standard Install Flow

### Quick Start (Zero Questions)

The simplest path is `start`. It creates the runtime `.env` when needed. An empty `LLM_MODEL` allows the web UI to start, but LLM requests need a model configured in the first-login wizard. Set `CLAWCROSS_REQUIRE_LLM_MODEL=1` if strict startup validation is wanted.

**How many commands?**

| Situation | Typical commands |
|---|---|
| **Fresh machine / first clone** | **One:** **`start`** only. It prepares Python 3.11, a venv, and core Python dependencies when needed. |
| **Fresh machine with empty `LLM_MODEL`** | **One:** `start`, then configure the model in the first-login wizard. |
| **Optional** | `setup` — 仅当你想**单独**重装/检查环境时；日常不必先跑。 |

`start` automatically runs the equivalent of `configure --init` when `config/.env` is missing, so you **do not** need a separate `configure --init` unless you want to create or inspect `.env` before launching.

```bash
# Linux / macOS
bash launch/run.sh start          # 准备 Python/venv/核心依赖，初始化 .env，启动服务
# Optional flags (same semantics as Windows run.ps1):
#   --tunnel         Use an already installed cloudflared binary for a public tunnel.
#   --with-openclaw  Import LLM settings from an existing OpenClaw install when ClawCross has no key.
bash launch/run.sh start --tunnel --with-openclaw   # explicit integrations
# → Open http://127.0.0.1:51209 (or use the printed Magic link; remote/HTTPS needs the remote link)
# → First login: Magic link or passwordless localhost
# → Setup wizard appears if LLM is not yet configured in Clawcross
```

```powershell
# Windows PowerShell（入口脚本只负责准备 Python；后续交给共享 Python 控制器）
powershell -ExecutionPolicy Bypass -File launch/run.ps1 start
# The same opt-in flags work on Windows. Foreground mode does not start a tunnel.
powershell -ExecutionPolicy Bypass -File .\launch\run.ps1 start --tunnel --with-openclaw
```

The `setup` command (optional standalone) automatically:
1. Installs `uv` package manager if missing
2. Creates a Python 3.11+ virtual environment
3. Installs Python dependencies from `config/requirements.txt`
4. Reports optional integrations with `components`; none are downloaded by `setup`.

Install external integrations explicitly, only when the feature is needed:

```bash
bash launch/run.sh components
bash launch/run.sh install-component acpx
bash launch/run.sh install-component nonebot --adapter telegram
bash launch/run.sh install-component channels
bash launch/run.sh install-component weclaw
bash launch/run.sh install-component cloudflared
bash launch/run.sh install-component srt          # optional command sandbox
```

Use the same subcommands with `launch/run.ps1` on Windows. `channels` installs legacy QQ/Telegram and media packages; NoneBot adapters are installed separately. `acpx`, WeClaw, and cloudflared are placed under `CLAWCROSS_BIN_DIR` when installed through this interface. `start` may use a cloudflared binary already present on the machine, but never downloads it.
SRT is also explicit and stays off until a session selects `command_sandbox=srt`. On Linux it needs `bwrap`, `socat`, and `rg`; on macOS it needs `rg`. Windows additionally requires the separately elevated `srt windows-install` setup.

The `start` command automatically:
1. **When needed**, creates the Python environment through the platform wrapper, then installs only `config/requirements.txt` from Python. Optional integrations are installed only through `install-component`.
2. Creates `config/.env` from template if missing
3. Imports LLM fields from an existing OpenClaw installation (read-only) only with `--with-openclaw` and only when the local key is empty or placeholder.
4. Warns if `LLM_MODEL` is missing; set `CLAWCROSS_REQUIRE_LLM_MODEL=1` to require one before launching services
5. Starts all services after the model check passes
6. Prints a local Magic link. With `--tunnel`, it also starts an already installed Cloudflare Quick Tunnel and prints a remote link when available. Startup never downloads cloudflared.

After startup, the frontend setup wizard handles remaining LLM configuration via the web UI. The wizard detects local OpenClaw and Antigravity-Manager and offers one-click import buttons.

### `start` / `start-foreground` flags (`run.sh` and `run.ps1` aligned)

| Flag | When to use | Behavior |
|------|-------------|----------|
| **`--tunnel`** | Public access is explicitly requested. | Background `start` uses an already installed cloudflared binary. Foreground mode remains local. |
| **`--with-openclaw`** | The user wants ClawCross to reuse OpenClaw's LLM settings. | Reads OpenClaw's config into `config/.env` while ClawCross has no key; never writes OpenClaw. |
| **`--no-tunnel`, `--no-openclaw`** | Older scripts or automation still pass these flags. | Accepted for compatibility; they preserve the local-only default. |

Environment variable (for advanced/manual launcher runs): **`CLAWCROSS_NO_TUNNEL`** may be set to `1` / `true` / `yes` / `on` where documented; scripts set them when the flags above are used.

### For agents using this SKILL (settings are documented — startup does not enforce them)

Follow **[For AI agents that read this SKILL](#for-ai-agents-that-read-this-skill)** before running `start`. Use the rest of this file when the user **wants** to configure something: **OpenClaw**, **Advanced: Manual CLI Configuration** (`configure`, `auto-model`), **Magic link** / `add-user`, provider tables, etc. **None of that blocks `start`:** the stack comes up with a template `.env`; the human signs in and finishes LLM/account choices in the UI or CLI when ready.

### Optional: auto-import OpenClaw LLM at startup

With `--with-openclaw`, startup may import provider/model/key from an existing OpenClaw installation when ClawCross has no real `LLM_API_KEY`. Ordinary startup does not probe OpenClaw.

### Magic Prompts for AI Code CLI

After the first login, users can send these prompts to their AI code CLI agent:

- `阅读SKILL帮我安装并配置AntiGravity`
- `帮我自动选择目前能用的最好的LLM模型`

---

## OpenClaw (Optional)

OpenClaw is an ACP agent like Codex: ClawCross reaches it through acpx (`openclaw acp`) and never changes OpenClaw's own configuration. It needs `install-component acpx` and an `openclaw` CLI whose gateway runs; install or configure OpenClaw itself only when the user explicitly asks, with OpenClaw's own documentation. See [docs/openclaw-commands.md](./docs/openclaw-commands.md).

### Provider Switching Notes

- **DeepSeek**: Update Clawcross `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_PROVIDER` together. Tested stable pair: Clawcross `LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-chat`, `LLM_PROVIDER=deepseek`.
- **Antigravity-Manager** (local reverse proxy, free 67+ models via Google One Pro): `LLM_BASE_URL=http://127.0.0.1:8045`, `LLM_API_KEY=sk-antigravity`, `LLM_MODEL=gemini-3.1-pro`, `LLM_PROVIDER=antigravity`.
- **MiniMax** (1M context): `LLM_BASE_URL=https://api.minimaxi.com`, `LLM_MODEL=MiniMax-M2.7`, `LLM_PROVIDER=minimax`.

---

## ACP Tools Integration (Optional)

Clawcross communicates with external AI coding agents via **acpx** (ACP exchange). Install `acpx` explicitly when you need ACP agents. Each tool below is an independent CLI agent that acpx can bridge — install only the ones the user wants.

**Prerequisite for all:** `acpx` must be installed with `bash launch/run.sh install-component acpx` (Windows: `run.ps1 install-component acpx`). This installs it under the ClawCross runtime directory rather than globally.

After installing any tool below, **restart Clawcross** so the switcher bar picks it up. Verify with:

```bash
bash launch/run.sh components  # includes acpx availability
# or in browser: the switcher bar in ClawCross Studio shows available ACP tabs
```

### Codex (OpenAI)

OpenAI's terminal coding agent.

- **Prerequisites:** Node.js >= 22, OpenAI API key
- **Install:** `npm install -g @openai/codex`
- **Configure:**
  ```bash
  export OPENAI_API_KEY="sk-..."
  ```
- **Verify:** `codex --help`
- **Repo:** https://github.com/openai/codex

### Claude Code (Anthropic)

Anthropic's official CLI agent.

- **Prerequisites:** Node.js >= 18 (macOS / Linux / Windows WSL2)
- **Install:** `npm install -g @anthropic-ai/claude-code`
- **Configure (pick one):**
  - Interactive login: run `claude`, it opens browser auth on first launch
  - Environment variable:
    ```bash
    export ANTHROPIC_API_KEY="sk-ant-..."
    ```
- **Verify:** `claude --version`
- **Docs:** https://docs.anthropic.com/en/docs/claude-code/overview

### Gemini CLI (Google)

Google's terminal coding agent.

- **Prerequisites:** Node.js >= 20
- **Install (pick one):**
  ```bash
  npm install -g @google/gemini-cli
  # or
  brew install gemini-cli
  ```
- **Configure (pick one):**
  - Google OAuth (free tier, no key needed): run `gemini`, select "Sign in with Google"
  - API key:
    ```bash
    export GEMINI_API_KEY="your-key-here"
    ```
- **Verify:** `gemini --version`
- **Repo:** https://github.com/google-gemini/gemini-cli
- **Docs:** https://geminicli.com/docs/

### Aider

AI pair-programming CLI, supports multiple LLM providers.

- **Prerequisites:** Python >= 3.10, Git
- **Install:**
  ```bash
  python -m pip install aider-install && aider-install
  # or directly:
  pip install aider-chat
  ```
- **Configure (set the key for your provider):**
  ```bash
  export OPENAI_API_KEY="sk-..."       # OpenAI
  export ANTHROPIC_API_KEY="sk-ant-..."  # Anthropic
  # or use: aider --model deepseek --api-key deepseek=<key>
  ```
- **Verify:** `aider --version`
- **Repo:** https://github.com/Aider-AI/aider

### OpenCode

Open-source terminal TUI coding agent.

- **Prerequisites:** API key for your LLM provider; modern terminal (WezTerm / Alacritty / Kitty recommended)
- **Install (pick one):**
  ```bash
  curl -fsSL https://opencode.ai/install | bash
  # or
  npm install -g opencode-ai
  # or
  brew install anomalyco/tap/opencode
  ```
- **Verify:** `opencode --version`
- **Repo:** https://github.com/opencode-ai/opencode

### Kiro (AWS)

AWS's AI coding agent, CLI + IDE.

- **Prerequisites:** macOS or Linux; AWS Builder ID (or Google / GitHub login)
- **Install:**
  ```bash
  curl -fsSL https://cli.kiro.dev/install | bash
  ```
- **Verify:** `kiro --version`
- **Docs:** https://kiro.dev/cli/

### Copilot CLI (GitHub)

GitHub Copilot's standalone terminal agent.

- **Prerequisites:** Node.js >= 22; active GitHub Copilot subscription
- **Install (pick one):**
  ```bash
  npm install -g @github/copilot
  # or
  brew install copilot-cli
  ```
  Windows: `winget install GitHub.Copilot`
- **Verify:** `copilot --version`
- **Docs:** https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli

### Cursor CLI

Cursor's standalone terminal agent (Cursor 3+).

- **Prerequisites:** Cursor account / subscription
- **Install:**
  ```bash
  curl https://cursor.com/install -fsS | bash
  ```
- **Verify:** `cursor --version`
- **Docs:** https://cursor.com/cli

### Trae Agent (ByteDance)

ByteDance's open-source CLI coding agent (separate from Trae IDE).

- **Prerequisites:** Python >= 3.12, UV package manager, API key (OpenAI / Anthropic / Gemini)
- **Install:**
  ```bash
  git clone https://github.com/bytedance/trae-agent.git
  cd trae-agent
  uv sync --all-extras
  source .venv/bin/activate
  ```
- **Verify:** `trae-agent --help`
- **Repo:** https://github.com/bytedance/trae-agent

### Quick Reference Table

| Tool | Install | Key env var | Free? |
|---|---|---|---|
| Codex | `npm i -g @openai/codex` | `OPENAI_API_KEY` | API key required |
| Claude Code | `npm i -g @anthropic-ai/claude-code` | `ANTHROPIC_API_KEY` | API key required |
| Gemini CLI | `npm i -g @google/gemini-cli` | `GEMINI_API_KEY` or OAuth | Free tier (OAuth) |
| Aider | `pip install aider-chat` | Provider-specific | BYO API key |
| OpenCode | `npm i -g opencode-ai` | Provider-specific | BYO API key |
| Kiro | `curl -fsSL https://cli.kiro.dev/install \| bash` | AWS Builder ID | Free tier |
| Copilot CLI | `npm i -g @github/copilot` | Copilot subscription | Paid |
| Cursor CLI | `curl https://cursor.com/install -fsS \| bash` | Cursor subscription | Paid |
| Trae Agent | `git clone` + `uv sync` | Provider-specific | BYO API key |

> **Note:** `acpx` auto-discovers installed tools. You do not need to register tools manually — just install them and restart Clawcross.

---

## Advanced: Manual CLI Configuration

For users who prefer CLI over the web UI, or for automation scripts:

```bash
# Linux / macOS
bash launch/run.sh configure LLM_API_KEY sk-xxx
bash launch/run.sh configure LLM_BASE_URL https://api.example.com
bash launch/run.sh auto-model
bash launch/run.sh configure LLM_MODEL <model>
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File launch/run.ps1 configure LLM_API_KEY sk-xxx
powershell -ExecutionPolicy Bypass -File launch/run.ps1 configure LLM_BASE_URL https://api.example.com
powershell -ExecutionPolicy Bypass -File launch/run.ps1 auto-model
powershell -ExecutionPolicy Bypass -File launch/run.ps1 configure LLM_MODEL <model>
```

For managed terminals, CI, or agent runners that clean up child processes, use `start-foreground` instead of `start`.

### Windows WSL Fallback

Use WSL only when the user wants it or native PowerShell is not suitable.

- Install WSL in an elevated PowerShell window: `wsl --install -d Ubuntu`
- Prefer a Linux-side copy of the repo instead of running directly from `/mnt/c/...`
- Keep WSL and native Windows installs on separate copies and separate ports

---

## Configuration Reference

### Recommended Keys

These keys are recommended but **not required before first start**:

| Key | Purpose |
|---|---|
| `LLM_API_KEY` | Provider API key |
| `LLM_BASE_URL` | OpenAI-compatible base URL |
| `LLM_MODEL` | Model name chosen explicitly or after `auto-model` |

If left blank, Clawcross starts normally but LLM-dependent features won't work until configured via the web UI setup wizard.

### Optional Audio Configuration

| Key | Purpose |
|---|---|
| `TTS_MODEL` | Text-to-speech model |
| `TTS_VOICE` | Voice preset |
| `STT_MODEL` | Speech-to-text model |

Blank values follow the current LLM provider automatically.

---

## Startup Expectations

After `start`, these services should come up:

| Service | Port variable | Default |
|---|---|---|
| Agent | `PORT_AGENT` | `51200` |
| Scheduler | `PORT_SCHEDULER` | `51201` |
| OASIS | `PORT_OASIS` | `51202` |
| Frontend | `PORT_FRONTEND` | `51209` |

Useful checks:

- `bash launch/run.sh status` / `run.ps1 status`
- `GET http://127.0.0.1:<PORT_AGENT>/v1/models`
- Open `http://127.0.0.1:<PORT_FRONTEND>`

Notes:

- On Windows, default ports may be auto-remapped; always trust `config/.env` or `status`.
- Local `127.0.0.1` access supports passwordless login; **non-localhost / HTTPS** access uses the **magic link** from `start`, `status`, `tunnel-status`, or `start-tunnel` (not `cli.py status` alone).
- Clawcross starts even without LLM configured. The setup wizard prompts on first login.
- `src/backend/chatbot/setup.py` requires an interactive terminal. In non-interactive contexts, `launcher.py` automatically skips the chatbot menu. Force with `WEBOT_HEADLESS=1`.

**Mandatory for anyone guiding a user after `start`:** Reproduce or summarize the **Magic link** block (local + remote when available). Do not end the handoff with only “open localhost” if the user needs phone or HTTPS access.

---

## Common Operations

### Runtime

```bash
bash launch/run.sh status
bash launch/run.sh stop
bash launch/run.sh configure --show
```

**Magic link** (local + remote when Tunnel is ready) is printed by **`run.sh` / `run.ps1`** after `start` (once Tunnel has run), and again by **`status`**, **`tunnel-status`**, and **`start-tunnel`** — each uses `cli.py token generate` so the HMAC token is correct. It is **not** part of `uv run src/cli/cli.py status`. Those commands also print a line **directed at AI assistants** asking them to copy the URLs into the user reply.

**Magic link user id** defaults to **`default`** (the `user_id` in `?user=` and in `token generate -u`). To generate links for another user, set **`CLAWCROSS_MAGIC_LINK_USER`** before running the script (Linux/macOS: `export CLAWCROSS_MAGIC_LINK_USER=admin`). Note: CLI chat defaults to `admin` for `-u`; magic link scripts intentionally used `default` unless you override.

```powershell
powershell -ExecutionPolicy Bypass -File launch/run.ps1 status
powershell -ExecutionPolicy Bypass -File launch/run.ps1 stop
powershell -ExecutionPolicy Bypass -File launch/run.ps1 configure --show
```

### CLI

```bash
uv run src/cli/cli.py --help
uv run src/cli/cli.py teams --help
uv run src/cli/cli.py workflows --help
uv run src/cli/cli.py skill --help   # managed 技能 list/show/new/edit/delete
uv run src/cli/cli.py cron --help    # 定时任务 list/new/delete
```

### Workflow Monitoring

Prefer non-blocking checks:

```bash
uv run src/cli/cli.py topics show --topic-id <ID>
```

Avoid `topics watch` or `workflows conclusion` when you need a quick status snapshot.

### Team Data Layout

Team-specific data lives under:

```text
data/user_files/{user_id}/teams/{team_name}/
```

See [docs/repo-index.md](./docs/repo-index.md) and [docs/example_team.md](./docs/example_team.md).

---

## Debug Guide

When a startup/test/CLI command fails while following this guide, refresh the managed self-evolution block first:

```bash
bash launch/run.sh evolve-skill --skill SKILL.md --command "<failing command>"
```

### Python 2 vs Python 3

On macOS, the system `python` may point to **Python 2.7**. Clawcross requires **Python 3.11+**.

**Symptom**: `SyntaxError: Non-ASCII character '\xe5'`

**Fix** (in order of preference):

1. Always use the canonical startup: `bash launch/run.sh start`
2. Activate the venv first: `source .venv/bin/activate && python launch/launcher.py`
3. Use the venv python directly: `.venv/bin/python launch/launcher.py`

**Never** run `python3 src/frontend/server.py` directly.

Safety guards: `launcher.py` includes a Python version check and `run.sh` verifies after venv activation.

### EOFError on Startup

**Symptom**: `EOFError: EOF when reading a line` from `src/backend/chatbot/setup.py`

**Cause**: Non-interactive terminal (agent runners, CI, piped scripts).

**Fix**: Use `launch/run.sh start` (which backgrounds `launcher.py` correctly), or set `WEBOT_HEADLESS=1`.

### Clawcross API Returns "认证失败"

**Symptom**: Direct POST to `http://127.0.0.1:<PORT_AGENT>/v1/chat/completions` returns auth error.

**Cause**: Clawcross's Agent API is authenticated. This doesn't mean LLM config is wrong.

**Fix**: Verify the stack with `run.sh status` or test the LLM directly:

```bash
uv run python -c "
from dotenv import load_dotenv; load_dotenv('config/.env')
from src.services.llm_factory import create_chat_model, extract_text
resp = create_chat_model(max_tokens=8).invoke('Reply with OK only.')
print(extract_text(resp.content))
"
```

### Ports Conflict or Auto-Remapped

**Symptom**: Services don't start or wrong port in browser.

**Fix**: On Windows, ports may auto-remap. Always trust `config/.env` or `status` output, not hardcoded ports. See [docs/ports.md](./docs/ports.md) for the complete service map.

---

## Reference Docs

- [AGENTS.md](./AGENTS.md) — agent behavior rules and task router
- [docs/index.md](./docs/index.md) — canonical task-based docs map
- [docs/repo-index.md](./docs/repo-index.md) — codebase and file index
- [docs/overview.md](./docs/overview.md) — product overview
- [docs/team-creator.md](./docs/team-creator.md) — ClawCross Creator flow
- [docs/oasis-reference.md](./docs/oasis-reference.md) — OASIS runtime and orchestration
- [docs/runtime-reference.md](./docs/runtime-reference.md) — architecture and auth
- [docs/webot-agent-runtime.md](./docs/webot-agent-runtime.md) — WeBot subagents and profiles
- [docs/cli.md](./docs/cli.md) — CLI reference
- [docs/build_team.md](./docs/build_team.md) — Team creation and member config
- [docs/create_workflow.md](./docs/create_workflow.md) — workflow YAML format
- [docs/example_team.md](./docs/example_team.md) — example Team files
- [docs/openclaw-commands.md](./docs/openclaw-commands.md) — OpenClaw as an ACP agent
- [docs/tinyfish-monitor.md](./docs/tinyfish-monitor.md) — TinyFish monitor
- [docs/ports.md](./docs/ports.md) — service map and ports

<!-- clawcross:self-evolution:begin -->
## Self-Evolution Loop

This block is auto-maintained by ClawCross's lightweight EvoSkill adapter.
Read `skill_evolution_report`, then update the entry with `write_file(storage='memory')`; repository operators can use `tools/maintenance/evolve_skill.py`.

- Updated at: `2026-10-05T08:46:57.695060+00:00`
- Strategy: `harden`
- Heuristic candidate: `blended-verification-loop-approval-auth-workspace-preflight`
- Heuristic score: `0.652`

### Trigger Summary

Command exited with code 1. Command: (external failure context). Signals: verification-loop, approval-auth, workspace-preflight. stdout was used as fallback failure evidence.

### Strategy Rationale

- Intent mix: repair `0.4`, optimize `0.4`, innovate `0.2`
- Shift toward stability, bounded retries, and verifier quality.

### Latest Trigger Command

`(external failure context)`

### Latest Error Excerpt

```text
..F........................................................... [ 38%]
.....................................................................................................                   [100%]
=================================== FAILURES ===================================
___ test_all_selected_roots_are_available_to_files_but_strict_denies_outside ___

setup = (<agents.store.AgentStore object at 0x7526577aa0d0>, <teams.store.TeamStore object at 0x752657077c50>)
tmp_path = PosixPath('/tmp/pytest-of-ubuntu/pytest-30/test_all_selected_roots_are_av0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x752657582810>

    def test_all_selected_roots_are_available_to_files_but_strict_denies_outside(setup, tmp_path, monkeypatch):
        store, _ = setup
        other = tmp_path / 'second'; other.mkdir(); inside = other / 'inside.txt'; inside.write_text('INSIDE')
        outside = tmp_path / 'outside.txt'; outside.write_text('OUTSIDE')
        agent = store.create('alice',driver=WEBOT,config={'workspaces':workspace.normalize_workspace_config({'paths':[str(other)]})})
        state = workspace.resolve_session_workspace('alice',agent.agent_id)
        bound = bind_file_target('read_file', {'filename':str(inside)},'alice',agent.agent_id,workspace=state)
        assert not file_target_outside_workspace(bound)
        monkeypatch.setattr('webot.runtime_settings.get_runtime_settings',lambda *_:SimpleNamespace(approval=SimpleNamespace(sandbox_security='strict', mode='bypass')))
        from webot.approval_actions import file_access_violation
        forbidden = bind_file_target('read_file', {'filename':str(outside)},'alice',agent.agent_id,workspace=state)
        assert file_access_violation(forbidden,'alice',agent.agent_id)
>       assert asyncio.run(filemanager.read_file('alice',str(inside),session_id=agent.agent_id)) == 'INSIDE'
E       assert "📄 文件 '/tmp/p...: 6

INSIDE" == 'INSIDE'
E
E         + 📄 文件 '/tmp/pytest-of-ubuntu/pytest-30/t ...[truncated]
```

### Governance Snapshot

- Suppressed signals: (none)
- Consecutive repair cycles: `0`
- Consecutive empty cycles: `0`
- Recent failure ratio: `1.0`

### Operating Adjustments

1. Start from the narrowest reproducible failure before broad retries.
2. Record repo/cwd/entrypoint assumptions explicitly when failures mention paths or imports.
3. End every fix attempt with an explicit verifier command and observed result.

### Validation Loop

1. Run the minimal reproducer first, then the broader regression command.
2. Persist the command and result summary in the evolution report.

### Recent Evidence

- `2026-10-05T08:46:57.695060+00:00` `repo-skill` — ..F........................................................... [ 38%]
........................................................................ ...[truncated]

### Candidate Frontier Snapshot

- `blended-verification-loop-approval-auth-workspace-preflight` score `0.652` — Blend the strongest recent failure patterns (intent `repair`)
- `verification-loop-4` score `0.612` — Tighten verification loops (intent `repair`)
- `approval-auth-2` score `0.515` — Preflight auth and approval constraints (intent `repair`)
- `workspace-preflight-1` score `0.467` — Add repo/workspace preflight checks (intent `repair`)

### Local State Snapshot

- Python/platform: `3.11.16` / `Linux-7.0.0-14-generic-x86_64-with-glibc2.43`
- Feedback history entries: `0`
- Runtime failure entries: `0`
<!-- clawcross:self-evolution:end -->
