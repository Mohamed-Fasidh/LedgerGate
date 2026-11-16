import uuid, hashlib, tempfile, json, secrets, hmac
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from app.config import settings
from app.database import Base, engine, get_db
from app.models import User, Supplier, PurchaseOrder, Invoice, AuditEvent, PolicyDocument, AgentMemory, LoginAttempt, ApprovalPolicy, InvoiceApproval, AuthSession, IdempotencyRecord, RegistrationAttempt, Role, InvoiceStatus
from app.schemas import RegisterIn, TokenOut, RefreshIn, SupplierIn, POIn, InvoiceIn, ReconcileOut, ExtractionConfirmIn, PolicyIn, ApprovalPolicyIn, ApprovalDecisionIn
from app.security import hash_password, verify_password, create_access_token, create_refresh_token, decode_refresh_token, set_database_tenant, get_current_user, require_roles, generate_totp_secret, encrypt_totp_secret, decrypt_totp_secret, matching_totp_step, generate_mfa_recovery_codes, hash_mfa_recovery_code, consume_mfa_recovery_code
from app.audit import record_event, verify_tenant_chain, event_payload
from app.services.audit_export import store_immutable_audit_export, AuditExportUnavailable
from app.agent.runtime import AgentContext, ReconciliationAgent
from app.services.document_extraction import extract_file
from app.services.retrieval import retrieve_policy, retrieve_business_records
from app.services.evaluation import run_evaluation
from app.services.model_router import explain_findings, ModelUnavailable
from app.services.memory import remember, recall
from app.services.storage import store_invoice, read_invoice, delete_invoice, scan_with_clamav, MalwareDetected, StorageUnavailable

app=FastAPI(title="LedgerGate API",version="1.1.0",description="Enterprise financial verification and agent reliability platform")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origin_list,allow_credentials=True,allow_methods=["GET","POST","PATCH","DELETE"],allow_headers=["Authorization","Content-Type","Idempotency-Key","X-CSRF-Token"])


REFRESH_COOKIE_NAME = "ledgergate_refresh"
CSRF_COOKIE_NAME = "ledgergate_csrf"
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_TOKEN_MAX_AGE = 30 * 24 * 60 * 60


def _set_auth_cookies(response: Response, refresh_token: str, csrf_token: str | None = None):
    secure = settings.app_env.lower() == "production"
    csrf_value = csrf_token or secrets.token_urlsafe(32)
    response.set_cookie(key=REFRESH_COOKIE_NAME, value=refresh_token, max_age=REFRESH_TOKEN_MAX_AGE, path=REFRESH_COOKIE_PATH, secure=secure, httponly=True, samesite="lax")
    # The CSRF token is readable by same-origin JavaScript and echoed in a header.
    response.set_cookie(key=CSRF_COOKIE_NAME, value=csrf_value, max_age=REFRESH_TOKEN_MAX_AGE, path="/", secure=secure, httponly=False, samesite="lax")


def _clear_auth_cookies(response: Response):
    secure = settings.app_env.lower() == "production"
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", secure=secure, httponly=False, samesite="lax")


def _require_allowed_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origin_list:
        raise HTTPException(403, "Origin is not allowed")


def _require_csrf(request: Request):
    origin = request.headers.get("origin")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not origin or origin not in settings.cors_origin_list or not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(403, "CSRF validation failed")


REGISTRATION_WINDOW = timedelta(minutes=15)
REGISTRATION_EMAIL_LIMIT = 5
REGISTRATION_IP_LIMIT = 20
REGISTRATION_ATTEMPT_RETENTION = timedelta(hours=24)


