# LedgerGate threat model

Last reviewed: 2026-11-16

## Scope and security boundary

LedgerGate accepts invoices and supporting records, extracts candidate fields, reconciles them deterministically against purchase orders, and routes exceptions and approvals to authorized humans. AI explanations and retrieval are advisory. The application does not initiate payments, hold funds, or autonomously approve financial transactions.

This is an application-level threat model, not an independent assessment of the deployed cloud environment.

## Assets

- Invoice files, extracted fields, purchase orders, supplier records, and approval decisions.
- Tenant membership, authentication credentials, refresh sessions, MFA secrets, and recovery tokens.
- Approval policies, policy snapshots, idempotency records, and agent memories.
- Append-only audit events, per-tenant hash chains, signed export artifacts, and signing keys.
- Encrypted email-outbox payloads containing account-verification and password-reset links.
- Database credentials, storage identities, SMTP credentials, encryption keys, and deployment secrets.

## Trust boundaries

1. Browser to public edge and same-origin frontend/API.
2. API authentication and authorization to tenant-scoped PostgreSQL transactions.
3. API to private object storage and ClamAV scanning service.
4. API to optional AI/model services; model output is untrusted advisory data.
5. Email worker to SMTP provider.
6. Audit exporter to separately administered S3 Object Lock storage.
7. CI and release automation to production deployment credentials and infrastructure.

## Threats and controls

| Threat | Impact | Implemented controls | Residual risk / required validation |
|---|---|---|---|
| Cross-tenant object access | Disclosure or modification of another tenant's financial records | Tenant filters, PostgreSQL FORCE RLS, per-request tenant context, pooled-connection reset, restricted non-owner runtime role, tenant-isolation tests | Verify grants and negative tests with the exact production role and deployed database |
| Credential theft and session replay | Account takeover | Short-lived JWTs, persisted refresh sessions, rotation, reuse detection, family revocation, auth-token version invalidation, HttpOnly host-only cookie, CSRF and Origin checks | Validate TLS, edge proxy behavior, browser flows, and secret/key rotation in staging |
| Brute-force and account enumeration | Credential compromise or identity disclosure | Persistent email/IP throttling with keyed HMAC identifiers and PostgreSQL advisory locks; generic registration and recovery responses | Tune limits against production traffic and monitor distributed abuse |
| MFA bypass or recovery abuse | Account takeover | Encrypted TOTP secrets, replay prevention, hashed single-use recovery codes, fresh MFA for sensitive operations, shared throttle, session revocation | Test enrollment, recovery, device loss, and administrator-assisted recovery procedures |
| Malicious invoice upload | Malware execution, parser exploitation, resource exhaustion | Extension/type/signature checks, bounded uploads and extraction, ClamAV INSTREAM fail-closed, private storage, bounded downloads, non-root containers | Validate signature freshness, scanner resource limits, parser sandboxing, object permissions, and real malicious-file behavior |
| Prompt injection in invoice text | Unsafe model/tool behavior | Document text treated as untrusted data; deterministic reconciliation; allowlisted read-only tools; AI cannot approve or pay | Add ongoing adversarial prompt-injection tests and review every new tool scope |
| Agent/tool data leakage | Unauthorized retrieval or excessive resource use | Tenant-scoped tool queries, bounded candidate/result counts, bounded query lengths, sanitized tool/runtime errors | Test all future tools and model integrations for tenant scoping, output redaction, and resource limits |
| Duplicate or racing business writes | Duplicate invoices or conflicting approvals | Tenant-wide idempotency for supported writes, fingerprint conflict checks, maker-checker, policy snapshots, optimistic concurrency, serialized policy-range changes | Audit every new mutation endpoint and exercise concurrent requests against PostgreSQL |
| Outbox lease races | A stale worker overwrites a newer worker's delivery state | PostgreSQL SKIP LOCKED claims, bounded leases/retries, and lease-ownership checks on delivery, retry, and cancellation updates | Delivery remains at-least-once; a crash after SMTP acceptance can cause duplicate email. Monitor queue age and dead-letter rows |
| Email token disclosure | Account recovery or verification takeover | Random single-use tokens, SHA-256 token hashes, short expiry, URL fragments, encrypted outbox payloads, invalidation on resend | Validate actual SMTP provider, mailbox delivery, link origin, and secret rotation in staging |
| Audit tampering | Loss of accountability or regulatory evidence | Append-only mutation protection, per-tenant hash chain, verification endpoint, signed exports, S3 Object Lock COMPLIANCE target | Provision and independently verify Object Lock, IAM, versioning, retention, signing-key custody, and restore behavior |
| Database compromise or privilege escalation | Broad data access or RLS bypass | Separate migration and runtime roles; runtime role is non-owner and must not have BYPASSRLS; production DB TLS requires verify-full and explicit CA | Verify deployed grants, certificate chain/hostname, private networking, backup encryption, and managed DB controls |
| CI/dependency compromise | Malicious code or vulnerable dependencies shipped | Backend tests, frontend high-severity dependency audit, Docker/Compose validation, image builds, protected release process documentation | Pin reviewed action and image digests, review dependency updates, enable repository branch protection and supply-chain scanning |
| MCP bearer-token interception | User-scoped API token exposed between MCP adapter and API | HTTPS required for remote API origins, HTTP restricted to loopback development, redirects disabled for authenticated requests, MCP token remains a user-scoped short-lived access token | Validate the deployed MCP URL, TLS certificate chain, host network controls, and token lifecycle; rotate credentials after suspected exposure |
| Service exhaustion | API, database, parser, or model service unavailable | Bounded pagination, upload/extraction limits, query/result caps, throttling, readiness checks, non-root containers | Load test, enforce edge/WAF/body limits, resource quotas, autoscaling and alerting |
| Insider or administrator misuse | Unauthorized access or policy changes | Role checks, maker-checker, member deactivation/session revocation, audit logging | Validate separation of duties, privileged access monitoring, and incident response in the deployed environment |
| Backup or regional failure | Data loss or prolonged outage | Backup and restore scripts, checksum validation, disposable restore-drill tests | Execute real managed-database PITR and isolated restore drills; prove RPO/RTO |

