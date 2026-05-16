---
name: meta-mcp-instagram-threads
description: ClawCross local skill for Instagram Graph API and Threads API checks/publishing helpers.
source: local-clawcross-skill
---

# Meta Instagram and Threads Skill

Use this for Instagram and Threads operations through the official Meta APIs.

## Local Tool Model

This skill ships with a local Python script and uses only the Python standard library. From this skill directory:

```bash
./run.sh check-instagram
./run.sh check-threads
./run.sh threads-dry-run "post text"
./run.sh threads-post "post text"
```

## Required Account Types

- Instagram requires a Business or Creator account for Graph API access.
- Threads API uses Threads access token and user ID.
- Required env: `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` depending on action.

## Capabilities

- Instagram: account check and API readiness check in this local skill.
- Threads: account check, dry-run, and simple text post helper in this local skill.
- For advanced media/Reels/Stories/DM flows, integrate a full Meta MCP server after security review.

## Rules

- Require human confirmation for posting, comment replies, DMs, deletes, token changes, and webhook changes.
- Verify media format and account type before promising execution.
- Never expose tokens in chat or logs.
