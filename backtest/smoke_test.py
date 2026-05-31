"""Smoke tests for backtest code — zero network calls."""
import copy
import sys
from datetime import date
from unittest.mock import patch

results = []

def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        results.append((name, True, None))
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        results.append((name, False, str(e)))

# ── 1. Import sanity ──────────────────────────────────────────────────────────
print("\n=== 1. Import sanity ===")

def test_imports():
    from backtest.ground_truth import GROUND_TRUTH
    from backtest.asof import asof_person, role_active_at
    from backtest.evaluate import evaluate, build_asof_cohort
    print(f"    len(GROUND_TRUTH) = {len(GROUND_TRUTH)}")
    for g in GROUND_TRUTH:
        print(f"    anchor: {g['prior_employer_name']!r}  startup: {g['startup']!r}")

check("imports", test_imports)

# ── 2. asof_person correctness ────────────────────────────────────────────────
print("\n=== 2. asof_person correctness ===")

from detect.model import Person, Role
from backtest.asof import asof_person, role_active_at

stripe_role = Role(
    company_id=1,
    company_name="Stripe",
    title="Engineer",
    start_date=date(2019, 1, 1),
    end_date=date(2022, 6, 1),
)
stealth_role = Role(
    company_id=0,
    company_name="Stealth Co",
    title="Co-founder",
    start_date=date(2022, 7, 1),
    end_date=None,
    headcount_latest=3,
)
person = Person(
    profile_url="https://www.linkedin.com/in/test-person",
    name="Test Person",
    headline="Building something new",
    country="US",
    open_to=[],
    recently_changed=True,
    schools=[],
    roles=[stripe_role, stealth_role],
    updated_at=None,
)

def test_asof_before_stealth():
    p2 = asof_person(person, date(2021, 1, 1))
    assert p2.current_role is not None, "current_role should not be None"
    assert p2.current_role.company_name == "Stripe", f"expected Stripe, got {p2.current_role.company_name}"
    stealth_names = [r.company_name for r in p2.roles if r.company_name == "Stealth Co"]
    assert not stealth_names, f"Stealth Co should be stripped, got {stealth_names}"

check("asof before stealth → Stripe current", test_asof_before_stealth)

def test_asof_after_stealth():
    p2 = asof_person(person, date(2022, 9, 1))
    assert p2.current_role is not None, "current_role should not be None"
    assert p2.current_role.company_name == "Stealth Co", f"expected Stealth Co, got {p2.current_role.company_name}"

check("asof after stealth → Stealth Co current", test_asof_after_stealth)

def test_asof_boundary():
    p2 = asof_person(person, date(2019, 1, 1))
    assert p2.current_role is not None, "current_role should not be None at exact boundary"
    assert p2.current_role.company_name == "Stripe", f"expected Stripe on boundary, got {p2.current_role.company_name}"

check("asof exact start boundary → Stripe current", test_asof_boundary)

def test_no_mutation():
    original_roles = copy.deepcopy(person.roles)
    _ = asof_person(person, date(2021, 1, 1))
    _ = asof_person(person, date(2022, 9, 1))
    assert len(person.roles) == len(original_roles), "roles length changed"
    for orig, cur in zip(original_roles, person.roles):
        assert orig.end_date == cur.end_date, f"end_date mutated: {orig.end_date} → {cur.end_date}"
        assert orig.company_name == cur.company_name, f"company_name mutated"

check("original person not mutated after asof calls", test_no_mutation)

# ── 3. evaluate() with synthetic detector ────────────────────────────────────
print("\n=== 3. evaluate() with synthetic detector ===")

from backtest.evaluate import evaluate

url_a = "https://www.linkedin.com/in/VERIFY-shensi-ding"
url_b = "https://www.linkedin.com/in/VERIFY-gilbert-lau"

person_a = Person(
    profile_url=url_a, name="Founder A", headline="ex-Stripe", country="US",
    open_to=[], recently_changed=True, schools=[], roles=[], updated_at=None,
)
person_b = Person(
    profile_url=url_b, name="Founder B", headline="ex-Stripe", country="US",
    open_to=[], recently_changed=True, schools=[], roles=[], updated_at=None,
)

