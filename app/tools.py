"""The 5 async order-agent tools.

Every tool publishes a `tool_call` event before running and a `tool_result`
event after (with duration_ms). Any tool that mutates stock or reservations
publishes an `inventory_update` afterward. All money is recomputed from the DB
price — never trusted from model input. Unknown SKUs are rejected outright
(no fuzzy matching into a different product).

`reserve_items` and `place_order` implement BOTH a guarded path (row locks in a
single transaction) and a naive path (no locks, separate acquires), branching on
`settings.guardrails`.
"""
import asyncio
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from app import db
from app.config import settings
from app.events import bus, make_event


# --------------------------------------------------------------------------
# inventory helpers
# --------------------------------------------------------------------------
async def inventory_snapshot(pool: asyncpg.Pool) -> list[dict]:
    """Return the full product list with live availability.

    Shape: [{sku, name, price, qty_on_hand, available}] ordered by sku.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sku, name, price, qty_on_hand FROM products ORDER BY sku"
        )
        avail = await db.available_map(conn)
    return [
        {
            "sku": r["sku"],
            "name": r["name"],
            "price": float(r["price"]),
            "qty_on_hand": int(r["qty_on_hand"]),
            "available": int(avail.get(r["sku"], r["qty_on_hand"])),
        }
        for r in rows
    ]


async def _publish_inventory_update(pool: asyncpg.Pool, session_id: str | None = None) -> None:
    products = await inventory_snapshot(pool)
    bus.publish(make_event("inventory_update", {"products": products}, session_id))


def _new_reference() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "ABEG-" + "".join(secrets.choice(alphabet) for _ in range(6))


# --------------------------------------------------------------------------
# event-instrumentation wrapper
# --------------------------------------------------------------------------
def _emit_call(name: str, args: dict, session_id: str | None) -> str:
    call_id = uuid.uuid4().hex[:12]
    bus.publish(make_event("tool_call", {"name": name, "args": args, "call_id": call_id}, session_id))
    return call_id


def _emit_result(name: str, call_id: str, result: dict, started: float, session_id: str | None) -> None:
    duration_ms = int((time.perf_counter() - started) * 1000)
    bus.publish(
        make_event(
            "tool_result",
            {"name": name, "call_id": call_id, "result": result, "duration_ms": duration_ms},
            session_id,
        )
    )


# --------------------------------------------------------------------------
# Tool 1: search_inventory
# --------------------------------------------------------------------------
async def search_inventory(pool: asyncpg.Pool, query: str = "", limit: int = 10, session_id: str | None = None) -> dict:
    call_id = _emit_call("search_inventory", {"query": query, "limit": limit}, session_id)
    started = time.perf_counter()
    q = (query or "").strip()
    async with pool.acquire() as conn:
        if q:
            like = f"%{q}%"
            rows = await conn.fetch(
                "SELECT sku, name, price FROM products "
                "WHERE name ILIKE $1 OR sku ILIKE $1 ORDER BY sku LIMIT $2",
                like,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT sku, name, price FROM products ORDER BY sku LIMIT $1",
                limit,
            )
        skus = [r["sku"] for r in rows]
        avail = await db.available_map(conn, skus) if skus else {}
    result = {
        "products": [
            {
                "sku": r["sku"],
                "name": r["name"],
                "price": float(r["price"]),
                "available": int(avail.get(r["sku"], 0)),
            }
            for r in rows
        ]
    }
    _emit_result("search_inventory", call_id, result, started, session_id)
    return result


# --------------------------------------------------------------------------
# Tool 2: check_stock
# --------------------------------------------------------------------------
async def check_stock(pool: asyncpg.Pool, sku: str, session_id: str | None = None) -> dict:
    call_id = _emit_call("check_stock", {"sku": sku}, session_id)
    started = time.perf_counter()
    sku_norm = (sku or "").strip().upper()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT sku, name FROM products WHERE sku = $1", sku_norm)
        if row is None:
            result = {"error": "unknown_sku", "sku": sku}
        else:
            avail = await db.available_map(conn, [sku_norm])
            result = {"sku": row["sku"], "name": row["name"], "available": int(avail.get(sku_norm, 0))}
    _emit_result("check_stock", call_id, result, started, session_id)
    return result


# --------------------------------------------------------------------------
# Tool 3: reserve_items
# --------------------------------------------------------------------------
def _normalize_items(items) -> list[dict]:
    """Coalesce/validate raw items into {sku(upper), qty(int)} sorted by sku.

    Tolerant of the ways small tool-calling models mangle arguments: `items`
    may arrive as a JSON string, a single dict, or a list; individual entries
    may be non-dicts. Anything unparseable is skipped rather than crashing.
    """
    # A model may stringify the whole array.
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (json.JSONDecodeError, ValueError):
            return []
    # A model may send a single item object instead of a list.
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, (list, tuple)):
        return []

    merged: dict[str, int] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        sku = str(it.get("sku", "")).strip().upper()
        try:
            qty = int(it.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if not sku or qty <= 0:
            continue
        merged[sku] = merged.get(sku, 0) + qty
    return [{"sku": s, "qty": merged[s]} for s in sorted(merged)]


async def reserve_items(pool: asyncpg.Pool, items: list[dict], session_id: str) -> dict:
    call_id = _emit_call("reserve_items", {"items": items}, session_id)
    started = time.perf_counter()
    try:
        result = await _reserve_items_impl(pool, items, session_id)
    finally:
        pass
    _emit_result("reserve_items", call_id, result, started, session_id)
    if not result.get("refused"):
        await _publish_inventory_update(pool, session_id)
    # Publish a reservation event either way (refused or accepted).
    if result.get("refused"):
        bus.publish(
            make_event(
                "reservation",
                {
                    "reservation_id": None,
                    "expires_at": None,
                    "items": items,
                    "refused": True,
                    "reason": result.get("reason"),
                },
                session_id,
            )
        )
    else:
        bus.publish(
            make_event(
                "reservation",
                {
                    "reservation_id": result["reservation_id"],
                    "expires_at": result["expires_at"],
                    "items": result["items"],
                    "refused": False,
                },
                session_id,
            )
        )
    return result


async def _reserve_items_impl(pool: asyncpg.Pool, items: list[dict], session_id: str) -> dict:
    norm = _normalize_items(items)
    if not norm:
        return {"error": "insufficient_stock", "refused": True, "reason": "no valid items", "details": []}

    skus = [it["sku"] for it in norm]
    ttl = settings.reservation_ttl_seconds
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    if settings.guardrails:
        return await _reserve_guarded(pool, norm, skus, session_id, expires_at)
    return await _reserve_naive(pool, norm, skus, session_id, expires_at)


async def _reserve_guarded(pool, norm, skus, session_id, expires_at) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Deterministic sku order lock to avoid deadlocks.
            locked = await conn.fetch(
                "SELECT sku, name, price, qty_on_hand FROM products "
                "WHERE sku = ANY($1::text[]) ORDER BY sku FOR UPDATE",
                skus,
            )
            found = {r["sku"]: r for r in locked}
            unknown = [s for s in skus if s not in found]
            if unknown:
                return {
                    "error": "unknown_sku",
                    "refused": True,
                    "reason": f"unknown sku(s): {', '.join(unknown)}",
                    "details": [{"sku": s} for s in unknown],
                }

            # Availability inside the tx (locked rows).
            reserved_rows = await conn.fetch(
                """
                SELECT ri.sku, COALESCE(SUM(ri.qty), 0) AS reserved
                FROM reservation_items ri
                JOIN reservations r ON r.id = ri.reservation_id
                WHERE ri.sku = ANY($1::text[])
                  AND r.status = 'active'
                  AND r.expires_at > now()
                GROUP BY ri.sku
                """,
                skus,
            )
            reserved = {r["sku"]: int(r["reserved"]) for r in reserved_rows}

            details = []
            ok = True
            for it in norm:
                row = found[it["sku"]]
                available = int(row["qty_on_hand"]) - reserved.get(it["sku"], 0)
                satisfiable = available >= it["qty"]
                if not satisfiable:
                    ok = False
                details.append(
                    {"sku": it["sku"], "requested": it["qty"], "available": available, "ok": satisfiable}
                )

            if not ok:
                short = [d["sku"] for d in details if not d["ok"]]
                return {
                    "error": "insufficient_stock",
                    "refused": True,
                    "reason": f"insufficient stock for: {', '.join(short)}",
                    "details": details,
                }

            res_id = await conn.fetchval(
                "INSERT INTO reservations (session_id, expires_at, status) "
                "VALUES ($1, $2, 'active') RETURNING id",
                session_id,
                expires_at,
            )
            await conn.executemany(
                "INSERT INTO reservation_items (reservation_id, sku, qty) VALUES ($1, $2, $3)",
                [(res_id, it["sku"], it["qty"]) for it in norm],
            )
            out_items = [
                {"sku": it["sku"], "qty": it["qty"], "unit_price": float(found[it["sku"]]["price"])}
                for it in norm
            ]
            total = sum(i["unit_price"] * i["qty"] for i in out_items)
            return {
                "reservation_id": str(res_id),
                "expires_at": expires_at.isoformat(),
                "items": out_items,
                "total": round(total, 2),
            }


async def _reserve_naive(pool, norm, skus, session_id, expires_at) -> dict:
    # Read availability with NO lock, NO transaction (separate acquire).
    async with pool.acquire() as conn:
        prod_rows = await conn.fetch(
            "SELECT sku, name, price, qty_on_hand FROM products WHERE sku = ANY($1::text[])",
            skus,
        )
        found = {r["sku"]: r for r in prod_rows}
        unknown = [s for s in skus if s not in found]
        if unknown:
            return {
                "error": "unknown_sku",
                "refused": True,
                "reason": f"unknown sku(s): {', '.join(unknown)}",
                "details": [{"sku": s} for s in unknown],
            }
        avail = await db.available_map(conn, skus)

    # Naive: widen the read->write window so concurrent attempts reliably both
    # read stale availability and both proceed. This is what makes the oversell
    # deterministic on stage (guarded path has no such gap — it holds the lock).
    await asyncio.sleep(0.1)

    details = []
    ok = True
    for it in norm:
        available = int(avail.get(it["sku"], 0))
        satisfiable = available >= it["qty"]
        if not satisfiable:
            ok = False
        details.append({"sku": it["sku"], "requested": it["qty"], "available": available, "ok": satisfiable})

    if not ok:
        short = [d["sku"] for d in details if not d["ok"]]
        return {
            "error": "insufficient_stock",
            "refused": True,
            "reason": f"insufficient stock for: {', '.join(short)}",
            "details": details,
        }

    # Separate acquire for the write — this is what oversells under the race.
    async with pool.acquire() as conn:
        res_id = await conn.fetchval(
            "INSERT INTO reservations (session_id, expires_at, status) "
            "VALUES ($1, $2, 'active') RETURNING id",
            session_id,
            expires_at,
        )
        await conn.executemany(
            "INSERT INTO reservation_items (reservation_id, sku, qty) VALUES ($1, $2, $3)",
            [(res_id, it["sku"], it["qty"]) for it in norm],
        )
    out_items = [
        {"sku": it["sku"], "qty": it["qty"], "unit_price": float(found[it["sku"]]["price"])} for it in norm
    ]
    total = sum(i["unit_price"] * i["qty"] for i in out_items)
    return {
        "reservation_id": str(res_id),
        "expires_at": expires_at.isoformat(),
        "items": out_items,
        "total": round(total, 2),
    }


# --------------------------------------------------------------------------
# Tool 4: place_order
# --------------------------------------------------------------------------
async def place_order(
    pool: asyncpg.Pool,
    reservation_id: str,
    customer_name: str = "",
    idempotency_key: str | None = None,
    session_id: str | None = None,
) -> dict:
    call_id = _emit_call(
        "place_order",
        {"reservation_id": reservation_id, "customer_name": customer_name, "idempotency_key": idempotency_key},
        session_id,
    )
    started = time.perf_counter()
    if settings.guardrails:
        result = await _place_order_guarded(pool, reservation_id, customer_name, idempotency_key)
    else:
        result = await _place_order_naive(pool, reservation_id, customer_name)
    _emit_result("place_order", call_id, result, started, session_id)

    if not result.get("error"):
        bus.publish(
            make_event(
                "order_created",
                {"reference": result["reference"], "total": result["total"], "items": result["items"]},
                session_id,
            )
        )
        await _publish_inventory_update(pool, session_id)
    return result


def _parse_res_id(reservation_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(reservation_id))
    except (ValueError, TypeError, AttributeError):
        return None


async def _place_order_guarded(pool, reservation_id, customer_name, idempotency_key) -> dict:
    res_uuid = _parse_res_id(reservation_id)
    if res_uuid is None:
        return {"error": "invalid_reservation", "reason": "bad reservation id"}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Idempotency: return the existing order without any decrement.
            if idempotency_key:
                existing = await conn.fetchrow(
                    "SELECT order_id FROM idempotency_keys WHERE key = $1", idempotency_key
                )
                if existing and existing["order_id"]:
                    return await _load_order(conn, existing["order_id"])

            res = await conn.fetchrow(
                "SELECT id, status FROM reservations WHERE id = $1 FOR UPDATE", res_uuid
            )
            if res is None:
                return {"error": "unknown_reservation", "reason": "reservation not found"}
            if res["status"] != "active":
                return {"error": "reservation_not_active", "reason": f"status={res['status']}"}

            item_rows = await conn.fetch(
                """
                SELECT ri.sku, ri.qty, p.price, p.name
                FROM reservation_items ri
                JOIN products p ON p.sku = ri.sku
                WHERE ri.reservation_id = $1
                ORDER BY ri.sku
                FOR UPDATE OF p
                """,
                res_uuid,
            )
            if not item_rows:
                return {"error": "empty_reservation", "reason": "no items"}

            items = [
                {"sku": r["sku"], "qty": int(r["qty"]), "unit_price": float(r["price"])} for r in item_rows
            ]
            total = round(sum(i["unit_price"] * i["qty"] for i in items), 2)
            reference = await _unique_reference(conn)

            order_id = await conn.fetchval(
                "INSERT INTO orders (reference, customer_name, total) VALUES ($1, $2, $3) RETURNING id",
                reference,
                customer_name or None,
                total,
            )
            await conn.executemany(
                "INSERT INTO order_items (order_id, sku, qty, unit_price) VALUES ($1, $2, $3, $4)",
                [(order_id, i["sku"], i["qty"], i["unit_price"]) for i in items],
            )
            for i in items:
                await conn.execute(
                    "UPDATE products SET qty_on_hand = qty_on_hand - $1 WHERE sku = $2",
                    i["qty"],
                    i["sku"],
                )
            await conn.execute(
                "UPDATE reservations SET status = 'consumed' WHERE id = $1", res_uuid
            )
            if idempotency_key:
                await conn.execute(
                    "INSERT INTO idempotency_keys (key, order_id) VALUES ($1, $2) "
                    "ON CONFLICT (key) DO NOTHING",
                    idempotency_key,
                    order_id,
                )
            return {
                "order_id": str(order_id),
                "reference": reference,
                "total": total,
                "items": items,
            }


async def _place_order_naive(pool, reservation_id, customer_name) -> dict:
    res_uuid = _parse_res_id(reservation_id)
    if res_uuid is None:
        return {"error": "invalid_reservation", "reason": "bad reservation id"}

    # No single wrapping transaction; read-modify-write across acquires.
    # Reservation may be missing/expired but we proceed anyway (can oversell).
    async with pool.acquire() as conn:
        item_rows = await conn.fetch(
            """
            SELECT ri.sku, ri.qty, p.price, p.name
            FROM reservation_items ri
            JOIN products p ON p.sku = ri.sku
            WHERE ri.reservation_id = $1
            ORDER BY ri.sku
            """,
            res_uuid,
        )
    if not item_rows:
        return {"error": "empty_reservation", "reason": "no items"}

    items = [{"sku": r["sku"], "qty": int(r["qty"]), "unit_price": float(r["price"])} for r in item_rows]
    total = round(sum(i["unit_price"] * i["qty"] for i in items), 2)

    async with pool.acquire() as conn:
        reference = await _unique_reference(conn)
        order_id = await conn.fetchval(
            "INSERT INTO orders (reference, customer_name, total) VALUES ($1, $2, $3) RETURNING id",
            reference,
            customer_name or None,
            total,
        )
        await conn.executemany(
            "INSERT INTO order_items (order_id, sku, qty, unit_price) VALUES ($1, $2, $3, $4)",
            [(order_id, i["sku"], i["qty"], i["unit_price"]) for i in items],
        )
    # Decrement with NO lock and NO availability check — each order blindly
    # subtracts what it sold. Two orders for the last unit both run, so the
    # counter reliably goes negative: the visible payoff of act two.
    for i in items:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE products SET qty_on_hand = qty_on_hand - $1 WHERE sku = $2",
                i["qty"],
                i["sku"],
            )
    async with pool.acquire() as conn:
        await conn.execute("UPDATE reservations SET status = 'consumed' WHERE id = $1", res_uuid)

    return {"order_id": str(order_id), "reference": reference, "total": total, "items": items}


async def _unique_reference(conn) -> str:
    for _ in range(20):
        ref = _new_reference()
        exists = await conn.fetchval("SELECT 1 FROM orders WHERE reference = $1", ref)
        if not exists:
            return ref
    return _new_reference()


async def _load_order(conn, order_id) -> dict:
    order = await conn.fetchrow(
        "SELECT id, reference, total FROM orders WHERE id = $1", order_id
    )
    rows = await conn.fetch(
        "SELECT sku, qty, unit_price FROM order_items WHERE order_id = $1 ORDER BY sku", order_id
    )
    return {
        "order_id": str(order["id"]),
        "reference": order["reference"],
        "total": float(order["total"]),
        "items": [
            {"sku": r["sku"], "qty": int(r["qty"]), "unit_price": float(r["unit_price"])} for r in rows
        ],
    }


# --------------------------------------------------------------------------
# Tool 5: cancel_reservation
# --------------------------------------------------------------------------
async def cancel_reservation(pool: asyncpg.Pool, reservation_id: str, session_id: str | None = None) -> dict:
    call_id = _emit_call("cancel_reservation", {"reservation_id": reservation_id}, session_id)
    started = time.perf_counter()
    res_uuid = _parse_res_id(reservation_id)
    if res_uuid is None:
        result = {"error": "invalid_reservation", "ok": False}
    else:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE reservations SET status = 'cancelled' WHERE id = $1 AND status = 'active'",
                res_uuid,
            )
        result = {"ok": True}
    _emit_result("cancel_reservation", call_id, result, started, session_id)
    if result.get("ok"):
        await _publish_inventory_update(pool, session_id)
    return result


# --------------------------------------------------------------------------
# Tool 6: lookup_order
# --------------------------------------------------------------------------
async def lookup_order(pool: asyncpg.Pool, reference: str, session_id: str | None = None) -> dict:
    """Read-only order status by reference. Never fabricates a status."""
    call_id = _emit_call("lookup_order", {"reference": reference}, session_id)
    started = time.perf_counter()
    ref = (reference or "").strip().upper()
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT id, reference, customer_name, total, status, created_at "
            "FROM orders WHERE reference = $1",
            ref,
        )
        if order is None:
            result = {"error": "unknown_order", "reference": reference}
        else:
            rows = await conn.fetch(
                "SELECT oi.sku, p.name, oi.qty, oi.unit_price "
                "FROM order_items oi JOIN products p ON p.sku = oi.sku "
                "WHERE oi.order_id = $1 ORDER BY oi.sku",
                order["id"],
            )
            result = {
                "reference": order["reference"],
                "status": order["status"],
                "customer_name": order["customer_name"],
                "total": float(order["total"]),
                "created_at": order["created_at"].isoformat(),
                "items": [
                    {
                        "sku": r["sku"],
                        "name": r["name"],
                        "qty": int(r["qty"]),
                        "unit_price": float(r["unit_price"]),
                    }
                    for r in rows
                ],
            }
    _emit_result("lookup_order", call_id, result, started, session_id)
    return result


# --------------------------------------------------------------------------
# Tool 7: recommend_items
# --------------------------------------------------------------------------
async def recommend_items(pool: asyncpg.Pool, limit: int = 3, session_id: str | None = None) -> dict:
    """Suggest popular in-stock items only.

    Popularity is real order history (sum of ordered qty from non-cancelled
    orders). Anything out of stock is filtered out, so the model can only ever
    recommend items that actually exist and are available right now.
    """
    call_id = _emit_call("recommend_items", {"limit": limit}, session_id)
    started = time.perf_counter()
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(5, n))
    async with pool.acquire() as conn:
        avail = await db.available_map(conn)
        rows = await conn.fetch(
            """
            SELECT p.sku, p.name, p.price, COALESCE(SUM(oi.qty), 0) AS sold
            FROM products p
            LEFT JOIN order_items oi ON oi.sku = p.sku
            LEFT JOIN orders o ON o.id = oi.order_id AND o.status = 'placed'
            GROUP BY p.sku, p.name, p.price
            ORDER BY sold DESC, p.sku
            """
        )
    picked = [r for r in rows if int(avail.get(r["sku"], 0)) > 0][:n]
    result = {
        "recommendations": [
            {
                "sku": r["sku"],
                "name": r["name"],
                "price": float(r["price"]),
                "available": int(avail.get(r["sku"], 0)),
            }
            for r in picked
        ]
    }
    _emit_result("recommend_items", call_id, result, started, session_id)
    return result


# --------------------------------------------------------------------------
# Tool 8: cancel_order
# --------------------------------------------------------------------------
async def cancel_order(
    pool: asyncpg.Pool, reference: str, confirm: bool = False, session_id: str | None = None
) -> dict:
    """Cancel a placed order and restock its items.

    Cancelling is destructive, so it is gated: unless `confirm` is true this does
    NOTHING and returns needs_confirmation. That way a single mis-selected call
    (for example the model reaching for cancel on a status question) can never
    destroy an order. The caller must confirm with the customer first, then call
    again with confirm=true.

    When confirmed it branches on settings.guardrails, matching reserve_items and
    place_order: the guarded path locks the order in one transaction and is
    idempotent, while the naive path can double-restock under two concurrent
    cancels of the same order.
    """
    call_id = _emit_call("cancel_order", {"reference": reference, "confirm": confirm}, session_id)
    started = time.perf_counter()
    if not confirm:
        result = {
            "reference": reference,
            "needs_confirmation": True,
            "cancelled": False,
            "message": "Confirm the cancellation with the customer, then call again with confirm=true.",
        }
        _emit_result("cancel_order", call_id, result, started, session_id)
        return result
    if settings.guardrails:
        result = await _cancel_order_guarded(pool, reference)
    else:
        result = await _cancel_order_naive(pool, reference)
    _emit_result("cancel_order", call_id, result, started, session_id)
    if result.get("cancelled"):
        bus.publish(
            make_event(
                "order_updated",
                {
                    "reference": result.get("reference"),
                    "status": "cancelled",
                    "items": result.get("restocked", []),
                },
                session_id,
            )
        )
        await _publish_inventory_update(pool, session_id)
    return result


async def _cancel_order_guarded(pool, reference) -> dict:
    ref = (reference or "").strip().upper()
    async with pool.acquire() as conn:
        async with conn.transaction():
            order = await conn.fetchrow(
                "SELECT id, reference, status, total FROM orders WHERE reference = $1 FOR UPDATE",
                ref,
            )
            if order is None:
                return {"error": "unknown_order", "reference": reference}
            if order["status"] == "cancelled":
                return {
                    "reference": order["reference"],
                    "status": "cancelled",
                    "cancelled": False,
                    "already_cancelled": True,
                    "restocked": [],
                }
            item_rows = await conn.fetch(
                "SELECT oi.sku, oi.qty, p.name "
                "FROM order_items oi JOIN products p ON p.sku = oi.sku "
                "WHERE oi.order_id = $1 ORDER BY oi.sku FOR UPDATE OF p",
                order["id"],
            )
            for r in item_rows:
                await conn.execute(
                    "UPDATE products SET qty_on_hand = qty_on_hand + $1 WHERE sku = $2",
                    int(r["qty"]),
                    r["sku"],
                )
            await conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = $1", order["id"]
            )
            return {
                "reference": order["reference"],
                "status": "cancelled",
                "cancelled": True,
                "restocked": [
                    {"sku": r["sku"], "name": r["name"], "qty": int(r["qty"])} for r in item_rows
                ],
                "total": float(order["total"]),
            }


async def _cancel_order_naive(pool, reference) -> dict:
    ref = (reference or "").strip().upper()
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT id, reference, status, total FROM orders WHERE reference = $1", ref
        )
        if order is None:
            return {"error": "unknown_order", "reference": reference}
        if order["status"] == "cancelled":
            return {
                "reference": order["reference"],
                "status": "cancelled",
                "cancelled": False,
                "already_cancelled": True,
                "restocked": [],
            }
        item_rows = await conn.fetch(
            "SELECT oi.sku, oi.qty, p.name "
            "FROM order_items oi JOIN products p ON p.sku = oi.sku "
            "WHERE oi.order_id = $1 ORDER BY oi.sku",
            order["id"],
        )
    # Widen the read to write window so two concurrent cancels both read 'placed'
    # and both restock (a lost update that over-restocks), the cancel-side mirror
    # of the oversell race.
    await asyncio.sleep(0.1)
    for r in item_rows:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE products SET qty_on_hand = qty_on_hand + $1 WHERE sku = $2",
                int(r["qty"]),
                r["sku"],
            )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET status = 'cancelled' WHERE id = $1", order["id"]
        )
    return {
        "reference": order["reference"],
        "status": "cancelled",
        "cancelled": True,
        "restocked": [
            {"sku": r["sku"], "name": r["name"], "qty": int(r["qty"])} for r in item_rows
        ],
        "total": float(order["total"]),
    }


# --------------------------------------------------------------------------
# Model-facing schemas + dispatch map
# --------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_inventory",
            "description": "Search the menu by name or SKU. Empty query returns all items. Returns sku, name, price and available quantity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to match against product name or SKU. Empty for all."},
                    "limit": {"type": "integer", "description": "Max results.", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check live availability for one exact SKU. Returns unknown_sku if the SKU does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "description": "Exact product SKU, e.g. JOLLOF."},
                },
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reserve_items",
            "description": "Reserve one or more items by SKU and quantity. Recomputes prices from the catalog. Refuses on unknown SKU or insufficient stock. Do NOT pass a session id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "qty": {"type": "integer"},
                            },
                            "required": ["sku", "qty"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_order",
            "description": "Place an order for an existing active reservation. Call this immediately once the customer confirms (e.g. 'yes'). customer_name is optional — omit it if the customer did not give one. Total is recomputed from the catalog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reservation_id": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
                "required": ["reservation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reservation",
            "description": "Cancel an active reservation, releasing its held stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reservation_id": {"type": "string"},
                },
                "required": ["reservation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Use this for ANY question about an existing order: its status, what is in it, or its total. Read only, changes nothing. Takes the order reference (for example ABEG-7F3K9Q) and returns status, items, quantities, unit prices and total. Returns unknown_order if the reference does not exist. This is the tool for 'what is the status of my order', never cancel_order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {"type": "string", "description": "Order reference, e.g. ABEG-7F3K9Q."},
                },
                "required": ["reference"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_items",
            "description": "Suggest popular in-stock menu items. Only ever returns items that exist and are currently available. Use when the customer asks what is good, what to get, or for a suggestion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many items to suggest.", "default": 3},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel a placed order and return its items to stock. ONLY when the customer explicitly asks to cancel, never on a status question. Destructive and gated: first show the order (use lookup_order) and get the customer's explicit yes, then call this with confirm=true. Called without confirm=true it does NOT cancel; it only signals that confirmation is still needed. Returns unknown_order if the reference does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {"type": "string", "description": "Order reference, e.g. ABEG-7F3K9Q."},
                    "confirm": {
                        "type": "boolean",
                        "description": "Must be true to actually cancel. Set it only after the customer explicitly confirms.",
                        "default": False,
                    },
                },
                "required": ["reference"],
            },
        },
    },
]


TOOL_FUNCS = {
    "search_inventory": search_inventory,
    "check_stock": check_stock,
    "reserve_items": reserve_items,
    "place_order": place_order,
    "cancel_reservation": cancel_reservation,
    "lookup_order": lookup_order,
    "recommend_items": recommend_items,
    "cancel_order": cancel_order,
}
