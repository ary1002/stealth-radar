import networkx as nx
from collections import defaultdict
from datetime import date
from detect.leavers import anchor_role
from config import MEDIUM_CLUSTER_WINDOW_MONTHS, MIN_CLUSTER_SIZE, MEDIUM_MIN_CO_TENURE_MONTHS

FAR = date(2100, 1, 1)


def _months(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def tenure_overlap_months(r1, r2) -> float:
    """Overlap of two people's tenure AT THE ANCHOR."""
    if not (r1 and r2 and r1.start_date and r2.start_date):
        return 0.0
    s = max(r1.start_date, r2.start_date)
    e = min(r1.end_date or FAR, r2.end_date or FAR)
    return max(0, _months(s, e)) if e > s else 0.0


def strong_clusters(leavers):
    """Convergence on a shared, already-indexed new employer.
    company_id=0 (unresolved sentinel) is excluded — falsy check.
    Headcount gating is handled downstream at tier/label assignment.
    """
    groups = defaultdict(list)
    for p in leavers:
        cur = p.current_role
        if not cur or not cur.company_id:
            continue
        groups[cur.company_id].append(p)
    return [(cid, g) for cid, g in groups.items() if len(g) >= MIN_CLUSTER_SIZE]


def medium_clusters(leavers, tags, anchor_id=None, anchor_name=None):
    """Stealth/founder leavers, no shared dest id yet (too new to be indexed):
    link pairs that left the anchor in a tight window AND overlapped there."""
    cand = [p for p in leavers if tags[p.profile_url]["stealth"] or tags[p.profile_url]["founder"]]
    by_url = {p.profile_url: p for p in cand}
    G = nx.Graph()
    G.add_nodes_from(by_url)
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i], cand[j]
            ra = anchor_role(a, anchor_id, anchor_name)
            rb = anchor_role(b, anchor_id, anchor_name)
            if not (ra and rb and ra.end_date and rb.end_date):
                continue
            gap     = abs(_months(ra.end_date, rb.end_date))
            overlap = tenure_overlap_months(ra, rb)
            # Same destination check
            a_cur = a.current_role
            b_cur = b.current_role
            same_dest = (
                a_cur and b_cur and a_cur.company_id and b_cur.company_id
                and a_cur.company_id == b_cur.company_id
            ) or (
                a_cur and b_cur and a_cur.company_name and b_cur.company_name
                and a_cur.company_name.strip().lower() == b_cur.company_name.strip().lower()
            )
            qualifies = (
                gap <= MEDIUM_CLUSTER_WINDOW_MONTHS
                and (same_dest or overlap >= MEDIUM_MIN_CO_TENURE_MONTHS)
            )
            if qualifies:
                G.add_edge(a.profile_url, b.profile_url)
    return [[by_url[u] for u in c] for c in nx.connected_components(G) if len(c) >= MIN_CLUSTER_SIZE]
