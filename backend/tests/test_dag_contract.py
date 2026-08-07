from __future__ import annotations

import ast


def test_catalog_ingestion_dag_contract(project_root) -> None:
    dag_path = project_root / "airflow/dags/catalog_ingestion.py"
    source = dag_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    task_ids = {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "task_id" and isinstance(keyword.value, ast.Constant)
    }
    assert {
        "register_run",
        "resolve_active_versions",
        "stage_reviews",
        "deduplicate_reviews",
        "extract_opinion_units",
        "validate_opinion_units",
        "map_to_active_taxonomy",
        "prepare_catalog_delta",
        "commit_catalog_delta",
        "check_taxonomy_rebuild_condition",
    } <= task_ids
    assert 'schedule="0 0 * * *"' in source
    assert "max_active_runs=1" in source
    assert 'pool="duckdb_writer_pool"' in source
    assert "pool_slots=1" in source
    assert "DateTimeSensor" not in source
    assert "generate_static_catalog_report" not in source
    assert "generate_dynamic_decision_proposal" not in source


def test_catalog_reclustering_dag_is_manual_and_single_writer(project_root) -> None:
    dag_path = project_root / "airflow/dags/catalog_reclustering.py"
    source = dag_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    task_ids = {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "task_id" and isinstance(keyword.value, ast.Constant)
    }
    assert task_ids == {
        "register_reclustering",
        "capture_catalog_snapshot",
        "build_full_reclustering",
        "commit_reclustering",
    }
    assert "schedule=None" in source
    assert "max_active_runs=1" in source
    assert 'pool="duckdb_writer_pool"' in source
    assert "pool_slots=1" in source
