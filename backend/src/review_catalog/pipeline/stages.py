from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from review_catalog.db.models import (
    CatalogRelease,
    DemoReview,
    DemoSubmission,
    PipelineRun,
    new_id,
    utcnow,
)
from review_catalog.db.session import SessionLocal, create_schema
from review_catalog.extraction import ReviewInput, build_extractor
from review_catalog.extraction.contracts import ExtractionResult
from review_catalog.normalization.embedding import build_embedder
from review_catalog.normalization.mapper import MappingDecision, TaxonomyMapper
from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.pipeline.artifacts import (
    atomic_write_json,
    canonical_sha256,
    read_json,
    sha256_file,
)
from review_catalog.pipeline.catalog_store import (
    catalog_has_legacy_migration,
    commit_catalog_delta,
    existing_review_hashes,
)
from review_catalog.services.versions import (
    bootstrap_component_versions,
    resolve_active_versions,
)
from review_catalog.settings import get_settings


def _work_file(run_id: str, name: str) -> Path:
    settings = get_settings()
    path = settings.work_root / run_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _set_run(run_id: str, **values: Any) -> None:
    with SessionLocal.begin() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(f"pipeline run not found: {run_id}")
        for key, value in values.items():
            setattr(run, key, value)


def register_run(
    *,
    dag_run_id: str,
    conf: dict[str, Any] | None,
    trigger_type: str,
    dag_id: str | None = None,
    default_mode: str = "catalog",
) -> str:
    create_schema()
    settings = get_settings()
    incoming = dict(conf or {})
    run_id = str(incoming.get("pipeline_run_id") or new_id("run"))
    with SessionLocal.begin() as session:
        bootstrap_component_versions(session, settings)
        run = session.get(PipelineRun, run_id)
        if run is None:
            run = PipelineRun(
                id=run_id,
                dag_id=dag_id or settings.airflow_dag_id,
                dag_run_id=dag_run_id,
                trigger_type=trigger_type,
                mode=str(incoming.get("mode", default_mode)),
                state="running",
                current_task="register_run",
                conf_json=incoming,
                started_at=utcnow(),
                work_dir=str(settings.work_root / run_id),
            )
            session.add(run)
        else:
            expected_dag_id = dag_id or settings.airflow_dag_id
            expected_mode = str(incoming.get("mode", default_mode))
            if run.dag_id != expected_dag_id or run.mode != expected_mode:
                raise ValueError(f"pipeline run identity differs on retry: {run_id}")
            run.dag_run_id = dag_run_id
            run.state = "running"
            run.current_task = "register_run"
            run.started_at = run.started_at or utcnow()
    return run_id


def resolve_versions_stage(run_id: str) -> str:
    _set_run(run_id, current_task="resolve_active_versions")
    with SessionLocal.begin() as session:
        versions = resolve_active_versions(session)
        run = session.get(PipelineRun, run_id)
        run.resolved_versions_json = versions
    destination = _work_file(run_id, "versions.json")
    atomic_write_json(destination, versions)
    return str(destination)


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def stage_reviews(run_id: str) -> str:
    _set_run(run_id, current_task="stage_reviews")
    settings = get_settings()
    with SessionLocal() as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise KeyError(run_id)
        conf = dict(run.conf_json)
        if run.mode == "demo_submission":
            submission_id = str(conf["submission_id"])
            submission = session.get(DemoSubmission, submission_id)
            if submission is None:
                raise KeyError(f"demo submission not found: {submission_id}")
            reviews = session.scalars(
                select(DemoReview)
                .where(DemoReview.submission_id == submission_id)
                .order_by(DemoReview.position)
            ).all()
            rows = [
                {
                    "review_id": new_id("review"),
                    "source": "demo_ui",
                    "source_review_id": review.id,
                    "product_id": submission.product_id,
                    "product_name": str(conf["product_name"]),
                    "category": str(conf.get("category", "모니터")),
                    "review_text": review.review_text,
                    "content_sha256": review.content_sha256,
                    "demo_review_id": review.id,
                }
                for review in reviews
            ]
            submission.state = "running"
            session.commit()
            payload = {"mode": "incremental", "reviews": rows}
        elif not catalog_has_legacy_migration(_current_snapshot()):
            if conf.get("review_limit") not in (None, 749):
                raise ValueError(
                    "initial legacy migration is atomic and must import all 749 reviews"
                )
            payload = {
                "mode": "legacy_migration",
                "reviews": [],
                "source_review_count": 749,
                "source_normalization_run_id": "20260803-213339",
            }
        else:
            inbox = read_json(settings.ingestion_inbox_path)
            if not isinstance(inbox, list):
                raise TypeError("ingestion inbox must be a JSON array")
            limit = int(conf.get("review_limit", len(inbox)))
            rows = []
            for row in inbox[:limit]:
                text = str(row["review_text"]).strip()
                rows.append(
                    {
                        "review_id": new_id("review"),
                        "source": str(row.get("source", "catalog_inbox")),
                        "source_review_id": str(row["source_review_id"]),
                        "product_id": str(row["product_id"]),
                        "product_name": str(row["product_name"]),
                        "category": str(row.get("category", "모니터")),
                        "review_text": text,
                        "content_sha256": _content_sha(text),
                        "demo_review_id": None,
                    }
                )
            payload = {"mode": "incremental", "reviews": rows}
    destination = _work_file(run_id, "staged_reviews.json")
    atomic_write_json(destination, payload)
    _set_run(
        run_id,
        staged_review_count=(
            int(payload["source_review_count"])
            if payload["mode"] == "legacy_migration"
            else len(payload["reviews"])
        ),
    )
    return str(destination)


