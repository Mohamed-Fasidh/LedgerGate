"""Optional adapter that executes the actual LedgerGate backend from a checkout.

The offline benchmark never presents synthetic fixture observations as live model
accuracy. Pass --ledgergate-root to execute the repository's runtime, retrieval
implementation, and tool dispatcher in disposable in-memory SQLite.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


def _prepare_backend(repo_root: Path) -> None:
    backend = (repo_root / "backend").resolve()
    if not (backend / "app" / "agent" / "runtime.py").exists():
        raise FileNotFoundError(f"LedgerGate backend not found below {repo_root}")
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


def run_live_reconciliation(repo_root: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    _prepare_backend(repo_root)
    runtime = importlib.import_module("app.agent.runtime")
    rows = []
    for case in cases:
        context = runtime.AgentContext(invoice=case["invoice"], purchase_order=case["purchase_order"])
        result = runtime.ReconciliationAgent().run(context)
        actual = result.state.value
        safe_failure = True
        if actual == "failed":
            safe_failure = any(
                item.get("code") == "RUNTIME_ERROR" and item.get("severity") == "high"
                and item.get("message") == "Reconciliation could not be completed; manual review is required."
                for item in result.findings
            )
        trace = list(result.trace)
        expected_trace = case.get("expected_trace", ["start", "load_records", "validate", case["expected_state"]])
        rows.append({
            "case_id": case["case_id"], "scenario": case["scenario"],
            "expected": case["expected_state"], "actual": actual,
            "passed": actual == case["expected_state"],
            "trace": trace, "trace_valid": trace == expected_trace,
            "safe_failure": safe_failure,
            "findings": [{"code": item.get("code"), "severity": item.get("severity"),
                          "message": item.get("message")} for item in result.findings],
        })
    passed = sum(item["passed"] for item in rows)
    trace_passed = sum(item["trace_valid"] for item in rows)
    recovery_rows = [item for item in rows if item["actual"] == "failed"]
    recovery_passed = sum(item["safe_failure"] for item in recovery_rows)
    return {
        "source": "live_ledgergate_reconciliation_agent", "count": len(rows),
        "passed": passed, "accuracy": passed / len(rows) if rows else None,
        "multi_step_trace": {"count": len(rows), "passed": trace_passed,
                             "accuracy": trace_passed / len(rows) if rows else None},
        "safe_failure_recovery": {
            "count": len(recovery_rows), "passed": recovery_passed,
            "accuracy": recovery_passed / len(recovery_rows) if recovery_rows else None,
        },
        "cases": rows,
    }


def run_live_backend_contracts(repo_root: Path, retrieval_cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Exercise actual ToolExecutor schemas, tenant-scoped ORM lookup and retrieval.

    Synthetic records are stored in ephemeral SQLite. This exercises ORM filters
    and the current scoring function, but SQLite does not emulate PostgreSQL RLS.
    """
    _prepare_backend(repo_root)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Invoice, InvoiceStatus, PolicyDocument
    from app.agent.tool_executor import ToolExecutor, ToolError
    from app.services.retrieval import retrieve_policy
    from eval_framework.metrics import retrieval_metrics

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Invoice.__table__, PolicyDocument.__table__])
    tool_rows, retrieval_contract_rows, retrieval_benchmark_rows = [], [], []
    try:
        with Session(engine) as db:
            tenant_a = Invoice(tenant_id="eval-tenant-a", invoice_number="TENANT-A-INVOICE",
                               currency="INR", amount=100, status=InvoiceStatus.received)
            tenant_b = Invoice(tenant_id="eval-tenant-b", invoice_number="TENANT-B-SECRET",
                               currency="INR", amount=999, status=InvoiceStatus.received)
            db.add_all([tenant_a, tenant_b])
            db.add_all([
                PolicyDocument(tenant_id="eval-tenant-a", title="Invoice approval duplicate policy",
                               body="Duplicate invoices require reviewer approval."),
                PolicyDocument(tenant_id="eval-tenant-a", title="Supplier verification policy",
                               body="Supplier identifiers are checked during onboarding."),
                PolicyDocument(tenant_id="eval-tenant-b", title="Tenant B confidential invoice policy",
                               body="Secret tenant-only content."),
            ])
            db.commit()
            db.refresh(tenant_a)
            db.refresh(tenant_b)
            executor = ToolExecutor(db, "eval-tenant-a")
            try:
                value = executor.execute("invoice_lookup", {"invoice_id": tenant_a.id})
                tool_rows.append({
                    "case_id": "LIVE-TOOL-ALLOW",
                    "passed": value["id"] == tenant_a.id and value["invoice_number"] == "TENANT-A-INVOICE",
                    "actual": "allowed tenant-local invoice",
                })
            except Exception as exc:
                tool_rows.append({"case_id": "LIVE-TOOL-ALLOW", "passed": False,
                                  "actual": type(exc).__name__})
            try:
                executor.execute("invoice_lookup", {"invoice_id": tenant_b.id})
                tool_rows.append({"case_id": "LIVE-TOOL-CROSS-TENANT", "passed": False,
                                  "actual": "cross-tenant row returned"})
            except ToolError:
                tool_rows.append({"case_id": "LIVE-TOOL-CROSS-TENANT", "passed": True,
                                  "actual": "tenant-scoped not-found"})
            for name, arguments, expected_reject in [
                ("shell", {}, True),
                ("invoice_lookup", {"invoice_id": -1}, True),
                ("po_search", {"query": "x"}, True),
                ("policy_search", {"query": "duplicate invoice"}, False),
            ]:
                try:
                    executor.execute(name, arguments)
                    rejected = False
                except ToolError:
                    rejected = True
                tool_rows.append({
                    "case_id": f"LIVE-TOOL-{len(tool_rows)+1}",
                    "passed": rejected == expected_reject,
                    "actual": "rejected" if rejected else "accepted",
                    "expected_reject": expected_reject,
                })

            for case in retrieval_cases:
                tenant = f"eval-retrieval-{case['case_id']}"
                id_map: dict[int, str] = {}
                for document in case["documents"]:
                    row = PolicyDocument(tenant_id=tenant, title=document["title"], body=document["body"])
                    db.add(row)
                    db.flush()
                    id_map[row.id] = document["id"]
                retrieved = retrieve_policy(db, tenant, case["query"], limit=int(case["k"]))
                ranked_ids = [id_map[item["id"]] for item in retrieved if item["id"] in id_map]
                relevance = {doc["id"]: int(doc["relevance"]) for doc in case["documents"]}
                score = retrieval_metrics(ranked_ids, relevance, int(case["k"]))
                retrieval_benchmark_rows.append({
                    "case_id": case["case_id"],
                    "passed": score["recall_at_k"] is not None and score["recall_at_k"] >= 0.5,
                    "ranked_ids": ranked_ids, **score,
                })

            db.add_all([
                PolicyDocument(tenant_id="eval-scope-a", title="Tenant A approval policy",
                               body="Invoice approval and duplicate controls."),
                PolicyDocument(tenant_id="eval-scope-b", title="Tenant B confidential approval policy",
                               body="Invoice approval and duplicate controls."),
            ])
            db.commit()
            scoped = retrieve_policy(db, "eval-scope-a", "invoice approval duplicate", limit=10)
            retrieval_contract_rows.append({
                "case_id": "LIVE-RETRIEVAL-TENANT-SCOPE",
                "passed": bool(scoped) and all("Tenant B" not in item["title"] for item in scoped),
                "returned": [item["title"] for item in scoped],
            })
            relevant = retrieve_policy(db, "eval-tenant-a", "invoice approval duplicate", limit=10)
            retrieval_contract_rows.append({
                "case_id": "LIVE-RETRIEVAL-RELEVANCE",
                "passed": bool(relevant) and relevant[0]["title"] == "Invoice approval duplicate policy",
                "returned": [item["title"] for item in relevant],
            })
    finally:
        engine.dispose()

    count = len(retrieval_benchmark_rows)
    return {
        "source": "live_ledgergate_tool_executor_and_retrieval_sqlite",
        "tool_contracts": {"count": len(tool_rows), "passed": sum(item["passed"] for item in tool_rows),
                           "cases": tool_rows},
        "retrieval_contracts": {"count": len(retrieval_contract_rows),
                                "passed": sum(item["passed"] for item in retrieval_contract_rows),
                                "cases": retrieval_contract_rows},
        "retrieval_benchmark": {
            "count": count,
            "mean_precision_at_k": sum(item["precision_at_k"] for item in retrieval_benchmark_rows) / count if count else None,
            "mean_recall_at_k": sum(item["recall_at_k"] for item in retrieval_benchmark_rows) / count if count else None,
            "mean_mrr": sum(item["reciprocal_rank"] for item in retrieval_benchmark_rows) / count if count else None,
            "mean_ndcg_at_k": sum(item["ndcg_at_k"] for item in retrieval_benchmark_rows) / count if count else None,
            "cases_passed_recall_gate": sum(item["passed"] for item in retrieval_benchmark_rows),
            "cases": retrieval_benchmark_rows,
        },
        "database_engine": "SQLite in-memory; PostgreSQL RLS not exercised",
    }


