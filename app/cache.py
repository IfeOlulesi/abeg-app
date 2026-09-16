"""Canned, deterministic LLM sequences for CachedLlm (offline demo mode).

Keyed by normalized user text (lowercase, stripped, collapsed whitespace).
Each entry maps to a list of "steps" that CachedLlm replays in order. Each step
is one of:

    {"type": "delta", "text": "..."}                       -> streamed text
    {"type": "tool_calls", "tool_calls": [                  -> tool call batch
        {"name": "reserve_items", "arguments": {...}}
    ]}
    {"type": "done", "finish_reason": "stop"|"tool_calls"}  -> optional terminator

CachedLlm inspects the LAST user message. There is a separate `CONFIRM_STEPS`
sequence used when the last user message is a confirmation (yes/confirm/...) so
the demo can progress from reserve_items -> place_order. Tool-call arguments
reference real seed SKUs (JOLLOF, SUYA, PUFFPUFF, ...).

Note: `reserve_items` args here intentionally omit session_id — the agent injects
it server-side. `place_order` in CONFIRM steps uses a placeholder reservation_id
of "$LAST_RESERVATION" which CachedLlm/agent resolves to the most recent
reservation id from the tool results of the current session.
"""
import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# Confirmation words that should advance a pending reservation to an order.
CONFIRM_WORDS = {
    "yes", "yeah", "yep", "yup", "confirm", "confirmed", "ok", "okay", "sure",
    "go ahead", "place it", "place the order", "do it", "correct", "please do",
}


def is_confirmation(text: str) -> bool:
    n = normalize(text)
    if n in CONFIRM_WORDS:
        return True
    return any(w in n for w in ("confirm", "place the order", "go ahead", "yes please"))


# Steps replayed when the user confirms a pending reservation.
CONFIRM_STEPS = [
    {"type": "delta", "text": "Great — placing your order now."},
    {
        "type": "tool_calls",
        "tool_calls": [
            {
                "name": "place_order",
                "arguments": {"reservation_id": "$LAST_RESERVATION", "customer_name": "Guest"},
            }
        ],
    },
    {"type": "delta", "text": " Your order is confirmed. Thank you!"},
    {"type": "done", "finish_reason": "stop"},
]


# Steps replayed when the user declines.
DECLINE_STEPS = [
    {"type": "delta", "text": "No problem — I won't place the order. Anything else?"},
    {"type": "done", "finish_reason": "stop"},
]


# Steps replayed when the user sends an order note / catering list image.
NOTE_STEPS = [
    {"type": "delta", "text": "I can read your order note! Let me check live inventory for you."},
    {
        "type": "tool_calls",
        "tool_calls": [
            {
                "name": "reserve_items",
                "arguments": {
                    "items": [
                        {"sku": "SUYA", "qty": 2},
                        {"sku": "ZOBO", "qty": 1},
                        {"sku": "PUFFPUFF", "qty": 1},
                    ]
                },
            }
        ],
    },
    {
        "type": "delta",
        "text": "I read your note and held: 2x Beef Suya (₦4,000), 1x Zobo Drink (₦600), and 1x Puff Puff (₦800). Total: ₦5,400. Shall I place the order? Please confirm.",
    },
    {"type": "done", "finish_reason": "stop"},
]


