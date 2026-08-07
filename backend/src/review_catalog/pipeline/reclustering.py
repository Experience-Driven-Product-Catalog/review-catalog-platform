from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from review_catalog.db.models import ComponentVersion, PipelineRun, utcnow
from review_catalog.db.session import SessionLocal
from review_catalog.normalization.embedding import build_embedder
from review_catalog.normalization.reclustering import build_reclustered_taxonomy
from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.pipeline.artifacts import (
    atomic_write_json,
    canonical_sha256,
    read_json,
    sha256_file,
)
from review_catalog.pipeline.catalog_store import commit_taxonomy_rebuild
from review_catalog.pipeline.stages import _current_snapshot, _set_run, register_run
from review_catalog.services.versions import resolve_active_versions
from review_catalog.settings import get_settings


def register_reclustering_run(
    *, dag_run_id: str, conf: dict[str, Any] | None, trigger_type: str
) -> str:
    run_id = register_run(
        dag_run_id=dag_run_id,
        conf=conf,
        trigger_type=trigger_type,
        dag_id="Catalog_reclustering",
        default_mode="recluster",
    )
    with SessionLocal.begin() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(run_id)
        run.resolved_versions_json = resolve_active_versions(session)
    return run_id


def capture_catalog_snapshot(run_id: str) -> str:
    _set_run(run_id, current_task="capture_catalog_snapshot")
    settings = get_settings()
    capture_dir = settings.work_root / run_id / "captured"
    capture_dir.mkdir(parents=True, exist_ok=True)
    destination = capture_dir / "catalog.duckdb"
    capture_path = capture_dir / "capture_manifest.json"
    if capture_path.is_file():
        existing = read_json(capture_path)
        if (
            not destination.is_file()
            or sha256_file(destination) != existing["captured_snapshot_sha256"]
        ):
            raise RuntimeError("captured catalog retry artifacts are incomplete or changed")
        return str(capture_path)
    source = _current_snapshot()
    if source is None or not source.is_file():
        raise FileNotFoundError("a published or committed catalog snapshot is required")
    source_sha = sha256_file(source)
    temporary = capture_dir / ".catalog.duckdb.tmp"
    shutil.copy2(source, temporary)
    if sha256_file(temporary) != source_sha:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("catalog snapshot changed while it was being captured")
    os.replace(temporary, destination)
    with duckdb.connect(str(destination), read_only=True) as connection:
        review_count, opinion_count = connection.execute(
            "SELECT (SELECT count(*) FROM reviews), (SELECT count(*) FROM opinion_units)"
        ).fetchone()
    payload = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "source_snapshot_path": str(source),
        "captured_snapshot_path": str(destination),
        "captured_snapshot_sha256": source_sha,
        "review_count": int(review_count),
        "opinion_unit_count": int(opinion_count),
    }
    atomic_write_json(capture_path, payload)
    return str(capture_path)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd")
        pd.read_parquet(temporary, engine="pyarrow")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _version_suffix(run_id: str) -> str:
    return run_id.removeprefix("recluster_").replace("-", "")


