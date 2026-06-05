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


# ── Post-processing: dedup and merge ─────────────────────────────────────────

def _co_tenure_score(person, cluster, anchor_id=None, anchor_name=None) -> float:
    """Average tenure overlap between person and all other cluster members."""
    ra = anchor_role(person, anchor_id, anchor_name)
    if not ra:
        return 0.0
    others = [p for p in cluster if p.profile_url != person.profile_url]
    if not others:
        return 0.0
    overlaps = [
        tenure_overlap_months(ra, anchor_role(o, anchor_id, anchor_name))
        for o in others
    ]
    return sum(overlaps) / len(overlaps)


def deduplicate_members(clusters: list[list], anchor_id=None, anchor_name=None) -> list[list]:
    """Rule 1: if a profile_url appears in more than one cluster, keep it only
    in the cluster where it has the highest co-tenure score; break ties by
    cluster size (prefer larger cluster).

    Returns the filtered cluster list, dropping any cluster that falls below
    MIN_CLUSTER_SIZE after removal.
    """
    # map url → list of (cluster_index, co_tenure_score, cluster_size)
    url_candidates: dict[str, list[tuple]] = defaultdict(list)
    for ci, cluster in enumerate(clusters):
        for p in cluster:
            score = _co_tenure_score(p, cluster, anchor_id, anchor_name)
            url_candidates[p.profile_url].append((ci, score, len(cluster)))

    # for each URL that appears in >1 cluster, pick the best cluster
    url_to_cluster: dict[str, int] = {}
    for url, entries in url_candidates.items():
        if len(entries) == 1:
            url_to_cluster[url] = entries[0][0]
        else:
            # sort by (co_tenure_score DESC, cluster_size DESC)
            best = max(entries, key=lambda e: (e[1], e[2]))
            url_to_cluster[url] = best[0]

    # rebuild clusters keeping only assigned members
    result = []
    for ci, cluster in enumerate(clusters):
        kept = [p for p in cluster if url_to_cluster.get(p.profile_url) == ci]
        if len(kept) >= MIN_CLUSTER_SIZE:
            result.append(kept)
    return result


def merge_overlapping_clusters(clusters: list[list]) -> list[list]:
    """Rule 2: if two clusters share >50% of their members (by profile_url),
    merge them. Destination of the merged cluster is the most common
    current_company among members, or left as-is (heterogeneous destinations
    are surfaced at scoring/adjudication time).

    Uses union-find to handle transitive chains.
    """
    n = len(clusters)
    if n < 2:
        return clusters

    url_sets = [frozenset(p.profile_url for p in c) for c in clusters]

    # union-find
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            intersection = url_sets[i] & url_sets[j]
            if not intersection:
                continue
            # overlap fraction relative to the smaller cluster
            smaller = min(len(url_sets[i]), len(url_sets[j]))
            if len(intersection) / smaller > 0.5:
                union(i, j)

    # group clusters by their root
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result = []
    for root, indices in groups.items():
        if len(indices) == 1:
            result.append(clusters[indices[0]])
            continue
        # merge: deduplicate members by profile_url, keep first occurrence
        seen_urls: set[str] = set()
        merged: list = []
        for ci in indices:
            for p in clusters[ci]:
                if p.profile_url not in seen_urls:
                    seen_urls.add(p.profile_url)
                    merged.append(p)
        if len(merged) >= MIN_CLUSTER_SIZE:
            result.append(merged)

    return result


def postprocess_clusters(
    clusters: list[list],
    anchor_id=None,
    anchor_name=None,
) -> list[list]:
    """Apply Rule 1 (dedup members) then Rule 2 (merge overlapping) in order."""
    clusters = deduplicate_members(clusters, anchor_id, anchor_name)
    clusters = merge_overlapping_clusters(clusters)
    return clusters
