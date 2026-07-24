"""
chat.py — the Altis assistant, backed by OpenRouter (Claude Haiku 4.5).

One assistant, three jobs, all grounded in real data:
  1. Product operations — how to use every part of the Altis dashboard.
  2. Claims intelligence — event/portfolio/property questions from CONTEXT.
  3. Underwriting & trends — book-level risk mix, zone exposure, and
     patterns computed ONLY from the aggregates supplied in CONTEXT.

Persona: a carrier claims-operations / MGA portfolio audience. Professional
plain-prose replies (no markdown — the UI renders raw text and may speak the
reply aloud in voice mode). Explicit about Altis's real physical constraints
(surge recession, urban SAR masking, US-only FEMA zones) rather than
overclaiming.

Also home to draft_adjuster_note(): a one-click professional dispatch/desk
note per property — LLM-drafted when OPENROUTER_API_KEY is set, with a
deterministic template fallback so the button always works.
"""
import json
import requests

from pipeline.config import OPENROUTER_API_KEY

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-haiku-4.5"

SYSTEM_PROMPT = """You are the Altis assistant, embedded in the Altis flood-intelligence \
dashboard. Your user works at an insurance carrier or MGA — claims operations, CAT \
response, underwriting, or portfolio management. You are their operations copilot: you \
answer data questions, guide them through the product, and read portfolio-level risk \
patterns. Your replies may be read aloud by a voice interface.

FORMAT — strict:
- Plain conversational prose only. Never use markdown: no asterisks, no bullet points, \
no numbered lists, no headers, no backticks, no bold. Write complete sentences, as a \
professional colleague would speak.
- Concise: two to four sentences for most answers, more only when the user asks for \
detail. Lead with the number or the answer, then the context.
- Professional and direct. No exclamation marks, no filler enthusiasm. Do not use \
em dashes; use commas, colons, or separate sentences instead.

HOW THE PRODUCT WORKS — use this to answer any "how do I" question:
The sidebar on the left has panels: Events lists flood events; selecting one flies the \
globe there and loads every property color-coded by triage decision. Upload Portfolio \
(top right) accepts CSV, Excel, or PDF files with any column naming — Altis maps the \
columns automatically, shows a review screen to confirm or correct the mapping, then \
geocodes the book onto the globe. The Analysis panel runs live satellite analysis on an \
uploaded portfolio for any location on Earth: enter the flood or landfall date and run; \
it takes one to a few minutes and needs Earth Engine credentials configured. Dispatch \
Queue is the ordered worklist — properties ranked by severity times coverage exposure, \
so the top row is the first field visit to make. Claims Grid (top right) is the \
spreadsheet view: sort, filter, select rows, export CSV. Clicking any property pin \
opens the drawer: aerial view of the parcel, before-and-after satellite imagery with a \
comparison slider, flood depth with uncertainty, confidence score with a full \
factor-by-factor explanation, estimated claim severity in dollars, and a Draft Note \
button that writes a professional adjuster note. The Adjuster Verdict widget in the \
drawer lets a human agree or disagree with any call — corrections feed the calibration \
model as ground truth. Reports downloads an audit-ready PDF per event and a \
catastrophe report per analyzed portfolio. Operations shows the always-on monitor that \
watches NHC and USGS feeds and auto-queues pipeline runs. The Pre-Event and Post-Event \
toggle at bottom left switches the satellite overlay when one is available. The \
Exposure heat layer toggle on the globe shows concentration of exposure; pins stay on.

GROUND RULES — do not violate:
- Only state numbers and facts that appear in CONTEXT. If something is not in CONTEXT, \
say you do not have that data on screen and name the panel or action that would load it.
- Trends and underwriting reads must be computed strictly from CONTEXT aggregates \
(zone mix, depth distribution, class counts, regional breakdowns, risk scores). \
Describe the pattern and what it means for the book; never extrapolate beyond the data \
or invent a forecast.
- Altis ships with three pre-computed demo events (Northern Rivers Floods NSW 2022, \
Hurricane Harvey 2017, Hurricane Ian 2022). With Earth Engine configured, live \
analysis works for any location and event date worldwide.
- Geocoding resolves worldwide via Mapbox, with US Census fallback.
- Known physical limits, state them plainly when relevant: storm surge that recedes \
before the next satellite pass reads dry; dense urban cores hide street-level water \
from radar and route to manual Review; FEMA NFHL flood zones exist only for US \
properties; optical Sentinel-2 imagery requires a cloud-free pass.
- Loss figures are depth-damage reserving estimates for claims operations, not \
adjusted claims. Say so if the user treats them as final.
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
                'pct_flooded', 'confidence_score', 'adjuster_note', 'flood_zone',
                'sfha_flag', 'severity_mid_usd', 'coverage_amount',
                'subrogation_flag', 'surge_check_flag', 'duration_days')
        snippet = {k: property_row.get(k) for k in keep if k in property_row}
        parts.append(f"SELECTED PROPERTY: {json.dumps(snippet, default=str)}")
    return "\n".join(parts) if parts else (
        "No event or property is currently selected. The user may still ask how to "
        "use the product, or general questions about what Altis can do.")


def _call_llm(messages: list[dict], max_tokens: int = 600,
              temperature: float = 0.3) -> str:
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": MODEL, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
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

    return _call_llm(messages)


# ── One-click adjuster note drafting ─────────────────────────────────────────

NOTE_SYSTEM_PROMPT = """You draft claim-file notes for a property and casualty \
insurance carrier's claims operations team, based on satellite flood analysis from \
Altis. Write the note in plain professional prose — no markdown, no headers, no \
bullet points, no em dashes. Four to six sentences. Structure: what the satellite analysis found at \
this property (depth, flooded area, confidence, corroborating signals); the \
recommended handling (dispatch, remote resolution, or review) and why; anything the \
handler should verify or watch for (urban radar limits, surge timing, subrogation \
potential, flood zone status); and next step. State only facts present in the data \
given. Write so the note can be pasted directly into a claims system. Do not invent \
policy details, names, or dates not provided."""


