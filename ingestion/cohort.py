from ingestion.client import CrustdataClient

# Nested field paths accepted by the raw REST API (list form).
# `recently_changed_jobs` is filter-only — not a returnable field.
# parse_person() normalises this nested response to flat before reading.
COHORT_FIELDS = [
    "basic_profile.name",
    "basic_profile.headline",
    "basic_profile.location.country",
    "social_handles.professional_network_identifier.profile_url",
    "professional_network.open_to_cards",
    "experience.employment_details.current",
    "experience.employment_details.past",
    "education.schools.school",
]

# Raw REST API uses "field" key (not "column"). Sortable fields use nested paths.
SORTS = [{"field": "experience.employment_details.start_date", "order": "desc"}]


def cohort_filter(anchor_linkedin_url=None, anchor_name=None) -> dict:
    # Raw REST API filter key is "field"; filter paths use nested experience.* schema.
    # COHORT_FIELDS uses flat names (current_employers.*) which controls the response shape.
    if anchor_linkedin_url:
        return {
            "field": "experience.employment_details.past.company_professional_network_profile_url",
            "type": "=",
            "value": anchor_linkedin_url,
        }
    return {
        "field": "experience.employment_details.past.company_name",
        "type": "=",
        "value": anchor_name,
    }


async def pull_cohort(client: CrustdataClient, anchor_linkedin_url=None, anchor_name=None) -> list[dict]:
    filt = cohort_filter(anchor_linkedin_url=anchor_linkedin_url, anchor_name=anchor_name)
    people = []
    async for page in client.person_search(filters=filt, fields=COHORT_FIELDS, sorts=SORTS):
        people.extend(page)
    return people
