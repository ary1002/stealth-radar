"""
Founder-anchored backtest — search path.

Profile acquisition: /person/search (name + anchor filter, $0.03/result).
/person/enrich is NOT used — it returns identity-only on this account.
Each unique founder is pulled once and reused across all horizons.
as-of reconstruction is in-memory and free.

Validates: "given the members, the engine scores a real formation correctly
and would have fired N months early."
NOT: cold-anchor discovery from a large cohort pull.
"""
import asyncio
import json
import logging
from datetime import date
from dateutil.relativedelta import relativedelta

from ingestion.client import CrustdataClient
from ingestion.cohort import COHORT_FIELDS, SORTS
from detect.parse import parse_person
from backtest.asof import asof_person

logger = logging.getLogger(__name__)

CREDIT_PER_RESULT = 0.03
BACKTEST_CREDIT_CEILING = 20.0


# ── profile acquisition via search ───────────────────────────────────────────

async def _search_one(client, first: str, last: str, anchor: str,
                      known_url: str) -> dict | None:
    """Search by name + anchor, disambiguate by known URL. Returns raw dict or None."""
    filt = {"op": "and", "conditions": [
        {"field": "basic_profile.first_name", "type": "[.]", "value": first},
        {"field": "basic_profile.last_name",  "type": "[.]", "value": last},
        {"field": "experience.employment_details.past.company_name",
         "type": "[.]", "value": anchor},
    ]}
    rows = []
    async for page in client.person_search(
        filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=5
    ):
        rows.extend(page)
        break

    norm = known_url.rstrip("/")
    for r in rows:
        p = parse_person(r)
        if p.profile_url and p.profile_url.rstrip("/") == norm:
            return r
    if rows:
        logger.warning("  %s %s: %d result(s) but none matched slug %s",
                       first, last, len(rows), known_url)
    else:
        logger.warning("  %s %s: NOT FOUND in Crustdata", first, last)
    return None


async def _fetch_all_founders(ground_truth: list) -> tuple[dict, float]:
    """Pull each unique founder once. Returns (url→raw_dict, credits_spent)."""
    # Deduplicate by URL
    by_url: dict[str, dict] = {}   # url → founder_search entry
    for gt in ground_truth:
        for fs in gt["founder_search"]:
            by_url.setdefault(fs["url"], fs)

    client = CrustdataClient()
    raw_by_url: dict[str, dict] = {}
    results_count = 0
    try:
        for url, fs in by_url.items():
            raw = await _search_one(
                client, fs["first"], fs["last"],
                next(gt["prior_employer_name"]
                     for gt in ground_truth
                     if any(f["url"] == url for f in gt["founder_search"])),
                url,
            )
            if raw is not None:
                raw_by_url[url] = raw
                results_count += 1  # we got 1 result (limit=5 but billing is per row)
    finally:
        await client.close()

    # Conservative: bill 1 result per successful lookup + 0 per miss
    credits = results_count * CREDIT_PER_RESULT
    return raw_by_url, credits


# ── main evaluate ─────────────────────────────────────────────────────────────

def _is_large_dest_strong(cluster):
    """True if all members share a single large-employer destination (headcount >= threshold)."""
    from config import STRONG_CLUSTER_MAX_HEADCOUNT
    cur_ids = {p.current_role.company_id for p in cluster
               if p.current_role and p.current_role.company_id}
    if len(cur_ids) != 1:
        return False  # not a strong cluster (multiple destinations)
    hcs = [p.current_role.headcount_latest for p in cluster
           if p.current_role and p.current_role.headcount_latest is not None]
    return bool(hcs) and max(hcs) >= STRONG_CLUSTER_MAX_HEADCOUNT