def _fmt_depth(row: dict) -> str:
    try:
        d = float(row.get('max_depth_ft') or 0)
    except (TypeError, ValueError):
        d = 0.0
    ci = row.get('depth_ci_ft')
    try:
        return f"{d:.1f} ft (± {float(ci):.1f} ft)" if ci not in (None, '') else f"{d:.1f} ft"
    except (TypeError, ValueError):
        return f"{d:.1f} ft"


def _deterministic_note(row: dict, event_label: str | None) -> str:
    """Template fallback — always available, no API key required."""
    cls = row.get('impact_class') or 'Review'
    addr = row.get('address') or 'the insured property'
    pct = row.get('pct_flooded') or 0
    conf = row.get('confidence_score') or 0
    ev = f" during {event_label}" if event_label else ""

    action = {
        'Dispatch': "Recommend field adjuster dispatch; damage is consistent with "
                    "interior water intrusion and warrants on-site inspection.",
        'Remote-Approve': "Damage indicators support remote handling; recommend "
                          "desk adjudication with policyholder-submitted photos.",
        'Remote-Deny': "No significant flood signature at this parcel; recommend "
                       "remote resolution unless the policyholder provides evidence "
                       "of interior damage.",
        'Review': "Signals are mixed at this parcel; recommend a manual review "
                  "before committing to dispatch or remote handling.",
    }.get(cls, "Recommend manual review.")

    parts = [
        f"Satellite flood analysis for {addr}{ev}: maximum estimated water depth "
        f"{_fmt_depth(row)} with {pct}% of the parcel showing flood signature "
        f"(model confidence {conf}%).",
        action,
    ]
    if row.get('urban_flag') in (1, '1', True):
        parts.append("Note: dense urban surroundings can mask street-level water "
                     "from radar, so on-the-ground conditions may exceed the "
                     "satellite estimate.")
    if row.get('surge_check_flag') in (1, '1', True):
        parts.append("This low-lying waterfront parcel read dry, but storm surge "
                     "can recede between satellite passes; verify with photos or a "
                     "call-out before closing remotely.")
    if row.get('subrogation_flag') in (1, '1', True):
        parts.append("Flooding was detected adjacent to permanent water or "
                     "drainage infrastructure; flag the file for subrogation "
                     "screening.")
    sev = row.get('severity_mid_usd')
    if sev not in (None, ''):
        try:
            parts.append(f"Preliminary depth-damage reserving estimate is "
                         f"${int(float(sev)):,}; this is a reserving aid, not an "
                         f"adjusted figure.")
        except (TypeError, ValueError):
            pass
    parts.append("Generated by Altis satellite analysis; pending adjuster "
                 "confirmation.")
    return " ".join(parts)


def draft_adjuster_note(row: dict, event_label: str | None = None) -> dict:
    """
    Professional claim-file note for one property. LLM-drafted when a key is
    configured; deterministic template otherwise (and on any LLM failure) so
    the Draft Note button never dead-ends.
    Returns {'note': str, 'source': 'llm'|'template'}.
    """
    if OPENROUTER_API_KEY:
        keep = ('address', 'impact_class', 'max_depth_ft', 'depth_ci_ft',
                'pct_flooded', 'confidence_score', 'urban_flag', 'flood_zone',
                'sfha_flag', 'severity_low_usd', 'severity_mid_usd',
                'severity_high_usd', 'subrogation_flag', 'surge_check_flag',
                'duration_days', 'rain_mm', 'vegetation_loss', 'coverage_amount',
                'policy_number')
        data = {k: row.get(k) for k in keep if row.get(k) not in (None, '')}
        if event_label:
            data['event'] = event_label
        try:
            note = _call_llm(
                [{"role": "system", "content": NOTE_SYSTEM_PROMPT},
                 {"role": "user", "content": "Draft the claim-file note for this "
                  "property:\n" + json.dumps(data, default=str)}],
                max_tokens=400, temperature=0.2)
            return {'note': note, 'source': 'llm'}
        except ChatError:
            pass  # fall through to template — the button must always work
    return {'note': _deterministic_note(row, event_label), 'source': 'template'}
