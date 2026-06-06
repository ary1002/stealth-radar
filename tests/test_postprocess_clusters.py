"""Tests for postprocess_clusters: member dedup (Rule 1) and cluster merging (Rule 2)."""
from datetime import date
from detect.model import Person, Role
from detect.cluster import postprocess_clusters, deduplicate_members, merge_overlapping_clusters
from config import MIN_CLUSTER_SIZE


def _person(name, dest_co=None, dest_id=None, anchor_start=None, anchor_end=None, url=None):
    roles = []
    if dest_co or dest_id:
        roles.append(Role(
            company_id=dest_id or 0, company_name=dest_co or "Dest",
            title="Founder", start_date=date(2024, 1, 1), end_date=None, headcount_latest=5,
        ))
    if anchor_start:
        roles.append(Role(
            company_id=999, company_name="AnchorCo", title="Engineer",
            start_date=anchor_start, end_date=anchor_end or date(2023, 12, 1),
        ))
    return Person(
        profile_url=url or f"https://linkedin.com/in/{name.lower().replace(' ','-')}",
        name=name, headline="", country=None, open_to=[], recently_changed=True,
        schools=[], updated_at=None, roles=roles,
    )


# ── Rule 1: dedup members ─────────────────────────────────────────────────────

def test_dedup_assigns_person_to_larger_cluster():
    """Shared member goes to the bigger cluster."""
    shared = _person("Shared", dest_co="Co1", dest_id=1, url="https://linkedin.com/in/shared")
    a1 = _person("A1", dest_co="Co1", dest_id=1)
    a2 = _person("A2", dest_co="Co1", dest_id=1)
    b1 = _person("B1", dest_co="Co2", dest_id=2)

    cluster_big   = [shared, a1, a2]   # 3 members
    cluster_small = [shared, b1]        # 2 members — shared also here

    result = deduplicate_members([cluster_big, cluster_small], anchor_name="AnchorCo")
    # shared should stay in cluster_big (larger); cluster_small drops to 1 member → removed
    assert len(result) == 1, f"Expected 1 cluster after dedup, got {len(result)}"
    assert all(p.name != "Shared" or p in result[0] for p in result[0])
    urls_in_result = {p.profile_url for c in result for p in c}
    # shared is only in one cluster
    assert sum(1 for c in result for p in c if p.profile_url == shared.profile_url) == 1


def test_dedup_prefers_higher_co_tenure():
    """Shared member goes to cluster with better co-tenure, not just larger."""
    shared = _person("Shared", anchor_start=date(2021, 1, 1), anchor_end=date(2023, 6, 1),
                     url="https://linkedin.com/in/shared")
    # cluster A: shared + someone with long overlap
    long_overlap = _person("LongTenure", anchor_start=date(2020, 1, 1), anchor_end=date(2023, 6, 1))
    # cluster B: shared + someone with no overlap (joined after shared left)
    no_overlap = _person("NoTenure", anchor_start=date(2024, 1, 1), anchor_end=date(2024, 6, 1))

    cluster_a = [shared, long_overlap]   # high co-tenure
    cluster_b = [shared, no_overlap]     # zero co-tenure

    result = deduplicate_members([cluster_a, cluster_b], anchor_name="AnchorCo")
    surviving = [c for c in result if any(p.profile_url == shared.profile_url for p in c)]
    assert len(surviving) == 1, "Shared person should appear in exactly one cluster"
    assert any(p.name == "LongTenure" for p in surviving[0]), \
        "Shared person should be in the high-co-tenure cluster"


def test_dedup_no_duplicates_unchanged():
    """Clusters with no shared members are returned unchanged."""
    c1 = [_person("A"), _person("B")]
    c2 = [_person("C"), _person("D")]
    result = deduplicate_members([c1, c2])
    assert len(result) == 2


# ── Rule 2: merge overlapping clusters ────────────────────────────────────────

def test_merge_above_50pct_overlap():
    """Two clusters sharing >50% members → merged into one."""
    shared1 = _person("S1", url="https://linkedin.com/in/s1")
    shared2 = _person("S2", url="https://linkedin.com/in/s2")
    unique1 = _person("U1", url="https://linkedin.com/in/u1")
    unique2 = _person("U2", url="https://linkedin.com/in/u2")

    c1 = [shared1, shared2, unique1]   # 3 members, 2 shared
    c2 = [shared1, shared2, unique2]   # 3 members, 2 shared — overlap = 2/3 > 50%

    result = merge_overlapping_clusters([c1, c2])
    assert len(result) == 1, f"Expected 1 merged cluster, got {len(result)}"
    merged_urls = {p.profile_url for p in result[0]}
    assert len(merged_urls) == 4, "Merged cluster should have 4 unique members"


def test_no_merge_below_50pct_overlap():
    """Two clusters with ≤50% overlap stay separate."""
    shared = _person("S1", url="https://linkedin.com/in/s1")
    c1 = [shared, _person("A"), _person("B"), _person("C")]  # 4 members, 1 shared → 25%
    c2 = [shared, _person("D"), _person("E"), _person("F")]

    result = merge_overlapping_clusters([c1, c2])
    assert len(result) == 2, "Clusters with 25% overlap should not be merged"


def test_merge_exactly_50pct_not_merged():
    """Exactly 50% overlap (not strictly greater) → not merged."""
    shared = _person("S1", url="https://linkedin.com/in/s1")
    c1 = [shared, _person("A")]   # 2 members, 1 shared → exactly 50%
    c2 = [shared, _person("B")]

    result = merge_overlapping_clusters([c1, c2])
    assert len(result) == 2, "Exactly 50% overlap should not be merged (>50% required)"


def test_postprocess_end_to_end():
    """Full pipeline: dedup then merge on a realistic scenario."""
    # Three stealth leavers from the same anchor
    alice = _person("Alice", dest_co="StealthA", dest_id=101,
                    anchor_start=date(2021,1,1), anchor_end=date(2023,6,1),
                    url="https://linkedin.com/in/alice")
    bob   = _person("Bob",   dest_co="StealthA", dest_id=101,
                    anchor_start=date(2021,3,1), anchor_end=date(2023,7,1),
                    url="https://linkedin.com/in/bob")
    carol = _person("Carol", dest_co="StealthA", dest_id=101,
                    anchor_start=date(2021,6,1), anchor_end=date(2023,9,1),
                    url="https://linkedin.com/in/carol")

    # Two overlapping clusters formed before postprocessing
    c1 = [alice, bob]
    c2 = [bob, carol]   # bob in both; c1+c2 share >50%

    result = postprocess_clusters([c1, c2], anchor_name="AnchorCo")
    # After dedup: bob stays in the cluster with better co-tenure; after merge: one cluster
    assert len(result) == 1, f"Expected 1 cluster after full postprocessing, got {len(result)}"
    all_urls = {p.profile_url for p in result[0]}
    assert len(all_urls) >= 2, "Merged cluster should have at least 2 members"