def build_full_reclustering(run_id: str, capture_manifest_path: str) -> str:
    _set_run(run_id, current_task="build_full_reclustering")
    settings = get_settings()
    capture = read_json(capture_manifest_path)
    snapshot_path = Path(capture["captured_snapshot_path"])
    config = yaml.safe_load(settings.normalization_config_path.read_text(encoding="utf-8"))
    with SessionLocal() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(run_id)
        versions = dict(run.resolved_versions_json)
        if not versions:
            versions = resolve_active_versions(session)
            run.resolved_versions_json = versions
            session.commit()
    active_manifest_path = Path(str(versions["mapping_table"]["artifact_uri"]))
    active_manifest = load_taxonomy_manifest(active_manifest_path)
    if config["embedding"]["model_id"] != active_manifest.embedding_model_id:
        raise ValueError("active mapping table and configured embedding model differ")
    normalization_fingerprint = canonical_sha256(
        {
            "normalization_version": config["project"]["normalization_version"],
            "filters": config["filters"],
            "embedding": config["embedding"],
            "clustering": config["clustering"],
            "canonical_label": config["canonical_label"],
        }
    )
    if normalization_fingerprint != active_manifest.normalization_config_sha256:
        raise ValueError("configured reclustering parameters differ from the active taxonomy")
    with duckdb.connect(str(snapshot_path), read_only=True) as connection:
        opinion_units = connection.execute(
            """
            SELECT opinion_unit_id, review_id, raw_aspect, raw_status
            FROM opinion_units ORDER BY opinion_unit_id
            """
        ).fetchdf()
    taxonomy = build_reclustered_taxonomy(
        opinion_units,
        embedder=build_embedder(settings),
        config=config,
    )

    suffix = _version_suffix(run_id)
    mapping_version_id = f"mapping-table-{suffix}"
    embedding_version_id = f"embedding-model-{suffix}"
    final_dir = settings.work_root / run_id / "reclustering-bundle"
    if final_dir.exists():
        existing = final_dir / "reclustering_manifest.json"
        if existing.is_file():
            return str(existing)
        raise RuntimeError(f"incomplete reclustering bundle requires inspection: {final_dir}")
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{run_id}-bundle-", dir=settings.work_root / run_id)
    )
    try:
        frames = {
            "experiment_d.parquet": taxonomy.assignments,
            "experiment_d_aspect_nodes.parquet": taxonomy.aspect_nodes,
            "experiment_d_aspect_clusters.parquet": taxonomy.aspect_clusters,
            "experiment_d_status_nodes.parquet": taxonomy.status_nodes,
            "experiment_d_status_clusters.parquet": taxonomy.status_clusters,
        }
        for name, frame in frames.items():
            _atomic_write_parquet(frame, temporary_dir / name)
        embedding_manifest = {
            "schema_version": "1.0.0",
            "embedding_model_version_id": embedding_version_id,
            "model_id": active_manifest.embedding_model_id,
            "artifact_sha256": active_manifest.embedding_model_artifact_sha256,
            "artifact_uri": versions["embedding_model"]["artifact_uri"],
            "weights_reused_without_retraining": True,
            "parameters": config["embedding"],
            "recluster_run_id": run_id,
        }
        atomic_write_json(temporary_dir / "embedding_model_manifest.json", embedding_manifest)
        artifact_names = [*frames, "embedding_model_manifest.json"]
        artifact_hashes = {
            name: sha256_file(temporary_dir / name) for name in sorted(artifact_names)
        }
        counts = {
            "source_reviews": int(capture["review_count"]),
            "source_opinion_units": len(opinion_units),
            "taxonomy_eligible_opinion_units": int(
                (taxonomy.assignments["mapping_state"] == "mapped_exact").sum()
            ),
            "excluded_opinion_units": int(
                (taxonomy.assignments["mapping_state"] == "excluded_taxonomy").sum()
            ),
            "aspect_mapping_expressions": len(taxonomy.aspect_nodes),
            "aspect_clusters": len(taxonomy.aspect_clusters),
            "aspect_status_mapping_expressions": len(taxonomy.status_nodes),
            "status_clusters": len(taxonomy.status_clusters),
        }
        taxonomy_manifest = {
            "schema_version": "1.0.0",
            "mapping_table_version_id": mapping_version_id,
            "normalization_version": active_manifest.normalization_version,
            "normalization_run_id": run_id,
            "normalization_config_sha256": active_manifest.normalization_config_sha256,
            "embedding_model_id": active_manifest.embedding_model_id,
            "embedding_model_artifact_sha256": active_manifest.embedding_model_artifact_sha256,
            "source_prompt_version_id": active_manifest.source_prompt_version_id,
            "source_prompt_sha256": active_manifest.source_prompt_sha256,
            "source_extraction_model_version_id": (
                active_manifest.source_extraction_model_version_id
            ),
            "source_extraction_backend": active_manifest.source_extraction_backend,
            "source_extraction_model": active_manifest.source_extraction_model,
            "source_extraction_reasoning_effort": (
                active_manifest.source_extraction_reasoning_effort
            ),
            "metric": active_manifest.metric,
            "linkage": active_manifest.linkage,
            "aspect_distance_threshold": float(
                config["clustering"]["experiment_d"]["aspect"]["distance_threshold"]
            ),
            "status_distance_threshold": float(
                config["clustering"]["experiment_d"]["status"]["distance_threshold"]
            ),
            "counts": counts,
            "artifacts": artifact_hashes,
        }
        taxonomy_manifest_path = temporary_dir / "taxonomy_manifest.json"
        atomic_write_json(taxonomy_manifest_path, taxonomy_manifest)
        manifest = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "capture": capture,
            "mapping_table_version_id": mapping_version_id,
            "embedding_model_version_id": embedding_version_id,
            "taxonomy_manifest_sha256": sha256_file(taxonomy_manifest_path),
            "counts": counts,
        }
        atomic_write_json(temporary_dir / "reclustering_manifest.json", manifest)
        os.replace(temporary_dir, final_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return str(final_dir / "reclustering_manifest.json")


def _register_pending_component_versions(
    *, run_id: str, bundle_dir: Path, manifest: dict[str, Any], taxonomy_content_sha256: str
) -> dict[str, dict[str, str | None]]:
    settings = get_settings()
    taxonomy = load_taxonomy_manifest(bundle_dir / "taxonomy_manifest.json")
    embedding = read_json(bundle_dir / "embedding_model_manifest.json")
    final_taxonomy_manifest = (
        settings.release_root
        / f"release-{run_id}"
        / "taxonomy"
        / taxonomy.mapping_table_version_id
        / "taxonomy_manifest.json"
    )
    with SessionLocal.begin() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(run_id)
        desired = (
            ComponentVersion(
                id=taxonomy.mapping_table_version_id,
                component_type="mapping_table",
                version=f"{taxonomy.normalization_version}:{run_id}",
                content_sha256=taxonomy_content_sha256,
                artifact_uri=str(final_taxonomy_manifest),
                metadata_json={
                    "normalization_run_id": run_id,
                    "normalization_config_sha256": taxonomy.normalization_config_sha256,
                    "reclustered": True,
                },
                is_active=False,
            ),
            ComponentVersion(
                id=str(manifest["embedding_model_version_id"]),
                component_type="embedding_model",
                version=f"{taxonomy.embedding_model_id}@{run_id}",
                content_sha256=taxonomy.embedding_model_artifact_sha256,
                artifact_uri=str(embedding["artifact_uri"]),
                metadata_json={
                    "model": taxonomy.embedding_model_id,
                    "artifact_sha256": taxonomy.embedding_model_artifact_sha256,
                    "weights_reused_without_retraining": True,
                    "recluster_run_id": run_id,
                },
                is_active=False,
            ),
        )
        for component in desired:
            existing = session.get(ComponentVersion, component.id)
            if existing is None:
                session.add(component)
            elif (
                existing.component_type != component.component_type
                or existing.version != component.version
                or existing.content_sha256 != component.content_sha256
                or existing.artifact_uri != component.artifact_uri
            ):
                raise RuntimeError(f"immutable component version mismatch: {component.id}")
        versions = dict(run.resolved_versions_json)
        versions["mapping_table"] = {
            "id": taxonomy.mapping_table_version_id,
            "version": desired[0].version,
            "content_sha256": taxonomy_content_sha256,
            "artifact_uri": str(final_taxonomy_manifest),
        }
        versions["embedding_model"] = {
            "id": desired[1].id,
            "version": desired[1].version,
            "content_sha256": desired[1].content_sha256,
            "artifact_uri": desired[1].artifact_uri,
        }
        conf = dict(run.conf_json)
        conf["activate_component_versions"] = [
            {"component_type": component.component_type, "id": component.id}
            for component in desired
        ]
        run.conf_json = conf
        run.resolved_versions_json = versions
        session.flush()
        return versions


def _mark_reclustering_pipeline_succeeded(
    run_id: str, final_staging: Path, reclustering_manifest: dict[str, Any]
) -> None:
    with SessionLocal.begin() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(run_id)
        run.state = "pipeline_succeeded"
        run.current_task = None
        run.pipeline_finished_at = run.pipeline_finished_at or utcnow()
        run.staged_release_path = str(final_staging)
        run.committed_review_count = int(reclustering_manifest["capture"]["review_count"])
        run.candidate_count = 0
        run.taxonomy_rebuild_recommended = False
        run.error_message = None


def commit_reclustering(run_id: str, reclustering_manifest_path: str) -> str:
    _set_run(run_id, current_task="commit_reclustering")
    settings = get_settings()
    bundle_dir = Path(reclustering_manifest_path).parent
    reclustering_manifest = read_json(reclustering_manifest_path)
    taxonomy_manifest_path = bundle_dir / "taxonomy_manifest.json"
    taxonomy = load_taxonomy_manifest(taxonomy_manifest_path)
    final_staging = settings.release_staging_root / run_id
    if final_staging.exists():
        pipeline_manifest_path = final_staging / "pipeline_manifest.json"
        if not pipeline_manifest_path.is_file():
            raise RuntimeError(f"incomplete release staging requires inspection: {final_staging}")
        _mark_reclustering_pipeline_succeeded(run_id, final_staging, reclustering_manifest)
        return str(pipeline_manifest_path)
    previous_snapshot = _current_snapshot()
    if previous_snapshot is None:
        raise FileNotFoundError("latest catalog snapshot disappeared before commit")
    temporary_staging = Path(
        tempfile.mkdtemp(prefix=f".{run_id}-release-", dir=settings.release_staging_root)
    )
    try:
        taxonomy_destination = temporary_staging / "taxonomy" / taxonomy.mapping_table_version_id
        shutil.copytree(bundle_dir, taxonomy_destination)
        snapshot_path = temporary_staging / "catalog.duckdb"
        counts = commit_taxonomy_rebuild(
            destination=snapshot_path,
            previous_snapshot=previous_snapshot,
            artifact_root=taxonomy_destination,
            taxonomy_manifest=taxonomy,
            embedding_model_version_id=str(reclustering_manifest["embedding_model_version_id"]),
            recluster_run_id=run_id,
            captured_snapshot_sha256=str(
                reclustering_manifest["capture"]["captured_snapshot_sha256"]
            ),
            writer_lock_path=settings.writer_lock_path,
            writer_identity="airflow",
        )
        versions = _register_pending_component_versions(
            run_id=run_id,
            bundle_dir=bundle_dir,
            manifest=reclustering_manifest,
            taxonomy_content_sha256=sha256_file(taxonomy_manifest_path),
        )
        pipeline_manifest = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "pipeline_type": "full_reclustering",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "versions": versions,
            "snapshot_sha256": sha256_file(snapshot_path),
            "taxonomy_manifest_sha256": sha256_file(taxonomy_manifest_path),
            "counts": counts,
            "report_artifacts_included": False,
            "publishable": False,
        }
        atomic_write_json(temporary_staging / "pipeline_manifest.json", pipeline_manifest)
        os.replace(temporary_staging, final_staging)
    except Exception:
        shutil.rmtree(temporary_staging, ignore_errors=True)
        raise
    _mark_reclustering_pipeline_succeeded(run_id, final_staging, reclustering_manifest)
    return str(final_staging / "pipeline_manifest.json")