gt_entry = {
    "startup": "Merge",
    "announce_date": "2024-02-01",
    "prior_employer_name": "Stripe",
    "prior_employer_linkedin_url": "https://www.linkedin.com/company/stripe",
    "founder_profile_urls": [url_a, url_b],
}

def mock_detector(people, anchor_name=None):
    if len(people) >= 2:
        return [(people[:2], 80.0, "High", {})]
    return []

def mock_build_cohort(anchor_linkedin_url, asof_date):
    return [person_a, person_b]

def test_evaluate_synthetic():
    with patch("backtest.evaluate.build_asof_cohort", side_effect=mock_build_cohort):
        results_ev = evaluate([gt_entry], mock_detector, horizons=(3,))
    h3 = results_ev["per_horizon"][3]
    assert h3["caught"] == 1, f"caught={h3['caught']} expected 1"
    assert h3["recall"] == 1.0, f"recall={h3['recall']} expected 1.0"
    print(f"    caught={h3['caught']}, recall={h3['recall']}")

check("evaluate() synthetic detector recall=1.0", test_evaluate_synthetic)

# ── 4. ground_truth.py structure check ───────────────────────────────────────
print("\n=== 4. ground_truth.py structure check ===")

from backtest.ground_truth import GROUND_TRUTH

def test_gt_structure():
    errors = []
    for g in GROUND_TRUTH:
        name = g.get("startup", "?")
        if "startup" not in g:
            errors.append(f"{name}: missing startup")
        if "announce_date" not in g:
            errors.append(f"{name}: missing announce_date")
        else:
            try:
                date.fromisoformat(g["announce_date"])
            except ValueError as e:
                errors.append(f"{name}: announce_date parse error: {e}")
        if "prior_employer_name" not in g:
            errors.append(f"{name}: missing prior_employer_name")
        url = g.get("prior_employer_linkedin_url", "")
        if not url.startswith("https://www.linkedin.com/company/"):
            errors.append(f"{name}: prior_employer_linkedin_url invalid: {url!r}")
        founders = g.get("founder_profile_urls", [])
        if len(founders) < 2:
            errors.append(f"{name}: founder_profile_urls has <2 entries: {founders}")
    if errors:
        raise AssertionError("\n    ".join(errors))
    print(f"    {len(GROUND_TRUTH)} entries all valid")

check("ground_truth structure valid", test_gt_structure)

# ── 5. _detector from evaluate.__main__ handles empty list ───────────────────
print("\n=== 5. _detector handles empty people list ===")

def test_inline_detector_empty():
    # Import the modules _detector depends on without executing __main__
    import importlib, types

    # We need to extract _detector without running the evaluate script's
    # if __name__=="__main__" block. We do this by importing its dependencies
    # and reconstructing it inline here (same logic as in evaluate.py).
    from detect.leavers import is_leaver
    from detect.signals import tag
    from detect.cluster import strong_clusters, medium_clusters
    from score.model import cluster_features, score_clusters, tier

    def _detector(people, anchor_name=None):
        leavers = [p for p in people if is_leaver(p, anchor_name=anchor_name)]
        tags = {p.profile_url: tag(p) for p in leavers}
        strong = strong_clusters(leavers)
        medium = medium_clusters(leavers, tags, anchor_name=anchor_name)
        all_clusters = [(g, "strong") for _, g in strong] + [(g, "medium") for g in medium]
        rows = [cluster_features(g, tags, anchor_name=anchor_name) for g, _ in all_clusters]
        scores = score_clusters(rows)
        ranked = []
        for i, (g, _kind) in enumerate(all_clusters):
            s = float(scores[i]) if len(scores) > i else 0.0
            t = tier(s)
            ranked.append((g, s, t, rows[i]))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    result = _detector([], anchor_name="Stripe")
    assert result == [], f"expected [], got {result}"
    print("    _detector([]) returned [] without crash")

check("_detector handles empty list", test_inline_detector_empty)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n=== Summary ===")
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
for name, ok, err in results:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" → {err}" if err else ""))
print(f"\n{passed}/{passed+failed} checks passed")
sys.exit(0 if failed == 0 else 1)
