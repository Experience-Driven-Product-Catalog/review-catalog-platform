from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from sqlalchemy import select, update

from review_catalog import __version__
from review_catalog.db.models import (
    CatalogRelease,
    ComponentVersion,
    DemoSubmission,
    PipelineRun,
    ReportArtifact,
    new_id,
    utcnow,
)
from review_catalog.db.session import SessionLocal
from review_catalog.pipeline.artifacts import atomic_write_json, read_json, sha256_file
from review_catalog.reporting import (
    generate_dynamic_decision_proposal,
    generate_static_catalog_report,
)
from review_catalog.reporting.common import fetch_dicts
from review_catalog.settings import get_settings


def _artifact_record(
    *,
    release_id: str,
    report_type: str,
    relative_path: str,
    sha256: str,
    product_id: str | None = None,
    demo_review_id: str | None = None,
) -> dict:
    return {
        "id": new_id("artifact"),
        "release_id": release_id,
        "report_type": report_type,
        "product_id": product_id,
        "demo_review_id": demo_review_id,
        "relative_path": relative_path,
        "sha256": sha256,
        "generator_version": __version__,
    }


def _register_published_directory(*, release_id: str, run_id: str, final_path: Path) -> str:
    """Register an already atomically published directory in Postgres.

    Keeping this step replayable closes the small crash window between the
    filesystem rename and the metadata transaction.
    """
    manifest_path = final_path / "release_manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("release_id") != release_id or manifest.get("pipeline_run_id") != run_id:
        raise ValueError("published release manifest identity does not match the pipeline run")

    snapshot_path = final_path / str(manifest["snapshot"]["path"])
    snapshot_sha = sha256_file(snapshot_path)
    if snapshot_sha != manifest["snapshot"]["sha256"]:
        raise ValueError("published DuckDB snapshot hash differs from release manifest")

    artifacts: list[dict] = []
    release_root = final_path.resolve()
    for report in manifest["reports"]:
        artifact_path = (final_path / str(report["path"])).resolve()
        if not artifact_path.is_relative_to(release_root):
            raise ValueError("report artifact escapes the immutable release directory")
        artifact_sha = sha256_file(artifact_path)
        if artifact_sha != report["sha256"]:
            raise ValueError(f"report artifact hash mismatch: {report['path']}")
        artifacts.append(
            _artifact_record(
                release_id=release_id,
                report_type=str(report["report_type"]),
                product_id=report.get("product_id"),
                demo_review_id=report.get("demo_review_id"),
                relative_path=str(report["path"]),
                sha256=artifact_sha,
            )
        )

    with SessionLocal.begin() as session:
        existing = session.get(CatalogRelease, release_id)
        if existing is None:
            session.execute(
                update(CatalogRelease)
                .where(CatalogRelease.is_current.is_(True))
                .values(is_current=False)
            )
            session.add(
                CatalogRelease(
                    id=release_id,
                    pipeline_run_id=run_id,
                    previous_release_id=manifest.get("previous_release_id"),
                    state="published",
                    release_path=str(final_path),
                    snapshot_path=str(snapshot_path),
                    snapshot_sha256=snapshot_sha,
                    manifest_path=str(manifest_path),
                    manifest_sha256=sha256_file(manifest_path),
                    version_manifest_json=dict(manifest["versions"]),
                    is_current=True,
                    published_at=utcnow(),
                )
            )
            # Report artifacts and DemoSubmission.release_id both reference
            # this row without ORM relationships that can order the flush.
            session.flush()
            for artifact in artifacts:
                session.add(ReportArtifact(**artifact))
        elif existing.state != "published":
            raise ValueError(f"release metadata exists in unexpected state: {existing.state}")

        run = session.get(PipelineRun, run_id)
        if run is None:
            raise ValueError(f"pipeline run disappeared during release registration: {run_id}")
        requested_activations = dict(run.conf_json).get("activate_component_versions", [])
        for activation in requested_activations:
            component_type = str(activation["component_type"])
            component_id = str(activation["id"])
            component = session.get(ComponentVersion, component_id)
            if component is None or component.component_type != component_type:
                raise ValueError(f"pending component version is invalid: {component_id}")
            resolved = dict(run.resolved_versions_json).get(component_type)
            if not resolved or resolved.get("id") != component_id:
                raise ValueError(
                    f"release version manifest does not select pending component: {component_id}"
                )
            session.execute(
                update(ComponentVersion)
                .where(
                    ComponentVersion.component_type == component_type,
                    ComponentVersion.is_active.is_(True),
                    ComponentVersion.id != component_id,
                )
                .values(is_active=False)
            )
            component.is_active = True
        run.state = "completed"
        run.current_task = None
        run.completed_at = run.completed_at or utcnow()
        run.error_message = None
        submission = session.scalar(
            select(DemoSubmission).where(DemoSubmission.pipeline_run_id == run_id)
        )
        if submission:
            submission.state = "completed"
            submission.release_id = release_id
            submission.completed_at = submission.completed_at or utcnow()
            submission.error_message = None
    return release_id


