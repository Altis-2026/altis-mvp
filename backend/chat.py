"""
chat.py — portfolio-intelligence assistant, backed by OpenRouter (Claude Haiku 4.5).

Persona: answers for carrier claims-operations / MGA portfolio users at the
book-of-business level. Grounds answers in the actual event/portfolio data the
user has open (no live web access, no invented numbers) and is explicit about
Altis's real physical constraints (surge recession, urban SAR masking, US-only
FEMA zones) rather than overclaiming "works perfectly anywhere."
"""
import json
import requests

from pipeline.config import OPENROUTER_API_KEY

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = """You are the Altis flood-intelligence assistant, embedded in the \
Altis dashboard below the globe view. Your user is a carrier claims-operations (CAT \
ops) or MGA portfolio manager running catastrophe response across a book of business \
— assume portfolio-level questions (exposure, TIV at risk, estimated loss range, \
dispatch counts, which regions are worst hit) unless they drill into one property. \
Unlike static hazard scores, Altis delivers real-time satellite ground truth: \
Sentinel-1 SAR + Sentinel-2 optical change detection within days of a flood event, \
triaging every policy by severity, coverage, and confidence.

Answer questions about the event, portfolio, or property data given to you in CONTEXT \
below. Be concise (2-4 sentences unless asked for detail), specific, and numbers-first.

Ground rules — do not violate these:
- Only state numbers/facts that appear in CONTEXT. If something isn't in CONTEXT, say \
you don't have that data rather than guessing.
- Altis ships with three pre-computed demo events (Northern Rivers Floods NSW 2022, \
Hurricane Harvey 2017, Hurricane Ian 2022) plus any uploaded portfolio. When Google \
Earth Engine credentials are configured, live on-demand analysis works for any \
location on Earth and any event date — it takes one to a few minutes, not seconds.
- Geocoding uses Mapbox and resolves worldwide (US Census as fallback).
- Known physical limits — state them plainly when relevant: storm surge that recedes \
before the next satellite pass reads dry; dense urban cores hide street water from \
radar and are routed to manual Review; FEMA NFHL flood zones exist only for US \
properties.
- Property thumbnails are real Sentinel-1/2 imagery when a live analysis ran (or a \
cached GEE tile exists); otherwise they are clearly-labeled synthetic previews — be \
upfront about which is which if asked.
- Loss figures are depth-damage reserving estimates for claims operations, not \
adjusted claims.
- Never invent claims numbers, adjuster names, or addresses not present in CONTEXT.
"""


class ChatError(Exception):
    pass


def _build_context(event_meta: dict | None, event_stats: dict | None,
                    property_row: dict | None,
                    portfolio_summary: dict | None = None) -> str:
    parts = []
    if portfolio_summary:
        parts.append("ANALYZED PORTFOLIO (book of business): "
                     + json.dumps(portfolio_summary, default=str))
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
        event_stats: dict | None, property_row: dict | None,
        portfolio_summary: dict | None = None) -> str:
    if not OPENROUTER_API_KEY:
        raise ChatError(
            "Chat isn't configured — OPENROUTER_API_KEY is missing from the backend "
            "environment. Set it in your .env and restart the server."
        )

    context = _build_context(event_meta, event_stats, property_row, portfolio_summary)
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
