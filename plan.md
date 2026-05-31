# Stealth Radar — Implementation Plan

## Context

The spec (spec.md) was written against an assumed nested Crustdata REST API schema that does not match the actual API. The api.md feasibility report identified the exact mismatches via live API testing. Before implementing any code, spec.md must be corrected so all generated files are built against the real API from the start. After fixing the spec, the full project is implemented from scratch (0 Python files exist today).

---

## Phase 1 — Correct spec.md

Three sections need rewriting in place:

### 1a. `ingestion/cohort.py` (§4)

Replace `COHORT_FIELDS` and `cohort_filter()` with corrected versions:

```python
COHORT_FIELDS = (
    "name,headline,linkedin_profile_url,recently_changed_jobs,open_to_cards,"
    "location_country,"
    "current_employers.name,current_employers.title,current_employers.start_date,"
    "current_employers.company_id,current_employers.company_headcount_latest,"
    "current_employers.function_category,current_employers.seniority_level,"
    "past_employers.name,past_employers.start_date,past_employers.end_date,"
    "past_employers.company_id,past_employers.company_linkedin_profile_url,"
    "past_employers.company_headcount_latest,"
    "education_background.institute_name"
)

SORTS = [{"column": "current_employers.start_date", "order": "desc"}]

def cohort_filter(anchor_linkedin_url=None, anchor_name=None):
    if anchor_linkedin_url:
        return {"column": "past_employers.company_linkedin_profile_url",
                "type": "=", "value": anchor_linkedin_url}
    return {"column": "past_employers.name", "type": "[.]", "value": anchor_name}
```

Also update `pull_cohort()` signature to match (`anchor_linkedin_url` instead of `anchor_profile_url`).

### 1b. `ingestion/client.py` (§3)

The `person_search` method payload key was `filters` (a list). Change to match actual API:

```python
async def person_search(self, filters: dict, fields: str, sorts: list, limit=1000):
    cursor = None
    while True:
        payload = {"filters": filters, "fields": fields, "sorts": sorts, "limit": limit}
        if cursor:
            payload["cursor"] = cursor
        data = await self._post("/person/search", payload)
        profiles = data.get("profiles", [])
        yield profiles
        cursor = data.get("next_cursor")
        if not cursor or not profiles:
            break
```

Note: `fields` is now a comma-separated string (not a list).

### 1c. `detect/parse.py` (§5)

Replace `_roles()` and `parse_person()` entirely to match the flat response structure:

```python
def _roles_from_flat(raw: dict) -> list[Role]:
    roles = []
    for c in (raw.get("current_employers") or []):
        roles.append(Role(
            c.get("company_id"), c.get("name"),
            c.get("title"), _d(c.get("start_date")), None,
            c.get("company_headcount_latest"),
            c.get("function_category"), c.get("seniority_level"),
        ))
    for p in (raw.get("past_employers") or []):
        roles.append(Role(
            p.get("company_id"), p.get("name"),
            None, _d(p.get("start_date")), _d(p.get("end_date")),
            p.get("company_headcount_latest"),
        ))
    return roles

def parse_person(raw: dict) -> Person:
    schools = [e.get("institute_name") for e in
               (raw.get("education_background") or []) if e.get("institute_name")]
    return Person(
        raw.get("linkedin_profile_url", ""),
        raw.get("name", ""),
        raw.get("headline", "") or "",
        raw.get("location_country"),
        raw.get("open_to_cards") or [],
        bool(raw.get("recently_changed_jobs")),
        schools,
        _roles_from_flat(raw),
        None,
    )
```

Remove the old `_roles()` function and the `COHORT_FIELDS` list-of-strings block in §4.

### 1d. Note to add at top of §3 (Data Layer)

Add a callout noting:
- Filter key is `column` (not `field`)
- `fields` param is a comma-separated string (not a list)
- `open_to_cards` signal is sparse; `"CAREER_INTEREST"` check kept but weighted low

---

## Phase 2 — Implement All Files

Implement in dependency order. Each file is taken verbatim from the corrected spec unless noted.

### Step 1 — Scaffolding
- `requirements.txt` — as per §16
- `.env.example` — `CRUSTDATA_API_KEY=`, `ANTHROPIC_API_KEY=`
- `config.py` — verbatim from §3

### Step 2 — Ingestion layer
- `ingestion/__init__.py`
- `ingestion/client.py` — corrected version from 1b above
- `ingestion/cohort.py` — corrected version from 1a above
- `ingestion/snapshot.py` — DuckDB snapshot diffing helper (save leavers/clusters rows, return new + strengthening clusters vs prior run_date)

### Step 3 — Detection layer
- `detect/__init__.py`
- `detect/model.py` — verbatim from §5
- `detect/parse.py` — corrected version from 1c above
- `detect/leavers.py` — verbatim from §6
- `detect/signals.py` — verbatim from §7
- `detect/cluster.py` — verbatim from §8
- `detect/flow.py` — verbatim from §10

### Step 4 — Scoring
- `score/__init__.py`
- `score/model.py` — verbatim from §9

### Step 5 — Claude integration
- `claude/__init__.py`
- `claude/adjudicate.py` — verbatim from §12
- `claude/dossier.py` — verbatim from §12

### Step 6 — Storage
- `data/` directory (gitignored)
- DuckDB schema init embedded in `ingestion/snapshot.py` (CREATE TABLE IF NOT EXISTS for leavers, clusters, flow_edges, backtest per §13)

### Step 7 — Backtest
- `backtest/__init__.py`
- `backtest/ground_truth.py` — stub with 3–5 real examples (YC/public announcements 2024–2025)
- `backtest/asof.py` — verbatim from §11
- `backtest/evaluate.py` — verbatim from §11

### Step 8 — Orchestration
- `main.py` — verbatim from §14, with `anchor_linkedin_url` param added

### Step 9 — API + UI
- `api/__init__.py`
- `api/server.py` — FastAPI per §14: `POST /radar`, `GET /flow`, `GET /backtest`
- `ui/__init__.py`
- `ui/app.py` — Streamlit per §14: Radar / TalentFlow / Backtest tabs

### Step 10 — Tests
- `tests/test_leavers.py`
- `tests/test_cluster.py`
- `tests/test_score.py`
- `tests/test_asof.py`

Tests cover the four cases from §16: `tenure_overlap_months`, `asof_person`, `is_leaver`, `size_score`.

---

## Critical Files Modified in spec.md

| Section | What changes |
|---|---|
| §3 `ingestion/client.py` | `fields` type: list → comma-string; add API notes callout |
| §4 `ingestion/cohort.py` | `COHORT_FIELDS` rewritten flat; `cohort_filter` key `field`→`column`, path fixed |
| §5 `detect/parse.py` | `_roles()` + `parse_person()` replaced with flat-structure versions |

All other spec sections (§6–§14) are correct as written and implemented verbatim.

---

## Verification

1. `python -c "import asyncio, main; print(asyncio.run(main.run(anchor_name='Stripe'))[:3])"` — prints 3 ranked clusters
2. `python -m pytest tests/ -v` — all 4 test modules pass
3. `python -m backtest.evaluate` — prints recall @ 3/6/9 months
4. `streamlit run ui/app.py` — Radar tab loads with anchor input
