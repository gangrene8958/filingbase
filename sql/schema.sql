-- =============================================================================
-- sec-extraction: database schema
--
-- Layers (Postgres schemas):
--   silver -> clean data, faithful to what each company reported
--   gold   -> standardized metrics, ready for analysis
--   ops    -> pipeline control (queue and runs)
--
-- Requires PostgreSQL 15+ (uses UNIQUE NULLS NOT DISTINCT).
-- Run with:
--   psql "$DATABASE_URL" -f sql/schema.sql
--
-- Everything runs in one transaction: if any statement fails, nothing is created.
-- =============================================================================
 
BEGIN;
 
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;
 
 
-- =============================================================================
-- SILVER
-- =============================================================================
 
-- SCD type 1: upserts overwrite the current values, no history is kept.
CREATE TABLE silver.dim_company (
    cik             int         PRIMARY KEY,
    primary_ticker  text        NOT NULL,
    company_name    text        NOT NULL,
    sic_code        text,                           -- a code, not a number: keeps leading zeros ('0100')
    industry        text,
    fiscal_year_end text,
    tracked         boolean     NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
 
    CONSTRAINT dim_company_fiscal_year_end_format
        CHECK (fiscal_year_end IS NULL OR fiscal_year_end ~ '^[0-9]{4}$')
);
 
