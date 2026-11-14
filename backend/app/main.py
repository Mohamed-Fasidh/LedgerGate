import uuid, hashlib, os, tempfile
from pathlib import Path
import logging
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.database import Base, engine, get_db, set_tenant_context
from app.models import User, Supplier, PurchaseOrder, Invoice, AuditEvent, PolicyDocument, AgentMemory, IdempotencyRecord, InvoiceApproval, ApprovalPolicy, AuthSession, Role, InvoiceStatus
from app.schemas import RegisterIn, TokenOut, SupplierIn, POIn, InvoiceIn, ReconcileOut, ExtractionConfirmIn, PolicyIn, ApprovalPolicyIn
from app.security import hash_password, verify_password, create_access_token, get_current_user, require_roles, login_is_locked, record_login_failure, clear_login_failures
from app.audit import record_event, verify_event_chain
from app.financial_controls import ensure_independent_decision, required_invoice_approvals, ensure_reviewable_status, ensure_extraction_editable, snapshot_approval_policy, create_approval_policy_version
from app.idempotency import begin_idempotent_request, complete_idempotent_request
from app.agent.runtime import AgentContext, ReconciliationAgent
from app.services.document_extraction import extract_file
from app.services.storage import save_document, local_document_path, create_download_url, delete_document
from app.services.retrieval import retrieve_policy, retrieve_business_records
from app.services.evaluation import run_evaluation
from app.services.model_router import explain_findings, ModelUnavailable
from app.services.memory import remember, recall

logger = logging.getLogger(__name__)

app=FastAPI(title="LedgerGate API",version="1.1.0",description="Enterprise financial verification and agent reliability platform")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origin_list,allow_credentials=True,allow_methods=["GET","POST","PATCH","DELETE"],allow_headers=["Authorization","Content-Type","Idempotency-Key"])

@app.get("/health")
def health(): return {"status":"ok","service":"ledgergate-api"}

@app.post("/api/v1/auth/register",response_model=TokenOut)
def register(payload:RegisterIn,db:Session=Depends(get_db)):
    tenant_id=str(uuid.uuid4())
    set_tenant_context(db, tenant_id)
    user=User(tenant_id=tenant_id,tenant_name=payload.tenant_name,email=payload.email.lower(),hashed_password=hash_password(payload.password),role=Role.admin)
    db.add(user)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Account already exists")
    db.refresh(user)
    token=create_access_token(user,db)
    db.commit()
    return {"access_token":token}

@app.post("/api/v1/auth/token",response_model=TokenOut)
def login(username:str=Form(...),password:str=Form(...),db:Session=Depends(get_db)):
    email=username.strip().lower()
    if login_is_locked(db,email):
        raise HTTPException(429,"Too many failed login attempts. Try again in 15 minutes.")
    user=db.query(User).filter(User.email==email).first()
    if not user or not user.is_active or not verify_password(password,user.hashed_password):
        record_login_failure(db,email)
        db.commit()
        raise HTTPException(401,"Incorrect email or password")
    set_tenant_context(db,user.tenant_id)
    clear_login_failures(db,email)
    token=create_access_token(user,db)
    db.commit()
    return {"access_token":token}


@app.post("/api/v1/auth/logout-all")
def logout_all_sessions(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    db.query(AuthSession).filter(
        AuthSession.user_id==user.id,
        AuthSession.tenant_id==user.tenant_id,
        AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at:now},synchronize_session=False)
    db.commit()
    return {"message":"All active sessions have been revoked. Sign in again on each device."}

