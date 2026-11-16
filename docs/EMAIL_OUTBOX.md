# Account email outbox operations

LedgerGate persists verification and password-reset delivery requests in PostgreSQL before returning success. The URL containing the one-time token is encrypted with `EMAIL_OUTBOX_ENCRYPTION_KEY`; only the recipient and message type remain as readable metadata. The separate `email-worker` service delivers SMTP messages.

## Delivery guarantees

- The API inserts the token row and encrypted outbox row in the same database transaction. A rollback therefore cannot leave a live token without its email delivery record.
- Workers claim due messages using PostgreSQL `FOR UPDATE SKIP LOCKED` and a five-minute lease, so multiple replicas can work concurrently.
- Transient errors retry with exponential backoff starting at 30 seconds and capped at one hour. After 12 attempts, a message is marked `dead`.
- Expired worker leases are reclaimed automatically after five minutes.
- Sent and cancelled records have their encrypted token payload cleared immediately. Sent/cancelled metadata is purged after 30 days; dead-letter metadata is purged after 90 days.
- Delivery is **at least once**, not exactly once. If SMTP accepts a message but the worker crashes before committing `sent`, the same email can be delivered again after lease expiry. The links themselves remain single-use.
- The outbox deliberately does not log raw URLs, token values, SMTP response bodies, or exception messages. `last_error` stores only the exception class.

## Local operation

1. Generate a dedicated key:
   ```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
2. Put it in `.env` as `EMAIL_OUTBOX_ENCRYPTION_KEY`. Do not reuse `JWT_SECRET_KEY` or `MFA_ENCRYPTION_KEY`.
3. Configure the SMTP host, port 587, username, password, sender address, and `PUBLIC_APP_URL`.
4. Start the stack with `docker compose up --build -d`. Compose starts the `email-worker` after the API is healthy.
5. Inspect worker logs with `docker compose logs -f email-worker`. Logs include message IDs and error classes only, not message contents.

## Monitoring queries

Run these read-only queries against the application database. Restrict access to operators because recipient addresses are personal data.

```sql
-- Queue depth by state
SELECT status, count(*) AS messages,
       min(created_at) AS oldest_created_at
FROM outbound_emails
GROUP BY status
ORDER BY status;

-- Messages that have been waiting longer than 10 minutes
SELECT id, recipient, message_type, attempts, created_at, next_attempt_at, last_error
FROM outbound_emails
WHERE status = 'pending'
  AND created_at < now() - interval '10 minutes'
ORDER BY created_at
LIMIT 100;

-- Exhausted deliveries requiring operator attention
SELECT id, recipient, message_type, attempts, created_at, last_error
FROM outbound_emails
WHERE status = 'dead'
ORDER BY created_at DESC
LIMIT 100;
```

Alert on any `dead` message and on sustained growth in old `pending` messages. Terminal records are retained for a bounded period (30 days for sent/cancelled, 90 days for dead) and purged hourly by the worker. Do not manually reset a dead message to pending until the SMTP/provider issue is understood; doing so may send duplicate messages.

## Encryption-key rotation

The worker encrypts new messages with `EMAIL_OUTBOX_ENCRYPTION_KEY` and can decrypt with the comma-separated `EMAIL_OUTBOX_DECRYPTION_KEYS` list.

1. Generate a new Fernet key and store it in the secrets manager.
2. Set the new key as `EMAIL_OUTBOX_ENCRYPTION_KEY` and put the previous key in `EMAIL_OUTBOX_DECRYPTION_KEYS`.
3. Roll out the same configuration to API and worker processes.
4. Wait until no pending/sending/dead messages need the old key, or re-encrypt remaining rows under a controlled maintenance procedure.
5. Remove the old key only after verifying no retained outbox payload needs it.

Back up encryption keys separately from the database. Losing all keys that can decrypt pending payloads makes those messages unrecoverable. Do not include keys in logs, source control, database backups, or support tickets.