def _registration_hash(value: str) -> str:
    # A keyed digest prevents offline dictionary recovery of common emails/IPs
    # if the throttle table is exposed independently of application secrets.
    return hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        b"ledgergate:registration-hash:v1:" + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _registration_lock_key(namespace: str, value_hash: str) -> int:
    digest = hashlib.sha256(f"ledgergate:registration:{namespace}:{value_hash}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _login_attempt_hash(value: str) -> str:
    # Login telemetry contains only keyed identifiers, never raw identities or IPs.
    return hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        b"ledgergate:login-hash:v1:" + value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _record_registration_attempt(db: Session, email: str, remote_ip: str, now: datetime | None = None) -> int | None:
    """Persist and enforce distributed registration limits; return Retry-After when blocked."""
    attempted_at = now or datetime.now(timezone.utc)
    email_hash = _registration_hash(email.strip().lower())
    ip_hash = _registration_hash(remote_ip or "unknown")
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize checks for the same email/IP across API replicas. Acquire locks
        # in stable order so concurrent requests cannot deadlock each other.
        from sqlalchemy import text as sql_text
        for lock_key in sorted({
            _registration_lock_key("email", email_hash),
            _registration_lock_key("ip", ip_hash),
        }):
            db.execute(sql_text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    window_start = attempted_at - REGISTRATION_WINDOW
    email_query = db.query(RegistrationAttempt).filter(
        RegistrationAttempt.email_hash == email_hash,
        RegistrationAttempt.attempted_at >= window_start,
    )
    ip_query = db.query(RegistrationAttempt).filter(
        RegistrationAttempt.ip_hash == ip_hash,
        RegistrationAttempt.attempted_at >= window_start,
    )
    email_count = email_query.count()
    ip_count = ip_query.count()
    retry_after = None
    if email_count >= REGISTRATION_EMAIL_LIMIT or ip_count >= REGISTRATION_IP_LIMIT:
        relevant_query = email_query if email_count >= REGISTRATION_EMAIL_LIMIT else ip_query
        oldest = relevant_query.order_by(RegistrationAttempt.attempted_at.asc()).first()
        oldest_at = oldest.attempted_at if oldest else window_start
        if oldest_at.tzinfo is None:
            oldest_at = oldest_at.replace(tzinfo=timezone.utc)
        retry_after = max(1, int((oldest_at + REGISTRATION_WINDOW - attempted_at).total_seconds()))

    # Keep this pre-authentication table bounded without a separate scheduler.
    stale_ids = [
        row[0] for row in db.query(RegistrationAttempt.id)
        .filter(RegistrationAttempt.attempted_at < attempted_at - REGISTRATION_ATTEMPT_RETENTION)
        .order_by(RegistrationAttempt.id.asc()).limit(250).all()
    ]
    if stale_ids:
        db.query(RegistrationAttempt).filter(RegistrationAttempt.id.in_(stale_ids)).delete(synchronize_session=False)

    # Do not persist repeated blocked requests: otherwise an attacker can turn
    # the throttle table itself into an unbounded write-amplification target.
    if retry_after is not None:
        db.commit()
        return retry_after

    db.add(RegistrationAttempt(email_hash=email_hash, ip_hash=ip_hash, attempted_at=attempted_at))
    db.commit()
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _issue_token_pair(db: Session, user: User, family_id: str | None = None):
    token_id = str(uuid.uuid4())
    family = family_id or str(uuid.uuid4())
    refresh_token, expires_at = create_refresh_token(user, token_id, family)
    db.add(AuthSession(
        tenant_id=user.tenant_id, user_id=user.id, token_id=token_id,
        token_hash=_token_hash(refresh_token), family_id=family, expires_at=expires_at,
    ))
    db.flush()
    return {"access_token": create_access_token(user), "refresh_token": refresh_token}



@app.get("/health")
def health(): return {"status":"ok","service":"ledgergate-api"}

@app.post("/api/v1/auth/register",response_model=TokenOut)
def register(payload:RegisterIn,request:Request,response:Response,db:Session=Depends(get_db)):
    _require_allowed_origin(request)
    remote_ip = request.client.host if request.client else "unknown"
    retry_after = _record_registration_attempt(db, payload.email, remote_ip)
    if retry_after is not None:
        raise HTTPException(429, "Too many registration attempts. Try again later.", headers={"Retry-After": str(retry_after)})
    tenant_id=str(uuid.uuid4())
    user=User(tenant_id=tenant_id,tenant_name=payload.tenant_name,email=payload.email.lower(),hashed_password=hash_password(payload.password),role=Role.admin)
    db.add(user)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Account already exists")
    db.refresh(user)
    set_database_tenant(db, user.tenant_id)
    tokens=_issue_token_pair(db,user); db.commit(); _set_auth_cookies(response,tokens["refresh_token"]); return {"access_token":tokens["access_token"]}

@app.post("/api/v1/auth/token",response_model=TokenOut)
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    totp_code: str | None = Form(default=None),
    recovery_code: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    _require_allowed_origin(request)
    normalized_email = username.strip().lower()
    remote_ip = request.client.host if request.client else "unknown"
    email_hash = _login_attempt_hash(normalized_email)
    ip_hash = _login_attempt_hash(remote_ip)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=15)
    if db.get_bind().dialect.name == "postgresql":
        # Serialize by both identity and source address across API replicas.
        # Stable lock ordering prevents deadlocks when requests share either key.
        from sqlalchemy import text as sql_text
        for lock_key in sorted({
            _registration_lock_key("login-email", email_hash),
            _registration_lock_key("login-ip", ip_hash),
        }):
            db.execute(sql_text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
    failed_query = db.query(LoginAttempt).filter(
        LoginAttempt.succeeded.is_(False),
        LoginAttempt.attempted_at >= window_start,
        or_(LoginAttempt.email_hash == email_hash, LoginAttempt.ip_hash == ip_hash),
    )
    failures = failed_query.count()
    if failures >= 10:
        oldest = failed_query.order_by(LoginAttempt.attempted_at.asc()).first()
        oldest_at = oldest.attempted_at if oldest else window_start
        if oldest_at.tzinfo is None:
            oldest_at = oldest_at.replace(tzinfo=timezone.utc)
        retry_after = max(1, 900 - int((now - oldest_at).total_seconds()))
        db.rollback()
        raise HTTPException(429, "Too many login attempts. Try again later.", headers={"Retry-After": str(retry_after)})
    # Opportunistically prune old records in bounded batches.
    stale_ids = [
        row[0] for row in db.query(LoginAttempt.id)
        .filter(LoginAttempt.attempted_at < now - timedelta(hours=24))
        .order_by(LoginAttempt.id.asc()).limit(250).all()
    ]
    if stale_ids:
        db.query(LoginAttempt).filter(LoginAttempt.id.in_(stale_ids)).delete(synchronize_session=False)

    user = db.query(User).filter(User.email == normalized_email).with_for_update().first()
    password_valid = bool(user and verify_password(password, user.hashed_password))
    matched_step = None
    remaining_recovery_hashes = None
    authenticated = password_valid
    if password_valid and user.mfa_enabled:
        try:
            secret = decrypt_totp_secret(user.mfa_secret_encrypted or "")
            matched_step = matching_totp_step(secret, totp_code, last_used_step=user.mfa_last_used_step)
        except RuntimeError:
            matched_step = None
        if matched_step is not None:
            authenticated = True
        else:
            remaining_recovery_hashes = consume_mfa_recovery_code(user.mfa_recovery_code_hashes, recovery_code)
            authenticated = remaining_recovery_hashes is not None

    if authenticated and matched_step is not None:
        # Persist the consumed TOTP step in the same transaction as the
        # login-attempt record while the user row lock is still held.
        user.mfa_last_used_step = matched_step
    elif authenticated and remaining_recovery_hashes is not None:
        # Recovery codes are single-use and consumed atomically with login.
        user.mfa_recovery_code_hashes = remaining_recovery_hashes
    db.add(LoginAttempt(email_hash=email_hash, ip_hash=ip_hash, succeeded=authenticated, attempted_at=now))
    db.commit()
    if not authenticated:
        raise HTTPException(401, "Incorrect email, password, or authenticator code")

    set_database_tenant(db, user.tenant_id)
    tokens = _issue_token_pair(db, user)
    db.commit()
    _set_auth_cookies(response, tokens["refresh_token"])
    return {"access_token": tokens["access_token"]}


@app.get("/api/v1/auth/mfa/status")
def mfa_status(user: User = Depends(get_current_user)):
    return {"enabled": bool(user.mfa_enabled), "method": "totp" if user.mfa_enabled else None}


@app.post("/api/v1/auth/mfa/setup")
def setup_mfa(
    current_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(403, "Current password is incorrect")
    if user.mfa_enabled:
        raise HTTPException(409, "MFA is already enabled; disable it before re-enrolling")
    try:
        secret = generate_totp_secret()
        user.mfa_secret_encrypted = encrypt_totp_secret(secret)
        db.commit()
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(503, str(exc))
    label = quote(f"LedgerGate:{user.email}")
    issuer = quote("LedgerGate")
    otpauth_url = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_url": otpauth_url, "issuer": "LedgerGate", "account": user.email}


@app.post("/api/v1/auth/mfa/enable")
def enable_mfa(
    totp_code: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    locked_user = db.query(User).filter(User.id == user.id, User.tenant_id == user.tenant_id).with_for_update().first()
    if not locked_user or not locked_user.mfa_secret_encrypted:
        raise HTTPException(409, "Start MFA setup before enabling it")
    if locked_user.mfa_enabled:
        raise HTTPException(409, "MFA is already enabled")
    try:
        secret = decrypt_totp_secret(locked_user.mfa_secret_encrypted)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    matched_step = matching_totp_step(secret, totp_code, last_used_step=locked_user.mfa_last_used_step)
    if matched_step is None:
        raise HTTPException(400, "Invalid or expired authenticator code")
    recovery_codes = generate_mfa_recovery_codes()
    locked_user.mfa_enabled = True
    locked_user.mfa_enabled_at = datetime.now(timezone.utc)
    locked_user.mfa_last_used_step = matched_step
    locked_user.mfa_recovery_code_hashes = [hash_mfa_recovery_code(code) for code in recovery_codes]
    record_event(db, locked_user, "user", locked_user.id, "mfa_enabled", {
        "method": "totp",
        "recovery_codes_issued": len(recovery_codes),
    })
    db.commit()
    return {"status": "enabled", "method": "totp", "recovery_codes": recovery_codes}


@app.post("/api/v1/auth/mfa/disable")
def disable_mfa(
    current_password: str = Form(...),
    totp_code: str | None = Form(default=None),
    recovery_code: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    locked_user = db.query(User).filter(User.id == user.id, User.tenant_id == user.tenant_id).with_for_update().first()
    if not locked_user or not locked_user.mfa_enabled or not locked_user.mfa_secret_encrypted:
        raise HTTPException(409, "MFA is not enabled")
    if not verify_password(current_password, locked_user.hashed_password):
        raise HTTPException(403, "Current password is incorrect")
    try:
        secret = decrypt_totp_secret(locked_user.mfa_secret_encrypted)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    matched_step = matching_totp_step(secret, totp_code, last_used_step=locked_user.mfa_last_used_step)
    remaining_recovery_hashes = None
    if matched_step is None:
        remaining_recovery_hashes = consume_mfa_recovery_code(locked_user.mfa_recovery_code_hashes, recovery_code)
        if remaining_recovery_hashes is None:
            raise HTTPException(400, "Invalid or expired authenticator/recovery code")
    locked_user.mfa_enabled = False
    locked_user.mfa_secret_encrypted = None
    locked_user.mfa_enabled_at = None
    locked_user.mfa_last_used_step = None
    locked_user.mfa_recovery_code_hashes = []
    record_event(db, locked_user, "user", locked_user.id, "mfa_disabled", {"method": "totp"})
    db.commit()
    return {"status": "disabled"}



@app.post("/api/v1/auth/refresh", response_model=TokenOut)
def refresh_session(request: Request, response: Response, payload: RefreshIn | None = None, db: Session = Depends(get_db)):
    _require_csrf(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME) or (payload.refresh_token if payload else None)
    unauthorized = HTTPException(401, "Invalid or expired refresh token")
    if not refresh_token:
        _clear_auth_cookies(response)
        raise unauthorized
    try:
        claims = decode_refresh_token(refresh_token)
        user_id = int(claims["sub"])
        tenant_id = claims["tenant"]
        token_id = claims["jti"]
        family_id = claims["family"]
    except Exception:
        _clear_auth_cookies(response)
        raise unauthorized
    try:
        set_database_tenant(db, tenant_id)
    except ValueError:
        _clear_auth_cookies(response)
        raise unauthorized
    token_hash = _token_hash(refresh_token)
    session = db.query(AuthSession).filter(
        AuthSession.tenant_id == tenant_id,
        AuthSession.user_id == user_id,
        AuthSession.token_id == token_id,
        AuthSession.token_hash == token_hash,
    ).with_for_update().first()
    if not session:
        _clear_auth_cookies(response)
        raise unauthorized
    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if session.revoked_at is not None:
        # Reuse of a rotated token indicates possible theft: revoke the whole family.
        db.query(AuthSession).filter(
            AuthSession.tenant_id == tenant_id,
            AuthSession.family_id == family_id,
            AuthSession.revoked_at.is_(None),
        ).update({AuthSession.revoked_at: now}, synchronize_session=False)
        db.commit()
        _clear_auth_cookies(response)
        raise unauthorized
    if expires_at <= now:
        session.revoked_at = now
        db.commit()
        _clear_auth_cookies(response)
        raise unauthorized
    user = db.query(User).filter(
        User.id == user_id, User.tenant_id == tenant_id, User.is_active.is_(True)
    ).first()
    if not user:
        session.revoked_at = now
        db.commit()
        _clear_auth_cookies(response)
        raise unauthorized
    session.revoked_at = now
    tokens = _issue_token_pair(db, user, family_id=family_id)
    replacement = db.query(AuthSession).filter(
        AuthSession.tenant_id == tenant_id,
        AuthSession.token_hash == _token_hash(tokens["refresh_token"]),
    ).first()
    session.replaced_by_token_id = replacement.token_id if replacement else None
    db.commit()
    _set_auth_cookies(response, tokens["refresh_token"])
    return {"access_token": tokens["access_token"]}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response, payload: RefreshIn | None = None, db: Session = Depends(get_db)):
    _require_csrf(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME) or (payload.refresh_token if payload else None)
    if not refresh_token:
        _clear_auth_cookies(response)
        return {"status": "logged_out"}
    unauthorized = HTTPException(401, "Invalid refresh token")
    try:
        claims = decode_refresh_token(refresh_token)
        tenant_id = claims["tenant"]
        user_id = int(claims["sub"])
        token_id = claims["jti"]
        set_database_tenant(db, tenant_id)
    except Exception:
        _clear_auth_cookies(response)
        raise unauthorized
    session = db.query(AuthSession).filter(
        AuthSession.tenant_id == tenant_id,
        AuthSession.user_id == user_id,
        AuthSession.token_id == token_id,
        AuthSession.token_hash == _token_hash(refresh_token),
    ).first()
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    _clear_auth_cookies(response)
    return {"status": "logged_out"}

def _revoke_refresh_sessions(db: Session, tenant_id: str, user_id: int, now: datetime) -> int:
    return db.query(AuthSession).filter(
        AuthSession.tenant_id == tenant_id,
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now,
    ).update({AuthSession.revoked_at: now}, synchronize_session=False)


@app.post("/api/v1/auth/logout-all")
def logout_all_sessions(response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    revoked_count = _revoke_refresh_sessions(db, user.tenant_id, user.id, now)
    record_event(
        db, user, "auth_session", user.id, "all_refresh_sessions_revoked",
        {"revoked_sessions": revoked_count},
    )
    db.commit()
    _clear_auth_cookies(response)
    return {"status": "logged_out_everywhere", "revoked_sessions": revoked_count}


@app.post("/api/v1/users")
def create_tenant_user(
    email: str,
    password: str = Form(...),
    role: str = Form("analyst"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    _validate_idempotency_key(idempotency_key)
    normalized_email = email.strip().lower()
    if len(password) < 12 or len(password) > 72:
        raise HTTPException(400, "Password must be 12-72 characters")
    if role not in ("analyst", "reviewer"):
        raise HTTPException(400, "Only analyst or reviewer accounts can be created here")
    operation = "tenant_user.create"
    fingerprint = _tenant_user_request_hash(normalized_email, password, role)
    if idempotency_key:
        cached = _get_idempotent_response(db, user.tenant_id, idempotency_key, operation, fingerprint)
        if cached is not None:
            return cached
    if db.query(User).filter(User.email == normalized_email).first():
        raise HTTPException(409, "Email already registered")
    member = User(
        tenant_id=user.tenant_id,
        tenant_name=user.tenant_name,
        email=normalized_email,
        hashed_password=hash_password(password),
        role=Role(role),
    )
    db.add(member)
    try:
        db.flush()
        response = {"id": member.id, "email": member.email, "role": member.role.value}
        if idempotency_key:
            _store_idempotency_response(db, user.tenant_id, idempotency_key, operation, fingerprint, response)
        record_event(db, user, "user", member.id, "tenant_user_created", {
            "email": member.email,
            "role": role,
            "idempotent": bool(idempotency_key),
        })
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached = _get_idempotent_response(db, user.tenant_id, idempotency_key, operation, fingerprint)
            if cached is not None:
                return cached
        raise HTTPException(409, "Email already registered")

@app.get("/api/v1/me")
def me(user:User=Depends(get_current_user)):
    return {"id":user.id,"email":user.email,"tenant_id":user.tenant_id,"tenant_name":user.tenant_name,"role":user.role.value}

@app.post("/api/v1/suppliers")
def create_supplier(payload:SupplierIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    payload_data=payload.model_dump()
    operation="supplier.create"
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    obj=Supplier(tenant_id=user.tenant_id,**payload_data); db.add(obj)
    try:
        db.flush()
        response={"id":obj.id,"name":obj.name,"tax_id":obj.tax_id}
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
        record_event(db,user,"supplier",obj.id,"created",{"name":obj.name,"idempotent":bool(idempotency_key)})
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Supplier already exists")

@app.get("/api/v1/suppliers")
def list_suppliers(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [{"id":x.id,"name":x.name,"tax_id":x.tax_id} for x in db.query(Supplier).filter(Supplier.tenant_id==user.tenant_id).all()]

@app.post("/api/v1/purchase-orders")
def create_po(payload:POIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    payload_data=payload.model_dump()
    operation="purchase_order.create"
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    supplier=db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first()
    if not supplier: raise HTTPException(404,"Supplier not found")
    obj=PurchaseOrder(tenant_id=user.tenant_id,**payload_data); db.add(obj)
    try:
        db.flush()
        response=po_view(obj)
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
        record_event(db,user,"purchase_order",obj.id,"created",{"po_number":obj.po_number,"idempotent":bool(idempotency_key)})
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Purchase order number already exists")

def po_view(x):
    return {"id":x.id,"po_number":x.po_number,"supplier_id":x.supplier_id,"currency":x.currency,"total_amount":float(x.total_amount),"description":x.description}

@app.get("/api/v1/purchase-orders")
def list_pos(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [po_view(x) for x in db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id==user.tenant_id).all()]

def invoice_view(x):
    steps=x.approval_steps_snapshot or []
    stage=x.approval_stage or 1
    required_roles=steps[stage-1] if 1<=stage<=len(steps) else []
    return {"id":x.id,"invoice_number":x.invoice_number,"supplier_id":x.supplier_id,"purchase_order_id":x.purchase_order_id,"currency":x.currency,"amount":float(x.amount) if x.amount is not None else None,"invoice_date":x.invoice_date,"status":x.status.value,"file_name":x.file_name,"extraction_fields":x.extraction_fields or {},"reconciliation":x.reconciliation or {},"approval_stage":stage,"approval_total_stages":len(steps),"approval_required_roles":required_roles,"created_at":x.created_at.isoformat()}

def _validate_idempotency_key(key: str | None) -> None:
    if key is not None and (len(key)<8 or len(key)>128 or not key.isascii() or any(ord(c)<33 or ord(c)>126 for c in key)):
        raise HTTPException(400,"Idempotency-Key must be 8-128 printable ASCII characters")

def _request_fingerprint(operation: str, payload: dict) -> str:
    canonical = json.dumps({"operation":operation,"payload":payload},sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_idempotent_response(db: Session, tenant_id: str, key: str, operation: str, request_hash: str):
    record = db.query(IdempotencyRecord).filter(
        IdempotencyRecord.tenant_id == tenant_id,
        IdempotencyRecord.key == key,
    ).first()
    if record is None:
        return None
    if record.operation != operation or record.request_hash != request_hash:
        raise HTTPException(409, "Idempotency key was already used with a different operation or request")
    return record.response_json


def _store_idempotency_response(db: Session, tenant_id: str, key: str, operation: str, request_hash: str, response: dict):
    db.add(IdempotencyRecord(
        tenant_id=tenant_id, key=key, operation=operation,
        request_hash=request_hash, response_json=jsonable_encoder(response),
    ))

def _tenant_user_request_hash(email: str, password: str, role: str) -> str:
    # A keyed digest avoids exposing a fast offline password-guessing oracle
    # through the persisted idempotency request hash.
    password_digest = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        b"ledgergate:tenant-user-idempotency:v1:" + password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _request_fingerprint("tenant_user.create", {
        "email": email.strip().lower(),
        "role": role,
        "password_hmac_sha256": password_digest,
    })

def _delete_expired_idempotency_records(db: Session, tenant_id: str, cutoff: datetime, batch_size: int = 1000) -> int:
    """Delete a bounded batch of expired records for the current tenant only."""
    records = (
        db.query(IdempotencyRecord)
        .filter(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.created_at < cutoff,
        )
        .order_by(IdempotencyRecord.created_at.asc())
        .limit(batch_size)
        .all()
    )
    for record in records:
        db.delete(record)
    return len(records)

def _delete_expired_auth_sessions(db: Session, tenant_id: str, cutoff: datetime, batch_size: int = 1000) -> int:
    """Delete a bounded batch of sessions expired before the retention cutoff."""
    sessions = (
        db.query(AuthSession)
        .filter(
            AuthSession.tenant_id == tenant_id,
            AuthSession.expires_at < cutoff,
        )
        .order_by(AuthSession.expires_at.asc())
        .limit(batch_size)
        .all()
    )
    for session in sessions:
        db.delete(session)
    return len(sessions)


def _same_idempotent_request(existing: Invoice, operation: str, fingerprint: str) -> bool:
    # Older create-invoice keys predate operation/fingerprint columns; retain
    # backwards compatibility for those records without replaying them as uploads.
    existing_operation = existing.idempotency_operation or "create"
    if existing_operation != operation:
        return False
    if existing.idempotency_request_hash:
        return existing.idempotency_request_hash == fingerprint
    return operation == "create"

@app.post("/api/v1/invoices")
def create_invoice(payload:InvoiceIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    payload_data=payload.model_dump()
    fingerprint=_request_fingerprint("create",payload_data)
    def matches(existing):
        return all(getattr(existing,k)==v for k,v in payload_data.items())
    if idempotency_key:
        existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
        if existing:
            if not _same_idempotent_request(existing,"create",fingerprint) or (not existing.idempotency_request_hash and not matches(existing)):
                raise HTTPException(409,"Idempotency key was already used with a different request")
            return invoice_view(existing)
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,"invoice.create",fingerprint)
        if cached is not None:
            return cached
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    if payload.purchase_order_id:
        po=db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first()
        if not po: raise HTTPException(404,"Purchase order not found")
    obj=Invoice(tenant_id=user.tenant_id,created_by_user_id=user.id,idempotency_key=idempotency_key,idempotency_operation="create" if idempotency_key else None,idempotency_request_hash=fingerprint if idempotency_key else None,**payload_data)
    db.add(obj)
    try:
        db.flush()
        response=invoice_view(obj)
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,"invoice.create",fingerprint,response)
        record_event(db,user,"invoice",obj.id,"created",{"invoice_number":obj.invoice_number,"idempotent":bool(idempotency_key)})
        db.commit(); db.refresh(obj)
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
            if existing and _same_idempotent_request(existing,"create",fingerprint) and (existing.idempotency_request_hash or matches(existing)):
                return invoice_view(existing)
            if existing: raise HTTPException(409,"Idempotency key was already used with a different request")
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,"invoice.create",fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice conflicts with an existing record")

@app.get("/api/v1/invoices")
def list_invoices(status:str|None=None,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    q=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id)
    if status:
        try: q=q.filter(Invoice.status==InvoiceStatus(status))
        except ValueError: raise HTTPException(400,"Unknown invoice status")
    return [invoice_view(x) for x in q.order_by(Invoice.created_at.desc()).all()]

@app.get("/api/v1/invoices/{invoice_id}")
def get_invoice(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not x: raise HTTPException(404,"Invoice not found")
    return invoice_view(x)

@app.post("/api/v1/invoices/upload")
async def upload_invoice(file:UploadFile=File(...),idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    suffix=Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf",".txt"}: raise HTTPException(415,"Only PDF and TXT files are accepted")
    content=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(content)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    if not content: raise HTTPException(400,"Empty file")
    if suffix==".pdf" and not content.startswith(b"%PDF"): raise HTTPException(415,"File content is not a valid PDF")
    safe_filename=Path(file.filename or "document").name
    content_hash=hashlib.sha256(content).hexdigest()
    fingerprint=_request_fingerprint("upload",{"filename":safe_filename,"suffix":suffix,"content_sha256":content_hash})
    if idempotency_key:
        existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
        if existing:
            if _same_idempotent_request(existing,"upload",fingerprint):
                return invoice_view(existing)
            raise HTTPException(409,"Idempotency key was already used with a different request")
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,"invoice.upload",fingerprint)
        if cached is not None:
            return cached
    temp_path=None
    stored_location=None
    try:
        scan_with_clamav(content)
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix) as tmp:
            tmp.write(content); temp_path=tmp.name
        extracted,fields=extract_file(temp_path,suffix)
        stored_location=store_invoice(user.tenant_id,suffix,content)
    except MalwareDetected as exc:
        raise HTTPException(415,str(exc))
    except StorageUnavailable as exc:
        raise HTTPException(503,str(exc))
    except Exception as exc:
        raise HTTPException(422,f"Document processing failed: {type(exc).__name__}")
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
    candidate=fields.get("invoice_number") or f"UNCONFIRMED-{uuid.uuid4().hex[:10].upper()}"
    obj=Invoice(tenant_id=user.tenant_id,created_by_user_id=user.id,idempotency_key=idempotency_key,idempotency_operation="upload" if idempotency_key else None,idempotency_request_hash=fingerprint if idempotency_key else None,invoice_number=candidate,amount=fields.get("amount"),currency=fields.get("currency") or "INR",invoice_date=fields.get("invoice_date"),status=InvoiceStatus.EXTRACTION_REVIEW,file_name=safe_filename,file_path=stored_location,extracted_text=extracted,extraction_fields=fields,reconciliation={"note":"Extracted fields are unverified. Confirm them before reconciliation."})
    db.add(obj)
    try:
        db.flush()
        response=invoice_view(obj)
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,"invoice.upload",fingerprint,response)
        record_event(db,user,"invoice",obj.id,"uploaded",{"file_name":obj.file_name,"sha256":content_hash,"bytes":len(content),"idempotent":bool(idempotency_key)})
        db.commit(); db.refresh(obj)
        return response
    except IntegrityError:
        db.rollback()
        if stored_location:
            delete_invoice(user.tenant_id,stored_location)
        if idempotency_key:
            existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
            if existing and _same_idempotent_request(existing,"upload",fingerprint):
                return invoice_view(existing)
            if existing: raise HTTPException(409,"Idempotency key was already used with a different request")
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,"invoice.upload",fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice upload conflicts with an existing record")
    except Exception:
        db.rollback()
        if stored_location:
            delete_invoice(user.tenant_id,stored_location)
        raise

@app.get("/api/v1/invoices/{invoice_id}/file")
def download_invoice_file(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv or not inv.file_path: raise HTTPException(404,"Invoice file not found")
    try:
        content,suffix=read_invoice(user.tenant_id,inv.file_path)
    except FileNotFoundError:
        raise HTTPException(404,"Invoice file not found")
    except StorageUnavailable as exc:
        raise HTTPException(503,str(exc))
    media_type="application/pdf" if suffix==".pdf" else "text/plain"
    safe_filename=quote(Path(inv.file_name or "invoice-document").name)
    record_event(db,user,"invoice",inv.id,"file_downloaded",{"file_name":inv.file_name})
    db.commit()
    return StreamingResponse(BytesIO(content),media_type=media_type,headers={"Content-Disposition":f"attachment; filename*=UTF-8''{safe_filename}","X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store"})

@app.post("/api/v1/invoices/{invoice_id}/confirm-extraction")
def confirm_extraction(invoice_id:int,payload:ExtractionConfirmIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"invoice.confirm_extraction:{invoice_id}:{user.id}"
    payload_data=payload.model_dump()
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    if payload.purchase_order_id and not db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Purchase order not found")
    for k,v in payload_data.items(): setattr(inv,k,v)
    inv.status=InvoiceStatus.RECEIVED
    try:
        db.flush()
        response=invoice_view(inv)
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
        record_event(db,user,"invoice",inv.id,"extraction_confirmed",{"invoice_number":inv.invoice_number,"amount":payload.amount,"idempotent":bool(idempotency_key)})
        db.commit()
        return response
    except (IntegrityError,StaleDataError):
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice extraction confirmation conflicted with another request")

@app.post("/api/v1/invoices/{invoice_id}/reconcile",response_model=ReconcileOut)
def reconcile(invoice_id:int,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    _validate_idempotency_key(idempotency_key)
    operation=f"invoice.reconcile:{invoice_id}:{user.id}"
    fingerprint=_request_fingerprint(operation,{})
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.amount is None or inv.invoice_number.startswith("UNCONFIRMED-"):
        raise HTTPException(409,"Confirm extracted invoice fields before reconciliation")
    po=None
    if inv.purchase_order_id:
        po=db.query(PurchaseOrder).filter(PurchaseOrder.id==inv.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first()
    duplicate=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.id!=inv.id,Invoice.invoice_number==inv.invoice_number,Invoice.supplier_id==inv.supplier_id).first()
    po_data=({"po_number":po.po_number,"total_amount":float(po.total_amount),"currency":po.currency,"supplier_id":po.supplier_id} if po else None)
    ctx=AgentContext(invoice={"invoice_number":inv.invoice_number,"amount":float(inv.amount),"currency":inv.currency,"supplier_id":inv.supplier_id},purchase_order=po_data)
    result=ReconciliationAgent().run(ctx)
    if duplicate: result.findings.append({"code":"POSSIBLE_DUPLICATE","severity":"high","message":f"Potential duplicate of invoice record {duplicate.id}."})
    if inv.supplier_id and po and inv.supplier_id!=po.supplier_id:
        # state machine receives supplier IDs; this finding is included by the runtime.
        pass
    policies=retrieve_policy(db,user.tenant_id,f"invoice {inv.invoice_number} approval amount discrepancy duplicate")
    evidence=result.evidence+[{"source":"policy_document","reference":p["title"],"snippet":p["snippet"],"score":p["score"]} for p in policies]
    decision="HUMAN_REVIEW" if result.state.value=="human_review" or duplicate else "MATCHED"
    inv.status=InvoiceStatus.REVIEW_REQUIRED
    inv.reconciliation={"decision":decision,"findings":result.findings,"evidence":evidence,"trace":result.trace,"policy_matches":policies}
    response={"invoice_id":inv.id,"status":inv.status.value,"decision":decision,"findings":result.findings,"evidence":evidence,"requires_human_review":decision=="HUMAN_REVIEW"}
    if idempotency_key:
        _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
    record_event(db,user,"invoice",inv.id,"reconciled",{"decision":decision,"finding_count":len(result.findings),"idempotent":bool(idempotency_key)})
    try:
        db.commit()
        return response
    except (IntegrityError,StaleDataError):
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice reconciliation conflicted with another request")

def _validate_approval_steps(steps:list):
    allowed={"admin","reviewer"}
    if not steps or len(steps)>5 or any(not stage or len(stage)>2 or len(set(stage))!=len(stage) or not set(stage).issubset(allowed) for stage in steps):
        raise HTTPException(400,"Each approval stage must contain one or two distinct roles: admin and/or reviewer")
    return steps

def _approval_ranges_overlap(first_min:Decimal,first_max:Decimal|None,second_min:Decimal,second_max:Decimal|None)->bool:
    # Ranges are half-open: [minimum, maximum). Adjacent boundaries do not overlap.
    return (first_max is None or second_min<first_max) and (second_max is None or first_min<second_max)

def _resolve_invoice_approval_plan(db:Session,inv:Invoice,user:User):
    if inv.approval_steps_snapshot:
        return inv.approval_steps_snapshot
    amount=inv.amount if inv.amount is not None else Decimal('0.00')
    policy=db.query(ApprovalPolicy).filter(
        ApprovalPolicy.tenant_id==user.tenant_id,
        ApprovalPolicy.is_active.is_(True),
        ApprovalPolicy.min_amount<=amount,
        or_(ApprovalPolicy.max_amount.is_(None),ApprovalPolicy.max_amount>amount)
    ).order_by(ApprovalPolicy.min_amount.desc()).first()
    steps=policy.approval_steps if policy else [["reviewer","admin"]]
    inv.approval_policy_id=policy.id if policy else None
    inv.approval_steps_snapshot=steps
    inv.approval_stage=1
    return steps

def _validate_approval_actor(db:Session,inv:Invoice,user:User,steps:list):
    stage=inv.approval_stage or 1
    if stage<1 or stage>len(steps):
        raise HTTPException(409,"Invoice approval workflow is in an invalid state")
    allowed_roles=steps[stage-1]
    if user.role.value not in allowed_roles:
        raise HTTPException(403,f"Current approval stage requires one of these roles: {', '.join(allowed_roles)}")
    previous=db.query(InvoiceApproval).filter(
        InvoiceApproval.tenant_id==user.tenant_id,
        InvoiceApproval.invoice_id==inv.id,
        InvoiceApproval.approver_user_id==user.id
    ).first()
    if previous:
        raise HTTPException(409,"You have already submitted a decision for this invoice")
    return stage

@app.post("/api/v1/approval-policies")
def create_approval_policy(payload:ApprovalPolicyIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"approval_policy.create:{user.id}"
    payload_data=payload.model_dump()
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    _validate_approval_steps(payload.approval_steps)
    if payload.max_amount is not None and payload.max_amount<=payload.min_amount:
        raise HTTPException(400,"max_amount must be greater than min_amount")
    existing=db.query(ApprovalPolicy).filter(ApprovalPolicy.tenant_id==user.tenant_id,ApprovalPolicy.is_active.is_(True)).all()
    for policy in existing:
        if _approval_ranges_overlap(payload.min_amount,payload.max_amount,policy.min_amount,policy.max_amount):
            raise HTTPException(409,f"Amount range overlaps active policy '{policy.name}'")
    policy=ApprovalPolicy(tenant_id=user.tenant_id,created_by_user_id=user.id,name=payload.name,min_amount=payload.min_amount,max_amount=payload.max_amount,approval_steps=payload.approval_steps)
    db.add(policy)
    try:
        db.flush()
        response={"id":policy.id,"name":policy.name,"min_amount":float(policy.min_amount),"max_amount":float(policy.max_amount) if policy.max_amount is not None else None,"approval_steps":policy.approval_steps,"is_active":policy.is_active}
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
        record_event(db,user,"approval_policy",policy.id,"created",{"name":policy.name,"min_amount":str(payload.min_amount),"max_amount":str(payload.max_amount) if payload.max_amount is not None else None,"approval_steps":payload.approval_steps,"idempotent":bool(idempotency_key)})
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"An approval policy with this name or idempotency key already exists")

@app.get("/api/v1/approval-policies")
def list_approval_policies(db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    rows=db.query(ApprovalPolicy).filter(ApprovalPolicy.tenant_id==user.tenant_id).order_by(ApprovalPolicy.min_amount.asc()).all()
    return [{"id":p.id,"name":p.name,"min_amount":float(p.min_amount),"max_amount":float(p.max_amount) if p.max_amount is not None else None,"approval_steps":p.approval_steps,"is_active":p.is_active} for p in rows]

@app.post("/api/v1/approval-policies/{policy_id}/deactivate")
def deactivate_approval_policy(policy_id:int,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"approval_policy.deactivate:{policy_id}:{user.id}"
    fingerprint=_request_fingerprint(operation,{})
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    policy=db.query(ApprovalPolicy).filter(ApprovalPolicy.id==policy_id,ApprovalPolicy.tenant_id==user.tenant_id).first()
    if not policy: raise HTTPException(404,"Approval policy not found")
    policy.is_active=False
    response={"id":policy.id,"is_active":False}
    if idempotency_key:
        _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
    record_event(db,user,"approval_policy",policy.id,"deactivated",{"name":policy.name,"idempotent":bool(idempotency_key)})
    try:
        db.commit()
        return response
    except (IntegrityError,StaleDataError):
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Approval policy deactivation conflicted with another request")

@app.post("/api/v1/invoices/{invoice_id}/approve")
def approve(invoice_id:int,payload:ApprovalDecisionIn|None=None,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"invoice.approve:{invoice_id}:{user.id}"
    payload_data=payload.model_dump() if payload else {}
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status not in (InvoiceStatus.REVIEW_REQUIRED,InvoiceStatus.PROCESSING): raise HTTPException(409,"Invoice is not awaiting a decision")
    if inv.created_by_user_id == user.id: raise HTTPException(403,"Maker-checker policy: the invoice creator cannot approve their own invoice")
    if inv.reconciliation.get("decision") not in ("MATCHED","HUMAN_REVIEW"): raise HTTPException(409,"Reconcile invoice before approval")
    steps=_resolve_invoice_approval_plan(db,inv,user)
    stage=_validate_approval_actor(db,inv,user,steps)
    db.add(InvoiceApproval(tenant_id=user.tenant_id,invoice_id=inv.id,policy_id=inv.approval_policy_id,stage=stage,approver_user_id=user.id,decision="approved",comment=payload.comment if payload else None))
    try:
        db.flush()
        if stage>=len(steps):
            inv.status=InvoiceStatus.APPROVED
            message="All approval stages completed. Human approval recorded; no payment was initiated."
            record_event(db,user,"invoice",inv.id,"human_approved",{"stage":stage,"total_stages":len(steps),"maker_user_id":inv.created_by_user_id,"warning":"No payment is initiated."})
        else:
            inv.approval_stage=stage+1
            message=f"Approval stage {stage} recorded. Waiting for stage {stage+1} of {len(steps)}."
            record_event(db,user,"invoice",inv.id,"approval_stage_approved",{"stage":stage,"total_stages":len(steps),"next_stage_roles":steps[stage]})
        response={"id":inv.id,"status":inv.status.value,"approval_stage":inv.approval_stage,"approval_total_stages":len(steps),"message":message}
        if idempotency_key:
            _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
        db.commit()
        return response
    except StaleDataError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice changed concurrently; reload it before approving")
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"An approval decision or idempotency record already exists")

@app.post("/api/v1/invoices/{invoice_id}/reject")
def reject(invoice_id:int,payload:ApprovalDecisionIn|None=None,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"invoice.reject:{invoice_id}:{user.id}"
    payload_data=payload.model_dump() if payload else {}
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status in (InvoiceStatus.APPROVED,InvoiceStatus.REJECTED): raise HTTPException(409,"Invoice is already finalized")
    if inv.status not in (InvoiceStatus.REVIEW_REQUIRED,InvoiceStatus.PROCESSING): raise HTTPException(409,"Invoice is not awaiting a decision")
    if inv.reconciliation.get("decision") not in ("MATCHED","HUMAN_REVIEW"): raise HTTPException(409,"Reconcile invoice before rejection")
    if inv.created_by_user_id == user.id: raise HTTPException(403,"Maker-checker policy: the invoice creator cannot reject their own invoice")
    steps=_resolve_invoice_approval_plan(db,inv,user)
    stage=_validate_approval_actor(db,inv,user,steps)
    db.add(InvoiceApproval(tenant_id=user.tenant_id,invoice_id=inv.id,policy_id=inv.approval_policy_id,stage=stage,approver_user_id=user.id,decision="rejected",comment=payload.comment if payload else None))
    inv.status=InvoiceStatus.REJECTED
    record_event(db,user,"invoice",inv.id,"human_rejected",{"stage":stage,"total_stages":len(steps),"maker_user_id":inv.created_by_user_id,"idempotent":bool(idempotency_key)})
    response={"id":inv.id,"status":inv.status.value,"approval_stage":stage,"message":"Invoice rejected at approval stage."}
    if idempotency_key:
        _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
    try:
        db.commit()
        return response
    except StaleDataError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Invoice changed concurrently; reload it before rejecting")
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"An approval decision or idempotency record already exists")

@app.get("/api/v1/invoices/{invoice_id}/approvals")
def invoice_approvals(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    rows=db.query(InvoiceApproval).filter(InvoiceApproval.tenant_id==user.tenant_id,InvoiceApproval.invoice_id==inv.id).order_by(InvoiceApproval.stage.asc(),InvoiceApproval.created_at.asc()).all()
    return {"invoice_id":inv.id,"policy_id":inv.approval_policy_id,"approval_steps":inv.approval_steps_snapshot or [["reviewer","admin"]],"current_stage":inv.approval_stage or 1,"decisions":[{"stage":r.stage,"approver_user_id":r.approver_user_id,"decision":r.decision,"comment":r.comment,"created_at":r.created_at.isoformat()} for r in rows]}

@app.post("/api/v1/agent/memory")
def add_agent_memory(content:str,memory_type:str="episodic",idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"agent_memory.create:{user.id}"
    payload={"content":content,"memory_type":memory_type}
    fingerprint=_request_fingerprint(operation,payload)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    if len(content.strip())<5 or len(content)>10000: raise HTTPException(400,"Memory content must be 5-10000 characters")
    try: obj=remember(db,user.tenant_id,content,memory_type,{"created_by":user.id})
    except ValueError as exc: raise HTTPException(400,str(exc))
    response={"id":obj.id,"memory_type":obj.memory_type}
    if idempotency_key:
        _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
    record_event(db,user,"agent_memory",obj.id,"created",{"memory_type":memory_type,"idempotent":bool(idempotency_key)})
    try:
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Agent memory creation conflicted with another request")

@app.get("/api/v1/agent/memory/search")
def search_agent_memory(q:str,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if len(q.strip())<2: raise HTTPException(400,"Query must contain at least two characters")
    return {"memories":recall(db,user.tenant_id,q)}

@app.post("/api/v1/invoices/{invoice_id}/explain")
def explain_invoice(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    try:
        explanation=explain_findings(invoice_view(inv),inv.reconciliation.get("findings",[]),inv.reconciliation.get("evidence",[]))
    except ModelUnavailable as exc:
        raise HTTPException(503,str(exc))
    record_event(db,user,"invoice",inv.id,"ai_explanation_generated",{"provider":explanation.get("provider"),"advisory_only":True})
    db.commit()
    return {"invoice_id":inv.id,**explanation,"advisory_only":True}

@app.post("/api/v1/policies")
def add_policy(payload:PolicyIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    _validate_idempotency_key(idempotency_key)
    operation=f"policy.create:{user.id}"
    payload_data=payload.model_dump()
    fingerprint=_request_fingerprint(operation,payload_data)
    if idempotency_key:
        cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
        if cached is not None: return cached
    doc=PolicyDocument(tenant_id=user.tenant_id,**payload_data); db.add(doc); db.flush()
    response={"id":doc.id,"title":doc.title}
    if idempotency_key:
        _store_idempotency_response(db,user.tenant_id,idempotency_key,operation,fingerprint,response)
    record_event(db,user,"policy",doc.id,"created",{"title":doc.title,"idempotent":bool(idempotency_key)})
    try:
        db.commit()
        return response
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            cached=_get_idempotent_response(db,user.tenant_id,idempotency_key,operation,fingerprint)
            if cached is not None: return cached
        raise HTTPException(409,"Policy creation conflicted with another request")

@app.get("/api/v1/policies/search")
def search_policies(q:str,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    if len(q.strip())<2: raise HTTPException(400,"Query must contain at least two characters")
    return retrieve_policy(db,user.tenant_id,q)

@app.get("/api/v1/agent/tools")
def agent_tools(user:User=Depends(get_current_user)):
    return {"tools":[{"name":"invoice_lookup","description":"Retrieve invoice data scoped to the authenticated tenant."},{"name":"purchase_order_lookup","description":"Retrieve purchase order data scoped to the authenticated tenant."},{"name":"policy_search","description":"Search the tenant's policy documents."},{"name":"duplicate_check","description":"Check possible duplicate invoice identifiers."}]}

@app.get("/api/v1/evaluations/run")
def evaluate(user:User=Depends(require_roles("admin","reviewer"))):
    return run_evaluation()

@app.post("/api/v1/maintenance/idempotency/cleanup")
def cleanup_idempotency_records(
    retention_days: int = 90,
    batch_size: int = 1000,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    """Remove one bounded batch of this tenant's idempotency keys older than the retention window."""
    if retention_days < 30 or retention_days > 3650:
        raise HTTPException(400, "retention_days must be between 30 and 3650")
    if batch_size < 1 or batch_size > 10000:
        raise HTTPException(400, "batch_size must be between 1 and 10000")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = _delete_expired_idempotency_records(db, user.tenant_id, cutoff, batch_size)
    record_event(db, user, "idempotency_record", user.tenant_id, "expired_records_cleaned", {
        "deleted_count": deleted,
        "retention_days": retention_days,
        "batch_size": batch_size,
    })
    db.commit()
    return {
        "status": "completed",
        "deleted_records": deleted,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "has_more": deleted == batch_size,
    }


@app.post("/api/v1/maintenance/auth-sessions/cleanup")
def cleanup_expired_auth_sessions(
    retention_days: int = 30,
    batch_size: int = 1000,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    """Remove a bounded batch of sessions expired beyond the investigation grace period."""
    if retention_days < 0 or retention_days > 3650:
        raise HTTPException(400, "retention_days must be between 0 and 3650")
    if batch_size < 1 or batch_size > 10000:
        raise HTTPException(400, "batch_size must be between 1 and 10000")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = _delete_expired_auth_sessions(db, user.tenant_id, cutoff, batch_size)
    record_event(db, user, "auth_session", user.tenant_id, "expired_sessions_cleaned", {
        "deleted_count": deleted,
        "retention_days_after_expiry": retention_days,
        "batch_size": batch_size,
    })
    db.commit()
    return {
        "status": "completed",
        "deleted_sessions": deleted,
        "retention_days_after_expiry": retention_days,
        "cutoff": cutoff.isoformat(),
        "has_more": deleted == batch_size,
    }


@app.get("/api/v1/audit-events")
def audit_events(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    rows=db.query(AuditEvent).filter(AuditEvent.tenant_id==user.tenant_id).order_by(AuditEvent.created_at.desc()).limit(500).all()
    return [{"id":x.id,"actor_user_id":x.actor_user_id,"entity_type":x.entity_type,"entity_id":x.entity_id,"action":x.action,"details":x.details,"created_at":x.created_at.isoformat()} for x in rows]


@app.get("/api/v1/audit-events/verify")
def verify_audit_events(db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    """Verify the tenant's append-only audit hash chain; this does not replace WORM export."""
    result = verify_tenant_chain(db, user.tenant_id)
    record_event(db, user, "audit_chain", user.tenant_id, "verification_performed", {
        "valid": result["valid"], "verified_events": result["verified_events"],
    })
    db.commit()
    return result


@app.post("/api/v1/audit-events/export")
def export_audit_events(
    after_id: int = 0,
    batch_size: int = 1000,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    """Export a verified audit-chain batch to an S3 Object Lock COMPLIANCE bucket."""
    if after_id < 0:
        raise HTTPException(400, "after_id must be zero or greater")
    if batch_size < 1 or batch_size > 10000:
        raise HTTPException(400, "batch_size must be between 1 and 10000")

    chain = verify_tenant_chain(db, user.tenant_id)
    if not chain["valid"]:
        raise HTTPException(409, "Audit chain verification failed; immutable export refused")

    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.tenant_id == user.tenant_id, AuditEvent.id > after_id)
        .order_by(AuditEvent.id.asc())
        .limit(batch_size)
        .all()
    )
    if not rows:
        return {"status": "no_events", "after_id": after_id, "event_count": 0}

    events = []
    for event in rows:
        payload = event_payload(
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action,
            details=event.details,
            created_at=event.created_at,
        )
        events.append({
            "id": event.id,
            **payload,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
        })

    try:
        exported = store_immutable_audit_export(user.tenant_id, events)
    except AuditExportUnavailable as exc:
        raise HTTPException(503, str(exc))
    record_event(db, user, "audit_export", exported["key"], "immutable_export_created", {
        "event_count": exported["event_count"],
        "first_event_id": exported["first_event_id"],
        "last_event_id": exported["last_event_id"],
        "payload_sha256": exported["payload_sha256"],
        "version_id": exported.get("version_id"),
        "object_lock_mode": exported["object_lock_mode"],
        "retain_until": exported["retain_until"],
    })
    db.commit()
    return {"status": "exported", **exported, "next_after_id": exported["last_event_id"]}
