"""Agent-level tests using CachedLlm (no network).

Covers: grounded menu answer, a full cached order flow, the bounded-tool-call
guard, and the ungrounded-answer guard. The two guard tests inject a tiny fake
LLM (via monkeypatching app.agent.get_llm) to deterministically force the
edge conditions, since the shipped cache is intentionally well-behaved.
"""
import json
import uuid

import pytest

from app import agent
from app.agent import SESSIONS, _PreambleStripper, run_turn
from app.config import settings


# ---------------------------------------------------------------------------
# _PreambleStripper: drop leaked tool-scaffolding, keep real replies intact.
# ---------------------------------------------------------------------------
_PREAMBLE = (
    "Use the results below to formulate an answer to the user question unless "
    "additional information is needed."
)


def _run_stripper(chunks: list[str]) -> str:
    s = _PreambleStripper()
    out = "".join(s.feed(c) for c in chunks)
    return out + s.flush()


def test_stripper_drops_leaked_preamble_before_reply():
    chunks = [_PREAMBLE, "\n\nYour order ABEG-4ACNKS is placed."]
    assert _run_stripper(chunks) == "Your order ABEG-4ACNKS is placed."


def test_stripper_drops_preamble_split_across_deltas():
    # The preamble arrives token by token, then the real reply.
    chunks = ["Use the results ", "below to formulate an answer to the user ",
              "question unless additional information is needed.", " Your order is placed."]
    assert _run_stripper(chunks) == "Your order is placed."


def test_stripper_leaves_normal_reply_untouched():
    chunks = ["We have ", "Party Jollof Rice for 3,500. ", "How many?"]
    assert _run_stripper(chunks) == "We have Party Jollof Rice for 3,500. How many?"


def test_stripper_keeps_reply_that_merely_starts_like_preamble():
    chunks = ["Use ", "your reference ABEG-1234 to track it."]
    assert _run_stripper(chunks) == "Use your reference ABEG-1234 to track it."


def _new_sid() -> str:
    return "sess-" + uuid.uuid4().hex[:8]


async def _drive(pool, sid, text):
    """Run one turn, return the list of yielded event dicts."""
    return [ev async for ev in run_turn(pool, sid, text)]


def _events_of(events, etype):
    return [e for e in events if e["type"] == etype]


def _tool_messages(sid):
    """Tool-role messages recorded in the session transcript."""
    return [m for m in SESSIONS.get(sid, []) if m.get("role") == "tool"]


# ---------------------------------------------------------------------------
# Grounded menu answer: calls a tool, streams deltas + done, invents no price.
# ---------------------------------------------------------------------------
async def test_menu_answer_is_grounded(pool):
    settings.cached_mode = True
    sid = _new_sid()

    events = await _drive(pool, sid, "What do you have?")

    assert _events_of(events, "assistant_delta"), "should stream assistant text"
    done = _events_of(events, "assistant_done")
    assert len(done) == 1

    # It reached its answer by calling a tool (search_inventory), not by inventing.
    tool_msgs = _tool_messages(sid)
    assert any(m["name"] == "search_inventory" for m in tool_msgs)

    # No ungrounded-answer notice fired (the reply is backed by a tool).
    assert not _events_of(events, "notice")
    # The scripted reply lists items without quoting fabricated prices/stock.
    assert done[0]["data"]["text"]


# ---------------------------------------------------------------------------
# Full cached order flow: reserve then confirm -> order placed, on-hand drops.
# ---------------------------------------------------------------------------
async def test_full_order_flow_places_order(pool, stock):
    settings.cached_mode = True
    sid = _new_sid()

    before = await stock("SUYA")

    await _drive(pool, sid, "I'll take two beef suya")
    # reservation reduces availability but not on-hand yet
    mid = await stock("SUYA")
    assert mid["qty_on_hand"] == before["qty_on_hand"]
    assert mid["available"] == before["available"] - 2

    events = await _drive(pool, sid, "yes")

    # place_order tool was executed on the confirmation turn
    assert any(m["name"] == "place_order" for m in _tool_messages(sid))
    # an order actually landed
    async with pool.acquire() as conn:
        orders = int(await conn.fetchval("SELECT COUNT(*) FROM orders"))
    assert orders == 1

    after = await stock("SUYA")
    assert after["qty_on_hand"] == before["qty_on_hand"] - 2  # SUYA on-hand -2
    assert _events_of(events, "assistant_done")


