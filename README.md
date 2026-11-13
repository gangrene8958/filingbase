# Filingbase
 
A data pipeline that builds a clean, **point-in-time correct** database of US public company fundamentals from SEC EDGAR filings, stored in PostgreSQL.
 
The long-term goal is a research-grade dataset of financial statements and market prices that can feed machine learning models for systematic trading. This repository starts with the part that everything else depends on: getting the data right.
 
> **Status:** early stage. The database schema is designed and the company and ticker reference tables are loaded. Filing and XBRL fact ingestion is in progress. See the [roadmap](#roadmap).
 
---
 
## Why data quality comes first
 
Most trading models that look great in a backtest fail because of the data, not the model. This project is designed around the most common sources of error in financial datasets:
 
| Problem | What goes wrong | How this project handles it |
|---|---|---|
| **Look-ahead bias** | A model "knows" a figure before it was public | Every fact stores the date it was **filed**, not only the period it describes, so you can ask "what was known on date X?" |
| **Restatements** | Companies correct past figures; overwriting them rewrites history | Facts are never overwritten. Each filing's version of a figure is kept with its filing date |
| **Survivorship bias** | Using today's company list hides the companies that disappeared | Companies are keyed by SEC **CIK**, which never changes, and raw snapshots of the company list are archived over time |
| **Ticker reuse and changes** | A ticker can move to a different company (or a company can change ticker) | Tickers live in their own table linked to the CIK, never used as a company identifier |
| **Inconsistent concepts** | Companies report the same item under different XBRL tags | Raw tags are kept as reported (silver); standardized metrics are built separately with an explicit mapping (gold) |
| **Precision loss** | Floats introduce rounding errors in financial figures | All values are stored as `numeric`, never `float` |
 
---
 
## Architecture
 
The pipeline follows a medallion architecture:
 
```
SEC EDGAR ──► bronze ──► silver ──► gold ──► ML features / models (future)
             raw files   clean,     standardized
                         as reported metrics
```
 
- **Bronze** — raw files exactly as downloaded from the SEC, stored on disk by source and download date. Never modified, so any layer can be rebuilt from scratch.
- **Silver** — clean, typed and deduplicated data, faithful to what each company reported. No business logic.
- **Gold** — standardized metrics (e.g. revenue, net income) with documented rules: which XBRL concepts map to each metric, how missing quarters (Q4) are derived.
- **Ops** — pipeline state: which filings were discovered, loaded or failed, and a log of every run.
### Design principles
 
- **Idempotent** — running any load twice gives the same result. All writes are upserts on natural keys.
- **Incremental** — after the historical backfill, only new filings are processed.
- **Atomic** — each load runs in a single transaction: it either fully succeeds or leaves the database untouched.
- **Reproducible** — the schema lives in version control and silver/gold can be rebuilt from bronze.
- **Enforced by the database** — primary keys, foreign keys and check constraints guarantee integrity, not just the Python code.
---
 
## Data model
 
PostgreSQL schemas: `silver`, `gold`, `ops`. The full definition is in [`sql/schema.sql`](sql/schema.sql).
 
| Schema | Table | Grain | Status |
|---|---|---|---|
| silver | `dim_company` | One row per company (CIK) | Loaded |
| silver | `dim_ticker` | One row per ticker, linked to its company | Loaded |
| silver | `dim_filing` | One row per 10-K / 10-Q filing | In progress |
| silver | `dim_concept` | One row per XBRL concept | In progress |
| silver | `fact_xbrl` | One row per figure as reported in a filing | In progress |
| gold | `dim_metric` | One row per standardized metric | Planned |
| gold | `concept_mapping` | Which XBRL concepts feed each metric | Planned |
| gold | `fact_metrics` | Company × metric × fiscal period | Planned |
| ops | `pipeline_runs` | One row per pipeline execution | Loaded |
| ops | `filings_queue` | One row per discovered filing and its load status | Loaded |
 
```
            dim_ticker
                │
                ▼
dim_concept ◄── fact_xbrl ──► dim_company ◄── fact_metrics ──► dim_metric
                    │
                    ▼
                dim_filing
```
 
---
 
## Data sources
 
| Source | Used for |
|---|---|
| [SEC company tickers](https://www.sec.gov/files/company_tickers.json) | Company list (CIK, name, primary ticker) |
| [SEC company tickers with exchange](https://www.sec.gov/files/company_tickers_exchange.json) | All tickers per company and their exchange |
| SEC EDGAR filings via [edgartools](https://github.com/dgunning/edgartools) | 10-K / 10-Q filings and their XBRL financial data |
| Market prices | Planned |
 
The SEC requires every automated request to identify itself with a name and email in the `User-Agent` header, and limits clients to 10 requests per second. This project follows the SEC's [fair access policy](https://www.sec.gov/os/accessing-edgar-data).
 
---
 
## Getting started
 
### Requirements
 
- Linux or WSL (Ubuntu)
- Python, managed with [uv](https://docs.astral.sh/uv/)
- PostgreSQL 15 or later
### 1. Install dependencies
 
```bash
uv sync
```
 
### 2. Create the database
 
As the Postgres superuser, create a dedicated role and database:
 
```sql
CREATE ROLE edgar_pipeline WITH LOGIN PASSWORD '<password>';
CREATE DATABASE edgar OWNER edgar_pipeline;
```
 
### 3. Configure the environment
 
Create a `.env` file in the project root (it is git-ignored):
 
```
DATABASE_URL=postgresql://edgar_pipeline:<password>@localhost:5432/edgar
EDGAR_NAME=Your Name
EDGAR_MAIL=you@example.com
BRONZE_DIR=data/bronze
```
 
### 4. Create the schema
 
```bash
psql "$(grep DATABASE_URL .env | cut -d= -f2-)" -f sql/schema.sql
```
 
### 5. Load companies and tickers
 
```bash
uv run src/sec_extraction/load_data.py
```
 
---
 
## Project structure
 
```
filingbase/
├── sql/
│   └── schema.sql            # database schema (all layers)
├── src/sec_extraction/
│   ├── db.py                 # PostgreSQL connection
│   ├── sec_client.py         # SEC HTTP client (identity, session)
│   └── load_data.py          # company and ticker load
├── data/                     # bronze files (git-ignored)
├── pyproject.toml
└── uv.lock
```
 
---
 
## Roadmap
 
1. **Reference data** — companies and tickers (done)
2. **Raw archive** — save every SEC download to the bronze layer, building a history of company and ticker snapshots (done)
3. **Filings** — discover 10-K / 10-Q filings for tracked companies and track them in a load queue (done)
4. **XBRL facts** — parse and load every reported figure, starting with a small set of companies and validating them by hand
5. **Historical backfill** — at least 10 years of filings, possibly extended further back; resumable and idempotent
6. **Gold metrics** — concept mapping, derived Q4 values, standardized quarterly and annual metrics
7. **Data quality checks** — automated checks after every load (e.g. assets = liabilities + equity, no duplicates, no scale errors)
8. **Scale** — all companies that file 10-K / 10-Q, using the SEC bulk datasets
9. **Market prices** — daily prices linked to companies by CIK
10. **Ticker history** — point-in-time ticker ↔ company mapping
11. **ML features** — point-in-time feature tables for modelling
---
 
## Disclaimer
 
This project is for research and educational purposes. Nothing in it is investment advice.