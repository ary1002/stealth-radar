# Crustdata API Usage Guide
 
> **Base URL:** `https://api.crustdata.com`  
> **API Version:** `2025-11-01`
 
---
 
## Universal Headers (Required on Every Request)
 
```http
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
x-api-version: 2025-11-01
```
 
---
 
## Credit Pricing Summary
 
| Endpoint | Cost |
|---|---|
| `/company/search` | 0.03 per result |
| `/company/enrich` | 2 per record |
| `/company/identify` | **Free** |
| `/company/search/autocomplete` | **Free** |
| `/person/search` | 0.03 per result |
| `/person/enrich` | 1–7 per record (additive) |
| `/person/search/autocomplete` | **Free** |
| `/job/search` | 0.03 per result |
| `/web/search/live` | 1 per query |
| `/web/enrich/live` | 1 per page |
| `/person/professional_network/enrich/live` *(enterprise)* | 7 per profile |
| `/person/professional_network/search/live` *(enterprise)* | 2 per profile |
| `/company/professional_network/search/live` *(enterprise)* | 2 per company |
 
**Person enrich is additive:**
 
| Add-on | +Credits |
|---|---|
| Base profile | 1 |
| Business email | +1 |
| Personal email | +2 |
| Phone | +2 |
| Developer platform data | +1 |
 
Credits expire 6 months from purchase. Search is billed per result returned — a zero-result query costs nothing.
 
---
 
## Rate Limits (Default)
 
| Endpoint | Limit |
|---|---|
| `/person/enrich` | 15 req/min |
| `/person/search` | 30 req/min |
| `/person/search/autocomplete` | 45 req/min |
| `/company/enrich` | 15 req/min |
| `/company/search` | 30 req/min |
| `/company/search/autocomplete` | 45 req/min |
| `/job/search` | 30 req/min |
| `/web/search/live` | 10 req/min |
| `/web/enrich/live` | 10 req/min |
| Live endpoints (person/company/job) | 10 req/min |
 
Use steady distribution — avoid burst sending. On `429`, retry with exponential backoff.
 
---
 
## Pagination (All Search Endpoints)
 
All search APIs use **cursor-based pagination**. Pass `next_cursor` from the response back as `cursor` in the next request. Keep `filters`, `sorts`, `fields`, and `limit` identical between pages. Stop when `next_cursor` is `null`.
 
```json
{
  "filters": { ... },
  "sorts": [{ "field": "crustdata_company_id", "order": "asc" }],
  "limit": 100,
  "cursor": "PASTE_NEXT_CURSOR_HERE"
}
```
 
⚠️ Always include `sorts` when paginating to guarantee stable ordering.
 
---
 
## Filter Operators (Company & Person Search)
 
| Operator | Meaning | Notes |
|---|---|---|
| `=` | Exact match | Case-insensitive for text |
| `!=` | Not equal | |
| `>` / `<` | Greater / less than | Numbers and dates |
| `=>` / `=<` | ≥ / ≤ | **Not** `>=` or `<=` |
| `in` | Value in list | `["USA","GBR"]` |
| `not_in` | Value not in list | |
| `is_null` / `is_not_null` | Null check | No `value` needed |
| `(.)` | Fuzzy / contains match | Tolerates typos, word variants |
| `[.]` | Exact token match | Case-sensitive, no typos |
| `(!)` | Fuzzy negation (Person only) | Opposite of `(.)` |
| `geo_distance` | Radius search (Person only) | See Person Search |
 
**Compound filters:**
```json
{
  "op": "and",
  "conditions": [
    { "field": "locations.country", "type": "=", "value": "USA" },
    { "field": "headcount.total", "type": "=>", "value": 100 }
  ]
}
```
 
---
 
## Company APIs
 
### 1. Company Search — `POST /company/search`
 
Search indexed companies with filters, sorting, and pagination.
 
**Cost:** 0.03 credits per result  
**Rate limit:** 30 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/company/search \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "filters": {
      "op": "and",
      "conditions": [
        { "field": "locations.country", "type": "=", "value": "USA" },
        { "field": "funding.total_investment_usd", "type": "=>", "value": 10000000 }
      ]
    },
    "fields": ["basic_info.name", "basic_info.primary_domain", "funding", "headcount"],
    "sorts": [{ "field": "funding.total_investment_usd", "order": "desc" }],
    "limit": 20
  }'