def evaluate(ground_truth: list, horizons=(3, 6, 9)) -> dict:
    from detect.signals import tag
    from detect.cluster import strong_clusters, medium_clusters
    from score.model import cluster_features, score_clusters, tier

    # Budget check
    unique_founders = {f["url"] for gt in ground_truth for f in gt["founder_search"]}
    est = len(unique_founders) * CREDIT_PER_RESULT
    logger.info("Estimated cost: %d profiles × $%.2f = $%.2f credits",
                len(unique_founders), CREDIT_PER_RESULT, est)
    if est > BACKTEST_CREDIT_CEILING:
        raise RuntimeError(f"Estimated ${est:.2f} exceeds ceiling ${BACKTEST_CREDIT_CEILING:.2f}")

    # Acquire profiles
    logger.info("Fetching %d unique founder profiles via /person/search…",
                len(unique_founders))
    raw_by_url, credits_spent = asyncio.run(_fetch_all_founders(ground_truth))
    logger.info("Fetched %d/%d — running total $%.2f credits",
                len(raw_by_url), len(unique_founders), credits_spent)

    # Parse once, reuse across horizons
    parsed_by_url = {}
    for url, raw in raw_by_url.items():
        try:
            p = parse_person(raw)
            if p.profile_url:
                parsed_by_url[p.profile_url] = p
        except Exception as exc:
            logger.warning("parse_person failed for %s: %s", url, exc)

    per_horizon = {n: {"recall": 0.0, "caught": 0, "total": len(ground_truth)}
                   for n in horizons}
    rows = []
    detail = []   # per-entry per-horizon detail for reporting

    for gt in ground_truth:
        announce     = date.fromisoformat(gt["announce_date"])
        anchor_name  = gt["prior_employer_name"]
        founder_urls = gt["founder_profile_urls"]

        founders = [parsed_by_url[u] for u in founder_urls if u in parsed_by_url]
        missing  = [u for u in founder_urls if u not in parsed_by_url]
        if missing:
            logger.warning("%s: %d founder(s) unresolved: %s",
                           gt["startup"], len(missing), missing)
        if len(founders) < 2:
            logger.warning("%s: <2 founders — marking uncaught all horizons", gt["startup"])
            for n in horizons:
                rows.append({"startup": gt["startup"], "announce_date": announce,
                             "horizon_months": n, "caught": False, "score_at_horizon": 0.0})
                detail.append({"startup": gt["startup"], "horizon": n,
                               "caught": False, "score": 0.0,
                               "clusters": 0, "note": "founder unresolved"})
            continue

        for n in sorted(horizons, reverse=True):
            asof_t = announce - relativedelta(months=n)

            asof_founders = []
            for p in founders:
                try:
                    asof_founders.append(asof_person(p, asof_t))
                except Exception as exc:
                    logger.warning("asof_person %s @ %s: %s", p.name, asof_t, exc)

            if len(asof_founders) < 2:
                rows.append({"startup": gt["startup"], "announce_date": announce,
                             "horizon_months": n, "caught": False, "score_at_horizon": 0.0})
                detail.append({"startup": gt["startup"], "horizon": n,
                               "caught": False, "score": 0.0, "clusters": 0,
                               "note": "asof produced <2 founders"})
                continue

            tags         = {p.profile_url: tag(p) for p in asof_founders}
            strong       = [g for _, g in strong_clusters(asof_founders)]
            medium_cl    = medium_clusters(asof_founders, tags, anchor_name=anchor_name)
            all_clusters = strong + medium_cl

            hit        = False
            best_score = 0.0
            note       = ""

            if all_clusters:
                feats  = [cluster_features(c, tags, anchor_name=anchor_name)
                          for c in all_clusters]
                scores = score_clusters(feats)
                fset   = {p.profile_url for p in asof_founders}
                for cluster, s in zip(all_clusters, scores):
                    s = float(s)
                    if s >= 50 and len({p.profile_url for p in cluster} & fset) >= 2 and not _is_large_dest_strong(cluster):
                        hit        = True
                        best_score = s
                        note       = (f"strong" if cluster in strong else "medium") + f" score={s:.1f}"
                        break
                if not hit:
                    best_score = float(max(scores))
                    note = f"no cluster scored ≥50 (best={best_score:.1f})"
            else:
                # Describe why no cluster formed
                cur_roles = [(p.name, p.current_role.company_name if p.current_role else "None",
                              p.current_role.company_id if p.current_role else None)
                             for p in asof_founders]
                note = f"no clusters — current_roles: {cur_roles}"

            logger.info("  %s -%dmo (%s): caught=%s score=%.1f clusters=%d note=%s",
                        gt["startup"], n, asof_t, hit, best_score,
                        len(all_clusters), note)

            rows.append({"startup": gt["startup"], "announce_date": announce,
                         "horizon_months": n, "caught": hit, "score_at_horizon": best_score})
            detail.append({"startup": gt["startup"], "horizon": n,
                           "caught": hit, "score": best_score,
                           "clusters": len(all_clusters), "note": note})
            if hit:
                per_horizon[n]["caught"] += 1

    for n in horizons:
        total = per_horizon[n]["total"]
        per_horizon[n]["recall"] = per_horizon[n]["caught"] / total if total else 0.0

    # Lead time = largest N where caught (most advance warning)
    lead_times = []
    for gt in ground_truth:
        caught_h = [r["horizon_months"] for r in rows
                    if r["startup"] == gt["startup"] and r["caught"]]
        lead_times.append(max(caught_h) if caught_h else None)

    return {
        "per_horizon":             per_horizon,
        "lead_times":              lead_times,
        "credits_spent":           credits_spent,
        "rows":                    rows,
        "detail":                  detail,
    }


