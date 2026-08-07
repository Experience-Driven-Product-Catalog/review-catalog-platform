from __future__ import annotations

import pendulum
from airflow.sdk import dag, task
from review_catalog.pipeline import reclustering
from review_catalog.pipeline.stages import mark_run_failed


def _mark_failed(context) -> None:
    task_instance = context["task_instance"]
    run_id = task_instance.xcom_pull(task_ids="register_reclustering")
    if run_id:
        mark_run_failed(run_id, str(context.get("exception", "Airflow task failed")))


@dag(
    dag_id="Catalog_reclustering",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "on_failure_callback": _mark_failed},
    tags=["review-catalog", "manual-only", "full-reclustering"],
    doc_md="""
    Manual-only full reclustering. The first task copies the current immutable catalog
    snapshot, so data arriving after that boundary stays on the previous taxonomy until
    the next manual run. The final task alone writes DuckDB through duckdb_writer_pool.
    """,
)
def catalog_reclustering():
    @task(task_id="register_reclustering")
    def register_reclustering(**context) -> str:
        dag_run = context["dag_run"]
        return reclustering.register_reclustering_run(
            dag_run_id=dag_run.run_id,
            conf=dag_run.conf,
            trigger_type=str(dag_run.run_type),
        )

    run_id = register_reclustering()

    @task(task_id="capture_catalog_snapshot")
    def capture_catalog_snapshot(selected_run_id: str) -> str:
        return reclustering.capture_catalog_snapshot(selected_run_id)

    @task(task_id="build_full_reclustering")
    def build_full_reclustering(
        selected_run_id: str, capture_manifest_path: str
    ) -> str:
        return reclustering.build_full_reclustering(
            selected_run_id, capture_manifest_path
        )

    @task(
        task_id="commit_reclustering",
        pool="duckdb_writer_pool",
        pool_slots=1,
    )
    def commit_reclustering(
        selected_run_id: str, reclustering_manifest_path: str
    ) -> str:
        return reclustering.commit_reclustering(
            selected_run_id, reclustering_manifest_path
        )

    capture = capture_catalog_snapshot(run_id)
    bundle = build_full_reclustering(run_id, capture)
    commit_reclustering(run_id, bundle)


catalog_reclustering()