```
 
**Key filterable fields:**
 
| Field | Type | Notes |
|---|---|---|
| `basic_info.name` | string | Company name |
| `basic_info.primary_domain` | string | Website domain |
| `basic_info.year_founded` | integer | |
| `basic_info.company_type` | string | e.g., `"Privately Held"`, `"Public Company"` |
| `basic_info.industries` | string[] | |
| `locations.country` | string | ISO-3: `"USA"`, `"GBR"` |
| `headcount.total` | integer | |
| `headcount.growth_percent.1m/3m/6m/12m` | number | Filterable, not sortable |
| `funding.total_investment_usd` | number | |
| `funding.last_fundraise_date` | date | |
| `funding.last_round_type` | string | `"series_a"`, `"series_b"`, etc. |
| `funding.investors` | string[] | |
| `revenue.estimated.lower_bound_usd` | integer | |
| `followers.count` | integer | |
| `roles.distribution.<function>` | integer | e.g., `roles.distribution.engineering` |
 
**Sortable fields:** `basic_info.name`, `headcount.total`, `funding.total_investment_usd`, `funding.last_fundraise_date`, `followers.count`, `locations.country`, `basic_info.year_founded`, `updated_at`
 
**Response shape:**
```json
{
  "companies": [ { "basic_info": {}, "headcount": {}, "funding": {}, ... } ],
  "next_cursor": "...",
  "total_count": 1234
}
```
 
**Response sections (use in `fields`):** `basic_info`, `headcount`, `funding`, `locations`, `taxonomy`, `revenue`, `hiring`, `followers`, `social_profiles`
 
**Validation:**
- `limit`: 1–1000, default 20
- Omitting `filters` matches all companies — always filter in production
- Omitting `fields` returns all sections (large payload)
---
 
### 2. Company Enrich — `POST /company/enrich`
 
Get a full company profile including people, web traffic, news, reviews, and more.
 
**Cost:** 2 credits per record  
**Rate limit:** 15 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/company/enrich \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "domains": ["stripe.com", "retool.com"],
    "fields": ["basic_info", "headcount", "funding", "people", "news", "web_traffic"]
  }'
```
 
**Identifiers (use exactly one type):**
 
| Parameter | Type | Description |
|---|---|---|
| `domains` | string[] | Website domains |
| `names` | string[] | Company names |
| `professional_network_profile_urls` | string[] | Profile URLs |
| `crustdata_company_ids` | integer[] | Crustdata IDs |
 
**Optional params:**
 
| Parameter | Default | Notes |
|---|---|---|
| `fields` | `["basic_info"]` | List of section groups to return |
| `exact_match` | `null` | `true` for strict domain matching |
 
**Available `fields` groups:**
 
| Group | What you get |
|---|---|
| `basic_info` | Name, domain, website, profile URL, industry, type, year founded |
| `headcount` | Employee count, role/region breakdowns, growth metrics |
| `funding` | Total funding, last round, investors, milestones, acquisitions |
| `locations` | HQ address, all office addresses |
| `taxonomy` | Industry, categories, NAICS, SIC, specialities |
| `revenue` | Revenue estimates, public market data, acquisition status |
| `hiring` | Open job count, hiring growth, recent job titles |
| `followers` | Count and MoM/QoQ/YoY growth |
| `seo` | Organic results, monthly clicks, Google Ads budget |
| `competitors` | Competitor domains and SEO peers |
| `social_profiles` | Crunchbase, Twitter, profile links |
| `web_traffic` | Monthly visitors, traffic source breakdown |
| `employee_reviews` | Overall, culture, work-life balance ratings |
| `people` | Decision makers, founders, C-level executives |
| `news` | Recent article URLs, titles, publish dates |
| `software_reviews` | Review count and average rating |
| `public_launches` | Product launches, makers, reviews, ratings |
| `market_intel` | Product and review intelligence |
 
**Response shape:**
```json
[
  {
    "matched_on": "stripe.com",
    "match_type": "domain",
    "matches": [
      {
        "confidence_score": 1.0,
        "company_data": { "basic_info": {}, "headcount": {}, ... }
      }
    ]
  }
]
```
 
**Output validation:**
- Iterate the top-level array; check `matches.length > 0` before accessing `company_data`
- No match → `200` with `"matches": []` (handle alongside spec's `404`)
- Partial batch: some succeed, some return empty `matches` — all in the same `200`
---
 
### 3. Company Identify — `POST /company/identify`
 
Resolve a company from partial info and get confidence-ranked matches. Use before enrichment for entity resolution.
 
**Cost:** **Free**  
**Rate limit:** 30 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/company/identify \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "names": ["OpenAI"]
  }'
