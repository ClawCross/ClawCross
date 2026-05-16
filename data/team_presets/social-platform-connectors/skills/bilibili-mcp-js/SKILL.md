---
name: bilibili-mcp-js
description: ClawCross local skill for Bilibili public search, popular list, video details, and UP profile research.
source: local-clawcross-skill
---

# Bilibili Toolkit

Use this for Bilibili research and intelligence gathering.

## Local Tool Model

This skill ships with a local Python script and uses only the Python standard library. From this skill directory:

```bash
./run.sh search "关键词"
./run.sh popular
./run.sh video BV1xxxx
./run.sh up 123456
```

## Capabilities

- Search Bilibili video summaries.
- Get popular lists.
- Get video details by BV or AV ID.
- Get UP creator profile, follower count, following count.

## Boundary

This is a research/search skill, not a Bilibili publishing connector. For uploading, comments, or account operations, use a dedicated official API path if available or escalate to browser automation with explicit human approval.
