from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import numpy as np

from review_catalog.extraction.contracts import ExtractionResult, OpinionUnit, ReviewInput
from review_catalog.normalization.mapper import TaxonomyMapper, _status_pair_is_constrained
from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.pipeline.catalog_store import commit_catalog_delta


class SelectedVectorEmbedder:
    model_id = "test-selected-historical-vector"

    def __init__(self, vector: list[float]) -> None:
        self.vector = np.asarray(vector, dtype=np.float32)

    def encode(self, texts):
        return np.stack([self.vector for _ in texts])


def review(text: str) -> ReviewInput:
    return ReviewInput(
        review_id="review-1",
        product_id="product-1",
        product_name="상품",
        product_category="모니터",
        review=text,
        source="test",
    )


def _migrated_snapshot(tmp_path: Path, project_root: Path) -> tuple[Path, object]:
    manifest = load_taxonomy_manifest(project_root / "config/taxonomy/20260803-213339.json")
    versions = {
        "opinion_unit_prompt": {"id": manifest.source_prompt_version_id},
        "extraction_model": {"id": "runtime-unused"},
        "mapping_table": {"id": manifest.mapping_table_version_id},
        "embedding_model": {
            "id": f"embedding-model-{manifest.embedding_model_artifact_sha256[:16]}"
        },
    }
    delta = {
        "run_id": "test-migration",
        "versions": versions,
        "products": [],
        "reviews": [],
        "opinion_units": [],
        "legacy_migration": {"migration_id": "legacy-monitor-20260803-213339"},
        "delta_sha256": "0" * 64,
    }
    snapshot = tmp_path / "catalog.duckdb"
    commit_catalog_delta(
        destination=snapshot,
        previous_snapshot=None,
        delta=delta,
        taxonomy_manifest=manifest,
        legacy_migration_root=project_root / "migration/source",
        writer_lock_path=tmp_path / "writer.lock",
        writer_identity="airflow",
    )
    return snapshot, manifest


def test_prompt_is_byte_identical_to_extract_attribute(project_root) -> None:
    prompt = project_root / "backend/src/review_catalog/extraction/opinion_units_prompt.md"
    assert hashlib.sha256(prompt.read_bytes()).hexdigest() == (
        "bdc8ec050cc500c2fef533e503e0a27f49028e490bc3f1deaf9503c43efd7adb"
    )


def test_grounding_contract_rejects_non_contiguous_excerpt() -> None:
    source = review("화질이 선명합니다.")
    unit = OpinionUnit(
        raw_aspect="화질",
        raw_status="선명함",
        excerpt="화질이 매우 선명합니다.",
        opinion="화질이 선명함",
        sentiment="positive",
    )
    try:
        ExtractionResult(
            review=source,
            opinion_units=[unit],
            prompt_version_id="prompt-v1",
            model_version_id="model-v1",
            raw_response_sha256="a" * 64,
        )
    except ValueError as exc:
        assert "contiguous source review text" in str(exc)
    else:
        raise AssertionError("ungrounded excerpt was accepted")


def test_real_mapping_table_exact_lookup_and_unseen_candidate(tmp_path, project_root) -> None:
    snapshot, manifest = _migrated_snapshot(tmp_path, project_root)
    with duckdb.connect(str(snapshot), read_only=True) as connection:
        assert {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "products",
                "reviews",
                "opinion_units",
                "representative_attributes",
                "aspect_mapping_table",
                "aspect_clusters",
                "status_mapping_table",
                "status_clusters",
            )
        } == {
            "products": 9,
            "reviews": 749,
            "opinion_units": 2583,
            "representative_attributes": 2352,
            "aspect_mapping_table": 870,
            "aspect_clusters": 408,
            "status_mapping_table": 1457,
            "status_clusters": 1320,
        }
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM reviews r
                JOIN read_parquet(?) p ON r.external_review_idx = p.idx
                JOIN products product USING (product_id)
                WHERE r.review_text IS DISTINCT FROM p.review
                   OR product.product_name IS DISTINCT FROM p.productName
                """,
                [str(project_root / "migration/source/legacy_monitor/monitor_reviews.parquet")],
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """
                SELECT count(*)
                FROM opinion_units o
                JOIN read_parquet(?) p ON o.source_opinion_unit_idx = p.idx
                WHERE o.raw_aspect IS DISTINCT FROM p.raw_aspect
                   OR o.raw_status IS DISTINCT FROM p.raw_status
                   OR o.excerpt IS DISTINCT FROM p.excerpt
                   OR o.opinion IS DISTINCT FROM p.opinion
                   OR o.sentiment IS DISTINCT FROM p.sentiment
                """,
                [
                    str(
                        project_root
                        / "migration/source/legacy_monitor/monitor_opinion_units.parquet"
                    )
                ],
            ).fetchone()[0]
            == 0
        )
        vector = connection.execute(
            """
            SELECT embedding FROM aspect_mapping_table
            WHERE mapping_table_version_id = ? AND raw_aspect = '화질'
            """,
            [manifest.mapping_table_version_id],
        ).fetchone()[0]
    config = {
        "status_opposition_groups": [],
        "status_opposition_suffix_pairs": [],
        "status_explicit_negation": {
            "enabled": True,
            "suffixes": ["지 않음", "아님"],
            "whole_word_tokens": ["안", "못"],
            "core_similarity_threshold": 0.65,
        },
    }
    mapper = TaxonomyMapper(
        snapshot_path=snapshot,
        mapping_table_version_id=manifest.mapping_table_version_id,
        manifest=manifest,
        embedder=SelectedVectorEmbedder(vector),
        clustering_config=config,
    )
    exact = mapper.map(
        OpinionUnit(
            raw_aspect="화질",
            raw_status="깔끔함",
            excerpt="화질이 깔끔함",
            opinion="화질이 깔끔함",
            sentiment="positive",
        )
    )
    assert exact.mapping_state == "mapped_exact"
    assert (exact.aspect_id, exact.aspect) == ("D-A-000404", "화질")
    assert (exact.status_id, exact.status) == ("D-S-001267", "깨끗함")

    candidate = mapper.map(
        OpinionUnit(
            raw_aspect="등록되지 않은 화질 표현",
            raw_status=None,
            excerpt="등록되지 않은 화질 표현",
            opinion="등록되지 않은 화질 표현",
            sentiment="neutral",
        )
    )
    assert candidate.mapping_state == "candidate"
    assert candidate.aspect is None
    assert candidate.suggested_aspect_id == "D-A-000404"
    assert candidate.aspect_candidate_eligible is True
    assert candidate.aspect_membership_max_distance is not None
    assert candidate.aspect_second_nearest_distance is not None


def test_status_cannot_link_rules_are_preserved(project_root) -> None:
    import yaml

    config = yaml.safe_load(
        (project_root / "config/normalization/20260803-213339.yaml").read_text()
    )["clustering"]
    assert _status_pair_is_constrained("있음", "없음", config)
    assert _status_pair_is_constrained("선명함", "선명하지 않음", config)
    assert not _status_pair_is_constrained("선명함", "깨끗함", config)
