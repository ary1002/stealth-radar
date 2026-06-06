from datetime import datetime, date
from detect.model import Role, Person


def _d(s):
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _normalise(raw: dict) -> dict:
    """Convert nested REST API response to the flat shape that _roles / parse_person read.

    The raw REST API returns experience.employment_details.current/past with
    crustdata_company_id as the id key.  We flatten this to current_employers /
    past_employers with company_id so the rest of the codebase stays clean.
    If the dict is already flat (e.g. in tests) it passes through unchanged.
    """
    if raw.get("current_employers") is not None or raw.get("past_employers") is not None:
        return raw  # already flat

    ed  = ((raw.get("experience") or {}).get("employment_details") or {})
    bp  = raw.get("basic_profile") or {}
    sh  = ((raw.get("social_handles") or {}).get("professional_network_identifier") or {})
    pn  = raw.get("professional_network") or {}
    edu = ((raw.get("education") or {}).get("schools") or [])

    cur = ed.get("current") or []
    if isinstance(cur, dict):
        cur = [cur]

    current_employers = [
        {
            "name":                   c.get("name"),
            "title":                  c.get("title"),
            "start_date":             c.get("start_date"),
            "company_id":             c.get("crustdata_company_id"),
            "company_headcount_latest": c.get("company_headcount_latest"),
            "function_category":      c.get("function_category"),
            "seniority_level":        c.get("seniority_level"),
        }
        for c in cur
    ]
    past_employers = [
        {
            "name":                   p.get("name"),
            "title":                  p.get("title"),
            "start_date":             p.get("start_date"),
            "end_date":               p.get("end_date"),
            "company_id":             p.get("crustdata_company_id"),
            "company_headcount_latest": p.get("company_headcount_latest"),
        }
        for p in (ed.get("past") or [])
    ]

    return {
        "name":               bp.get("name", ""),
        "headline":           bp.get("headline", ""),
        "linkedin_profile_url": sh.get("profile_url", ""),
        "location_country":   (bp.get("location") or {}).get("country"),
        "location_city":      (bp.get("location") or {}).get("city"),
        "open_to_cards":      pn.get("open_to_cards") or [],
        "recently_changed_jobs": raw.get("recently_changed_jobs", False),
        "education_background": [{"institute_name": s.get("school")} for s in edu if s.get("school")],
        "current_employers":  current_employers,
        "past_employers":     past_employers,
    }


def _roles(raw: dict) -> list[Role]:
    roles = []
    for c in (raw.get("current_employers") or []):
        roles.append(Role(
            c.get("company_id"), c.get("name"),
            c.get("title"), _d(c.get("start_date")), None,
            c.get("company_headcount_latest"),
            c.get("function_category"), c.get("seniority_level"),
        ))
    for p in (raw.get("past_employers") or []):
        roles.append(Role(
            p.get("company_id"), p.get("name"),
            p.get("title"), _d(p.get("start_date")), _d(p.get("end_date")),
            p.get("company_headcount_latest"),
            None, None,
        ))
    return roles


def parse_person(raw: dict) -> Person:
    raw = _normalise(raw)
    edu = raw.get("education_background") or []
    schools = [s.get("institute_name") for s in edu if s.get("institute_name")]
    return Person(
        raw.get("linkedin_profile_url", ""),
        raw.get("name", ""),
        raw.get("headline", "") or "",
        raw.get("location_country"),
        raw.get("open_to_cards") or [],
        bool(raw.get("recently_changed_jobs")),
        schools,
        _roles(raw),
        None,
        city=raw.get("location_city"),
    )
