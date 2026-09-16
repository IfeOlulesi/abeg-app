"""LLM providers: OpenRouter (live streaming) and Cached (offline scripted).

All providers yield a uniform event stream from `stream()`:
    {"type": "delta", "text": str}
    {"type": "tool_calls", "tool_calls": [{"id","name","arguments"(dict)}]}
    {"type": "done", "finish_reason": str}
"""
import json
import uuid
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx

from app import cache
from app.config import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LlmProvider(ABC):
    @abstractmethod
    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        ...
        yield  # pragma: no cover  (make this an async generator for typing)


# --------------------------------------------------------------------------
# OpenRouter
# --------------------------------------------------------------------------
class OpenRouterLlm(LlmProvider):
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.openrouter_model

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        model_name = settings.openrouter_model or self.model
        has_image = any(
            isinstance(m.get("content"), list)
            and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        # If the turn includes an image and the active model is not vision-capable,
        # fallback to gpt-4o-mini so the call succeeds.
        if has_image and not any(v in model_name.lower() for v in ("gpt-4o", "gemini", "vision", "claude-3")):
            model_name = "openai/gpt-4o-mini"

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": settings.temperature,
            # Ask OpenRouter to include token counts + cost in the final chunk.
            "usage": {"include": True},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter uses these to identify the app; anonymous datacenter
            # traffic (e.g. a cloud host's shared IP) gets throttled harder.
            "HTTP-Referer": settings.app_url,
            "X-Title": "Abeg",
        }

        # Accumulator for streamed tool_call deltas, keyed by index.
        tool_acc: dict[int, dict] = {}
        finish_reason = "stop"
        usage: dict | None = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            async with client.stream("POST", OPENROUTER_URL, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Usage arrives on the final chunk (often with empty choices).
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    text = delta.get("content")
                    if text:
                        yield {"type": "delta", "text": text}

                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

        if tool_acc:
            calls = []
            for idx in sorted(tool_acc):
                slot = tool_acc[idx]
                raw = slot["arguments"] or "{}"
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    args = {}
                calls.append(
                    {
                        "id": slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                        "name": slot["name"],
                        "arguments": args,
                    }
                )
            yield {"type": "tool_calls", "tool_calls": calls}
            finish_reason = "tool_calls"

        if usage:
            yield {"type": "usage", "usage": usage}
        yield {"type": "done", "finish_reason": finish_reason}


# --------------------------------------------------------------------------
# Cached / offline
# --------------------------------------------------------------------------
class CachedLlm(LlmProvider):
    """Replays deterministic scripted steps from app/cache.py.

    Inspects the LAST user message. Confirmation turns (yes/confirm/...) replay
    the place_order sequence, resolving "$LAST_RESERVATION" to the most recent
    reservation id found in prior tool messages of the conversation.
    """

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        last_user = self._last_user_text(messages)
        has_image = self._last_user_has_image(messages)

        if cache.is_confirmation(last_user):
            steps = cache.CONFIRM_STEPS
        elif self._is_decline(last_user):
            steps = cache.DECLINE_STEPS
        elif has_image:
            steps = cache.NOTE_STEPS
        else:
            steps = cache.steps_for(last_user)

        last_res = self._last_reservation_id(messages)
        last_order = self._last_order_reference(messages)

        # A script may contain several tool_calls steps. The agent calls stream()
        # once per loop iteration, executing tools between calls. We therefore
        # emit only ONE segment per call: replay up to and including the next
        # unexecuted tool_calls step, or the trailing text + done if none remain.
        # "Segment index" = number of tool-result batches already produced since
        # the last user message.
        already = self._tool_batches_since_last_user(messages)

        seen_tool_batches = 0
        for step in steps:
            stype = step.get("type")
            if stype == "tool_calls":
                if seen_tool_batches < already:
                    # This tool batch was already executed on a prior segment.
                    seen_tool_batches += 1
                    continue
                calls = []
                for c in step["tool_calls"]:
                    args = self._resolve_args(c.get("arguments", {}), last_res, last_order)
                    calls.append(
                        {"id": f"call_{uuid.uuid4().hex[:8]}", "name": c["name"], "arguments": args}
                    )
                yield {"type": "tool_calls", "tool_calls": calls}
                yield {"type": "done", "finish_reason": "tool_calls"}
                return
            # delta / done steps: only surface those belonging to the current
            # (not-yet-consumed) segment.
            if seen_tool_batches < already:
                continue
            if stype == "delta":
                yield {"type": "delta", "text": step["text"]}
            elif stype == "done":
                yield {"type": "done", "finish_reason": step.get("finish_reason", "stop")}
                return

        # Ensure a terminating done if the script omitted it.
        yield {"type": "done", "finish_reason": "stop"}

    @staticmethod
    def _tool_batches_since_last_user(messages: list[dict]) -> int:
        """Count assistant tool-call batches emitted after the last user message."""
        count = 0
        for m in reversed(messages):
            role = m.get("role")
            if role == "user":
                break
            if role == "assistant" and m.get("tool_calls"):
                count += 1
        return count

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        return ""

    @staticmethod
    def _last_user_has_image(messages: list[dict]) -> bool:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, list):
                    return any(
                        isinstance(part, dict) and part.get("type") == "image_url"
                        for part in content
                    )
        return False

    @staticmethod
    def _is_decline(text: str) -> bool:
        n = cache.normalize(text)
        return n in {"no", "nope", "cancel", "nah", "don't", "dont"} or "cancel" in n

    @staticmethod
    def _resolve_args(args: dict, last_res: str | None, last_order: str | None = None) -> dict:
        resolved = json.loads(json.dumps(args))  # deep copy
        if resolved.get("reservation_id") == "$LAST_RESERVATION" and last_res:
            resolved["reservation_id"] = last_res
        if resolved.get("reference") == "$LAST_ORDER" and last_order:
            resolved["reference"] = last_order
        return resolved

    @staticmethod
    def _last_reservation_id(messages: list[dict]) -> str | None:
        """Scan tool result messages for the most recent reservation_id."""
        for m in reversed(messages):
            if m.get("role") != "tool":
                continue
            content = m.get("content")
            if not isinstance(content, str):
                continue
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            rid = data.get("reservation_id")
            if rid:
                return rid
        return None

    @staticmethod
    def _last_order_reference(messages: list[dict]) -> str | None:
        """Scan tool result messages for the most recent order reference."""
        for m in reversed(messages):
            if m.get("role") != "tool":
                continue
            content = m.get("content")
            if not isinstance(content, str):
                continue
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            # place_order returns a reference; ignore an error payload.
            if data.get("reference") and not data.get("error"):
                return data["reference"]
        return None


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------
def get_llm(api_key: str | None = None) -> LlmProvider:
    # `api_key` is a per-request BYOK key: when a visitor brings their own
    # OpenRouter key, calls are billed to them (and skip the demo's rate limit).
    if settings.cached_mode:
        return CachedLlm()
    return OpenRouterLlm(api_key=api_key)
