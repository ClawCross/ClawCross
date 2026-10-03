# Cumulative command approval verification

Failure: old tests assumed one schema-output attempt and discarded original user messages above 16,000 characters; a schema check found missing parameter docs and an outdated private-field list.
Fix: profile fallback for workers without exported LLM variables; two attempts under native JSON Schema, wrapper parsing with strict validation; full original requests selected by actual reviewer token budget.
Command retry: up to eight scoped reviews in one call; each reviewer sees previous grants and decides on their union. Linked approvals bind command/user/session/workspace, survive pending human Y, and close at completion/denial. KEEP Y persists only the new scope.
Verification: focused batch 173 tests and 299 subtests passed; failed cases were fixed and verified by 4 targeted tests and 5 subtests. Seven real-chain/limit cases passed. Seven final material/audit cases and 3 subtests passed. Schema budget remains 40,000 characters.
Live verification: a worker with all LLM_* variables removed used the configured profile; real DeepSeek API approved two successive destinations. One real command executed three times, the second review saw the first grant, both approvals closed, and no temporary permission persisted.
