"""Full-dataset clustering used only by the manually triggered rebuild DAG."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_distances

from review_catalog.normalization.embedding import Embedder


@dataclass(frozen=True)
class ReclusteredTaxonomy:
    assignments: pd.DataFrame
    aspect_nodes: pd.DataFrame
    aspect_clusters: pd.DataFrame
    status_nodes: pd.DataFrame
    status_clusters: pd.DataFrame


def _sorted_unique_tuple(values: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(sorted(set(map(str, values))))


def _build_nodes(
    frame: pd.DataFrame,
    text_column: str,
    boundary_columns: Sequence[str] = (),
) -> pd.DataFrame:
    grouping = [*boundary_columns, text_column]
    if frame[text_column].isna().any():
        raise ValueError(f"{text_column} must be non-null before clustering")
    nodes = (
        frame.groupby(grouping, sort=True, observed=True, dropna=False)
        .agg(
            source_row_count=("opinion_unit_id", "size"),
            _review_ids=("review_id", _sorted_unique_tuple),
        )
        .reset_index()
    )
    nodes["source_row_count"] = nodes["source_row_count"].astype("int64")
    nodes["unique_review_count"] = nodes["_review_ids"].map(len).astype("int64")
    return nodes


def _candidate_limit(size: int, config: Mapping[str, Any]) -> int:
    counts = config["candidate_count"]
    if size == 1:
        return int(counts["size_1"])
    if size == 2:
        return int(counts["size_2"])
    if size < 10:
        return int(counts["size_3_to_9"])
    return int(counts["size_10_plus"])


def _role_allowed(
    expression: str,
    role: str,
    config: Mapping[str, Any],
    forbidden: set[str],
) -> bool:
    filters = config["role_filters"]
    if expression in set(map(str, filters[f"{role}_disallowed_exact"])):
        return False
    return not (
        role == "status"
        and bool(filters["exclude_status_matching_aspect"])
        and expression in forbidden
    )


def _select_label(
    nodes: pd.DataFrame,
    distances: np.ndarray,
    text_column: str,
    role: str,
    config: Mapping[str, Any],
    naming_max_distance: float,
    forbidden: set[str],
) -> dict[str, Any]:
    size = len(nodes)
    expressions = nodes[text_column].astype(str).tolist()
    if size == 1:
        average = np.array([0.0])
        maximum = np.array([0.0])
    else:
        average = distances.sum(axis=1) / (size - 1)
        maximum = distances.max(axis=1)
    medoid_position = min(range(size), key=lambda pos: (average[pos], expressions[pos]))
    cluster_maximum = float(distances.max()) if size > 1 else 0.0

    if size == 1:
        details = [
            {
                "expression": expressions[0],
                "average_distance": 0.0,
                "max_distance": 0.0,
                "unique_review_count": int(nodes.iloc[0]["unique_review_count"]),
                "central_candidate": True,
                "role_filter_passed": True,
                "centrality": 1.0,
                "frequency_score": 1.0,
                "combined_score": 1.0,
            }
        ]
        return {
            "canonical_label": expressions[0],
            "canonical_position": 0,
            "medoid_label": expressions[0],
            "naming_status": "singleton_inherited",
            "cluster_max_distance": cluster_maximum,
            "member_details": details,
        }

    ordered = sorted(range(size), key=lambda pos: (average[pos], expressions[pos]))
    central = set(ordered[: min(size, _candidate_limit(size, config))])
    eligible = [
        pos
        for pos in ordered
        if pos in central and _role_allowed(expressions[pos], role, config, forbidden)
    ]
    scores: dict[int, tuple[float, float, float]] = {}
    if eligible:
        centrality = np.clip(1.0 - np.array([average[pos] for pos in eligible]) / 2.0, 0, 1)
        review_logs = np.array(
            [math.log1p(int(nodes.iloc[pos]["unique_review_count"])) for pos in eligible]
        )
        frequency = review_logs / review_logs.max()
        alpha = float(config["centrality_weight_alpha"])
        combined = alpha * centrality + (1.0 - alpha) * frequency
        scores = {
            pos: (float(centrality[i]), float(frequency[i]), float(combined[i]))
            for i, pos in enumerate(eligible)
        }

    canonical_position: int | None = None
    if cluster_maximum > naming_max_distance + 1e-12:
        naming_status = "cohesion_review_required"
    elif not eligible:
        naming_status = "no_role_eligible_candidate"
    else:
        ranked = sorted(
            eligible,
            key=lambda pos: (
                -scores[pos][2],
                average[pos],
                -int(nodes.iloc[pos]["unique_review_count"]),
                expressions[pos],
            ),
        )
        first = ranked[0]
        margin = scores[first][2] - scores[ranked[1]][2] if len(ranked) > 1 else math.inf
        if int(nodes.iloc[first]["unique_review_count"]) < int(
            config["min_canonical_review_count"]
        ):
            naming_status = "insufficient_review_support"
        elif margin < float(config["min_score_margin"]):
            naming_status = "ambiguous_score"
        else:
            canonical_position = first
            naming_status = "selected"

    details = []
    for pos, expression in enumerate(expressions):
        score = scores.get(pos)
        details.append(
            {
                "expression": expression,
                "average_distance": round(float(average[pos]), 8),
                "max_distance": round(float(maximum[pos]), 8),
                "unique_review_count": int(nodes.iloc[pos]["unique_review_count"]),
                "central_candidate": pos in central,
                "role_filter_passed": _role_allowed(expression, role, config, forbidden),
                "centrality": round(score[0], 8) if score else None,
                "frequency_score": round(score[1], 8) if score else None,
                "combined_score": round(score[2], 8) if score else None,
            }
        )
    return {
        "canonical_label": (
            expressions[canonical_position] if canonical_position is not None else None
        ),
        "canonical_position": canonical_position,
        "medoid_label": expressions[medoid_position],
        "naming_status": naming_status,
        "cluster_max_distance": cluster_maximum,
        "member_details": details,
    }


def _explicit_negation_core(text: str, config: Mapping[str, Any]) -> tuple[bool, str]:
    normalized = " ".join(text.split())
    for suffix in sorted(map(str, config["suffixes"]), key=len, reverse=True):
        if normalized.endswith(suffix):
            core = normalized[: -len(suffix)].strip()
            if core:
                return True, core
    tokens = normalized.split()
    negative_tokens = set(map(str, config["whole_word_tokens"]))
    for index, token in enumerate(tokens):
        if token in negative_tokens:
            core = tokens[:index] + tokens[index + 1 :]
            if core:
                return True, " ".join(core)
    return False, normalized


def _status_distances(
    texts: Sequence[str], embeddings: np.ndarray, config: Mapping[str, Any]
) -> tuple[np.ndarray, int]:
    distances = np.clip(cosine_distances(embeddings), 0.0, 2.0)
    constrained: set[tuple[int, int]] = set()
    for group in config.get("status_opposition_groups", []):
        side_a = set(map(str, group["side_a"]))
        side_b = set(map(str, group["side_b"]))
        for left, left_text in enumerate(texts):
            for right in range(left + 1, len(texts)):
                right_text = texts[right]
                if (left_text in side_a and right_text in side_b) or (
                    left_text in side_b and right_text in side_a
                ):
                    constrained.add((left, right))

    explicit = config["status_explicit_negation"]
    similarity = float(explicit["core_similarity_threshold"])
    normalized = [" ".join(str(text).split()) for text in texts]
    for left, left_text in enumerate(normalized):
        for right in range(left + 1, len(normalized)):
            right_text = normalized[right]
            suffix_opposites = False
            for pair in config.get("status_opposition_suffix_pairs", []):
                side_a = str(pair["side_a_suffix"])
                side_b = str(pair["side_b_suffix"])
                if left_text.endswith(side_a) and right_text.endswith(side_b):
                    left_core = left_text[: -len(side_a)].strip()
                    right_core = right_text[: -len(side_b)].strip()
                elif left_text.endswith(side_b) and right_text.endswith(side_a):
                    left_core = left_text[: -len(side_b)].strip()
                    right_core = right_text[: -len(side_a)].strip()
                else:
                    continue
                if (
                    left_core
                    and right_core
                    and SequenceMatcher(None, left_core, right_core).ratio() >= similarity
                ):
                    suffix_opposites = True
                    break
            explicit_opposites = False
            if bool(explicit["enabled"]):
                left_negative, left_core = _explicit_negation_core(left_text, explicit)
                right_negative, right_core = _explicit_negation_core(right_text, explicit)
                if left_negative != right_negative:
                    negative_core = left_core if left_negative else right_core
                    positive_text = right_text if left_negative else left_text
                    explicit_opposites = (
                        SequenceMatcher(None, negative_core, positive_text).ratio() >= similarity
                    )
            if suffix_opposites or explicit_opposites:
                constrained.add((left, right))

    for left, right in constrained:
        distances[left, right] = 2.0
        distances[right, left] = 2.0
    np.fill_diagonal(distances, 0.0)
    return distances, len(constrained)


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0.0 else vector / norm


def _cluster_nodes(
    nodes: pd.DataFrame,
    *,
    text_column: str,
    embedder: Embedder,
    stage_name: str,
    prefix: str,
    role: str,
    stage_config: Mapping[str, Any],
    clustering_config: Mapping[str, Any],
    canonical_config: Mapping[str, Any],
    boundary_columns: Sequence[str] = (),
    forbidden_by_boundary: Mapping[tuple[Any, ...], set[str]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if nodes.empty:
        raise ValueError(f"no nodes available for {stage_name}")
    result = nodes.copy().reset_index(drop=True)
    vectors = embedder.encode(result[text_column].astype(str).tolist())
    result["embedding"] = [vector.astype(np.float32) for vector in vectors]
    for column in ("cluster_id", "canonical_label", "naming_status"):
        result[column] = pd.Series(index=result.index, dtype="object")
    result["mapping_applied"] = False
    result["mapping_distance"] = np.nan
    threshold = float(stage_config["distance_threshold"])
    naming_maximum = float(stage_config["naming_max_distance"])
    tolerance = float(clustering_config["distance_tolerance"])
    cluster_rows: list[dict[str, Any]] = []
    counter = 0

    if boundary_columns:
        key: str | list[str] = (
            boundary_columns[0] if len(boundary_columns) == 1 else list(boundary_columns)
        )
        groups = result.groupby(key, sort=True, observed=True, dropna=False)
    else:
        groups = [((), result)]

    for boundary_value, group in groups:
        boundary = boundary_value if isinstance(boundary_value, tuple) else (boundary_value,)
        if not boundary_columns:
            boundary = ()
        group_indices = group.index.to_list()
        matrix = np.stack(group["embedding"].to_list())
        constraint_count = 0
        if len(group) == 1:
            raw_labels = np.array([0])
        else:
            if role == "status":
                fit_matrix, constraint_count = _status_distances(
                    group[text_column].astype(str).tolist(), matrix, clustering_config
                )
                metric = "precomputed"
            else:
                fit_matrix = matrix
                metric = str(clustering_config["metric"])
            raw_labels = AgglomerativeClustering(
                n_clusters=None,
                metric=metric,
                linkage=str(clustering_config["linkage"]),
                distance_threshold=threshold,
                compute_full_tree=True,
                compute_distances=True,
            ).fit_predict(fit_matrix)

        ordered_clusters = []
        for raw_label in sorted(set(raw_labels.tolist())):
            positions = np.flatnonzero(raw_labels == raw_label).tolist()
            minimum = min(str(group.iloc[pos][text_column]) for pos in positions)
            ordered_clusters.append((minimum, positions))
        forbidden = forbidden_by_boundary.get(boundary, set()) if forbidden_by_boundary else set()
        for _, positions in sorted(ordered_clusters):
            counter += 1
            cluster_id = f"{prefix}-{counter:06d}"
            member_indices = [group_indices[pos] for pos in positions]
            members = result.loc[member_indices].reset_index(drop=True)
            member_matrix = np.stack(members["embedding"].to_list())
            member_distances = np.clip(cosine_distances(member_matrix), 0.0, 2.0)
            label = _select_label(
                members,
                member_distances,
                text_column,
                role,
                canonical_config,
                naming_maximum,
                forbidden,
            )
            if label["canonical_label"] is None:
                raise ValueError(
                    f"{stage_name} cluster {cluster_id} needs a canonical label before publication"
                )
            if float(label["cluster_max_distance"]) > threshold + tolerance:
                raise AssertionError(f"complete-linkage threshold violated by {cluster_id}")
            centroid = _normalize(member_matrix.mean(axis=0)).astype(np.float32)
            canonical_position = int(label["canonical_position"])
            canonical_vector = member_matrix[canonical_position].astype(np.float32)
            canonical_to_centroid = float(
                cosine_distances(canonical_vector[None, :], centroid[None, :])[0, 0]
            )
            representative = member_distances[canonical_position]
            representative_average = (
                float(representative.sum() / (len(members) - 1)) if len(members) > 1 else 0.0
            )
            representative_maximum = float(representative.max()) if len(members) > 1 else 0.0
            for member_position, node_index in enumerate(member_indices):
                raw = str(result.at[node_index, text_column])
                canonical = str(label["canonical_label"])
                result.at[node_index, "cluster_id"] = cluster_id
                result.at[node_index, "canonical_label"] = canonical
                result.at[node_index, "naming_status"] = label["naming_status"]
                result.at[node_index, "mapping_applied"] = raw != canonical
                result.at[node_index, "mapping_distance"] = float(
                    member_distances[member_position, canonical_position]
                )
            review_ids = set().union(*members["_review_ids"].tolist())
            cluster_row = {
                "stage": stage_name,
                "cluster_id": cluster_id,
                "canonical_label": str(label["canonical_label"]),
                "medoid_label": str(label["medoid_label"]),
                "naming_status": str(label["naming_status"]),
                "member_count": len(members),
                "source_row_count": int(members["source_row_count"].sum()),
                "unique_review_count": len(review_ids),
                "distance_threshold": threshold,
                "naming_max_distance": naming_maximum,
                "status_cannot_link_pair_count_in_boundary": constraint_count,
                "cluster_max_distance": float(label["cluster_max_distance"]),
                "representative_average_distance": representative_average,
                "representative_max_distance": representative_maximum,
                "representative_centroid_distance": canonical_to_centroid,
                "member_expressions": members[text_column].astype(str).tolist(),
                "member_details_json": json.dumps(
                    label["member_details"], ensure_ascii=False, separators=(",", ":")
                ),
                "centroid_embedding": centroid.tolist(),
                "canonical_embedding": canonical_vector.tolist(),
            }
            cluster_row.update(dict(zip(boundary_columns, boundary, strict=True)))
            cluster_rows.append(cluster_row)

    result["embedding"] = result["embedding"].map(lambda vector: vector.tolist())
    return result, pd.DataFrame(cluster_rows)


def build_reclustered_taxonomy(
    opinion_units: pd.DataFrame,
    *,
    embedder: Embedder,
    config: Mapping[str, Any],
) -> ReclusteredTaxonomy:
    required = {"opinion_unit_id", "review_id", "raw_aspect", "raw_status"}
    missing = required - set(opinion_units.columns)
    if missing:
        raise ValueError(f"reclustering input columns missing: {sorted(missing)}")
    if opinion_units["opinion_unit_id"].duplicated().any():
        raise ValueError("opinion_unit_id must be unique in the captured snapshot")
    excluded = set(map(str, config["filters"]["excluded_raw_aspects"]))
    eligible = opinion_units.loc[~opinion_units["raw_aspect"].isin(excluded)].copy()
    if eligible.empty:
        raise ValueError("no taxonomy-eligible opinion units remain")
    clustering = config["clustering"]
    canonical = config["canonical_label"]
    aspect_nodes, aspect_clusters = _cluster_nodes(
        _build_nodes(eligible, "raw_aspect"),
        text_column="raw_aspect",
        embedder=embedder,
        stage_name="experiment_d_aspect",
        prefix="D-A",
        role="aspect",
        stage_config=clustering["experiment_d"]["aspect"],
        clustering_config=clustering,
        canonical_config=canonical,
    )
    assigned = eligible.merge(
        aspect_nodes[["raw_aspect", "cluster_id", "canonical_label", "mapping_distance"]].rename(
            columns={
                "cluster_id": "aspect_cluster_id",
                "canonical_label": "aspect",
                "mapping_distance": "aspect_mapping_distance",
            }
        ),
        on="raw_aspect",
        how="left",
        validate="many_to_one",
    )
    forbidden = {
        (str(cluster_id),): set(group["raw_aspect"].astype(str))
        | set(group["canonical_label"].astype(str))
        for cluster_id, group in aspect_nodes.groupby("cluster_id", sort=True, observed=True)
    }
    status_input = assigned.loc[assigned["raw_status"].notna()].copy()
    if status_input.empty:
        raise ValueError("no non-null statuses remain for reclustering")
    status_nodes, status_clusters = _cluster_nodes(
        _build_nodes(status_input, "raw_status", ["aspect_cluster_id"]),
        text_column="raw_status",
        embedder=embedder,
        stage_name="experiment_d_status",
        prefix="D-S",
        role="status",
        stage_config=clustering["experiment_d"]["status"],
        clustering_config=clustering,
        canonical_config=canonical,
        boundary_columns=["aspect_cluster_id"],
        forbidden_by_boundary=forbidden,
    )
    assigned = assigned.merge(
        status_nodes[
            [
                "aspect_cluster_id",
                "raw_status",
                "cluster_id",
                "canonical_label",
                "mapping_distance",
            ]
        ].rename(
            columns={
                "cluster_id": "status_cluster_id",
                "canonical_label": "status",
                "mapping_distance": "status_mapping_distance",
            }
        ),
        on=["aspect_cluster_id", "raw_status"],
        how="left",
        validate="many_to_one",
    )
    assignments = opinion_units[["opinion_unit_id", "raw_aspect"]].copy()
    assignments = assignments.merge(
        assigned[
            [
                "opinion_unit_id",
                "aspect_cluster_id",
                "aspect",
                "status_cluster_id",
                "status",
                "aspect_mapping_distance",
                "status_mapping_distance",
            ]
        ],
        on="opinion_unit_id",
        how="left",
        validate="one_to_one",
    )
    assignments["mapping_state"] = np.where(
        assignments["raw_aspect"].isin(excluded), "excluded_taxonomy", "mapped_exact"
    )
    assignments = assignments.drop(columns=["raw_aspect"])
    return ReclusteredTaxonomy(
        assignments=assignments,
        aspect_nodes=aspect_nodes.drop(columns=["_review_ids"]),
        aspect_clusters=aspect_clusters,
        status_nodes=status_nodes.drop(columns=["_review_ids"]),
        status_clusters=status_clusters,
    )
