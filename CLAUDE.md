# Stealth Radar — Agent Instructions

---

## ⚠️ CRITICAL: Crustdata Credit Budget — READ THIS FIRST

**Current balance: ~80 credits. Every wasted call burns real money.**

### Pricing (self-serve, per result)
| Endpoint | Cost |
|---|---|
| `/person/search` | **0.03 credits per result returned** |
| `/company/identify` | **FREE** |
| `/company/enrich` | 2 credits per record |
| `/person/enrich` | 1–7 credits per record (base=1) |

Pagination is free. Only rows cost. A call returning 0 results costs nothing.

### HARD LIMITS — NEVER EXCEED THESE

```
DEMO / MAIN RUN:     limit=50   per person/search call   →  1.5 credits max
BACKTEST:            limit=30   per person/search call   →  0.9 credits max per call
NEVER call /person/enrich or /company/enrich during testing
NEVER paginate more than 2 pages in demo mode
NEVER run main.py on large anchors (Stripe, Google, Meta) without limit=50
```

### Budget allocation (80 credits total)
| Use | Calls | Results | Cost |
|---|---|---|---|
| Backtest (7 entries × 3 horizons) | 21 | 30 each | ~19 credits |
| Demo Radar run (1–2 anchors) | 2 | 50 each | ~3 credits |
| Buffer / debug retries | — | — | ~58 credits |

### Rules every agent MUST follow

1. **Always pass `limit=30` in backtest calls, `limit=50` in demo/main calls.** Never use the default `limit=1000`.
2. **Always add a date filter** (`experience.employment_details.past.end_date => <cutoff>`) to narrow cohorts. Pulling all-time alumni of Stripe (13,000+ people) will bankrupt the budget in one call.
3. **Add `recently_changed_jobs=true` filter** whenever the goal is detecting recent leavers — it dramatically reduces result set.
4. **Company identify is free** — always use it to resolve company_id before filtering by ID.
5. **Never call `person/enrich`** unless explicitly asked and confirmed against the budget.
6. **Never run the backtest or main.py without verifying `limit` is set small** (`<=50`).
7. **Check credits before and after any multi-call operation**: use the `crustdata_credits_check` MCP tool.

### Retry behaviour warning

The tenacity retry in `ingestion/client.py` retries **5 times** on any HTTPStatusError. A single bad request → 5 API calls consumed. If you see retries firing, fix the root cause immediately — do not let it burn 5× credits.

---

## What this project does

Stealth Radar detects forming startup teams by analysing employment graphs. It pulls everyone who has ever left an "anchor" company, clusters those who converge on the same small/stealth destination, scores each cluster, and uses Claude to adjudicate whether the cluster is a genuine founding team or noise.

