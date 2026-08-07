from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from review_catalog.pipeline.artifacts import canonical_sha256
from review_catalog.reporting.analytics import (
    all_mapped_rows,
    catalog_review_counts,
    product_profile_data,
    rank_alternatives,
    review_vote_rows,
    sentiment_distribution,
)
from review_catalog.reporting.common import (
    collapse_sentiments,
    fetch_dicts,
    report_source,
    write_report_pair,
)
from review_catalog.reporting.reference_format import (
    render_dynamic_review_decision_markdown,
)

DYNAMIC_REPORT_SCHEMA_VERSION = "2.0.0"
DYNAMIC_REPORT_GENERATOR_VERSION = "embedding-clustering-experiment-dynamic-format-v3"


def _vote_summary(votes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "supporting_review_count": len(votes),
        "sentiment": sentiment_distribution([str(vote["sentiment"]) for vote in votes]),
    }


def _submitted_groups(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        if not unit["catalog_comparison_eligible"]:
            continue
        grouped[(unit["aspect_cluster_id"], unit["status_cluster_id"])].append(unit)
    outputs = []
    for (aspect_id, status_id), item_units in grouped.items():
        first = item_units[0]
        outputs.append(
            {
                "aspect": first["aspect"],
                "status": first["status"],
                "aspect_cluster_id": aspect_id,
                "status_cluster_id": status_id,
                "submitted_review_vote_count": 1,
                "submitted_sentiment": collapse_sentiments(
                    {str(unit["sentiment"]) for unit in item_units}
                ),
                "submitted_opinion_unit_ids": [unit["opinion_unit_id"] for unit in item_units],
            }
        )
    return outputs


def _unresolved_catalog_comparison_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep non-canonical submitted evidence visible without comparing it as canonical."""
    unresolved = []
    for unit in units:
        if unit["catalog_comparison_eligible"]:
            continue
        mapping_state = str(unit["mapping_state"])
        unresolved.append(
            {
                "opinion_unit_id": unit["opinion_unit_id"],
                "raw_aspect": unit["raw_aspect"],
                "raw_status": unit["raw_status"],
                "sentiment": unit["sentiment"],
                "mapping_state": mapping_state,
                "suggested_aspect": unit["suggested_aspect"],
                "suggested_status": unit["suggested_status"],
                "reason": (
                    "CANDIDATE_HAS_NO_CANONICAL_ASPECT_STATUS"
                    if mapping_state == "candidate"
                    else "EXCLUDED_FROM_TAXONOMY"
                ),
            }
        )
    return unresolved


def _catalog_comparison_summary(units: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _submitted_groups(units)
    unresolved = _unresolved_catalog_comparison_units(units)
    return {
        "submitted_opinion_unit_count": len(units),
        "exact_mapped_opinion_unit_count": len(units) - len(unresolved),
        "exact_mapped_aspect_status_count": sum(
            group["status_cluster_id"] is not None for group in groups
        ),
        "unresolved_opinion_unit_count": len(unresolved),
        "unresolved_opinion_units": unresolved,
    }


def _other_top_statuses(
    pair_votes: list[dict[str, Any]],
    *,
    product_id: str,
    aspect_id: str,
    excluded_review_id: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for vote in pair_votes:
        if (
            vote["product_id"] == product_id
            and vote["aspect_id"] == aspect_id
            and vote["status_id"] is not None
            and vote["review_id"] != excluded_review_id
        ):
            grouped[(vote["status_id"], vote["status"])].append(vote)
    rows = [
        {
            "status": status,
            "status_cluster_id": status_id,
            "supporting_review_count": len(votes),
            "sentiment": sentiment_distribution([str(vote["sentiment"]) for vote in votes]),
        }
        for (status_id, status), votes in grouped.items()
    ]
    rows.sort(
        key=lambda row: (
            -row["supporting_review_count"],
            str(row["status"]),
            row["status_cluster_id"],
        )
    )
    for rank, row in enumerate(rows[:3], start=1):
        row["rank"] = rank
    return rows[:3]


def _relationships(
    *,
    mapped_rows: list[dict[str, Any]],
    submitted_units: list[dict[str, Any]],
    product_id: str,
    submitted_review_id: str,
    catalog_review_count: int,
) -> list[dict[str, Any]]:
    aspect_votes = review_vote_rows(mapped_rows, ("aspect_id", "aspect"))
    pair_votes = review_vote_rows(mapped_rows, ("aspect_id", "status_id", "aspect", "status"))
    relationships = []
    for rank, group in enumerate(_submitted_groups(submitted_units), start=1):
        full_aspect = [
            vote
            for vote in aspect_votes
            if vote["product_id"] == product_id and vote["aspect_id"] == group["aspect_cluster_id"]
        ]
        if group["status_cluster_id"] is None:
            full_pair = full_aspect
            comparison_grain = "aspect"
        else:
            full_pair = [
                vote
                for vote in pair_votes
                if vote["product_id"] == product_id
                and vote["aspect_id"] == group["aspect_cluster_id"]
                and vote["status_id"] == group["status_cluster_id"]
            ]
            comparison_grain = "aspect_status"
        other_pair = [vote for vote in full_pair if vote["review_id"] != submitted_review_id]
        other_summary = _vote_summary(other_pair)
        dominant = other_summary["sentiment"]["dominant_sentiment"]
        submitted_sentiment = group["submitted_sentiment"]
        if not other_pair:
            relation_code = "NOT_MENTIONED_BY_OTHER_REVIEWS"
            relation_label = "판단하기 어렵습니다"
        elif (
            submitted_sentiment in {"positive", "negative"}
            and dominant in {"positive", "negative"}
            and submitted_sentiment == dominant
        ):
            relation_code = "ALIGNS_WITH_OTHER_REVIEW_MAJORITY"
            relation_label = "일치합니다"
        elif submitted_sentiment in {"positive", "negative"} and dominant in {
            "positive",
            "negative",
        }:
            relation_code = "CONTRADICTS_OTHER_REVIEW_MAJORITY"
            relation_label = "일치하지 않습니다"
        else:
            relation_code = "INSUFFICIENT_OR_NON_DIRECTIONAL_OTHER_REVIEW_EVIDENCE"
            relation_label = "판단하기 어렵습니다"
        top_statuses = (
            _other_top_statuses(
                pair_votes,
                product_id=product_id,
                aspect_id=group["aspect_cluster_id"],
                excluded_review_id=submitted_review_id,
            )
            if group["status_cluster_id"] is not None and not other_pair
            else []
        )
        relationship = {
            "rank": rank,
            "comparison_grain": comparison_grain,
            "aspect": group["aspect"],
            "status": group["status"],
            "aspect_cluster_id": group["aspect_cluster_id"],
            "status_cluster_id": group["status_cluster_id"],
            "catalog_review_count": catalog_review_count,
            "aspect_review_count": len(full_aspect),
            "same_status_review_count": len(full_pair),
            "other_status_review_count": len(other_pair),
            "other_status_sentiment": other_summary["sentiment"],
            "other_aspect_top_statuses": top_statuses,
            "submitted_sentiment": submitted_sentiment,
            "submitted_review_vote_count": group["submitted_review_vote_count"],
            "submitted_opinion_unit_ids": group["submitted_opinion_unit_ids"],
            "relation_code": relation_code,
            "relation_label": relation_label,
        }
        relationships.append(relationship)
    return relationships


def _negative_conditions(units: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    conditions = []
    excluded = []
    for group in _submitted_groups(units):
        if group["submitted_sentiment"] != "negative":
            continue
        if group["status_cluster_id"] is None:
            excluded.append(
                {
                    "aspect": group["aspect"],
                    "status": group["status"],
                    "reason": "NEGATIVE_INPUT_HAS_NO_STATUS_CLUSTER_ID",
                    "submitted_review_vote_count": group["submitted_review_vote_count"],
                }
            )
            continue
        conditions.append(
            {
                "condition_id": f"condition_{len(conditions) + 1}",
                "aspect": group["aspect"],
                "status": group["status"],
                "aspect_cluster_id": group["aspect_cluster_id"],
                "status_cluster_id": group["status_cluster_id"],
                "submitted_review_vote_count": group["submitted_review_vote_count"],
                "importance": float(group["submitted_review_vote_count"]),
                "submitted_opinion_unit_ids": group["submitted_opinion_unit_ids"],
            }
        )
    for unit in units:
        if unit["sentiment"] != "negative" or unit["catalog_comparison_eligible"]:
            continue
        excluded.append(
            {
                "opinion_unit_id": unit["opinion_unit_id"],
                "raw_aspect": unit["raw_aspect"],
                "raw_status": unit["raw_status"],
                "sentiment": unit["sentiment"],
                "mapping_state": unit["mapping_state"],
                "suggested_aspect": unit["suggested_aspect"],
                "suggested_status": unit["suggested_status"],
                "reason": "NEGATIVE_INPUT_HAS_NO_CANONICAL_ASPECT_STATUS",
            }
        )
    return conditions, excluded


def generate_dynamic_decision_proposal(
    *,
    snapshot_path: Path,
    demo_review_id: str,
    release_id: str,
    generated_at: datetime,
    versions: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Generate the source project's per-review proposal format from DuckDB."""
    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        reviews = fetch_dicts(
            connection,
            """
            SELECT r.review_id, r.external_review_idx, r.review_text, r.product_id,
                   p.product_name
            FROM reviews r JOIN products p USING (product_id)
            WHERE r.demo_review_id = ?
            """,
            [demo_review_id],
        )
        if len(reviews) != 1:
            raise ValueError(f"demo_review_id must resolve to one catalog review: {demo_review_id}")
        review = reviews[0]
        raw_units = fetch_dicts(
            connection,
            """
            SELECT opinion_unit_id, unit_position, raw_aspect, raw_status, excerpt,
                   opinion, sentiment, mapping_state, aspect_id, aspect, status_id, status,
                   suggested_aspect_id, suggested_aspect, suggested_status_id, suggested_status,
                   prompt_version_id, model_version_id, mapping_table_version_id,
                   embedding_model_version_id, extraction_response_sha256, ingestion_run_id
            FROM opinion_units WHERE review_id = ? ORDER BY unit_position
            """,
            [review["review_id"]],
        )
        submitted_units = [
            {
                "submission_review_id": demo_review_id,
                "source_review_idx": review["external_review_idx"],
                "raw_aspect": unit["raw_aspect"],
                "aspect": unit["aspect"],
                "raw_status": unit["raw_status"],
                "status": unit["status"],
                "excerpt": unit["excerpt"],
                "opinion": unit["opinion"],
                "sentiment": unit["sentiment"],
                "aspect_cluster_id": unit["aspect_id"],
                "status_cluster_id": unit["status_id"],
                "catalog_comparison_eligible": unit["mapping_state"] == "mapped_exact",
                "opinion_unit_id": unit["opinion_unit_id"],
                "mapping_state": unit["mapping_state"],
                "suggested_aspect": unit["suggested_aspect"],
                "suggested_status": unit["suggested_status"],
            }
            for unit in raw_units
        ]
        mapped_rows = all_mapped_rows(connection)
        counts = catalog_review_counts(connection)
        catalog_comparison = _catalog_comparison_summary(submitted_units)
        relationships = _relationships(
            mapped_rows=mapped_rows,
            submitted_units=submitted_units,
            product_id=review["product_id"],
            submitted_review_id=review["review_id"],
            catalog_review_count=int(counts[review["product_id"]]["review_count"]),
        )
        conditions, excluded_conditions = _negative_conditions(submitted_units)
        observed_negative_count = sum(unit["sentiment"] == "negative" for unit in submitted_units)
        alternatives = rank_alternatives(
            product_profile_data(connection),
            review["product_id"],
            conditions,
            static_policy=False,
        )
        submitted_at = generated_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")
        submission = {
            "submission_id": demo_review_id,
            "submitted_at_local": submitted_at,
            "product_name": review["product_name"],
            "reviews": [
                {
                    "submission_review_id": demo_review_id,
                    "source_review_idx": review["external_review_idx"],
                    "review": review["review_text"],
                    "opinion_units": submitted_units,
                }
            ],
            "excluded_products": [review["product_name"]],
        }
        identity = {
            "release_id": release_id,
            "submission": submission,
            "versions": versions,
            "format_source": "embedding_clustering_experiment",
        }
        run_id = str(versions["mapping_table"]["id"]).removeprefix("mapping-table-")
        payload = {
            "schema_version": DYNAMIC_REPORT_SCHEMA_VERSION,
            "proposal_type": "dynamic_review_decision_proposal",
            "proposal_id": (
                f"dynamic_review_decision_proposal:{run_id}:{demo_review_id}:"
                f"{canonical_sha256(identity)[:12]}"
            ),
            "source": report_source(
                release_id=release_id,
                generated_at=generated_at,
                versions=versions,
            ),
            "submission": submission,
            "submitted_opinion_units": submitted_units,
            "catalog_relationships": relationships,
            "catalog_comparison": catalog_comparison,
            "alternative_recommendations": {
                "status": "COMPLETED"
                if alternatives
                else (
                    "NO_CANDIDATE_WITH_REPAIR_EVIDENCE"
                    if conditions
                    else (
                        "NEGATIVE_INPUT_UNRESOLVED"
                        if excluded_conditions
                        else "NO_NEGATIVE_SUBMITTED_ASPECT_STATUS"
                    )
                ),
                "ranking_contract": {
                    "source": "submitted_negative_aspect_status_review_votes",
                    "status_match_policy": "exact_status_cluster_id_for_undesired_condition",
                    "positive_alternative_policy": "same_aspect_different_status_with_positive_dominant_sentiment",
                    "negative_evidence_strength": "support_reliability * negative_wilson_upper_95",
                    "positive_alternative_strength": "support_reliability * positive_wilson_lower_95",
                    "weakness_utility": "weighted_mean(positive_alternative_strength - negative_evidence_strength)",
                    "experience_similarity_weight": 0.25,
                    "weakness_utility_weight": 0.75,
                    "weakness_repair_score": "0.25 * experience_similarity + 0.75 * ((weakness_utility + 1) / 2)",
                },
                "observed_negative_opinion_unit_count": observed_negative_count,
                "negative_conditions": conditions,
                "excluded_negative_conditions": excluded_conditions,
                "alternatives": alternatives,
            },
            "generator_version": DYNAMIC_REPORT_GENERATOR_VERSION,
            "versions": versions,
        }
        markdown = render_dynamic_review_decision_markdown(payload)
        json_path, markdown_path = write_report_pair(
            output_dir, "dynamic_decision_proposal", payload, markdown
        )
        return {"payload": payload, "json_path": json_path, "markdown_path": markdown_path}
    finally:
        connection.close()
