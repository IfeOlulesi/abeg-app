"""Report Card eval suite: verdicts react to the live guardrail toggles.

The suite runs deterministic scenarios through the real agent loop. With the
guardrails on every case is green; flipping a guardrail off makes its case slip.
"""
from app import evals
from app.config import settings


def _by_id(result: dict) -> dict:
    return {c["id"]: c for c in result["cases"]}


async def test_suite_all_green_with_guardrails_on(pool):
    settings.guardrails = True
    settings.on_task = True

    result = await evals.run_suite(pool)
    cases = _by_id(result)

    assert cases["fabricated-price"]["status"] == "held"
    assert cases["code-hijack"]["status"] == "held"
    assert cases["bounded-loop"]["status"] == "held"
    assert cases["stray-cancel"]["status"] == "held"
    assert cases["grounded-menu"]["status"] == "grounded"
    assert cases["out-of-stock"]["status"] == "grounded"
    assert cases["order-lookup"]["status"] == "grounded"

    assert result["summary"]["failed"] == 0
    assert result["summary"]["holding"] == result["summary"]["total"]
    assert result["summary"]["disabled"] == 0


async def test_stray_cancel_never_cancels(pool, stock):
    # The stray-cancel case must hold whether or not grounding is on: the
    # confirmation gate is not a toggle.
    settings.guardrails = True
    settings.on_task = True
    result = await evals.run_suite(pool)
    assert _by_id(result)["stray-cancel"]["status"] == "held"


async def test_grounding_case_slips_when_grounding_off(pool):
    settings.guardrails = False
    settings.on_task = True

    result = await evals.run_suite(pool)
    cases = _by_id(result)

    assert cases["fabricated-price"]["status"] == "slipped"
    # Stay-on-task is still on, so the hijack case still holds.
    assert cases["code-hijack"]["status"] == "held"
    assert result["summary"]["disabled"] >= 1
    assert result["summary"]["failed"] == 0


async def test_on_task_case_slips_when_on_task_off(pool):
    settings.guardrails = True
    settings.on_task = False

    result = await evals.run_suite(pool)
    cases = _by_id(result)

    assert cases["code-hijack"]["status"] == "slipped"
    assert cases["fabricated-price"]["status"] == "held"
    assert result["summary"]["failed"] == 0


async def test_run_suite_is_read_only(pool):
    settings.guardrails = True
    settings.on_task = True

    async with pool.acquire() as conn:
        orders_before = int(await conn.fetchval("SELECT COUNT(*) FROM orders"))

    await evals.run_suite(pool)

    async with pool.acquire() as conn:
        orders_after = int(await conn.fetchval("SELECT COUNT(*) FROM orders"))
    assert orders_after == orders_before


async def test_list_cases_metadata():
    cases = evals.list_cases()
    assert len(cases) == len(evals.CASES)
    for c in cases:
        assert set(c.keys()) == {"id", "title", "guardrail", "description"}
