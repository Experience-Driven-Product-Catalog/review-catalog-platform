from __future__ import annotations

import pendulum
from airflow.sdk import dag, task
from review_catalog.pipeline import stages


def _mark_failed(context) -> None:
    task_instance = context["task_instance"]
    run_id = task_instance.xcom_pull(task_ids="register_run")
    if run_id:
        stages.mark_run_failed(run_id, str(context.get("exception", "Airflow task failed")))


@dag(
    dag_id="Catalog_ingestion",
    schedule="0 0 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "on_failure_callback": _mark_failed},
    tags=["review-catalog", "duckdb-single-writer"],
    doc_md="""
    The first run atomically migrates the immutable legacy Parquet and Experiment D mapping
    bundle. Later runs are incremental. Report and proposal generation intentionally lives in
    FastAPI's release finalizer, outside this DAG.
    """,
)
def catalog_ingestion():
    @task(task_id="register_run")
    def register_run(**context) -> str:
        dag_run = context["dag_run"]
        return stages.register_run(
            dag_run_id=dag_run.run_id,
            conf=dag_run.conf,
            trigger_type=str(dag_run.run_type),
        )

    run_id = register_run()

    @task(task_id="resolve_active_versions")
    def resolve_active_versions(selected_run_id: str) -> str:
        return stages.resolve_versions_stage(selected_run_id)

    @task(task_id="stage_reviews")
    def stage_reviews(selected_run_id: str) -> str:
        return stages.stage_reviews(selected_run_id)

    @task(task_id="deduplicate_reviews")
    def deduplicate_reviews(selected_run_id: str, staged_path: str) -> str:
        return stages.deduplicate_reviews(selected_run_id, staged_path)

    @task(task_id="extract_opinion_units")
    def extract_opinion_units(
        selected_run_id: str, deduplicated_path: str, versions_path: str
    ) -> str:
        return stages.extract_opinion_units(
            selected_run_id, deduplicated_path, versions_path
        )

    @task(task_id="validate_opinion_units")
    def validate_opinion_units(selected_run_id: str, extracted_path: str) -> str:
        return stages.validate_opinion_units(selected_run_id, extracted_path)

    @task(task_id="map_to_active_taxonomy")
    def map_to_active_taxonomy(
        selected_run_id: str, validated_path: str, versions_path: str
    ) -> str:
        return stages.map_to_active_taxonomy(selected_run_id, validated_path, versions_path)

    @task(task_id="prepare_catalog_delta")
    def prepare_catalog_delta(
        selected_run_id: str,
        deduplicated_path: str,
        mapped_path: str,
        versions_path: str,
    ) -> str:
        return stages.prepare_catalog_delta(
            selected_run_id, deduplicated_path, mapped_path, versions_path
        )

    @task(
        task_id="commit_catalog_delta",
        pool="duckdb_writer_pool",
        pool_slots=1,
    )
    def commit_catalog_delta(selected_run_id: str, delta_path: str) -> str:
        return stages.commit_catalog_delta_stage(selected_run_id, delta_path)

    @task(task_id="check_taxonomy_rebuild_condition")
    def check_taxonomy_rebuild_condition(selected_run_id: str, manifest_path: str) -> dict:
        return stages.check_taxonomy_rebuild_condition(selected_run_id, manifest_path)

    versions = resolve_active_versions(run_id)
    staged = stage_reviews(run_id)
    versions >> staged
    deduplicated = deduplicate_reviews(run_id, staged)
    extracted = extract_opinion_units(run_id, deduplicated, versions)
    validated = validate_opinion_units(run_id, extracted)
    mapped = map_to_active_taxonomy(run_id, validated, versions)
    delta = prepare_catalog_delta(run_id, deduplicated, mapped, versions)
    manifest = commit_catalog_delta(run_id, delta)
    check_taxonomy_rebuild_condition(run_id, manifest)


catalog_ingestion()
