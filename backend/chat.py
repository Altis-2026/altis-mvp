"""
chat.py — "Ask about this area" assistant, backed by OpenRouter (Claude Haiku 4.5).

Grounds answers in the actual event/property data the user has open (no live web
access, no invented numbers) and is explicit about Altis's real constraints
(synthetic demo imagery vs. live GEE, US-only geocoding, which events are
pre-computed) rather than overclaiming "works perfectly anywhere."
"""
import json
import requests

from pipeline.config import OPENROUTER_API_KEY

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = """You are the Altis flood-intelligence assistant, embedded in the \
Altis dashboard below the globe view. Altis triages property-level flood damage from \
Sentinel-1 SAR + Sentinel-2 optical satellite imagery for insurance carriers, ranking \
properties by severity and coverage so adjusters know who to dispatch first.

Answer questions about the event, portfolio, or property data given to you in CONTEXT \
below. Be concise (2-4 sentences unless asked for detail), specific, and numbers-first.

Ground rules — do not violate these:
- Only state numbers/facts that appear in CONTEXT. If something isn't in CONTEXT, say \
you don't have that data rather than guessing.
- Altis currently ships with two pre-computed demo events (Hurricane Harvey, Hurricane \
Ian) plus any portfolio the user has uploaded and analyzed. Live coverage of an \
arbitrary new location/date range requires a configured Google Earth Engine service \
account and a pipeline run — it is not instant or "anywhere, automatically" today. If \
asked about other locations (e.g. outside the US, or events without precomputed data), \
say so plainly instead of implying it already works there.
- Geocoding for uploaded portfolios currently uses the US Census geocoder, so non-US \
addresses won't resolve automatically yet.
- The SAR/optical thumbnails shown in the demo are illustrative synthetic renders \
seeded from each property's analyzed flood depth, not live satellite tiles, unless a \
GEE-cached real thumbnail exists — be upfront about this if asked "is this a real \
satellite photo."
- Never invent claims numbers, adjuster names, or addresses not present in CONTEXT.
"""


class ChatError(Exception):
    pass


def _build_context(event_meta: dict | None, event_stats: dict | None,
                    property_row: dict | None) -> str:
    parts = []
    if event_meta:
        parts.append(f"EVENT: {event_meta.get('label', event_meta.get('id'))} "
                     f"({event_meta.get('sub', '')})")
    if event_stats:
        savings = event_stats.get('estimated_savings')
        parts.append(
            "EVENT STATS: "
            f"total properties={event_stats.get('total')}, "
            f"dispatch={event_stats.get('dispatch')}, "
            f"review={event_stats.get('review')}, "
            f"remote-approve={event_stats.get('remote_approve')}, "
            f"remote-deny={event_stats.get('remote_deny')}, "
            f"% handled remotely={event_stats.get('pct_remote')}%, "
            + (f"estimated adjuster-trip savings=${savings:,}" if savings is not None else "")
        )
    if property_row:
        keep = ('property_id', 'address', 'impact_class', 'max_depth_ft',
                'pct_flooded', 'confidence_score', 'adjuster_note')
        snippet = {k: property_row.get(k) for k in keep if k in property_row}
        parts.append(f"SELECTED PROPERTY: {json.dumps(snippet, default=str)}")
    return "\n".join(parts) if parts else "No event or property is currently selected."


def ask(message: str, history: list[dict] | None, event_meta: dict | None,
        event_stats: dict | None, property_row: dict | None) -> str:
    if not OPENROUTER_API_KEY:
        raise ChatError(
            "Chat isn't configured — OPENROUTER_API_KEY is missing from the backend "
            "environment. Set it in your .env and restart the server."
        )

    context = _build_context(event_meta, event_stats, property_row)
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nCONTEXT:\n" + context}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": message[:2000]})

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": MODEL, "messages": messages, "max_tokens": 500, "temperature": 0.3},
            timeout=30,
        )
    except requests.RequestException as e:
        raise ChatError(f"Couldn't reach the assistant: {e}")

    if resp.status_code != 200:
        raise ChatError(f"Assistant request failed ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        raise ChatError("Assistant returned an unexpected response.")
