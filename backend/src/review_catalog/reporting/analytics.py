from __future__ import annotations

import math
from collections import defaultdict
from copy import deepcopy
from typing import Any

import duckdb

from review_catalog.reporting.common import collapse_sentiments, fetch_dicts, wilson_interval

SENTIMENT_ORDER = ("positive", "negative", "mixed", "neutral", "unknown")
PROFILE_SENTIMENTS = ("positive", "negative", "mixed", "neutral")
PROFILE_PRIOR_STRENGTH = 5.0
MINIMUM_WEAKNESS_SUPPORT = 2
RELATED_PRODUCT_LIMIT = 3


def round_float(value: Any, places: int = 6) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), places)
    return 0.0 if rounded == 0 else rounded


def sentiment_distribution(labels: list[str], *, places: int = 6) -> dict[str, Any]:
    counts = {sentiment: labels.count(sentiment) for sentiment in SENTIMENT_ORDER}
    total = len(labels)
    shares = {
        sentiment: round_float(count / total, places) if total else None
        for sentiment, count in counts.items()
    }
    probabilities = [count / total for count in counts.values() if count and total]
    entropy = (
        -sum(probability * math.log2(probability) for probability in probabilities)
        / math.log2(len(SENTIMENT_ORDER))
        if total
        else None
    )
    dominant = (
        max(SENTIMENT_ORDER, key=lambda value: (counts[value], -SENTIMENT_ORDER.index(value)))
        if total
        else None
    )
    positive_lower, positive_upper = wilson_interval(counts["positive"], total)
    negative_lower, negative_upper = wilson_interval(counts["negative"], total)
    return {
        "counts": counts,
        "shares": shares,
        "dominant_sentiment": dominant,
        "dominant_share": shares[dominant] if dominant else None,
        "positive_wilson_95": {
            "lower": round_float(positive_lower, places) if total else None,
            "upper": round_float(positive_upper, places) if total else None,
        },
        "negative_wilson_95": {
            "lower": round_float(negative_lower, places) if total else None,
            "upper": round_float(negative_upper, places) if total else None,
        },
        "normalized_entropy": round_float(entropy, places),
    }


def catalog_review_counts(connection: duckdb.DuckDBPyConnection) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(
        connection,
        """
        SELECT p.product_id, p.product_name, count(r.review_id) AS review_count
        FROM products p LEFT JOIN reviews r USING (product_id)
        GROUP BY p.product_id, p.product_name
        ORDER BY p.product_name
        """,
    )
    return {row["product_id"]: row for row in rows}


def all_mapped_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return fetch_dicts(
        connection,
        """
        SELECT p.product_id, p.product_name, r.review_id,
               r.external_review_idx AS review_idx, r.review_text,
               o.opinion_unit_id, o.source_opinion_unit_idx, o.unit_position,
               o.raw_aspect, o.raw_status,
               o.excerpt, o.opinion, o.sentiment,
               o.aspect_id, o.aspect, o.status_id, o.status,
               o.aspect_distance, o.status_distance
        FROM products p
        JOIN reviews r USING (product_id)
        JOIN opinion_units o USING (review_id)
        WHERE o.mapping_state = 'mapped_exact'
        ORDER BY p.product_name, r.review_id, o.unit_position
        """,
    )


def review_vote_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    context: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row["review_id"], *(row.get(field) for field in fields))
        grouped[key].add(str(row["sentiment"]))
        context[key] = row
    votes: list[dict[str, Any]] = []
    for key, sentiments in grouped.items():
        row = context[key]
        votes.append(
            {
                "review_id": key[0],
                "review_idx": row.get("review_idx"),
                "product_id": row.get("product_id"),
                "product_name": row.get("product_name"),
                **{field: value for field, value in zip(fields, key[1:], strict=True)},
                "sentiment": collapse_sentiments(sentiments),
            }
        )
    return votes


