"""
db/ledger.py — append-only predictions ledger for Stealth Radar v2.
Uses the same DuckDB file as v1 (data/radar.duckdb), different table.
No ORM — raw conn.execute with list params, mirroring ingestion/snapshot.py style.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

import duckdb

from models.schemas import EvidenceBundle, ThesisConfig
from config import DUCKDB_PATH as _DEFAULT_DUCKDB_PATH


_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def _canonical_json(row_content: dict) -> str:
    """Return stable JSON string for hashing — sort_keys, str fallback for non-serialisable types."""
    return json.dumps(row_content, sort_keys=True, default=str)


def init_predictions_db(path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Open DuckDB (same file as v1) and ensure predictions table exists."""
    if path is None:
        path = _DEFAULT_DUCKDB_PATH
    dir_part = os.path.dirname(path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    conn = duckdb.connect(path)
    schema_sql = open(_SCHEMA_PATH).read()
    conn.execute(schema_sql)
    # Migration: add conviction_score if not present (existing DBs)
    cols = {row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='predictions'"
    ).fetchall()}
    if "conviction_score" not in cols:
        conn.execute("ALTER TABLE predictions ADD COLUMN conviction_score DOUBLE")
    # One-time seed import: if the table is empty and a seed file exists, load it.
    # This runs on first deploy against a fresh volume; skipped on every subsequent start.
    count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    if count == 0:
        from config import DATA_DIR as _DATA_DIR
        seed_path = os.path.join(_DATA_DIR, "seed_predictions.json")
        if os.path.exists(seed_path):
            with open(seed_path) as f:
                seed_rows = json.load(f)
            for row in seed_rows:
                cols = ", ".join(row.keys())
                placeholders = ", ".join("?" * len(row))
                conn.execute(
                    f"INSERT INTO predictions ({cols}) VALUES ({placeholders})"
                    " ON CONFLICT (prediction_id) DO NOTHING",
                    list(row.values()),
                )
            print(f"Seeded {len(seed_rows)} predictions from {seed_path}")
    return conn


