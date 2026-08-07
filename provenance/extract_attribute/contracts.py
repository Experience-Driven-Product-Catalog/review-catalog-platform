"""Strict request and response contracts for both extraction strategies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
from typing import ClassVar, Literal, TypeAlias

Sentiment: TypeAlias = Literal[
    "positive",
    "negative",
    "mixed",
    "neutral",
    "unknown",
]
SENTIMENT_VALUES: tuple[Sentiment, ...] = (
    "positive",
    "negative",
    "mixed",
    "neutral",
    "unknown",
)


def validate_exact_fields(
    value: Mapping[str, object],
    expected_fields: tuple[str, ...],
    *,
    location: str,
) -> None:
    """Reject missing or unexpected response fields at a contract boundary."""
    expected = set(expected_fields)
    actual = set(value)
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if not unexpected and not missing:
        return

    details: list[str] = []
    if unexpected:
        details.append("예상하지 못한 필드: " + ", ".join(unexpected))
    if missing:
        details.append("누락된 필드: " + ", ".join(missing))
    raise ValueError(f"Codex {location} 계약 위반 ({'; '.join(details)}).")


@dataclass(frozen=True)
class ReviewInput:
    """One source review with the metadata needed to construct a prompt."""

    review_idx: int
    product_name: str
    product_category: str
    review: str

    def __post_init__(self) -> None:
        if isinstance(self.review_idx, bool) or not isinstance(self.review_idx, Integral):
            raise TypeError("review_idx는 정수여야 합니다.")
        object.__setattr__(self, "review_idx", int(self.review_idx))

        for field_name in ("product_name", "product_category", "review"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"{field_name}은 비어 있지 않은 문자열이어야 합니다.")


@dataclass(frozen=True)
class OpinionUnit:
    """One grounded opinion unit emitted for a source review."""

    response_fields: ClassVar[tuple[str, ...]] = (
        "raw_aspect",
        "raw_status",
        "excerpt",
        "opinion",
        "sentiment",
    )

    review_idx: int
    raw_aspect: str
    raw_status: str | None
    excerpt: str
    opinion: str
    sentiment: Sentiment

    @classmethod
    def from_payload(
        cls,
        review_input: ReviewInput,
        payload: Mapping[str, object],
    ) -> OpinionUnit | None:
        """Convert one semantically valid item; invalid items are filtered alone."""
        raw_aspect = payload.get("raw_aspect")
        raw_status = payload.get("raw_status")
        excerpt = payload.get("excerpt")
        opinion = payload.get("opinion")
        sentiment = payload.get("sentiment")

        if not isinstance(raw_aspect, str) or not raw_aspect.strip():
            return None
        if raw_status is not None and (
            not isinstance(raw_status, str) or not raw_status.strip()
        ):
            return None
        if not isinstance(excerpt, str) or not excerpt.strip():
            return None
        if excerpt not in review_input.review:
            return None
        if not isinstance(opinion, str) or not opinion.strip():
            return None
        if sentiment not in SENTIMENT_VALUES:
            return None

        return cls(
            review_idx=review_input.review_idx,
            raw_aspect=raw_aspect.strip(),
            raw_status=raw_status.strip() if isinstance(raw_status, str) else None,
            excerpt=excerpt,
            opinion=opinion.strip(),
            sentiment=sentiment,
        )


@dataclass(frozen=True)
class RepresentativeAttribute:
    """One clusterable raw attribute and its review-grounded sentiment."""

    response_fields: ClassVar[tuple[str, ...]] = ("raw_attribute", "sentiment")

    review_idx: int
    raw_attribute: str
    sentiment: Sentiment

    @classmethod
    def from_payload(
        cls,
        review_input: ReviewInput,
        payload: Mapping[str, object],
    ) -> RepresentativeAttribute | None:
        """Convert one valid attribute item; invalid items are filtered alone."""
        raw_attribute = payload.get("raw_attribute")
        sentiment = payload.get("sentiment")
        if not isinstance(raw_attribute, str) or not raw_attribute.strip():
            return None
        if sentiment not in SENTIMENT_VALUES:
            return None
        return cls(
            review_idx=review_input.review_idx,
            raw_attribute=raw_attribute.strip(),
            sentiment=sentiment,
        )

