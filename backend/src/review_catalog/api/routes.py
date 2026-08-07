from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from review_catalog.api.schemas import (
    CatalogRunRequest,
    DemoSubmissionRequest,
    RunAccepted,
    SubmissionAccepted,
)
from review_catalog.db.models import (
    CatalogRelease,
    DemoReview,
    DemoSubmission,
    PipelineRun,
    ReportArtifact,
    new_id,
)
from review_catalog.db.session import get_db
from review_catalog.pipeline.artifacts import sha256_file
from review_catalog.reporting.common import fetch_dicts
from review_catalog.services.airflow import AirflowClient
from review_catalog.settings import Settings, get_settings

router = APIRouter(prefix="/api")

REMOTE_README_URLS = {
    "overview": (
        "https://raw.githubusercontent.com/Experience-Driven-Product-Catalog/"
        ".github/refs/heads/main/profile/README.md"
    ),
    "experiment": (
        "https://raw.githubusercontent.com/Experience-Driven-Product-Catalog/"
        "embedding_clustering_experiment/refs/heads/main/README.md"
    ),
    "release": (
        "https://raw.githubusercontent.com/Experience-Driven-Product-Catalog/"
        "review-catalog-platform/refs/heads/main/README.md"
    ),
}


def _current_release(session: Session) -> CatalogRelease | None:
    return session.scalar(
        select(CatalogRelease).where(
            CatalogRelease.is_current.is_(True), CatalogRelease.state == "published"
        )
    )


def _verified_markdown(release: CatalogRelease, artifact: ReportArtifact) -> str:
    path = Path(release.release_path) / artifact.relative_path
    if not path.is_file() or sha256_file(path) != artifact.sha256:
        raise HTTPException(status_code=503, detail="published report integrity check failed")
    return path.read_text(encoding="utf-8")


@router.get("/health")
def health(session: Session = Depends(get_db)) -> dict:
    release = _current_release(session)
    return {
        "status": "ok",
        "current_release_id": release.id if release else None,
        "duckdb_api_access_mode": "read_only",
    }


@router.get("/about")
def about(settings: Settings = Depends(get_settings)) -> dict:
    path = settings.project_readme_path
    if not path.exists():
        raise HTTPException(status_code=503, detail=f"README not available: {path}")
    return {"markdown": path.read_text(encoding="utf-8")}


@router.get("/about/{section}")
def about_section(section: str, settings: Settings = Depends(get_settings)) -> dict:
    if section == "about-me":
        path = settings.profile_markdown_path
        if not path.exists():
            raise HTTPException(status_code=503, detail=f"profile not available: {path}")
        return {"markdown": path.read_text(encoding="utf-8"), "source_url": None}
    source_url = REMOTE_README_URLS.get(section)
    if source_url is None:
        raise HTTPException(status_code=404, detail="about section not found")
    try:
        response = httpx.get(
            source_url,
            follow_redirects=True,
            timeout=settings.readme_request_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"README not available: {section}") from exc
    return {"markdown": response.text, "source_url": source_url}


@router.get("/catalog/releases/current")
def current_release(session: Session = Depends(get_db)) -> dict:
    release = _current_release(session)
    if not release:
        raise HTTPException(status_code=404, detail="no catalog release has been published")
    return {
        "release_id": release.id,
        "published_at": release.published_at,
        "snapshot_sha256": release.snapshot_sha256,
        "versions": release.version_manifest_json,
    }


@router.get("/products")
def products(session: Session = Depends(get_db)) -> list[dict]:
    release = _current_release(session)
    if not release:
        return []
    connection = duckdb.connect(release.snapshot_path, read_only=True)
    try:
        return fetch_dicts(
            connection,
            """
            SELECT p.product_id, p.product_name, p.category, count(DISTINCT r.review_id) AS review_count
            FROM products p LEFT JOIN reviews r USING (product_id)
            GROUP BY ALL ORDER BY p.product_name
            """,
        )
    finally:
        connection.close()


@router.get("/products/{product_id}/report")
def product_report(product_id: str, session: Session = Depends(get_db)) -> Response:
    release = _current_release(session)
    if not release:
        raise HTTPException(status_code=404, detail="no catalog release has been published")
    artifact = session.scalar(
        select(ReportArtifact).where(
            ReportArtifact.release_id == release.id,
            ReportArtifact.report_type == "static_catalog",
            ReportArtifact.product_id == product_id,
        )
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="product report not found")
    return Response(
        _verified_markdown(release, artifact), media_type="text/markdown; charset=utf-8"
    )


@router.post("/pipeline-runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_pipeline_run(
    request: CatalogRunRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RunAccepted:
    run_id = new_id("run")
    conf = {"mode": "catalog"}
    if request.review_limit is not None:
        conf["review_limit"] = request.review_limit
    run = PipelineRun(
        id=run_id,
        trigger_type="api_scheduled" if request.scheduled_for else "api_manual",
        mode="catalog",
        state="dispatching",
        scheduled_for=request.scheduled_for,
        conf_json=conf,
    )
    session.add(run)
    session.commit()
    try:
        dag_run_id = AirflowClient(settings).trigger_catalog_ingestion(
            pipeline_run_id=run_id,
            conf=conf,
            scheduled_for=request.scheduled_for,
        )
    except Exception as exc:
        run.state = "dispatch_failed"
        run.error_message = str(exc)[:4000]
        session.commit()
        raise HTTPException(status_code=502, detail="Airflow dispatch failed") from exc
    run.dag_run_id = dag_run_id
    run.state = "queued"
    session.commit()
    return RunAccepted(pipeline_run_id=run_id, dag_run_id=dag_run_id, state=run.state)


@router.get("/pipeline-runs/{run_id}")
def pipeline_run(run_id: str, session: Session = Depends(get_db)) -> dict:
    run = session.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="pipeline run not found")
    return {
        "pipeline_run_id": run.id,
        "dag_run_id": run.dag_run_id,
        "state": run.state,
        "current_task": run.current_task,
        "staged_review_count": run.staged_review_count,
        "committed_review_count": run.committed_review_count,
        "candidate_count": run.candidate_count,
        "taxonomy_rebuild_recommended": run.taxonomy_rebuild_recommended,
        "error_message": run.error_message,
        "release_id": run.release.id if run.release else None,
    }


