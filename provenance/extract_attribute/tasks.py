"""Task-specific JSON Schemas and strict response parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from extraction.contracts import (
    SENTIMENT_VALUES,
    OpinionUnit,
    RepresentativeAttribute,
    ReviewInput,
    validate_exact_fields,
)

OPINION_UNITS_FIELD = "opinion_units"
REPRESENTATIVE_ATTRIBUTES_FIELD = "representative_attributes"


def opinion_units_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            OPINION_UNITS_FIELD: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_aspect": {"type": "string"},
                        "raw_status": {"type": ["string", "null"]},
                        "excerpt": {"type": "string"},
                        "opinion": {"type": "string"},
                        "sentiment": {
                            "type": "string",
                            "enum": list(SENTIMENT_VALUES),
                        },
                    },
                    "required": list(OpinionUnit.response_fields),
                    "additionalProperties": False,
                },
            }
        },
        "required": [OPINION_UNITS_FIELD],
        "additionalProperties": False,
    }


def representative_attributes_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            REPRESENTATIVE_ATTRIBUTES_FIELD: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "raw_attribute": {"type": "string"},
                        "sentiment": {
                            "type": "string",
                            "enum": list(SENTIMENT_VALUES),
                        },
                    },
                    "required": list(RepresentativeAttribute.response_fields),
                    "additionalProperties": False,
                },
            }
        },
        "required": [REPRESENTATIVE_ATTRIBUTES_FIELD],
        "additionalProperties": False,
    }


def _read_items(
    response: Mapping[str, object],
    *,
    output_field: str,
) -> Sequence[object]:
    validate_exact_fields(response, (output_field,), location="최상위 응답")
    items = response[output_field]
    if not isinstance(items, list):
        raise TypeError(f"Codex {output_field!r} 값은 JSON 배열이어야 합니다.")
    return items


def parse_opinion_units(
    review_input: ReviewInput,
    response: Mapping[str, object],
) -> list[OpinionUnit]:
    outputs: list[OpinionUnit] = []
    for index, item in enumerate(
        _read_items(response, output_field=OPINION_UNITS_FIELD)
    ):
        if not isinstance(item, Mapping):
            raise TypeError(f"Codex opinion_units[{index}]은 JSON 객체여야 합니다.")
        validate_exact_fields(
            item,
            OpinionUnit.response_fields,
            location=f"opinion_units[{index}]",
        )
        output = OpinionUnit.from_payload(review_input, item)
        if output is not None:
            outputs.append(output)
    return outputs


def parse_representative_attributes(
    review_input: ReviewInput,
    response: Mapping[str, object],
) -> list[RepresentativeAttribute]:
    outputs: list[RepresentativeAttribute] = []
    for index, item in enumerate(
        _read_items(response, output_field=REPRESENTATIVE_ATTRIBUTES_FIELD)
    ):
        if not isinstance(item, Mapping):
            raise TypeError(
                f"Codex representative_attributes[{index}]은 JSON 객체여야 합니다."
            )
        validate_exact_fields(
            item,
            RepresentativeAttribute.response_fields,
            location=f"representative_attributes[{index}]",
        )
        output = RepresentativeAttribute.from_payload(review_input, item)
        if output is not None:
            outputs.append(output)
    return outputs

