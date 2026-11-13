import psycopg
import traceback
import pandas as pd
from edgar import Company, get_filings, Filing
import os
from dotenv import load_dotenv
from requests import get
from sec_extraction.db import get_connection
from sec_extraction.sec_client import get_json, get_session, init_edgar
load_dotenv()
init_edgar()


portfolio = ["AAPL", "NVDA", "AMZN"]


def control_table_load(conn, pipeline, pipeline_name, mode) -> int:
    """Inserts row of pipeline run in ops.pipeline_runs and returns run_id"""

    query = """
        INSERT INTO ops.pipeline_runs (pipeline, pipeline_name, mode)
        VALUES (%s, %s, %s)
        RETURNING run_id
    """
    with conn.cursor() as cur:
        cur.execute(query, (pipeline, pipeline_name, mode))
        return cur.fetchone()[0]

def pipeline_status_update(conn, run_id, status):
    """When finished, updates pipeline status to success or failed"""

    query = """UPDATE ops.pipeline_runs
            SET status = %s, finished_at = now()
            WHERE run_id = %s
              """

    with conn.cursor() as cur:
        cur.execute(query, (status, run_id))

    return "PIPELINE RUN FAILED"


def to_company_rows(data: dict) -> list[tuple]:
    df = pd.DataFrame(list(data.values()))
    df = df.rename(columns={
        "cik_str": "cik",
        "ticker": "primary_ticker",
        "title": "company_name",
    })
    df = df.drop_duplicates(subset="cik", keep="first")
    df["cik"] = df["cik"].astype(int)
    return list(df[["cik", "primary_ticker", "company_name"]].itertuples(index=False, name=None))

def upsert_dim_company(conn, rows: list[tuple], mode = 'full'):
    """Inserts and updates companies from dim_company"""


    query = """
            INSERT INTO silver.dim_company (cik, primary_ticker, company_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (cik) DO UPDATE SET
            primary_ticker = EXCLUDED.primary_ticker,
            company_name   = EXCLUDED.company_name,
            updated_at     = now()
            WHERE silver.dim_company.primary_ticker IS DISTINCT FROM EXCLUDED.primary_ticker
            OR silver.dim_company.company_name   IS DISTINCT FROM EXCLUDED.company_name

    """


    with conn.cursor() as cur:
        cur.executemany(query, rows)

        



def to_ticker_rows(data):
    df = pd.DataFrame(data["data"], columns = data["fields"])
    df = df.drop_duplicates(subset="ticker", keep="first")
    df["cik"] = df["cik"].astype(int)
    return list(df[["cik", "ticker", "exchange"]].itertuples(index=False, name=None))

def upsert_dim_ticker(conn, rows: list[tuple]):
    pipeline = "DIM_TICKER LOAD"

    control_query = f"""
        INSERT INTO 
    """

    """Inserts and updates companies from dim_ticker"""

    query = """
            INSERT INTO silver.dim_ticker (cik, ticker, exchange)
            VALUES (%s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
            cik            = EXCLUDED.cik,
            exchange   = EXCLUDED.exchange,
            updated_at     = now()
            WHERE silver.dim_ticker.cik IS DISTINCT FROM EXCLUDED.cik
            """
    with conn.cursor() as cur:
        cur.executemany(query, rows)


def fetch_company_filings(ticker) -> list[tuple]:
    init_edgar()
    company = Company(ticker)
    filings = [(filing.accession_no, filing.cik, filing.form, filing.filing_date) for filing in company.get_filings() if filing.form in ["10-K","10-Q"]]
    return filings

def insert_ops_filing_queue(conn, rows):

    """Inserts filings from tracked companies into ops.filings_queue"""

    query = """
                INSERT INTO ops.filings_queue (accession_number, cik, form_type, filed_date)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (accession_number) DO UPDATE SET
                cik            = EXCLUDED.cik,
                form_type      = EXCLUDED.form_type,
                filed_date     = EXCLUDED.filed_date,
                updated_at     = now()
                WHERE filings_queue.cik IS DISTINCT FROM EXCLUDED.cik """

    with conn.cursor() as cur:
        cur.executemany(query, rows)

def track_company(ticker_list):
    query = """
            UPDATE silver.dim_company SET tracked = true WHERE primary_ticker = %s

            """
    ticker_list_tuple = [(ticker,) for ticker in ticker_list]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(query,ticker_list_tuple)

def pipeline_load_dim_company():
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    pipeline = "reference_data"
    pipeline_name = "Load dim_company"
    mode = 'full'
    status = 'failed'
    error = None

    with get_connection() as conn:
        run_id =  control_table_load(conn, pipeline, pipeline_name, mode)


    try:
        session = get_session()
        data_cik = get_json(session, tickers_url)
        company_rows = to_company_rows(data_cik)
        print("rows:", len(company_rows))

        with get_connection() as conn:
            upsert_dim_company(conn, company_rows)
            status = "success"
    except Exception as e:
        print(f"Couldn't load or update dim_company: {e}")
        raise

    finally:
        with get_connection() as conn:
            pipeline_status_update(conn, run_id, status)


def pipeline_load_dim_ticker():

    tickers_exchange_url = "https://www.sec.gov/files/company_tickers_exchange.json"
    pipeline = "reference_data"
    pipeline_name = "Load dim_ticker"
    mode = 'full'
    status = 'failed'
    error = None

    with get_connection() as conn:
        run_id =  control_table_load(conn, pipeline, pipeline_name, mode)


    try:
        session = get_session()
        data_tickers = get_json(session, tickers_exchange_url)
        company_rows = to_ticker_rows(data_tickers)
        print("rows:", len(company_rows))

        with get_connection() as conn:
            upsert_dim_company(conn, company_rows)
            status = "success"
    except Exception as e:
        print(f"Couldn't load or update dim_company: {e}")
        raise

    finally:
        with get_connection() as conn:
            pipeline_status_update(conn, run_id, status)

def pipeline_load_filing_queue():

    pipeline = "filings_discovery"
    pipeline_name = "Load filing_queue"
    mode = "full"
    status = 'failed'
    with get_connection() as conn:
            run_id =  control_table_load(conn, pipeline, pipeline_name, mode)

    try:
        init_edgar()
        retrieve_query = """
        SELECT primary_ticker from silver.dim_company WHERE dim_company.tracked = true
        """
        with get_connection() as conn:

            tickers = [row[0] for row in conn.cursor().execute(retrieve_query).fetchall()]
            filings = [fetch_company_filings(ticker) for ticker in tickers]
            filings = [filing for companies in filings for filing in companies]
            insert_ops_filing_queue(conn, filings)
            status = "success"
        
    except Exception as e:
        print(f"Couldn't load filing filing queue: {e}")
        raise

    finally:
        with get_connection() as conn:
            pipeline_status_update(conn, run_id, status)


     



def main():
    pipeline_load_dim_company()
    pipeline_load_dim_ticker()
    track_company(portfolio)
    pipeline_load_filing_queue()


if __name__ == "__main__":
       main()