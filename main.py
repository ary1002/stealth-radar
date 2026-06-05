import asyncio
import time
from ingestion.client import CrustdataClient

_FOUNDER_TERMS = {"founder", "co-founder", "cofounder", "founding"}


def _is_explicit_founding_team(cluster) -> bool:
    """Return True if ≥50% of cluster members have an explicit founder/co-founder
    title at their current destination.  When true, the headcount gate is bypassed —
    Crustdata's indexed headcount for the company_id is stale or misattributed,
    and the self-reported founding role is the authoritative signal.
    """
    if not cluster:
        return False
    founders = sum(
        1 for p in cluster
        if p.current_role and p.current_role.title
        and any(ft in p.current_role.title.lower() for ft in _FOUNDER_TERMS)
    )
    return founders >= len(cluster) / 2
from ingestion.cohort import pull_cohort, COHORT_FIELDS, SORTS
from detect.parse import parse_person
from detect.leavers import is_leaver, anchor_role
from detect.signals import tag
from detect.cluster import strong_clusters, medium_clusters, postprocess_clusters
from score.model import cluster_features, score_clusters, tier
from claude.adjudicate import adjudicate
from claude.dossier import dossier
from config import DEMO_PAGE_LIMIT, STRONG_CLUSTER_MAX_HEADCOUNT


async def run(anchor_name=None, anchor_linkedin_url=None,
              crustdata_key: str | None = None,
              anthropic_key: str | None = None):
    client = CrustdataClient(api_key=crustdata_key)
    try:
        anchor_filt = (
            {"field": "experience.employment_details.past.company_professional_network_profile_url",
             "type": "=", "value": anchor_linkedin_url}
            if anchor_linkedin_url else
            {"field": "experience.employment_details.past.company_name",
             "type": "=", "value": anchor_name}
        )
        filt = {
            "op": "and",
            "conditions": [
                anchor_filt,
                {"field": "recently_changed_jobs", "type": "=", "value": True},
            ],
        }
        raw = []
        async for page in client.person_search(
            filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=DEMO_PAGE_LIMIT
        ):
            raw.extend(page)
            break  # single page — no pagination in demo mode
    finally:
        await client.close()

    people = [parse_person(r) for r in raw]
    leavers = [p for p in people if is_leaver(p, anchor_name=anchor_name)]
    tags = {p.profile_url: tag(p) for p in leavers}

    strong_cluster_pairs = strong_clusters(leavers)
    strong_cluster_groups = [g for _, g in strong_cluster_pairs]
    medium_cluster_groups = medium_clusters(leavers, tags, anchor_name=anchor_name)
    clusters = postprocess_clusters(
        strong_cluster_groups + medium_cluster_groups,
        anchor_name=anchor_name,
    )

    feats = [cluster_features(c, tags, anchor_name=anchor_name) for c in clusters]
    scores = score_clusters(feats)

    results = []
    for cluster, score, feat in zip(clusters, scores, feats):
        tier_label = tier(float(score))
        dest_ids = {p.current_role.company_id for p in cluster if p.current_role and p.current_role.company_id}
        kind = "strong" if len(dest_ids) == 1 else "medium"

        # Demotion: large-destination strong clusters cannot be High or forming_team.
        # They are VISIBLE but capped so they don't masquerade as stealth signals.
        if kind == "strong":
            dest_hcs = [p.current_role.headcount_latest
                        for p in cluster
                        if p.current_role and p.current_role.headcount_latest is not None]
            if (dest_hcs and max(dest_hcs) >= STRONG_CLUSTER_MAX_HEADCOUNT
                    and not _is_explicit_founding_team(cluster)):
                if tier_label == "High":
                    tier_label = "Medium"
                is_large_dest = True
            else:
                is_large_dest = False
        else:
            is_large_dest = False

        def _tenure_months(p):
            ar = anchor_role(p, anchor_name=anchor_name)
            if not ar or not ar.start_date or not ar.end_date:
                return 0.0
            delta = ar.end_date - ar.start_date
            return round(delta.days / 30.44, 1)

        cluster_summary = {
            "anchor": anchor_name or anchor_linkedin_url,
            "kind": kind,
            "score": float(score),
            "tier": tier_label,
            "features": feat,
            "members": [
                {
                    "name": p.name,
                    "headline": p.headline,
                    "current_title": p.current_role.title if p.current_role else None,
                    "current_company": p.current_role.company_name if p.current_role else None,
                    "current_start_date": str(p.current_role.start_date) if p.current_role and p.current_role.start_date else None,
                    "anchor_tenure_months": _tenure_months(p),
                }
                for p in cluster
            ],
            "destination_convergence": feat["shared_destination"],
        }

        adj = adjudicate(cluster_summary, anthropic_key=anthropic_key)
        if is_large_dest and adj.get("label") == "forming_team":
            adj = {**adj, "label": "coincidental",
                   "rationale": f"[gated: large employer] {adj.get('rationale', '')}"}
        dos = dossier(cluster_summary, anthropic_key=anthropic_key) if tier_label in ("High", "Medium") else None

        results.append((cluster, float(score), tier_label, feat, adj, dos))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


