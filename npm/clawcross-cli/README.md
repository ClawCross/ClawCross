# ClawCross CLI

Codex-style command-line shell for selecting and using ClawCross agent platforms.

```bash
clawcross
clawcross platforms
clawcross run -p codex "review the current diff"
```

State is stored in `~/.clawcross/state.json`. The CLI talks to a running local ClawCross service by default:

- agent API: `http://127.0.0.1:51200`
- frontend proxy: `http://127.0.0.1:51209`

Override those with `CLAWCROSS_AGENT_BASE` and `CLAWCROSS_FRONT_BASE`.
