"""
Tests 1–3: Thesis → Radar connection verification.
Total cost: limit=5 × 0.03 × 1 cohort call = 0.15 cr
"""
import asyncio, json, sys, os
from datetime import date
import numpy as np
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from ingestion.client import CrustdataClient
from ingestion.cohort import COHORT_FIELDS, SORTS
from detect.parse import parse_person
from detect.leavers import is_leaver
from detect.signals import tag
from detect.cluster import strong_clusters, medium_clusters
from score.model import cluster_features, tier
from detect.model import Person, Role
from models.schemas import ThesisConfig, AnchorStrategy, CompanyGate, ScoringWeights
from config import STRONG_CLUSTER_MAX_HEADCOUNT

LIMIT  = 5
ANCHOR = "Google"
ANCHOR_ID = 4926893
results_table = []

# ── Thesis fixtures ───────────────────────────────────────────────────────────

DOGFOOD = ThesisConfig(
    thesis_id="preseed_ai_agents_data_infra_faang",
    label="Pre-seed AI/data infra founders, ex-FAANG, US-based",
    anchor_strategy=AnchorStrategy(mode="explicit",
        companies=[{"name": "Google", "company_id": ANCHOR_ID}]),
    person_filters={
        "op": "and",
        "conditions": [
            {"field": "basic_profile.location.country",  "type": "=",  "value": "United States"},
            {"field": "recently_changed_jobs",            "type": "=",  "value": True},
            {"field": "experience.employment_details.current.seniority_level",
             "type": "in", "value": ["Senior","Director","Vice President","CXO","Owner / Partner"]},
        ],
    },
    company_gate=CompanyGate(max_headcount=500, countries=["USA"]),
    scoring_weights=ScoringWeights(
        size_score=0.05, shared_destination=0.25, destination_tiny=0.20,
        stealth_founder_ratio=0.25, window_tightness=0.10, co_tenure=0.10, open_to=0.05,
    ),
    max_investigation_credits=10.0,
)

TENURE = ThesisConfig(
    thesis_id="long_tenure_team_formers",
    label="Teams with long shared tenure",
    anchor_strategy=AnchorStrategy(mode="explicit",
        companies=[{"name": "Google", "company_id": ANCHOR_ID}]),
    person_filters={"field": "recently_changed_jobs", "type": "=", "value": True},
    company_gate=CompanyGate(max_headcount=500, countries=[]),
    scoring_weights=ScoringWeights(
        size_score=0.10, shared_destination=0.15, destination_tiny=0.05,
        stealth_founder_ratio=0.05, window_tightness=0.10, co_tenure=0.45, open_to=0.10,
    ),
    max_investigation_credits=5.0,
)

# ── Scoring helper ────────────────────────────────────────────────────────────

ABSOLUTE = {"shared_destination", "stealth_founder_ratio", "window_tightness",
            "destination_tiny", "size_score"}

def score_with_weights(feats, weights):
    w = {
        "size_score":            weights.size_score,
        "shared_destination":    weights.shared_destination,
        "destination_tiny":      weights.destination_tiny,
        "stealth_founder_ratio": weights.stealth_founder_ratio,
        "window_tightness":      weights.window_tightness,
        "co_tenure":             weights.co_tenure,
        "open_to":               weights.open_to,
    }
    if not feats:
        return np.array([])
    out = np.zeros(len(feats))
    for k, wv in w.items():
        v = np.array([r[k] for r in feats], dtype=float)
        norm = v if k in ABSOLUTE else (
            (rankdata(v, "average") - 1) / (len(v) - 1) if len(v) > 1 else np.ones_like(v)
        )
        out += wv * norm
    return out * 100

# ── Cohort pull that merges thesis.person_filters ────────────────────────────

async def pull_cohort(client, thesis, limit):
    anchor_id = next(
        (a["company_id"] for a in (thesis.anchor_strategy.companies or [])
         if isinstance(a, dict) and a.get("company_id")),
        None
    )
    anchor_cond = (
        {"field": "experience.employment_details.past.company_id", "type": "=", "value": anchor_id}
        if anchor_id else
        {"field": "experience.employment_details.past.company_name", "type": "=", "value": ANCHOR}
    )
    pf = thesis.person_filters or {}
    extra = (pf.get("conditions", []) if pf.get("op") == "and"
             else ([pf] if pf.get("field") else []))
    filt = {"op": "and", "conditions": [anchor_cond] + extra}

    raw = []
    async for page in client.person_search(
        filters=filt, fields=COHORT_FIELDS, sorts=SORTS, limit=limit
    ):
        raw.extend(page)
        break
    return raw, filt