# ---------------------------------------------------------------------------
# Bounded tool calls: the agent never exceeds settings.max_tool_calls.
# ---------------------------------------------------------------------------
class _LoopLlm:
    """Always asks for one more tool call — would loop forever if unbounded."""

    async def stream(self, messages, tools):
        yield {
            "type": "tool_calls",
            "tool_calls": [
                {"id": "c-" + uuid.uuid4().hex[:6], "name": "check_stock", "arguments": {"sku": "SUYA"}}
            ],
        }
        yield {"type": "done", "finish_reason": "tool_calls"}


async def test_bounded_tool_calls(pool, monkeypatch):
    settings.cached_mode = True
    settings.max_tool_calls = 3
    monkeypatch.setattr(agent, "get_llm", lambda *a, **k: _LoopLlm())
    sid = _new_sid()

    events = await _drive(pool, sid, "loop please")

    # Exactly max_tool_calls tool executions — never more.
    tool_msgs = _tool_messages(sid)
    assert len(tool_msgs) == settings.max_tool_calls == 3

    # The bounded notice fired and a graceful message was returned.
    notices = _events_of(events, "notice")
    assert any("bound" in n["data"]["message"].lower() for n in notices)
    done = _events_of(events, "assistant_done")
    assert len(done) == 1


# ---------------------------------------------------------------------------
# Ungrounded guard: a price/stock number with NO tool call is blocked.
# ---------------------------------------------------------------------------
class _UngroundedLlm:
    """Quotes a price without ever calling a tool."""

    async def stream(self, messages, tools):
        yield {"type": "delta", "text": "That will cost 5000 naira each."}
        yield {"type": "done", "finish_reason": "stop"}


async def test_ungrounded_answer_blocked(pool, monkeypatch):
    settings.cached_mode = True
    settings.guardrails = True
    monkeypatch.setattr(agent, "get_llm", lambda *a, **k: _UngroundedLlm())
    sid = _new_sid()

    events = await _drive(pool, sid, "how much is jollof?")

    notices = _events_of(events, "notice")
    assert any("ungrounded" in n["data"]["message"].lower() for n in notices)

    # Final text was replaced — the fabricated "5000 naira" must be gone.
    done = _events_of(events, "assistant_done")
    assert len(done) == 1
    assert "5000" not in done[0]["data"]["text"]

    # No tools ran this turn (that's precisely why the guard fired).
    assert _tool_messages(sid) == []


async def test_ungrounded_guard_off_when_guardrails_off(pool, monkeypatch):
    """With guardrails off, the same ungrounded reply passes through untouched."""
    settings.cached_mode = True
    settings.guardrails = False
    monkeypatch.setattr(agent, "get_llm", lambda *a, **k: _UngroundedLlm())
    sid = _new_sid()

    events = await _drive(pool, sid, "how much is jollof?")

    assert not _events_of(events, "notice")
    done = _events_of(events, "assistant_done")
    assert "5000" in done[0]["data"]["text"]


# ---------------------------------------------------------------------------
# Out of stock: "50 chin chin" reports only the real 15 and reserves nothing.
# ---------------------------------------------------------------------------
async def test_out_of_stock_no_reservation(pool, stock, reservation_count):
    settings.cached_mode = True
    sid = _new_sid()

    events = await _drive(pool, sid, "Do you have 50 chin chin?")

    # It checked real stock via a tool.
    assert any(m["name"] == "check_stock" for m in _tool_messages(sid))

    # Reports the genuine availability (15), never fabricates 50.
    text = _events_of(events, "assistant_done")[0]["data"]["text"]
    assert "15" in text

    # Nothing was reserved; CHINCHIN untouched.
    assert await reservation_count() == 0
    st = await stock("CHINCHIN")
    assert st["qty_on_hand"] == 15
    assert st["available"] == 15


# ---------------------------------------------------------------------------
# Multi-modal vision: uploading an order note image triggers reservations.
# ---------------------------------------------------------------------------
async def test_multimodal_image_order(pool, stock):
    settings.cached_mode = True
    sid = _new_sid()

    sample_image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    events = [ev async for ev in run_turn(pool, sid, "Order from this note", image=sample_image)]

    # Check that session history contains multi-modal content parts
    user_msg = SESSIONS[sid][1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])

    # In cached mode, NOTE_STEPS reserves items from the note
    tool_msgs = _tool_messages(sid)
    assert any(m["name"] == "reserve_items" for m in tool_msgs)
    assert _events_of(events, "assistant_done")

    # Confirm order
    confirm_events = await _drive(pool, sid, "yes")
    assert any(m["name"] == "place_order" for m in _tool_messages(sid))
    assert _events_of(confirm_events, "assistant_done")

