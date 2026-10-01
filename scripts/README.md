# Launch scripts

This directory contains launch and environment helpers only:

- `environment.py`: core environment and explicit optional component installation.
- `runtime_control.py`: shared cross-platform start, stop, status and tunnel commands.
- `launcher.py`: service process launcher.
- `tunnel.py`: optional Cloudflare tunnel launcher.
- `migrate_to_user_home.py`: migrate an existing runtime before startup.
- `clawcross`: model CLI launcher.
- `cli.py`: compatibility launcher for external agents whose initial instructions
  already contain the old command path. The implementation is in `src/cli/cli.py`.

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
