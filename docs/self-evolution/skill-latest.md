# KEEP Y verification

Regression: KEEP Y permission was saved, but the generic metadata updater ignored used approvals.
Fix: record_approval_memory updates only a consumed approval and preserves its execution metadata.
Other fixes: scope remembered actions to the authenticated session; normalize permit arguments; retain the 40,000-character tool schema budget; locate the permission summary independently of its count.
Verification: 168 backend tests and 302 subtests passed in the focused batch; its 2 failed cases both passed after fixes. Three added boundary tests and 4 subtests passed. Three focused browser tests passed (the grant removal case was fixed and rerun).
Actual Landlock: persistent read cannot delete, another Agent requires review, ceiling/revocation removes access; remembered HTTPS destination works without a second review.
Actual configured reviewer: native JSON Schema returned KEEP Y with a valid original-user source.