async def run_with_queue(anchor_name=None, anchor_linkedin_url=None, event_queue=None,
                         crustdata_key: str | None = None,
                         anthropic_key: str | None = None):
    """Same as run() but emits SSE events to event_queue at each pipeline stage."""

    async def emit(event: dict):
        if event_queue is not None:
            await event_queue.put(event)
            # Yield to the event loop so the SSE generator can flush this
            # event over the network before the next one is queued.
            await asyncio.sleep(0)

    total_credits = 0.0
    total_rows = 0

    # ── Stage: cohort_pull ────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "cohort_pull", "status": "start"})

    client = CrustdataClient(api_key=crustdata_key)
    try:
        anchor_filt = (
            {"field": "experience.employment_details.past.company_professional_network_profile_url",
             "type": "=", "value": anchor_linkedin_url}
            if anchor_linkedin_url else
            {"field": "experience.employment_details.past.company_name",
             "type": "=", "value": anchor_name}
        )
        filt = {
            "op": "and",
            "conditions": [
                anchor_filt,
                {"field": "recently_changed_jobs", "type": "=", "value": True},
            ],
        }

        filter_summary = (
            f"past_employers.company_linkedin_profile_url = {anchor_linkedin_url}"
            if anchor_linkedin_url
            else f"past_employers.name = {anchor_name}"
        )

        t0 = time.time()
        raw = []
        async for page in client.person_search(
            filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=DEMO_PAGE_LIMIT
        ):
            raw.extend(page)
            break  # single page — no pagination in demo mode

        latency_ms = round((time.time() - t0) * 1000)
        rows = len(raw)
        credits = round(max(3.0, rows * 0.03), 2)
        total_credits += credits
        total_rows += rows

        await emit({
            "type": "api_call",
            "endpoint": "POST /person/search",
            "filter_summary": filter_summary,
            "rows": rows,
            "credits": credits,
            "latency_ms": latency_ms,
        })
        await emit({"type": "credits", "total_credits": round(total_credits, 2), "total_rows": total_rows})
    finally:
        await client.close()

    await emit({"type": "stage", "stage": "cohort_pull", "status": "done", "count": len(raw)})

    # ── Stage: parse ──────────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "parse", "status": "start"})
    people = [parse_person(r) for r in raw]
    await emit({"type": "stage", "stage": "parse", "status": "done", "count": len(people)})

    # ── Stage: leavers ────────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "leavers", "status": "start"})
    leavers = [p for p in people if is_leaver(p, anchor_name=anchor_name)]
    tags = {p.profile_url: tag(p) for p in leavers}
    await emit({"type": "stage", "stage": "leavers", "status": "done", "count": len(leavers)})

    # ── Stage: signals ────────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "signals", "status": "start"})
    # tags already computed above
    await emit({"type": "stage", "stage": "signals", "status": "done", "count": len(tags)})

    # ── Stage: clustering ─────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "clustering", "status": "start"})
    strong_cluster_pairs = strong_clusters(leavers)
    strong_cluster_groups = [g for _, g in strong_cluster_pairs]
    medium_cluster_groups = medium_clusters(leavers, tags, anchor_name=anchor_name)
    clusters = postprocess_clusters(
        strong_cluster_groups + medium_cluster_groups,
        anchor_name=anchor_name,
    )
    await emit({"type": "stage", "stage": "clustering", "status": "done", "count": len(clusters)})

    # ── Stage: scoring ────────────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "scoring", "status": "start"})
    feats = [cluster_features(c, tags, anchor_name=anchor_name) for c in clusters]
    scores = score_clusters(feats)
    await emit({"type": "stage", "stage": "scoring", "status": "done", "count": len(scores)})

    # ── Stage: adjudication ───────────────────────────────────────────────────
    await emit({"type": "stage", "stage": "adjudication", "status": "start"})

    results = []
    for idx, (cluster, score, feat) in enumerate(zip(clusters, scores, feats)):
        tier_label = tier(float(score))
        dest_ids = {p.current_role.company_id for p in cluster if p.current_role and p.current_role.company_id}
        kind = "strong" if len(dest_ids) == 1 else "medium"

        # Demotion: large-destination strong clusters cannot be High or forming_team.
        # They are VISIBLE but capped so they don't masquerade as stealth signals.
        if kind == "strong":
            dest_hcs = [p.current_role.headcount_latest
                        for p in cluster
                        if p.current_role and p.current_role.headcount_latest is not None]
            if (dest_hcs and max(dest_hcs) >= STRONG_CLUSTER_MAX_HEADCOUNT
                    and not _is_explicit_founding_team(cluster)):
                if tier_label == "High":
                    tier_label = "Medium"
                is_large_dest = True
            else:
                is_large_dest = False
        else:
            is_large_dest = False

        def _tenure_months(p, _anchor_name=anchor_name):
            ar = anchor_role(p, anchor_name=_anchor_name)
            if not ar or not ar.start_date or not ar.end_date:
                return 0.0
            delta = ar.end_date - ar.start_date
            return round(delta.days / 30.44, 1)

        members_list = [
            {
                "name": p.name,
                "headline": p.headline,
                "current_title": p.current_role.title if p.current_role else None,
                "current_company": p.current_role.company_name if p.current_role else None,
                "current_start_date": str(p.current_role.start_date) if p.current_role and p.current_role.start_date else None,
                "anchor_tenure_months": _tenure_months(p),
            }
            for p in cluster
        ]

        cluster_summary = {
            "anchor": anchor_name or anchor_linkedin_url,
            "kind": kind,
            "score": float(score),
            "tier": tier_label,
            "features": feat,
            "members": members_list,
            "destination_convergence": feat["shared_destination"],
        }

        adj = adjudicate(cluster_summary, anthropic_key=anthropic_key)
        if is_large_dest and adj.get("label") == "forming_team":
            adj = {**adj, "label": "coincidental",
                   "rationale": f"[gated: large employer] {adj.get('rationale', '')}"}
        dos = dossier(cluster_summary, anthropic_key=anthropic_key) if tier_label in ("High", "Medium") else None

        adj_dict = adj if isinstance(adj, dict) else (adj.__dict__ if hasattr(adj, '__dict__') else {"raw": str(adj)})
        dos_dict = dos if isinstance(dos, dict) else (dos.__dict__ if dos and hasattr(dos, '__dict__') else dos)

        results.append((cluster, float(score), tier_label, feat, adj, dos))

        # Emit cluster event as it is scored
        await emit({
            "type": "cluster",
            "rank": idx + 1,
            "tier": tier_label,
            "score": float(score),
            "members": members_list,
            "adjudication": adj_dict,
            "dossier": dos_dict,
        })

    await emit({"type": "stage", "stage": "adjudication", "status": "done", "count": len(results)})

    results.sort(key=lambda x: x[1], reverse=True)

    await emit({"type": "done", "total_clusters": len(results)})
    return results


if __name__ == "__main__":
    import sys, json
    anchor = sys.argv[1] if len(sys.argv) > 1 else "Stripe"
    results = asyncio.run(run(anchor_name=anchor))
    for cluster, score, t, feat, adj, dos in results[:5]:
        print(f"\n[{t}] score={score:.1f} members={len(cluster)}")
        print("  adj:", adj)
