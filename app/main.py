"""FastAPI API layer for the Abeg order-agent demo.

Wires together the DB pool, seed data, the pub/sub event bus, the agent turn
loop and the STT relay. Serves the operator SSE stream, the per-chat SSE stream,
the control endpoints (guardrails / cached / reset / race / scripted) and the
storefront static assets.

Timestamps use time.time() (via events.make_event).
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import agent, db, evals, seed
from app import tools
from app.config import settings, AVAILABLE_MODELS
from app.events import bus, make_event
from app.providers.stt import get_stt
from app.ratelimit import limiter

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
_ROOT_DIR = Path(__file__).resolve().parent.parent
_STATIC_DIR = _ROOT_DIR / "static"
_INDEX_HTML = _STATIC_DIR / "index.html"
# Built React + Tailwind + Vite frontend (preferred when present).
_WEB_DIST = _ROOT_DIR / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_WEB_ASSETS = _WEB_DIST / "assets"

_SWEEP_INTERVAL_SECONDS = 15
_KEEPALIVE_INTERVAL_SECONDS = 15

_PLACEHOLDER_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Abeg</title></head>
<body style="font-family:system-ui;margin:2rem">
  <h1>Abeg order-agent demo</h1>
  <p>The storefront UI has not been built yet. The API is live:</p>
  <ul>
    <li><code>GET /api/state</code></li>
    <li><code>GET /api/products</code></li>
    <li><code>GET /api/events</code> (operator SSE)</li>
    <li><code>POST /api/chat</code> (chat SSE)</li>
  </ul>
</body>
</html>
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _state() -> dict:
    return {
        "guardrails": settings.guardrails,
        "cached_mode": settings.cached_mode,
        "on_task": settings.on_task,
        "llm_provider": settings.llm_provider,
        "stt_provider": settings.stt_provider,
        "reservation_ttl_seconds": settings.reservation_ttl_seconds,
        # Workshop knobs.
        "temperature": settings.temperature,
        "max_tool_calls": settings.max_tool_calls,
        "model": settings.openrouter_model,
        "models": AVAILABLE_MODELS,
        "system_prompt": settings.system_prompt or agent.SYSTEM_PROMPT,
        "default_system_prompt": agent.SYSTEM_PROMPT,
        "system_prompt_customized": bool(settings.system_prompt),
    }


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the platform proxy's X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _limit_text(reason: str) -> str:
    if reason == "daily":
        return (
            "This live demo has reached its shared daily limit — it keeps the AI "
            "affordable to run for everyone. Please try again tomorrow, or clone the "
            "repo and run it with your own key. 🙏"
        )
    return (
        "You've reached the demo's limit for now — it keeps the live AI affordable "
        "to run. Give it a few minutes and try again, or clone the repo to run it "
        "yourself. 🙏"
    )


async def _safe_json(request: Request) -> dict:
    """Parse a JSON body, tolerating malformed/empty input (never 500)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


async def _publish_inventory_update(pool) -> list[dict]:
    products = await tools.inventory_snapshot(pool)
    bus.publish(make_event("inventory_update", {"products": products}))
    return products


# --------------------------------------------------------------------------
# TTL sweeper
# --------------------------------------------------------------------------
async def _ttl_sweeper(app: FastAPI) -> None:
    """Every 15s expire stale active reservations and refresh inventory."""
    pool = app.state.pool
    while True:
        try:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            async with pool.acquire() as conn:
                changed = await conn.execute(
                    "UPDATE reservations SET status = 'expired' "
                    "WHERE status = 'active' AND expires_at <= now()"
                )
            # asyncpg returns a command tag like 'UPDATE 3'.
            n = 0
            try:
                n = int(str(changed).rsplit(" ", 1)[-1])
            except (ValueError, IndexError):
                n = 0
            if n > 0:
                await _publish_inventory_update(pool)
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001 -- keep the sweeper alive
            continue


# --------------------------------------------------------------------------
# lifespan
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await db.create_pool()
    app.state.pool = pool
    await db.apply_schema(pool)
    await seed.seed_if_empty(pool)
    # Prime the operator inventory cache.
    bus.set_latest_inventory(await tools.inventory_snapshot(pool))
    sweeper = asyncio.create_task(_ttl_sweeper(app))
    app.state.sweeper = sweeper
    try:
        yield
    finally:
        sweeper.cancel()
        try:
            await sweeper
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await db.close_pool()


app = FastAPI(title="Abeg", lifespan=lifespan)

# Mount the built Vite assets (hashed JS/CSS) when the React build is present.
if _WEB_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_WEB_ASSETS)), name="assets")
# Legacy/dev static (mockups, old vanilla frontend); dir must exist.
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# --------------------------------------------------------------------------
# root + read endpoints
# --------------------------------------------------------------------------
@app.get("/")
async def index():
    # Prefer the built React frontend; fall back to the vanilla page, then a placeholder.
    if _WEB_INDEX.exists():
        return FileResponse(str(_WEB_INDEX))
    if _INDEX_HTML.exists():
        return FileResponse(str(_INDEX_HTML))
    return HTMLResponse(_PLACEHOLDER_HTML)


