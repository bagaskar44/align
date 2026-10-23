"""Shared PostgreSQL transaction lock for paid-spend state transitions."""

from sqlalchemy import text
from sqlalchemy.orm import Session

# One local/project budget account is intentionally serialized. Calls are
# low-volume and correctness requires the active release, run cap, held
# liabilities, and settlement state to be observed in one order.
PAID_FINANCE_LOCK_KEY = 1095519559


def lock_paid_finance_scope(db: Session) -> None:
    """Serialize finance mutations; PostgreSQL advisory locks are transaction scoped."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": PAID_FINANCE_LOCK_KEY},
        )
