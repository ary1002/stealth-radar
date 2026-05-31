"""Tests for backtest/asof.py — role_active_at() and asof_person()."""
import pytest
from datetime import date
from detect.model import Person, Role
from backtest.asof import role_active_at, asof_person


def make_role(company_id, start_date, end_date):
    return Role(company_id=company_id, company_name=f"Co{company_id}", title="SWE",
                start_date=start_date, end_date=end_date)


def make_person(roles):
    return Person(
        profile_url="https://linkedin.com/in/test",
        name="Test",
        headline="",
        country="US",
        open_to=[],
        recently_changed=False,
        schools=[],
        roles=roles,
        updated_at=None,
    )


# ---------------------------------------------------------------------------
# role_active_at
# ---------------------------------------------------------------------------

def test_role_active_at_started_before_t_and_ends_after_t():
    role = make_role(1, date(2020, 1, 1), date(2025, 1, 1))
    p = make_person([role])
    result = role_active_at(p, date(2022, 6, 1))
    assert result is role


def test_role_active_at_started_exactly_on_t_no_end_date():
    t = date(2022, 6, 1)
    role = make_role(1, t, None)
    p = make_person([role])
    assert role_active_at(p, t) is role


def test_role_active_at_ended_exactly_on_t_not_active():
    """end_date == t → condition end_date > t is False → not active."""
    t = date(2022, 6, 1)
    role = make_role(1, date(2020, 1, 1), t)
    p = make_person([role])
    assert role_active_at(p, t) is None


def test_role_active_at_no_roles_returns_none():
    p = make_person([])
    assert role_active_at(p, date(2022, 1, 1)) is None


# ---------------------------------------------------------------------------
# asof_person
# ---------------------------------------------------------------------------

def test_asof_person_strips_role_starting_after_t():
    past_role = make_role(1, date(2019, 1, 1), date(2021, 1, 1))
    future_role = make_role(2, date(2025, 1, 1), None)
    p = make_person([past_role, future_role])
    result = asof_person(p, date(2022, 1, 1))
    company_ids = [r.company_id for r in result.roles]
    assert 2 not in company_ids
    assert 1 in company_ids


def test_asof_person_active_role_at_t_has_end_date_none():
    active = make_role(1, date(2020, 1, 1), None)
    p = make_person([active])
    t = date(2022, 6, 1)
    result = asof_person(p, t)
    assert result.roles[0].end_date is None


def test_asof_person_future_current_role_becomes_past():
    """A role that starts after t (so it shouldn't appear in result at all) is stripped."""
    past = make_role(1, date(2018, 1, 1), date(2021, 1, 1))
    # "current" role that actually starts after t
    future_current = make_role(2, date(2025, 1, 1), None)
    p = make_person([past, future_current])
    t = date(2022, 1, 1)
    result = asof_person(p, t)
    # Only past role should remain; future current stripped
    assert len(result.roles) == 1
    assert result.roles[0].company_id == 1


def test_asof_person_does_not_mutate_original():
    """asof_person must deep-copy — original roles unchanged."""
    active = make_role(1, date(2020, 1, 1), None)
    p = make_person([active])
    t = date(2022, 6, 1)
    result = asof_person(p, t)
    # Mutate result
    result.roles[0].company_id = 999
    # Original should be unaffected
    assert p.roles[0].company_id == 1
