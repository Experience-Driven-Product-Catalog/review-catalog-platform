from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from review_catalog.normalization.taxonomy import load_taxonomy_manifest
from review_catalog.pipeline.catalog_store import commit_catalog_delta
from review_catalog.reporting.dynamic import generate_dynamic_decision_proposal
from review_catalog.reporting.static import generate_static_catalog_report


def _unit(unit_id, review_id, status_id, status, sentiment, run_id):
    return {
        "opinion_unit_id": unit_id,
        "source_opinion_unit_idx": None,
        "review_id": review_id,
        "unit_position": 1,
        "raw_aspect": "화면 밝기",
        "raw_status": status,
        "excerpt": f"화면 밝기 {status}",
        "opinion": f"화면 밝기 {status}",
        "sentiment": sentiment,
        "mapping_state": "mapped_exact",
        "aspect_id": "aspect-brightness",
        "aspect": "화면 밝기",
        "status_id": status_id,
        "status": status,
        "suggested_aspect_id": None,
        "suggested_aspect": None,
        "aspect_distance": 0.0,
        "aspect_membership_max_distance": None,
        "aspect_centroid_distance": None,
        "aspect_second_nearest_distance": None,
        "aspect_distance_margin": None,
        "aspect_candidate_eligible": None,
        "suggested_status_id": None,
        "suggested_status": None,
        "status_distance": 0.0,
        "status_membership_max_distance": None,
        "status_centroid_distance": None,
        "status_second_nearest_distance": None,
        "status_distance_margin": None,
        "status_candidate_eligible": None,
        "prompt_version_id": "prompt-v1",
        "model_version_id": "model-v1",
        "mapping_table_version_id": "mapping-v1",
        "embedding_model_version_id": "embedding-v1",
        "normalization_run_id": "normalization-v1",
        "normalization_config_sha256": "b" * 64,
        "extraction_response_sha256": "a" * 64,
        "ingestion_run_id": run_id,
    }


def _candidate_unit(unit_id, review_id, unit_position, raw_aspect, raw_status, run_id):
    unit = _unit(unit_id, review_id, None, None, "negative", run_id)
    unit.update(
        {
            "unit_position": unit_position,
            "raw_aspect": raw_aspect,
            "raw_status": raw_status,
            "excerpt": f"{raw_aspect} {raw_status}",
            "opinion": f"{raw_aspect}에 {raw_status} 문제가 있음",
            "mapping_state": "candidate",
            "aspect_id": None,
            "aspect": None,
            "status_id": None,
            "status": None,
            "suggested_aspect_id": "aspect-panel",
            "suggested_aspect": "패널 상태",
            "suggested_status_id": "status-defect",
            "suggested_status": "불량",
            "aspect_distance": 0.12,
            "aspect_membership_max_distance": 0.24,
            "aspect_centroid_distance": 0.10,
            "aspect_second_nearest_distance": 0.35,
            "aspect_distance_margin": 0.11,
            "aspect_candidate_eligible": True,
            "status_distance": 0.11,
            "status_membership_max_distance": 0.18,
            "status_centroid_distance": 0.09,
            "status_second_nearest_distance": 0.28,
            "status_distance_margin": 0.10,
            "status_candidate_eligible": True,
        }
    )
    return unit