def _current_snapshot() -> Path | None:
    with SessionLocal() as session:
        pending = session.scalar(
            select(PipelineRun)
            .where(
                PipelineRun.state.in_(["pipeline_succeeded", "release_failed"]),
                PipelineRun.staged_release_path.is_not(None),
            )
            .order_by(PipelineRun.pipeline_finished_at.desc())
        )
        if pending:
            staged_snapshot = Path(pending.staged_release_path) / "catalog.duckdb"
            if staged_snapshot.is_file():
                return staged_snapshot
            # The release finalizer may be in the narrow window after the
            # atomic directory rename and before its Postgres transaction.
            renamed_snapshot = (
                get_settings().release_root / f"release-{pending.id}" / "catalog.duckdb"
            )
            if renamed_snapshot.is_file():
                return renamed_snapshot
        release = session.scalar(
            select(CatalogRelease).where(
                CatalogRelease.is_current.is_(True), CatalogRelease.state == "published"
            )
        )
        return Path(release.snapshot_path) if release else None


def deduplicate_reviews(run_id: str, staged_reviews_path: str) -> str:
    _set_run(run_id, current_task="deduplicate_reviews")
    staged = read_json(staged_reviews_path)
    if staged["mode"] == "legacy_migration":
        destination = _work_file(run_id, "deduplicated_reviews.json")
        atomic_write_json(destination, staged)
        return str(destination)
    rows = staged["reviews"]
    existing = existing_review_hashes(_current_snapshot())
    seen_catalog_hashes = set(existing)
    seen_demo_review_ids: set[str] = set()
    unique = []
    for row in rows:
        if row["source"] == "demo_ui":
            # A UI-submitted review is an event, not merely catalog text. It
            # must survive even when its text already exists so the promised
            # one-proposal-per-input contract can be fulfilled.
            key = str(row["demo_review_id"])
            if key in seen_demo_review_ids:
                continue
            seen_demo_review_ids.add(key)
        else:
            key = row["content_sha256"]
            if key in seen_catalog_hashes:
                continue
            seen_catalog_hashes.add(key)
        unique.append(row)
    destination = _work_file(run_id, "deduplicated_reviews.json")
    atomic_write_json(
        destination,
        {
            "mode": "incremental",
            "reviews": unique,
            "duplicate_count": len(rows) - len(unique),
        },
    )
    return str(destination)


def extract_opinion_units(run_id: str, deduplicated_path: str, versions_path: str) -> str:
    _set_run(run_id, current_task="extract_opinion_units")
    settings = get_settings()
    staged = read_json(deduplicated_path)
    reviews = staged["reviews"]
    versions = read_json(versions_path)
    inputs = [
        ReviewInput(
            review_id=row["review_id"],
            product_id=row["product_id"],
            product_name=row["product_name"],
            product_category=row["category"],
            review=row["review_text"],
            source=row["source"],
            source_review_id=row["source_review_id"],
            demo_review_id=row["demo_review_id"],
        )
        for row in reviews
    ]
    extractor = build_extractor(
        settings,
        prompt_version_id=versions["opinion_unit_prompt"]["id"],
        model_version_id=versions["extraction_model"]["id"],
    )
    results = extractor.extract(inputs) if inputs else []
    destination = _work_file(run_id, "extracted_opinion_units.json")
    atomic_write_json(destination, [result.model_dump(mode="json") for result in results])
    return str(destination)


