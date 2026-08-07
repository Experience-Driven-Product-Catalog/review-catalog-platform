from __future__ import annotations

import os
import time

import httpx


def main() -> None:
    base_url = os.environ.get("AIRFLOW_BASE_URL", "http://airflow-apiserver:8080").rstrip("/")
    username = os.environ.get("AIRFLOW_USERNAME", "airflow")
    password = os.environ.get("AIRFLOW_PASSWORD", "airflow")
    with httpx.Client(timeout=10) as client:
        for attempt in range(30):
            try:
                token_response = client.post(
                    f"{base_url}/auth/token",
                    json={"username": username, "password": password},
                )
                token_response.raise_for_status()
                token = token_response.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                pool_url = f"{base_url}/api/v2/pools/duckdb_writer_pool"
                current = client.get(pool_url, headers=headers)
                if current.status_code == 404:
                    response = client.post(
                        f"{base_url}/api/v2/pools",
                        headers=headers,
                        json={
                            "name": "duckdb_writer_pool",
                            "slots": 1,
                            "description": "Exactly one Airflow task may write DuckDB",
                            "include_deferred": False,
                        },
                    )
                else:
                    current.raise_for_status()
                    response = client.patch(
                        pool_url,
                        headers=headers,
                        json={
                            # Airflow 3.3's PoolPatchBody requires the aliased
                            # pool field even though its OpenAPI schema marks
                            # it nullable.
                            "pool": "duckdb_writer_pool",
                            "slots": 1,
                            "description": "Exactly one Airflow task may write DuckDB",
                            "include_deferred": False,
                        },
                    )
                response.raise_for_status()
                return
            except (httpx.HTTPError, KeyError):
                if attempt == 29:
                    raise
                time.sleep(2)


if __name__ == "__main__":
    main()
