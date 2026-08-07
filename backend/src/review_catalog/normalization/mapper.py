from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from review_catalog.extraction.contracts import OpinionUnit
from review_catalog.normalization.embedding import Embedder
from review_catalog.normalization.taxonomy import TaxonomyManifest


@dataclass(frozen=True)
class MappingDecision:
    mapping_state: str
    aspect_id: str | None
    aspect: str | None
    status_id: str | None
    status: str | None
    suggested_aspect_id: str | None
    suggested_aspect: str | None
    aspect_distance: float | None
    aspect_membership_max_distance: float | None
    aspect_centroid_distance: float | None
    aspect_second_nearest_distance: float | None
    aspect_distance_margin: float | None
    aspect_candidate_eligible: bool | None
    suggested_status_id: str | None
    suggested_status: str | None
    status_distance: float | None
    status_membership_max_distance: float | None
    status_centroid_distance: float | None
    status_second_nearest_distance: float | None
    status_distance_margin: float | None
    status_candidate_eligible: bool | None


@dataclass(frozen=True)
class _ExactMapping:
    cluster_id: str
    label: str
    mapping_distance: float


@dataclass(frozen=True)
class _Cluster:
    cluster_id: str
    label: str
    threshold: float
    centroid: np.ndarray
    canonical: np.ndarray
    member_texts: tuple[str, ...]
    member_embeddings: np.ndarray


@dataclass(frozen=True)
class _Candidate:
    cluster: _Cluster
    canonical_distance: float
    max_member_distance: float
    centroid_distance: float
    eligible: bool


def _distance(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.clip(1.0 - float(np.dot(left, right)), 0.0, 2.0))


def _distances(vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return np.clip(1.0 - matrix @ vector, 0.0, 2.0)


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
            core_tokens = tokens[:index] + tokens[index + 1 :]
            if core_tokens:
                return True, " ".join(core_tokens)
    return False, normalized


def _status_pair_is_constrained(
    left: str,
    right: str,
    clustering_config: Mapping[str, Any],
) -> bool:
    for group in clustering_config.get("status_opposition_groups", []):
        side_a = set(map(str, group["side_a"]))
        side_b = set(map(str, group["side_b"]))
        if (left in side_a and right in side_b) or (left in side_b and right in side_a):
            return True

    left_text = " ".join(left.split())
    right_text = " ".join(right.split())
    explicit = clustering_config["status_explicit_negation"]
    similarity_threshold = float(explicit["core_similarity_threshold"])
    for pair in clustering_config.get("status_opposition_suffix_pairs", []):
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
            and SequenceMatcher(None, left_core, right_core).ratio() >= similarity_threshold
        ):
            return True

    if bool(explicit["enabled"]):
        left_negative, left_core = _explicit_negation_core(left_text, explicit)
        right_negative, right_core = _explicit_negation_core(right_text, explicit)
        if left_negative != right_negative:
            negative_core = left_core if left_negative else right_core
            positive_text = right_text if left_negative else left_text
            if SequenceMatcher(None, negative_core, positive_text).ratio() >= similarity_threshold:
                return True
    return False


def _rounded(value: float | None) -> float | None:
    return round(value, 8) if value is not None else None


def _rank_candidates(
    vector: np.ndarray,
    clusters: Sequence[_Cluster],
    *,
    raw_text: str,
    status_constraints: Mapping[str, Any] | None = None,
) -> list[_Candidate]:
    ranked: list[_Candidate] = []
    for cluster in clusters:
        member_distances = _distances(vector, cluster.member_embeddings)
        if status_constraints is not None:
            for index, member_text in enumerate(cluster.member_texts):
                if _status_pair_is_constrained(raw_text, member_text, status_constraints):
                    member_distances[index] = 2.0
        maximum = float(member_distances.max())
        ranked.append(
            _Candidate(
                cluster=cluster,
                canonical_distance=_distance(vector, cluster.canonical),
                max_member_distance=maximum,
                centroid_distance=_distance(vector, cluster.centroid),
                eligible=maximum <= cluster.threshold + 1e-6,
            )
        )
    return sorted(
        ranked,
        key=lambda item: (
            not item.eligible,
            item.max_member_distance,
            item.centroid_distance,
            item.canonical_distance,
            item.cluster.cluster_id,
        ),
    )


