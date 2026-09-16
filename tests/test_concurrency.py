"""ACT TWO — the concurrency guarantee.

Guarded mode MUST prevent overselling the single JOLLOF unit under a concurrent
race (exactly one reservation wins). Naive mode MUST be able to oversell (the
intended, demonstrated failure). Also verifies the availability TTL: an expired
reservation no longer holds stock.
"""
import asyncio

from app import tools
from app.config import settings

# distinct session ids for the racers
def _sids(n: int) -> list[str]:
    return [f"race-{i}" for i in range(n)]


async def _reserve_one_jollof(pool, sid: str) -> dict:
    return await tools.reserve_items(pool, [{"sku": "JOLLOF", "qty": 1}], sid)


# ---------------------------------------------------------------------------
# Guarded: exactly one winner, never oversells.
# ---------------------------------------------------------------------------
async def _run_guarded_race(pool, stock, n: int):
    settings.guardrails = True
    results = await asyncio.gather(*[_reserve_one_jollof(pool, s) for s in _sids(n)])

    winners = [r for r in results if not r.get("refused")]
    refused = [r for r in results if r.get("refused")]

    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)} (n={n})"
    assert len(refused) == n - 1
    for r in refused:
        assert r["error"] == "insufficient_stock"

    st = await stock("JOLLOF")
    assert st["available"] >= 0  # guarded: availability never negative
    assert st["qty_on_hand"] >= 0  # on-hand never negative
    return winners[0]


async def test_guarded_race_n2(pool, stock):
    winner = await _run_guarded_race(pool, stock, 2)
    order = await tools.place_order(pool, winner["reservation_id"], "Winner")
    assert order["reference"].startswith("ABEG-")
    assert (await stock("JOLLOF"))["qty_on_hand"] == 0


async def test_guarded_race_n5(pool, stock):
    winner = await _run_guarded_race(pool, stock, 5)
    order = await tools.place_order(pool, winner["reservation_id"], "Winner")
    assert order["reference"].startswith("ABEG-")
    assert (await stock("JOLLOF"))["qty_on_hand"] == 0


# ---------------------------------------------------------------------------
# Naive: CAN oversell the single unit (the intended, demonstrated failure).
#
# The naive path reads availability with no lock/transaction, so concurrent
# racers all observe available=1 before any reservation is written, then each
# places an order for the single unit.
#
# Oversell signature: MORE THAN ONE order lands for a 1-unit product, so the
# total JOLLOF units sold exceeds the 1 unit that ever existed. That is the
# guarantee the demo breaks. NOTE on the counter value: the naive place_order
# decrements via a read-modify-write (`SET qty_on_hand = current - qty`) across
# separate acquires, so concurrent decrements suffer *lost updates* and land at
# 0 rather than a strictly negative number. We therefore assert the robust,
# always-true invariant (units sold > stock; counter depleted to <= 0) instead
# of requiring the counter to be negative, which asyncio's scheduling does not
# guarantee. We still run a few rounds and only need oversell once to be robust
# against the (empirically ~1-in-30) round where the race happens not to fire.
# ---------------------------------------------------------------------------
async def _run_naive_round(pool, stock, seed, n: int) -> dict:
    await seed.reset_seed(pool)
    settings.guardrails = False

    reserves = await asyncio.gather(*[_reserve_one_jollof(pool, s) for s in _sids(n)])
    accepted = [r for r in reserves if not r.get("refused")]

    orders = await asyncio.gather(
        *[tools.place_order(pool, r["reservation_id"], f"cust-{i}") for i, r in enumerate(accepted)]
    )
    placed = [o for o in orders if not o.get("error")]

    async with pool.acquire() as conn:
        units_sold = int(
            await conn.fetchval(
                "SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE sku = 'JOLLOF'"
            )
        )
    return {
        "accepted": accepted,
        "placed": placed,
        "units_sold": units_sold,
        "stock": await stock("JOLLOF"),
    }


