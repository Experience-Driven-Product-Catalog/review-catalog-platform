from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CatalogRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_limit: int | None = Field(default=None, ge=1, le=10000)
    scheduled_for: datetime | None = None


class DemoSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=120)
    reviews: list[str] = Field(min_length=1, max_length=20)
    scheduled_for: datetime | None = None

    @field_validator("reviews")
    @classmethod
    def validate_reviews(cls, reviews: list[str]) -> list[str]:
        stripped = [review.strip() for review in reviews]
        if any(not review for review in stripped):
            raise ValueError("reviews must not contain blank text")
        if any(len(review) > 10000 for review in stripped):
            raise ValueError("each review must be at most 10000 characters")
        return stripped


class RunAccepted(BaseModel):
    pipeline_run_id: str
    dag_run_id: str
    state: str


class SubmissionAccepted(RunAccepted):
    submission_id: str