def test_single_writer_snapshot_and_report_entrypoints(tmp_path, project_root) -> None:
    run_id = "run-test"
    versions = {
        "mapping_table": {"id": "mapping-v1", "content_sha256": "x"},
        "opinion_unit_prompt": {"id": "prompt-v1"},
        "extraction_model": {"id": "model-v1"},
        "embedding_model": {"id": "embedding-v1"},
        "report_generator": {"id": "report-v1"},
    }
    reviews = [
        {
            "review_id": review_id,
            "source": "demo_ui" if demo_id else "test_fixture",
            "source_review_id": source_id,
            "product_id": product_id,
            "review_text": f"화면 밝기 {status}",
            "content_sha256": str(index) * 64,
            "ingestion_run_id": run_id,
            "demo_review_id": demo_id,
        }
        for index, (review_id, source_id, product_id, status, demo_id) in enumerate(
            [
                ("review-source", "demo-1", "product-a", "어두움", "demo-1"),
                ("review-a2", "a2", "product-a", "어두움", None),
                ("review-b", "b1", "product-b", "밝음", None),
            ],
            start=1,
        )
    ]
    units = [
        _unit("u1", "review-source", "status-dark", "어두움", "negative", run_id),
        _unit("u2", "review-a2", "status-dark", "어두움", "negative", run_id),
        _unit("u3", "review-b", "status-bright", "밝음", "positive", run_id),
    ]
    delta = {
        "run_id": run_id,
        "versions": versions,
        "products": [
            {"product_id": "product-a", "product_name": "상품 A", "category": "모니터"},
            {"product_id": "product-b", "product_name": "상품 B", "category": "모니터"},
        ],
        "reviews": reviews,
        "opinion_units": units,
        "delta_sha256": "d" * 64,
    }
    snapshot = tmp_path / "catalog.duckdb"
    manifest = load_taxonomy_manifest(project_root / "config/taxonomy/20260803-213339.json")
    counts = commit_catalog_delta(
        destination=snapshot,
        previous_snapshot=None,
        delta=delta,
        taxonomy_manifest=manifest,
        legacy_migration_root=project_root / "migration/source",
        writer_lock_path=tmp_path / "writer.lock",
        writer_identity="airflow",
    )
    assert counts == {"review_count": 3, "opinion_unit_count": 3, "candidate_count": 0}
    with duckdb.connect(str(snapshot), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM reviews").fetchone()[0] == 3
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE forbidden(value INTEGER)")

    generated_at = datetime(2026, 8, 4, tzinfo=UTC)
    static = generate_static_catalog_report(
        snapshot_path=snapshot,
        product_id="product-a",
        release_id="release-test",
        generated_at=generated_at,
        versions=versions,
        output_dir=tmp_path / "static",
    )
    assert static["payload"]["schema_version"] == "1.2.0"
    assert static["payload"]["report_type"] == "static_catalog_analysis"
    assert static["payload"]["aspect_summary"][0]["supporting_review_count"] == 2
    static_markdown = static["markdown_path"].read_text(encoding="utf-8")
    assert static_markdown.startswith("# **상품 A** 상품에 대한 정적 카탈로그 분석 보고서\n")
    assert "## 속성 감성 행렬 (상위 10개)" in static_markdown
    assert "## 속성-상태 행렬 (상위 10개)" in static_markdown
    assert "## 가장 논쟁적인 속성" in static_markdown
    assert "## 관련 상품" in static_markdown
    assert "### 관찰된 약점을 보완하는 대안 상품" in static_markdown
    dynamic = generate_dynamic_decision_proposal(
        snapshot_path=snapshot,
        demo_review_id="demo-1",
        release_id="release-test",
        generated_at=generated_at,
        versions=versions,
        output_dir=tmp_path / "dynamic",
    )
    assert dynamic["payload"]["schema_version"] == "2.0.0"
    assert dynamic["payload"]["proposal_type"] == "dynamic_review_decision_proposal"
    assert dynamic["payload"]["alternative_recommendations"]["status"] == "COMPLETED"
    assert (
        dynamic["payload"]["alternative_recommendations"]["alternatives"][0]["product_id"]
        == "product-b"
    )
    dynamic_markdown = dynamic["markdown_path"].read_text(encoding="utf-8")
    assert dynamic_markdown.startswith("# 동적 의사결정 제안서\n")
    assert '"화면 밝기 어두움"' in dynamic_markdown
    assert (
        "| raw_aspect | aspect | raw_status | status | excerpt | opinion | sentiment |"
        in dynamic_markdown
    )
    assert "- **aspect**:" in dynamic_markdown
    assert "## 다른 리뷰와의 관계" in dynamic_markdown
    assert "### 다른 리뷰에서 언급되지 않은 정확 매핑 aspect-status" not in dynamic_markdown
    assert "## 대안 상품 추천" in dynamic_markdown


def test_dynamic_report_keeps_candidate_evidence_out_of_comparison_and_ranking(
    tmp_path, project_root
) -> None:
    run_id = "run-candidate-test"
    versions = {
        "mapping_table": {"id": "mapping-v1", "content_sha256": "x"},
        "opinion_unit_prompt": {"id": "prompt-v1"},
        "extraction_model": {"id": "model-v1"},
        "embedding_model": {"id": "embedding-v1"},
        "report_generator": {"id": "report-v1"},
    }
    reviews = [
        {
            "review_id": "review-demo",
            "source": "demo_ui",
            "source_review_id": "demo-1",
            "product_id": "product-a",
            "review_text": "화질은 선명한데 액정 빛 바램 및 파손이 존재해요. 반품할려고요.",
            "content_sha256": "1" * 64,
            "ingestion_run_id": run_id,
            "demo_review_id": "demo-1",
        },
        {
            "review_id": "review-supported",
            "source": "test_fixture",
            "source_review_id": "supported-1",
            "product_id": "product-a",
            "review_text": "화면 밝기가 밝아요.",
            "content_sha256": "2" * 64,
            "ingestion_run_id": run_id,
            "demo_review_id": None,
        },
    ]
    units = [
        _unit("u-exact-demo", "review-demo", "status-bright", "밝음", "positive", run_id),
        _unit(
            "u-exact-supported",
            "review-supported",
            "status-bright",
            "밝음",
            "positive",
            run_id,
        ),
        _candidate_unit("u-candidate-fade", "review-demo", 2, "액정 빛 바램", "존재함", run_id),
        _candidate_unit("u-candidate-damage", "review-demo", 3, "액정 파손", "존재함", run_id),
        _candidate_unit("u-candidate-return", "review-demo", 4, "반품 의사", "반품 예정", run_id),
    ]
    delta = {
        "run_id": run_id,
        "versions": versions,
        "products": [{"product_id": "product-a", "product_name": "상품 A", "category": "모니터"}],
        "reviews": reviews,
        "opinion_units": units,
        "delta_sha256": "d" * 64,
    }
    snapshot = tmp_path / "catalog.duckdb"
    manifest = load_taxonomy_manifest(project_root / "config/taxonomy/20260803-213339.json")
    commit_catalog_delta(
        destination=snapshot,
        previous_snapshot=None,
        delta=delta,
        taxonomy_manifest=manifest,
        legacy_migration_root=project_root / "migration/source",
        writer_lock_path=tmp_path / "writer.lock",
        writer_identity="airflow",
    )

    dynamic = generate_dynamic_decision_proposal(
        snapshot_path=snapshot,
        demo_review_id="demo-1",
        release_id="release-test",
        generated_at=datetime(2026, 8, 4, tzinfo=UTC),
        versions=versions,
        output_dir=tmp_path / "dynamic",
    )

    payload = dynamic["payload"]
    comparison = payload["catalog_comparison"]
    assert comparison["exact_mapped_aspect_status_count"] == 1
    assert comparison["unresolved_opinion_unit_count"] == 3
    assert "unmentioned_exact_mapped_aspect_status" not in payload
    alternatives = payload["alternative_recommendations"]
    assert alternatives["status"] == "NEGATIVE_INPUT_UNRESOLVED"
    assert alternatives["observed_negative_opinion_unit_count"] == 3
    assert len(alternatives["negative_conditions"]) == 0
    assert len(alternatives["excluded_negative_conditions"]) == 3
    markdown = dynamic["markdown_path"].read_text(encoding="utf-8")
    assert "다른 리뷰에서 언급되지 않은 정확 매핑 aspect-status" not in markdown
    assert "다른 리뷰와의 비교가 보류된 Opinion Unit" in markdown
    assert (
        "정확한 mapping table match가 없어 canonical aspect-status로 확정되지 않았습니다"
        in markdown
    )
    assert (
        "candidate 추정값은 근거 기반 대안 순위에 사용하지 않으므로 추천을 보류합니다" in markdown
    )
    assert "제출된 리뷰에서 상태가 정규화된 부정적인 속성-상태가 없어" not in markdown


def test_non_airflow_writer_is_rejected(tmp_path, project_root) -> None:
    with pytest.raises(PermissionError, match="restricted to Airflow"):
        commit_catalog_delta(
            destination=tmp_path / "catalog.duckdb",
            previous_snapshot=None,
            delta={},
            taxonomy_manifest=load_taxonomy_manifest(
                project_root / "config/taxonomy/20260803-213339.json"
            ),
            legacy_migration_root=project_root / "migration/source",
            writer_lock_path=tmp_path / "writer.lock",
            writer_identity="fastapi",
        )
