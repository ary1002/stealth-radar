# Stealth Radar — API Feasibility Report

### Summary verdict: **Core pipeline is feasible with one critical fix needed**

---

## ✅ What Works (Confirmed)

| Feature | Test | Result |
|---|---|---|
| Past-employer cohort pull by name | `past_employers.name [.] "Stripe"` | 13,885 results, paginated |
| Past-employer filter by company_id | `past_employers.company_id = 631394` | ✅ exact match, 499 recently-changed |
| **Anchor by LinkedIn URL** | `past_employers.company_linkedin_profile_url = "https://www.linkedin.com/company/stripe"` | ✅ 11,000 results |
| Leaver date filter (`=>`) | `past_employers.end_date => "2024-11-01"` | ✅ works, correct operator spelling |
| Tiny-destination filter (`=<`) | `current_employers.company_headcount_latest =< 25` | ✅ works |
| `recently_changed_jobs` as boolean filter | `recently_changed_jobs = true` | ✅ works |
| `past_employers.start_date` / `.end_date` fields | requested via `fields=` | ✅ both present |
| `current_employers.company_id` / `.headcount_latest` | in response | ✅ |
| `open_to_cards` field | in response | ✅ (array, often empty) |
| `current_employers.start_date` sort | `sorts: [{column, order}]` | ✅ |
| `next_cursor` pagination | second page fetched | ✅ |
| Company identify (name → company_id) | Stripe | ✅ company_id: 631394 |
| Company enrich | by company_id | ✅ |
| `company_id = 0` for unindexed/stealth companies | observed in results | ✅ consistent with spec |

---

## 🚨 Critical Discrepancy — Fix Required Before Coding

**The spec's `detect/parse.py` and `ingestion/cohort.py` use wrong field names.** The spec was written assuming a nested REST API schema that doesn't match actual Crustdata response structure.

**Spec assumes:**
```python
# cohort.py — these field names don't exist
"experience.employment_details.current"
"experience.employment_details.past"
"professional_network.open_to_cards"
"basic_profile.name"
"social_handles.professional_network_identifier.profile_url"

# cohort_filter — wrong field path
"field": "experience.employment_details.past.company_professional_network_profile_url"
```

**Actual API returns (flat structure):**
```python
raw["name"]                                    # not basic_profile.name
raw["headline"]
raw["linkedin_profile_url"]                    # not social_handles.professional_network_identifier.profile_url
raw["recently_changed_jobs"]
raw["open_to_cards"]                           # not professional_network.open_to_cards
raw["current_employers"][0]["name"]            # not experience.employment_details.current
raw["current_employers"][0]["title"]
raw["current_employers"][0]["start_date"]
raw["current_employers"][0]["company_id"]
raw["current_employers"][0]["company_headcount_latest"]
raw["past_employers"][0]["name"]               # not experience.employment_details.past
raw["past_employers"][0]["start_date"]
raw["past_employers"][0]["end_date"]
raw["past_employers"][0]["company_id"]
raw["past_employers"][0]["company_linkedin_profile_url"]
```

The **filter field name** also needs fixing:
- Spec: `experience.employment_details.past.company_professional_network_profile_url`
- Actual: `past_employers.company_linkedin_profile_url`

The filter key is **`column`** (not `field` as the spec uses in `cohort_filter()`).

---

## ⚠️ Moderate Issues

**`open_to_cards` is very sparse.** All 15 tested profiles returned `open_to_cards: []`. The `"CAREER_INTEREST"` signal the spec relies on will fire rarely — treat it as a weak bonus signal, not a primary filter.

**`company_id = 0`** for unindexed/stealth companies is expected behavior. The medium-cluster path handles this correctly. ~10-15% of past employer entries have `company_id: 0`.

**Cohort size for large anchors is significant.** Stripe alone has 13,885 past-employer matches. With `limit=1000` per page that's ~14 API calls (~42 credits just for the cohort pull). Budget scope to 1-2 anchors per demo run.

---

## Required Changes to `detect/parse.py`

The parser needs a complete rewrite to match the flat response structure:

```python
def _roles_from_flat(raw: dict) -> list[Role]:
    roles = []
    for c in (raw.get("current_employers") or []):
        roles.append(Role(
            company_id=c.get("company_id"),
            company_name=c.get("name"),
            title=c.get("title"),
            start_date=_d(c.get("start_date")),
            end_date=None,
            headcount_latest=c.get("company_headcount_latest"),
        ))
    for p in (raw.get("past_employers") or []):
        roles.append(Role(
            company_id=p.get("company_id"),
            company_name=p.get("name"),
            title=None,   # past_employers has no title field
            start_date=_d(p.get("start_date")),
            end_date=_d(p.get("end_date")),
            headcount_latest=p.get("company_headcount_latest"),
        ))
    return roles

def parse_person(raw: dict) -> Person:
    return Person(
        profile_url=raw.get("linkedin_profile_url", ""),
        name=raw.get("name", ""),
        headline=raw.get("headline", "") or "",
        country=raw.get("location_country"),
        open_to=raw.get("open_to_cards") or [],
        recently_changed=bool(raw.get("recently_changed_jobs")),
        schools=[],   # need education_background.institute_name in fields
        roles=_roles_from_flat(raw),
        updated_at=None,
    )
```

Also fix `cohort_filter()` and `COHORT_FIELDS` in `ingestion/cohort.py` to use the correct column names and request the right fields.

---

## Everything Else Is Go

The scoring model, clustering logic, leaver detection, backtest, Claude adjudication, DuckDB schema, and TalentFlow graph are all internally correct — they operate on the parsed `Person`/`Role` objects, so once parse.py is fixed everything downstream works as designed.

**Next step:** rewrite `ingestion/cohort.py` (fields list + filter key) and `detect/parse.py` (flat → dataclass mapping), then the pipeline is ready to implement.