async def test_naive_oversell(pool, stock):
    # import here to reuse the same seed module the fixtures use
    from app import seed

    oversold = None
    rounds = 5
    for _ in range(rounds):
        r = await _run_naive_round(pool, stock, seed, n=3)
        # Oversell: >1 order for the single unit AND more units sold than existed.
        if len(r["placed"]) > 1 and r["units_sold"] > 1:
            oversold = r
            break

    assert oversold is not None, (
        f"naive path failed to oversell across {rounds} rounds "
        "(expected >1 order placed and units_sold > 1 at least once)"
    )
    # The counter is corrupted/depleted despite over-committing stock.
    assert oversold["stock"]["qty_on_hand"] <= 0
    assert oversold["units_sold"] > oversold["stock"]["qty_on_hand"]


# ---------------------------------------------------------------------------
# TTL: expired reservations release their held stock in the availability map.
# ---------------------------------------------------------------------------
async def test_expired_reservation_releases_stock(pool, stock, expire_reservation):
    settings.guardrails = True
    before = await stock("SUYA")

    reserve = await tools.reserve_items(pool, [{"sku": "SUYA", "qty": 4}], "ttl-sess")
    assert (await stock("SUYA"))["available"] == before["available"] - 4

    # Force the reservation to have expired.
    await expire_reservation(reserve["reservation_id"])

    after = await stock("SUYA")
    assert after["available"] == before["available"]  # stock returned
    assert after["qty_on_hand"] == before["qty_on_hand"]


# ---------------------------------------------------------------------------
# Naive cancel_order: two concurrent cancels of the same order can double-restock
# (the cancel-side mirror of the oversell). Guarded mode prevents it.
# ---------------------------------------------------------------------------
async def test_naive_cancel_double_restock(pool, stock):
    from app import seed

    oversold = None
    rounds = 5
    for _ in range(rounds):
        await seed.reset_seed(pool)
        settings.guardrails = True
        before = await stock("SUYA")
        reserve = await tools.reserve_items(pool, [{"sku": "SUYA", "qty": 2}], "cancel-sess")
        order = await tools.place_order(pool, reserve["reservation_id"], "Ada")

        # Fire two concurrent cancels of the SAME order on the naive path.
        settings.guardrails = False
        await asyncio.gather(
            tools.cancel_order(pool, order["reference"], confirm=True),
            tools.cancel_order(pool, order["reference"], confirm=True),
        )
        after = await stock("SUYA")
        # Over-restock signature: on-hand climbs above the original seed level.
        if after["qty_on_hand"] > before["qty_on_hand"]:
            oversold = after["qty_on_hand"]
            break

    assert oversold is not None, (
        f"naive cancel failed to double-restock across {rounds} rounds"
    )


async def test_guarded_cancel_restocks_exactly_once_under_race(pool, stock):
    settings.guardrails = True
    before = await stock("SUYA")
    reserve = await tools.reserve_items(pool, [{"sku": "SUYA", "qty": 2}], "cancel-sess-2")
    order = await tools.place_order(pool, reserve["reservation_id"], "Ada")

    # Two concurrent guarded cancels: exactly one restocks, the other is a no-op.
    await asyncio.gather(
        tools.cancel_order(pool, order["reference"], confirm=True),
        tools.cancel_order(pool, order["reference"], confirm=True),
    )
    after = await stock("SUYA")
    assert after["qty_on_hand"] == before["qty_on_hand"]  # restocked exactly once


async def test_expired_reservation_short_ttl(pool, stock):
    """Same guarantee via a very short reservation_ttl_seconds instead of a helper."""
    settings.guardrails = True
    settings.reservation_ttl_seconds = 1
    before = await stock("PUFFPUFF")

    await tools.reserve_items(pool, [{"sku": "PUFFPUFF", "qty": 5}], "ttl-sess-2")
    assert (await stock("PUFFPUFF"))["available"] == before["available"] - 5

    await asyncio.sleep(1.2)  # let the TTL lapse

    assert (await stock("PUFFPUFF"))["available"] == before["available"]
