import uuid, hashlib, tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from app.config import settings
from app.database import Base, engine, get_db
from app.models import User, Supplier, PurchaseOrder, Invoice, AuditEvent, PolicyDocument, AgentMemory, LoginAttempt, Role, InvoiceStatus
from app.schemas import RegisterIn, TokenOut, SupplierIn, POIn, InvoiceIn, ReconcileOut, ExtractionConfirmIn, PolicyIn
from app.security import hash_password, verify_password, create_access_token, get_current_user, require_roles
from app.audit import record_event
from app.agent.runtime import AgentContext, ReconciliationAgent
from app.services.document_extraction import extract_file
from app.services.retrieval import retrieve_policy, retrieve_business_records
from app.services.evaluation import run_evaluation
from app.services.model_router import explain_findings, ModelUnavailable
from app.services.memory import remember, recall
from app.services.storage import store_invoice, read_invoice, scan_with_clamav, MalwareDetected, StorageUnavailable

app=FastAPI(title="LedgerGate API",version="1.1.0",description="Enterprise financial verification and agent reliability platform")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origin_list,allow_credentials=True,allow_methods=["GET","POST","PATCH","DELETE"],allow_headers=["Authorization","Content-Type"])

@app.get("/health")
def health(): return {"status":"ok","service":"ledgergate-api"}

@app.post("/api/v1/auth/register",response_model=TokenOut)
def register(payload:RegisterIn,db:Session=Depends(get_db)):
    tenant_id=str(uuid.uuid4())
    user=User(tenant_id=tenant_id,tenant_name=payload.tenant_name,email=payload.email.lower(),hashed_password=hash_password(payload.password),role=Role.admin)
    db.add(user)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Account already exists")
    db.refresh(user)
    return {"access_token":create_access_token(user)}

@app.post("/api/v1/auth/token",response_model=TokenOut)
def login(request:Request,username:str=Form(...),password:str=Form(...),db:Session=Depends(get_db)):
    normalized_email=username.strip().lower()
    email_hash=hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
    remote_ip=request.client.host if request.client else "unknown"
    ip_hash=hashlib.sha256(remote_ip.encode("utf-8")).hexdigest()
    now=datetime.now(timezone.utc)
    window_start=now-timedelta(minutes=15)
    failed_query=db.query(LoginAttempt).filter(
        LoginAttempt.succeeded.is_(False),
        LoginAttempt.attempted_at>=window_start,
        or_(LoginAttempt.email_hash==email_hash,LoginAttempt.ip_hash==ip_hash)
    )
    failures=failed_query.count()
    if failures>=10:
        oldest=failed_query.order_by(LoginAttempt.attempted_at.asc()).first()
        oldest_at=oldest.attempted_at if oldest else now-timedelta(minutes=15)\n        if oldest_at.tzinfo is None: oldest_at=oldest_at.replace(tzinfo=timezone.utc)\n        retry_after=max(1,900-int((now-oldest_at).total_seconds()))
        raise HTTPException(429,"Too many login attempts. Try again later.",headers={"Retry-After":str(retry_after)})
    user=db.query(User).filter(User.email==normalized_email).first()
    authenticated=bool(user and verify_password(password,user.hashed_password))
    db.add(LoginAttempt(email_hash=email_hash,ip_hash=ip_hash,succeeded=authenticated,attempted_at=now))
    db.commit()
    if not authenticated: raise HTTPException(401,"Incorrect email or password")
    return {"access_token":create_access_token(user)}

