"""Shared pytest fixtures for the Abeg test suite.

Provides a fresh asyncpg pool per test (bound to the test's own event loop),
schema application, per-test seed reset for isolation/order-independence, and
default settings (guardrails on, cached off). Also exposes small async helpers
for reading stock and forcing reservation expiry.

pytest is configured with asyncio_mode=auto, so async tests and async fixtures
run without explicit markers.
"""
import asyncio
import time

import pytest

from app import db, seed
from app.agent import SESSIONS
from app.config import settings


# ---------------------------------------------------------------------------
# settings defaults — reset before AND after every test
# ---------------------------------------------------------------------------
def _reset_settings() -> None:
    settings.guardrails = True
    settings.cached_mode = False
    settings.on_task = True
    settings.system_prompt = ""
    settings.reservation_ttl_seconds = 90
    settings.max_tool_calls = 5


@pytest.fixture(autouse=True)
def default_settings():
    _reset_settings()
    SESSIONS.clear()
    yield
    _reset_settings()
    SESSIONS.clear()


# ---------------------------------------------------------------------------
# pool — function scoped so it binds to the test's event loop.
#
# db.create_pool() caches a module-level singleton; we close it first so each
# test gets a pool attached to its own loop (avoids asyncpg "different loop"
# errors under pytest-asyncio function scoping). Schema is applied idempotently
# and the seed is reset before every test for isolation.
# ---------------------------------------------------------------------------
@pytest.fixture
async def pool():
    await db.close_pool()
    p = await db.create_pool()
    await db.apply_schema(p)
    await seed.reset_seed(p)
    yield p
    await db.close_pool()


# ---------------------------------------------------------------------------
# stock helper — read qty_on_hand + live available for a sku
# ---------------------------------------------------------------------------
@pytest.fixture
def stock(pool):
    async def _stock(sku: str) -> dict:
        s = sku.upper()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT qty_on_hand FROM products WHERE sku = $1", s
            )
            avail = await db.available_map(conn, [s])
        return {
            "qty_on_hand": int(row["qty_on_hand"]) if row else None,
            "available": avail.get(s),
        }

    return _stock


# ---------------------------------------------------------------------------
# expire_reservation helper — force a reservation's expires_at into the past so
# the availability formula releases its held stock (TTL test).
# ---------------------------------------------------------------------------
@pytest.fixture
def expire_reservation(pool):
    async def _expire(reservation_id: str) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE reservations SET expires_at = now() - interval '1 hour' "
                "WHERE id = $1::uuid",
                reservation_id,
            )

    return _expire


# ---------------------------------------------------------------------------
# reservation_count helper — how many reservation rows currently exist
# ---------------------------------------------------------------------------
@pytest.fixture
def reservation_count(pool):
    async def _count() -> int:
        async with pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM reservations"))

    return _count
