"""
Hypothesis catalog for cluster adjudication routing.

detect_hypotheses(cluster, investigation_evidence, anchor_name) -> dict
  "benign":    [{name, description, auto_confirmed}]
  "confirming": [{name, description}]

should_skip_adjudication(hypotheses) -> bool
  Hard skip only for auto-confirmed geographic_coincidence with no confirming signals.

route_cluster(hypotheses) -> str
  "full" | "skeptical" | "hostile"
"""
from __future__ import annotations

_NON_CONTRIBUTING_TERMS = {
    "investor", "vc", "venture", "venture capital", "advisor", "adviser",
    "board", "angel", "scout", "in residence", "partner at", "limited partner",
    "lp", "check writer", "fund",
}
_FOUNDING_TERMS = {"founder", "co-founder", "cofounder", "ceo", "cto", "founding"}
_RESEARCH_TERMS = {"researcher", "scientist", "research", "phd", "postdoc", "fellow", "faculty"}
_INVESTOR_BACKING = {"a16z", "yc", "sequoia", "greylock", "founders fund", "sr"}
_FUNCTION_BUCKETS = {
    "technical": {"engineer", "developer", "architect", "researcher", "scientist",
                  "technical", "cto", "data"},
    "product":   {"product", "design", "designer", "ux", "ui", "creative"},
    "business":  {"ceo", "business", "commercial", "gtm", "go-to-market", "sales",
                  "marketing", "growth", "partnerships", "revenue"},
}


# ── Tiny helpers ──────────────────────────────────────────────────────────────

def _title(p) -> str:
    return (p.current_role.title or "").lower() if p.current_role else ""

def _has(title: str, terms: set) -> bool:
    return any(t in title for t in terms)

def _is_non_contributing(title: str) -> bool:
    return _has(title, _NON_CONTRIBUTING_TERMS)

def _is_founding(title: str) -> bool:
    return _has(title, _FOUNDING_TERMS)

def _is_research(title: str) -> bool:
    return _has(title, _RESEARCH_TERMS)

def _is_generic_stealth(name: str | None) -> bool:
    if not name:
        return True
    n = name.lower().strip()
    return n in {"stealth", "stealth mode", "stealth startup",
                 "stealth ai startup", "stealth ai", ""}

def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

def _dest_name(p) -> str | None:
    if p.current_role and p.current_role.company_name:
        return p.current_role.company_name.strip()
    return None


# ── Main function ─────────────────────────────────────────────────────────────