def validate_opinion_units(run_id: str, extracted_path: str) -> str:
    _set_run(run_id, current_task="validate_opinion_units")
    raw_results = read_json(extracted_path)
    results = [ExtractionResult.model_validate(result) for result in raw_results]
    seen_reviews: set[str] = set()
    for result in results:
        if result.review.review_id in seen_reviews:
            raise ValueError(f"duplicate extraction result: {result.review.review_id}")
        seen_reviews.add(result.review.review_id)
    destination = _work_file(run_id, "validated_opinion_units.json")
    atomic_write_json(destination, [result.model_dump(mode="json") for result in results])
    return str(destination)


def map_to_active_taxonomy(run_id: str, validated_path: str, versions_path: str) -> str:
    _set_run(run_id, current_task="map_to_active_taxonomy")
    settings = get_settings()
    versions = read_json(versions_path)
    mapping_artifact_uri = versions["mapping_table"].get("artifact_uri")
    manifest_path = (
        Path(str(mapping_artifact_uri)) if mapping_artifact_uri else settings.taxonomy_manifest_path
    )
    manifest = load_taxonomy_manifest(manifest_path)
    if manifest.content_sha256 != versions["mapping_table"]["content_sha256"]:
        raise ValueError("active taxonomy manifest hash differs from resolved mapping version")
    raw_results = read_json(validated_path)
    mapper = None
    clustering_config = yaml.safe_load(settings.normalization_config_path.read_text())["clustering"]
    excluded_aspects = set(
        yaml.safe_load(settings.normalization_config_path.read_text())["filters"][
            "excluded_raw_aspects"
        ]
    )
    if raw_results:
        snapshot = _current_snapshot()
        if snapshot is None:
            raise RuntimeError("mapping inference requires the migrated catalog snapshot")
        mapper = TaxonomyMapper(
            snapshot_path=snapshot,
            mapping_table_version_id=versions["mapping_table"]["id"],
            manifest=manifest,
            embedder=build_embedder(settings),
            clustering_config=clustering_config,
        )
    mapped: list[dict[str, Any]] = []
    for raw_result in raw_results:
        result = ExtractionResult.model_validate(raw_result)
        units = []
        for position, unit in enumerate(result.opinion_units, start=1):
            if unit.raw_aspect in excluded_aspects:
                decision = MappingDecision(
                    mapping_state="excluded_taxonomy",
                    aspect_id=None,
                    aspect=None,
                    status_id=None,
                    status=None,
                    suggested_aspect_id=None,
                    suggested_aspect=None,
                    aspect_distance=None,
                    aspect_membership_max_distance=None,
                    aspect_centroid_distance=None,
                    aspect_second_nearest_distance=None,
                    aspect_distance_margin=None,
                    aspect_candidate_eligible=None,
                    suggested_status_id=None,
                    suggested_status=None,
                    status_distance=None,
                    status_membership_max_distance=None,
                    status_centroid_distance=None,
                    status_second_nearest_distance=None,
                    status_distance_margin=None,
                    status_candidate_eligible=None,
                )
            else:
                decision = mapper.map(unit)
            units.append(
                {
                    "opinion_unit_id": new_id("ou"),
                    "review_id": result.review.review_id,
                    "unit_position": position,
                    **unit.model_dump(),
                    **asdict(decision),
                    "prompt_version_id": result.prompt_version_id,
                    "model_version_id": result.model_version_id,
                    "mapping_table_version_id": versions["mapping_table"]["id"],
                    "embedding_model_version_id": versions["embedding_model"]["id"],
                    "normalization_run_id": manifest.normalization_run_id,
                    "normalization_config_sha256": manifest.normalization_config_sha256,
                    "extraction_response_sha256": result.raw_response_sha256,
                    "ingestion_run_id": run_id,
                }
            )
        mapped.append({"review": result.review.model_dump(), "opinion_units": units})
    destination = _work_file(run_id, "mapped_opinion_units.json")
    atomic_write_json(destination, mapped)
    return str(destination)