@router.post(
    "/demo/submissions", response_model=SubmissionAccepted, status_code=status.HTTP_202_ACCEPTED
)
def create_demo_submission(
    request: DemoSubmissionRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SubmissionAccepted:
    release = _current_release(session)
    if not release:
        raise HTTPException(
            status_code=409,
            detail="publish the initial catalog release before submitting demo reviews",
        )
    connection = duckdb.connect(release.snapshot_path, read_only=True)
    try:
        product = fetch_dicts(
            connection,
            "SELECT product_id, product_name, category FROM products WHERE product_id = ?",
            [request.product_id],
        )
    finally:
        connection.close()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")
    product = product[0]
    submission_id = new_id("submission")
    run_id = new_id("run")
    conf = {
        "mode": "demo_submission",
        "submission_id": submission_id,
        "product_id": product["product_id"],
        "product_name": product["product_name"],
        "category": product["category"],
    }
    run = PipelineRun(
        id=run_id,
        trigger_type="demo_ui",
        mode="demo_submission",
        state="dispatching",
        scheduled_for=request.scheduled_for,
        conf_json=conf,
    )
    submission = DemoSubmission(
        id=submission_id,
        product_id=request.product_id,
        state="dispatching",
        pipeline_run_id=run_id,
    )
    session.add(run)
    # The submission references the operational run, but the ORM models do
    # not expose a relationship between them. Flush explicitly so Postgres
    # always sees the parent row first.
    session.flush()
    session.add(submission)
    for position, text in enumerate(request.reviews, start=1):
        session.add(
            DemoReview(
                id=new_id("demo_review"),
                submission_id=submission_id,
                position=position,
                review_text=text,
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    session.commit()
    try:
        dag_run_id = AirflowClient(settings).trigger_catalog_ingestion(
            pipeline_run_id=run_id,
            conf=conf,
            scheduled_for=request.scheduled_for,
        )
    except Exception as exc:
        run.state = "dispatch_failed"
        run.error_message = str(exc)[:4000]
        submission.state = "dispatch_failed"
        submission.error_message = str(exc)[:4000]
        session.commit()
        raise HTTPException(status_code=502, detail="Airflow dispatch failed") from exc
    run.dag_run_id = dag_run_id
    run.state = "queued"
    submission.state = "queued"
    session.commit()
    return SubmissionAccepted(
        submission_id=submission_id,
        pipeline_run_id=run_id,
        dag_run_id=dag_run_id,
        state="queued",
    )


@router.get("/demo/submissions/{submission_id}")
def demo_submission(submission_id: str, session: Session = Depends(get_db)) -> dict:
    submission = session.scalar(
        select(DemoSubmission)
        .options(selectinload(DemoSubmission.reviews))
        .where(DemoSubmission.id == submission_id)
    )
    if not submission:
        raise HTTPException(status_code=404, detail="submission not found")
    payload = {
        "submission_id": submission.id,
        "pipeline_run_id": submission.pipeline_run_id,
        "product_id": submission.product_id,
        "state": submission.state,
        "release_id": submission.release_id,
        "error_message": submission.error_message,
        "reviews": [
            {"demo_review_id": review.id, "position": review.position, "review": review.review_text}
            for review in submission.reviews
        ],
        "results": [],
    }
    if submission.state != "completed" or not submission.release_id:
        return payload
    release = session.get(CatalogRelease, submission.release_id)
    connection = duckdb.connect(release.snapshot_path, read_only=True)
    try:
        for review in submission.reviews:
            units = fetch_dicts(
                connection,
                """
                SELECT o.raw_aspect, o.raw_status, o.aspect, o.status, o.sentiment,
                       o.mapping_state, o.suggested_aspect, o.aspect_distance,
                       o.aspect_membership_max_distance,
                       o.aspect_centroid_distance,
                       o.aspect_second_nearest_distance,
                       o.aspect_distance_margin,
                       o.aspect_candidate_eligible,
                       o.suggested_status, o.status_distance,
                       o.status_membership_max_distance,
                       o.status_centroid_distance,
                       o.status_second_nearest_distance,
                       o.status_distance_margin,
                       o.status_candidate_eligible,
                       o.normalization_run_id,
                       o.excerpt, o.opinion
                FROM reviews r JOIN opinion_units o USING (review_id)
                WHERE r.demo_review_id = ? ORDER BY o.unit_position
                """,
                [review.id],
            )
            artifact = session.scalar(
                select(ReportArtifact).where(
                    ReportArtifact.release_id == release.id,
                    ReportArtifact.report_type == "dynamic_review_decision",
                    ReportArtifact.demo_review_id == review.id,
                )
            )
            payload["results"].append(
                {
                    "demo_review_id": review.id,
                    "opinion_units": units,
                    "proposal_markdown": _verified_markdown(release, artifact)
                    if artifact
                    else None,
                }
            )
    finally:
        connection.close()
    return payload
