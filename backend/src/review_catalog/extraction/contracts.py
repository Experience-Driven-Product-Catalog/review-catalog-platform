from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Sentiment = Literal["positive", "negative", "mixed", "neutral", "unknown"]
SENTIMENTS = ("positive", "negative", "mixed", "neutral", "unknown")


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    product_id: str
    product_name: str
    product_category: str
    review: str = Field(min_length=1)
    source: str
    source_review_id: str | None = None
    demo_review_id: str | None = None


class OpinionUnit(BaseModel):
    """Strictly review-grounded extraction contract copied from extract_attribute."""

    model_config = ConfigDict(extra="forbid")

    raw_aspect: str = Field(min_length=1)
    raw_status: str | None
    excerpt: str = Field(min_length=1)
    opinion: str = Field(min_length=1)
    sentiment: Sentiment

    @field_validator("raw_aspect", "raw_status", "excerpt", "opinion")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("text fields must not be blank")
        return stripped


class OpinionUnitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opinion_units: list[OpinionUnit]


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review: ReviewInput
    opinion_units: list[OpinionUnit]
    prompt_version_id: str
    model_version_id: str
    raw_response_sha256: str

    @model_validator(mode="after")
    def validate_grounding(self) -> ExtractionResult:
        for position, unit in enumerate(self.opinion_units):
            if unit.excerpt not in self.review.review:
                raise ValueError(
                    f"opinion_units[{position}].excerpt is not contiguous source review text"
                )
        return self


OPINION_UNIT_JSON_SCHEMA = OpinionUnitResponse.model_json_schema()