def prepare_catalog_delta(
    run_id: str,
    deduplicated_path: str,
    mapped_path: str,
    versions_path: str,
) -> str:
    _set_run(run_id, current_task="prepare_catalog_delta")
    staged = read_json(deduplicated_path)
    reviews = staged["reviews"]
    mapped = read_json(mapped_path)
    mapped_by_review = {row["review"]["review_id"]: row["opinion_units"] for row in mapped}
    products = {
        row["product_id"]: {
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "category": row["category"],
        }
        for row in reviews
    }
    delta = {
        "run_id": run_id,
        "versions": read_json(versions_path),
        "products": sorted(products.values(), key=lambda row: row["product_id"]),
        "reviews": [
            {
                "review_id": row["review_id"],
                "source": row["source"],
                "source_review_id": row["source_review_id"],
                "product_id": row["product_id"],
                "review_text": row["review_text"],
                "content_sha256": row["content_sha256"],
                "ingestion_run_id": run_id,
                "demo_review_id": row["demo_review_id"],
            }
            for row in reviews
        ],
        "opinion_units": [
            unit for row in reviews for unit in mapped_by_review.get(row["review_id"], [])
        ],
    }
    if staged["mode"] == "legacy_migration":
        delta["legacy_migration"] = {
            "migration_id": "legacy-monitor-20260803-213339",
            "source_review_count": staged["source_review_count"],
        }
    delta["delta_sha256"] = canonical_sha256(delta)
    destination = _work_file(run_id, "catalog_delta.json")
    atomic_write_json(destination, delta)
    return str(destination)


def commit_catalog_delta_stage(run_id: str, delta_path: str) -> str:
    _set_run(run_id, current_task="commit_catalog_delta")
    settings = get_settings()
    delta = read_json(delta_path)
    staging_dir = settings.release_staging_root / run_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = staging_dir / "catalog.duckdb"
    manifest_path = staging_dir / "pipeline_manifest.json"
    if snapshot_path.exists() and manifest_path.exists():
        existing_manifest = read_json(manifest_path)
        if existing_manifest.get("delta_sha256") == delta["delta_sha256"] and existing_manifest.get(
            "snapshot_sha256"
        ) == sha256_file(snapshot_path):
            counts = existing_manifest["counts"]
            _set_run(
                run_id,
                state="committed",
                staged_release_path=str(staging_dir),
                committed_review_count=counts["review_count"],
                candidate_count=counts["candidate_count"],
            )
            return str(manifest_path)
        raise RuntimeError(f"staging directory contains conflicting artifacts: {staging_dir}")
    if snapshot_path.exists() or manifest_path.exists():
        raise RuntimeError(
            f"staging directory is incomplete and requires inspection: {staging_dir}"
        )
    mapping_artifact_uri = delta["versions"]["mapping_table"].get("artifact_uri")
    taxonomy_manifest = load_taxonomy_manifest(
        Path(str(mapping_artifact_uri)) if mapping_artifact_uri else settings.taxonomy_manifest_path
    )
    counts = commit_catalog_delta(
        destination=snapshot_path,
        previous_snapshot=_current_snapshot(),
        delta=delta,
        taxonomy_manifest=taxonomy_manifest,
        legacy_migration_root=settings.legacy_migration_root,
        writer_lock_path=settings.writer_lock_path,
        writer_identity="airflow",
    )
    pipeline_manifest = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "versions": delta["versions"],
        "delta_sha256": delta["delta_sha256"],
        "snapshot_sha256": sha256_file(snapshot_path),
        "counts": counts,
        "report_artifacts_included": False,
        "publishable": False,
    }
    atomic_write_json(manifest_path, pipeline_manifest)
    _set_run(
        run_id,
        state="committed",
        staged_release_path=str(staging_dir),
        committed_review_count=counts["review_count"],
        candidate_count=counts["candidate_count"],
    )
    return str(manifest_path)


def check_taxonomy_rebuild_condition(run_id: str, manifest_path: str) -> dict[str, Any]:
    _set_run(run_id, current_task="check_taxonomy_rebuild_condition")
    settings = get_settings()
    counts = read_json(manifest_path)["counts"]
    recommended = counts["candidate_count"] >= settings.taxonomy_rebuild_candidate_threshold
    _set_run(
        run_id,
        state="pipeline_succeeded",
        current_task=None,
        pipeline_finished_at=utcnow(),
        taxonomy_rebuild_recommended=recommended,
    )
    return {
        "recommended": recommended,
        "candidate_count": counts["candidate_count"],
        "threshold": settings.taxonomy_rebuild_candidate_threshold,
        "action": "record_only_demo_does_not_rebuild_taxonomy",
    }


def mark_run_failed(run_id: str, error: str) -> None:
    _set_run(
        run_id,
        state="failed",
        current_task=None,
        completed_at=utcnow(),
        error_message=error[:4000],
    )
    with SessionLocal.begin() as session:
        submission = session.scalar(
            select(DemoSubmission).where(DemoSubmission.pipeline_run_id == run_id)
        )
        if submission:
            submission.state = "failed"
            submission.error_message = error[:4000]
