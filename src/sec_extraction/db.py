import os

import psycopg
from dotenv import load_dotenv


def get_connection() -> psycopg.Connection:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (check your .env)")
    return psycopg.connect(url)