```
 
**Same identifier params as Enrich** (`domains`, `names`, `professional_network_profile_urls`, `crustdata_company_ids`). Use one type per request. Optional: `exact_match: true`.
 
**Response shape:** Same as Enrich — `[{ matched_on, match_type, matches: [{ confidence_score, company_data }] }]`
 
**Output validation:** No match returns `200` with `"matches": []`. Handle both `200` (empty) and `404`.
 
---
 
### 4. Company Autocomplete — `POST /company/search/autocomplete`
 
Discover valid filter values for Company Search fields (e.g., valid industry names, country codes, round types).
 
**Cost:** **Free**  
**Rate limit:** 45 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/company/search/autocomplete \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "field": "basic_info.industries",
    "value": "software"
  }'
```
 
Use the returned `value` directly as a filter value in `/company/search`. Especially useful for `basic_info.industries`, `taxonomy.professional_network_industry`, `locations.country`, `basic_info.company_type`, and `funding.last_round_type`.
 
---
 
## Person APIs
 
### 5. Person Search — `POST /person/search`
 
Search indexed person profiles with flexible filters on title, company, location, seniority, education, and more.
 
**Cost:** 0.03 credits per result  
**Rate limit:** 30 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/person/search \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "filters": {
      "op": "and",
      "conditions": [
        { "field": "experience.employment_details.current.title", "type": "(.)", "value": "VP|Director" },
        { "field": "experience.employment_details.current.company_name", "type": "in", "value": ["Stripe", "Plaid"] },
        { "field": "basic_profile.location.country", "type": "=", "value": "United States" }
      ]
    },
    "fields": ["basic_profile.name", "basic_profile.location", "experience", "contact"],
    "sorts": [{ "field": "professional_network.connections", "order": "desc" }],
    "limit": 25
  }'
```
 
**Key filterable fields:**
 
| Field | Notes |
|---|---|
| `basic_profile.name` | Full name |
| `basic_profile.location.country` | Full country name, e.g., `"United States"` |
| `basic_profile.location.city` / `.state` | Full names |
| `basic_profile.location.continent` | Full name, e.g., `"North America"` |
| `experience.employment_details.current.title` | Current job title |
| `experience.employment_details.current.company_name` | Current employer |
| `experience.employment_details.current.seniority_level` | Seniority |
| `experience.employment_details.current.company_headquarters_country` | ISO-3 code: `"USA"` |
| `experience.employment_details.past.company_name` | Past employer |
| `experience.employment_details.company_name` | Any employer (current + past) |
| `education.schools.school` | School name |
| `education.schools.degree` | Degree |
| `skills.professional_network_skills` | Skills (filterable only, not returned) |
| `years_of_experience_raw` | Total years of experience |
| `recently_changed_jobs` | Boolean |
| `professional_network.open_to_cards` | `"CAREER_INTEREST"`, `"HIRING_MANAGER"`, `"VOLUNTEERING"` |
 
⚠️ **Country format difference:** Use full country names (`"United States"`) for `basic_profile.location.country`, but ISO-3 codes (`"USA"`) for employer HQ country fields.
 
**Geo-distance filter:**
```json
{
  "field": "basic_profile.location",
  "type": "geo_distance",
  "value": {
    "location": "San Francisco, CA",
    "distance": 50,
    "unit": "km"
  }
}
```
 
**Post-processing (exclude known profiles):**
```json
{
  "post_processing": {
    "exclude_profiles": ["https://linkedin.com/in/someone"],
    "exclude_names": ["John Smith"]
  }
}
```
 
**Response shape:**
```json
{
  "profiles": [ { "basic_profile": {}, "experience": {}, "contact": {}, ... } ],
  "next_cursor": "...",
  "total_count": 450
}
```
 
**Response sections:** `basic_profile`, `experience`, `education`, `contact` (availability flags only), `social_handles`, `professional_network`
 
**Validation:**
- `limit`: 1–1000, default 20
- `skills` and `dev_platform_profiles` are not returned by search — use Enrich for those
- `contact` in search returns availability flags (`has_business_email`, `has_phone_number`), not actual contact data
---
 
### 6. Person Enrich — `POST /person/enrich`
 
Get full person profiles including contact data, developer platform data, and employment history from a LinkedIn URL or business email.
 
**Cost:** 1–7 credits per record (additive)  
**Rate limit:** 15 req/min
 
```bash
# By LinkedIn URL (base profile = 1 credit)
curl --request POST \
  --url https://api.crustdata.com/person/enrich \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "professional_network_profile_urls": [
      "https://www.linkedin.com/in/dvdhsu/"
    ],
    "fields": ["basic_profile", "experience", "contact", "dev_platform_profiles"]
  }'
 