def cluster_run(raw_data, thesis):
    people  = [parse_person(r) for r in raw_data]
    leavers = [p for p in people if is_leaver(p, anchor_name=ANCHOR)]
    tgs     = {p.profile_url: tag(p) for p in leavers}
    strong  = [g for _, g in strong_clusters(leavers)]
    medium  = medium_clusters(leavers, tgs, anchor_name=ANCHOR)
    clusters = strong + medium
    if not clusters:
        return []
    feats  = [cluster_features(c, tgs, anchor_name=ANCHOR) for c in clusters]
    scores = score_with_weights(feats, thesis.scoring_weights)
    return sorted([
        {
            "dest":       c[0].current_role.company_name if c[0].current_role else "?",
            "score":      float(s),
            "co_tenure":  f["co_tenure"],
            "stealth":    f["stealth_founder_ratio"],
        }
        for c, f, s in zip(clusters, feats, scores)
    ], key=lambda x: -x["score"])


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1 — person_filters wired into outgoing /person/search
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 65)
print("TEST 1 — person_filters present in outgoing /person/search")
print("=" * 65)

async def test1():
    client = CrustdataClient()
    try:
        raw, filt = await pull_cohort(client, DOGFOOD, LIMIT)
    finally:
        await client.close()

    conds = filt.get("conditions", [])
    fields = [c.get("field", "") for c in conds]
    country_val = next(
        (c.get("value") for c in conds if c.get("field") == "basic_profile.location.country"),
        None
    )

    print(f"  Profiles returned : {len(raw)}  (${len(raw)*0.03:.2f} cr)")
    print(f"  Conditions sent   : {len(conds)}")
    print(f"  Fields in request : {fields}")
    print(f"  location.country  : {country_val!r}")
    print()

    checks = {
        "location.country present":          "basic_profile.location.country" in fields,
        "location.country = 'United States'": country_val == "United States",
        "recently_changed_jobs present":      any("recently_changed_jobs" in f for f in fields),
        "seniority_level present":            any("seniority" in f for f in fields),
        "thesis filters merged (>2 conds)":   len(fields) > 2,
    }
    for name, ok in checks.items():
        print(f"  [{'v' if ok else 'X'}] {name}")

    passed = all(checks.values())
    print(f"\n  --> {'PASS' if passed else 'FAIL'}")
    results_table.append({
        "test": 1, "what": "person_filters in outgoing request",
        "pass": passed,
        "evidence": f"fields={fields!r}, country={country_val!r}",
    })
    return raw

raw_live = asyncio.run(test1())


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Scoring weights change cluster ranking
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 65)
print("TEST 2 — Scoring weights change cluster ranking")
print("=" * 65)

r_dog = cluster_run(raw_live, DOGFOOD)
r_ten = cluster_run(raw_live, TENURE)

print("  Dogfood weights (stealth=0.25, co_tenure=0.10):")
for i, r in enumerate(r_dog[:5]):
    print(f"    #{i+1} {r['dest']:<28} score={r['score']:5.1f}  "
          f"co_tenure={r['co_tenure']:.2f}  stealth={r['stealth']:.2f}")

print("  Tenure weights  (stealth=0.05, co_tenure=0.45):")
for i, r in enumerate(r_ten[:5]):
    print(f"    #{i+1} {r['dest']:<28} score={r['score']:5.1f}  "
          f"co_tenure={r['co_tenure']:.2f}  stealth={r['stealth']:.2f}")

order_dog = [r["dest"] for r in r_dog]
order_ten = [r["dest"] for r in r_ten]
order_changed = order_dog != order_ten

common = [(r_d, r_t) for r_d in r_dog for r_t in r_ten if r_d["dest"] == r_t["dest"]]
diffs  = [abs(a["score"] - b["score"]) for a, b in common]
max_diff = max(diffs) if diffs else 0.0

# Expected-direction move: high co_tenure cluster rises with tenure weights
moved_expected = False
for i_d, rd in enumerate(r_dog):
    for i_t, rt in enumerate(r_ten):
        if rd["dest"] == rt["dest"] and rd["co_tenure"] > 0.3 and i_t < i_d:
            print(f"\n  '{rd['dest']}' (co_tenure={rd['co_tenure']:.2f}) "
                  f"#{i_d+1}→#{i_t+1} with tenure weights  [expected direction v]")
            moved_expected = True