def finalize_catalog_release(run_id: str) -> str:
    """Read a staged snapshot, generate every report, then publish the directory atomically."""
    settings = get_settings()
    release_id = f"release-{run_id}"
    final_path = settings.release_root / release_id
    with SessionLocal() as session:
        existing = session.get(CatalogRelease, release_id)
        if existing and existing.state == "published":
            return release_id
        run = session.get(PipelineRun, run_id)
        if run is None or run.state not in {"pipeline_succeeded", "release_failed"}:
            raise ValueError(f"run {run_id} is not ready for release finalization")
        if not run.staged_release_path:
            raise ValueError(f"run {run_id} has no staged release path")
        staging_path = Path(run.staged_release_path)
        versions = dict(run.resolved_versions_json)
        generated_at = run.pipeline_finished_at or utcnow()
        previous = session.scalar(select(CatalogRelease).where(CatalogRelease.is_current.is_(True)))
        previous_release_id = previous.id if previous else None
        submission = session.scalar(
            select(DemoSubmission).where(DemoSubmission.pipeline_run_id == run_id)
        )
        demo_review_ids = [review.id for review in submission.reviews] if submission else []

    if final_path.exists():
        return _register_published_directory(
            release_id=release_id,
            run_id=run_id,
            final_path=final_path,
        )

    snapshot_path = staging_path / "catalog.duckdb"
    pipeline_manifest_path = staging_path / "pipeline_manifest.json"
    if not snapshot_path.exists() or not pipeline_manifest_path.exists():
        raise FileNotFoundError("staged snapshot or pipeline manifest is missing")
    pipeline_manifest = read_json(pipeline_manifest_path)
    snapshot_sha = sha256_file(snapshot_path)
    if snapshot_sha != pipeline_manifest["snapshot_sha256"]:
        raise ValueError("staged DuckDB snapshot hash differs from pipeline manifest")

    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        products = fetch_dicts(
            connection, "SELECT product_id, product_name FROM products ORDER BY product_id"
        )
    finally:
        connection.close()

    artifacts: list[dict] = []
    for product in products:
        output_dir = staging_path / "reports/static" / product["product_id"]
        result = generate_static_catalog_report(
            snapshot_path=snapshot_path,
            product_id=product["product_id"],
            release_id=release_id,
            generated_at=generated_at,
            versions=versions,
            output_dir=output_dir,
        )
        markdown_path = result["markdown_path"]
        artifacts.append(
            _artifact_record(
                release_id=release_id,
                report_type="static_catalog",
                product_id=product["product_id"],
                relative_path=str(markdown_path.relative_to(staging_path)),
                sha256=sha256_file(markdown_path),
            )
        )

    for demo_review_id in demo_review_ids:
        output_dir = staging_path / "reports/dynamic" / demo_review_id
        result = generate_dynamic_decision_proposal(
            snapshot_path=snapshot_path,
            demo_review_id=demo_review_id,
            release_id=release_id,
            generated_at=generated_at,
            versions=versions,
            output_dir=output_dir,
        )
        markdown_path = result["markdown_path"]
        artifacts.append(
            _artifact_record(
                release_id=release_id,
                report_type="dynamic_review_decision",
                demo_review_id=demo_review_id,
                relative_path=str(markdown_path.relative_to(staging_path)),
                sha256=sha256_file(markdown_path),
            )
        )

    release_manifest = {
        "schema_version": "1.0.0",
        "release_id": release_id,
        "pipeline_run_id": run_id,
        "previous_release_id": previous_release_id,
        "generated_at_utc": generated_at.isoformat(),
        "published_at_utc": datetime.now(UTC).isoformat(),
        "snapshot": {
            "path": "catalog.duckdb",
            "sha256": snapshot_sha,
            "access_mode": "read_only_for_api",
        },
        "versions": versions,
        "reports": [
            {
                "report_type": artifact["report_type"],
                "product_id": artifact["product_id"],
                "demo_review_id": artifact["demo_review_id"],
                "path": artifact["relative_path"],
                "sha256": artifact["sha256"],
            }
            for artifact in artifacts
        ],
        "pipeline_manifest_sha256": sha256_file(pipeline_manifest_path),
        "publishable": True,
        "atomicity_contract": "snapshot_and_all_reports_are_published_by_one_directory_rename",
    }
    manifest_path = staging_path / "release_manifest.json"
    atomic_write_json(manifest_path, release_manifest)
    if final_path.exists():
        raise FileExistsError(f"immutable release path already exists: {final_path}")
    os.replace(staging_path, final_path)
    return _register_published_directory(
        release_id=release_id,
        run_id=run_id,
        final_path=final_path,
    )


def finalize_pending_releases(limit: int = 5) -> list[str]:
    with SessionLocal() as session:
        run_ids = session.scalars(
            select(PipelineRun.id)
            .where(PipelineRun.state.in_(["pipeline_succeeded", "release_failed"]))
            .order_by(PipelineRun.pipeline_finished_at)
            .limit(limit)
        ).all()
    completed = []
    for run_id in run_ids:
        try:
            completed.append(finalize_catalog_release(run_id))
        except Exception as exc:
            with SessionLocal.begin() as session:
                run = session.get(PipelineRun, run_id)
                run.state = "release_failed"
                run.error_message = str(exc)[:4000]
                submission = session.scalar(
                    select(DemoSubmission).where(DemoSubmission.pipeline_run_id == run_id)
                )
                if submission:
                    submission.state = "release_failed"
                    submission.error_message = str(exc)[:4000]
    return completed


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", nargs="?")
    args = parser.parse_args()
    if args.run_id:
        print(json.dumps({"release_id": finalize_catalog_release(args.run_id)}))
    else:
        print(json.dumps({"release_ids": finalize_pending_releases()}))


if __name__ == "__main__":
    cli()