# By business email (reverse lookup)
curl --request POST \
  --url https://api.crustdata.com/person/enrich \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "business_emails": ["founder@company.com"],
    "min_similarity_score": 0.8
  }'
```
 
**Identifiers (use exactly one type, max 25 per request):**
 
| Parameter | Description |
|---|---|
| `professional_network_profile_urls` | LinkedIn profile URLs |
| `business_emails` | Business email for reverse lookup |
 
**Optional params:**
 
| Parameter | Default | Notes |
|---|---|---|
| `fields` | All fields | Section groups or dot-paths to return |
| `min_similarity_score` | None | 0–1 confidence threshold for email lookups |
| `force_fetch` | `false` | Request fresh fetch path when supported |
| `enrich_realtime` | `false` | Request realtime enrich when supported |
 
**Available `fields` sections:**
 
| Section | What you get |
|---|---|
| `basic_profile` | Name, headline, current title, summary, location, languages |
| `professional_network` | Profile picture, connections, followers, open-to signals |
| `social_handles` | Twitter, dev platform, and other profile identifiers |
| `experience` | Full employment history (current + past) |
| `education` | Schools, degrees, fields of study |
| `skills` | Professional skills |
| `contact` | Business emails, personal emails, phone numbers, websites |
| `dev_platform_profiles` | GitHub/developer platform profiles, repos, orgs |
 
**Response shape:**
```json
[
  {
    "matched_on": "https://linkedin.com/in/dvdhsu/",
    "match_type": "professional_network_profile_url",
    "matches": [
      {
        "confidence_score": 1.0,
        "person_data": { "basic_profile": {}, "contact": {}, ... }
      }
    ]
  }
]
```
 
**Output validation:** Check `matches.length > 0` before accessing `person_data`. No match → `200` with `"matches": []`.
 
---
 
### 7. Person Autocomplete — `POST /person/search/autocomplete`
 
Discover valid indexed values for Person Search filters.
 
**Cost:** **Free**  
**Rate limit:** 45 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/person/search/autocomplete \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "field": "basic_profile.location.country",
    "value": "ind"
  }'
```
 
Feed the returned `value` verbatim into a `/person/search` filter. Use to discover exact indexed labels for country, industry, seniority, and function fields before building queries.
 
---
 
## Job APIs
 
### 8. Job Search — `POST /job/search`
 
Search indexed job listings with filters on title, company, location, and firmographics. Supports aggregations for hiring analysis.
 
**Cost:** 0.03 credits per result  
**Rate limit:** 30 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/job/search \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "filters": {
      "op": "and",
      "conditions": [
        { "field": "job_details.title", "type": "(.)", "value": "Software Engineer" },
        { "field": "job_details.workplace_type", "type": "=", "value": "Remote" },
        { "field": "company.funding.total_investment_usd", "type": "=>", "value": 50000000 },
        { "field": "metadata.date_added", "type": "=>", "value": "2026-01-01" }
      ]
    },
    "fields": [
      "job_details.title", "job_details.url", "job_details.workplace_type",
      "company.basic_info.name", "company.basic_info.primary_domain",
      "location.raw", "metadata.date_added"
    ],
    "sorts": [{ "field": "metadata.date_added", "order": "desc" }],
    "limit": 25
  }'
