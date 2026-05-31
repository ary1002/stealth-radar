# Stealth Radar

> *"See the company before the company exists."*

Stealth Radar detects forming startup teams by analysing the employment graph. It surfaces clusters of people who left the same company together, converged on a shared new destination, and exhibit stealth or founder signals — months before any public announcement.

---

## What it does

Most startup intelligence arrives too late: the announcement, the TechCrunch article, the funding round. Stealth Radar operates on a different signal — **co-movement in the talent graph**. When two or three people who worked together quietly leave the same company within a tight window and end up at the same tiny new employer (or both list "Stealth" in their LinkedIn headline), that pattern is detectable and scoreable well before it becomes news.

The tool produces two views:

- **Radar** — for a given anchor company, ranks every cluster of departing alumni who appear to be co-founding or co-joining a new venture, scored and adjudicated by Claude
- **TalentFlow** — a weighted Sankey of where that company's leavers are going overall, highlighting tiny and stealth destinations

---

## Methodology

### 1. Cohort pull

For a given anchor company, Stealth Radar queries the [Crustdata Person API](https://crustdata.com) (`POST /person/search`) filtering on:
- `past_employers.company` = the anchor
- `recently_changed_jobs = true`

This returns the set of people who have the anchor in their employment history and recently moved. A single paginated call with `limit=50` covers the active signal window while staying credit-efficient ($0.03/result).

### 2. Parsing the employment graph

Each profile is parsed into a structured `Person` object with a complete role history — company name, company ID, start/end dates, headcount, seniority. The raw Crustdata response uses a flat schema (`current_employers[]`, `past_employers[]`); this is normalised into the internal graph model.

### 3. Leaver detection

A person is classified as a **leaver** if:
- Their anchor role has an `end_date` (they actually left), AND
- They are not currently employed at the anchor (no re-hire false positives), AND
- Their new current role started within an 18-month lookback window

### 4. Signal tagging

Each leaver is tagged on five signals:
- `stealth` — current role/headline matches stealth or co-founding language
- `founder` — title contains Founder or Co-Founder
- `tiny_destination` — current employer headcount ≤ 25
- `open_to_career` — profile signals active career interest
- `has_dest_id` — destination company is indexed (has a Crustdata company_id > 0)

### 5. Clustering

Two paths:

**Strong clusters** — group leavers who share the same `current_employers.company_id`. This catches cases where an indexed destination company is already attracting multiple anchor alumni. A headcount gate (≥ 500 employees) demotes large-employer clusters — convergence on Google or Anthropic is independent hiring, not team formation — so these remain visible but are capped at Medium tier and cannot receive a `forming_team` label.

**Medium clusters** — for stealth/founder leavers whose new company is not yet indexed (the typical case for a company that doesn't exist yet). Two leavers are linked in the same cluster if:
- They departed the anchor within a 4-month window, AND
- Either they share the same (stealth) destination name, OR they co-tenured at the anchor for ≥ 6 months

This co-tenure requirement is the key false-positive guard: two people who happened to leave the same large company in the same month but with no meaningful overlap are not a team.

Clusters are built as connected components of this graph using NetworkX.

### 6. Scoring

Each cluster is scored 0–100 using a weighted composite of seven features:

| Feature | Weight | What it measures |
|---|---|---|
| `shared_destination` | 0.22 | Fraction of members converging on one company_id |
| `co_tenure` | 0.14 | Did they actually work together at the anchor |
| `stealth_founder_ratio` | 0.16 | Fraction with stealth/founder signals |
| `size_score` | 0.18 | Gaussian peak at 3–5 members (too small = noise, too large = reorg) |
| `window_tightness` | 0.12 | How close together the departure dates are |
| `destination_tiny` | 0.12 | Destination headcount ≤ 25 |
| `open_to` | 0.06 | Career-interest signal (sparse; minor bonus) |

The five convergence/signal features use absolute values (0–1 directly meaningful); co-tenure and open_to use percentile normalisation across the current run's clusters. A cluster that genuinely converges on a tiny stealth destination with strong co-tenure can score into the High tier (≥ 75).

Tier labels: **High** (≥75) · **Medium** (≥50) · **Low** (≥25) · **Watch** (<25)

### 7. Claude adjudication

Every cluster is sent to Claude (`claude-sonnet-4-6`) with a structured summary — anchor name, member names and headlines, current roles, tenure overlap, destination convergence, and the feature vector. Claude is prompted to be sceptical and name disconfirming evidence, then returns a structured verdict:

- `forming_team` — intentional co-founding or co-joining signal
- `layoff_dispersion` — looks like a coordinated layoff landing at the same new employer
- `coincidental` — independent moves to a common desirable destination
- `unclear` — insufficient signal

Clusters demoted by the headcount gate cannot receive `forming_team` regardless of Claude's output.

High and Medium `forming_team` clusters also receive a **dossier** — a tight intelligence brief with thesis, evidence timeline, recommended action, and urgency (now / 30d / 90d).

### 8. TalentFlow

In parallel with cluster detection, a directed weighted graph of `anchor → destination` edges is built from all leavers' current employers. This Sankey view — independent of the clustering — shows where a company's talent is systematically flowing, highlighting destinations with headcount ≤ 25 or unresolved company IDs (stealth) in amber.

---

## Validation

The detector was validated on three known founding teams using a **founder-anchored** methodology: rather than sampling a large cohort and hoping the founders appear, we pull the known founders' profiles directly and reconstruct their employment state as-of T−3, T−6, and T−9 months before announcement.

| Case | Result | What it proves |
|---|---|---|
| **Character.AI** (ex-Google, Series A Mar 2023) | Caught at all three horizons, score 73.9 | Co-tenure + convergence path fires correctly on a real startup-scale spinout (headcount 257, passes the <500 gate) |
| **Sierra AI** (ex-Salesforce/Google, Sep 2023) | Correctly demoted | Founders converged on Sierra (headcount 757) — the gate suppresses the cluster, demonstrating the false-positive guard against large-but-real companies |
| **Sakana AI** (ex-Google, Aug 2023) | No cluster | Founders departed 14 months apart — no co-movement signal exists by design. Honest method boundary: staggered solo departures are outside the detector's scope |

This is case-study design verification, not a large-sample recall claim.

---

## Data

All employment data is sourced from the **Crustdata Person API** — a B2B intelligence platform with 800M+ professional profiles indexed from public sources. Stealth Radar uses only the `/person/search` endpoint (database-cached data, not live scraping). No data is stored beyond the in-session pipeline run; DuckDB is used only for backtest results and snapshot diffing across runs.

---

*Built on [Crustdata](https://crustdata.com) + [Claude](https://anthropic.com/claude) · Aryan Gupta · IIT Bombay*
