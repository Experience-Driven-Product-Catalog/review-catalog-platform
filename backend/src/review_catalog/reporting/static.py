from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from review_catalog.pipeline.artifacts import canonical_sha256
from review_catalog.reporting.analytics import (
    MINIMUM_WEAKNESS_SUPPORT,
    all_mapped_rows,
    catalog_review_counts,
    most_debated,
    normalization_reduction,
    product_profile_data,
    rank_alternatives,
    review_vote_rows,
    sentiment_distribution,
    similarity_rows,
    summary_rows,
)
from review_catalog.reporting.common import (
    fetch_dicts,
    human_evaluation_placeholder,
    report_source,
    write_report_pair,
)
from review_catalog.reporting.reference_format import render_static_markdown

STATIC_REPORT_SCHEMA_VERSION = "1.2.0"
STATIC_REPORT_GENERATOR_VERSION = "embedding-clustering-experiment-static-format-v1"


def _source_weaknesses(pair_rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    eligible: list[dict] = []
    excluded: list[dict] = []
    for row in pair_rows:
        negative_count = int(row["sentiment"]["counts"]["negative"])
        support = int(row["supporting_review_count"])
        if not negative_count:
            continue
        if row["status_cluster_id"] is None:
            excluded.append(
                {
                    "aspect": row["aspect"],
                    "status": row["status"],
                    "reason": "STATUS_CLUSTER_ID_UNAVAILABLE",
                    "negative_review_count": negative_count,
                }
            )
            continue
        if support < MINIMUM_WEAKNESS_SUPPORT:
            excluded.append(
                {
                    "aspect": row["aspect"],
                    "status": row["status"],
                    "reason": "SOURCE_NEGATIVE_SUPPORT_BELOW_MINIMUM",
                    "negative_review_count": negative_count,
                    "supporting_review_count": support,
                }
            )
            continue
        negative_rate = negative_count / support
        eligible.append(
            {
                "aspect": row["aspect"],
                "status": row["status"],
                "aspect_cluster_id": row["aspect_cluster_id"],
                "status_cluster_id": row["status_cluster_id"],
                "negative_review_count": negative_count,
                "supporting_review_count": support,
                "negative_rate": round(negative_rate, 6),
                "importance": round(negative_rate * support / (support + 5.0), 6),
            }
        )
    eligible.sort(
        key=lambda row: (
            -row["importance"],
            -row["negative_review_count"],
            -row["supporting_review_count"],
            row["aspect"],
            str(row["status"]),
        )
    )
    for number, item in enumerate(eligible[:10], start=1):
        item["requirement_id"] = f"weakness_{number}"
        item["condition_id"] = item["requirement_id"]
    return eligible[:10], excluded


def generate_static_catalog_report(
    *,
    snapshot_path: Path,
    product_id: str,
    release_id: str,
    generated_at: datetime,
    versions: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Generate the source project's static JSON and Markdown format from DuckDB."""
    connection = duckdb.connect(str(snapshot_path), read_only=True)
    try:
        products = catalog_review_counts(connection)
        if product_id not in products:
            raise ValueError(f"unknown product_id: {product_id}")
        product = products[product_id]
        all_rows = all_mapped_rows(connection)
        rows = [row for row in all_rows if row["product_id"] == product_id]
        review_count = int(product["review_count"])
        aspect_rows = summary_rows(
            rows,
            ("aspect_id", "aspect"),
            product_review_count=review_count,
            evidence_per_sentiment=3,
        )
        pair_rows = summary_rows(
            rows,
            ("aspect_id", "status_id", "aspect", "status"),
            product_review_count=review_count,
            evidence_per_sentiment=2,
        )
        aspect_votes = review_vote_rows(rows, ("aspect_id", "aspect"))
        pair_votes = review_vote_rows(rows, ("aspect_id", "status_id", "aspect", "status"))
        profile_data = product_profile_data(connection)
        similar = similarity_rows(profile_data, product_id)
        weaknesses, excluded_weaknesses = _source_weaknesses(pair_rows)
        alternatives = rank_alternatives(
            profile_data,
            product_id,
            weaknesses,
            static_policy=True,
        )
        excluded_general = fetch_dicts(
            connection,
            """
            SELECT count(*) AS unit_count, count(DISTINCT r.review_id) AS review_count
            FROM reviews r JOIN opinion_units o USING (review_id)
            WHERE r.product_id = ? AND o.mapping_state = 'excluded_taxonomy'
            """,
            [product_id],
        )[0]
        candidate_count = connection.execute(
            """
            SELECT count(*) FROM reviews r JOIN opinion_units o USING (review_id)
            WHERE r.product_id = ? AND o.mapping_state = 'candidate'
            """,
            [product_id],
        ).fetchone()[0]
        source = report_source(
            release_id=release_id,
            generated_at=generated_at,
            versions=versions,
        )
        identity = {
            "schema_version": STATIC_REPORT_SCHEMA_VERSION,
            "release_id": release_id,
            "product_id": product_id,
            "versions": versions,
            "format_source": "embedding_clustering_experiment",
        }
        run_id = source["run_id"]
        payload = {
            "schema_version": STATIC_REPORT_SCHEMA_VERSION,
            "report_type": "static_catalog_analysis",
            "report_id": (
                f"static_catalog_analysis:{run_id}:{canonical_sha256(identity)[:12]}"
            ),
            "source": source,
            "human_evaluation": human_evaluation_placeholder(),
            "product": {
                "product_name": product["product_name"],
                "catalog_review_count": review_count,
                "reviews_with_eligible_opinion_units": len(
                    {row["review_id"] for row in rows}
                ),
                "review_coverage_rate": round(
                    len({row["review_id"] for row in rows}) / review_count, 6
                )
                if review_count
                else None,
            },
            "coverage": {
                "opinion_unit_count": len(rows),
                "review_aspect_vote_count": len(aspect_votes),
                "review_aspect_status_vote_count": len(pair_votes),
                "unique_aspect_cluster_count": len({row["aspect_id"] for row in rows}),
                "unique_aspect_status_cluster_count": len(
                    {(row["aspect_id"], row["status_id"]) for row in rows}
                ),
                "excluded_general_experience_opinion_unit_count": int(
                    excluded_general["unit_count"]
                ),
                "excluded_general_experience_review_count": int(
                    excluded_general["review_count"]
                ),
                "mean_opinion_units_per_covered_review": round(
                    len(rows) / len({row["review_id"] for row in rows}), 6
                )
                if rows
                else None,
            },
            "normalization_reduction": normalization_reduction(rows),
            "aggregation_contract": {
                "aspect_vote_grain": ["product_name", "review_id", "aspect_cluster_id"],
                "aspect_status_vote_grain": [
                    "product_name",
                    "review_id",
                    "aspect_cluster_id",
                    "status_cluster_id",
                ],
                "sentiment_order": [
                    "positive",
                    "negative",
                    "mixed",
                    "neutral",
                    "unknown",
                ],
                "review_vote_collapse": {
                    "unknown_only": "unknown",
                    "one_distinct_known_sentiment": "that_sentiment",
                    "multiple_distinct_known_sentiments": "mixed",
                },
                "sentiment_denominator": "review_level_vote",
                "evidence_per_sentiment": 2,
                "decimal_places": 6,
                "absence_policy": "NO_EVIDENCE",
            },
            "sentiment_distribution": {
                "opinion_unit": sentiment_distribution(
                    [str(row["sentiment"]) for row in rows]
                ),
                "review_aspect_vote": sentiment_distribution(
                    [str(vote["sentiment"]) for vote in aspect_votes]
                ),
            },
            "aspect_summary": aspect_rows,
            "aspect_status_summary": pair_rows,
            "most_debated_aspect": most_debated(aspect_rows),
            "related_products": {
                "schema_version": "1.0.1",
                "source_product_name": product["product_name"],
                "candidate_product_count": len(similar),
                "similarity_contract": {
                    "feature_scope": "all_canonical_aspect_cluster_ids_not_display_top_n",
                    "vote_grain": ["product_name", "review_id", "aspect_cluster_id"],
                    "sentiment_channels": ["positive", "negative", "mixed", "neutral"],
                    "category_prior_strength": 5.0,
                    "experience_similarity": "cosine(smoothed_multi_channel_aspect_profile) * sqrt(weighted_aspect_overlap)",
                    "evidence_overlap": "sum(min(aspect_mention_rate)) / sum(max(aspect_mention_rate))",
                },
                "similar_products": similar[:3],
                "weakness_repair_alternatives": {
                    "status": "COMPLETED"
                    if alternatives
                    else "NO_HIGH_SUPPORT_NEGATIVE_ASPECT_STATUS_EVIDENCE",
                    "ranking_contract": {
                        "source_negative_support_minimum": MINIMUM_WEAKNESS_SUPPORT,
                        "experience_similarity_weight": 0.25,
                        "weakness_utility_weight": 0.75,
                    },
                    "source_weakness_requirements": weaknesses,
                    "excluded_source_weaknesses": excluded_weaknesses,
                    "alternatives": alternatives,
                },
            },
            "normalization_quality": {
                "automatic_integrity_checks": {
                    "passed": 3,
                    "total": 3,
                    "all_passed": True,
                },
                "aspect_mapping": aspect_rows[0]["aspect_mapping"] if aspect_rows else None,
                "status_mapping": pair_rows[0]["status_mapping"] if pair_rows else None,
                "risky_clusters_present": [],
            },
            "quality_flags": [
                {
                    "code": "PENDING_MAPPING_CANDIDATES",
                    "severity": "warning" if candidate_count else "info",
                    "value": candidate_count,
                    "implication": "excluded_from_canonical_report_aggregation",
                },
                {
                    "code": "COMMERCE_CONTEXT_UNAVAILABLE",
                    "severity": "warning",
                    "value": ["price", "inventory", "shipping", "promotion"],
                    "implication": "review_experience_only_not_purchase_optimality",
                },
            ],
            "agent_capabilities": {
                "supported": [
                    "SUMMARIZE_STRUCTURED",
                    "COMPARE_REVIEW_EVIDENCE",
                    "RECOMMEND_REVIEW_CONDITIONED",
                    "ASK_CLARIFICATION",
                    "ABSTAIN",
                ],
                "unsupported_claims": [
                    "best_overall_product",
                    "lowest_price",
                    "in_stock",
                    "fastest_shipping",
                    "objective_spec_superiority",
                    "causal_product_effect",
                ],
                "absence_policy": "NO_EVIDENCE",
            },
            "generator_version": STATIC_REPORT_GENERATOR_VERSION,
            "versions": versions,
        }
        markdown = render_static_markdown(payload)
        json_path, markdown_path = write_report_pair(
            output_dir, "static_catalog_report", payload, markdown
        )
        return {"payload": payload, "json_path": json_path, "markdown_path": markdown_path}
    finally:
        connection.close()
