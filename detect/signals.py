import re
from config import TINY_DESTINATION_MAX_HEADCOUNT

STEALTH_RE = re.compile(r"stealth|building something|incubating|co-?found", re.I)
FOUNDER_RE = re.compile(r"\bfounder|co-?founder|founding (engineer|team)", re.I)


def tag(person) -> dict:
    cur = person.current_role
    name  = (cur.company_name or "") if cur else ""
    title = (cur.title or "")        if cur else ""
    blob  = f"{title} {person.headline}"
    return {
        "stealth":          bool(STEALTH_RE.search(blob) or "stealth" in name.lower()),
        "founder":          bool(FOUNDER_RE.search(blob)),
        "tiny_destination": bool(cur and cur.headcount_latest is not None
                                 and cur.headcount_latest <= TINY_DESTINATION_MAX_HEADCOUNT),
        "open_to_career":   "CAREER_INTEREST" in person.open_to,
        "has_dest_id":      bool(cur and cur.company_id),
    }