Two outputs: **Radar** (forming-team clusters per anchor) and **TalentFlow** (where a company's leavers go overall).

---

## Project layout

```
config.py               — env vars, thresholds, paths
main.py                 — orchestration entry point
ingestion/
  client.py             — async httpx client with rate-limiting + retry
  cohort.py             — pull everyone who left the anchor (paginated)
  snapshot.py           — DuckDB save + diff (new/strengthening clusters)
detect/
  model.py              — Person / Role dataclasses
  parse.py              — flat Crustdata JSON → Person objects
  leavers.py            — filter cohort to actual leavers within lookback
  signals.py            — tag each leaver: stealth / founder / tiny_dest / open_to
  cluster.py            — strong clusters (shared company_id) + medium (stealth window)
  flow.py               — TalentFlow edge aggregation
score/
  model.py              — weighted percentile scoring → 0–100, tier label
claude/
  adjudicate.py         — Claude classifies forming_team | layoff_dispersion | coincidental | unclear
  dossier.py            — Claude writes a tight brief for High/Medium clusters
backtest/
  ground_truth.py       — known founding teams with prior employer + announce date
  asof.py               — reconstruct person's employment state at a past date
  evaluate.py           — precision@K, recall@3/6/9 months, lead-time
api/
  server.py             — FastAPI: POST /radar, GET /flow, GET /backtest
ui/
  app.py                — Streamlit: Radar / TalentFlow / Backtest tabs
tests/                  — pytest unit tests
data/                   — radar.duckdb (gitignored)
```

---

## Crustdata API — critical facts

### Endpoint
`POST https://api.crustdata.com/person/search`

Headers:
```
Authorization: Bearer <CRUSTDATA_API_KEY>
x-api-version: 2025-11-01
content-type: application/json
```

### Filter syntax
- Key is **`column`** (NOT `field`)
- Single filter: `{"column": "...", "type": "...", "value": "..."}`
- Combined: `{"op": "and", "conditions": [<filter>, ...]}`

### Operators (exact strings)
| Operator | Meaning |
|---|---|
| `[.]` | substring match |
| `=` | exact match |
| `=>` | greater than or equal |
| `=<` | less than or equal |
| `in` | set membership (value must be array) |

### `fields` param
A **comma-separated string** (NOT a list):
```python
"name,headline,linkedin_profile_url,recently_changed_jobs,open_to_cards,..."
```

### Confirmed working columns

**Filtering:**
- `past_employers.company_linkedin_profile_url` — exact anchor match (preferred)
- `past_employers.name` — substring match fallback
- `past_employers.company_id` — exact integer
- `past_employers.end_date` — date string `"YYYY-MM-DD"` with `=>` / `=<`
- `recently_changed_jobs` — boolean
- `current_employers.company_headcount_latest` — integer with `=<`

**Returnable fields (flat structure):**
```
name, headline, linkedin_profile_url
recently_changed_jobs, open_to_cards, location_country
current_employers.name, .title, .start_date, .company_id
current_employers.company_headcount_latest, .function_category, .seniority_level
past_employers.name, .start_date, .end_date, .company_id
past_employers.company_linkedin_profile_url, .company_headcount_latest
education_background.institute_name
```

### Actual response structure
The API returns **flat arrays** — NOT nested `experience.employment_details.*`:
```python
raw["name"]
raw["headline"]
raw["linkedin_profile_url"]
raw["recently_changed_jobs"]        # bool
raw["open_to_cards"]                # list (often empty)
raw["location_country"]
raw["current_employers"]            # list of dicts
raw["past_employers"]               # list of dicts
raw["education_background"]         # list of dicts
```

`current_employers` entry keys: `name`, `title`, `start_date`, `company_id`, `company_headcount_latest`, `function_category`, `seniority_level`

`past_employers` entry keys: `name`, `start_date`, `end_date`, `company_id`, `company_linkedin_profile_url`, `company_headcount_latest`

### Pagination
```python
cursor = data.get("next_cursor")   # None on last page
# pass as payload["cursor"] on next request
```

### Sorting (stable, required for safe pagination)
```python
[{"column": "current_employers.start_date", "order": "desc"}]
```

Only these columns are sortable: `current_employers.start_date`, `current_employers.company_headcount_latest`, `years_of_experience_raw`, `recently_changed_jobs`, `name`, `person_id`.

### Credits
- Person search: 3 credits per 100 results (min 3 per call)
- Company identify: free
- Company enrich: 1 credit per company
- Person enrich: 2–5 credits per profile

**Budget constraint:** Large anchors (e.g. Stripe) have 10,000+ alumni. Scope demos to 1–2 anchors; use `recently_changed_jobs=true` or date filters to limit cohort size.

---

## Key design decisions

- **`company_id = 0`** means the company is not yet indexed (common for stealth/new cos). This is expected — medium clusters handle it via headline regex.
- **`open_to_cards`** signal is very sparse (most profiles return `[]`). It's used as a weak bonus signal only.
- All fine-grained logic (which anchor role, co-tenure, leaver window) runs **client-side** on returned data. Never rely on nested filters to scope two fields to the same role.
- Leaver detection: person is a leaver if their anchor role has an `end_date` AND their current role started within `LEAVER_LOOKBACK_MONTHS`.
- Strong clusters: leavers share the same `current_employers.company_id`.
- Medium clusters: stealth/founder leavers (no shared company_id) linked by co-tenure at anchor + departure within `MEDIUM_CLUSTER_WINDOW_MONTHS`.

---

## Running the project

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in keys

# one-shot run
python -c "import asyncio, main; print(asyncio.run(main.run(anchor_name='Stripe')))"

# by LinkedIn URL (preferred)
python -c "import asyncio, main; print(asyncio.run(main.run(anchor_linkedin_url='https://www.linkedin.com/company/stripe')))"

# backtest
python -m backtest.evaluate

# UI
streamlit run ui/app.py
```

---

## Spec and plan references

- Full technical spec: `spec.md`
- API feasibility report (live-tested): `api.md`
- Implementation plan: `plan.md`
