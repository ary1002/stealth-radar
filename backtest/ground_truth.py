# Ground truth: known founding teams with shared prior employer.
# All entries verified via Crustdata /person/search — both founders resolved
# with populated start_date + end_date on the anchor employer.
#
# Schema note: `founder_search` carries the (first, last) lookup params used
# at backtest run time to acquire profiles via search ($0.03/result).
# `founder_profile_urls` is the canonical set used for hit-checking.
# `co_tenure_expected` flags whether Google tenures overlap; Sierra=False
# (Bret left 2007, Clay joined 2013) — tests strong-cluster path only.

GROUND_TRUTH = [
    {
        # Noam Shazeer (Google 2000-2009, 2012-2021) +
        # Daniel de Freitas (Google 2016-2018, 2018-2020).
        # Co-tenure at Google: ~2016-2020. Full co-tenure + medium/strong cluster test.
        "startup": "Character.AI",
        "announce_date": "2023-03-01",  # Series A — first date both founders already at Char.AI
        "prior_employer_name": "Google",
        "prior_employer_linkedin_url": "https://www.linkedin.com/company/google",
        "co_tenure_expected": True,
        "founder_profile_urls": [
            "https://www.linkedin.com/in/noam-shazeer-3b27288",
            "https://www.linkedin.com/in/daniel-de-freitas-3a350464",
        ],
        "founder_search": [
            {"first": "Noam",   "last": "Shazeer", "url": "https://www.linkedin.com/in/noam-shazeer-3b27288"},
            {"first": "Daniel", "last": "Freitas",  "url": "https://www.linkedin.com/in/daniel-de-freitas-3a350464"},
        ],
    },
    {
        # David Ha (Google Brain 2016-2022, then Stability AI) +
        # Llion Jones (Google/YouTube 2012-2023, co-authored Transformer paper).
        # Co-tenure at Google: 2016-2022. Full co-tenure + medium/strong cluster test.
        "startup": "Sakana AI",
        "announce_date": "2023-08-01",
        "prior_employer_name": "Google",
        "prior_employer_linkedin_url": "https://www.linkedin.com/company/google",
        "co_tenure_expected": True,
        "founder_profile_urls": [
            "https://www.linkedin.com/in/hardmaru",
            "https://www.linkedin.com/in/llion-jones-9ab3064b",
        ],
        "founder_search": [
            {"first": "David", "last": "Ha",    "url": "https://www.linkedin.com/in/hardmaru"},
            {"first": "Llion", "last": "Jones", "url": "https://www.linkedin.com/in/llion-jones-9ab3064b"},
        ],
    },
    {
        # Bret Taylor (Google 2003-2007, Salesforce CEO 2019-2022) +
        # Clay Bavor (Google 2013-2021).
        # Google tenures DO NOT overlap — co_tenure=0 expected.
        # Tests strong-cluster/convergence path only (both at Sierra at asof).
        "startup": "Sierra AI",
        "announce_date": "2023-09-01",
        "prior_employer_name": "Google",
        "prior_employer_linkedin_url": "https://www.linkedin.com/company/google",
        "co_tenure_expected": False,
        "founder_profile_urls": [
            "https://www.linkedin.com/in/brettaylor",
            "https://www.linkedin.com/in/claybavor",
        ],
        "founder_search": [
            {"first": "Bret", "last": "Taylor", "url": "https://www.linkedin.com/in/brettaylor"},
            {"first": "Clay", "last": "Bavor",  "url": "https://www.linkedin.com/in/claybavor"},
        ],
    },
]