@app.post("/api/v1/users")
def create_tenant_user(email:str,password:str=Form(...),role:str=Form("analyst"),idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    normalized_email=email.strip().lower()
    if len(password)<12 or len(password.encode("utf-8"))>72: raise HTTPException(400,"Password must be at least 12 characters and no more than 72 UTF-8 bytes")
    if role not in ("analyst","reviewer"): raise HTTPException(400,"Only analyst or reviewer accounts can be created here")
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"tenant_user.create",{"email":normalized_email,"password":password,"role":role})
    if replay is not None: return replay
    if db.query(User).filter(User.email==normalized_email).first(): raise HTTPException(409,"Email already registered")
    member=User(tenant_id=user.tenant_id,tenant_name=user.tenant_name,email=normalized_email,hashed_password=hash_password(password),role=Role(role))
    db.add(member); db.flush(); record_event(db,user,"user",member.id,"tenant_user_created",{"email":member.email,"role":role})
    response={"id":member.id,"email":member.email,"role":member.role.value}
    complete_idempotent_request(idem,response)
    db.commit()
    return response

@app.get("/api/v1/me")
def me(user:User=Depends(get_current_user)):
    return {"id":user.id,"email":user.email,"tenant_id":user.tenant_id,"tenant_name":user.tenant_name,"role":user.role.value}

@app.post("/api/v1/suppliers")
def create_supplier(payload:SupplierIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"supplier.create",payload.model_dump())
    if replay is not None: return replay
    obj=Supplier(tenant_id=user.tenant_id,**payload.model_dump()); db.add(obj)
    try:
        db.flush(); record_event(db,user,"supplier",obj.id,"created",{"name":obj.name}); response={"id":obj.id,"name":obj.name,"tax_id":obj.tax_id}; complete_idempotent_request(idem,response); db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Supplier already exists")
    return response

