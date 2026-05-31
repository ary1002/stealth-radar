import copy
from datetime import date


def role_active_at(person, t: date):
    """Role the person held at date t (started <= t AND (end_date is None OR end_date > t))."""
    active = [r for r in person.roles
              if r.start_date and r.start_date <= t
              and (r.end_date is None or r.end_date > t)]
    active.sort(key=lambda r: r.start_date, reverse=True)
    return active[0] if active else None


def asof_person(person, t: date):
    """View of person as they appeared at date t.
    - Strip roles starting after t
    - Role active at t → end_date = None (current)
    - All other past roles keep their end_date (or t if they had None)
    """
    p = copy.deepcopy(person)
    cur = role_active_at(person, t)
    p.roles = [r for r in p.roles if r.start_date and r.start_date <= t]
    for r in p.roles:
        is_cur = bool(cur and r.start_date == cur.start_date
                      and r.company_id == cur.company_id
                      and r.company_name == cur.company_name)
        r.end_date = None if is_cur else (r.end_date or t)
    return p
