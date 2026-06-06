import numpy as np
from scipy.stats import rankdata
from detect.leavers import anchor_role
from detect.cluster import tenure_overlap_months

# ── City normalisation ────────────────────────────────────────────────────────

_METRO_MAP: dict[str, str] = {
    "san francisco": "bay area", "sf": "bay area",
    "south san francisco": "bay area", "san jose": "bay area",
    "palo alto": "bay area", "menlo park": "bay area",
    "mountain view": "bay area", "sunnyvale": "bay area",
    "new york": "new york", "new york city": "new york",
    "nyc": "new york", "brooklyn": "new york", "manhattan": "new york",
    "los angeles": "los angeles", "la": "los angeles",
    "santa monica": "los angeles", "culver city": "los angeles",
    "venice": "los angeles",
    "seattle": "seattle", "bellevue": "seattle", "redmond": "seattle",
    "boston": "boston", "cambridge": "boston", "somerville": "boston",
    "washington": "dc", "washington dc": "dc", "dc": "dc",
    "arlington": "dc", "bethesda": "dc",
}


def _normalise_city(city: str) -> str:
    c = city.strip().lower()
    return _METRO_MAP.get(c, c)


def _location_multiplier(cluster) -> float:
    """Compute co-tenure location multiplier based on city co-location of pairs."""
    members = list(cluster)
    n = len(members)
    if n < 2:
        return 1.0

    cities = [
        _normalise_city(p.city) if p.city else None
        for p in members
    ]

    comparable = same = 0
    for i in range(n):
        for j in range(i + 1, n):
            if cities[i] is not None and cities[j] is not None:
                comparable += 1
                if cities[i] == cities[j]:
                    same += 1

    if comparable == 0:
        return 0.90   # no city data — slight unknown discount

    ratio = same / comparable
    if ratio == 1.0:
        return 1.00
    if ratio >= 0.5:
        return 0.85
    if ratio > 0.0:
        return 0.70
    return 0.55

# ── Destination convergence ───────────────────────────────────────────────────

_NON_CONTRIBUTING_TERMS = {
    "investor", "vc", "venture", "venture capital", "advisor", "adviser",
    "board", "angel", "scout", "in residence", "partner at", "limited partner",
    "lp", "check writer", "fund",
}

_FOUNDING_TERMS = {
    "founder", "co-founder", "cofounder", "ceo", "cto",
}


