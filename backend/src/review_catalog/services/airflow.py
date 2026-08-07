from __future__ import annotations

from datetime import datetime

import httpx

from review_catalog.settings import Settings


class AirflowClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def trigger_catalog_ingestion(
        self,
        *,
        pipeline_run_id: str,
        conf: dict,
        scheduled_for: datetime | None = None,
    ) -> str:
        dag_run_id = f"api__{pipeline_run_id}"
        payload = {
            "dag_run_id": dag_run_id,
            "logical_date": None,
            "run_after": scheduled_for.isoformat() if scheduled_for else None,
            "conf": {
                **conf,
                "pipeline_run_id": pipeline_run_id,
                "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
            },
        }
        url = (
            f"{self.settings.airflow_base_url.rstrip('/')}/api/v2/dags/"
            f"{self.settings.airflow_dag_id}/dagRuns"
        )
        with httpx.Client(timeout=self.settings.airflow_request_timeout_seconds) as client:
            token_response = client.post(
                f"{self.settings.airflow_base_url.rstrip('/')}/auth/token",
                json={
                    "username": self.settings.airflow_username,
                    "password": self.settings.airflow_password,
                },
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            response = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        return dag_run_id
