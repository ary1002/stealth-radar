"""
api/scoreboard.py — FastAPI APIRouter for the v2 predictions scoreboard.
Mounted at prefix /scoreboard in api/server.py.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter

from db.ledger import _canonical_json, get_db, get_feed, get_metrics

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])

# Seeded backtest IDs — excluded from scoreboard (shown in Validation tab only)
_BACKTEST_IDS = {"pred_characterai_001", "pred_sierraai_001", "pred_sakanaai_001"}

def _is_backtest(row: dict) -> bool:
    return row.get("prediction_id", "") in _BACKTEST_IDS


@router.get("/metrics")
def scoreboard_metrics() -> dict:
    """Return hit_rate, tier_breakdown, and totals — backtest seeds excluded."""
    all_rows = get_feed(get_db())
    rows = [r for r in all_rows if not _is_backtest(r)]
    total_open      = sum(1 for r in rows if r["status"] == "open")
    total_confirmed = sum(1 for r in rows if r["status"] == "confirmed")
    total_expired   = sum(1 for r in rows if r["status"] == "expired")
    denom = total_confirmed + total_expired
    hit_rate = total_confirmed / denom if denom else 0.0
    tier_breakdown: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0, "Watch": 0}
    for r in rows:
        t = r.get("tier", "Watch")
        tier_breakdown[t] = tier_breakdown.get(t, 0) + 1
    return {
        "total_open": total_open,
        "total_confirmed": total_confirmed,
        "total_expired": total_expired,
        "hit_rate": hit_rate,
        "tier_breakdown": tier_breakdown,
    }


@router.get("/feed")
def scoreboard_feed() -> list:
    """Return confirmed live predictions (backtest seeds excluded)."""
    return [r for r in get_feed(get_db(), status_filter="confirmed") if not _is_backtest(r)]


@router.get("/verify-chain")
def scoreboard_verify_chain() -> dict:
    """Verify the SHA-256 hash chain across all predictions ordered by rowid."""
    rows = get_db().execute(
        """
        SELECT rowid, prediction_id, thesis_id, cluster_id, created_at,
               anchor_company, members, destination_name, destination_company_id,
               score, tier, claude_verdict, evidence_bundle,
               predicted_event, status, row_hash, prev_hash
        FROM predictions
        ORDER BY rowid ASC
        """
    ).fetchall()

    if not rows:
        return {"valid": True, "count": 0, "earliest": None, "latest": None}

    columns = [
        "rowid", "prediction_id", "thesis_id", "cluster_id", "created_at",
        "anchor_company", "members", "destination_name", "destination_company_id",
        "score", "tier", "claude_verdict", "evidence_bundle",
        "predicted_event", "status", "row_hash", "prev_hash",
    ]

    prev_hash = ""
    earliest = None
    latest = None

    for i, raw in enumerate(rows):
        r = dict(zip(columns, raw))

        members = r["members"]
        if isinstance(members, str):
            try:
                members = json.loads(members)
            except (json.JSONDecodeError, TypeError):
                members = []

        evidence = r["evidence_bundle"]
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except (json.JSONDecodeError, TypeError):
                evidence = {}

        created_at_str = str(r["created_at"]) if r["created_at"] is not None else None

        row_content = {
            "prediction_id": r["prediction_id"],
            "thesis_id": r["thesis_id"],
            "cluster_id": r["cluster_id"] or "",
            "created_at": created_at_str,
            "anchor_company": r["anchor_company"] or "",
            "members": members,
            "destination_name": r["destination_name"],
            "destination_company_id": r["destination_company_id"],
            "score": float(r["score"] or 0),
            "tier": r["tier"],
            "claude_verdict": r["claude_verdict"],
            "evidence_bundle": evidence.get("items", []) if isinstance(evidence, dict) else evidence,
            "predicted_event": r["predicted_event"],
            "status": "open",
        }

        expected_hash = hashlib.sha256(
            _canonical_json(row_content).encode() + prev_hash.encode()
        ).hexdigest()

        if expected_hash != r["row_hash"]:
            return {
                "valid": False,
                "broken_at": r["prediction_id"],
                "reason": f"hash mismatch at row {i + 1}",
            }

        if r["prev_hash"] != prev_hash:
            return {
                "valid": False,
                "broken_at": r["prediction_id"],
                "reason": f"prev_hash mismatch at row {i + 1}",
            }

        prev_hash = r["row_hash"]
        ts = created_at_str
        if ts:
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    return {
        "valid": True,
        "count": len(rows),
        "earliest": earliest,
        "latest": latest,
    }


@router.get("/open")
def scoreboard_open() -> list:
    """Return open live predictions (backtest seeds excluded)."""
    rows = [r for r in get_feed(get_db(), status_filter="open") if not _is_backtest(r)]
    result = []
    for row in rows:
        evidence = row.get("evidence_bundle") or {}
        items = (evidence.get("items", []) if isinstance(evidence, dict) else [])[:2]
        result.append({
            "prediction_id":          row["prediction_id"],
            "anchor_company":         row["anchor_company"],
            "destination_name":       row["destination_name"],
            "destination_company_id": row.get("destination_company_id"),
            "score":                  row["score"],
            "tier":                   row["tier"],
            "claude_verdict":         row["claude_verdict"],
            "created_at":             row["created_at"],
            "evidence_summary":       items,
            "members_count":          len(row.get("members") or []),
            "members":                row.get("members") or [],
            "row_hash":               row.get("row_hash", ""),
        })
    return result
