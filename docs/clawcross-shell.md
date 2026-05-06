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
npm run pack:clawcross-cli
```

Use a single prompt without entering the shell:

```bash
./clawcross run "check the current project"
./clawcross run -p codex "review the current diff"
```

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

`/session` opens a picker for sessions from the current platform. The first item is `<new session>`, which creates a fresh session and switches to it. `/new session` does the same directly.

`/use` opens a platform picker with every known platform. `/use <platform>` still switches directly.

```text
/use <platform>     switch platform, e.g. codex or internal
/session            choose a current-platform session
/session <id>       switch session by id
/new session        create and switch to a new session
/cwd [path]         show or change workspace directory
/mode <mode>        set execute, plan, or review label
/platforms          list known platforms
/state              print persisted state
/cancel             cancel current internal-agent generation
/help               list commands
/exit               quit
```

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

The repository package exposes a `clawcross` binary through `bin/clawcross.js`. The package file list is intentionally restricted to the shell wrapper, Python CLI, docs, license, and README so `npm pack` does not include runtime data, frontend bundles, logs, or binaries.

For a standalone CLI tarball without frontend dependencies, use the package under `npm/clawcross-cli`:

```bash
npm pack ./npm/clawcross-cli
npm install -g ./clawcross-cli-0.0.1.tgz
```
