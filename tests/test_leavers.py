"""Tests for detect/leavers.py — is_leaver() and anchor_role()."""
import pytest
from datetime import date
from detect.model import Person, Role
from detect.leavers import anchor_role, is_leaver


def make_person(roles, recently_changed=False):
    return Person(
        profile_url="https://linkedin.com/in/test",
        name="Test User",
        headline="Engineer",
        country="US",
        open_to=[],
        recently_changed=recently_changed,
        schools=[],
        roles=roles,
        updated_at=None,
    )


ANCHOR_ID = 42
ANCHOR_NAME = "Acme Corp"


# ---------------------------------------------------------------------------
# anchor_role tests
# ---------------------------------------------------------------------------

def test_anchor_role_matches_by_company_id():
    role = Role(company_id=ANCHOR_ID, company_name="Something Else", title="SWE",
                start_date=date(2020, 1, 1), end_date=date(2022, 1, 1))
    p = make_person([role])
    assert anchor_role(p, anchor_id=ANCHOR_ID) is role


def test_anchor_role_matches_by_name_substring_case_insensitive():
    role = Role(company_id=99, company_name="ACME CORPORATION INC", title="PM",
                start_date=date(2020, 1, 1), end_date=date(2022, 1, 1))
    p = make_person([role])
    assert anchor_role(p, anchor_name="acme corp") is role


def test_anchor_role_returns_none_when_anchor_never_in_past_roles():
    role = Role(company_id=99, company_name="Other Co", title="PM",
                start_date=date(2020, 1, 1), end_date=date(2022, 1, 1))
    p = make_person([role])
    assert anchor_role(p, anchor_id=ANCHOR_ID) is None


def test_anchor_role_skips_current_role():
    """A role with end_date=None (still current) should NOT count as a past anchor role."""
    role = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                start_date=date(2021, 1, 1), end_date=None)
    p = make_person([role])
    assert anchor_role(p, anchor_id=ANCHOR_ID) is None


# ---------------------------------------------------------------------------
# is_leaver tests
# ---------------------------------------------------------------------------

def test_is_leaver_false_when_still_employed_at_anchor():
    """Person still at anchor (end_date=None) → anchor_role returns None → is_leaver False."""
    current_anchor = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                          start_date=date(2021, 1, 1), end_date=None)
    p = make_person([current_anchor])
    assert is_leaver(p, anchor_id=ANCHOR_ID, asof=date(2024, 1, 1)) is False


def test_is_leaver_true_left_6_months_ago_new_role_5_months_ago():
    """Left anchor 6 months ago, current role started 5 months ago → is_leaver True."""
    asof = date(2024, 6, 1)
    anchor = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                  start_date=date(2020, 1, 1), end_date=date(2023, 12, 1))  # ~6mo ago
    new_role = Role(company_id=77, company_name="NewCo", title="Lead",
                    start_date=date(2024, 1, 1), end_date=None)  # ~5mo ago
    p = make_person([anchor, new_role])
    assert is_leaver(p, anchor_id=ANCHOR_ID, asof=asof) is True


def test_is_leaver_false_beyond_18_month_lookback():
    """Left anchor 24 months ago → beyond LEAVER_LOOKBACK_MONTHS → is_leaver False."""
    asof = date(2024, 6, 1)
    anchor = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                  start_date=date(2019, 1, 1), end_date=date(2022, 6, 1))  # 24 months ago
    new_role = Role(company_id=77, company_name="NewCo", title="Lead",
                    start_date=date(2022, 7, 1), end_date=None)  # started just after leaving
    p = make_person([anchor, new_role])
    # new_role.start_date (2022-07-01) is NOT >= asof - 18 months (2022-12-01)
    assert is_leaver(p, anchor_id=ANCHOR_ID, asof=asof) is False


def test_is_leaver_true_no_current_role_recently_changed_true():
    """Left anchor, no current role, recently_changed=True → is_leaver True."""
    anchor = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                  start_date=date(2020, 1, 1), end_date=date(2023, 12, 1))
    p = make_person([anchor], recently_changed=True)
    assert is_leaver(p, anchor_id=ANCHOR_ID, asof=date(2024, 6, 1)) is True


def test_is_leaver_false_no_current_role_recently_changed_false():
    """Left anchor, no current role, recently_changed=False → is_leaver False."""
    anchor = Role(company_id=ANCHOR_ID, company_name=ANCHOR_NAME, title="SWE",
                  start_date=date(2020, 1, 1), end_date=date(2023, 12, 1))
    p = make_person([anchor], recently_changed=False)
    assert is_leaver(p, anchor_id=ANCHOR_ID, asof=date(2024, 6, 1)) is False


def test_is_leaver_false_when_currently_at_anchor():
    """Person who left and rejoined the anchor should NOT be a leaver."""
    today = date.today()
    # Has a PAST Stripe role (left 2 years ago) AND a CURRENT Stripe role (rejoined recently)
    person = Person(
        profile_url="https://www.linkedin.com/in/test",
        name="Test", headline="",
        country=None, open_to=[], recently_changed=True,
        schools=[], updated_at=None,
        roles=[
            Role(company_id=631394, company_name="Stripe", title="Eng",
                 start_date=date(2019, 1, 1), end_date=date(2021, 6, 1)),   # past
            Role(company_id=9999, company_name="Other Co", title="Eng",
                 start_date=date(2021, 7, 1), end_date=date(2023, 12, 1)),  # past
            Role(company_id=631394, company_name="Stripe", title="Staff",
                 start_date=date(2024, 1, 1), end_date=None),               # current — at anchor
        ]
    )
    assert is_leaver(person, anchor_name="Stripe") is False
