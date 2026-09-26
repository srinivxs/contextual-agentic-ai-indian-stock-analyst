"""Re-check stored facts against the validator's current label rule, without calling the LLM (P11).

    docker compose --profile app run --rm worker python -m app.recheck_facts

When a validator rule is tightened, facts stored under the old rule may no longer pass it. Every
filing fact keeps its verbatim quote, so the label rule can be applied again for free: a fact whose
quote does not name its metric by the current synonyms is deleted. The page, number, currency and
period checks passed when the fact was stored and do not depend on the synonyms.

Found in the first real run: revenue accepted the loose word "revenue", so a headline figure of
another definition (gross of taxes) was stored as revenue from operations. screener.in facts have
no quote and are never touched.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_worker_settings
from app.core.logging import configure_logging
from app.db.engine import create_db_engine, create_session_factory
from app.fact_validation import label_in_quote

logger = logging.getLogger("app.recheck_facts")

_FILING_FACTS = text("SELECT id, metric, quote FROM facts WHERE source = 'filing'")
_DELETE = text("DELETE FROM facts WHERE id = ANY(:ids)")


async def recheck_labels(db: AsyncSession) -> int:
    """Delete the filing facts whose quote no longer names their metric. Returns how many."""
    failing = [
        row.id
        for row in await db.execute(_FILING_FACTS)
        if not label_in_quote(row.metric, row.quote)
    ]
    if failing:
        await db.execute(_DELETE, {"ids": failing})
    return len(failing)


async def _main() -> None:  # pragma: no cover - process wiring; the logic is tested above
    settings = get_worker_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            removed = await recheck_labels(db)
            await db.commit()
        logger.info("facts_rechecked", extra={"removed": removed})
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover - the process entrypoint
    asyncio.run(_main())