def _is_non_contributing_role(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(term in t for term in _NON_CONTRIBUTING_TERMS)


def _is_founding_role(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(term in t for term in _FOUNDING_TERMS)


def _is_stealth_name(name: str | None) -> bool:
    """True if name is null/empty or contains 'stealth'."""
    if not name:
        return True
    return "stealth" in name.lower()


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity on whitespace-split token sets."""
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def compute_destination_convergence(cluster) -> float:
    """Continuous convergence signal: fraction of members at the most common destination.

    Uses company_id when indexed (> 0); falls back to normalised destination name
    for unindexed clusters. Country and role signals now live in the hypothesis layer.
    """
    from collections import Counter
    members = list(cluster)
    n = len(members)
    if n == 0:
        return 0.0

    ids     = [p.current_role.company_id if p.current_role else None for p in members]
    indexed = [cid for cid in ids if cid and cid > 0]

    if indexed:
        most_common_count = Counter(indexed).most_common(1)[0][1]
        return most_common_count / n

    # All unindexed — use normalised name fraction
    names = [
        p.current_role.company_name.strip().lower()
        if p.current_role and p.current_role.company_name
        else None
        for p in members
    ]
    non_null = [nm for nm in names if nm]
    if not non_null:
        return 0.0
    most_common_count = Counter(non_null).most_common(1)[0][1]
    return most_common_count / n


def convergence_evidence_items(cluster) -> list[dict]:
    """Return evidence dicts for Branches 4/5 (different destinations).
    Callers may convert these to EvidenceItem objects for the investigation bundle.
    """
    members = list(cluster)
    n = len(members)
    if n == 0:
        return []

    ids     = [p.current_role.company_id if p.current_role else None for p in members]
    indexed = [cid for cid in ids if cid and cid > 0]
    if len(indexed) > 0:
        return []     # only emit for all-unindexed case

    countries   = [p.country for p in members]
    non_null_co = [c for c in countries if c]
    all_same_co = (len(non_null_co) == n and len({c.lower() for c in non_null_co}) == 1)
    all_diff_co = (len(non_null_co) >= 2 and len({c.lower() for c in non_null_co}) > 1)
    titles      = [(p.current_role.title or "") if p.current_role else "" for p in members]

    items = []
    if all_same_co:
        has_non_contrib = any(_is_non_contributing_role(t) for t in titles)
        if has_non_contrib:
            items.append({
                "source": "convergence_analysis",
                "finding": "non-contributing roles detected — possible investor or advisor around founding team",
                "supports": 0.45,
                "credits_spent": 0.0,
            })
        else:
            items.append({
                "source": "convergence_analysis",
                "finding": "split destinations same country",
                "supports": 0.35,
                "credits_spent": 0.0,
            })
    elif all_diff_co:
        items.append({
            "source": "convergence_analysis",
            "finding": "split destinations cross-country — weak signal",
            "supports": 0.05,
            "credits_spent": 0.0,
        })
    return items


WEIGHTS = {
    "size_score":            0.18,
    "shared_destination":    0.22,
    "destination_tiny":      0.12,
    "stealth_founder_ratio": 0.16,
    "window_tightness":      0.12,
    "co_tenure":             0.14,
    "open_to":               0.06,
}

def size_score(n: int) -> float:
    return float(np.exp(-0.5 * ((n - 4) / 2.2) ** 2))

def cluster_features(cluster, tags, anchor_id=None, anchor_name=None) -> dict:
    n = len(cluster)
    shared = compute_destination_convergence(cluster)

    tiny = np.mean([tags[p.profile_url]["tiny_destination"] for p in cluster])
    sf   = np.mean([tags[p.profile_url]["stealth"] or tags[p.profile_url]["founder"] for p in cluster])
    op   = np.mean([tags[p.profile_url]["open_to_career"] for p in cluster])

    starts = [p.current_role.start_date for p in cluster if p.current_role and p.current_role.start_date]
    tight  = float(np.exp(-((max(starts) - min(starts)).days / 30.44) / 6)) if len(starts) >= 2 else 0.5

    ars   = {p.profile_url: anchor_role(p, anchor_id, anchor_name) for p in cluster}
    pairs = [(cluster[i], cluster[j]) for i in range(n) for j in range(i + 1, n)]
    raw_co = float(np.mean([tenure_overlap_months(ars[a.profile_url], ars[b.profile_url]) > 0
                             for a, b in pairs])) if pairs else 0.0
    co = raw_co * _location_multiplier(cluster)

    return {"size_score": size_score(n), "shared_destination": shared,
            "destination_tiny": float(tiny), "stealth_founder_ratio": float(sf),
            "window_tightness": tight, "co_tenure": co, "open_to": float(op)}

# Features scored on absolute value (0-1 already meaningful)
ABSOLUTE_FEATURES = {"shared_destination", "stealth_founder_ratio",
                     "window_tightness", "destination_tiny", "size_score"}
# Features scored by percentile rank (sparse or continuous)
PERCENTILE_FEATURES = {"co_tenure", "open_to"}

def score_clusters(rows: list[dict]) -> np.ndarray:
    """Hybrid scoring: absolute for convergence/signal features, percentile for noisy ones."""
    if not rows:
        return np.array([])
    out = np.zeros(len(rows))
    for k, w in WEIGHTS.items():
        v = np.array([r[k] for r in rows], dtype=float)
        if k in ABSOLUTE_FEATURES:
            norm = v  # raw value, already 0-1
        else:
            # percentile rank
            norm = (rankdata(v, method="average") - 1) / (len(v) - 1) if len(v) > 1 else np.ones_like(v)
        out += w * norm
    return out * 100

def tier(s: float) -> str:
    return "High" if s >= 75 else "Medium" if s >= 50 else "Low" if s >= 25 else "Watch"
