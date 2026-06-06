-- Stealth Radar v2 — predictions ledger
-- Append-only. Each row's hash includes the previous row's hash (chain).

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id     VARCHAR PRIMARY KEY,
    thesis_id         VARCHAR NOT NULL,
    cluster_id        VARCHAR NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anchor_company    VARCHAR NOT NULL,
    members           JSON NOT NULL,          -- list of {name, profile_url, headline}
    destination_name  VARCHAR,
    destination_company_id INTEGER,
    score             NUMERIC(5,2) NOT NULL,
    tier              VARCHAR NOT NULL,        -- High|Medium|Low|Watch
    claude_verdict    VARCHAR NOT NULL,        -- forming_team|layoff_dispersion|coincidental|unclear
    evidence_bundle   JSON NOT NULL,           -- serialised EvidenceBundle
    predicted_event   VARCHAR,                 -- e.g. "funding_round_6mo"
    conviction_score  DOUBLE,                  -- score × verdict multiplier; NULL for legacy rows
    status            VARCHAR NOT NULL DEFAULT 'open',   -- open|confirmed|expired
    confirmed_at      TIMESTAMPTZ,
    lead_time_days    INTEGER,
    row_hash          VARCHAR NOT NULL,        -- sha256(canonical_json + prev_hash)
    prev_hash         VARCHAR NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS predictions_thesis_id    ON predictions (thesis_id);
CREATE INDEX IF NOT EXISTS predictions_status       ON predictions (status);
CREATE INDEX IF NOT EXISTS predictions_created_at   ON predictions (created_at DESC);