## Security assumptions

- The production runtime database role is distinct from the migration/table-owner role, is not a superuser, and has no BYPASSRLS.
- PostgreSQL and object storage are private and reachable only through approved workload identities/network paths.
- Production secrets and signing keys are supplied by a managed secret system and are never committed to the repository.
- The model provider and all extracted document text are untrusted. Model output never grants authorization.
- The reverse proxy preserves the same-origin cookie/CSRF design and enforces HTTPS, request-size limits, and trusted proxy configuration.
- SMTP, ClamAV, PostgreSQL, object storage, and external identity systems can fail or be compromised; failures must not silently bypass security controls.

## Outstanding assurance work

1. Provision staging and production infrastructure, managed PostgreSQL, private networking, edge TLS/WAF, workload IAM, secret management, logging, metrics, and actionable alerts.
2. Verify PostgreSQL certificate hostname/CA and runtime-role RLS behavior in staging.
3. Configure a real SMTP provider and test verification, password reset, throttling, expiry, and mailbox receipt end-to-end.
4. Provision a versioned S3 Object Lock COMPLIANCE bucket, validate IAM/retention/versioning, and perform signed-export verification and key-rotation drills. The current storage adapter requests SSE-S3; customer-managed SSE-KMS is not implemented.
5. Validate ClamAV signature updates, limits, monitoring, scanner outage behavior, and malicious-file rejection.
6. Execute real backup/PITR and isolated restore drills; record RPO/RTO evidence.
7. Perform independent threat-model review, SAST/DAST, penetration testing, load testing, privacy/legal review, incident-response exercises, and user acceptance.
8. Decide and implement SSO/OIDC if required; it is not currently implemented.
9. Configure repository branch protection and release approvals appropriate to the production risk.

## Release rule

A passing GitHub Actions workflow verifies only the code and checks executed by that workflow. It does not certify cloud configuration, live email delivery, Object Lock enforcement, restore recovery, independent security testing, or production operations. Do not route production traffic until the outstanding staging and operational gates have evidence and named sign-off.
