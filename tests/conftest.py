"""Shared fixtures for live API verification tests."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import psycopg
import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env", override=False)

DATA_QUERY_BASE_URL = os.environ.get(
    "DATA_QUERY_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
RISK_BASE_URL = os.environ.get("RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


@pytest.fixture(scope="session")
def data_client() -> httpx.Client:
    client = httpx.Client(base_url=DATA_QUERY_BASE_URL, timeout=60.0)
    try:
        r = client.get("/health")
    except httpx.ConnectError as exc:
        pytest.fail(
            f"data_query API not reachable at {DATA_QUERY_BASE_URL}: {exc}. "
            "Start with: uvicorn services.data_query.app:app --port 8000 --app-dir ."
        )
    if r.status_code != 200:
        pytest.fail(f"data_query /health -> {r.status_code}: {r.text}")
    return client


@pytest.fixture(scope="session")
def risk_client() -> httpx.Client:
    client = httpx.Client(base_url=RISK_BASE_URL, timeout=60.0)
    try:
        r = client.get("/health")
    except httpx.ConnectError as exc:
        pytest.fail(
            f"risk API not reachable at {RISK_BASE_URL}: {exc}. "
            "Start with: uvicorn services.risk_forecasting.app:app --port 8001 --app-dir ."
        )
    if r.status_code == 503 and _risk_reports_not_loaded(r):
        # Startup check failed (params, covariates, or trial prediction). Return
        # the client so each test reports its own "model not loaded" message.
        return client
    if r.status_code >= 500:
        pytest.fail(f"risk /health -> {r.status_code}: {r.text}")
    return client


def _risk_reports_not_loaded(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (
        body.get("ready") is False or body.get("model_loaded") is False
    )


@pytest.fixture(scope="session")
def db_conn():
    from shared.db import connect, get_settings

    conn = connect(get_settings())
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def demo_data_dir() -> Path:
    from shared.db import get_settings

    path = get_settings().dataset_demo_data_dir
    if not path.is_dir():
        pytest.fail(f"DATASET_DEMO_DATA_DIR missing: {path}")
    return path


def sql_count(conn: psycopg.Connection, sql: str, params: tuple | list = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])