def detect_hypotheses(
    cluster,
    investigation_evidence=None,
    anchor_name: str | None = None,
) -> dict:
    """Return detected benign hypotheses and confirming signals for the cluster."""
    members   = list(cluster)
    n         = len(members)
    titles    = [_title(p) for p in members]
    countries = [p.country for p in members if p.country]
    dest_names = [_dest_name(p) for p in members]
    dest_ids  = [p.current_role.company_id if p.current_role else None for p in members]
    headcounts = [
        p.current_role.headcount_latest
        for p in members
        if p.current_role and p.current_role.headcount_latest
    ]
    max_hc = max(headcounts) if headcounts else 0

    non_null_cos  = [c.lower() for c in countries]
    unique_cos    = set(non_null_cos)
    indexed_ids   = [cid for cid in dest_ids if cid and isinstance(cid, int) and cid > 0]

    has_founding         = any(_is_founding(t) for t in titles)
    has_non_contributing = any(_is_non_contributing(t) for t in titles)
    has_research         = any(_is_research(t) for t in titles)
    all_generic_stealth  = all(_is_generic_stealth(nm) for nm in dest_names)

    benign:    list[dict] = []
    confirming: list[dict] = []

    # ── Benign hypotheses ─────────────────────────────────────────────────────

    # geographic_coincidence
    cross_country = len(unique_cos) > 1 and len(non_null_cos) >= 2
    if cross_country:
        benign.append({
            "name": "geographic_coincidence",
            "description": (
                f"Members are in different countries ({', '.join(sorted(unique_cos))}) — "
                "this may be independent career moves rather than a coordinated departure."
            ),
            "auto_confirmed": True,
        })

    # coordinated_layoff
    if n >= 4:
        try:
            from detect.leavers import anchor_role as _ar
            end_dates = [
                _ar(p, anchor_name=anchor_name).end_date
                for p in members
                if _ar(p, anchor_name=anchor_name) and _ar(p, anchor_name=anchor_name).end_date
            ]
            if len(end_dates) >= n:
                window = (max(end_dates) - min(end_dates)).days
                title_prefixes = {t.split()[0] if t else "" for t in titles}
                if window < 14 and len(title_prefixes) <= 2:
                    benign.append({
                        "name": "coordinated_layoff",
                        "description": (
                            f"{n} members departed within {window} days with similar titles — "
                            "pattern is consistent with a coordinated layoff."
                        ),
                        "auto_confirmed": False,
                    })
        except Exception:
            pass

    # acquihire
    if len(indexed_ids) == n and len(set(indexed_ids)) == 1 and max_hc > 100:
        benign.append({
            "name": "acquihire",
            "description": (
                f"All members at the same indexed destination (headcount ~{max_hc}). "
                "Pattern is consistent with an acquihire rather than independent co-founding."
            ),
            "auto_confirmed": False,
        })

    # desirable_employer_gravity
    if max_hc >= 500 and len(indexed_ids) > 0:
        benign.append({
            "name": "desirable_employer_gravity",
            "description": (
                f"Destination company headcount ~{max_hc} — a large, desirable employer. "
                "Convergence may reflect independent hiring rather than coordinated team formation."
            ),
            "auto_confirmed": True,
        })

    # research_rotation
    if has_research and not has_founding:
        benign.append({
            "name": "research_rotation",
            "description": (
                "Members have research/scientist titles with no founding roles. "
                "Pattern is consistent with a researcher rotating between labs."
            ),
            "auto_confirmed": False,
        })

    # internal_reorg
    if anchor_name:
        for nm in dest_names:
            if nm and _token_overlap(anchor_name, nm) > 0.5:
                benign.append({
                    "name": "internal_reorg",
                    "description": (
                        f"Destination name '{nm}' shares significant tokens with anchor '{anchor_name}' — "
                        "may be a spin-off, rebranding, or internal restructure rather than a new founding."
                    ),
                    "auto_confirmed": False,
                })
                break

    # generic_stealth_collision
    if all_generic_stealth:
        benign.append({
            "name": "generic_stealth_collision",
            "description": (
                "All members list generic 'Stealth'/'Stealth Mode'/null destinations — "
                "they may be at different unrelated companies, not a single shared one."
            ),
            "auto_confirmed": False,
        })

    # investor_advisor_proximity
    if has_non_contributing:
        benign.append({
            "name": "investor_advisor_proximity",
            "description": (
                "At least one member has a non-contributing role (investor/advisor/board/VC/scout). "
                "They may be supporting rather than co-founding the venture."
            ),
            "auto_confirmed": False,
        })

    # stale_title_lag
    if anchor_name:
        an_lower = anchor_name.lower()
        try:
            from detect.leavers import anchor_role as _ar
            for p in members:
                ar = _ar(p, anchor_name=anchor_name)
                if ar and ar.end_date and an_lower in p.headline.lower():
                    benign.append({
                        "name": "stale_title_lag",
                        "description": (
                            f"{p.name}'s headline still references {anchor_name} despite an "
                            "end_date on that role — title data may be stale."
                        ),
                        "auto_confirmed": False,
                    })
                    break
        except Exception:
            pass

    # no_real_cotenure
    try:
        from detect.leavers import anchor_role as _ar
        from detect.cluster import tenure_overlap_months
        ars = [_ar(p, anchor_name=anchor_name) for p in members]
        pairs = [
            (ars[i], ars[j])
            for i in range(n)
            for j in range(i + 1, n)
        ]
        if pairs and all(
            tenure_overlap_months(a, b) == 0
            for a, b in pairs
            if a and b
        ):
            benign.append({
                "name": "no_real_cotenure",
                "description": (
                    "Members share the anchor company but employment date ranges do not overlap — "
                    "they may never have worked together."
                ),
                "auto_confirmed": False,
            })
    except Exception:
        pass

    # employee_not_founder
    if (
        not has_founding
        and 0 < max_hc < 25
        and len(indexed_ids) > 0
        and len(set(indexed_ids)) == 1
    ):
        benign.append({
            "name": "employee_not_founder",
            "description": (
                f"Destination resolves to a small company (headcount ~{max_hc}) "
                "but no member has a founding title — may be early employees, not founders."
            ),
            "auto_confirmed": False,
        })

    # ── Confirming signals ────────────────────────────────────────────────────

    # explicit_founding_titles
    founding_count = sum(1 for t in titles if _is_founding(t))
    if founding_count >= n / 2:
        confirming.append({
            "name": "explicit_founding_titles",
            "description": (
                f"{founding_count}/{n} members have explicit founding titles "
                "(Founder/Co-Founder/CEO/CTO)."
            ),
        })

    # named_investor_backing
    for p in members:
        hl = p.headline.lower()
        matched = [inv for inv in _INVESTOR_BACKING if inv in hl]
        if matched:
            confirming.append({
                "name": "named_investor_backing",
                "description": (
                    f"{p.name}'s headline references named backer(s): {', '.join(matched)}."
                ),
            })
            break

    # identical_specific_stealth_name (only meaningful when founding intent present)
    non_generic = [nm for nm in dest_names if nm and not _is_generic_stealth(nm)]
    if (
        non_generic
        and len(set(nm.lower() for nm in non_generic)) == 1
        and len(non_generic) == n
        and has_founding  # don't fire for pure research-rotation clusters
    ):
        confirming.append({
            "name": "identical_specific_stealth_name",
            "description": f"All {n} members list the same specific destination: '{non_generic[0]}'.",
        })

    # resolved_tiny_full_convergence
    if 0 < max_hc < 25 and len(indexed_ids) == n and len(set(indexed_ids)) == 1:
        confirming.append({
            "name": "resolved_tiny_full_convergence",
            "description": (
                f"Destination is indexed with headcount ~{max_hc} and "
                f"all {n} members converge on it."
            ),
        })

    # co_located_tight_window
    if len(non_null_cos) >= 2 and len(unique_cos) == 1:
        try:
            from score.model import _normalise_city
            from detect.leavers import anchor_role as _ar
            cities = [_normalise_city(p.city) if p.city else None for p in members]
            non_null_cities = [c for c in cities if c]
            if len(non_null_cities) == n and len(set(non_null_cities)) == 1:
                end_dates = [
                    _ar(p, anchor_name=anchor_name).end_date
                    for p in members
                    if _ar(p, anchor_name=anchor_name) and _ar(p, anchor_name=anchor_name).end_date
                ]
                if len(end_dates) >= 2:
                    window_days = (max(end_dates) - min(end_dates)).days
                    if window_days <= 30:
                        confirming.append({
                            "name": "co_located_tight_window",
                            "description": (
                                f"All members in the same metro ({non_null_cities[0]}) "
                                f"with departures within {window_days} days."
                            ),
                        })
        except Exception:
            pass

    # complementary_skills
    buckets_hit: set[str] = set()
    for t in titles:
        for bucket, terms in _FUNCTION_BUCKETS.items():
            if any(term in t for term in terms):
                buckets_hit.add(bucket)
    if len(buckets_hit) >= 2:
        confirming.append({
            "name": "complementary_skills",
            "description": (
                f"Member titles span {len(buckets_hit)} distinct functions "
                f"({', '.join(sorted(buckets_hit))}) — suggests a balanced founding team."
            ),
        })

    # Investigation-derived signals
    if investigation_evidence:
        items = (
            investigation_evidence.items
            if hasattr(investigation_evidence, "items")
            else []
        )
        for ev in items:
            finding = (
                ev.get("finding", "") if isinstance(ev, dict) else getattr(ev, "finding", "") or ""
            ).lower()
            if ("domain" in finding or "press" in finding) and not any(
                c["name"] == "web_footprint_found" for c in confirming
            ):
                confirming.append({
                    "name": "web_footprint_found",
                    "description": "Investigation found a registered domain or press coverage for the destination.",
                })
            if "job posting" in finding and not any(
                c["name"] == "first_job_postings_found" for c in confirming
            ):
                confirming.append({
                    "name": "first_job_postings_found",
                    "description": "Investigation found active job postings at the destination.",
                })

    return {"benign": benign, "confirming": confirming}


