---
name: xiaohongshu-mcp
description: ClawCross local skill wrapper for Xiaohongshu / RedNote operations through a running local xiaohongshu-mcp service.
source: local-clawcross-skill
---

# Xiaohongshu MCP Skill

Use this when the user wants Xiaohongshu / RedNote operations:

- publish image/text notes or video notes
- search notes and trends
- inspect note details and comments
- check login state and user/feed context

## Local Tool Model

This skill ships with a local Python wrapper. From this skill directory:

```bash
./run.sh status
./run.sh raw GET /health
./run.sh raw POST /some/path '{"json":"payload"}'
```

The wrapper expects a running Xiaohongshu service at:

```text
XIAOHONGSHU_MCP_URL=http://localhost:18060
```

If the service is not running, the skill returns a clear JSON error. It does not install or start third-party binaries by itself.

## Operating Rules

- Treat publish, comment, like, favorite, follow, delete, and account changes as live account actions requiring human confirmation.
- Prefer one test post or read-only search before any live publishing.
- Keep media paths explicit and verify file existence before publishing.
- Stop on login challenge, CAPTCHA, account risk warning, or abnormal platform response.
