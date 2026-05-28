# ClawCross Shell CLI

`clawcross` is a Codex-style interactive shell for choosing and using ClawCross agent platforms from the command line.

## Start

```bash
./clawcross
```

Equivalent repo-local launcher:

```bash
bash selfskill/scripts/run.sh clawcross
```

PowerShell launcher:

```powershell
.\clawcross.ps1
.\selfskill\scripts\run.ps1 clawcross
```

NPM entrypoints:

```bash
npm run clawcross -- platforms
npm exec -- clawcross platforms
npm run pack:clawcross
```

Use a single prompt without entering the shell:

```bash
./clawcross run "check the current project"
./clawcross run -p codex "review the current diff"
```

Upgrade the globally installed npm package:

```bash
clawcross update
clawcross update 0.0.5
```

`clawcross update` only runs `npm install -g clawcross@<version-or-latest>`. It does not write the Web UI self-update status, poll logs, or restart running ClawCross services. Use the Web or mobile "Complete Update" action when you want the managed self-update flow that records status and restarts the service after updating.

## State

The shell persists its current platform/session/workspace in:

```text
~/.clawcross/state.json
```

Override the state directory for tests or isolated runs:

```bash
CLAWCROSS_STATE_DIR=/tmp/clawcross-state ./clawcross state
```

State records the current platform and remembers the last session per platform, so you do not need to pass `--platform` and `--session` repeatedly.

## Interactive Commands

The startup screen is intentionally short: version, Web UI, current platform/session/user, and working directory.

When `clawcross` is running in an interactive terminal, type `/` as the first character in the prompt to open a command picker. Use the up/down arrow keys to select a command and press Enter.

`/resume` opens a picker for sessions from the current platform. The first item is `<new session>`, which creates a fresh session and switches to it. `/session` remains as a legacy alias. `/new session` does the same directly.

`/use` opens a platform picker with every known platform. `/use <platform>` still switches directly.

```text
/use <platform>     switch platform, e.g. codex or internal
/resume             choose a current-platform session
/resume <id>        switch session by id
/new session        create and switch to a new session
/cwd [path]         show or change workspace directory
/mode <mode>        set execute, plan, or review label
/platforms          list known platforms
/state              print persisted state
/restart            request a backend restart
/cancel             cancel current internal-agent generation
/help               list commands
/exit               quit
```

`LLM_MODEL`、`LLM_PROVIDER`、`LLM_API_KEY`、`LLM_BASE_URL`、`NONEBOT_ADAPTERS`、`WHITELIST_FILE` 以及相关 `*_BOTS` 配置通常需要重启后才会生效；界面会在保存后给出提示。

`clawcross restart` 会在发起后继续等待服务恢复，恢复成功后会打印 `✅ 重启完成`。

Normal input is sent as a prompt to the current platform.

## Platforms

Known platforms:

```text
internal
openclaw
codex
claude
gemini
aider
cursor
copilot
droid
iflow
kilocode
kimi
kiro
opencode
pi
qoder
qwen
trae
acp
http
temp
openclaw:main
team:default
```

ACP-backed platforms route through the local `acpx` bridge and the frontend proxy. `acp`, `http`, and `temp` are generic connector targets; `openclaw:main` and `team:default` are namespace targets reserved for follow-up routing work.

## NPM Packaging

The repository is published as the `clawcross` npm package. `bin/clawcross.js` is the CLI entrypoint declared in the `bin` field. The package ships the full git-tracked source tree (frontend, agents, scripts, docs) so a global install behaves like a `git clone` of the project — minus everything excluded by `.gitignore` / `.npmignore` (virtual envs, logs, runtime data, large binaries such as `bin/cloudflared`).

Build a tarball from the repo root:

```bash
npm pack
npm install -g ./clawcross-0.0.5.tgz
```

`.npmignore` mirrors `.gitignore` and additionally drops `.git/`, `.github/`, `.pytest_cache/`, the cloudflared binary, and runtime logs so they never reach the registry.
