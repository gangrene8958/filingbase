import psycopg
import pandas as pd
from edgar import Company, get_filings
import os
from dotenv import load_dotenv
from requests import get
from sec_extraction.db import get_connection
from sec_extraction.sec_client import get_json, get_session
load_dotenv()

tickers_url = "https://www.sec.gov/files/company_tickers.json"
tickers_exchange_url = "https://www.sec.gov/files/company_tickers_exchange.json"

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

def upsert_dim_company(conn, rows: list[tuple]):
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


def main():
    session = get_session()
    data_cik = get_json(session, tickers_url)
    data_exchange = get_json(session, tickers_exchange_url)

    company_rows = to_company_rows(data_cik)   # adaptada al format _exchange
    ticker_rows = to_ticker_rows(data_exchange)

    print("rows:", len(ticker_rows))
    print("first:", ticker_rows[:3])

    with get_connection() as conn:
        upsert_dim_company(conn, company_rows)
        upsert_dim_ticker(conn, ticker_rows)
        print("in db:", conn.execute("SELECT count(*) FROM silver.dim_ticker").fetchone())


    print(f"{len(company_rows)} companies and {len(ticker_rows)} tickers loaded")


if __name__ == "__main__":
      main()