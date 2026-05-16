---
name: browser-automation-fallback
description: Use browser automation only as a fallback for social platforms when official APIs or MCP servers cannot cover the task.
source: https://www.skillavatars.com/skills/fastest-browser-use
---

# Browser Automation Fallback

Use this when the task needs JavaScript rendering, authenticated sessions, complex DOM interaction, or manual UI flows that are not covered by an official API or platform-specific MCP.

## Good Uses

- Inspect a logged-in dashboard with a separate browser profile.
- Prepare a post in a web UI and stop before final publish.
- Extract visible public data from JavaScript-heavy pages.
- Capture screenshots for human review.
- Coordinate a few tabs for comparison or evidence collection.

## Bad Uses

- Bypassing platform safeguards, CAPTCHA, account restrictions, or rate limits.
- Bulk likes/follows/comments.
- Scraping private data without authorization.
- Making irreversible account changes without human confirmation.

## Operating Rules

- Prefer isolated browser profiles over the user's personal browser.
- Run read-only inspection first.
- Stop on CAPTCHA, login challenge, 2FA, account risk warning, payment prompt, or irreversible action.
- Use screenshots or snapshots before live actions.
- Require human confirmation for final publish, delete, DM, comment, follow/unfollow, settings changes, and payment-related actions.