@app.post("/api/v1/users")
def create_tenant_user(email:str,password:str=Form(...),role:str=Form("analyst"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    if len(password)<12 or len(password)>72: raise HTTPException(400,"Password must be 12-72 characters")
    if role not in ("analyst","reviewer"): raise HTTPException(400,"Only analyst or reviewer accounts can be created here")
    if db.query(User).filter(User.email==email.lower()).first(): raise HTTPException(409,"Email already registered")
    member=User(tenant_id=user.tenant_id,tenant_name=user.tenant_name,email=email.lower(),hashed_password=hash_password(password),role=Role(role))
    db.add(member); db.flush(); record_event(db,user,"user",member.id,"tenant_user_created",{"email":member.email,"role":role}); db.commit()
    return {"id":member.id,"email":member.email,"role":member.role.value}

@app.get("/api/v1/me")
def me(user:User=Depends(get_current_user)):
    return {"id":user.id,"email":user.email,"tenant_id":user.tenant_id,"tenant_name":user.tenant_name,"role":user.role.value}

@app.post("/api/v1/suppliers")
def create_supplier(payload:SupplierIn,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    obj=Supplier(tenant_id=user.tenant_id,**payload.model_dump()); db.add(obj)
    try:
        db.flush(); record_event(db,user,"supplier",obj.id,"created",{"name":obj.name}); db.commit(); db.refresh(obj)
    except IntegrityError: db.rollback(); raise HTTPException(409,"Supplier already exists")
    return {"id":obj.id,"name":obj.name,"tax_id":obj.tax_id}

@app.get("/api/v1/suppliers")
def list_suppliers(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [{"id":x.id,"name":x.name,"tax_id":x.tax_id} for x in db.query(Supplier).filter(Supplier.tenant_id==user.tenant_id).all()]

@app.post("/api/v1/purchase-orders")
def create_po(payload:POIn,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    supplier=db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first()
    if not supplier: raise HTTPException(404,"Supplier not found")
    obj=PurchaseOrder(tenant_id=user.tenant_id,**payload.model_dump()); db.add(obj)
    try:
        db.flush(); record_event(db,user,"purchase_order",obj.id,"created",{"po_number":obj.po_number}); db.commit(); db.refresh(obj)
    except IntegrityError: db.rollback(); raise HTTPException(409,"Purchase order number already exists")
    return po_view(obj)

def po_view(x):
    return {"id":x.id,"po_number":x.po_number,"supplier_id":x.supplier_id,"currency":x.currency,"total_amount":float(x.total_amount),"description":x.description}

@app.get("/api/v1/purchase-orders")
def list_pos(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [po_view(x) for x in db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id==user.tenant_id).all()]

def invoice_view(x):
    return {"id":x.id,"invoice_number":x.invoice_number,"supplier_id":x.supplier_id,"purchase_order_id":x.purchase_order_id,"currency":x.currency,"amount":float(x.amount) if x.amount is not None else None,"invoice_date":x.invoice_date,"status":x.status.value,"file_name":x.file_name,"extraction_fields":x.extraction_fields or {},"reconciliation":x.reconciliation or {},"created_at":x.created_at.isoformat()}

@app.post("/api/v1/invoices")
def create_invoice(payload:InvoiceIn,idempotency_key:str|None=Header(default=None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    if idempotency_key is not None and (len(idempotency_key)<8 or len(idempotency_key)>128 or not idempotency_key.isascii() or any(ord(c)<33 or ord(c)>126 for c in idempotency_key)):
        raise HTTPException(400,"Idempotency-Key must be 8-128 printable ASCII characters")
    def matches(existing):
        return all(getattr(existing,k)==v for k,v in payload.model_dump().items())
    if idempotency_key:
        existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
        if existing:
            if not matches(existing): raise HTTPException(409,"Idempotency key was already used with a different request")
            return invoice_view(existing)
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    if payload.purchase_order_id:
        po=db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first()
        if not po: raise HTTPException(404,"Purchase order not found")
    obj=Invoice(tenant_id=user.tenant_id,created_by_user_id=user.id,idempotency_key=idempotency_key,**payload.model_dump())
    db.add(obj)
    try:
        db.flush()
        record_event(db,user,"invoice",obj.id,"created",{"invoice_number":obj.invoice_number,"idempotent":bool(idempotency_key)})
        db.commit(); db.refresh(obj)
        return invoice_view(obj)
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id,Invoice.idempotency_key==idempotency_key).first()
            if existing and matches(existing): return invoice_view(existing)
            if existing: raise HTTPException(409,"Idempotency key was already used with a different request")
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
async def upload_invoice(file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    suffix=Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf",".txt"}: raise HTTPException(415,"Only PDF and TXT files are accepted")
    content=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(content)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    if not content: raise HTTPException(400,"Empty file")
    # Basic signature validation; production should additionally scan with AV and use private object storage.
    if suffix==".pdf" and not content.startswith(b"%PDF"): raise HTTPException(415,"File content is not a valid PDF")
    temp_path=None
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
    obj=Invoice(tenant_id=user.tenant_id,created_by_user_id=user.id,invoice_number=candidate,amount=fields.get("amount"),currency=fields.get("currency") or "INR",invoice_date=fields.get("invoice_date"),status=InvoiceStatus.EXTRACTION_REVIEW,file_name=Path(file.filename or "document").name,file_path=stored_location,extracted_text=extracted,extraction_fields=fields,reconciliation={"note":"Extracted fields are unverified. Confirm them before reconciliation."})
    db.add(obj); db.flush(); record_event(db,user,"invoice",obj.id,"uploaded",{"file_name":obj.file_name,"sha256":hashlib.sha256(content).hexdigest(),"bytes":len(content)}); db.commit(); db.refresh(obj)
    return invoice_view(obj)

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
    return StreamingResponse(BytesIO(content),media_type=media_type,headers={"Content-Disposition":f"attachment; filename*=UTF-8''{safe_filename}","X-Content-Type-Options":"nosniff"})

@app.post("/api/v1/invoices/{invoice_id}/confirm-extraction")
def confirm_extraction(invoice_id:int,payload:ExtractionConfirmIn,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    if payload.purchase_order_id and not db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Purchase order not found")
    for k,v in payload.model_dump().items(): setattr(inv,k,v)
    inv.status=InvoiceStatus.RECEIVED
    record_event(db,user,"invoice",inv.id,"extraction_confirmed",{"invoice_number":inv.invoice_number,"amount":payload.amount})
    db.commit(); db.refresh(inv); return invoice_view(inv)

@app.post("/api/v1/invoices/{invoice_id}/reconcile",response_model=ReconcileOut)
def reconcile(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
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
    record_event(db,user,"invoice",inv.id,"reconciled",{"decision":decision,"finding_count":len(result.findings)})
    db.commit()
    return {"invoice_id":inv.id,"status":inv.status.value,"decision":decision,"findings":result.findings,"evidence":evidence,"requires_human_review":decision=="HUMAN_REVIEW"}

@app.post("/api/v1/invoices/{invoice_id}/approve")
def approve(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status not in (InvoiceStatus.REVIEW_REQUIRED,InvoiceStatus.PROCESSING): raise HTTPException(409,"Invoice is not awaiting a decision")
    if inv.created_by_user_id == user.id: raise HTTPException(403,"Maker-checker policy: the invoice creator cannot approve their own invoice")
    if inv.reconciliation.get("decision") not in ("MATCHED","HUMAN_REVIEW"): raise HTTPException(409,"Reconcile invoice before approval")
    inv.status=InvoiceStatus.APPROVED
    record_event(db,user,"invoice",inv.id,"human_approved",{"warning":"Approval recorded; no payment is initiated.","maker_user_id":inv.created_by_user_id})
    try:
        db.commit()
    except StaleDataError:
        db.rollback(); raise HTTPException(409,"Invoice changed concurrently; reload it before approving")
    return {"id":inv.id,"status":inv.status.value,"message":"Human approval recorded. No payment was initiated."}

@app.post("/api/v1/invoices/{invoice_id}/reject")
def reject(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status in (InvoiceStatus.APPROVED,InvoiceStatus.REJECTED): raise HTTPException(409,"Invoice is already finalized")
    if inv.created_by_user_id == user.id: raise HTTPException(403,"Maker-checker policy: the invoice creator cannot reject their own invoice")
    inv.status=InvoiceStatus.REJECTED; record_event(db,user,"invoice",inv.id,"human_rejected",{"maker_user_id":inv.created_by_user_id})
    try:
        db.commit()
    except StaleDataError:
        db.rollback(); raise HTTPException(409,"Invoice changed concurrently; reload it before rejecting")
    return {"id":inv.id,"status":inv.status.value}

@app.post("/api/v1/agent/memory")
def add_agent_memory(content:str,memory_type:str="episodic",db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    if len(content.strip())<5 or len(content)>10000: raise HTTPException(400,"Memory content must be 5-10000 characters")
    try: obj=remember(db,user.tenant_id,content,memory_type,{"created_by":user.id})
    except ValueError as exc: raise HTTPException(400,str(exc))
    record_event(db,user,"agent_memory",obj.id,"created",{"memory_type":memory_type}); db.commit()
    return {"id":obj.id,"memory_type":obj.memory_type}

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
def add_policy(payload:PolicyIn,db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    doc=PolicyDocument(tenant_id=user.tenant_id,**payload.model_dump()); db.add(doc); db.flush()
    record_event(db,user,"policy",doc.id,"created",{"title":doc.title}); db.commit(); db.refresh(doc)
    return {"id":doc.id,"title":doc.title}

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

@app.get("/api/v1/audit-events")
def audit_events(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    rows=db.query(AuditEvent).filter(AuditEvent.tenant_id==user.tenant_id).order_by(AuditEvent.created_at.desc()).limit(500).all()
    return [{"id":x.id,"actor_user_id":x.actor_user_id,"entity_type":x.entity_type,"entity_id":x.entity_id,"action":x.action,"details":x.details,"created_at":x.created_at.isoformat()} for x in rows]
