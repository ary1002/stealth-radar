"""
scripts/migrate_remove_git.py — read-only verification of the existing predictions chain.

Verifies the prev_hash linkage chain in predictions.db. Full hash recomputation
is not possible for pre-existing entries because the original hash included
the cluster dict's cluster_id (often "") rather than the DB column value
(content fingerprint). Structural linkage (each row's prev_hash == prior row's
row_hash) is verifiable and meaningful — it detects insertion of forged rows.

Seeded backtest entries are treated as trusted anchors since they were manually
created with hardcoded hashes outside the insert_prediction flow.

Makes no writes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.ledger import init_predictions_db

DUCKDB_PATH = os.environ.get("DUCKDB_PATH", "data/radar.duckdb")

_SEEDED_IDS = {"pred_characterai_001", "pred_sierraai_001", "pred_sakanaai_001"}


def main():
    if not os.path.exists(DUCKDB_PATH):
        print(f"Database not found at {DUCKDB_PATH}")
        sys.exit(1)

    conn = init_predictions_db(DUCKDB_PATH)
    rows = conn.execute(
        """
        SELECT prediction_id, created_at, row_hash, prev_hash
        FROM predictions
        ORDER BY rowid ASC
        """
    ).fetchall()
    conn.close()

    print(f"Found {len(rows)} predictions")

    if not rows:
        print("Chain verification: OK (empty)")
        print("Earliest prediction: —")
        print("Latest prediction:   —")
        return

    seeded = [r for r in rows if r[0] in _SEEDED_IDS]
    real   = [r for r in rows if r[0] not in _SEEDED_IDS]

    if seeded:
        print(f"Skipping {len(seeded)} seeded backtest entries (manually created, hardcoded hashes)")

    # Build a hash lookup from seeded entries so we can accept any of them as prev anchors
    known_hashes = {r[2] for r in seeded}  # set of row_hash values from seeded rows
    known_hashes.add("")  # empty string is the initial anchor

    earliest = None
    latest = None
    prev_hash = None  # will be set from first real entry's declared prev_hash

    for i, (pred_id, created_at, row_hash, stored_prev_hash) in enumerate(real):
        actual_prev = stored_prev_hash or ""

        if i == 0:
            # First real row — its prev_hash must point to a known anchor (seeded or empty)
            if actual_prev not in known_hashes:
                print(f"Chain verification: BROKEN at {pred_id} (row 1) — prev_hash does not point to a known anchor")
                sys.exit(1)
            prev_hash = actual_prev
        else:
            if actual_prev != prev_hash:
                print(f"Chain verification: BROKEN at {pred_id} (pipeline row {i + 1}) — prev_hash mismatch")
                sys.exit(1)

        prev_hash = row_hash

        ts = str(created_at) if created_at is not None else None
        if ts:
            if earliest is None or ts < earliest:
                earliest = ts
            if latest is None or ts > latest:
                latest = ts

    print("Chain verification: OK")
    print(f"Earliest prediction: {earliest or '—'}")
    print(f"Latest prediction:   {latest or '—'}")


if __name__ == "__main__":
    main()
