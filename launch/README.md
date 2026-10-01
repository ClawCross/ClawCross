# Launch scripts

This directory contains launch and environment helpers only:

- `environment.py`: core environment and explicit optional component installation.
- `runtime_control.py`: shared cross-platform start, stop, status and tunnel commands.
- `launcher.py`: service process launcher.
- `tunnel.py`: optional Cloudflare tunnel launcher.
- `migrate_to_user_home.py`: migrate an existing runtime before startup.
- `clawcross`: model CLI launcher.
- `cli.py`: thin CLI launcher. The implementation is in `src/cli/cli.py`.

Application CLIs live in `src/cli/`, Fleet utilities in `src/backend/fleet/`,
and monitoring/workflow/password tools in their backend modules. Development
build tools live in `tools/dev/`; examples live in `examples/`.

Startup is local by default. Public access requires an explicit command:

```bash
bash selfskill/scripts/run.sh start --tunnel
# Or add a tunnel to an already running service:
bash selfskill/scripts/run.sh start-tunnel
```

Install cloudflared separately with `install-component cloudflared` if missing.

## Startup order

1. `selfskill/scripts/run.sh` or `run.ps1` locates uv, installs it if missing,
   and creates the Python 3.11 virtual environment if missing. uv can download
   Python when a suitable interpreter is unavailable.
2. The platform entry hands control to `launch/runtime_control.py`.
3. The controller migrates legacy runtime data when needed, installs core
   dependencies only when needed, initializes configuration and checks it.
   OpenClaw integration requires `--with-openclaw`.
4. `launch/launcher.py` creates Scheduler, OASIS, Agent and frontend processes
   in that order, then waits for their readiness concurrently. Fleet and
   channel processes start only when their configuration is available.
5. For background `start --tunnel`, the controller starts an already installed
   cloudflared through `launch/tunnel.py`, then prints local and public Magic
   Links. Foreground startup does not start a tunnel.

Ordinary startup never installs cloudflared, acpx, NoneBot, WeClaw, SRT, Node
or Chromium. Installing core Python packages can download wheels containing
native libraries.
