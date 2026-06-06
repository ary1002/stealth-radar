"""
Track A — Investigation pipeline.

investigate(cluster, thesis, client) -> (EvidenceBundle, verdict)

Five ordered steps with early-exit credit guard:
  1. Resolve destination (FREE)      — company_identify
  2. Destination profile (2 cr)      — company_enrich
  3. First roles posted (0.03/result)— job_search
  4. Public footprint (1 cr)         — web_search_live
  5. Founder pedigree (1 cr/profile) — person_enrich (max 3 profiles)

Verdict: "forming_team" | "coincidental" | "unclear" | "truly_stealth"
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from models.schemas import EvidenceBundle, EvidenceItem, ThesisConfig


# ── helpers ───────────────────────────────────────────────────────────────────

def _headcount_range_to_int(employee_count_range: str | None) -> int | None:
    """Convert '11-50' or '10001+' etc. to approximate integer."""
    if not employee_count_range:
        return None
    s = employee_count_range.strip()
    if s.endswith("+"):
        try:
            return int(s[:-1])
        except ValueError:
            return None
    m = re.match(r"(\d+)-(\d+)", s)
    if m:
        return int(m.group(2))  # use upper bound for conservative check
    try:
        return int(s)
    except ValueError:
        return None


def _member_name(member: Any) -> str:
    if isinstance(member, dict):
        return member.get("name") or ""
    return getattr(member, "name", "") or ""


def _member_url(member: Any) -> str | None:
    if isinstance(member, dict):
        return member.get("profile_url") or member.get("linkedin_profile_url")
    return (
        getattr(member, "profile_url", None)
        or getattr(member, "linkedin_profile_url", None)
    )


def _member_headline(member: Any) -> str:
    if isinstance(member, dict):
        return member.get("headline") or member.get("current_role") or ""
    return getattr(member, "headline", "") or getattr(member, "current_role", "") or ""


def _member_tenure(member: Any) -> float:
    """Return anchor_tenure as float; 0 if not available."""
    if isinstance(member, dict):
        return float(member.get("anchor_tenure", 0) or 0)
    return float(getattr(member, "anchor_tenure", 0) or 0)


def _budget_ok(bundle: EvidenceBundle, thesis: ThesisConfig, cost: float) -> bool:
    return (bundle.total_credits + cost) <= thesis.max_investigation_credits


# ── main function ─────────────────────────────────────────────────────────────

async def investigate(
    cluster: dict,
    thesis: ThesisConfig,
    client,
) -> tuple[EvidenceBundle, str]:
    """
    Investigate a cluster and return (EvidenceBundle, verdict).

    verdict is one of: "forming_team" | "coincidental" | "unclear" | "truly_stealth"
    """
    bundle = EvidenceBundle()
    members = cluster.get("members", [])
    destination_name: str | None = cluster.get("destination_name")
    destination_company_id: int | None = cluster.get("destination_company_id")
    truly_stealth = False

    # ── Step 1 — Resolve destination (FREE) ──────────────────────────────────
    if destination_name:
        raw_identify = await client.company_identify(names=[destination_name])

        # Fixture is a list of match objects; each has "matches" key
        resolved_id: int | None = None
        resolved_headcount: int | None = None

        if isinstance(raw_identify, list) and raw_identify:
            first_match_group = raw_identify[0]
            matches = first_match_group.get("matches", []) if isinstance(first_match_group, dict) else []
        elif isinstance(raw_identify, dict):
            # Some clients may return {companies: [...]}
            companies = raw_identify.get("companies", [])
            matches = [{"confidence_score": 1.0, "company_data": c} for c in companies]
        else:
            matches = []

        if matches:
            best = matches[0]
            company_data = best.get("company_data", {})
            resolved_id = company_data.get("crustdata_company_id")

            # Try to get headcount from basic_info.employee_count_range
            basic_info = company_data.get("basic_info", {})
            ecr = basic_info.get("employee_count_range")
            hc_from_range = _headcount_range_to_int(ecr)

            # Also try headcount.total
            hc_obj = company_data.get("headcount", {})
            hc_total = hc_obj.get("total") if isinstance(hc_obj, dict) else None

            resolved_headcount = hc_total or hc_from_range

        if not matches or resolved_id is None:
            # Truly stealth — not indexed
            truly_stealth = True
            bundle.items.append(EvidenceItem(
                source="company_identify",
                finding=f"'{destination_name}' not found in Crustdata index — likely stealth/unindexed",
                supports=0.4,
                confidence=0.6,
                credits_spent=0.0,
                raw={},
            ))
        elif resolved_headcount is not None and resolved_headcount >= 500:
            # Large established company — coincidental
            bundle.items.append(EvidenceItem(
                source="company_identify",
                finding=(
                    f"'{destination_name}' resolved to a company with {resolved_headcount}+ employees"
                    " — too large to be a forming team; cluster likely coincidental"
                ),
                supports=-0.8,
                confidence=0.9,
                credits_spent=0.0,
                raw={"headcount": resolved_headcount, "company_id": resolved_id},
            ))
            bundle.early_exit_reason = "destination_is_large_company"
            return bundle, "coincidental"
        else:
            # Small company found
            destination_company_id = resolved_id
            bundle.items.append(EvidenceItem(
                source="company_identify",
                finding=(
                    f"'{destination_name}' resolved to company_id={resolved_id}"
                    f" (headcount~{resolved_headcount or 'unknown'})"
                ),
                supports=0.3,
                confidence=0.8,
                credits_spent=0.0,
                raw={"company_id": resolved_id, "headcount": resolved_headcount},
            ))
    else:
        # No destination name at all
        truly_stealth = True
        bundle.items.append(EvidenceItem(
            source="company_identify",
            finding="No destination company name available — cluster is stealth/unresolvable",
            supports=0.3,
            confidence=0.5,
            credits_spent=0.0,
            raw={},
        ))

    # ── Step 2 — Destination profile (2 cr) ──────────────────────────────────
    if destination_company_id and not truly_stealth:
        STEP2_COST = 2.0
        if not _budget_ok(bundle, thesis, STEP2_COST):
            bundle.early_exit_reason = "budget_exceeded_before_step2"
            return bundle, "unclear"

        raw_enrich = await client.company_enrich(
            company_ids=[destination_company_id],
            fields=["basic_info", "headcount", "funding", "hiring", "web_traffic", "news"],
        )

        # Parse company_enrich response: list of match groups or dict
        enrich_data: dict = {}
        if isinstance(raw_enrich, list) and raw_enrich:
            first = raw_enrich[0]
            matches_e = first.get("matches", []) if isinstance(first, dict) else []
            if matches_e:
                enrich_data = matches_e[0].get("company_data", {})
        elif isinstance(raw_enrich, dict):
            # Could be top-level company data or {matches: [...]}
            matches_e = raw_enrich.get("matches", [])
            if matches_e:
                enrich_data = matches_e[0].get("company_data", {})
            else:
                enrich_data = raw_enrich

        bundle.total_credits += STEP2_COST

        # Headcount evidence
        hc_obj = enrich_data.get("headcount", {})
        hc_total = hc_obj.get("total") if isinstance(hc_obj, dict) else None
        if hc_total is not None:
            supports = 0.6 if hc_total < 50 else (0.3 if hc_total < 200 else 0.0)
            bundle.items.append(EvidenceItem(
                source="company_enrich/headcount",
                finding=f"Destination headcount: {hc_total}",
                supports=supports,
                confidence=0.8,
                credits_spent=0.0,
                raw={"headcount_total": hc_total},
            ))

        # Funding evidence
        funding = enrich_data.get("funding", {})
        has_funding = False
        if isinstance(funding, dict):
            total_inv = funding.get("total_investment_usd") or 0
            has_funding = total_inv > 0
        bundle.items.append(EvidenceItem(
            source="company_enrich/funding",
            finding="Funding found" if has_funding else "No funding found",
            supports=0.4 if has_funding else 0.0,
            confidence=0.7,
            credits_spent=0.0,
            raw={"has_funding": has_funding},
        ))

        # Web traffic evidence
        wt = enrich_data.get("web_traffic")
        has_traffic = bool(wt and isinstance(wt, dict) and wt.get("domain_traffic"))
        bundle.items.append(EvidenceItem(
            source="company_enrich/web_traffic",
            finding="Web traffic data present" if has_traffic else "No web traffic data",
            supports=0.2 if has_traffic else 0.0,
            confidence=0.6,
            credits_spent=0.0,
            raw={"has_web_traffic": has_traffic},
        ))

    # ── Step 3 — First roles posted (0.03/result) ─────────────────────────────
    if destination_company_id and not truly_stealth:
        # Zero-cost count check first
        STEP3_COST_PER_RESULT = 0.03
        if not _budget_ok(bundle, thesis, 0.0):
            bundle.early_exit_reason = "budget_exceeded_before_step3"
            return bundle, "unclear"

        count_resp = await client.job_search(
            filters={"field": "company.basic_info.company_id", "type": "=", "value": destination_company_id},
            limit=0,
        )
        total_count = count_resp.get("total_count", 0) if isinstance(count_resp, dict) else 0

        if total_count > 0:
            # Estimate cost for limit=5 fetch
            fetch_limit = 5
            step3_cost = STEP3_COST_PER_RESULT * fetch_limit
            if not _budget_ok(bundle, thesis, step3_cost):
                bundle.early_exit_reason = "budget_exceeded_before_step3_fetch"
                return bundle, "unclear"

            job_resp = await client.job_search(
                filters={"field": "company.basic_info.company_id", "type": "=", "value": destination_company_id},
                limit=fetch_limit,
            )
            job_listings = job_resp.get("job_listings", []) if isinstance(job_resp, dict) else []
            count = len(job_listings)
            bundle.total_credits += STEP3_COST_PER_RESULT * count
        else:
            count = 0

        bundle.items.append(EvidenceItem(
            source="job_search",
            finding=f"{count} job postings found",
            supports=0.5 if count > 0 else -0.1,
            confidence=0.7,
            credits_spent=STEP3_COST_PER_RESULT * count,
            raw={"job_count": count},
        ))

    # ── Step 4 — Public footprint (1 cr) ──────────────────────────────────────
    STEP4_COST = 1.0
    if not _budget_ok(bundle, thesis, STEP4_COST):
        bundle.early_exit_reason = "budget_exceeded_before_step4"
        return bundle, "unclear"

    # Build query from member names + destination_name
    query_parts = [_member_name(m) for m in members[:3] if _member_name(m)]
    if destination_name:
        query_parts.append(destination_name)
    query_parts.append("co-founder")
    query = " ".join(query_parts)

    web_resp = await client.web_search_live(
        query=query,
        sources=["web", "news", "social"],
    )
    bundle.total_credits += STEP4_COST

    results = []
    if isinstance(web_resp, dict):
        results = web_resp.get("results", [])

    # Convert timestamp_ms to datetime for any result that has it
    parsed_results = []
    for r in results:
        ts_ms = r.get("timestamp_ms")
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None
        parsed_results.append({**r, "_datetime": dt})

    bundle.items.append(EvidenceItem(
        source="web_search_live",
        finding=f"{len(parsed_results)} web/news results found",
        supports=0.3 if parsed_results else 0.0,
        confidence=0.6,
        credits_spent=STEP4_COST,
        raw={"result_count": len(parsed_results), "query": query},
    ))

    # ── Step 5 — Founder pedigree (1 cr/profile, max 3) ──────────────────────
    STEP5_COST_PER = 1.0
    MAX_PROFILES = 3

    # Sort members by anchor_tenure descending to pick highest-tenure first
    sorted_members = sorted(members, key=_member_tenure, reverse=True)
    candidates = sorted_members[:MAX_PROFILES]

    for member in candidates:
        if not _budget_ok(bundle, thesis, STEP5_COST_PER):
            bundle.early_exit_reason = "budget_exceeded_during_step5"
            break

        url = _member_url(member)
        if not url:
            continue

        enrich_resp = await client.person_enrich(
            profile_urls=[url],
            fields=["basic_profile", "experience", "education", "professional_network"],
        )
        bundle.total_credits += STEP5_COST_PER

        # Parse response: list of match groups
        name = _member_name(member)
        headline_snippet = _member_headline(member)

        if isinstance(enrich_resp, list) and enrich_resp:
            first_group = enrich_resp[0]
            matches_p = first_group.get("matches", []) if isinstance(first_group, dict) else []
            if matches_p:
                person_data = matches_p[0].get("person_data", {})
                basic = person_data.get("basic_profile", {})
                name = basic.get("name") or name
                headline_snippet = basic.get("headline") or headline_snippet

        bundle.items.append(EvidenceItem(
            source="person_enrich",
            finding=f"{name}: {headline_snippet[:120]}" if headline_snippet else name,
            supports=0.2,
            confidence=0.7,
            credits_spent=STEP5_COST_PER,
            raw={"profile_url": url, "name": name},
        ))

    # ── Determine verdict ─────────────────────────────────────────────────────
    if truly_stealth:
        verdict = "truly_stealth"
    else:
        total_support = sum(item.supports for item in bundle.items)
        if total_support >= 1.0:
            verdict = "forming_team"
        elif total_support <= -0.5:
            verdict = "coincidental"
        else:
            verdict = "unclear"

    return bundle, verdict