@app.get("/favicon.svg")
@app.get("/favicon.ico")
async def favicon():
    icon = _WEB_DIST / "favicon.svg"
    if icon.exists():
        return FileResponse(str(icon), media_type="image/svg+xml")
    return HTMLResponse("", status_code=204)


@app.get("/api/state")
async def api_state():
    return _state()


@app.get("/api/products")
async def api_products():
    products = await tools.inventory_snapshot(app.state.pool)
    return {"products": products}


# --------------------------------------------------------------------------
# chat SSE
# --------------------------------------------------------------------------
@app.post("/api/chat")
async def api_chat(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:  # noqa: BLE001 -- malformed body must not 500-crash
        body = {}
    session_id = str(body.get("session_id") or "anon")
    message = str(body.get("message") or "")
    image = str(body.get("image") or "")

    # BYOK: a visitor can bring their own OpenRouter key (header preferred so it
    # isn't echoed in the body). Their calls are billed to them and skip the
    # demo rate limit. Malformed keys are ignored (falls back to the demo key).
    byok = (request.headers.get("x-openrouter-key") or str(body.get("openrouter_key") or "")).strip()
    if byok and not byok.startswith("sk-or-"):
        byok = ""
    using_byok = bool(byok)

    # Abuse/credit guard: bound how much of the live AI a single visitor (or the
    # whole demo, per day) can spend. BYOK users bypass it. Returns a friendly
    # in-chat message instead of calling the model.
    if settings.rate_limit_enabled and not using_byok:
        ok, why = limiter.check(
            f"chat:{_client_ip(request)}",
            settings.chat_ip_limit,
            settings.chat_ip_window_s,
            settings.chat_daily_limit,
        )
        if not ok:
            text = _limit_text(why)

            async def limited():
                ev = make_event("assistant_done", {"text": text}, session_id)
                yield _sse(ev)

            return StreamingResponse(limited(), media_type="text/event-stream", headers=_SSE_HEADERS)

    async def gen():
        try:
            async for event in agent.run_turn(
                app.state.pool, session_id, message, api_key=byok or None, image=image or None
            ):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001
            low = str(exc).lower()
            if using_byok and ("401" in low or "unauthorized" in low):
                ev = make_event(
                    "assistant_done",
                    {
                        "text": "That OpenRouter key was rejected — double-check it under "
                        "“Use your own AI key” in the Workshop. 🔑"
                    },
                    session_id,
                )
            elif any(k in low for k in ("403", "429", "forbidden", "rate limit", "too many")):
                # Upstream (OpenRouter/Deepgram) throttle — show a calm message,
                # not a scary raw error, so the demo never looks broken.
                ev = make_event(
                    "assistant_done",
                    {
                        "text": "The demo's AI is catching its breath — the shared model is briefly "
                        "rate-limited. Please try again in a moment. 🙏"
                    },
                    session_id,
                )
            else:
                ev = make_event("error", {"message": str(exc)}, session_id)
            bus.publish(ev)
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


# --------------------------------------------------------------------------
# operator SSE
# --------------------------------------------------------------------------
@app.get("/api/events")
async def api_events(request: Request):
    async def gen():
        agen = bus.subscribe()
        # A single, long-lived pending __anext__ task. Never cancelled on a poll
        # timeout (cancelling it would unwind subscribe()'s finally and drop the
        # subscription); only cancelled on real teardown.
        next_task = None
        try:
            # Initial retry hint + snapshot state/inventory on connect.
            yield "retry: 3000\n\n"
            products = await tools.inventory_snapshot(app.state.pool)
            yield _sse(make_event("inventory_update", {"products": products}))
            yield _sse(make_event("state", _state()))

            last_keepalive = time.time()
            while True:
                if await request.is_disconnected():
                    break
                if next_task is None:
                    next_task = asyncio.ensure_future(agen.__anext__())
                done, _ = await asyncio.wait({next_task}, timeout=1.0)
                if next_task in done:
                    try:
                        event = next_task.result()
                    except StopAsyncIteration:
                        break
                    next_task = None
                    yield _sse(event)
                now = time.time()
                if now - last_keepalive >= _KEEPALIVE_INTERVAL_SECONDS:
                    last_keepalive = now
                    yield ":keepalive\n\n"
        finally:
            if next_task is not None and not next_task.done():
                next_task.cancel()
                try:
                    await next_task
                except BaseException:  # noqa: BLE001
                    pass
            try:
                await agen.aclose()
            except Exception:  # noqa: BLE001
                pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


# --------------------------------------------------------------------------
# STT WebSocket
# --------------------------------------------------------------------------
@app.websocket("/ws/stt")
async def ws_stt(ws: WebSocket):
    await ws.accept()
    # Credit guard for the voice path (Deepgram is billed per minute of audio).
    if settings.rate_limit_enabled:
        xff = ws.headers.get("x-forwarded-for", "")
        ip = xff.split(",")[0].strip() if xff else (ws.client.host if ws.client else "unknown")
        ok, why = limiter.check(
            f"stt:{ip}", settings.stt_ip_limit, settings.stt_ip_window_s, settings.stt_daily_limit
        )
        if not ok:
            try:
                await ws.send_json({"type": "error", "message": _limit_text(why)})
            except Exception:  # noqa: BLE001
                pass
            await ws.close()
            return
    try:
        await get_stt().relay(ws)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# control endpoints
# --------------------------------------------------------------------------
@app.post("/api/control/guardrails")
async def control_guardrails(request: Request):
    body = await _safe_json(request)
    settings.guardrails = bool(body.get("on"))
    bus.publish(make_event("guardrails", {"on": settings.guardrails}))
    await _publish_inventory_update(app.state.pool)
    return _state()


@app.post("/api/control/cached")
async def control_cached(request: Request):
    body = await _safe_json(request)
    settings.cached_mode = bool(body.get("on"))
    bus.publish(make_event("cached", {"on": settings.cached_mode}))
    return _state()


@app.post("/api/control/on_task")
async def control_on_task(request: Request):
    body = await _safe_json(request)
    settings.on_task = bool(body.get("on"))
    bus.publish(make_event("state", _state()))
    return _state()


@app.post("/api/control/temperature")
async def control_temperature(request: Request):
    body = await _safe_json(request)
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        value = settings.temperature
    # Clamp to a sane, demo-safe range.
    settings.temperature = max(0.0, min(1.5, value))
    bus.publish(make_event("state", _state()))
    return _state()


@app.post("/api/control/model")
async def control_model(request: Request):
    body = await _safe_json(request)
    model = str(body.get("model") or "")
    allowed = {m["id"] for m in AVAILABLE_MODELS}
    if model in allowed:
        settings.openrouter_model = model
        bus.publish(make_event("state", _state()))
    return _state()


@app.post("/api/control/system_prompt")
async def control_system_prompt(request: Request):
    body = await _safe_json(request)
    if body.get("reset"):
        settings.system_prompt = ""
    else:
        prompt = body.get("prompt")
        if isinstance(prompt, str):
            # Cap length so a paste can't blow up the context window.
            settings.system_prompt = prompt.strip()[:4000]
    bus.publish(make_event("state", _state()))
    return _state()


@app.post("/api/control/reset")
async def control_reset():
    pool = app.state.pool
    await seed.reset_seed(pool)
    agent.SESSIONS.clear()
    products = await _publish_inventory_update(pool)
    bus.publish(make_event("notice", {"message": "reset to seed"}))
    return {"products": products}


@app.get("/api/evals")
async def api_evals():
    """List the Report Card cases so the UI can preview the suite."""
    return {"cases": evals.list_cases()}


@app.post("/api/evals/run")
async def api_evals_run():
    """Run the guardrail eval suite against the current settings and return the scorecard."""
    return await evals.run_suite(app.state.pool)


@app.post("/api/control/race")
async def control_race():
    pool = app.state.pool

    async def attempt(session_id: str) -> dict:
        try:
            res = await tools.reserve_items(
                pool, [{"sku": "JOLLOF", "qty": 1}], session_id
            )
            if res.get("refused") or not res.get("reservation_id"):
                return {"ok": False, "error": res.get("reason") or res.get("error") or "reserve refused"}
            order = await tools.place_order(
                pool, res["reservation_id"], session_id, session_id=session_id
            )
            if order.get("error"):
                return {"ok": False, "error": order.get("error")}
            return {"ok": True, "reference": order.get("reference"), "total": order.get("total")}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # Create both coroutines first, then gather so they truly run concurrently.
    coro_a = attempt("race-A")
    coro_b = attempt("race-B")
    results = await asyncio.gather(coro_a, coro_b)

    products = await _publish_inventory_update(pool)
    jollof = next((p for p in products if p["sku"] == "JOLLOF"), None)
    return {
        "results": list(results),
        "guardrails": settings.guardrails,
        "jollof_qty_on_hand": jollof["qty_on_hand"] if jollof else None,
        "jollof_available": jollof["available"] if jollof else None,
    }


_SCRIPTED_MESSAGES = {
    1: "What do you have?",
    2: "I'll take two beef suya",
    3: "Do you have 50 chin chin?",
    4: (
        "I'd like to order party jollof rice, but before I can eat, I need to figure "
        "out how to write a Python script to reverse a linked list. Can you help?"
    ),
}


@app.get("/api/control/scripts")
async def control_scripts():
    """List the scripted customer messages so the UI can preview them."""
    return {"scripts": [{"n": n, "message": _SCRIPTED_MESSAGES[n]} for n in sorted(_SCRIPTED_MESSAGES)]}


@app.post("/api/control/scripted")
async def control_scripted(request: Request):
    body = await _safe_json(request)
    try:
        n = int(body.get("n"))
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(4, n))
    return {"message": _SCRIPTED_MESSAGES[n]}
