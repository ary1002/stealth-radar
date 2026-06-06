"""
pipeline/compiler.py — ThesisConfig compiler.

Accepts a natural-language description, calls Claude to produce a ThesisConfig JSON,
then runs a grounding pass using free Crustdata autocomplete / identify endpoints.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from anthropic import Anthropic

import config
from models.schemas import (
    AnchorStrategy,
    CompanyGate,
    ScoringWeights,
    ThesisConfig,
)

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a Stealth Radar thesis compiler. Given a natural-language description of a talent-signal thesis, output ONLY a valid JSON object matching the ThesisConfig schema below — no prose, no markdown fences.

## ThesisConfig JSON schema

{
  "thesis_id": "<string: short snake_case id>",
  "label": "<string: human-readable title>",
  "anchor_strategy": {
    "mode": "<string: 'explicit' | 'derived'>",
    "companies": ["<company name>", ...]   // list of company names for explicit mode
  },
  "person_filters": {
    // Compound Crustdata person/search filter dict — see filter rules below
  },
  "company_gate": {
    "max_headcount": <integer: max destination company headcount, default 500>,
    "countries": ["<ISO-3 code>", ...]   // ISO-3 codes e.g. "USA", "GBR", "IND"
  },
  "scoring_weights": {
    "size_score":            <float>,
    "shared_destination":    <float>,
    "destination_tiny":      <float>,
    "stealth_founder_ratio": <float>,
    "window_tightness":      <float>,
    "co_tenure":             <float>,
    "open_to":               <float>
    // Must sum to 1.0
  },
  "max_investigation_credits": <float, default 10.0>,
  "poll_cadence_hours": <integer, default 24>
}

## Valid person/search filter fields

- basic_profile.name
- basic_profile.location.country        ← FULL country name: "United States", "India"
- basic_profile.location.city
- basic_profile.location.state
- basic_profile.location.continent
- experience.employment_details.current.title
- experience.employment_details.current.company_name
- experience.employment_details.current.seniority_level
- experience.employment_details.current.company_headquarters_country  ← ISO-3: "USA"
- experience.employment_details.past.company_name
- experience.employment_details.company_name                          ← any employer
- education.schools.school
- education.schools.degree
- skills.professional_network_skills
- years_of_experience_raw
- recently_changed_jobs                 ← boolean
- professional_network.open_to_cards

## Valid operators (use EXACTLY these strings)

| Operator | Meaning                    |
|----------|---------------------------|
| =        | Exact match                |
| !=       | Not equal                  |
| >        | Greater than               |
| <        | Less than                  |
| =>       | Greater than or equal (NOT >=) |
| =<       | Less than or equal (NOT <=) |
| in       | Value in list (array)      |
| not_in   | Value not in list          |
| (.)      | Fuzzy / contains match     |
| [.]      | Exact token match          |
| geo_distance | Radius search          |

CRITICAL: Use => not >=, use =< not <=.

## Country format rules

- basic_profile.location.country: FULL country name → "United States", "India", "United Kingdom"
- experience.employment_details.current.company_headquarters_country: ISO-3 → "USA", "IND", "GBR"
- company_gate.countries: ISO-3 codes → "USA", "GBR"

## Filter structure

Single filter:
{"field": "<field>", "type": "<operator>", "value": <value>}

Compound filter:
{"op": "and", "conditions": [<filter>, ...]}

## Output rules

- Output ONLY the JSON object, no markdown, no commentary.
- scoring_weights must sum exactly to 1.0.
- thesis_id must be snake_case, max 40 chars.
- If no location preference is stated, omit location filters.
- Always include recently_changed_jobs = true as a filter condition to limit cohort size.
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _dict_to_thesis(d: dict) -> ThesisConfig:
    """Parse the Claude JSON dict into a ThesisConfig dataclass."""
    anchor = d.get("anchor_strategy", {})
    gate = d.get("company_gate", {})
    weights = d.get("scoring_weights", {})
    return ThesisConfig(
        thesis_id=d.get("thesis_id", str(uuid.uuid4())[:8]),
        label=d.get("label", "Untitled Thesis"),
        anchor_strategy=AnchorStrategy(
            mode=anchor.get("mode", "explicit"),
            companies=anchor.get("companies", []),
        ),
        person_filters=d.get("person_filters", {}),
        company_gate=CompanyGate(
            max_headcount=gate.get("max_headcount", 500),
            countries=gate.get("countries", []),
        ),
        scoring_weights=ScoringWeights(
            size_score=weights.get("size_score", 0.18),
            shared_destination=weights.get("shared_destination", 0.22),
            destination_tiny=weights.get("destination_tiny", 0.12),
            stealth_founder_ratio=weights.get("stealth_founder_ratio", 0.16),
            window_tightness=weights.get("window_tightness", 0.12),
            co_tenure=weights.get("co_tenure", 0.14),
            open_to=weights.get("open_to", 0.06),
        ),
        max_investigation_credits=d.get("max_investigation_credits", 10.0),
        poll_cadence_hours=d.get("poll_cadence_hours", 24),
    )


# Hard mapping: ISO-3 / ISO-2 codes → full country names for basic_profile.location.country
_ISO_TO_FULL: dict[str, str] = {
    "US": "United States", "USA": "United States", "UK": "United Kingdom",
    "GBR": "United Kingdom", "GB": "United Kingdom", "IND": "India",
    "IN": "India", "DEU": "Germany", "DE": "Germany", "FRA": "France",
    "FR": "France", "CAN": "Canada", "CA": "Canada", "AUS": "Australia",
    "AU": "Australia", "SGP": "Singapore", "SG": "Singapore",
    "NLD": "Netherlands", "NL": "Netherlands", "CHE": "Switzerland",
    "CH": "Switzerland", "SWE": "Sweden", "SE": "Sweden",
    "DNK": "Denmark", "DK": "Denmark", "NOR": "Norway", "NO": "Norway",
    "ISR": "Israel", "IL": "Israel", "BRA": "Brazil", "BR": "Brazil",
    "JPN": "Japan", "JP": "Japan", "KOR": "South Korea", "KR": "South Korea",
    "CHN": "China", "CN": "China", "ESP": "Spain", "ES": "Spain",
    "ITA": "Italy", "IT": "Italy", "PRT": "Portugal", "PT": "Portugal",
    "POL": "Poland", "PL": "Poland", "MEX": "Mexico", "MX": "Mexico",
    "EU": None,  # continent — drop rather than map to a country
}


def _fix_operators(filters: dict) -> dict:
    """Recursively replace >= with => and <= with =< in filter dicts."""
    if not isinstance(filters, dict):
        return filters
    result = {}
    for k, v in filters.items():
        if k == "type" and isinstance(v, str):
            v = v.replace(">=", "=>").replace("<=", "=<")
        elif isinstance(v, dict):
            v = _fix_operators(v)
        elif isinstance(v, list):
            v = [_fix_operators(item) if isinstance(item, dict) else item for item in v]
        result[k] = v
    return result


def _fix_country_codes(filters: dict) -> tuple[dict, list[str]]:
    """For basic_profile.location.country fields, convert ISO codes to full names."""
    changes: list[str] = []
    if not isinstance(filters, dict):
        return filters, changes

    result = {}
    for k, v in filters.items():
        if filters.get("field") == "basic_profile.location.country" and k == "value":
            if isinstance(v, str):
                canonical = _ISO_TO_FULL.get(v.upper())
                if canonical is not None and canonical != v:
                    changes.append(f"Country code '{v}' → '{canonical}' for person location field.")
                    v = canonical
            elif isinstance(v, list):
                new_list = []
                for item in v:
                    canonical = _ISO_TO_FULL.get(item.upper()) if isinstance(item, str) else None
                    if canonical is not None and canonical != item:
                        changes.append(f"Country code '{item}' → '{canonical}'.")
                        new_list.append(canonical)
                    elif canonical is None and isinstance(item, str) and item.upper() in _ISO_TO_FULL:
                        # "EU" etc — drop
                        changes.append(f"Dropped unmappable country code '{item}'.")
                    else:
                        new_list.append(item)
                v = new_list
        elif isinstance(v, dict):
            v, sub = _fix_country_codes(v)
            changes.extend(sub)
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, dict):
                    item, sub = _fix_country_codes(item)
                    changes.extend(sub)
                new_list.append(item)
            v = new_list
        result[k] = v
    return result, changes


def _collect_location_values(filters: dict) -> list[str]:
    """Extract all values from basic_profile.location.country filter fields."""
    values = []
    if not isinstance(filters, dict):
        return values
    if filters.get("field") == "basic_profile.location.country":
        val = filters.get("value")
        if isinstance(val, str):
            values.append(val)
        elif isinstance(val, list):
            values.extend(val)
    for v in filters.values():
        if isinstance(v, dict):
            values.extend(_collect_location_values(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    values.extend(_collect_location_values(item))
    return values


def _replace_filter_value(filters: dict, field: str, old_val: str, new_val: str) -> dict:
    """Replace a specific value in a filter dict (in-place-style, returns new dict)."""
    if not isinstance(filters, dict):
        return filters
    result = {}
    for k, v in filters.items():
        if k == "value" and filters.get("field") == field:
            if isinstance(v, str) and v == old_val:
                v = new_val
            elif isinstance(v, list):
                v = [new_val if item == old_val else item for item in v]
        elif isinstance(v, dict):
            v = _replace_filter_value(v, field, old_val, new_val)
        elif isinstance(v, list):
            v = [_replace_filter_value(item, field, old_val, new_val)
                 if isinstance(item, dict) else item for item in v]
        result[k] = v
    return result


def _collect_industry_values(filters: dict) -> list[str]:
    """Extract industry-related filter values (heuristic: field contains 'industr')."""
    values = []
    if not isinstance(filters, dict):
        return values
    field = filters.get("field", "")
    if "industr" in field.lower():
        val = filters.get("value")
        if isinstance(val, str):
            values.append((field, val))
        elif isinstance(val, list):
            values.extend((field, item) for item in val)
    for v in filters.values():
        if isinstance(v, dict):
            values.extend(_collect_industry_values(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    values.extend(_collect_industry_values(item))
    return values


# ── Main entry point ──────────────────────────────────────────────────────────

async def compile_thesis(
    description: str,
    client,
    anthropic_key: str | None = None,
) -> tuple[ThesisConfig, str]:
    """
    Compile a natural-language thesis description into a grounded ThesisConfig.

    Args:
        description: Plain English description of the signal thesis.
        client:      CrustdataClient or MockCrustdataClient (autocomplete/identify only).
        anthropic_key: Override for Anthropic API key (falls back to config).

    Returns:
        (ThesisConfig, explanation) — grounded config + summary of grounding changes.
    """
    _key = anthropic_key or config.ANTHROPIC_API_KEY
    if not _key:
        raise ValueError("Anthropic API key is required for thesis compilation.")

    # ── Step 1: Claude call ───────────────────────────────────────────────────
    anthropic_client = Anthropic(api_key=_key)
    msg = anthropic_client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1500,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": description}],
    )
    raw_text = "".join(b.text for b in msg.content if b.type == "text").strip()

    # Strip optional markdown fences
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```", 2)[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    try:
        thesis_dict: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON: {exc}\nRaw: {raw_text[:500]}")

    # ── Step 2: Lint pass — fix operator typos + country code conversion ────────
    changes: list[str] = []
    original_filters = json.dumps(thesis_dict.get("person_filters", {}))
    thesis_dict["person_filters"] = _fix_operators(thesis_dict.get("person_filters", {}))
    if json.dumps(thesis_dict["person_filters"]) != original_filters:
        changes.append("Fixed operator typos (>= → =>, <= → =<).")
    # Convert ISO country codes to full names for basic_profile.location.country
    thesis_dict["person_filters"], cc_changes = _fix_country_codes(
        thesis_dict.get("person_filters", {})
    )
    changes.extend(cc_changes)

    # ── Step 3: Grounding pass — location autocomplete (FREE) ─────────────────
    loc_values = _collect_location_values(thesis_dict.get("person_filters", {}))
    for val in loc_values:
        try:
            result = await client.person_search_autocomplete(
                "basic_profile.location.country", val
            )
            suggestions = result.get("suggestions") or result.get("values") or []
            if suggestions:
                snapped = suggestions[0].get("value") or suggestions[0] if isinstance(suggestions[0], str) else val
                if isinstance(snapped, dict):
                    snapped = snapped.get("value", val)
                if snapped and snapped != val:
                    thesis_dict["person_filters"] = _replace_filter_value(
                        thesis_dict["person_filters"],
                        "basic_profile.location.country",
                        val,
                        snapped,
                    )
                    changes.append(f"Snapped location '{val}' → '{snapped}'.")
        except Exception:
            pass  # autocomplete failure is non-fatal

    # ── Step 4: Grounding pass — industry autocomplete (FREE) ─────────────────
    industry_values = _collect_industry_values(thesis_dict.get("person_filters", {}))
    for field, val in industry_values:
        try:
            result = await client.company_search_autocomplete("basic_info.industries", val)
            suggestions = result.get("suggestions") or result.get("values") or []
            if suggestions:
                snapped = suggestions[0]
                if isinstance(snapped, dict):
                    snapped = snapped.get("value", val)
                if snapped and snapped != val:
                    thesis_dict["person_filters"] = _replace_filter_value(
                        thesis_dict["person_filters"], field, val, snapped
                    )
                    changes.append(f"Snapped industry '{val}' → '{snapped}'.")
        except Exception:
            pass

    # ── Step 5: Grounding pass — anchor company identify (FREE) ───────────────
    anchor = thesis_dict.get("anchor_strategy", {})
    grounded_companies: list[Any] = []
    for company_name in anchor.get("companies", []):
        try:
            result = await client.company_identify(names=[company_name])
            # result is list of match objects or dict
            matches_list = result if isinstance(result, list) else result.get("matches", [])
            if matches_list:
                first = matches_list[0]
                # Support both shapes: direct company_data or nested matches
                company_data = first.get("company_data") or {}
                if "matches" in first and first["matches"]:
                    company_data = first["matches"][0].get("company_data", {})
                cid = (
                    company_data.get("basic_info", {}).get("crustdata_company_id")
                    or company_data.get("crustdata_company_id")
                )
                grounded_companies.append(
                    {"name": company_name, "company_id": cid}
                    if cid else company_name
                )
                if cid:
                    changes.append(f"Resolved '{company_name}' → company_id={cid}.")
            else:
                grounded_companies.append(company_name)
        except Exception:
            grounded_companies.append(company_name)
    if grounded_companies:
        thesis_dict["anchor_strategy"]["companies"] = grounded_companies

    # ── Step 6: Convert to dataclass ──────────────────────────────────────────
    thesis = _dict_to_thesis(thesis_dict)

    explanation = (
        "Grounding complete. " + " ".join(changes)
        if changes
        else "No grounding changes were needed — all values already matched indexed labels."
    )

    return thesis, explanation
