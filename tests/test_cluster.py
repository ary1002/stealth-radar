"""Tests for detect/cluster.py — tenure_overlap_months(), strong_clusters(), medium_clusters()."""
import pytest
from datetime import date
from detect.model import Person, Role
from detect.cluster import tenure_overlap_months, strong_clusters, medium_clusters


ANCHOR_ID = 42
ANCHOR_NAME = "Acme Corp"


def make_role(company_id, company_name, start_date, end_date, **kwargs):
    return Role(company_id=company_id, company_name=company_name, title="SWE",
                start_date=start_date, end_date=end_date, **kwargs)


def make_person(url, roles, recently_changed=False):
    return Person(
        profile_url=url,
        name="Person",
        headline="",
        country="US",
        open_to=[],
        recently_changed=recently_changed,
        schools=[],
        roles=roles,
        updated_at=None,
    )


def default_tags(people):
    return {p.profile_url: {"stealth": True, "founder": False,
                             "tiny_destination": False, "open_to_career": False}
            for p in people}


# ---------------------------------------------------------------------------
# tenure_overlap_months
# ---------------------------------------------------------------------------

def test_tenure_overlap_both_open_ended_fully_overlapping():
    r1 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), None)
    r2 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 6, 1), None)
    assert tenure_overlap_months(r1, r2) > 0


def test_tenure_overlap_no_overlap_a_ended_before_b_started():
    r1 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2019, 1, 1), date(2020, 1, 1))
    r2 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2021, 1, 1), date(2022, 1, 1))
    assert tenure_overlap_months(r1, r2) == 0


def test_tenure_overlap_full_containment_b_within_a():
    r1 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2019, 1, 1), date(2023, 1, 1))
    r2 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), date(2022, 1, 1))
    # B's duration = 24 months; overlap should equal B's duration
    assert tenure_overlap_months(r1, r2) == 24


def test_tenure_overlap_both_start_date_none():
    r1 = make_role(ANCHOR_ID, ANCHOR_NAME, None, date(2022, 1, 1))
    r2 = make_role(ANCHOR_ID, ANCHOR_NAME, None, date(2022, 6, 1))
    assert tenure_overlap_months(r1, r2) == 0


# ---------------------------------------------------------------------------
# strong_clusters
# ---------------------------------------------------------------------------

def test_strong_clusters_three_people_same_company():
    dest = make_role(77, "NewCo", date(2024, 1, 1), None)
    anchor = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), date(2023, 12, 1))
    people = [make_person(f"url{i}", [anchor, dest]) for i in range(3)]
    clusters = strong_clusters(people)
    assert len(clusters) == 1
    cid, group = clusters[0]
    assert cid == 77
    assert len(group) == 3


def test_strong_clusters_company_id_zero_not_clustered():
    dest = make_role(0, "NewCo", date(2024, 1, 1), None)
    anchor = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), date(2023, 12, 1))
    people = [make_person(f"url{i}", [anchor, dest]) for i in range(3)]
    clusters = strong_clusters(people)
    # company_id=0 is falsy → not grouped
    assert clusters == []


def test_strong_clusters_single_person_not_returned():
    dest = make_role(77, "NewCo", date(2024, 1, 1), None)
    anchor = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), date(2023, 12, 1))
    people = [make_person("url0", [anchor, dest])]
    clusters = strong_clusters(people)
    assert clusters == []


# ---------------------------------------------------------------------------
# medium_clusters
# ---------------------------------------------------------------------------

def test_medium_clusters_two_stealth_leavers_overlapping_tenure_left_within_window():
    anchor1 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2020, 1, 1), date(2024, 1, 1))
    anchor2 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2021, 1, 1), date(2024, 2, 1))
    # No shared destination company_id
    p1 = make_person("url1", [anchor1])
    p2 = make_person("url2", [anchor2])
    tags = default_tags([p1, p2])
    clusters = medium_clusters([p1, p2], tags, anchor_id=ANCHOR_ID)
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_medium_clusters_no_tenure_overlap_not_clustered():
    # r1 ends before r2 starts → no overlap
    anchor1 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2018, 1, 1), date(2020, 1, 1))
    anchor2 = make_role(ANCHOR_ID, ANCHOR_NAME, date(2022, 1, 1), date(2022, 4, 1))
    p1 = make_person("url1", [anchor1])
    p2 = make_person("url2", [anchor2])
    tags = default_tags([p1, p2])
    clusters = medium_clusters([p1, p2], tags, anchor_id=ANCHOR_ID)
    assert clusters == []