# ── __main__ ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    from backtest.ground_truth import GROUND_TRUTH
    from ingestion.snapshot import init_db, save_backtest_results, load_backtest_results

    conn = init_db()
    cached = load_backtest_results(conn)
    if cached is not None:
        logger.info("Loaded from DuckDB cache — no API calls")
        print(json.dumps({k: v for k, v in cached.items() if k != "rows"},
                         indent=2, default=str))
        conn.close()
        sys.exit(0)

    logger.info("No cache — running founder-anchored backtest (search path)")
    results = evaluate(GROUND_TRUTH)

    save_backtest_results(conn, results["rows"])
    conn.close()
    logger.info("Saved to DuckDB — subsequent runs use cache")

    # ── formatted report ──────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("BACKTEST RESULTS — founder-anchored, search path")
    print("="*60)

    gt_by_name = {gt["startup"]: gt for gt in GROUND_TRUTH}
    co_tenure_entries = [gt["startup"] for gt in GROUND_TRUTH if gt.get("co_tenure_expected")]
    strong_only       = [gt["startup"] for gt in GROUND_TRUTH if not gt.get("co_tenure_expected")]

    for group_label, names in [
        ("CO-TENURE + CONVERGENCE path (Character.AI, Sakana AI)", co_tenure_entries),
        ("STRONG-CLUSTER / CONVERGENCE ONLY path (Sierra AI — co_tenure=0 expected)", strong_only),
    ]:
        print(f"\n── {group_label} ──")
        group_detail = [d for d in results["detail"] if d["startup"] in names]
        for startup in names:
            print(f"\n  {startup}:")
            for d in sorted(group_detail, key=lambda x: x["horizon"]):
                if d["startup"] != startup:
                    continue
                caught_str = "✅ CAUGHT" if d["caught"] else "❌ missed"
                print(f"    T-{d['horizon']:>2}mo: {caught_str}  "
                      f"score={d['score']:.1f}  clusters={d['clusters']}  {d['note']}")

        # Recall for this group
        caught_count = sum(1 for gt in GROUND_TRUTH if gt["startup"] in names
                           for n in (3, 6, 9)
                           for r in results["rows"]
                           if r["startup"] == gt["startup"]
                              and r["horizon_months"] == n and r["caught"])
        total_slots = len(names) * 3
        print(f"\n  recall (slots caught / total): {caught_count}/{total_slots}")

        # Lead times for this group
        lts = [lt for gt, lt in zip(GROUND_TRUTH, results["lead_times"])
               if gt["startup"] in names and lt is not None]
        if lts:
            print(f"  lead times: {sorted(lts)} months — median {sorted(lts)[len(lts)//2]}")
        else:
            print("  lead times: none caught")

    print(f"\n── Budget ──")
    print(f"  Credits spent: ${results['credits_spent']:.2f}")
    print(f"  Under $20 ceiling: {'✅' if results['credits_spent'] < 20 else '❌'}")
