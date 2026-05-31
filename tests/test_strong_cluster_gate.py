"""Regression tests for strong_clusters — demotion model.

strong_clusters() now groups ALL shared company_ids (excluding id=0).
Headcount gating (demotion to Medium, override of forming_team label)
is handled downstream in main.py and backtest/evaluate.py.
"""
from datetime import date
from detect.model import Person, Role
from detect.cluster import strong_clusters


def _person(name, company_id, headcount, url=None):
    return Person(
        profile_url=url or f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}",
        name=name, headline="", country=None, open_to=[],
        recently_changed=True, schools=[], updated_at=None,
        roles=[
            Role(company_id=company_id, company_name="DestCo", title="Engineer",
                 start_date=date(2023, 1, 1), end_date=None,
                 headcount_latest=headcount),
            Role(company_id=999, company_name="PriorCo", title="Engineer",
                 start_date=date(2020, 1, 1), end_date=date(2022, 12, 1)),
        ],
    )


def test_large_employer_forms_cluster():
    """Two people sharing a 342k-headcount employer → cluster FORMS (demotion happens downstream)."""
    alice = _person("Alice", company_id=12345, headcount=342_000)
    bob   = _person("Bob",   company_id=12345, headcount=342_000)
    result = strong_clusters([alice, bob])
    assert len(result) == 1, \
        "Expected cluster to form for large-headcount destination (demotion is downstream)"
    cid, group = result[0]
    assert cid == 12345
    assert len(group) == 2


def test_small_employer_forms_cluster():
    """Two people sharing a <25-headcount employer → strong cluster forms."""
    alice = _person("Alice", company_id=77777, headcount=12)
    bob   = _person("Bob",   company_id=77777, headcount=12)
    result = strong_clusters([alice, bob])
    assert len(result) == 1, f"Expected 1 cluster, got {len(result)}"
    cid, group = result[0]
    assert cid == 77777
    assert len(group) == 2


def test_company_id_zero_gated_out():
    """Two people sharing company_id=0 (unresolved sentinel) → NO strong cluster."""
    alice = _person("Alice", company_id=0, headcount=5)
    bob   = _person("Bob",   company_id=0, headcount=5)
    assert strong_clusters([alice, bob]) == [], \
        "Expected no cluster for company_id=0"


def test_threshold_boundary_forms_cluster():
    """At-threshold (500) and below both form clusters — gating is downstream."""
    a1 = _person("A1", company_id=1111, headcount=500, url="https://www.linkedin.com/in/a1")
    b1 = _person("B1", company_id=1111, headcount=500, url="https://www.linkedin.com/in/b1")
    result = strong_clusters([a1, b1])
    assert len(result) == 1, "headcount=500 should still form a cluster (demotion downstream)"

    a2 = _person("A2", company_id=2222, headcount=499, url="https://www.linkedin.com/in/a2")
    b2 = _person("B2", company_id=2222, headcount=499, url="https://www.linkedin.com/in/b2")
    result2 = strong_clusters([a2, b2])
    assert len(result2) == 1, "headcount=499 should form a cluster"


def test_none_headcount_forms_cluster():
    """headcount_latest=None (unknown) is treated as eligible — don't gate unknowns."""
    alice = _person("Alice", company_id=88888, headcount=None)
    bob   = _person("Bob",   company_id=88888, headcount=None)
    result = strong_clusters([alice, bob])
    assert len(result) == 1, "None headcount should not be gated"