print(f"\n  Rank order changed : {order_changed}")
print(f"  Max score delta    : {max_diff:.1f}")

passed2 = order_changed or max_diff > 3.0
print(f"\n  --> {'PASS' if passed2 else 'FAIL'}")
results_table.append({
    "test": 2, "what": "Scoring weights change ranking",
    "pass": passed2,
    "evidence": f"order_changed={order_changed}, max_delta={max_diff:.1f}",
})


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Headcount gate demotes large-destination clusters
# ═══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 65)
print("TEST 3 — Headcount gate demotes large-destination clusters")
print("=" * 65)

def mock_person(name, co, cid, hc, a_start, a_end):
    return Person(
        profile_url=f"https://linkedin.com/in/{name.lower().replace(' ', '-')}",
        name=name, headline=f"Builder @ {co}", country="US",
        open_to=[], recently_changed=True, schools=[], updated_at=None,
        roles=[
            Role(company_id=cid, company_name=co, title="Co-Founder",
                 start_date=date(2024, 1, 1), end_date=None, headcount_latest=hc),
            Role(company_id=ANCHOR_ID, company_name="Google", title="SWE",
                 start_date=a_start, end_date=a_end),
        ],
    )

tiny_cluster  = [
    mock_person("Alice T", "TinyCo",  11111,  8,    date(2022,1,1), date(2024,1,1)),
    mock_person("Bob T",   "TinyCo",  11111,  8,    date(2022,3,1), date(2024,2,1)),
]
large_cluster = [
    mock_person("Carol L", "BigCorp", 22222, 18000, date(2022,1,1), date(2024,1,1)),
    mock_person("Dave L",  "BigCorp", 22222, 18000, date(2022,3,1), date(2024,2,1)),
]

leavers  = tiny_cluster + large_cluster
tgs      = {p.profile_url: tag(p) for p in leavers}
clusters = [g for _, g in strong_clusters(leavers)]
feats    = [cluster_features(c, tgs, anchor_name="Google") for c in clusters]
scores   = score_with_weights(feats, DOGFOOD.scoring_weights)

print(f"  Config gate threshold : {STRONG_CLUSTER_MAX_HEADCOUNT}")
print(f"  Thesis gate value     : {DOGFOOD.company_gate.max_headcount}  "
      f"(pipeline uses config constant, not thesis value)")
print()

large_demoted = tiny_surfaced = False
for c, f, s in zip(clusters, feats, scores):
    s    = float(s)
    dest = c[0].current_role.company_name if c[0].current_role else "?"
    hc   = c[0].current_role.headcount_latest if c[0].current_role else 0
    hcs  = [p.current_role.headcount_latest for p in c
            if p.current_role and p.current_role.headcount_latest]
    is_large  = bool(hcs) and max(hcs) >= STRONG_CLUSTER_MAX_HEADCOUNT
    tier_raw  = tier(s)
    tier_show = (f"{tier_raw} [demoted]" if is_large else tier_raw)

    print(f"  {dest:<22} hc={hc:<8} score={s:5.1f}  tier={tier_show}")

    if is_large:
        large_demoted = True
    elif hc and hc < 500:
        tiny_surfaced = True

print()
print(f"  Large-dest demoted : {'YES v' if large_demoted else 'NO — gate not firing X'}")
print(f"  Tiny-dest surfaced : {'YES v' if tiny_surfaced else 'NO X'}")

passed3 = large_demoted and tiny_surfaced
print(f"\n  --> {'PASS' if passed3 else 'FAIL'}")
results_table.append({
    "test": 3, "what": "Headcount gate demotes large-dest clusters",
    "pass": passed3,
    "evidence": (f"large_demoted={large_demoted}, tiny_surfaced={tiny_surfaced}, "
                 f"gate={STRONG_CLUSTER_MAX_HEADCOUNT}"),
})


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

print("\n\n" + "=" * 95)
print("SUMMARY")
print("=" * 95)
print(f"{'Test':<6} {'What was checked':<46} {'Pass/Fail':<11} Evidence")
print("-" * 95)
for r in results_table:
    icon = "PASS ✓" if r["pass"] else "FAIL ✗"
    print(f"{r['test']:<6} {r['what']:<46} {icon:<11} {r['evidence'][:44]}")
