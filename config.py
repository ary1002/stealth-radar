import os
from dotenv import load_dotenv
load_dotenv()

# Optional at startup — keys must be provided per-request when deploying without env vars.
CRUSTDATA_API_KEY = os.environ.get("CRUSTDATA_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CRUSTDATA_BASE = "https://api.crustdata.com"
API_VERSION    = "2025-11-01"
CLAUDE_MODEL   = "claude-sonnet-4-6"

# detection thresholds
LEAVER_LOOKBACK_MONTHS         = 18
TINY_DESTINATION_MAX_HEADCOUNT = 25
MEDIUM_CLUSTER_WINDOW_MONTHS   = 4
MIN_CLUSTER_SIZE               = 2
MEDIUM_MIN_CO_TENURE_MONTHS    = 6  # medium cluster requires this much co-tenure OR same destination

DUCKDB_PATH    = "data/radar.duckdb"
RATE_LIMIT_RPS = 8

# Strong-cluster eligibility gate.
# A shared destination is only eligible for strong-cluster formation if its
# headcount is BELOW this threshold.  Above it, co-arrival is explained by
# normal independent hiring at a large employer (e.g. Google 342k, Stripe 15k).
# 500 ≈ Series-B boundary; below it convergence is still a meaningful signal.
STRONG_CLUSTER_MAX_HEADCOUNT = 500

# ── Credit-safe query limits ──────────────────────────────────────────────────
# /person/search costs 0.03 credits per result. Never exceed these in demo/test.
DEMO_PAGE_LIMIT     = 50   # main.py / UI: max results per search call
BACKTEST_PAGE_LIMIT = 30   # backtest: max results per search call