```
 
**Key filterable fields:**
 
| Field | Examples |
|---|---|
| `job_details.title` | `"Software Engineer"` |
| `job_details.category` | `"Engineering"`, `"Sales"`, `"Operations"`, `"Others"` |
| `job_details.workplace_type` | `"Remote"`, `"Hybrid"`, `"On-site"`, `""` |
| `job_details.reposted_job` | `true` / `false` |
| `content.description` | Full JD text — use `(.)` for keyword search |
| `company.basic_info.company_id` | Crustdata company ID |
| `company.basic_info.name` | Company name |
| `company.basic_info.primary_domain` | Domain |
| `company.basic_info.industries` | Industry array |
| `company.headcount.total` | Employee count |
| `company.headcount.range` | e.g., `"5001-10000"` |
| `company.followers.count` | Follower count |
| `company.funding.total_investment_usd` | Total funding |
| `company.funding.last_fundraise_date` | Last funding date |
| `company.funding.last_round_type` | Round type |
| `company.revenue.estimated.lower_bound_usd` | Revenue lower bound |
| `company.locations.country` | HQ country |
| `location.country` | Job location country |
| `location.city` / `.state` | Job location |
| `metadata.date_added` | When Crustdata indexed it |
| `metadata.date_updated` | Last refresh |
 
**Sortable fields:** `metadata.date_added`, `metadata.date_updated`, `company.headcount.total`, `company.followers.count`, `company.revenue.estimated.lower_bound_usd`, `company.funding.total_investment_usd`, `company.funding.valuation_usd`, `company.funding.last_fundraise_date`, `company.funding.num_funding_rounds`
 
**⚠️ Filter gotchas:**
- AND on the same field with `=` always returns zero (e.g., `title = "SWE" AND title = "AE"` is impossible for one listing). Use `(.)` for multi-word matching, or run two queries and intersect company IDs client-side.
- `is_null` / `is_not_null` are not implemented — filter null presence client-side.
- `location.country` can appear as full names, ISO short forms, or variants; use `in` with multiple variants or run a `group_by` to discover exact values.
**Aggregations:**
 
Set `limit: 0` when you only want aggregation output.
 
```bash
# Count total Engineering jobs
curl ... --data '{
  "filters": { "field": "job_details.category", "type": "=", "value": "Engineering" },
  "limit": 0,
  "aggregations": [{ "type": "count" }]
}'
 
# Top hiring companies for a role
curl ... --data '{
  "filters": { "field": "job_details.title", "type": "=", "value": "Account Executive" },
  "limit": 0,
  "aggregations": [{
    "type": "group_by",
    "column": "company.basic_info.company_id",
    "agg": "count",
    "size": 10
  }]
}'
```
 
**Groupable columns:** `company.basic_info.company_id`, `company.basic_info.industries`, `company.basic_info.primary_domain`, `company.funding.last_round_type`, `company.headcount.range`, `company.locations.country`, `job_details.category`, `job_details.title`, `job_details.workplace_type`, `location.country`
 
**Response shape:**
```json
{
  "job_listings": [ { "crustdata_job_id": 41053563, "job_details": {}, "company": {}, "location": {}, "content": {}, "metadata": {} } ],
  "next_cursor": "...",
  "total_count": 4354217,
  "aggregations": []
}
```
 
**Output validation:**
- Use `crustdata_job_id` as your dedup key
- `company.basic_info.company_id` (filter alias) ≠ `company.basic_info.crustdata_company_id` (response field) — they hold the same integer value but use different paths
- Null fields are normal: `revenue.public_markets`, `location.district`, `location.pincode`, parts of `company.funding`
- Dataset is a rolling index — recently closed listings may still appear; filter on `metadata.date_added` within the last 30 days to approximate "currently hiring"
---
 
## Web APIs
 
### 9. Web Search — `POST /web/search/live`
 
Search across web, news, academic, AI-generated summaries, and social sources.
 
**Cost:** 1 credit per query  
**Rate limit:** 10 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/web/search/live \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "query": "Stripe latest funding round",
    "sources": ["web", "news"],
    "location": "US",
    "start_date": 1704067200,
    "end_date": 1735689600,
    "page": 2
  }'
```
 
**Request parameters:**
 
| Parameter | Required | Notes |
|---|---|---|
| `query` | Yes | Max 5,000 chars. Supports `site:` and `filetype:` operators |
| `sources` | No | One or more of `web`, `news`, `scholar-articles`, `scholar-articles-enriched`, `scholar-author`, `ai`, `social`. Omit to search all. |
| `location` | No | ISO 3166-1 alpha-2 country code, e.g., `"US"`, `"GB"` |
| `site` | No | Restrict to domain, e.g., `"github.com"`. Max 500 chars. Effective only on `web` and `news`. |
| `start_date` | No | Unix timestamp (seconds) — results after this date |
| `end_date` | No | Unix timestamp (seconds) — results before this date. Must be > `start_date` |
| `human_mode` | No | `false` (default). Use `true` to bypass bot protection. |
| `page` | No | Number of result pages to aggregate. Min 1. |
 
**Response shape:**
```json
{
  "success": true,
  "query": "...",
  "timestamp": 1748000000000,
  "results": [ { "source": "web", "title": "...", "url": "...", "snippet": "...", "position": 1 } ],
  "metadata": { "total_results": 120, "failed_pages": [], "empty_pages": [] }
}
```
 
