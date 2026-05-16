---
name: x-api-publisher
description: ClawCross local skill for drafting and publishing tweets to X / Twitter through the official API using OAuth credentials.
source: local-clawcross-skill
---

# X API Publisher Skill

Use this when the user wants X / Twitter publishing through official API credentials from inside a ClawCross team.

## Required Environment

The account-posting credentials must be configured outside chat:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

These credentials allow posting as the account. Never print them, ask the user to paste them into chat, or send them to other domains.

## Tool Model

This skill ships with a local Python script and uses only the Python standard library. From this skill directory:

```bash
./run.sh dry-run "Your tweet text here"
./run.sh post "Your tweet text here"
```

## Rules

- Tweet max: 280 characters.
- Thread max: 25 tweets.
- Parse JSON output and check `success`.
- Prefer `dry-run` before `post`.
- Require human confirmation before live publishing, replying, quote tweeting, or deleting.