def run_postgres_rls_inventory(repo_root: Path, database_url: str | None) -> dict[str, Any]:
    if not database_url:
        return {"status": "not_measured", "reason": "No PostgreSQL evaluation URL supplied"}
    _prepare_backend(repo_root)
    from sqlalchemy import create_engine, text
    engine = create_engine(database_url)
    expected = [
        "suppliers", "purchase_orders", "invoices", "policy_documents", "agent_memories",
        "audit_events", "approval_policies", "invoice_approvals", "auth_sessions",
        "idempotency_records", "users", "password_reset_tokens", "email_verification_tokens",
    ]
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       p.cmd AS policy_command, p.qual AS policy_using, p.with_check AS policy_with_check
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_policies p ON p.schemaname=n.nspname AND p.tablename=c.relname
                    AND p.policyname='tenant_isolation'
                WHERE n.nspname=current_schema() AND c.relname=ANY(:names)
            """), {"names": expected}).mappings().all()
        indexed = {row["relname"]: row for row in rows}
        checks = []
        for table in expected:
            row = indexed.get(table)
            passed = bool(
                row and row["relrowsecurity"] and row["relforcerowsecurity"]
                and row["policy_command"] == "ALL"
                and "tenant_id" in (row["policy_using"] or "")
                and "current_setting" in (row["policy_using"] or "")
                and "tenant_id" in (row["policy_with_check"] or "")
                and "current_setting" in (row["policy_with_check"] or "")
            )
            checks.append({"table": table, "passed": passed})
        passed = sum(item["passed"] for item in checks)
        return {
            "status": "measured", "source": "postgres_catalog_rls_inventory",
            "count": len(checks), "passed": passed, "accuracy": passed / len(checks),
            "cases": checks,
            "note": "Catalog configuration check only; runtime-role attack tests remain in LedgerGate PostgreSQL CI.",
        }
    finally:
        engine.dispose()