**Result shapes by source:**
 
| Source | Key fields |
|---|---|
| `web`, `news`, `social` | `source`, `title`, `url`, `snippet`, `position` |
| `scholar-articles`, `scholar-articles-enriched` | Above + `authors`, `citations`, `pdf_url`, `metadata` (citation string) |
| `scholar-author` | `source`, `url`, `name`, `affiliation`, `website`, `interests`, `citations`, `h_index`, `i10_index`, `articles` |
| `ai` | `source`, `title` (`"AI Overview"`), `content`, `references`, `images` |
 
**Output validation:**
- Always check `result.source` before accessing fields — different sources return different shapes
- Search `timestamp` is in **milliseconds**; divide by 1000 to compare with Fetch timestamps
- Multi-source responses interleave results; `position` reflects per-source rank, not global rank
- `social` results may be empty for some queries — check `results.length` first
---
 
### 10. Web Fetch — `POST /web/enrich/live`
 
Fetch the HTML content of up to 10 URLs in one request.
 
**Cost:** 1 credit per page  
**Rate limit:** 10 req/min
 
```bash
curl --request POST \
  --url https://api.crustdata.com/web/enrich/live \
  --header 'authorization: Bearer YOUR_API_KEY' \
  --header 'content-type: application/json' \
  --header 'x-api-version: 2025-11-01' \
  --data '{
    "urls": [
      "https://stripe.com/about",
      "https://retool.com/about"
    ],
    "human_mode": false
  }'
```
 
**Request parameters:**
 
| Parameter | Required | Notes |
|---|---|---|
| `urls` | Yes | 1–10 URLs. Must include `http://` or `https://`. |
| `human_mode` | No | `false` (default). Set `true` for Cloudflare-protected pages. |
 
**Response shape:**
```json
[
  {
    "success": true,
    "url": "https://stripe.com/about",
    "timestamp": 1748000000,
    "title": "About Stripe",
    "content": "<!DOCTYPE html>..."
  },
  {
    "success": false,
    "url": null,
    "timestamp": null,
    "title": null,
    "content": null
  }
]
```
 
**Output validation:**
- A `200` can contain failed entries — always check `success` per item
- Match results by `url` field, not array index
- Fetch `timestamp` is in **seconds** (Search is in milliseconds)
- JavaScript-heavy SPAs may return minimal HTML — the API fetches server-side HTML only
---
 
## Error Reference
 
| Status | Meaning | Shape |
|---|---|---|
| `400` | Bad request — invalid field, wrong operator, missing param | `{ "error": { "type", "message", "metadata" } }` |
| `401` | Bad API key | `{ "message": "..." }` *(flat, not nested)* |
| `403` | Insufficient credits or plan restriction | Nested error envelope |
| `404` | No match found (some endpoints) | Nested error envelope |
| `429` | Rate limit exceeded | Retry with exponential backoff |
| `500` | Server error | Retry after short delay |
 
⚠️ `401` uses a **flat** `{ "message": "..." }` envelope — different from all other error codes.
 
**Common mistakes:**
 
| Mistake | Fix |
|---|---|
| Using `>=` or `<=` | Use `=>` and `=<` |
| Country format mismatch | Full name for person location; ISO-3 for employer HQ |
| Mixing identifier types in Enrich/Identify | Use exactly one type per request |
| Changing `filters`/`sorts` between pages | Keep identical when paginating |
| AND exact-match on same job field | Impossible for one row; run two queries + intersect |
| More than 10 URLs in Web Fetch | Max is 10; batch larger lists |
| Missing `http://` in Web Fetch URLs | Protocol prefix required |
 
---
 
## Quick Reference: All Endpoints
 
| Method | Endpoint | What it does | Cost |
|---|---|---|---|
| POST | `/company/search` | Filter company database | 0.03/result |
| POST | `/company/enrich` | Full company profile | 2/record |
| POST | `/company/identify` | Resolve company by name/domain | Free |
| POST | `/company/search/autocomplete` | Valid filter values for company fields | Free |
| POST | `/person/search` | Filter people database | 0.03/result |
| POST | `/person/enrich` | Full person profile + contact data | 1–7/record |
| POST | `/person/search/autocomplete` | Valid filter values for person fields | Free |
| POST | `/job/search` | Filter job listings + aggregations | 0.03/result |
| POST | `/web/search/live` | Web/news/academic/AI search | 1/query |
| POST | `/web/enrich/live` | Fetch HTML from URLs | 1/page |