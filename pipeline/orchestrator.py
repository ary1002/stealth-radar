"""
Stealth Radar v2 — Orchestrator

Wires the three tracks into a single event-driven pipeline:

    NormalisedEvent (from queue)
      → cohort pull + clustering (existing v1 engine)
      → investigate() [Track A]
      → scorer + Claude adjudicator (existing v1)
      → if forming_team AND tier in (High, Medium):
            ledger.insert_prediction() [Track B]

No track imports from another track.
All cross-track types come from models/schemas.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from main import _is_explicit_founding_team
from models.schemas import (
    NormalisedEvent,
    ThesisConfig,
    EvidenceBundle,
)

logger = logging.getLogger(__name__)

# Credit logger — every live call appended here
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_CREDIT_LOG = os.path.join(_LOG_DIR, "credits.log")


def _log_credit(endpoint: str, credits: float, thesis_id: str) -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}  {endpoint}  {credits:.2f}cr  {thesis_id}\n"
    with open(_CREDIT_LOG, "a") as f:
        f.write(line)


async def process_event(
    event: NormalisedEvent,
    thesis: ThesisConfig,
    client,
    *,
    save_predictions: bool = True,
) -> list[dict]:
    """
    Run the full pipeline for one NormalisedEvent.
    Returns list of prediction dicts (may be empty if no forming_team clusters found).
    """
    from ingestion.cohort import COHORT_FIELDS, SORTS
    from detect.parse import parse_person
    from detect.leavers import is_leaver
    from detect.signals import tag
    from detect.cluster import strong_clusters, medium_clusters, postprocess_clusters
    from score.model import cluster_features, score_clusters, tier
    from claude.adjudicate import adjudicate_and_dossier
    from pipeline.investigation import investigate
    from config import DEMO_PAGE_LIMIT, STRONG_CLUSTER_MAX_HEADCOUNT

    # Anchors may be plain strings OR grounded dicts {name, company_id}
    raw_anchors = thesis.anchor_strategy.companies or []
    anchor_ids  = [a["company_id"] for a in raw_anchors if isinstance(a, dict) and a.get("company_id")]
    anchor_names= [a["name"] if isinstance(a, dict) else a for a in raw_anchors]
    anchor_name = anchor_names[0] if anchor_names else None

    # ── 1. Cohort pull ────────────────────────────────────────────────────────
    # Prefer company_id filter (exact) over name match; use first anchor for demo.
    if anchor_ids:
        anchor_cond = {
            "field": "experience.employment_details.past.company_id",
            "type": "=",
            "value": anchor_ids[0],   # one anchor per run keeps credits bounded
        }
    elif anchor_name:
        anchor_cond = {
            "field": "experience.employment_details.past.company_name",
            "type": "=",
            "value": anchor_name,
        }
    else:
        anchor_cond = None

    filt: dict = {"field": "recently_changed_jobs", "type": "=", "value": True}
    if anchor_cond:
        filt = {"op": "and", "conditions": [anchor_cond,
                {"field": "recently_changed_jobs", "type": "=", "value": True}]}
    raw = []
    async for page in client.person_search(
        filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=DEMO_PAGE_LIMIT
    ):
        raw.extend(page)
        break

    rows = len(raw)
    credits = round(rows * 0.03, 2)
    _log_credit("/person/search", credits, thesis.thesis_id)
    logger.info("Cohort pull: %d profiles (%.2f cr)", rows, credits)

    # ── 2. Parse + leaver detection ───────────────────────────────────────────
    people  = [parse_person(r) for r in raw]
    leavers = [p for p in people if is_leaver(p, anchor_name=anchor_name)]
    tags    = {p.profile_url: tag(p) for p in leavers}
    logger.info("Leavers: %d", len(leavers))

    # ── 3. Clustering ─────────────────────────────────────────────────────────
    strong_pairs   = strong_clusters(leavers)
    strong_groups  = [g for _, g in strong_pairs]
    medium_groups  = medium_clusters(leavers, tags, anchor_name=anchor_name)
    clusters       = postprocess_clusters(
        strong_groups + medium_groups,
        anchor_name=anchor_name,
    )
    logger.info("Clusters: %d (%d strong, %d medium)", len(clusters), len(strong_groups), len(medium_groups))

    if not clusters:
        return []

    # ── 4. Score ──────────────────────────────────────────────────────────────
    feats  = [cluster_features(c, tags, anchor_name=anchor_name) for c in clusters]
    scores = score_clusters(feats)

    results = []
    for cluster, score_val, feat in zip(clusters, scores, feats):
        s          = float(score_val)
        tier_label = tier(s)
        # after postprocessing, use shared company_id as the strong indicator
        dest_ids = {p.current_role.company_id for p in cluster if p.current_role and p.current_role.company_id}
        kind = "strong" if len(dest_ids) == 1 else "medium"

        # Display-score demotion only — label override now lives in hypothesis routing.
        from main import _is_explicit_founding_team
        if kind == "strong":
            dest_hcs = [p.current_role.headcount_latest for p in cluster
                        if p.current_role and p.current_role.headcount_latest]
            if (dest_hcs and max(dest_hcs) >= STRONG_CLUSTER_MAX_HEADCOUNT
                    and not _is_explicit_founding_team(cluster)):
                if tier_label == "High":
                    tier_label = "Medium"

        from detect.leavers import anchor_role as _anchor_role

        def _tenure(p):
            ar = _anchor_role(p, anchor_name=anchor_name)
            if not ar or not ar.start_date or not ar.end_date:
                return 0.0
            return round((ar.end_date - ar.start_date).days / 30.44, 1)

        dest_company = cluster[0].current_role.company_name if cluster[0].current_role else None
        dest_id      = cluster[0].current_role.company_id  if cluster[0].current_role else None

        cluster_summary = {
            "anchor":                anchor_name or "",
            "kind":                  kind,
            "score":                 s,
            "tier":                  tier_label,
            "features":              feat,
            "destination_name":      dest_company,
            "destination_company_id": dest_id,
            "members": [
                {
                    "name":               p.name,
                    "profile_url":        p.profile_url,
                    "headline":           p.headline,
                    "current_title":      p.current_role.title if p.current_role else None,
                    "current_company":    p.current_role.company_name if p.current_role else None,
                    "anchor_tenure":      _tenure(p),
                }
                for p in cluster
            ],
            "destination_convergence": feat["shared_destination"],
        }

        # ── 5. Hypothesis detection → routing → adjudication ─────────────────
        from detect.hypotheses import detect_hypotheses, should_skip_adjudication, route_cluster
        hyps = detect_hypotheses(cluster, anchor_name=anchor_name)
        if should_skip_adjudication(hyps):
            adj = {"label": "coincidental", "confidence": 0.8,
                   "rationale": "Skipped adjudication — cross-country with no founding signal."}
            dos = None
        else:
            route = route_cluster(hyps)
            adj, dos_from_adj = adjudicate_and_dossier(
                cluster_summary, hypotheses=hyps, route=route
            )
            dos = dos_from_adj if (tier_label in ("High", "Medium") and route != "hostile") else None

        # ── 5b. Conviction score + tier recomputation ─────────────────────────
        from main import VERDICT_MULTIPLIERS, _conviction_tier
        verdict_label    = adj.get("label", "coincidental")
        multiplier       = VERDICT_MULTIPLIERS.get(verdict_label, 0.50)
        conviction_score = round(s * multiplier, 1)
        tier_label       = _conviction_tier(conviction_score, verdict_label)

        # ── 6. Investigation (Track A) ────────────────────────────────────────
        bundle: EvidenceBundle | None = None
        if adj.get("label") == "forming_team" and tier_label in ("High", "Medium"):
            bundle, inv_verdict = await investigate(cluster_summary, thesis, client)
            _log_credit("investigation", bundle.total_credits, thesis.thesis_id)
            logger.info("Investigation verdict: %s (%.2f cr)", inv_verdict, bundle.total_credits)

        # ── 7. Persist prediction (Track B) ───────────────────────────────────
        prediction_id = None
        should_log = (
            (verdict_label == "forming_team" and tier_label in ("High", "Medium"))
            or (verdict_label == "unclear" and tier_label == "High"
                and _is_explicit_founding_team(cluster))
        )
        if save_predictions and should_log:
            predicted_event = (
                "unverified_high_signal" if verdict_label == "unclear" else None
            )
            cluster_summary["predicted_event"] = predicted_event
            from db.ledger import init_predictions_db, insert_prediction
            conn = init_predictions_db()
            try:
                prediction_id = insert_prediction(
                    conn            = conn,
                    cluster         = cluster_summary,
                    evidence_bundle = bundle or EvidenceBundle(),
                    verdict         = verdict_label,
                    tier            = tier_label,
                    thesis          = thesis,
                )
                logger.info("Prediction saved: %s", prediction_id)
            finally:
                conn.close()

        results.append({
            "cluster_id":     __import__("hashlib").md5(
                ",".join(p.profile_url for p in cluster if p.profile_url).encode()
            ).hexdigest(),
            "score":            s,
            "conviction_score": conviction_score,
            "verdict_multiplier": multiplier,
            "tier":             tier_label,
            "kind":             kind,
            "members":          cluster_summary["members"],
            "features":         feat,
            "adjudication":     adj,
            "dossier":          dos,
            "evidence_bundle":  bundle.__dict__ if bundle else None,
            "prediction_id":    prediction_id,
        })

    results.sort(key=lambda x: x["conviction_score"], reverse=True)
    return results


async def run_from_event(
    event: NormalisedEvent,
    thesis: ThesisConfig,
    client,
) -> list[dict]:
    """Entry point called by trigger layer when a NormalisedEvent arrives."""
    logger.info("Processing event %s (thesis=%s)", event.event_id, thesis.thesis_id)
    return await process_event(event, thesis, client)
