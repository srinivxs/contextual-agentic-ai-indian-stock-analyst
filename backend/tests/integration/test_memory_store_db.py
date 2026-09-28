"""app/memory/store.py against the real database (P13).

Plain SQL, like the rest of the data access. What is checked here: the merge rule (a newer
``remember`` for a field replaces it; other fields are kept), ``set_field``/``forget_field`` from
the panel, ``forget_all``, that one user never sees or touches another's rows, and that two
concurrent ``remember`` calls for the same user leave exactly one row per field.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.memory.store import forget_all, forget_field, get_profile, remember, set_field
from app.memory.vocabulary import Preference, StoredPreference
from tests.integration.conftest import MakeUser

pytestmark = pytest.mark.usefixtures("migrated_db")

Factory = async_sessionmaker[AsyncSession]


async def test_get_profile_is_empty_for_a_new_user(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        assert await get_profile(db, user_id) == []


async def test_remember_stores_a_new_field(make_user: MakeUser, session_factory: Factory) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        await remember(
            db,
            user_id,
            [Preference(field="risk_preference", values=("conservative",), quote="I hate risk.")],
        )
        await db.commit()
        found = await get_profile(db, user_id)

    assert len(found) == 1
    assert found[0].field == "risk_preference"
    assert found[0].values == ("conservative",)
    assert found[0].quote == "I hate risk."
    assert found[0].source == "chat"
    assert found[0].updated_at is not None


async def test_remember_replaces_the_field_and_keeps_the_others(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        await remember(
            db,
            user_id,
            [
                Preference(field="risk_preference", values=("conservative",), quote="Cautious."),
                Preference(field="debt_preference", values=("avoid_high_debt",), quote="No debt."),
            ],
        )
        await db.commit()
        await remember(
            db,
            user_id,
            [Preference(field="risk_preference", values=("aggressive",), quote="Actually bold.")],
        )
        await db.commit()
        found = {row.field: row for row in await get_profile(db, user_id)}

    assert found["risk_preference"].values == ("aggressive",)
    assert found["risk_preference"].quote == "Actually bold."
    assert found["debt_preference"].values == ("avoid_high_debt",)  # untouched


async def test_get_profile_is_in_fields_order_regardless_of_insertion_order(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        await remember(
            db,
            user_id,
            [
                Preference(field="other_preferences", values=("long_term",), quote="Long term."),
                Preference(field="risk_preference", values=("moderate",), quote="Middling."),
            ],
        )
        await db.commit()
        found = await get_profile(db, user_id)

    assert [row.field for row in found] == ["risk_preference", "other_preferences"]


async def test_set_field_stores_labels_as_the_quote_with_source_edited(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        found = await set_field(db, user_id, "investment_style", ("income", "growth"))
        await db.commit()

    assert isinstance(found, StoredPreference)
    assert found.values == ("income", "growth")
    assert found.quote == "Dividends / income, Growth"
    assert found.source == "edited"


async def test_set_field_refuses_unknown_values(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        with pytest.raises(ValueError, match="risk_preference"):
            await set_field(db, user_id, "risk_preference", ("reckless",))
        with pytest.raises(ValueError, match="risk_preference"):
            await set_field(db, user_id, "risk_preference", ("conservative", "aggressive"))
        assert await get_profile(db, user_id) == []


async def test_forget_field_is_idempotent(make_user: MakeUser, session_factory: Factory) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        await remember(
            db, user_id, [Preference(field="risk_preference", values=("moderate",), quote="Ok.")]
        )
        await db.commit()
        await forget_field(db, user_id, "risk_preference")
        await forget_field(db, user_id, "risk_preference")  # already gone: no error
        await db.commit()
        assert await get_profile(db, user_id) == []


async def test_forget_all_clears_every_field(make_user: MakeUser, session_factory: Factory) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        await remember(
            db,
            user_id,
            [
                Preference(field="risk_preference", values=("moderate",), quote="Ok."),
                Preference(field="debt_preference", values=("debt_ok",), quote="Fine."),
            ],
        )
        await db.commit()
        await forget_all(db, user_id)
        await db.commit()
        assert await get_profile(db, user_id) == []


async def test_one_user_never_touches_another_users_rows(
    make_user: MakeUser, session_factory: Factory
) -> None:
    mine = await make_user()
    theirs = await make_user()
    async with session_factory() as db:
        await remember(
            db, theirs, [Preference(field="risk_preference", values=("moderate",), quote="Ok.")]
        )
        await db.commit()
        await forget_field(db, mine, "risk_preference")
        await forget_all(db, mine)
        await db.commit()

        assert await get_profile(db, mine) == []
        theirs_found = await get_profile(db, theirs)
    assert len(theirs_found) == 1  # untouched by another user's forget


async def test_concurrent_remember_for_the_same_user_leaves_one_row_per_field(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()

    async def one(value: str) -> None:
        async with session_factory() as db:
            await remember(
                db,
                user_id,
                [Preference(field="risk_preference", values=(value,), quote=f"I am {value}.")],
            )
            await db.commit()

    await asyncio.gather(one("conservative"), one("moderate"), one("aggressive"))

    async with admin_engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM investor_profiles WHERE user_id = :id"), {"id": user_id}
            )
        ).scalar_one()
    assert count == 1