def _mapping_stats(rows: list[dict[str, Any]], distance_field: str) -> dict[str, Any]:
    distances = [float(row[distance_field]) for row in rows if row.get(distance_field) is not None]
    distances.sort()

    def quantile(fraction: float) -> float | None:
        if not distances:
            return None
        position = (len(distances) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return round_float(distances[lower])
        value = distances[lower] + (distances[upper] - distances[lower]) * (position - lower)
        return round_float(value)

    return {
        "eligible_opinion_unit_count": len(rows),
        "mapped_opinion_unit_count": len(distances),
        "mapping_rate": round_float(len(distances) / len(rows)) if rows else None,
        "mapped_distance": {
            "p50": quantile(0.50),
            "p95": quantile(0.95),
            "max": round_float(max(distances)) if distances else None,
        },
    }


def _evidence(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    field_values: tuple[Any, ...],
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    selected = [
        row
        for row in rows
        if all(row.get(field) == value for field, value in zip(fields, field_values, strict=True))
    ]
    by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_review[row["review_id"]].append(row)
    vote_by_review = {
        review_id: collapse_sentiments({str(row["sentiment"]) for row in review_rows})
        for review_id, review_rows in by_review.items()
    }
    output = {sentiment: [] for sentiment in SENTIMENT_ORDER}
    for sentiment in SENTIMENT_ORDER:
        candidates = []
        for review_id, review_rows in by_review.items():
            if vote_by_review[review_id] != sentiment:
                continue
            candidates.append(
                min(
                    review_rows,
                    key=lambda row: (
                        str(row["sentiment"]) != sentiment,
                        float(row.get("aspect_distance") or 0.0)
                        + float(row.get("status_distance") or 0.0),
                        row.get("source_opinion_unit_idx")
                        if row.get("source_opinion_unit_idx") is not None
                        else math.inf,
                        int(row["unit_position"]),
                        row["opinion_unit_id"],
                    ),
                )
            )
        candidates.sort(
            key=lambda row: (
                str(row["sentiment"]) != sentiment,
                float(row.get("aspect_distance") or 0.0)
                + float(row.get("status_distance") or 0.0),
                row.get("source_opinion_unit_idx")
                if row.get("source_opinion_unit_idx") is not None
                else math.inf,
                int(row["unit_position"]),
                row["opinion_unit_id"],
            )
        )
        for row in candidates[:limit]:
            output[sentiment].append(
                {
                    "opinion_unit_idx": row.get("source_opinion_unit_idx"),
                    "opinion_unit_id": row["opinion_unit_id"],
                    "review_idx": row.get("review_idx"),
                    "review_text": row["review_text"],
                    "excerpt": row["excerpt"],
                    "opinion": row["opinion"],
                    "sentiment": row["sentiment"],
                    "review_vote_sentiment": sentiment,
                    "raw_aspect": row["raw_aspect"],
                    "raw_status": row["raw_status"],
                    "aspect_cluster_id": row["aspect_id"],
                    "aspect": row["aspect"],
                    "status_cluster_id": row["status_id"],
                    "status": row["status"],
                    "aspect_mapping_distance": round_float(row.get("aspect_distance")),
                    "status_mapping_distance": round_float(row.get("status_distance")),
                }
            )
    return output


def summary_rows(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    *,
    product_review_count: int,
    evidence_per_sentiment: int,
) -> list[dict[str, Any]]:
    votes = review_vote_rows(rows, fields)
    grouped_votes: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for vote in votes:
        grouped_votes[tuple(vote.get(field) for field in fields)].append(vote)
    outputs: list[dict[str, Any]] = []
    for values, item_votes in grouped_votes.items():
        record = dict(zip(fields, values, strict=True))
        matched_rows = [
            row
            for row in rows
            if all(row.get(field) == value for field, value in zip(fields, values, strict=True))
        ]
        payload: dict[str, Any] = {
            "aspect_cluster_id": record["aspect_id"],
            "aspect": record["aspect"],
            "supporting_review_count": len(item_votes),
            "mention_rate": round_float(len(item_votes) / product_review_count)
            if product_review_count
            else None,
            "sentiment": sentiment_distribution([vote["sentiment"] for vote in item_votes]),
            "aspect_mapping": _mapping_stats(matched_rows, "aspect_distance"),
            "normalization_risk": {"aspect": None},
            "evidence": _evidence(
                rows,
                fields,
                values,
                limit=evidence_per_sentiment,
            ),
        }
        if "status_id" in fields:
            payload.update(
                {
                    "status_cluster_id": record["status_id"],
                    "status": record["status"],
                    "status_mapping": _mapping_stats(matched_rows, "status_distance"),
                    "normalization_risk": {"aspect": None, "status": None},
                }
            )
        outputs.append(payload)
    outputs.sort(
        key=lambda row: (
            -row["supporting_review_count"],
            str(row["aspect"]),
            str(row.get("status") or ""),
            str(row["aspect_cluster_id"]),
            str(row.get("status_cluster_id") or ""),
        )
    )
    for rank, row in enumerate(outputs, start=1):
        row["rank"] = rank
    return outputs


def normalization_reduction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    raw_aspects = {row["raw_aspect"] for row in rows}
    aspects = {row["aspect_id"] for row in rows}
    raw_statuses = {
        (row["aspect_id"], row["raw_status"])
        for row in rows
        if row["raw_status"] is not None
    }
    statuses = {row["status_id"] for row in rows if row["status_id"] is not None}
    raw_pairs = {(row["raw_aspect"], row["raw_status"]) for row in rows}
    pairs = {(row["aspect_id"], row["status_id"]) for row in rows}

    def item(raw_count: int, normalized_count: int, grain: str) -> dict[str, Any]:
        return {
            "raw_count": raw_count,
            "normalized_count": normalized_count,
            "comparison_grain": grain,
            "decrease_rate": round_float((raw_count - normalized_count) / raw_count)
            if raw_count
            else None,
        }

    return {
        "aspect": item(
            len(raw_aspects), len(aspects), "unique_raw_aspect_to_unique_aspect_cluster_id"
        ),
        "status": item(
            len(raw_statuses),
            len(statuses),
            "unique_aspect_cluster_id_raw_status_to_unique_status_cluster_id; null_status_excluded",
        ),
        "aspect_status": item(
            len(raw_pairs),
            len(pairs),
            "unique_raw_aspect_raw_status_to_unique_aspect_cluster_id_status_cluster_id",
        ),
    }


def most_debated(aspect_rows: list[dict[str, Any]], *, top_limit: int = 10) -> dict | None:
    candidates = [
        row
        for row in aspect_rows[:top_limit]
        if row["sentiment"]["counts"]["positive"]
        + row["sentiment"]["counts"]["negative"]
        > 0
    ]
    if not candidates:
        return None
    selected = min(
        candidates,
        key=lambda row: (
            abs(
                float(row["sentiment"]["shares"]["positive"] or 0.0)
                - float(row["sentiment"]["shares"]["negative"] or 0.0)
            ),
            -row["supporting_review_count"],
            row["aspect"],
            row["aspect_cluster_id"],
        ),
    )
    result = deepcopy(selected)
    result["positive_negative_rate_gap"] = round_float(
        abs(
            float(selected["sentiment"]["shares"]["positive"] or 0.0)
            - float(selected["sentiment"]["shares"]["negative"] or 0.0)
        )
    )
    result["selection_scope"] = f"top_{top_limit}_aspect_summary"
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def product_profile_data(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, Any]:
    rows = all_mapped_rows(connection)
    products = catalog_review_counts(connection)
    aspect_votes = review_vote_rows(rows, ("aspect_id", "aspect"))
    pair_votes = review_vote_rows(rows, ("aspect_id", "status_id", "aspect", "status"))
    aspect_labels = {str(row["aspect_id"]): str(row["aspect"]) for row in rows}
    aspect_ids = sorted(aspect_labels)
    counts_by_product_aspect: dict[tuple[str, str], dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for vote in aspect_votes:
        grouped[(vote["product_id"], vote["aspect_id"])].append(vote)
    for key, votes in grouped.items():
        counts_by_product_aspect[key] = {
            "supporting_review_count": len(votes),
            "counts": {sentiment: sum(vote["sentiment"] == sentiment for vote in votes) for sentiment in SENTIMENT_ORDER},
        }

    priors: dict[str, dict[str, float]] = {}
    for aspect_id in aspect_ids:
        totals = {sentiment: 0 for sentiment in PROFILE_SENTIMENTS}
        for (_product_id, candidate_aspect_id), record in counts_by_product_aspect.items():
            if candidate_aspect_id == aspect_id:
                for sentiment in PROFILE_SENTIMENTS:
                    totals[sentiment] += record["counts"][sentiment]
        total = sum(totals.values())
        priors[aspect_id] = {
            sentiment: totals[sentiment] / total if total else 1.0 / len(PROFILE_SENTIMENTS)
            for sentiment in PROFILE_SENTIMENTS
        }

    profiles: dict[str, dict[str, Any]] = {}
    for product_id, product in products.items():
        review_count = int(product["review_count"])
        mention_vector: list[float] = []
        sentiment_vector: list[float] = []
        mention_rates: dict[str, float] = {}
        reliability: dict[str, float] = {}
        for aspect_id in aspect_ids:
            record = counts_by_product_aspect.get((product_id, aspect_id))
            support = int(record["supporting_review_count"]) if record else 0
            counts = record["counts"] if record else {sentiment: 0 for sentiment in SENTIMENT_ORDER}
            mention_rate = support / review_count if review_count else 0.0
            known = sum(counts[sentiment] for sentiment in PROFILE_SENTIMENTS)
            unknown_share = counts["unknown"] / support if support else 0.0
            support_reliability = support / (support + PROFILE_PRIOR_STRENGTH)
            smoothed = {
                sentiment: (
                    counts[sentiment] + PROFILE_PRIOR_STRENGTH * priors[aspect_id][sentiment]
                )
                / (known + PROFILE_PRIOR_STRENGTH)
                for sentiment in PROFILE_SENTIMENTS
            }
            scale = math.sqrt(mention_rate) * support_reliability * (1.0 - unknown_share)
            mention_vector.append(math.sqrt(mention_rate))
            sentiment_vector.extend(scale * smoothed[sentiment] for sentiment in PROFILE_SENTIMENTS)
            mention_rates[aspect_id] = mention_rate
            reliability[aspect_id] = support_reliability
        profiles[product_id] = {
            "product_id": product_id,
            "product_name": product["product_name"],
            "catalog_review_count": review_count,
            "mention_vector": mention_vector,
            "sentiment_vector": sentiment_vector,
            "mention_rates": mention_rates,
            "support_reliability": reliability,
        }

    pair_rates: dict[tuple[str, str, str | None], float] = {}
    pair_grouped: dict[tuple[str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for vote in pair_votes:
        pair_grouped[(vote["product_id"], vote["aspect_id"], vote["status_id"])].append(vote)
    for key, votes in pair_grouped.items():
        pair_rates[key] = len(votes) / int(products[key[0]]["review_count"])
    return {
        "rows": rows,
        "products": products,
        "profiles": profiles,
        "aspect_ids": aspect_ids,
        "aspect_labels": aspect_labels,
        "pair_rates": pair_rates,
    }


def similarity_rows(profile_data: dict[str, Any], source_product_id: str) -> list[dict[str, Any]]:
    source = profile_data["profiles"][source_product_id]
    aspect_ids = profile_data["aspect_ids"]
    pair_rates = profile_data["pair_rates"]
    pair_keys = sorted({key[1:] for key in pair_rates}, key=lambda value: (value[0], str(value[1])))
    rows: list[dict[str, Any]] = []
    for candidate_id, candidate in profile_data["profiles"].items():
        if candidate_id == source_product_id:
            continue
        overlap_numerator = sum(
            min(source["mention_rates"][aspect_id], candidate["mention_rates"][aspect_id])
            for aspect_id in aspect_ids
        )
        overlap_denominator = sum(
            max(source["mention_rates"][aspect_id], candidate["mention_rates"][aspect_id])
            for aspect_id in aspect_ids
        )
        overlap = overlap_numerator / overlap_denominator if overlap_denominator else 0.0
        reliability = (
            sum(
                min(source["mention_rates"][aspect_id], candidate["mention_rates"][aspect_id])
                * math.sqrt(
                    source["support_reliability"][aspect_id]
                    * candidate["support_reliability"][aspect_id]
                )
                for aspect_id in aspect_ids
            )
            / overlap_numerator
            if overlap_numerator
            else 0.0
        )
        sentiment_similarity = _cosine(source["sentiment_vector"], candidate["sentiment_vector"])
        experience_similarity = sentiment_similarity * math.sqrt(overlap)
        status_numerator = sum(
            min(
                pair_rates.get((source_product_id, *key), 0.0),
                pair_rates.get((candidate_id, *key), 0.0),
            )
            for key in pair_keys
        )
        status_denominator = sum(
            max(
                pair_rates.get((source_product_id, *key), 0.0),
                pair_rates.get((candidate_id, *key), 0.0),
            )
            for key in pair_keys
        )
        rows.append(
            {
                "product_id": candidate_id,
                "product_name": candidate["product_name"],
                "catalog_review_count": candidate["catalog_review_count"],
                "experience_similarity": round_float(experience_similarity),
                "components": {
                    "aspect_mention_similarity": round_float(
                        _cosine(source["mention_vector"], candidate["mention_vector"])
                    ),
                    "aspect_sentiment_similarity": round_float(sentiment_similarity),
                    "evidence_overlap": round_float(overlap),
                    "support_reliability": round_float(reliability),
                    "aspect_status_exact_overlap": round_float(
                        status_numerator / status_denominator if status_denominator else 0.0
                    ),
                },
                "shared_aspects": [],
                "normalization_risk": {
                    "source_risky_cluster_count": 0,
                    "candidate_risky_cluster_count": 0,
                },
                "quality_codes": [
                    *(["LOW_EVIDENCE_OVERLAP"] if overlap < 0.2 else []),
                    *(["LOW_SHARED_SUPPORT_RELIABILITY"] if reliability < 0.5 else []),
                ],
            }
        )
    rows.sort(key=lambda row: (-float(row["experience_similarity"] or 0.0), row["product_name"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def pair_profile_indexes(profile_data: dict[str, Any]) -> tuple[dict, dict]:
    votes = review_vote_rows(
        profile_data["rows"], ("aspect_id", "status_id", "aspect", "status")
    )
    grouped: dict[tuple[str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for vote in votes:
        grouped[(vote["product_id"], vote["aspect_id"], vote["status_id"])].append(vote)
    exact: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_aspect: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (product_id, aspect_id, status_id), item_votes in grouped.items():
        if status_id is None:
            continue
        first = item_votes[0]
        profile = {
            "product_id": product_id,
            "aspect_cluster_id": aspect_id,
            "status_cluster_id": status_id,
            "aspect": first["aspect"],
            "status": first["status"],
            "supporting_review_count": len(item_votes),
            "support_reliability": min(1.0, len(item_votes) / MINIMUM_WEAKNESS_SUPPORT),
            "sentiment": sentiment_distribution([vote["sentiment"] for vote in item_votes]),
        }
        exact[(product_id, aspect_id, status_id)] = profile
        by_aspect[(product_id, aspect_id)].append(profile)
    for profiles in by_aspect.values():
        profiles.sort(key=lambda row: (str(row["status"]), row["status_cluster_id"]))
    return exact, by_aspect


def _condition_match(
    product_id: str,
    condition: dict[str, Any],
    exact: dict,
    by_aspect: dict,
    *,
    static_policy: bool,
) -> dict[str, Any]:
    target = exact.get((product_id, condition["aspect_cluster_id"], condition["status_cluster_id"]))
    if target is None:
        negative_strength = 0.0
    elif static_policy:
        negative_strength = float(target["support_reliability"])
    else:
        negative_strength = float(target["support_reliability"]) * float(
            target["sentiment"]["negative_wilson_95"]["upper"] or 0.0
        )
    positives: list[tuple[float, dict[str, Any]]] = []
    for profile in by_aspect.get((product_id, condition["aspect_cluster_id"]), []):
        if profile["status_cluster_id"] == condition["status_cluster_id"]:
            continue
        if profile["sentiment"]["dominant_sentiment"] != "positive":
            continue
        strength = float(profile["support_reliability"]) * float(
            profile["sentiment"]["positive_wilson_95"]["lower"] or 0.0
        )
        positives.append((strength, profile))
    positives.sort(key=lambda item: (-item[0], -item[1]["supporting_review_count"], str(item[1]["status"])))
    positive = positives[0][1] if positives else None
    positive_strength = positives[0][0] if positives else 0.0
    if target is not None and positive is not None:
        relation = "EXACT_UNDESIRED_STATUS_AND_POSITIVE_ALTERNATIVE"
    elif target is not None:
        relation = "EXACT_UNDESIRED_STATUS_ONLY"
    elif positive is not None:
        relation = "POSITIVE_ALTERNATIVE_ONLY"
    else:
        relation = "NO_EVIDENCE"
    reasons = []
    if target is None:
        reasons.append(f"'{condition['aspect']} {condition['status']}' 부정 상태 미관측")
    else:
        reasons.append(
            f"'{condition['aspect']} {condition['status']}' 부정 근거 "
            f"{target['sentiment']['counts']['negative']}개"
        )
    if positive is not None:
        reasons.append(
            f"'{condition['aspect']} {positive['status']}' 긍정 근거 "
            f"{positive['sentiment']['counts']['positive']}개"
        )
    return {
        "condition_id": condition["condition_id"],
        "aspect": condition["aspect"],
        "status": condition["status"],
        "relation": relation,
        "utility": round_float(positive_strength - negative_strength),
        "undesired_status_profile": target,
        "positive_alternative_profile": positive,
        "reason": "; ".join(reasons) if reasons else "비교 가능한 카탈로그 근거 없음",
    }


def rank_alternatives(
    profile_data: dict[str, Any],
    source_product_id: str,
    conditions: list[dict[str, Any]],
    *,
    static_policy: bool,
) -> list[dict[str, Any]]:
    if not conditions:
        return []
    similarities = similarity_rows(profile_data, source_product_id)
    exact, by_aspect = pair_profile_indexes(profile_data)
    total_importance = sum(float(condition["importance"]) for condition in conditions)
    alternatives = []
    for similarity in similarities:
        matches = [
            _condition_match(
                similarity["product_id"], condition, exact, by_aspect, static_policy=static_policy
            )
            for condition in conditions
        ]
        supported = [match for match in matches if match["relation"] != "NO_EVIDENCE"]
        if not supported:
            continue
        utility = sum(
            float(condition["importance"]) * float(match["utility"] or 0.0)
            for condition, match in zip(conditions, matches, strict=True)
        ) / total_importance
        score = 0.25 * float(similarity["experience_similarity"] or 0.0) + 0.75 * (
            (utility + 1.0) / 2.0
        )
        alternatives.append(
            {
                "product_id": similarity["product_id"],
                "product_name": similarity["product_name"],
                "catalog_review_count": similarity["catalog_review_count"],
                "weakness_utility": round_float(utility),
                "weakness_utility_score": round_float(utility),
                "experience_similarity": similarity["experience_similarity"],
                "weakness_repair_score": round_float(score),
                "evidence_coverage": round_float(len(supported) / len(conditions)),
                "requirement_matches": matches,
                "recommendation_reason": " / ".join(match["reason"] for match in supported),
                "quality_codes": similarity["quality_codes"],
            }
        )
    alternatives.sort(
        key=lambda row: (
            -float(row["weakness_repair_score"] or 0.0),
            -float(row["weakness_utility"] or 0.0),
            -float(row["experience_similarity"] or 0.0),
            row["product_name"],
        )
    )
    for rank, row in enumerate(alternatives, start=1):
        row["rank"] = rank
    return alternatives[:RELATED_PRODUCT_LIMIT]
