import numpy as np
from scipy.stats import rankdata
from detect.leavers import anchor_role
from detect.cluster import tenure_overlap_months

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
    dest_ids = [p.current_role.company_id for p in cluster
                if p.current_role and p.current_role.company_id]
    shared = (max((dest_ids.count(x) for x in set(dest_ids)), default=0) / n) if n else 0.0

    tiny = np.mean([tags[p.profile_url]["tiny_destination"] for p in cluster])
    sf   = np.mean([tags[p.profile_url]["stealth"] or tags[p.profile_url]["founder"] for p in cluster])
    op   = np.mean([tags[p.profile_url]["open_to_career"] for p in cluster])

    starts = [p.current_role.start_date for p in cluster if p.current_role and p.current_role.start_date]
    tight  = float(np.exp(-((max(starts) - min(starts)).days / 30.44) / 6)) if len(starts) >= 2 else 0.5

    ars   = {p.profile_url: anchor_role(p, anchor_id, anchor_name) for p in cluster}
    pairs = [(cluster[i], cluster[j]) for i in range(n) for j in range(i + 1, n)]
    co    = float(np.mean([tenure_overlap_months(ars[a.profile_url], ars[b.profile_url]) > 0
                           for a, b in pairs])) if pairs else 0.0

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