-- Partial index: only the few tracked companies, so it stays small.
CREATE INDEX dim_company_tracked_idx ON silver.dim_company (cik) WHERE tracked;
 
 
-- Separate from dim_company because one company can have several tickers.
CREATE TABLE silver.dim_ticker (
    ticker      text        PRIMARY KEY,
    cik         int         NOT NULL REFERENCES silver.dim_company (cik),
    exchange    text,
    is_primary  boolean     NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
 
-- Postgres does not index foreign keys automatically.
CREATE INDEX dim_ticker_cik_idx ON silver.dim_ticker (cik);
 
 
CREATE TABLE silver.dim_filing (
    accession_number text        PRIMARY KEY,
    cik              int         NOT NULL REFERENCES silver.dim_company (cik),
    form_type        text        NOT NULL,
    filed_date       date        NOT NULL,
    period_of_report date,
    fiscal_year      int,
    fiscal_period    text,
    is_amendment     boolean     NOT NULL DEFAULT false,
    document_url     text,
    created_at       timestamptz NOT NULL DEFAULT now(),
 
    CONSTRAINT dim_filing_fiscal_period_valid
        CHECK (fiscal_period IS NULL OR fiscal_period IN ('FY', 'Q1', 'Q2', 'Q3'))
);
 
-- Covers the FK on cik and "filings of a company by date" queries.
CREATE INDEX dim_filing_cik_filed_idx ON silver.dim_filing (cik, filed_date);
 
 
CREATE TABLE silver.dim_concept (
    concept     text    PRIMARY KEY,
    taxonomy    text    NOT NULL,
    local_name  text    NOT NULL,
    label       text,
    period_type text,
    balance     text,
    is_custom   boolean NOT NULL DEFAULT false,
 
    CONSTRAINT dim_concept_period_type_valid
        CHECK (period_type IS NULL OR period_type IN ('instant', 'duration')),
    CONSTRAINT dim_concept_balance_valid
        CHECK (balance IS NULL OR balance IN ('debit', 'credit'))
);
 
 
CREATE TABLE silver.fact_xbrl (
    fact_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    accession_number text        NOT NULL REFERENCES silver.dim_filing (accession_number),
    -- Redundant (derivable from dim_filing) on purpose: avoids a join on the most
    -- common queries and allows the (cik, concept, period_end) index below.
    cik              int         NOT NULL REFERENCES silver.dim_company (cik),
    concept          text        NOT NULL REFERENCES silver.dim_concept (concept),
    unit             text        NOT NULL,
    period_type      text        NOT NULL,
    period_start     date,
    period_end       date        NOT NULL,
    duration_days    int         GENERATED ALWAYS AS (period_end - period_start) STORED,
    value            numeric     NOT NULL,          -- numeric, never float: exact decimals
    decimals         int,
    dims             jsonb,
    dims_hash        text        NOT NULL DEFAULT '',  -- '' instead of NULL so it works in the unique key
    filed_date       date        NOT NULL,          -- copied from dim_filing for point-in-time queries
    created_at       timestamptz NOT NULL DEFAULT now(),
 
    CONSTRAINT fact_xbrl_period_type_valid
        CHECK (period_type IN ('instant', 'duration')),
    CONSTRAINT fact_xbrl_period_consistent
        CHECK (
            (period_type = 'instant'  AND period_start IS NULL)
         OR (period_type = 'duration' AND period_start IS NOT NULL AND period_start <= period_end)
        ),
    -- Idempotency key. NULLS NOT DISTINCT: two instant facts (period_start NULL)
    -- with the same values count as duplicates.
    CONSTRAINT fact_xbrl_natural_key
        UNIQUE NULLS NOT DISTINCT
        (accession_number, concept, unit, period_start, period_end, dims_hash)
);
 
-- Main query pattern: a company's concept over time. Also covers the FK on cik.
CREATE INDEX fact_xbrl_cik_concept_period_idx ON silver.fact_xbrl (cik, concept, period_end);
-- FK on concept.
CREATE INDEX fact_xbrl_concept_idx ON silver.fact_xbrl (concept);
-- The FK on accession_number is covered by fact_xbrl_natural_key (it starts with that column).
 
 
-- =============================================================================
-- GOLD
-- =============================================================================
 
CREATE TABLE gold.dim_metric (
    metric      text PRIMARY KEY,
    label       text NOT NULL,
    statement   text,
    period_type text,
    aggregation text,
 
    CONSTRAINT dim_metric_statement_valid
        CHECK (statement IS NULL OR statement IN ('income', 'balance', 'cash_flow')),
    CONSTRAINT dim_metric_period_type_valid
        CHECK (period_type IS NULL OR period_type IN ('instant', 'duration')),
    CONSTRAINT dim_metric_aggregation_valid
        CHECK (aggregation IS NULL OR aggregation IN ('additive', 'semi_additive', 'non_additive'))
);
 
 
CREATE TABLE gold.concept_mapping (
    metric   text     NOT NULL REFERENCES gold.dim_metric (metric),
    concept  text     NOT NULL REFERENCES silver.dim_concept (concept),
    priority smallint NOT NULL,                     -- 1 = preferred
 
    PRIMARY KEY (metric, concept)
);
 
CREATE INDEX concept_mapping_concept_idx ON gold.concept_mapping (concept);
 
 
CREATE TABLE gold.fact_metrics (
    cik              int         NOT NULL REFERENCES silver.dim_company (cik),
    metric           text        NOT NULL REFERENCES gold.dim_metric (metric),
    fiscal_year      int         NOT NULL,
    fiscal_period    text        NOT NULL,
    period_start     date,
    period_end       date        NOT NULL,
    value            numeric     NOT NULL,
    is_derived       boolean     NOT NULL DEFAULT false,
    source_concept   text,
    source_accession text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
 
    PRIMARY KEY (cik, metric, fiscal_year, fiscal_period),
    CONSTRAINT fact_metrics_fiscal_period_valid
        CHECK (fiscal_period IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY'))
);
 
CREATE INDEX fact_metrics_metric_idx ON gold.fact_metrics (metric);
 
 
-- =============================================================================
-- OPS
-- =============================================================================
 
CREATE TABLE ops.pipeline_runs (
    run_id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode               text        NOT NULL,
    status             text        NOT NULL DEFAULT 'running',
    code_version       text,                        -- git commit hash
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    filings_discovered int         NOT NULL DEFAULT 0,
    filings_loaded     int         NOT NULL DEFAULT 0,
    filings_failed     int         NOT NULL DEFAULT 0,
 
    CONSTRAINT pipeline_runs_mode_valid
        CHECK (mode IN ('backfill', 'incremental')),
    CONSTRAINT pipeline_runs_status_valid
        CHECK (status IN ('running', 'success', 'failed'))
);
 
 
-- Unlike silver.dim_filing, this also holds filings that are not loaded yet.
CREATE TABLE ops.filings_queue (
    accession_number  text        PRIMARY KEY,
    cik               int         NOT NULL REFERENCES silver.dim_company (cik),
    form_type         text        NOT NULL,
    filed_date        date        NOT NULL,
    status            text        NOT NULL DEFAULT 'pending',
    attempts          int         NOT NULL DEFAULT 0,
    last_error        text,
    raw_path          text,
    facts_loaded      int,
    transform_version int,
    run_id            bigint      REFERENCES ops.pipeline_runs (run_id),
    discovered_at     timestamptz NOT NULL DEFAULT now(),
    started_at        timestamptz,
    finished_at       timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now(),
 
    CONSTRAINT filings_queue_status_valid
        CHECK (status IN ('pending', 'processing', 'loaded', 'failed', 'dead', 'skipped'))
);
 
CREATE INDEX filings_queue_cik_idx    ON ops.filings_queue (cik);
CREATE INDEX filings_queue_run_id_idx ON ops.filings_queue (run_id);
-- Partial index: only the rows the pipeline still has to work on.
CREATE INDEX filings_queue_todo_idx   ON ops.filings_queue (status, filed_date)
    WHERE status IN ('pending', 'failed', 'processing');
 
COMMIT;