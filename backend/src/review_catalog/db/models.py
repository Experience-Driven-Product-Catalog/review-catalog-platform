from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ComponentVersion(Base):
    __tablename__ = "component_versions"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    component_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(120), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("component_type", "version", name="uq_component_type_version"),
        Index(
            "uq_one_active_component_version",
            "component_type",
            unique=True,
            postgresql_where=is_active.is_(True),
        ),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    dag_id: Mapped[str] = mapped_column(String(120), nullable=False, default="Catalog_ingestion")
    dag_run_id: Mapped[str | None] = mapped_column(String(250), unique=True)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False, default="catalog")
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    current_task: Mapped[str | None] = mapped_column(String(120))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pipeline_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conf_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resolved_versions_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    work_dir: Mapped[str | None] = mapped_column(Text)
    staged_release_path: Mapped[str | None] = mapped_column(Text)
    staged_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    committed_review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    taxonomy_rebuild_recommended: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    release: Mapped[CatalogRelease | None] = relationship(back_populates="pipeline_run")


class DemoSubmission(Base):
    __tablename__ = "demo_submissions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    pipeline_run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.id"))
    release_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_releases.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    reviews: Mapped[list[DemoReview]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="DemoReview.position"
    )


class DemoReview(Base):
    __tablename__ = "demo_reviews"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("demo_submissions.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    review_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    submission: Mapped[DemoSubmission] = relationship(back_populates="reviews")

    __table_args__ = (
        UniqueConstraint("submission_id", "position", name="uq_demo_review_position"),
    )


class CatalogRelease(Base):
    __tablename__ = "catalog_releases"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_runs.id"), unique=True, nullable=False
    )
    previous_release_id: Mapped[str | None] = mapped_column(ForeignKey("catalog_releases.id"))
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="publishing")
    release_path: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_path: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version_manifest_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pipeline_run: Mapped[PipelineRun] = relationship(back_populates="release")
    artifacts: Mapped[list[ReportArtifact]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "uq_current_catalog_release",
            "is_current",
            unique=True,
            postgresql_where=is_current.is_(True),
        ),
    )


class ReportArtifact(Base):
    __tablename__ = "report_artifacts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("catalog_releases.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(120))
    demo_review_id: Mapped[str | None] = mapped_column(ForeignKey("demo_reviews.id"))
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(120), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    release: Mapped[CatalogRelease] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint(
            "release_id", "report_type", "product_id", "demo_review_id", name="uq_report_target"
        ),
    )
