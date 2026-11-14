"""Deterministic financial workflow controls."""


def ensure_independent_decision(invoice, actor_user_id: int, decision: str) -> None:
    """Reject a maker attempting to approve or reject their own invoice."""
    creator_id = getattr(invoice, "created_by_user_id", None)
    if creator_id is not None and creator_id == actor_user_id:
        raise ValueError(
            f"Maker-checker policy: the invoice creator cannot {decision} their own invoice"
        )