@app.get("/api/v1/suppliers")
def list_suppliers(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [{"id":x.id,"name":x.name,"tax_id":x.tax_id} for x in db.query(Supplier).filter(Supplier.tenant_id==user.tenant_id).all()]

@app.post("/api/v1/purchase-orders")
def create_po(payload:POIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"purchase_order.create",payload.model_dump())
    if replay is not None: return replay
    supplier=db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first()
    if not supplier: raise HTTPException(404,"Supplier not found")
    obj=PurchaseOrder(tenant_id=user.tenant_id,**payload.model_dump()); db.add(obj)
    try:
        db.flush(); record_event(db,user,"purchase_order",obj.id,"created",{"po_number":obj.po_number}); response=po_view(obj); complete_idempotent_request(idem,response); db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Purchase order number already exists")
    return response

def po_view(x):
    return {"id":x.id,"po_number":x.po_number,"supplier_id":x.supplier_id,"currency":x.currency,"total_amount":float(x.total_amount),"description":x.description}

@app.get("/api/v1/purchase-orders")
def list_pos(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    return [po_view(x) for x in db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id==user.tenant_id).all()]

def invoice_view(x,approval_count:int=0):
    required=x.required_approvals_snapshot or required_invoice_approvals(x.amount,x.currency,settings.dual_approval_threshold_inr)
    return {"approval_count":approval_count,"required_approvals":required,"approval_policy_version":x.approval_policy_version,"approval_threshold_snapshot":float(x.approval_threshold_snapshot) if x.approval_threshold_snapshot is not None else None,"id":x.id,"invoice_number":x.invoice_number,"supplier_id":x.supplier_id,"purchase_order_id":x.purchase_order_id,"currency":x.currency,"amount":float(x.amount) if x.amount is not None else None,"invoice_date":x.invoice_date,"created_by_user_id":x.created_by_user_id,"approved_by_user_id":x.approved_by_user_id,"rejected_by_user_id":x.rejected_by_user_id,"status":x.status.value,"file_name":x.file_name,"extraction_fields":x.extraction_fields or {},"reconciliation":x.reconciliation or {},"created_at":x.created_at.isoformat()}

@app.post("/api/v1/invoices")
def create_invoice(payload:InvoiceIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"invoice.create",payload.model_dump())
    if replay is not None: return replay
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    po=None
    if payload.purchase_order_id:
        po=db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first()
        if not po: raise HTTPException(404,"Purchase order not found")
    obj=Invoice(tenant_id=user.tenant_id,created_by_user_id=user.id,**payload.model_dump()); db.add(obj); db.flush(); snapshot_approval_policy(db,obj,user.tenant_id,user.id,settings.dual_approval_threshold_inr)
    record_event(db,user,"invoice",obj.id,"created",{"invoice_number":obj.invoice_number}); response=invoice_view(obj); complete_idempotent_request(idem,response); db.commit()
    return response

@app.get("/api/v1/invoices")
def list_invoices(status:str|None=None,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    q=db.query(Invoice).filter(Invoice.tenant_id==user.tenant_id)
    if status:
        try: q=q.filter(Invoice.status==InvoiceStatus(status))
        except ValueError: raise HTTPException(400,"Unknown invoice status")
    rows=q.order_by(Invoice.created_at.desc()).all()
    if not rows: return []
    counts=dict(db.query(InvoiceApproval.invoice_id,func.count(InvoiceApproval.id)).filter(
        InvoiceApproval.tenant_id==user.tenant_id,
        InvoiceApproval.invoice_id.in_([x.id for x in rows])
    ).group_by(InvoiceApproval.invoice_id).all())
    return [invoice_view(x,counts.get(x.id,0)) for x in rows]

@app.get("/api/v1/invoices/{invoice_id}")
def get_invoice(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    x=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not x: raise HTTPException(404,"Invoice not found")
    approval_count=db.query(func.count(InvoiceApproval.id)).filter(
        InvoiceApproval.tenant_id==user.tenant_id,InvoiceApproval.invoice_id==x.id
    ).scalar() or 0
    return invoice_view(x,approval_count)

@app.post("/api/v1/invoices/upload")
async def upload_invoice(file:UploadFile=File(...),idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    suffix=Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf",".txt"}: raise HTTPException(415,"Only PDF and TXT files are accepted")
    content=await file.read(settings.max_upload_mb*1024*1024+1)
    if len(content)>settings.max_upload_mb*1024*1024: raise HTTPException(413,"File too large")
    if not content: raise HTTPException(400,"Empty file")
    # Content checks are defense-in-depth, not a substitute for malware scanning.
    if suffix==".pdf" and not content.startswith(b"%PDF"): raise HTTPException(415,"File content is not a valid PDF")
    if suffix==".txt":
        if b"\x00" in content: raise HTTPException(415,"Text file contains binary data")
        try: content.decode("utf-8-sig",errors="strict")
        except UnicodeDecodeError: raise HTTPException(415,"Text files must use UTF-8 encoding")
    content_hash=hashlib.sha256(content).hexdigest()
    original_name=Path(file.filename or "document").name
    idem,replay=begin_idempotent_request(
        db,user,idempotency_key,"invoice.upload",
        {"file_name":original_name,"suffix":suffix,"sha256":content_hash,"bytes":len(content)},
    )
    if replay is not None: return replay
    # Extract from a short-lived private temp file; persisted storage uses an opaque object key.
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(prefix="ledgergate-extract-",suffix=suffix,delete=False) as temp:
            temp.write(content)
            temp_path=Path(temp.name)
        extracted,fields=extract_file(str(temp_path),suffix,settings.max_upload_pdf_pages)
    except Exception as exc:
        raise HTTPException(422,f"Document extraction failed: {type(exc).__name__}")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    try:
        storage_key=save_document(content,user.tenant_id,suffix)
    except Exception as exc:
        raise HTTPException(503,f"Document storage unavailable: {type(exc).__name__}")
    candidate=fields.get("invoice_number") or f"UNCONFIRMED-{uuid.uuid4().hex[:10].upper()}"
    obj=Invoice(tenant_id=user.tenant_id,invoice_number=candidate,amount=fields.get("amount"),currency=fields.get("currency") or "INR",invoice_date=fields.get("invoice_date"),status=InvoiceStatus.extraction_review,created_by_user_id=user.id,file_name=original_name,file_path=storage_key,extracted_text=extracted,extraction_fields=fields,reconciliation={"note":"Extracted fields are unverified. Confirm them before reconciliation."})
    try:
        db.add(obj)
        db.flush()
        record_event(db,user,"invoice",obj.id,"uploaded",{"file_name":obj.file_name,"sha256":content_hash,"bytes":len(content),"storage_backend":settings.storage_backend})
        response=invoice_view(obj)
        complete_idempotent_request(idem,response)
        db.commit()
        db.refresh(obj)
        return response
    except Exception:
        db.rollback()
        # The object is written before the database row. Remove it if persistence
        # fails so retries do not leave unreferenced private documents behind.
        try:
            delete_document(storage_key,user.tenant_id)
        except Exception as cleanup_error:
            # Do not log object keys, tenant identifiers, or exception text:
            # those may contain sensitive storage details.
            logger.error(
                "Invoice document cleanup failed after persistence error (error_type=%s)",
                type(cleanup_error).__name__,
            )
        raise HTTPException(500,"Invoice record could not be saved; document cleanup was attempted")


@app.get("/api/v1/invoices/{invoice_id}/document")
def download_invoice_document(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv or not inv.file_path:
        raise HTTPException(404,"Document not found")
    if settings.storage_backend=="s3":
        try:
            url=create_download_url(inv.file_path,user.tenant_id)
        except (FileNotFoundError,RuntimeError):
            raise HTTPException(404,"Document not found")
        except Exception:
            raise HTTPException(503,"Document storage unavailable")
        return RedirectResponse(url,status_code=307)
    try:
        path=local_document_path(inv.file_path,user.tenant_id)
    except (FileNotFoundError,ValueError):
        raise HTTPException(404,"Document not found")
    return FileResponse(path,filename=inv.file_name or "invoice-document",media_type="application/pdf" if path.suffix==".pdf" else "text/plain")


@app.post("/api/v1/invoices/{invoice_id}/confirm-extraction")
def confirm_extraction(invoice_id:int,payload:ExtractionConfirmIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    idem,replay=begin_idempotent_request(
        db,user,idempotency_key,"invoice.confirm_extraction",
        {"invoice_id":invoice_id,**payload.model_dump()},
    )
    if replay is not None: return replay
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).with_for_update().first()
    if not inv: raise HTTPException(404,"Invoice not found")
    try: ensure_extraction_editable(inv)
    except ValueError as exc: raise HTTPException(409,str(exc))
    if payload.supplier_id and not db.query(Supplier).filter(Supplier.id==payload.supplier_id,Supplier.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Supplier not found")
    if payload.purchase_order_id and not db.query(PurchaseOrder).filter(PurchaseOrder.id==payload.purchase_order_id,PurchaseOrder.tenant_id==user.tenant_id).first(): raise HTTPException(404,"Purchase order not found")
    for k,v in payload.model_dump().items(): setattr(inv,k,v)
    inv.status=InvoiceStatus.received
    snapshot_approval_policy(db,inv,user.tenant_id,user.id,settings.dual_approval_threshold_inr)
    record_event(db,user,"invoice",inv.id,"extraction_confirmed",{"invoice_number":inv.invoice_number,"amount":payload.amount})
    response=invoice_view(inv)
    complete_idempotent_request(idem,response)
    db.commit(); db.refresh(inv); return response

@app.post("/api/v1/invoices/{invoice_id}/reconcile",response_model=ReconcileOut)
def reconcile(invoice_id:int,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    idem,replay=begin_idempotent_request(
        db,user,idempotency_key,"invoice.reconcile",{"invoice_id":invoice_id}
    )
    if replay is not None: return replay
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).with_for_update().first()
    if not inv: raise HTTPException(404,"Invoice not found")
    try: ensure_reviewable_status(inv)
    except ValueError as exc: raise HTTPException(409,str(exc))
    snapshot_approval_policy(db,inv,user.tenant_id,user.id,settings.dual_approval_threshold_inr)
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
    inv.status=InvoiceStatus.review_required
    inv.reconciliation={"decision":decision,"findings":result.findings,"evidence":evidence,"trace":result.trace,"policy_matches":policies}
    record_event(db,user,"invoice",inv.id,"reconciled",{"decision":decision,"finding_count":len(result.findings)})
    response={"invoice_id":inv.id,"status":inv.status.value,"decision":decision,"findings":result.findings,"evidence":evidence,"requires_human_review":decision=="HUMAN_REVIEW"}
    complete_idempotent_request(idem,response)
    db.commit()
    return response

@app.post("/api/v1/invoices/{invoice_id}/approve")
def approve(invoice_id:int,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"invoice.approve",{"invoice_id":invoice_id})
    if replay is not None: return replay
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).with_for_update().first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status not in (InvoiceStatus.review_required,InvoiceStatus.processing): raise HTTPException(409,"Invoice is not awaiting a decision")
    try: ensure_independent_decision(inv,user.id,"approve")
    except ValueError as exc: raise HTTPException(409,str(exc))
    if inv.reconciliation.get("decision") not in ("MATCHED","HUMAN_REVIEW"): raise HTTPException(409,"Reconcile invoice before approval")
    prior=db.query(InvoiceApproval).filter(InvoiceApproval.tenant_id==user.tenant_id,InvoiceApproval.invoice_id==inv.id,InvoiceApproval.reviewer_user_id==user.id).first()
    if prior: raise HTTPException(409,"This reviewer has already approved the invoice")
    snapshot_approval_policy(db,inv,user.tenant_id,user.id,settings.dual_approval_threshold_inr)
    required=inv.required_approvals_snapshot or required_invoice_approvals(inv.amount,inv.currency,settings.dual_approval_threshold_inr)
    approval=InvoiceApproval(tenant_id=user.tenant_id,invoice_id=inv.id,reviewer_user_id=user.id)
    db.add(approval)
    db.flush()
    approval_count=db.query(InvoiceApproval).filter(InvoiceApproval.tenant_id==user.tenant_id,InvoiceApproval.invoice_id==inv.id).count()
    if approval_count>=required:
        inv.status=InvoiceStatus.approved
        inv.approved_by_user_id=user.id
        action="human_approved"
        message="Required human approval(s) recorded. No payment was initiated."
    else:
        action="human_approval_recorded"
        message="First approval recorded. A second independent reviewer is required before approval is final."
    record_event(db,user,"invoice",inv.id,action,{"approval_count":approval_count,"required_approvals":required,"threshold_currency":"INR","threshold_amount":float(inv.approval_threshold_snapshot) if inv.approval_threshold_snapshot is not None else None,"approval_policy_version":inv.approval_policy_version})
    response={"id":inv.id,"status":inv.status.value,"approval_count":approval_count,"required_approvals":required,"message":message}
    complete_idempotent_request(idem,response)
    db.commit()
    return response

@app.get("/api/v1/invoices/{invoice_id}/approvals")
def invoice_approvals(invoice_id:int,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).first()
    if not inv: raise HTTPException(404,"Invoice not found")
    rows=db.query(InvoiceApproval).filter(InvoiceApproval.tenant_id==user.tenant_id,InvoiceApproval.invoice_id==invoice_id).order_by(InvoiceApproval.created_at.asc()).all()
    return [{"reviewer_user_id":row.reviewer_user_id,"created_at":row.created_at.isoformat()} for row in rows]

@app.post("/api/v1/invoices/{invoice_id}/reject")
def reject(invoice_id:int,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"invoice.reject",{"invoice_id":invoice_id})
    if replay is not None: return replay
    inv=db.query(Invoice).filter(Invoice.id==invoice_id,Invoice.tenant_id==user.tenant_id).with_for_update().first()
    if not inv: raise HTTPException(404,"Invoice not found")
    if inv.status not in (InvoiceStatus.review_required,InvoiceStatus.processing): raise HTTPException(409,"Only reconciled invoices awaiting a decision can be rejected")
    try: ensure_independent_decision(inv,user.id,"reject")
    except ValueError as exc: raise HTTPException(409,str(exc))
    inv.status=InvoiceStatus.rejected
    inv.rejected_by_user_id=user.id
    record_event(db,user,"invoice",inv.id,"human_rejected",{})
    response={"id":inv.id,"status":inv.status.value}
    complete_idempotent_request(idem,response)
    db.commit()
    return response

@app.post("/api/v1/agent/memory")
def add_agent_memory(content:str,memory_type:str="episodic",idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    if len(content.strip())<5 or len(content)>10000: raise HTTPException(400,"Memory content must be 5-10000 characters")
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"agent_memory.create",{"content":content,"memory_type":memory_type})
    if replay is not None: return replay
    try: obj=remember(db,user.tenant_id,content,memory_type,{"created_by":user.id})
    except ValueError as exc: raise HTTPException(400,str(exc))
    record_event(db,user,"agent_memory",obj.id,"created",{"memory_type":obj.memory_type})
    response={"id":obj.id,"memory_type":obj.memory_type}
    complete_idempotent_request(idem,response)
    db.commit()
    return response

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


@app.get("/api/v1/approval-policies")
def list_approval_policies(db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    rows=db.query(ApprovalPolicy).filter(ApprovalPolicy.tenant_id==user.tenant_id).order_by(ApprovalPolicy.version.desc()).all()
    return [{
        "id":row.id,"version":row.version,"currency":row.currency,
        "threshold_amount":float(row.threshold_amount),
        "required_approvals":row.required_approvals,
        "created_by_user_id":row.created_by_user_id,
        "created_at":row.created_at.isoformat(),
    } for row in rows]


@app.post("/api/v1/approval-policies")
def create_approval_policy(payload:ApprovalPolicyIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin"))):
    idem,replay=begin_idempotent_request(
        db,user,idempotency_key,"approval_policy.create_version",payload.model_dump()
    )
    if replay is not None: return replay
    policy=create_approval_policy_version(db,user.tenant_id,user.id,payload.currency,payload.threshold_amount,payload.required_approvals)
    record_event(db,user,"approval_policy",policy.id,"version_created",{
        "version":policy.version,"currency":policy.currency,
        "threshold_amount":float(policy.threshold_amount),
        "required_approvals":policy.required_approvals,
    })
    response={
        "id":policy.id,"version":policy.version,"currency":policy.currency,
        "threshold_amount":float(policy.threshold_amount),
        "required_approvals":policy.required_approvals,
        "message":"New immutable policy version created. Existing invoices retain their policy snapshot.",
    }
    complete_idempotent_request(idem,response)
    db.commit()
    return response

@app.post("/api/v1/policies")
def add_policy(payload:PolicyIn,idempotency_key:str|None=Header(None,alias="Idempotency-Key"),db:Session=Depends(get_db),user:User=Depends(require_roles("admin","analyst"))):
    idem,replay=begin_idempotent_request(db,user,idempotency_key,"policy.create",payload.model_dump())
    if replay is not None: return replay
    doc=PolicyDocument(tenant_id=user.tenant_id,**payload.model_dump()); db.add(doc); db.flush()
    record_event(db,user,"policy",doc.id,"created",{"title":doc.title}); response={"id":doc.id,"title":doc.title}; complete_idempotent_request(idem,response); db.commit()
    return response

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

@app.get("/api/v1/audit-events/verify")
def verify_audit_events(db:Session=Depends(get_db),user:User=Depends(require_roles("admin","reviewer"))):
    events=db.query(AuditEvent).filter(AuditEvent.tenant_id==user.tenant_id).order_by(AuditEvent.id.asc()).yield_per(500)
    valid,invalid_event_id=verify_event_chain(events)
    return {"valid":valid,"first_invalid_event_id":invalid_event_id}


@app.get("/api/v1/audit-events")
def audit_events(db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    rows=db.query(AuditEvent).filter(AuditEvent.tenant_id==user.tenant_id).order_by(AuditEvent.created_at.desc()).limit(500).all()
    return [{"id":x.id,"actor_user_id":x.actor_user_id,"entity_type":x.entity_type,"entity_id":x.entity_id,"action":x.action,"details":x.details,"created_at":x.created_at.isoformat()} for x in rows]
