import duckdb
import json
import hashlib
import os
from datetime import date

from config import DUCKDB_PATH


def init_db(path=DUCKDB_PATH) -> duckdb.DuckDBPyConnection:
    dir_part = os.path.dirname(path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    conn = duckdb.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leavers (
            run_date DATE,
            anchor VARCHAR,
            profile_url VARCHAR,
            name VARCHAR,
            headline VARCHAR,
            current_company_id INTEGER,
            current_company_name VARCHAR,
            current_title VARCHAR,
            current_start_date DATE,
            anchor_end_date DATE,
            stealth BOOLEAN,
            founder BOOLEAN,
            tiny_dest BOOLEAN,
            open_to_career BOOLEAN,
            PRIMARY KEY (run_date, anchor, profile_url)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clusters (
            run_date DATE,
            anchor VARCHAR,
            cluster_id VARCHAR,
            member_urls VARCHAR,
            kind VARCHAR,
            score DOUBLE,
            tier VARCHAR,
            features VARCHAR,
            adjudication VARCHAR,
            dossier VARCHAR,
            PRIMARY KEY (run_date, anchor, cluster_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flow_edges (
            run_date DATE,
            anchor VARCHAR,
            target_id INTEGER,
            target VARCHAR,
            weight INTEGER,
            PRIMARY KEY (run_date, anchor, target_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest (
            startup VARCHAR,
            announce_date DATE,
            horizon_months INTEGER,
            caught BOOLEAN,
            score_at_horizon DOUBLE,
            PRIMARY KEY (startup, horizon_months)
        )
    """)
    return conn


def save_leavers(conn, run_date: date, anchor: str, leavers: list, tags: dict) -> None:
    for p in leavers:
        t = tags.get(p.profile_url, {})
        cur = p.current_role
        anchor_role_obj = None
        for r in p.roles:
            if r.end_date is not None and r.company_name and anchor.lower() in (r.company_name or "").lower():
                anchor_role_obj = r
                break

        conn.execute(
            """
            INSERT OR REPLACE INTO leavers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_date,
                anchor,
                p.profile_url,
                p.name,
                p.headline,
                cur.company_id if cur else 0,
                cur.company_name if cur else None,
                cur.title if cur else None,
                cur.start_date if cur else None,
                anchor_role_obj.end_date if anchor_role_obj else None,
                t.get("stealth", False),
                t.get("founder", False),
                t.get("tiny_destination", False),
                t.get("open_to_career", False),
            ],
        )


def save_clusters(conn, run_date: date, anchor: str, ranked: list) -> None:
    for item in ranked:
        cluster, score, tier, features, adjudication, dossier = item
        sorted_urls = sorted(p.profile_url for p in cluster)
        cluster_id = hashlib.md5(json.dumps(sorted_urls).encode()).hexdigest()
        member_urls = json.dumps(sorted_urls)
        features_json = json.dumps(features)
        adjudication_json = json.dumps(adjudication)
        dossier_json = json.dumps(dossier) if dossier is not None else None

        conn.execute(
            """
            INSERT OR REPLACE INTO clusters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_date,
                anchor,
                cluster_id,
                member_urls,
                None,   # kind not passed in ranked tuple; caller may extend
                score,
                tier,
                features_json,
                adjudication_json,
                dossier_json,
            ],
        )


def save_flow_edges(conn, run_date: date, anchor: str, edges: list) -> None:
    for edge in edges:
        conn.execute(
            """
            INSERT OR REPLACE INTO flow_edges VALUES (?, ?, ?, ?, ?)
            """,
            [
                run_date,
                anchor,
                edge["target_id"],
                edge["target"],
                edge["weight"],
            ],
        )


def diff_clusters(conn, anchor: str) -> dict:
    rows = conn.execute(
        """
        SELECT DISTINCT run_date FROM clusters
        WHERE anchor = ?
        ORDER BY run_date DESC
        LIMIT 2
        """,
        [anchor],
    ).fetchall()

    if len(rows) < 2:
        return {"new": [], "strengthening": []}

    latest_date = rows[0][0]
    prev_date = rows[1][0]

    latest = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT cluster_id, score FROM clusters WHERE anchor = ? AND run_date = ?",
            [anchor, latest_date],
        ).fetchall()
    }
    prev = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT cluster_id, score FROM clusters WHERE anchor = ? AND run_date = ?",
            [anchor, prev_date],
        ).fetchall()
    }

    new = [cid for cid in latest if cid not in prev]
    strengthening = [
        cid for cid in latest
        if cid in prev and latest[cid] > prev[cid] + 5
    ]

    return {"new": new, "strengthening": strengthening}


def save_backtest_results(conn, rows: list) -> None:
    """Persist the flat rows list returned by evaluate() into the backtest table."""
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO backtest VALUES (?, ?, ?, ?, ?)",
            [r["startup"], r["announce_date"], r["horizon_months"],
             r["caught"], r["score_at_horizon"]],
        )


def load_backtest_results(conn, horizons=(3, 6, 9)) -> dict | None:
    """Reconstruct evaluate()-style results from the backtest table.
    Returns None if the table is empty (backtest not yet run).
    """
    rows = conn.execute(
        "SELECT startup, announce_date, horizon_months, caught, score_at_horizon "
        "FROM backtest ORDER BY startup, horizon_months"
    ).fetchall()

    if not rows:
        return None

    per_horizon = {n: {"recall": 0.0, "caught": 0, "total": 0} for n in horizons}
    # track per-startup earliest caught horizon for lead_times
    startup_horizons: dict[str, list] = {}
    for startup, announce_date, n, caught, score in rows:
        if n not in per_horizon:
            continue
        per_horizon[n]["total"] += 1
        if caught:
            per_horizon[n]["caught"] += 1
        startup_horizons.setdefault(startup, [])
        if caught:
            startup_horizons[startup].append(n)

    for n in horizons:
        total = per_horizon[n]["total"]
        per_horizon[n]["recall"] = per_horizon[n]["caught"] / total if total else 0.0

    # lead_time = smallest horizon at which the startup was caught (most lead time)
    startups = sorted({r[0] for r in rows})
    lead_times = [min(startup_horizons[s]) if startup_horizons.get(s) else None for s in startups]

    flat_rows = [
        {"startup": r[0], "announce_date": str(r[1]), "horizon_months": r[2],
         "caught": r[3], "score_at_horizon": r[4]}
        for r in rows
    ]
    return {"per_horizon": per_horizon, "lead_times": lead_times, "rows": flat_rows}