class TaxonomyMapper:
    """Exact historical mappings plus non-mutating complete-linkage insertion inference.

    This is not a taxonomy rebuild. An unseen expression is never written into the
    mapping table and never becomes a canonical label during ingestion.
    """

    def __init__(
        self,
        *,
        snapshot_path: Path,
        mapping_table_version_id: str,
        manifest: TaxonomyManifest,
        embedder: Embedder,
        clustering_config: Mapping[str, Any],
    ) -> None:
        self.manifest = manifest
        self.embedder = embedder
        self.clustering_config = clustering_config
        connection = duckdb.connect(str(snapshot_path), read_only=True)
        try:
            aspect_rows = connection.execute(
                """
                SELECT raw_aspect, aspect_cluster_id, canonical_label, mapping_distance
                FROM aspect_mapping_table WHERE mapping_table_version_id = ?
                """,
                [mapping_table_version_id],
            ).fetchall()
            status_rows = connection.execute(
                """
                SELECT aspect_cluster_id, raw_status, status_cluster_id,
                       canonical_label, mapping_distance
                FROM status_mapping_table WHERE mapping_table_version_id = ?
                """,
                [mapping_table_version_id],
            ).fetchall()
            self.aspect_exact = {
                str(raw): _ExactMapping(str(cluster_id), str(label), float(distance))
                for raw, cluster_id, label, distance in aspect_rows
            }
            self.status_exact = {
                (str(aspect_id), str(raw)): _ExactMapping(
                    str(cluster_id), str(label), float(distance)
                )
                for aspect_id, raw, cluster_id, label, distance in status_rows
            }
            self.aspect_clusters = self._load_clusters(
                connection,
                "aspect_clusters",
                "aspect_mapping_table",
                "aspect_cluster_id",
                "raw_aspect",
                mapping_table_version_id,
            )
            status_clusters = self._load_clusters(
                connection,
                "status_clusters",
                "status_mapping_table",
                "status_cluster_id",
                "raw_status",
                mapping_table_version_id,
                boundary_column="aspect_cluster_id",
            )
            self.status_clusters_by_aspect: dict[str, list[_Cluster]] = {}
            for boundary, cluster in status_clusters:
                self.status_clusters_by_aspect.setdefault(boundary, []).append(cluster)
        finally:
            connection.close()
        if not self.aspect_exact or not self.aspect_clusters:
            raise ValueError("active snapshot does not contain the requested mapping table")

    @staticmethod
    def _load_clusters(
        connection: duckdb.DuckDBPyConnection,
        cluster_table: str,
        node_table: str,
        cluster_id_column: str,
        raw_column: str,
        version_id: str,
        *,
        boundary_column: str | None = None,
    ) -> list[_Cluster] | list[tuple[str, _Cluster]]:
        boundary_select = f", c.{boundary_column}" if boundary_column else ""
        rows = connection.execute(
            f"""
            SELECT c.{cluster_id_column}, c.canonical_label, c.distance_threshold,
                   c.centroid_embedding, c.canonical_embedding{boundary_select},
                   list(n.{raw_column} ORDER BY n.{raw_column}),
                   list(n.embedding ORDER BY n.{raw_column})
            FROM {cluster_table} c
            JOIN {node_table} n
              ON c.mapping_table_version_id = n.mapping_table_version_id
             AND c.{cluster_id_column} = n.{cluster_id_column}
            WHERE c.mapping_table_version_id = ?
            GROUP BY ALL
            ORDER BY c.{cluster_id_column}
            """,
            [version_id],
        ).fetchall()
        outputs: list[Any] = []
        for row in rows:
            cluster_id, label, threshold, centroid, canonical = row[:5]
            offset = 5
            boundary = None
            if boundary_column:
                boundary = str(row[offset])
                offset += 1
            texts, embeddings = row[offset : offset + 2]
            cluster = _Cluster(
                cluster_id=str(cluster_id),
                label=str(label),
                threshold=float(threshold),
                centroid=np.asarray(centroid, dtype=np.float32),
                canonical=np.asarray(canonical, dtype=np.float32),
                member_texts=tuple(map(str, texts)),
                member_embeddings=np.asarray(embeddings, dtype=np.float32),
            )
            outputs.append((boundary, cluster) if boundary_column else cluster)
        return outputs

    @staticmethod
    def _candidate_fields(
        ranked: Sequence[_Candidate], prefix: str
    ) -> dict[str, str | float | bool | None]:
        if not ranked:
            return {
                f"suggested_{prefix}_id": None,
                f"suggested_{prefix}": None,
                f"{prefix}_distance": None,
                f"{prefix}_membership_max_distance": None,
                f"{prefix}_centroid_distance": None,
                f"{prefix}_second_nearest_distance": None,
                f"{prefix}_distance_margin": None,
                f"{prefix}_candidate_eligible": None,
            }
        first = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        second_distance = second.max_member_distance if second else None
        return {
            f"suggested_{prefix}_id": first.cluster.cluster_id,
            f"suggested_{prefix}": first.cluster.label,
            f"{prefix}_distance": _rounded(first.canonical_distance),
            f"{prefix}_membership_max_distance": _rounded(first.max_member_distance),
            f"{prefix}_centroid_distance": _rounded(first.centroid_distance),
            f"{prefix}_second_nearest_distance": _rounded(second_distance),
            f"{prefix}_distance_margin": _rounded(
                second_distance - first.max_member_distance if second_distance is not None else None
            ),
            f"{prefix}_candidate_eligible": first.eligible,
        }

    def map(self, unit: OpinionUnit) -> MappingDecision:
        empty_aspect_candidate = self._candidate_fields([], "aspect")
        empty_status_candidate = self._candidate_fields([], "status")
        exact_aspect = self.aspect_exact.get(unit.raw_aspect)
        if exact_aspect is None:
            aspect_vector = self.embedder.encode([unit.raw_aspect])[0]
            aspect_ranked = _rank_candidates(
                aspect_vector, self.aspect_clusters, raw_text=unit.raw_aspect
            )
            aspect_candidate = self._candidate_fields(aspect_ranked, "aspect")
            suggested_aspect_id = aspect_candidate["suggested_aspect_id"]
            status_candidate = empty_status_candidate
            if unit.raw_status is not None and suggested_aspect_id is not None:
                status_clusters = self.status_clusters_by_aspect.get(str(suggested_aspect_id), [])
                status_vector = self.embedder.encode([unit.raw_status])[0]
                status_candidate = self._candidate_fields(
                    _rank_candidates(
                        status_vector,
                        status_clusters,
                        raw_text=unit.raw_status,
                        status_constraints=self.clustering_config,
                    ),
                    "status",
                )
            return MappingDecision(
                mapping_state="candidate",
                aspect_id=None,
                aspect=None,
                status_id=None,
                status=None,
                **aspect_candidate,
                **status_candidate,
            )

        exact_aspect_fields = {
            **empty_aspect_candidate,
            "aspect_distance": _rounded(exact_aspect.mapping_distance),
        }
        if unit.raw_status is None:
            return MappingDecision(
                mapping_state="mapped_exact",
                aspect_id=exact_aspect.cluster_id,
                aspect=exact_aspect.label,
                status_id=None,
                status=None,
                **exact_aspect_fields,
                **empty_status_candidate,
            )

        exact_status = self.status_exact.get((exact_aspect.cluster_id, unit.raw_status))
        if exact_status is not None:
            exact_status_fields = {
                **empty_status_candidate,
                "status_distance": _rounded(exact_status.mapping_distance),
            }
            return MappingDecision(
                mapping_state="mapped_exact",
                aspect_id=exact_aspect.cluster_id,
                aspect=exact_aspect.label,
                status_id=exact_status.cluster_id,
                status=exact_status.label,
                **exact_aspect_fields,
                **exact_status_fields,
            )

        status_vector = self.embedder.encode([unit.raw_status])[0]
        status_candidate = self._candidate_fields(
            _rank_candidates(
                status_vector,
                self.status_clusters_by_aspect.get(exact_aspect.cluster_id, []),
                raw_text=unit.raw_status,
                status_constraints=self.clustering_config,
            ),
            "status",
        )
        return MappingDecision(
            mapping_state="candidate",
            aspect_id=exact_aspect.cluster_id,
            aspect=exact_aspect.label,
            status_id=None,
            status=None,
            **exact_aspect_fields,
            **status_candidate,
        )
