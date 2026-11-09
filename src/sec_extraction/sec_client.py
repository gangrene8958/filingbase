import os

import requests
from dotenv import load_dotenv
from edgar import set_identity


def get_user_agent() -> str:
    load_dotenv()
    name = os.getenv("EDGAR_NAME")
    mail = os.getenv("EDGAR_MAIL")
    if not name or not mail:
        raise RuntimeError("EDGAR_NAME and EDGAR_MAIL must be set (check your .env)")
    return f"{name} {mail}"


def init_edgar() -> None:
    """Configure edgartools with the same identity."""
    set_identity(get_user_agent())


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": get_user_agent()})
    return session


def get_json(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.json()