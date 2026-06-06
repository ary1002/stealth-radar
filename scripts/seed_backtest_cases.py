# Historical backtest seeds — not live detections. These are v1 validation cases
# inserted for scoreboard demo purposes.

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.ledger import init_predictions_db, update_prediction_status
from models.schemas import (
    AnchorStrategy,
    CompanyGate,
    EvidenceBundle,
    EvidenceItem,
    ScoringWeights,
    ThesisConfig,
)


def _make_thesis(thesis_id: str, anchor: str) -> ThesisConfig:
    return ThesisConfig(
        thesis_id=thesis_id,
        label=f"Google alumni → {anchor}",
        anchor_strategy=AnchorStrategy(mode="explicit", companies=["Google"]),
        person_filters={},
        company_gate=CompanyGate(max_headcount=500),
        scoring_weights=ScoringWeights(),
    )


def _make_evidence(finding: str, supports: float) -> EvidenceBundle:
    return EvidenceBundle(
        items=[
            EvidenceItem(
                source="backtest_seed",
                finding=finding,
                supports=supports,
                confidence=0.9,
                credits_spent=0.0,
            )
        ],
        total_credits=0.0,
        early_exit_reason=None,
    )


def seed(conn) -> None:
    import hashlib
    from datetime import timezone
    from uuid import uuid4

    cases = [
        # Character.AI — caught 9 months before announcement
        {
            "prediction_id": "pred_characterai_001",
            "thesis_id": "thesis_google_alumni_stealth",
            "cluster_id": "clus_characterai_001",
            "anchor_company": "Google",
            "members": json.dumps([
                {"name": "Noam Shazeer", "profile_url": "https://www.linkedin.com/in/noam-shazeer", "headline": "Co-founder at Character.AI"},
                {"name": "Daniel de Freitas", "profile_url": "https://www.linkedin.com/in/daniel-de-freitas-abrahams", "headline": "Co-founder at Character.AI"},
            ]),
            "destination_name": "Character.AI",
            "destination_company_id": None,
            "score": 73.9,
            "tier": "Medium",
            "claude_verdict": "forming_team",
            "evidence_bundle": json.dumps({
                "items": [{"source": "backtest_seed", "finding": "Noam Shazeer + Daniel de Freitas both left Google and joined stealth co", "supports": 0.9, "confidence": 0.9, "credits_spent": 0.0}],
                "total_credits": 0.0,
                "early_exit_reason": None,
            }),
            "predicted_event": "funding_round_6mo",
            "status": "confirmed",
            "confirmed_at": "2023-03-01T00:00:00+00:00",
            "lead_time_days": 270,
        },
        # Sierra AI — gate worked correctly (score=0, correctly gated)
        {
            "prediction_id": "pred_sierraai_001",
            "thesis_id": "thesis_google_alumni_stealth",
            "cluster_id": "clus_sierraai_001",
            "anchor_company": "Google",
            "members": json.dumps([
                {"name": "Bret Taylor", "profile_url": "https://www.linkedin.com/in/brettaylor", "headline": "Co-founder at Sierra"},
                {"name": "Clay Bavor", "profile_url": "https://www.linkedin.com/in/claybavor", "headline": "Co-founder at Sierra"},
            ]),
            "destination_name": "Sierra AI",
            "destination_company_id": None,
            "score": 0.0,
            "tier": "Low",
            "claude_verdict": "coincidental",
            "evidence_bundle": json.dumps({
                "items": [{"source": "backtest_seed", "finding": "Destination headcount exceeded gate threshold — strong cluster gate triggered correctly", "supports": -0.5, "confidence": 0.85, "credits_spent": 0.0}],
                "total_credits": 0.0,
                "early_exit_reason": "headcount_gate",
            }),
            "predicted_event": None,
            "status": "confirmed",
            "confirmed_at": "2023-09-01T00:00:00+00:00",
            "lead_time_days": 180,
        },
        # Sakana AI — method boundary, status expired
        {
            "prediction_id": "pred_sakanaai_001",
            "thesis_id": "thesis_google_alumni_stealth",
            "cluster_id": "clus_sakanaai_001",
            "anchor_company": "Google",
            "members": json.dumps([
                {"name": "David Ha", "profile_url": "https://www.linkedin.com/in/david-ha-b3119a2", "headline": "Co-founder at Sakana AI"},
                {"name": "Llion Jones", "profile_url": "https://www.linkedin.com/in/llionjones", "headline": "Co-founder at Sakana AI"},
            ]),
            "destination_name": "Sakana AI",
            "destination_company_id": None,
            "score": 0.0,
            "tier": "Watch",
            "claude_verdict": "unclear",
            "evidence_bundle": json.dumps({
                "items": [{"source": "backtest_seed", "finding": "David Ha left Google (Japan office); Llion Jones left separately — departure window exceeded medium cluster limit", "supports": 0.2, "confidence": 0.5, "credits_spent": 0.0}],
                "total_credits": 0.0,
                "early_exit_reason": "method_boundary",
            }),
            "predicted_event": None,
            "status": "expired",
            "confirmed_at": "2023-08-01T00:00:00+00:00",
            "lead_time_days": None,
        },
    ]

    for case in cases:
        # Check if already inserted (idempotent)
        exists = conn.execute(
            "SELECT 1 FROM predictions WHERE prediction_id = ?",
            [case["prediction_id"]],
        ).fetchone()
        if exists:
            print(f"  skip (already exists): {case['prediction_id']}")
            continue

        # Compute chain hash
        prev_row = conn.execute(
            "SELECT row_hash FROM predictions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev_row[0] if prev_row else ""

        row_content = {k: v for k, v in case.items()
                       if k not in ("status", "confirmed_at", "lead_time_days")}
        row_hash = hashlib.sha256(
            json.dumps(row_content, sort_keys=True, default=str).encode()
            + prev_hash.encode()
        ).hexdigest()

        conn.execute(
            """
            INSERT INTO predictions (
                prediction_id, thesis_id, cluster_id, created_at,
                anchor_company, members, destination_name, destination_company_id,
                score, tier, claude_verdict, evidence_bundle,
                predicted_event, status, confirmed_at, lead_time_days,
                row_hash, prev_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                case["prediction_id"],
                case["thesis_id"],
                case["cluster_id"],
                "2024-01-01T00:00:00+00:00",  # seed insert time
                case["anchor_company"],
                case["members"],
                case["destination_name"],
                case["destination_company_id"],
                case["score"],
                case["tier"],
                case["claude_verdict"],
                case["evidence_bundle"],
                case["predicted_event"],
                case["status"],
                case["confirmed_at"],
                case["lead_time_days"],
                row_hash,
                prev_hash,
            ],
        )
        print(f"  inserted: {case['prediction_id']} ({case['destination_name']}, status={case['status']})")

    print("Seed complete.")


if __name__ == "__main__":
    from config import DUCKDB_PATH

    # Also ensure v1 tables exist so the same file works for both v1 and v2
    from ingestion.snapshot import init_db
    conn = init_db(DUCKDB_PATH)
    conn.close()

    conn = init_predictions_db(DUCKDB_PATH)
    print(f"Seeding backtest cases into {DUCKDB_PATH}...")
    seed(conn)
    conn.close()
