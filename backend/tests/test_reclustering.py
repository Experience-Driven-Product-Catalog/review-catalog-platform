from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

from review_catalog.normalization.reclustering import (
    ReclusteredTaxonomy,
    build_reclustered_taxonomy,
)
from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.pipeline.artifacts import atomic_write_json, sha256_file
from review_catalog.pipeline.catalog_store import SCHEMA_SQL, commit_taxonomy_rebuild


class FakeEmbedder:
    model_id = "fake/model"

    vectors = {
        "화면": [1.0, 0.0, 0.0],
        "디스플레이": [0.995, 0.1, 0.0],
        "밝음": [0.0, 1.0, 0.0],
        "어두움": [0.0, 0.995, 0.1],
    }

    def encode(self, texts):
        vectors = np.asarray([self.vectors[str(text)] for text in texts], dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def _input() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "opinion_unit_id": "ou-1",
                "review_id": "review-1",
                "raw_aspect": "화면",
                "raw_status": "밝음",
            },
            {
                "opinion_unit_id": "ou-2",
                "review_id": "review-2",
                "raw_aspect": "디스플레이",
                "raw_status": "어두움",
            },
            {
                "opinion_unit_id": "ou-3",
                "review_id": "review-3",
                "raw_aspect": "전반적 상품 경험",
                "raw_status": None,
            },
        ]
    )


def _config(project_root: Path) -> dict:
    return yaml.safe_load(
        (project_root / "config/normalization/20260803-213339.yaml").read_text(encoding="utf-8")
    )


def test_full_reclustering_reuses_existing_thresholds_and_constraints(project_root) -> None:
    result = build_reclustered_taxonomy(
        _input(), embedder=FakeEmbedder(), config=_config(project_root)
    )

    assert len(result.aspect_clusters) == 1
    assert set(result.aspect_clusters["distance_threshold"]) == {0.3591}
    assert len(result.status_clusters) == 2
    assert set(result.status_clusters["distance_threshold"]) == {0.2}
    assert result.status_clusters["status_cannot_link_pair_count_in_boundary"].min() == 1
    states = result.assignments.set_index("opinion_unit_id")["mapping_state"].to_dict()
    assert states == {
        "ou-1": "mapped_exact",
        "ou-2": "mapped_exact",
        "ou-3": "excluded_taxonomy",
    }


def _write_bundle(root: Path, taxonomy: ReclusteredTaxonomy, *, mapping_version_id: str) -> Path:
    frames = {
        "experiment_d.parquet": taxonomy.assignments,
        "experiment_d_aspect_nodes.parquet": taxonomy.aspect_nodes,
        "experiment_d_aspect_clusters.parquet": taxonomy.aspect_clusters,
        "experiment_d_status_nodes.parquet": taxonomy.status_nodes,
        "experiment_d_status_clusters.parquet": taxonomy.status_clusters,
    }
    for name, frame in frames.items():
        frame.to_parquet(root / name, index=False, engine="pyarrow")
    atomic_write_json(root / "embedding_model_manifest.json", {"model_id": "fake/model"})
    artifact_names = [*frames, "embedding_model_manifest.json"]
    manifest = {
        "schema_version": "1.0.0",
        "mapping_table_version_id": mapping_version_id,
        "normalization_version": "2026-08-03",
        "normalization_run_id": "recluster-test",
        "normalization_config_sha256": "c" * 64,
        "embedding_model_id": "fake/model",
        "embedding_model_artifact_sha256": "e" * 64,
        "source_prompt_version_id": "prompt-test",
        "source_prompt_sha256": "p" * 64,
        "source_extraction_model_version_id": "extract-test",
        "source_extraction_backend": "test",
        "source_extraction_model": "test",
        "source_extraction_reasoning_effort": "none",
        "metric": "cosine",
        "linkage": "complete",
        "aspect_distance_threshold": 0.3591,
        "status_distance_threshold": 0.2,
        "counts": {
            "source_reviews": 2,
            "source_opinion_units": 2,
            "taxonomy_eligible_opinion_units": 2,
            "excluded_opinion_units": 0,
            "aspect_mapping_expressions": len(taxonomy.aspect_nodes),
            "aspect_clusters": len(taxonomy.aspect_clusters),
            "aspect_status_mapping_expressions": len(taxonomy.status_nodes),
            "status_clusters": len(taxonomy.status_clusters),
        },
        "artifacts": {name: sha256_file(root / name) for name in sorted(artifact_names)},
    }
    path = root / "taxonomy_manifest.json"
    atomic_write_json(path, manifest)
    return path


def _insert_unit(
    connection: duckdb.DuckDBPyConnection, unit_id: str, aspect: str, status: str
) -> None:
    connection.execute(
        """
        INSERT INTO opinion_units (
          opinion_unit_id, review_id, unit_position, raw_aspect, raw_status,
          excerpt, opinion, sentiment, mapping_state, aspect_id, aspect,
          status_id, status, prompt_version_id, model_version_id,
          mapping_table_version_id, embedding_model_version_id,
          normalization_run_id, normalization_config_sha256,
          extraction_response_sha256, ingestion_run_id, created_at
        ) VALUES (?, ?, 1, ?, ?, ?, ?, 'neutral', 'mapped_exact',
                  'old-aspect', ?, 'old-status', ?, 'prompt', 'model',
                  'mapping-table-old', 'embedding-model-old', 'old-run', ?, ?, 'ingest', ?)
        """,
        [
            unit_id,
            f"review-{unit_id}",
            aspect,
            status,
            aspect,
            f"{aspect} {status}",
            aspect,
            status,
            "c" * 64,
            "r" * 64,
            datetime.now(UTC),
        ],
    )


def test_commit_keeps_rows_arriving_after_capture_on_previous_version(
    tmp_path, project_root
) -> None:
    captured = _input().iloc[:2].copy()
    taxonomy = build_reclustered_taxonomy(
        captured, embedder=FakeEmbedder(), config=_config(project_root)
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    taxonomy_manifest_path = _write_bundle(bundle, taxonomy, mapping_version_id="mapping-table-new")
    previous = tmp_path / "previous.duckdb"
    with duckdb.connect(str(previous)) as connection:
        connection.execute(SCHEMA_SQL)
        _insert_unit(connection, "ou-1", "화면", "밝음")
        _insert_unit(connection, "ou-2", "디스플레이", "어두움")
        _insert_unit(connection, "ou-late", "화면", "밝음")
    destination = tmp_path / "rebuilt.duckdb"

    counts = commit_taxonomy_rebuild(
        destination=destination,
        previous_snapshot=previous,
        artifact_root=bundle,
        taxonomy_manifest=load_taxonomy_manifest(taxonomy_manifest_path),
        embedding_model_version_id="embedding-model-new",
        recluster_run_id="recluster-test",
        captured_snapshot_sha256="s" * 64,
        writer_lock_path=tmp_path / "writer.lock",
        writer_identity="airflow",
    )

    with duckdb.connect(str(destination), read_only=True) as connection:
        versions = dict(
            connection.execute(
                "SELECT opinion_unit_id, mapping_table_version_id FROM opinion_units"
            ).fetchall()
        )
        audit = connection.execute(
            """
            SELECT captured_opinion_unit_count, carried_forward_opinion_unit_count
            FROM taxonomy_rebuilds WHERE recluster_run_id = 'recluster-test'
            """
        ).fetchone()
    assert versions["ou-1"] == "mapping-table-new"
    assert versions["ou-2"] == "mapping-table-new"
    assert versions["ou-late"] == "mapping-table-old"
    assert audit == (2, 1)
    assert counts["carried_forward_opinion_unit_count"] == 1