# ── Routing ───────────────────────────────────────────────────────────────────

def should_skip_adjudication(hypotheses: dict) -> bool:
    """Skip only when geographic_coincidence is auto-confirmed AND no confirming signals."""
    benign     = hypotheses.get("benign", [])
    confirming = hypotheses.get("confirming", [])
    geo_auto   = any(
        h["name"] == "geographic_coincidence" and h.get("auto_confirmed", False)
        for h in benign
    )
    has_confirming = bool(confirming)
    has_founding   = any(c["name"] == "explicit_founding_titles" for c in confirming)
    return geo_auto and not has_confirming and not has_founding


def route_cluster(hypotheses: dict) -> str:
    """Coarse routing: full / skeptical / hostile."""
    benign     = hypotheses.get("benign", [])
    confirming = hypotheses.get("confirming", [])
    n_benign     = len(benign)
    n_confirming = len(confirming)
    has_founding_confirm = any(c["name"] == "explicit_founding_titles" for c in confirming)

    # Research rotation with no founding signal → always hostile
    if any(b["name"] == "research_rotation" for b in benign) and not has_founding_confirm:
        return "hostile"

    if n_confirming >= 2 and n_benign <= 1:
        return "full"
    if n_confirming >= 1:
        return "skeptical"
    if n_benign >= 3:
        return "hostile"
    return "skeptical"


def hypotheses_to_prompt_block(hypotheses: dict, route: str) -> str:
    """Render hypotheses as a prompt block for Claude."""
    benign     = hypotheses.get("benign", [])
    confirming = hypotheses.get("confirming", [])
    lines = []
    if route == "hostile":
        lines.append(
            "⚠ Approach this cluster with strong skepticism — "
            "the signal pattern has multiple benign explanations.\n"
        )
    if benign:
        lines.append(
            "The following alternative explanations are consistent with this cluster's data. "
            "Address each one in your reasoning — state whether the evidence rules it out, "
            "and if it cannot be ruled out, say so plainly. Do not ignore any listed hypothesis.\n"
        )
        for h in benign:
            lines.append(f"• [{h['name']}] {h['description']}")
    if confirming:
        lines.append("\nConfirming signals detected:")
        for c in confirming:
            lines.append(f"• [{c['name']}] {c['description']}")
    return "\n".join(lines)