def insert_prediction(
    conn,
    cluster: dict,
    evidence_bundle: EvidenceBundle,
    verdict: str,
    tier: str,
    thesis: ThesisConfig,
) -> str:
    """
    Insert a prediction row into DuckDB and return the generated prediction_id.
    """
    # Dedup on (thesis_id, destination_name, sorted member profile_urls).
    # cluster_id is often empty; fingerprint the content and store it in cluster_id.
    member_keys = sorted(
        m.get("profile_url") or m.get("name", "") for m in cluster.get("members", [])
    )
    content_fp = hashlib.sha256(
        json.dumps({
            "thesis_id":        thesis.thesis_id,
            "destination_name": cluster.get("destination_name") or "",
            "members":          member_keys,
        }, sort_keys=True).encode()
    ).hexdigest()[:24]

    existing = conn.execute(
        "SELECT prediction_id FROM predictions WHERE cluster_id = ? LIMIT 1",
        [content_fp],
    ).fetchone()
    if existing:
        return existing[0]   # idempotent — already recorded

    prediction_id = f"pred_{uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()

    members_json = json.dumps(cluster.get("members", []), default=str)
    evidence_json = json.dumps(
        {
            "items": [
                {
                    "source": item.source,
                    "finding": item.finding,
                    "supports": item.supports,
                    "confidence": item.confidence,
                    "credits_spent": item.credits_spent,
                }
                for item in evidence_bundle.items
            ],
            "total_credits": evidence_bundle.total_credits,
            "early_exit_reason": evidence_bundle.early_exit_reason,
        },
        default=str,
    )

    # Fetch the most recent row_hash to chain to
    prev_row = conn.execute(
        "SELECT row_hash FROM predictions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    prev_hash = prev_row[0] if prev_row else ""

    # Build the canonical content (all prediction fields except hashes)
    row_content = {
        "prediction_id": prediction_id,
        "thesis_id": thesis.thesis_id,
        "cluster_id": cluster.get("cluster_id", ""),
        "created_at": created_at,
        "anchor_company": cluster.get("anchor_company") or cluster.get("anchor", ""),
        "members": cluster.get("members", []),
        "destination_name": cluster.get("destination_name"),
        "destination_company_id": cluster.get("destination_company_id"),
        "score": float(cluster.get("score", 0)),
        "tier": tier,
        "claude_verdict": verdict,
        "evidence_bundle": evidence_bundle.items,  # serialised below
        "predicted_event": cluster.get("predicted_event"),
        "status": "open",
    }
    row_hash = hashlib.sha256(
        _canonical_json(row_content).encode() + prev_hash.encode()
    ).hexdigest()

    conn.execute(
        """
        INSERT INTO predictions (
            prediction_id, thesis_id, cluster_id, created_at,
            anchor_company, members, destination_name, destination_company_id,
            score, tier, claude_verdict, evidence_bundle,
            predicted_event, conviction_score, status, confirmed_at, lead_time_days,
            row_hash, prev_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            prediction_id,
            thesis.thesis_id,
            content_fp,   # content fingerprint as dedup key
            created_at,
            cluster.get("anchor_company") or cluster.get("anchor", ""),
            members_json,
            cluster.get("destination_name"),
            cluster.get("destination_company_id"),
            float(cluster.get("score", 0)),
            tier,
            verdict,
            evidence_json,
            cluster.get("predicted_event"),
            cluster.get("conviction_score"),
            "open",
            None,
            None,
            row_hash,
            prev_hash,
        ],
    )

    return prediction_id


def get_metrics(conn) -> dict:
    """Return summary metrics for the predictions ledger."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'open')      AS total_open,
            COUNT(*) FILTER (WHERE status = 'confirmed') AS total_confirmed,
            COUNT(*) FILTER (WHERE status = 'expired')   AS total_expired
        FROM predictions
        """
    ).fetchone()

    total_open, total_confirmed, total_expired = row if row else (0, 0, 0)
    total_resolved = total_confirmed + total_expired
    hit_rate = (total_confirmed / total_resolved) if total_resolved > 0 else 0.0

    tier_rows = conn.execute(
        """
        SELECT tier, COUNT(*) FROM predictions GROUP BY tier
        """
    ).fetchall()
    tier_breakdown = {"High": 0, "Medium": 0, "Low": 0, "Watch": 0}
    for tier, count in tier_rows:
        if tier in tier_breakdown:
            tier_breakdown[tier] = count

    return {
        "total_open": total_open,
        "total_confirmed": total_confirmed,
        "total_expired": total_expired,
        "hit_rate": round(hit_rate, 4),
        "tier_breakdown": tier_breakdown,
    }


def get_feed(conn, status_filter: str | None = None) -> list[dict]:
    """Return predictions newest-first, optionally filtered by status."""
    if status_filter:
        rows = conn.execute(
            """
            SELECT prediction_id, thesis_id, cluster_id, created_at,
                   anchor_company, members, destination_name, destination_company_id,
                   score, tier, claude_verdict, evidence_bundle,
                   predicted_event, status, confirmed_at, lead_time_days,
                   row_hash, prev_hash
            FROM predictions
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            [status_filter],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT prediction_id, thesis_id, cluster_id, created_at,
                   anchor_company, members, destination_name, destination_company_id,
                   score, tier, claude_verdict, evidence_bundle,
                   predicted_event, status, confirmed_at, lead_time_days,
                   row_hash, prev_hash
            FROM predictions
            ORDER BY created_at DESC
            """
        ).fetchall()

    columns = [
        "prediction_id", "thesis_id", "cluster_id", "created_at",
        "anchor_company", "members", "destination_name", "destination_company_id",
        "score", "tier", "claude_verdict", "evidence_bundle",
        "predicted_event", "status", "confirmed_at", "lead_time_days",
        "row_hash", "prev_hash",
    ]
    result = []
    for row in rows:
        d = dict(zip(columns, row))
        # Parse JSON columns
        if isinstance(d["members"], str):
            try:
                d["members"] = json.loads(d["members"])
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(d["evidence_bundle"], str):
            try:
                d["evidence_bundle"] = json.loads(d["evidence_bundle"])
            except (json.JSONDecodeError, TypeError):
                pass
        # Normalise datetimes to strings for JSON serialisation
        if d["created_at"] is not None:
            d["created_at"] = str(d["created_at"])
        if d["confirmed_at"] is not None:
            d["confirmed_at"] = str(d["confirmed_at"])
        result.append(d)
    return result


def update_prediction_status(
    conn,
    prediction_id: str,
    status: str,
    confirmed_at=None,
    lead_time_days: int | None = None,
) -> None:
    """Manually confirm or expire a prediction."""
    conn.execute(
        """
        UPDATE predictions
        SET status = ?, confirmed_at = ?, lead_time_days = ?
        WHERE prediction_id = ?
        """,
        [status, confirmed_at, lead_time_days, prediction_id],
    )
