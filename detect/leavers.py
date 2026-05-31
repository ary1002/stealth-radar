from datetime import date
from dateutil.relativedelta import relativedelta
from config import LEAVER_LOOKBACK_MONTHS


def anchor_role(person, anchor_id=None, anchor_name=None):
    """Person's PAST role at the anchor (end_date is not None)."""
    for r in person.roles:
        if r.end_date is None:
            continue
        if anchor_id and r.company_id == anchor_id:
            return r
        if anchor_name and r.company_name and anchor_name.lower() in r.company_name.lower():
            return r
    return None


def is_leaver(person, anchor_id=None, anchor_name=None, asof: date | None = None) -> bool:
    # If person is currently at the anchor, they are not a leaver.
    for r in person.roles:
        if r.end_date is not None:
            continue  # past role, skip
        # This is a current role (end_date=None)
        if anchor_id and r.company_id == anchor_id:
            return False
        if anchor_name and r.company_name and anchor_name.lower() in r.company_name.lower():
            return False

    asof = asof or date.today()
    ar = anchor_role(person, anchor_id, anchor_name)
    if not ar:
        return False
    cur = person.current_role
    if not cur or not cur.start_date:
        return person.recently_changed
    return cur.start_date >= asof - relativedelta(months=LEAVER_LOOKBACK_MONTHS)
