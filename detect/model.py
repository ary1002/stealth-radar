from dataclasses import dataclass, field
from datetime import date


@dataclass
class Role:
    company_id: int | None
    company_name: str | None
    title: str | None
    start_date: date | None
    end_date: date | None        # None means current
    headcount_latest: int | None = None
    function: str | None = None
    seniority: str | None = None


@dataclass
class Person:
    profile_url: str
    name: str
    headline: str
    country: str | None
    open_to: list[str]
    recently_changed: bool
    schools: list[str]
    roles: list[Role]
    updated_at: str | None
    city: str | None = None      # from basic_profile.location.city

    @property
    def current_role(self) -> Role | None:
        cur = [r for r in self.roles if r.end_date is None]
        cur.sort(key=lambda r: (r.start_date or date.min), reverse=True)
        return cur[0] if cur else None
