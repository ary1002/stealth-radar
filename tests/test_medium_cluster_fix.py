"""Regression tests for medium-cluster co-tenure/same-destination gate."""
from datetime import date
from detect.model import Person, Role
from detect.cluster import medium_clusters
from detect.signals import tag

def _person(name, company_id, co_name, anchor_start, anchor_end, current_co_id, current_co_name, current_hc=5):
    return Person(
        profile_url=f"https://linkedin.com/in/{name.lower().replace(' ','-')}",
        name=name, headline="Founder at Stealth Startup", country=None,
        open_to=[], recently_changed=True, schools=[], updated_at=None,
        roles=[
            Role(company_id=current_co_id, company_name=current_co_name, title="Co-Founder",
                 start_date=date(2024,1,1), end_date=None, headcount_latest=current_hc),
            Role(company_id=999, company_name="AnchorCo", title="Engineer",
                 start_date=anchor_start, end_date=anchor_end),
        ],
    )

def test_diff_dest_low_cotenure_no_cluster():
    """Francis+Jesse pattern: diff destinations, overlap < 6mo → NO cluster."""
    # overlap: Feb 2023 - Jul 2023 = 5 months
    francis = _person("Francis Z", 111, "Core Automation",
                      date(2021,3,1), date(2023,7,1),
                      current_co_id=2001, current_co_name="Core Automation")
    jesse   = _person("Jesse Bray", 222, "Hoplon",
                      date(2023,2,1), date(2023,10,1),
                      current_co_id=2002, current_co_name="Hoplon")
    tags = {p.profile_url: tag(p) for p in [francis, jesse]}
    result = medium_clusters([francis, jesse], tags, anchor_name="AnchorCo")
    assert result == [], f"Expected no cluster, got {result}"

def test_same_dest_low_cotenure_clusters():
    """Same stealth destination with minimal co-tenure → cluster forms."""
    alice = _person("Alice", 111, "Stealth",
                    date(2023,1,1), date(2024,1,1),
                    current_co_id=3001, current_co_name="Stealth Co")
    bob   = _person("Bob", 222, "Stealth",
                    date(2023,9,1), date(2024,2,1),
                    current_co_id=3001, current_co_name="Stealth Co")
    tags = {p.profile_url: tag(p) for p in [alice, bob]}
    result = medium_clusters([alice, bob], tags, anchor_name="AnchorCo")
    assert len(result) == 1, f"Expected 1 cluster (same dest), got {result}"

def test_strong_cotenure_diff_dest_clusters():
    """6+ months co-tenure + tight window + diff destinations → cluster forms."""
    alice = _person("Alice", 111, "StealthA",
                    date(2022,1,1), date(2024,1,1),
                    current_co_id=4001, current_co_name="StealthA")
    bob   = _person("Bob", 222, "StealthB",
                    date(2022,3,1), date(2024,2,1),
                    current_co_id=4002, current_co_name="StealthB")
    # overlap: Mar 2022 - Jan 2024 = 22 months ≥ 6
    tags = {p.profile_url: tag(p) for p in [alice, bob]}
    result = medium_clusters([alice, bob], tags, anchor_name="AnchorCo")
    assert len(result) == 1, f"Expected 1 cluster (strong co-tenure), got {result}"

def test_borderline_cotenure_just_under_threshold_no_cluster():
    """5 months co-tenure + diff destinations → NO cluster."""
    alice = _person("Alice", 111, "StealthA",
                    date(2023,1,1), date(2024,1,1),
                    current_co_id=5001, current_co_name="StealthA")
    bob   = _person("Bob", 222, "StealthB",
                    date(2023,8,1), date(2024,2,1),
                    current_co_id=5002, current_co_name="StealthB")
    # overlap: Aug 2023 - Jan 2024 = 5 months < 6
    tags = {p.profile_url: tag(p) for p in [alice, bob]}
    result = medium_clusters([alice, bob], tags, anchor_name="AnchorCo")
    assert result == [], f"Expected no cluster (5mo < 6mo threshold), got {result}"
