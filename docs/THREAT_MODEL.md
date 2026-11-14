# Lightweight threat model

| Threat | Example | Current mitigation | Remaining work |
|---|---|---|---|
| Cross-tenant access | User guesses invoice ID | Tenant filters on API queries | PostgreSQL RLS and integration tests |
| Malicious upload | Crafted PDF | Extension, size, signature checks | AV scan, content sniffing, sandbox parsing |
| Prompt injection | Invoice text says ignore policy | Document text not executed as instructions | Retrieval isolation, explicit prompt-injection tests |
| Tool abuse | Call arbitrary command | Allowlisted tool registry | Per-tool scopes, rate limits, security review |
| Model hallucination | Fabricated explanation | Model only explains deterministic evidence | Output validation and reviewer feedback |
| Credential theft | Leaked JWT | Short-lived JWT | Refresh rotation, revocation, MFA |
| Audit tampering | Admin edits history | Audit events in DB | WORM storage and hash chain |
| Duplicate action | Repeated approval request | Finalized status checks | Idempotency keys and concurrency locking |
