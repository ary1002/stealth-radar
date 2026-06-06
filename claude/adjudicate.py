"""
Two-call adjudication pipeline.

Call 1 — dossier (free-form reasoning, no verdict constraint).
Call 2 — verdict (label + confidence, grounded in Call 1 reasoning).

The two-call design ensures the verdict follows causally from the dossier;
Claude cannot contradict its own written reasoning when classifying.
"""
import json
import logging
import re
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

_log = logging.getLogger(__name__)

# ── Call 1: dossier ───────────────────────────────────────────────────────────

_DOSSIER_SYS = (
    "You are a deal/talent scout analysing a potential startup founding team. "
    "Reason freely about the evidence. Cover: who the people are, what signals "
    "suggest intentional team formation, what signals weaken the thesis, what "
    "action to take, and how urgent. Flag anything that looks like a layoff, "
    "coincidental moves, or noise. Name exact signals and dates. No filler. "
    "Keep evidence_timeline to at most 5 items (the most signal-rich ones). "
    "Keep summary under 3 sentences. "
    'Respond ONLY with JSON: {"summary":str,"members":[str],'
    '"evidence_timeline":[str],"thesis":str,"recommended_action":str,'
    '"urgency":"now|30d|90d"}'
)

# ── Call 2: verdict ───────────────────────────────────────────────────────────

_VERDICT_SYS = (
    "You are classifying a cluster of people leaving a shared employer. "
    "You have already written a dossier reasoning about this cluster. "
    "Your verdict MUST follow from that reasoning — do not contradict it. "
    "Choose exactly one label: "
    "  forming_team — intentional co-founding or co-joining signal, evidence is clear; "
    "  layoff_dispersion — looks like a coordinated layoff landing at the same employer; "
    "  coincidental — independent moves to a common desirable destination; "
    "  unclear — real signal present but insufficient to classify confidently. "
    'Respond ONLY with JSON: {"label":"forming_team|layoff_dispersion|coincidental|unclear",'
    '"confidence":0-1,"rationale":"one sentence referencing the dossier reasoning"}'
)


def _call(client: Anthropic, system: str, user_content: str, max_tokens: int) -> str:
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _parse_json(text: str, fallback: dict) -> dict:
    """Extract and parse the outermost JSON object from Claude's response.

    Handles: markdown fences, preamble text, postamble text, truncation.
    Strategy:
      1. first-{ / last-} slice (handles fences and trailing text)
      2. If no closing }, the response was truncated — truncate the rationale
         at the last complete string boundary and close the object.
    """
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            _log.warning("_parse_json: JSON decode failed. len=%d tail=%r", len(text), text[-60:])

    # No closing brace — response was truncated mid-JSON. Extract fields by regex
    # so a truncated rationale string doesn't prevent us recovering label+confidence.
    if start != -1 and end == -1:
        fragment = text[start:]
        label_m = re.search(r'"label"\s*:\s*"([^"]*)"', fragment)
        conf_m  = re.search(r'"confidence"\s*:\s*([\d.]+)', fragment)
        # Rationale may be incomplete — grab whatever is there
        rat_m   = re.search(r'"rationale"\s*:\s*"([^"]*)', fragment)
        if label_m:
            _log.warning("_parse_json: truncated — salvaged label=%r", label_m.group(1))
            return {
                "label":      label_m.group(1),
                "confidence": float(conf_m.group(1)) if conf_m else 0.5,
                "rationale":  (rat_m.group(1).rstrip() + "…") if rat_m else "(truncated)",
            }
        _log.warning("_parse_json: truncated, no label found. tail=%r", text[-80:])
    else:
        _log.warning("_parse_json: no JSON object found. text=%r", text[:120])

    return fallback


def adjudicate_and_dossier(
    cluster_summary: dict,
    hypotheses: dict | None = None,
    route: str = "skeptical",
    anthropic_key: str | None = None,
) -> tuple[dict, dict]:
    """Two-call pipeline: dossier first, verdict second.

    Returns (verdict_dict, dossier_dict).
    verdict_dict  — {"label": str, "confidence": float, "rationale": str}
    dossier_dict  — {"summary": str, "members": [...], "evidence_timeline": [...],
                     "thesis": str, "recommended_action": str, "urgency": str}
    """
    _key = anthropic_key or ANTHROPIC_API_KEY
    if not _key:
        raise ValueError("Anthropic API key is required. Enter it in the sidebar.")
    client = Anthropic(api_key=_key)

    cluster_json = json.dumps(cluster_summary, default=str)

    # Build hypothesis prompt block (empty string if no hypotheses)
    hyp_block = ""
    if hypotheses:
        from detect.hypotheses import hypotheses_to_prompt_block
        hyp_block = hypotheses_to_prompt_block(hypotheses, route)

    # ── Call 1: dossier ───────────────────────────────────────────────────────
    dossier_user = cluster_json
    if hyp_block:
        dossier_user += f"\n\n{hyp_block}"

    dossier_text = _call(client, _DOSSIER_SYS, dossier_user,
                         max_tokens=1600 if route != "hostile" else 600)
    dossier_dict = _parse_json(dossier_text, {
        "summary": "parse_error", "members": [], "evidence_timeline": [],
        "thesis": "", "recommended_action": "", "urgency": "90d",
    })

    # For hostile route, skip dossier generation (verdict only)
    if route == "hostile":
        dossier_dict = None

    # ── Call 2: verdict (with dossier as grounding context) ───────────────────
    verdict_input = cluster_json
    if dossier_dict:
        verdict_input += "\n\nYour prior reasoning:\n" + json.dumps(dossier_dict, default=str)
    if hypotheses:
        benign_names     = [h["name"] for h in hypotheses.get("benign", [])]
        confirming_names = [c["name"] for c in hypotheses.get("confirming", [])]
        hyp_summary = ""
        if benign_names:
            hyp_summary += f"Benign hypotheses to rule out: {', '.join(benign_names)}. "
        if confirming_names:
            hyp_summary += f"Confirming signals: {', '.join(confirming_names)}. "
        if hyp_summary:
            verdict_input += (
                f"\n\nHypotheses summary: {hyp_summary}"
                "Classify using exactly one of forming_team, layoff_dispersion, "
                "coincidental, unclear. An unresolved coordinated_layoff points to "
                "layoff_dispersion; an unresolved geographic_coincidence or "
                "desirable_employer_gravity points to coincidental. "
                "Commit to the verdict the evidence supports. Respond ONLY with JSON."
            )
    verdict_text = _call(client, _VERDICT_SYS, verdict_input, max_tokens=400)
    verdict_dict = _parse_json(verdict_text, {
        "label": "unclear", "confidence": 0.0, "rationale": "parse_error",
    })

    return verdict_dict, dossier_dict


# ── Backward-compat shims (existing callers unchanged) ────────────────────────

def adjudicate(cluster_summary: dict, anthropic_key: str | None = None) -> dict:
    """Legacy single-call shim. Prefer adjudicate_and_dossier() for new code."""
    verdict, _ = adjudicate_and_dossier(cluster_summary, anthropic_key)
    return verdict


def dossier(cluster_summary: dict, anthropic_key: str | None = None) -> dict:
    """Legacy single-call shim. Prefer adjudicate_and_dossier() for new code."""
    _, dos = adjudicate_and_dossier(cluster_summary, anthropic_key)
    return dos
