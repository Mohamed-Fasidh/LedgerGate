"""Allowlisted tool dispatcher with strict input validation and bounded execution surface."""
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InvoiceLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: int = Field(gt=0)


class POSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=200)


class PolicySearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=500)


class ToolError(Exception):
    pass


class ToolExecutor:
    def __init__(self, db, tenant_id):
        self.db = db
        self.tenant_id = tenant_id
        self.registry = {
            "invoice_lookup": (InvoiceLookup, self._invoice),
            "po_search": (POSearch, self._po_search),
            "policy_search": (PolicySearch, self._policy_search),
        }

    def execute(self, name: str, payload: dict):
        if name not in self.registry:
            raise ToolError("Tool is not allowlisted")
        schema, fn = self.registry[name]
        try:
            args = schema.model_validate(payload)
        except ValidationError:
            # Pydantic's default error rendering includes rejected input values.
            # Tool arguments can contain invoice identifiers or business text;
            # return a stable error without reflecting submitted values.
            raise ToolError("Invalid tool arguments") from None
        return fn(args)

    def _invoice(self, args):
        from app.models import Invoice

        row = (
            self.db.query(Invoice)
            .filter(Invoice.id == args.invoice_id, Invoice.tenant_id == self.tenant_id)
            .first()
        )
        if not row:
            raise ToolError("Invoice not found")
        return {
            "id": row.id,
            "invoice_number": row.invoice_number,
            # Preserve exact currency precision in JSON/tool payloads.
            "amount": str(row.amount) if row.amount is not None else None,
            "status": row.status.value,
        }

    def _po_search(self, args):
        from app.services.retrieval import retrieve_business_records

        return retrieve_business_records(self.db, self.tenant_id, args.query)

    def _policy_search(self, args):
        from app.services.retrieval import retrieve_policy

        return retrieve_policy(self.db, self.tenant_id, args.query)
