# Stealth Radar — Technical Spec
### Forming-Team & Talent-Flow Intelligence on Crustdata + Claude
> *"See the company before the company exists."*

By Aryan Gupta · IIT Bombay

---

## Table of Contents
1. [Thesis & Scope](#1-thesis--scope)
2. [Pipeline](#2-pipeline)
3. [Data Layer — Endpoints, Auth, Client](#3-data-layer)
4. [Cohort Pull](#4-cohort-pull)
5. [Parsing the Employment Graph](#5-parsing)
6. [Leaver Detection](#6-leaver-detection)
7. [Signal Tagging](#7-signal-tagging)
8. [Clustering](#8-clustering)
9. [Formation Probability Score](#9-scoring)
10. [TalentFlow Graph](#10-talentflow)
11. [Backtest](#11-backtest)
12. [Claude Integration](#12-claude)
13. [Storage Schema](#13-storage)
14. [API + UI](#14-api-ui)
15. [File Structure](#15-files)
16. [Config / Setup / Run](#16-setup)
17. [Limitations](#17-limitations)

---

## 1. Thesis & Scope

When a tight cluster of people who worked **together** leave the **same** company within a short window and **converge** — onto one tiny new employer, or onto "Stealth / Founder" — a startup is forming. That pattern is in the timestamped employment graph months before the company raises or is announced. Stealth Radar detects it, scores it, and lets Claude adjudicate convergence vs. noise.

**Two views, one engine:** *Radar* (forming-team clusters per anchor) and *TalentFlow* (where a company's leavers go).

**Hard constraints, designed around (verified against the docs):**
- Detection runs entirely on the **public indexed** `/person/search` + `/person/enrich` (cached dataset). No enterprise gate.
- **No public Watcher** → near-real-time via scheduled re-runs + snapshot diffing.
- **Live web/person endpoints are enterprise-gated** → web corroboration is optional garnish.
- New API operators: `=>` / `=<` (not `>=` / `<=`); auth is `Bearer` + `x-api-version: 2025-11-01`.

---

## 2. Pipeline

```
COHORT      /person/search → everyone with the anchor in their history (paginate)
   │
PARSE       raw JSON → Person{roles:[Role...]}  (current + full past)
   │
LEAVERS     anchor is a PAST role AND current role started within lookback (or recently_changed)
   │
TAG         per leaver: stealth?, founder?, tiny destination?, open_to_career?, has dest company_id?
   │
CLUSTER     strong: group by shared current.company_id
            medium: stealth/founder leavers who left in a tight window AND overlapped at the anchor
   │
SCORE       Formation Probability per cluster (percentile-normalized weighted composite)
   │
ADJUDICATE  Claude: forming_team | layoff_dispersion | coincidental | unclear → dossier
   │
RANK/DIFF   ranked clusters; on re-run, diff vs last snapshot → new / strengthening
```

---

## 3. Data Layer

> **API notes (verified against live Crustdata):**
> - Filter key is **`column`** (not `field`)
> - `fields` param is a **comma-separated string** (not a list)
> - `open_to_cards` signal is sparse in practice; `"CAREER_INTEREST"` check is kept but weighted low

`config.py`
```python
import os
from dotenv import load_dotenv
load_dotenv()

CRUSTDATA_API_KEY = os.environ["CRUSTDATA_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CRUSTDATA_BASE = "https://api.crustdata.com"
API_VERSION    = "2025-11-01"
CLAUDE_MODEL   = "claude-sonnet-4-6"      # adjudication/dossier; cost-effective

# detection thresholds
LEAVER_LOOKBACK_MONTHS        = 18
TINY_DESTINATION_MAX_HEADCOUNT = 25
MEDIUM_CLUSTER_WINDOW_MONTHS  = 4
MIN_CLUSTER_SIZE              = 2

DUCKDB_PATH   = "data/radar.duckdb"
RATE_LIMIT_RPS = 8                         # tune to your plan; back off on 429
```

`ingestion/client.py` — async, rate-limited, cursor-following client.
```python
import asyncio, httpx
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from config import CRUSTDATA_BASE, CRUSTDATA_API_KEY, API_VERSION, RATE_LIMIT_RPS

HEADERS = {
    "Authorization": f"Bearer {CRUSTDATA_API_KEY}",
    "x-api-version": API_VERSION,
    "content-type": "application/json",
}

class CrustdataClient:
    def __init__(self, rps: int = RATE_LIMIT_RPS):
        self._sem = asyncio.Semaphore(rps)
        self._http = httpx.AsyncClient(base_url=CRUSTDATA_BASE, headers=HEADERS, timeout=60.0)

    async def close(self):
        await self._http.aclose()

    @retry(wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(5),
           retry=retry_if_exception_type(httpx.HTTPStatusError))
    async def _post(self, path: str, payload: dict) -> dict:
        async with self._sem:
            r = await self._http.post(path, json=payload)
            r.raise_for_status()          # 429/5xx → retry with backoff
            return r.json()

    async def person_search(self, filters: dict, fields: str, sorts: list, limit=1000):
        """Async generator yielding pages until next_cursor is null.
        filters: a single dict with 'column'/'type'/'value' (or nested op+conditions).
        fields: comma-separated string of field names to return.
        """
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

    # OPTIONAL — High-cluster enrichment. Confirm exact param name in
    # person-docs/enrichment/reference before relying on it.
    async def person_enrich(self, profile_urls: list[str]):
        return await self._post("/person/enrich",
                                {"professional_network_profile_urls": profile_urls})

    async def company_enrich(self, company_ids: list[int], fields: list[str]):
        return await self._post("/company/enrich",
                                {"crustdata_company_ids": company_ids, "fields": fields})
```

---

## 4. Cohort Pull

`ingestion/cohort.py`
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

# Stable sort is required for safe pagination.
SORTS = [{"column": "current_employers.start_date", "order": "desc"}]

def cohort_filter(anchor_linkedin_url: str | None = None, anchor_name: str | None = None):
    # Prefer the exact entity by LinkedIn URL — avoids name collisions
    # (e.g. two companies both called "Atlas").
    if anchor_linkedin_url:
        return {
            "column": "past_employers.company_linkedin_profile_url",
            "type": "=", "value": anchor_linkedin_url,
        }
    return {
        "column": "past_employers.name",
        "type": "[.]", "value": anchor_name,
    }

async def pull_cohort(client, anchor_linkedin_url=None, anchor_name=None) -> list[dict]:
    flt = cohort_filter(anchor_linkedin_url, anchor_name)
    out = []
    async for page in client.person_search(flt, COHORT_FIELDS, SORTS, limit=1000):
        out.extend(page)
    return out
```

> **Why `past_employers.*`:** filtering on `past_employers.company_linkedin_profile_url` (or `.name`) returns people for whom the anchor is a *past* role — i.e., they already left. Filtering on `current_employers.*` would catch current employees; you want the leavers.

---

## 5. Parsing the Employment Graph
<a id="5-parsing"></a>

The cohort filter is **coarse** (anchor anywhere in the past). All fine-grained logic — *which* anchor role, its `end_date`, co-tenure — happens client-side on the returned `experience` objects. Never trust a single nested filter to scope two fields to the same role.

`detect/model.py`
```python
from dataclasses import dataclass
from datetime import date

@dataclass
class Role:
    company_id: int | None
    company_name: str | None
    title: str | None
    start_date: date | None
    end_date: date | None            # None ⇒ current
    headcount_latest: int | None = None
    function: str | None = None
    seniority: str | None = None

@dataclass
class Person:
    profile_url: str
    name: str
    headline: str
    country: str | None
    open_to: list[str]
    recently_changed: bool
    schools: list[str]
    roles: list[Role]                # full history
    updated_at: str | None

    @property
    def current_role(self) -> Role | None:
        cur = [r for r in self.roles if r.end_date is None]
        cur.sort(key=lambda r: (r.start_date or date.min), reverse=True)
        return cur[0] if cur else None
```

`detect/parse.py`
```python
from datetime import datetime, date
from detect.model import Role, Person

def _d(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: continue
    return None

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

---

## 6. Leaver Detection

`detect/leavers.py`
```python
from datetime import date
from dateutil.relativedelta import relativedelta
from config import LEAVER_LOOKBACK_MONTHS

def anchor_role(person, anchor_id=None, anchor_name=None):
    """The person's PAST role at the anchor (the one they left)."""
    for r in person.roles:
        if anchor_id and r.company_id == anchor_id and r.end_date is not None:
            return r
        if anchor_name and r.company_name and anchor_name.lower() in r.company_name.lower() \
           and r.end_date is not None:
            return r
    return None

def is_leaver(person, anchor_id=None, anchor_name=None, asof: date | None = None) -> bool:
    asof = asof or date.today()
    ar = anchor_role(person, anchor_id, anchor_name)
    if not ar:                       # never left, or never there
        return False
    cur = person.current_role
    if not cur or not cur.start_date:
        return person.recently_changed
    return cur.start_date >= asof - relativedelta(months=LEAVER_LOOKBACK_MONTHS)
```

---

## 7. Signal Tagging

`detect/signals.py`
```python
import re
from config import TINY_DESTINATION_MAX_HEADCOUNT

STEALTH_RE = re.compile(r"stealth|building something|incubating|co-?found", re.I)
FOUNDER_RE = re.compile(r"\bfounder|co-?founder|founding (engineer|team)", re.I)

def tag(person) -> dict:
    cur = person.current_role
    name  = (cur.company_name or "") if cur else ""
    title = (cur.title or "")        if cur else ""
    blob  = f"{title} {person.headline}"
    return {
        "stealth":         bool(STEALTH_RE.search(blob) or "stealth" in name.lower()),
        "founder":         bool(FOUNDER_RE.search(blob)),
        "tiny_destination": bool(cur and cur.headcount_latest is not None
                                 and cur.headcount_latest <= TINY_DESTINATION_MAX_HEADCOUNT),
        "open_to_career":  "CAREER_INTEREST" in person.open_to,
        "has_dest_id":     bool(cur and cur.company_id),
    }
```

---

## 8. Clustering

`detect/cluster.py`
```python
import networkx as nx
from collections import defaultdict
from datetime import date
from detect.leavers import anchor_role
from config import MEDIUM_CLUSTER_WINDOW_MONTHS, MIN_CLUSTER_SIZE

FAR = date(2100, 1, 1)

def _months(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)

def tenure_overlap_months(r1, r2) -> float:
    """Overlap of two people's tenure AT THE ANCHOR."""
    if not (r1 and r2 and r1.start_date and r2.start_date):
        return 0.0
    s = max(r1.start_date, r2.start_date)
    e = min(r1.end_date or FAR, r2.end_date or FAR)
    return max(0, _months(s, e)) if e > s else 0.0

def strong_clusters(leavers):
    """Convergence on a shared, already-indexed new employer."""
    groups = defaultdict(list)
    for p in leavers:
        cur = p.current_role
        if cur and cur.company_id:
            groups[cur.company_id].append(p)
    return [(cid, g) for cid, g in groups.items() if len(g) >= MIN_CLUSTER_SIZE]

def medium_clusters(leavers, tags, anchor_id=None, anchor_name=None):
    """Stealth/founder leavers, no shared dest id yet (too new to be indexed):
    link pairs that left the anchor in a tight window AND overlapped there."""
    cand = [p for p in leavers if tags[p.profile_url]["stealth"] or tags[p.profile_url]["founder"]]
    by_url = {p.profile_url: p for p in cand}
    G = nx.Graph(); G.add_nodes_from(by_url)
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i], cand[j]
            ra = anchor_role(a, anchor_id, anchor_name)
            rb = anchor_role(b, anchor_id, anchor_name)
            if not (ra and rb and ra.end_date and rb.end_date):
                continue
            gap     = abs(_months(ra.end_date, rb.end_date))
            overlap = tenure_overlap_months(ra, rb)
            if gap <= MEDIUM_CLUSTER_WINDOW_MONTHS and overlap > 0:
                G.add_edge(a.profile_url, b.profile_url)
    return [[by_url[u] for u in c] for c in nx.connected_components(G) if len(c) >= MIN_CLUSTER_SIZE]
```

---

## 9. Formation Probability Score
<a id="9-scoring"></a>

`score/model.py`
```python
import numpy as np
from scipy.stats import rankdata
from detect.leavers import anchor_role
from detect.cluster import tenure_overlap_months

WEIGHTS = {
    "size_score":            0.18,   # inverted-U, peak 3–5 (1 = not a team; 50 = a reorg)
    "shared_destination":    0.22,   # strongest: fraction converging on one company_id
    "destination_tiny":      0.12,
    "stealth_founder_ratio": 0.16,
    "window_tightness":      0.12,
    "co_tenure":             0.14,   # did they actually work together at the anchor
    "open_to":               0.06,
}

def size_score(n: int) -> float:
    return float(np.exp(-0.5 * ((n - 4) / 2.2) ** 2))

def cluster_features(cluster, tags, anchor_id=None, anchor_name=None) -> dict:
    n = len(cluster)
    dest_ids = [p.current_role.company_id for p in cluster
                if p.current_role and p.current_role.company_id]
    shared = (max((dest_ids.count(x) for x in set(dest_ids)), default=0) / n) if n else 0.0

    tiny = np.mean([tags[p.profile_url]["tiny_destination"] for p in cluster])
    sf   = np.mean([tags[p.profile_url]["stealth"] or tags[p.profile_url]["founder"] for p in cluster])
    op   = np.mean([tags[p.profile_url]["open_to_career"] for p in cluster])

    starts = [p.current_role.start_date for p in cluster if p.current_role and p.current_role.start_date]
    tight  = float(np.exp(-((max(starts) - min(starts)).days / 30.44) / 6)) if len(starts) >= 2 else 0.5

    ars   = {p.profile_url: anchor_role(p, anchor_id, anchor_name) for p in cluster}
    pairs = [(cluster[i], cluster[j]) for i in range(n) for j in range(i + 1, n)]
    co    = float(np.mean([tenure_overlap_months(ars[a.profile_url], ars[b.profile_url]) > 0
                           for a, b in pairs])) if pairs else 0.0

    return {"size_score": size_score(n), "shared_destination": shared,
            "destination_tiny": float(tiny), "stealth_founder_ratio": float(sf),
            "window_tightness": tight, "co_tenure": co, "open_to": float(op)}

def score_clusters(rows: list[dict]) -> np.ndarray:
    """Percentile-normalize each feature across clusters, then weight → 0–100."""
    if not rows:
        return np.array([])
    out = np.zeros(len(rows))
    for k, w in WEIGHTS.items():
        v = np.array([r[k] for r in rows])
        norm = (rankdata(v, method="average") - 1) / (len(v) - 1) if len(v) > 1 else np.ones_like(v)
        out += w * norm
    return out * 100

def tier(s: float) -> str:
    return "High" if s >= 75 else "Medium" if s >= 50 else "Low" if s >= 25 else "Watch"
```

---

## 10. TalentFlow Graph
<a id="10-talentflow"></a>

`detect/flow.py` — directed edges anchor→destination, weighted by leaver count; used for the Sankey view and to spot systematic poaching.
```python
from collections import Counter

def flow_edges(leavers, anchor_label: str):
    dest = Counter()
    for p in leavers:
        cur = p.current_role
        if cur and (cur.company_name or cur.company_id):
            dest[(cur.company_id, cur.company_name)] += 1
    return [{"source": anchor_label, "target_id": cid, "target": name, "weight": w}
            for (cid, name), w in dest.most_common()]
```

---

## 11. Backtest
<a id="11-backtest"></a>

The headline proof — and feasible cold, because role dates are retrospective. You reconstruct the "as of T − N months" world from data already returned.

`backtest/asof.py`
```python
import copy
from datetime import date

def role_active_at(person, t: date):
    active = [r for r in person.roles if r.start_date and r.start_date <= t
              and (r.end_date is None or r.end_date > t)]
    active.sort(key=lambda r: r.start_date, reverse=True)
    return active[0] if active else None

def asof_person(person, t: date):
    """View of the person as they appeared at date t: roles starting after t are
    removed; the role active at t becomes 'current'."""
    p = copy.deepcopy(person)
    cur = role_active_at(person, t)
    p.roles = [r for r in p.roles if r.start_date and r.start_date <= t]
    for r in p.roles:
        is_cur = bool(cur and r.start_date == cur.start_date and r.company_id == cur.company_id)
        r.end_date = None if is_cur else (r.end_date or t)
    return p
```

`backtest/ground_truth.py` — hand-assembled, ~30–60 rows.
```python
# Startups that publicly announced/raised a first round in the last 6–18 months,
# each with its founding team's prior shared employer.
GROUND_TRUTH = [
    {
        "startup": "Example Labs",
        "announce_date": "2025-09-01",
        "prior_employer_name": "BigLab",
        "prior_employer_profile_url": "https://www.linkedin.com/company/biglab",
        "founder_profile_urls": [
            "https://www.linkedin.com/in/founder-one",
            "https://www.linkedin.com/in/founder-two",
        ],
    },
    # ... 30–60 of these
]
```

`backtest/evaluate.py` — run the full detector as-of T−N, record outcomes.
```python
from datetime import date
from dateutil.relativedelta import relativedelta

def evaluate(detector, ground_truth, horizons=(3, 6, 9)):
    """
    detector(asof_people, anchor_*) -> ranked [(cluster, score)].
    Returns lead-time per caught formation + Precision@K + recall per horizon.
    """
    results = {"lead_times": [], "per_horizon": {}}
    for n in horizons:
        caught = 0
        for gt in ground_truth:
            T = date.fromisoformat(gt["announce_date"])
            asof_T = T - relativedelta(months=n)
            people = build_asof_cohort(gt["prior_employer_profile_url"], asof_T)  # cohort + asof_person()
            ranked = detector(people, anchor_name=gt["prior_employer_name"])
            hit = any(_contains(cluster, gt["founder_profile_urls"]) and score >= 50
                      for cluster, score in ranked)
            caught += int(hit)
        results["per_horizon"][n] = {"recall": caught / len(ground_truth)}
    # lead_time: earliest horizon at which each formation crosses threshold
    return results

def _contains(cluster, founder_urls):
    have = {p.profile_url for p in cluster}
    return len(have & set(founder_urls)) >= 2
```

**Metrics to report:** median **lead time** (months of early warning — the slide), **Precision@K** per anchor, **recall** at 3/6/9 months. Target a card like *"caught 14/20 founding teams a median 5 months before announcement."*

---

## 12. Claude Integration
<a id="12-claude"></a>

`claude/adjudicate.py` — the hard call: convergence vs. dispersion gates which clusters reach the user.
```python
import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

_client = Anthropic(api_key=ANTHROPIC_API_KEY)

ADJUDICATE_SYS = (
    "You classify whether a cluster of people leaving a shared employer represents an "
    "intentional team formation or noise. Be skeptical. Name disconfirming evidence. "
    "Respond ONLY with JSON: "
    '{"label":"forming_team|layoff_dispersion|coincidental|unclear",'
    '"confidence":0-1,"rationale":"one sentence"}'
)

def adjudicate(cluster_summary: dict) -> dict:
    msg = _client.messages.create(
        model=CLAUDE_MODEL, max_tokens=400,
        system=ADJUDICATE_SYS,
        messages=[{"role": "user", "content": json.dumps(cluster_summary)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        return json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        return {"label": "unclear", "confidence": 0.0, "rationale": "parse_error"}
```

`claude/dossier.py` — brief for High/Medium clusters.
```python
import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL
_client = Anthropic(api_key=ANTHROPIC_API_KEY)

DOSSIER_SYS = (
    "You are a deal/talent scout. From structured cluster data, write a tight brief. "
    "Name exact signals and dates; no filler; flag anything that weakens the thesis. "
    'Respond ONLY with JSON: {"summary":str,"members":[str],'
    '"evidence_timeline":[str],"thesis":str,"recommended_action":str,'
    '"urgency":"now|30d|90d"}'
)

def dossier(cluster_summary: dict) -> dict:
    msg = _client.messages.create(
        model=CLAUDE_MODEL, max_tokens=900,
        system=DOSSIER_SYS,
        messages=[{"role": "user", "content": json.dumps(cluster_summary)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
```

**`cluster_summary` payload** (what you hand Claude): anchor name; per member name/headline/current title+company/current start_date; the anchor co-tenure overlaps; destination convergence; the feature dict + score. Keep it factual — let Claude judge, don't pre-conclude in the prompt.

---

## 13. Storage Schema (DuckDB)
<a id="13-storage"></a>

```sql
-- One row per run snapshot of a leaver (enables diffing for near-real-time)
CREATE TABLE leavers (
  run_date        DATE,
  anchor          VARCHAR,
  profile_url     VARCHAR,
  name            VARCHAR,
  headline        VARCHAR,
  current_company_id   INTEGER,
  current_company_name VARCHAR,
  current_title        VARCHAR,
  current_start_date   DATE,
  anchor_end_date      DATE,
  stealth         BOOLEAN,
  founder         BOOLEAN,
  tiny_dest       BOOLEAN,
  open_to_career  BOOLEAN,
  PRIMARY KEY (run_date, anchor, profile_url)
);

CREATE TABLE clusters (
  run_date        DATE,
  anchor          VARCHAR,
  cluster_id      VARCHAR,        -- hash of sorted member urls
  member_urls     VARCHAR,        -- JSON array
  kind            VARCHAR,        -- 'strong' | 'medium'
  score           DOUBLE,
  tier            VARCHAR,
  features        VARCHAR,        -- JSON
  adjudication    VARCHAR,        -- JSON {label,confidence,rationale}
  dossier         VARCHAR,        -- JSON
  PRIMARY KEY (run_date, anchor, cluster_id)
);

CREATE TABLE flow_edges (
  run_date    DATE, anchor VARCHAR, target_id INTEGER,
  target      VARCHAR, weight INTEGER,
  PRIMARY KEY (run_date, anchor, target_id)
);

CREATE TABLE backtest (
  startup VARCHAR, announce_date DATE, horizon_months INTEGER,
  caught BOOLEAN, score_at_horizon DOUBLE,
  PRIMARY KEY (startup, horizon_months)
);
```

Diffing: on each run, compare `clusters` to the prior `run_date` for the same anchor → emit *new* clusters and *score-increasing* clusters as alerts.

---

## 14. API + UI
<a id="14-api-ui"></a>

`api/server.py` (FastAPI) — `POST /radar {anchor}` → ranked clusters + dossiers; `GET /flow?anchor=` → Sankey edges; `GET /backtest` → metrics card.

`ui/app.py` (Streamlit) — three views:
1. **Radar** — anchor input → ranked clusters: members, score, tier, evidence timeline, Claude dossier, action.
2. **TalentFlow** — Sankey of leaver destinations (Plotly), tiny/stealth targets highlighted.
3. **Backtest** — lead-time card, Precision@K bar chart, "teams we'd have caught early" table vs. actual outcomes.

`main.py` — orchestration:
```python
import asyncio
from ingestion.client import CrustdataClient
from ingestion.cohort import pull_cohort
from detect.parse import parse_person
from detect.leavers import is_leaver
from detect.signals import tag
from detect.cluster import strong_clusters, medium_clusters
from score.model import cluster_features, score_clusters, tier

async def run(anchor_name=None, anchor_linkedin_url=None):
    client = CrustdataClient()
    try:
        raw = await pull_cohort(client, anchor_linkedin_url, anchor_name)
    finally:
        await client.close()

    people  = [parse_person(r) for r in raw]
    leavers = [p for p in people if is_leaver(p, anchor_name=anchor_name)]
    tags    = {p.profile_url: tag(p) for p in leavers}

    clusters = [g for _, g in strong_clusters(leavers)] \
             + medium_clusters(leavers, tags, anchor_name=anchor_name)

    feats  = [cluster_features(c, tags, anchor_name=anchor_name) for c in clusters]
    scores = score_clusters(feats)
    ranked = sorted(zip(clusters, scores, feats), key=lambda x: x[1], reverse=True)
    return [(c, s, tier(s), f) for c, s, f in ranked]
```

---

## 15. File Structure
<a id="15-files"></a>
```
stealth-radar/
├── config.py
├── main.py
├── ingestion/   client.py · cohort.py · snapshot.py
├── detect/      model.py · parse.py · leavers.py · signals.py · cluster.py · flow.py
├── score/       model.py
├── backtest/    ground_truth.py · asof.py · evaluate.py
├── claude/      adjudicate.py · dossier.py
├── api/         server.py
├── ui/          app.py
├── data/        radar.duckdb        (gitignored)
├── tests/       test_leavers.py · test_cluster.py · test_score.py · test_asof.py
├── requirements.txt · .env.example · README.md
```

---

## 16. Config / Setup / Run
<a id="16-setup"></a>
```
requirements.txt
────────────────
anthropic>=0.40.0
httpx>=0.27.0
tenacity>=8.2.0
networkx>=3.2
numpy>=1.26.0
scipy>=1.12.0
python-dateutil>=2.9.0
duckdb>=0.10.0
fastapi>=0.110.0
uvicorn>=0.29.0
streamlit>=1.35.0
plotly>=5.20.0
python-dotenv>=1.0.0
```
```bash
git clone https://github.com/ary1002/stealth-radar && cd stealth-radar
pip install -r requirements.txt
cp .env.example .env     # CRUSTDATA_API_KEY, ANTHROPIC_API_KEY

python -c "import asyncio, main; print(asyncio.run(main.run(anchor_name='AnchorCo')))"
# or by LinkedIn URL (preferred — avoids name collisions):
python -c "import asyncio, main; print(asyncio.run(main.run(anchor_linkedin_url='https://www.linkedin.com/company/anchorco')))"
python -m backtest.evaluate          # produces the lead-time card
streamlit run ui/app.py
```

**Tests worth writing first** (they catch the subtle bugs): `tenure_overlap_months` (open-ended roles, no overlap, full containment), `asof_person` (role active at boundary dates), `is_leaver` (still-employed vs. left), `size_score` (peak at 4, decay at 1 and 20).

---

## 17. Limitations (own these in the pitch)
<a id="17-limitations"></a>
- **Near-real-time, not instant** — no public Watcher; scheduled re-runs + diffing. Watcher is the enterprise upgrade path.
- **Newest stealth co's lack a `company_id`** → medium-cluster (headline-text) path, lower precision. Report per-cluster confidence and Claude's adjudication.
- **Credits/cost** — a large anchor's full alumni is credit-heavy. Scope the demo to one hot anchor + bounded window; `person_enrich` only High clusters.
- **Preview mode is premium** (`400` if not enabled) — don't build on it.
- **People dedup / entity resolution** is real work; key on `profile_url`, budget for collisions.
- **`person_enrich` param name** — confirm against `person-docs/enrichment/reference` before wiring it in.

---

*Stealth Radar — built on Crustdata + Claude · Aryan Gupta · IIT Bombay*