# The 4 scripted messages from the contract. These are intentionally
# JOLLOF-free so the last unit stays reserved for the oversell race demo.
SCRIPTS: dict[str, list[dict]] = {
    # 1) search / list the menu.
    normalize("What do you have?"): [
        {"type": "delta", "text": "Let me check the menu for you."},
        {
            "type": "tool_calls",
            "tool_calls": [{"name": "search_inventory", "arguments": {"query": "", "limit": 10}}],
        },
        {
            "type": "delta",
            "text": "Here's what we have today — jollof rice, suya, puff puff, chin chin, zobo, meat pie, plantain, moi moi, egusi and pepper soup. What would you like?",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 2) reserve 2x SUYA, then confirm -> place_order (clean success).
    normalize("I'll take two beef suya"): [
        {"type": "delta", "text": "Sure, let me reserve two Beef Suya."},
        {
            "type": "tool_calls",
            "tool_calls": [
                {"name": "reserve_items", "arguments": {"items": [{"sku": "SUYA", "qty": 2}]}}
            ],
        },
        {
            "type": "delta",
            "text": "That's 2x Beef Suya at 2,000 each, total 4,000. Shall I place the order? Please confirm.",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 3) reserve 3x PUFFPUFF + 1x ZOBO, then confirm -> place_order.
    normalize("Can I get three puff puff and one zobo?"): [
        {"type": "delta", "text": "Reserving three Puff Puff and one Zobo."},
        {
            "type": "tool_calls",
            "tool_calls": [
                {
                    "name": "reserve_items",
                    "arguments": {
                        "items": [{"sku": "PUFFPUFF", "qty": 3}, {"sku": "ZOBO", "qty": 1}]
                    },
                }
            ],
        },
        {
            "type": "delta",
            "text": "That's 3x Puff Puff and 1x Zobo. Want me to place the order? Please confirm.",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 4) 50 CHINCHIN -> out of stock; report only 15 available, offer it, NO reservation.
    normalize("Do you have 50 chin chin?"): [
        {"type": "delta", "text": "Let me check chin chin availability."},
        {
            "type": "tool_calls",
            "tool_calls": [
                {"name": "check_stock", "arguments": {"sku": "CHINCHIN"}}
            ],
        },
        {
            "type": "delta",
            "text": "Sorry, we don't have 50 chin chin — only 15 packs are available right now. Would you like those 15?",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 5) recommend_items -> grounded suggestions (only real, in-stock items).
    normalize("What do you recommend?"): [
        {"type": "delta", "text": "Let me suggest a few favourites."},
        {
            "type": "tool_calls",
            "tool_calls": [{"name": "recommend_items", "arguments": {"limit": 3}}],
        },
        {
            "type": "delta",
            "text": "Here are a few popular picks that are in stock right now. Want any of these?",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 6) lookup_order for a reference that does not exist -> reports not found,
    #    never invents a status. Used by the Report Card grounding case.
    normalize("What is the status of order ABEG-NOPE00?"): [
        {"type": "delta", "text": "Let me check that order for you."},
        {
            "type": "tool_calls",
            "tool_calls": [
                {"name": "lookup_order", "arguments": {"reference": "ABEG-NOPE00"}}
            ],
        },
        {
            "type": "delta",
            "text": "I couldn't find an order with that reference. Please double check it.",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
    # 7) cancel_order -> resolves $LAST_ORDER to the most recent order reference
    #    seen in this session's tool results, then restocks it.
    normalize("Cancel my last order"): [
        {"type": "delta", "text": "Cancelling your most recent order."},
        {
            "type": "tool_calls",
            "tool_calls": [
                {"name": "cancel_order", "arguments": {"reference": "$LAST_ORDER", "confirm": True}}
            ],
        },
        {
            "type": "delta",
            "text": "Done. Your order is cancelled and the items are back in stock.",
        },
        {"type": "done", "finish_reason": "stop"},
    ],
}


# Generic grounded fallback when no script matches.
FALLBACK_STEPS = [
    {"type": "delta", "text": "Let me check our menu for you."},
    {
        "type": "tool_calls",
        "tool_calls": [{"name": "search_inventory", "arguments": {"query": "", "limit": 10}}],
    },
    {
        "type": "delta",
        "text": "Here's what we have. Tell me what you'd like and the quantity, and I'll reserve it.",
    },
    {"type": "done", "finish_reason": "stop"},
]


def steps_for(text: str) -> list[dict]:
    """Return the step list for a given user message.

    Confirmation/decline handling is done by CachedLlm before calling this, but
    we also route confirmations here for safety.
    """
    n = normalize(text)
    if n in SCRIPTS:
        return SCRIPTS[n]
    # Loose containment match for slight phrasing differences.
    for key, steps in SCRIPTS.items():
        if key in n or n in key:
            return steps
    return FALLBACK_STEPS
